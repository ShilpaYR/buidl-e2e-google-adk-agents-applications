import json

import pytest

from common.adk_testing import ScriptedLlm, call_response, last_part, run_agent, swap_models, system_text, text_response
from code_review_assistant.agent import root_agent
from code_review_assistant.constants import StateKeys as K
from code_review_assistant.sub_agents import code_fix_pipeline
from code_review_assistant.tools import (
    analyze_code_structure, classify_fix, compute_grade, exit_fix_loop, extract_code, parse_json_block,
    structure_metrics, style_report,
)

BUGGY = '''def dfs_search_v1(graph, start, target):
    visited = set()
    stack = start
    while stack:
        current = stack.pop()
        if current == target:
            return True
        if current not in visited:
            visited.add(current)
            for neighbor in graph[current]:
                if neighbor not in visited:
                    stack.append(neighbor)
    return False
'''

CLEAN = '''"""Graph helpers."""


def dfs_search(graph, start, target):
    """Return True if target is reachable from start."""
    visited, stack = set(), [start]
    while stack:
        node = stack.pop()
        if node == target:
            return True
        if node not in visited:
            visited.add(node)
            stack.extend(n for n in graph.get(node, []) if n not in visited)
    return False
'''


class Ctx:
    def __init__(self, state=None):
        self.state = dict(state or {})

        class A:
            escalate = False
        self.actions = A()


# ------------------------------------------------------------ deterministic tools
def test_style_clean_code_scores_100():
    assert style_report(CLEAN)["score"] == 100


def test_style_detects_pep8_and_naming():
    bad = "def BadName( x ):\n  return x+1\nclass my_class: pass\n"
    report = style_report(bad)
    codes = {i["code"] for i in report["issues"]}
    assert {"N802", "N801", "E111", "E201"} <= codes
    assert report["score"] < 80


def test_structure_metrics():
    m = structure_metrics(BUGGY)["metrics"]
    assert m["function_count"] == 1 and m["docstring_coverage"] == 0.0
    assert m["max_complexity"] >= 6


@pytest.mark.asyncio
async def test_syntax_error_is_reported_not_raised():
    ctx = Ctx({K.CODE_TO_REVIEW: "def broken(:\n  pass"})
    result = await analyze_code_structure(ctx)
    assert result["status"] == "error" and result["error_type"] == "SyntaxError"
    assert ctx.state[K.SYNTAX_ERROR]["line"] == 1


def test_extract_and_parse_helpers():
    assert extract_code("please review\n```python\nx = 1\n```\nthanks") == "x = 1"
    assert parse_json_block('```json\n{"a": 1}\n```')["a"] == 1
    assert parse_json_block("not json") == {}


def test_grade_and_fix_classification():
    tests = {"test_summary": {"total_tests_run": 10, "tests_passed": 10}}
    assert compute_grade(100, tests, 1.0)["letter"] == "A"
    assert compute_grade(50, {"test_summary": {"total_tests_run": 10, "tests_passed": 2}}, 0.3)["passed"] is False
    ok = {"test_summary": {"total_tests_run": 10, "tests_failed": 0, "tests_with_errors": 0}}
    assert classify_fix(60, 100, ok) == "SUCCESSFUL"
    assert classify_fix(60, 80, ok) == "PARTIAL"
    assert classify_fix(60, 50, {"test_summary": {"total_tests_run": 5, "tests_failed": 3}}) == "FAILED"


def test_exit_loop_only_on_success():
    ctx = Ctx({K.FIX_STATUS: "PARTIAL"})
    assert exit_fix_loop(ctx)["status"] == "refused" and ctx.actions.escalate is False
    ctx.state[K.FIX_STATUS] = "SUCCESSFUL"
    assert exit_fix_loop(ctx)["status"] == "ok" and ctx.actions.escalate is True


# ------------------------------------------------------------ offline pipelines
TEST_JSON = json.dumps({"test_summary": {"total_tests_run": 12, "tests_passed": 3, "tests_failed": 1,
                                         "tests_with_errors": 8, "critical_issues_found": 1},
                        "critical_issues": [{"type": "interface_bug", "description": "stack = start is not a list",
                                             "severity": "high"}],
                        "verdict": {"status": "BROKEN"}})


def review_responder(req):
    text = system_text(req)
    part = last_part(req)
    done = part.function_response.name if part and part.function_response else None
    if "Python code review assistant" in text:
        return None if done else call_response("transfer_to_agent", {"agent_name": "CodeReviewPipeline"})
    if "analyze_code_structure" in text:
        return text_response("- one function, no docstring") if done else call_response("analyze_code_structure", {})
    if "check_code_style" in text:
        return text_response("style summary") if done else call_response("check_code_style", {})
    if "testing specialist" in text:
        assert "stack = start" in text  # the user's code was injected from state
        return text_response(TEST_JSON)
    if "mentoring a developer" in text:
        order = [None, "search_past_feedback", "update_grading_progress", "save_grading_report"]
        nxt = {None: ("search_past_feedback", {}), "search_past_feedback": ("update_grading_progress", {}),
               "update_grading_progress": ("save_grading_report", {"feedback_text": "# Review\nGrade F"})}
        if done in order[:-1]:
            return call_response(*nxt[done])
        return text_response("# Review\nGrade F")
    return None


@pytest.mark.asyncio
async def test_review_pipeline_end_to_end():
    swap_models(root_agent, ScriptedLlm(requests=[], responder=review_responder))
    _, state, runner, sid = await run_agent(root_agent, f"Review this:\n```python\n{BUGGY}```")
    assert state[K.CODE_TO_REVIEW].startswith("def dfs_search_v1")     # captured by callback
    assert state[K.CODE_ANALYSIS]["function_count"] == 1
    assert 0 < state[K.STYLE_SCORE] <= 100                               # deterministic pycodestyle score
    assert state[K.FINAL_GRADE]["passed"] is False
    assert len(state[K.USER_REVIEW_HISTORY]) == 1                        # user-scoped memory
    keys = await runner.artifact_service.list_artifact_keys(app_name="test_app", user_id="tester", session_id=sid)
    assert any(k.startswith("code_review_") for k in keys)


def make_fix_responder(calls):
    def responder(req):
        text = system_text(req)
        part = last_part(req)
        fr = part.function_response if part and part.function_response else None
        if "Fix this Python code" in text:
            calls["fixer"] += 1
            code = "def dfsSearch(graph,start,target):\n  return start==target\n" if calls["fixer"] == 1 else CLEAN
            return text_response(f"```python\n{code}```")
        if "Test the FIXED code" in text:
            return text_response(json.dumps({"test_summary": {"total_tests_run": 10, "tests_passed": 10,
                                                              "tests_failed": 0, "tests_with_errors": 0}}))
        if "Validate the latest fix attempt" in text:
            if fr is None:
                return call_response("validate_fixed_style", {})
            if fr.name == "validate_fixed_style":
                return call_response("compile_fix_report", {})
            if fr.name == "compile_fix_report":
                if fr.response.get("status") == "SUCCESSFUL":
                    return call_response("exit_fix_loop", {})
                return text_response("PARTIAL - retrying")
            return text_response("SUCCESSFUL")
        if "Present the fix result" in text:
            return text_response("done") if fr else call_response("save_fix_report", {})
        return None
    return responder


@pytest.mark.asyncio
async def test_fix_loop_retries_then_exits_on_success():
    calls = {"fixer": 0}
    swap_models(code_fix_pipeline, ScriptedLlm(requests=[], responder=make_fix_responder(calls)))
    state0 = {K.CODE_TO_REVIEW: BUGGY, K.STYLE_SCORE: 85, K.TEST_EXECUTION_SUMMARY: TEST_JSON}
    _, state, runner, sid = await run_agent(code_fix_pipeline, "yes please fix it", state0)

    assert calls["fixer"] == 2                 # attempt 1 PARTIAL (bad style), attempt 2 SUCCESSFUL
    assert state[K.FIX_ATTEMPTS] == 2
    assert state[K.FIX_STATUS] == "SUCCESSFUL"
    assert state[K.FIXED_STYLE_SCORE] == 100
    keys = await runner.artifact_service.list_artifact_keys(app_name="test_app", user_id="tester", session_id=sid)
    assert {"fixed_code.py", "fix_report.json"} <= set(keys)
