"""Deterministic tools for the review and fix pipelines.

Rule of thumb used throughout: anything that can be *computed* (AST metrics,
PEP 8 violations, scores, pass/fail status) is a Python tool; the LLM is used
only for judgement and explanation. Tools never raise — they return
{"status": "error", ...} so one failure can't crash the pipeline
(graceful degradation).
"""

from __future__ import annotations

import ast
import asyncio
import json
import logging
import re
from datetime import datetime, timezone
from typing import Any, Optional

import pycodestyle
from google.adk.agents.callback_context import CallbackContext
from google.adk.tools import ToolContext
from google.genai import types

from .config import config
from .constants import StateKeys as K

log = logging.getLogger(__name__)
FENCE = re.compile(r"```(?:python|py)?\s*\n(.*?)```", re.DOTALL | re.IGNORECASE)


# ====================================================================== helpers
def extract_code(text: str) -> str:
    """Return the first fenced Python block, or the text itself."""
    if not text:
        return ""
    match = FENCE.search(text)
    return (match.group(1) if match else text).strip("\n")


def parse_json_block(text: Any) -> dict:
    """Best-effort parse of an LLM's JSON answer (fenced or bare)."""
    if isinstance(text, dict):
        return text
    if not isinstance(text, str):
        return {}
    candidate = text.strip()
    fenced = re.search(r"```(?:json)?\s*\n(.*?)```", candidate, re.DOTALL)
    if fenced:
        candidate = fenced.group(1)
    start, end = candidate.find("{"), candidate.rfind("}")
    if start == -1 or end == -1:
        return {}
    try:
        return json.loads(candidate[start:end + 1])
    except json.JSONDecodeError:
        return {}


def _complexity(node: ast.AST) -> int:
    """Approximate McCabe cyclomatic complexity of a function."""
    branches = (ast.If, ast.For, ast.While, ast.AsyncFor, ast.With, ast.ExceptHandler,
                ast.IfExp, ast.comprehension, ast.Assert, ast.Match)
    score = 1
    for child in ast.walk(node):
        if isinstance(child, branches):
            score += 1
        elif isinstance(child, ast.BoolOp):
            score += len(child.values) - 1
    return score


def structure_metrics(code: str) -> dict:
    tree = ast.parse(code)
    functions, classes, imports = [], [], []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            length = (getattr(node, "end_lineno", node.lineno) or node.lineno) - node.lineno + 1
            functions.append({
                "name": node.name,
                "line": node.lineno,
                "args": [a.arg for a in node.args.args],
                "length": length,
                "complexity": _complexity(node),
                "has_docstring": ast.get_docstring(node) is not None,
                "is_async": isinstance(node, ast.AsyncFunctionDef),
            })
        elif isinstance(node, ast.ClassDef):
            classes.append({"name": node.name, "line": node.lineno,
                            "methods": [n.name for n in node.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))],
                            "has_docstring": ast.get_docstring(node) is not None})
        elif isinstance(node, ast.Import):
            imports += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            imports.append(node.module or ".")

    documented = sum(f["has_docstring"] for f in functions) + sum(c["has_docstring"] for c in classes)
    total = len(functions) + len(classes)
    doc_coverage = documented / total if total else 1.0
    max_cx = max((f["complexity"] for f in functions), default=1)
    avg_len = sum(f["length"] for f in functions) / len(functions) if functions else 0
    # structure score 0..1: documentation, small functions, low complexity
    structure_score = round(
        0.4 * doc_coverage
        + 0.3 * (1.0 if avg_len <= 30 else max(0.0, 1 - (avg_len - 30) / 70))
        + 0.3 * (1.0 if max_cx <= 10 else max(0.0, 1 - (max_cx - 10) / 15)), 3)
    return {
        "functions": functions,
        "classes": classes,
        "imports": sorted(set(imports)),
        "metrics": {
            "line_count": len(code.splitlines()),
            "function_count": len(functions),
            "class_count": len(classes),
            "docstring_coverage": round(doc_coverage, 2),
            "avg_function_length": round(avg_len, 1),
            "max_complexity": max_cx,
            "structure_score": structure_score,
        },
    }


class _CollectingReport(pycodestyle.BaseReport):
    """Collects violations in memory — thread-safe, no stdout redirection."""

    def __init__(self, options):
        super().__init__(options)
        self.items: list[dict] = []

    def error(self, line_number, offset, text, check):
        code = super().error(line_number, offset, text, check)
        if code:
            self.items.append({"line": line_number, "column": offset + 1,
                               "code": code, "message": text[5:].strip()})
        return code


STYLE_WEIGHTS = {"E1": 10, "E2": 3, "E3": 5, "E4": 8, "E5": 5, "E7": 7, "E9": 10,
                 "W1": 2, "W2": 2, "W3": 2, "W6": 4, "N8": 5}


def style_report(code: str) -> dict:
    if not code.endswith("\n"):
        code += "\n"  # code pasted in chat has no meaningful final newline (avoid spurious W292)
    guide =pycodestyle.StyleGuide(quiet=True, max_line_length=100, ignore=["W503", "W504"],
                                   reporter=_CollectingReport)
    checker = pycodestyle.Checker(lines=code.splitlines(keepends=True) or [""],
                                  options=guide.options, report=guide.options.report)
    checker.check_all()
    issues = list(checker.report.items)
    try:  # PEP 8 naming (pycodestyle doesn't check names)
        for node in ast.walk(ast.parse(code)):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name != node.name.lower():
                issues.append({"line": node.lineno, "column": node.col_offset + 1, "code": "N802",
                               "message": f"function name '{node.name}' should be lowercase"})
            elif isinstance(node, ast.ClassDef) and (not node.name[0].isupper() or "_" in node.name.strip("_")):
                issues.append({"line": node.lineno, "column": node.col_offset + 1, "code": "N801",
                               "message": f"class name '{node.name}' should use CapWords"})
    except SyntaxError:
        pass
    penalty = sum(STYLE_WEIGHTS.get(i["code"][:2], 3) for i in issues)
    score = max(0, 100 - penalty)
    issues.sort(key=lambda i: (i["line"], i["column"]))
    return {"status": "success", "score": score, "issue_count": len(issues), "issues": issues[:15],
            "summary": f"Style score {score}/100 with {len(issues)} violation(s)."}


async def _in_thread(fn, *args):
    return await asyncio.get_running_loop().run_in_executor(None, fn, *args)


# ====================================================================== callbacks
def capture_code(callback_context: CallbackContext) -> Optional[types.Content]:
    """before_agent_callback for the review pipeline: copy the user's code into
    state deterministically (the LLM never has to re-type it)."""
    user = callback_context.user_content
    text = "".join(p.text or "" for p in (user.parts if user else []) if p.text)
    code = extract_code(text)
    if len(code) > config.max_code_chars:
        code = code[: config.max_code_chars]
        callback_context.state["code_truncated"] = True
    callback_context.state[K.CODE_TO_REVIEW] = code
    callback_context.state[K.GRADING_ATTEMPTS] = int(callback_context.state.get(K.GRADING_ATTEMPTS, 0))
    return None


def reset_fix_state(callback_context: CallbackContext) -> Optional[types.Content]:
    """before_agent_callback for the fix pipeline."""
    callback_context.state[K.FIX_ATTEMPTS] = 0
    callback_context.state[K.FIX_STATUS] = "PENDING"
    return None


# ====================================================================== review tools
async def analyze_code_structure(tool_context: ToolContext, code: str = "") -> dict:
    """Parse the Python code with the AST and return functions, classes, imports
    and quality metrics (docstring coverage, function length, complexity).

    Args:
        code: Optional. Leave empty to analyse the code the user submitted.
    """
    code = code or tool_context.state.get(K.CODE_TO_REVIEW, "")
    if not code.strip():
        return {"status": "error", "message": "No code to analyse."}
    try:
        result = await _in_thread(structure_metrics, code)
    except SyntaxError as exc:
        err = {"line": exc.lineno, "message": exc.msg, "text": (exc.text or "").strip()}
        tool_context.state[K.SYNTAX_ERROR] = err
        return {"status": "error", "error_type": "SyntaxError", **err}
    tool_context.state[K.CODE_ANALYSIS] = result["metrics"]
    tool_context.state[K.CODE_LINE_COUNT] = result["metrics"]["line_count"]
    tool_context.state[K.SYNTAX_ERROR] = None
    return {"status": "success", **result}


async def check_code_style(tool_context: ToolContext, code: str = "") -> dict:
    """Check PEP 8 compliance (pycodestyle + naming rules) and return a 0-100 score.

    Args:
        code: Optional. Leave empty to check the code the user submitted.
    """
    code = code or tool_context.state.get(K.CODE_TO_REVIEW, "")
    if not code.strip():
        return {"status": "error", "message": "No code to check.", "score": 0}
    try:
        result = await _in_thread(style_report, code)
    except Exception as exc:  # graceful degradation
        log.exception("style check failed")
        return {"status": "error", "message": str(exc), "score": 0}
    tool_context.state[K.STYLE_SCORE] = result["score"]
    tool_context.state[K.STYLE_ISSUES] = result["issues"]
    tool_context.state[K.STYLE_ISSUE_COUNT] = result["issue_count"]
    return result


async def search_past_feedback(tool_context: ToolContext) -> dict:
    """Look up this developer's previous reviews (persisted in user-scoped state)
    so feedback can mention recurring issues and progress."""
    history = list(tool_context.state.get(K.USER_REVIEW_HISTORY, []) or [])
    recurring: dict[str, int] = {}
    for review in history:
        for code in review.get("issue_codes", []):
            recurring[code] = recurring.get(code, 0) + 1
    return {
        "status": "success",
        "previous_reviews": len(history),
        "last_reviews": history[-3:],
        "recurring_issue_codes": dict(sorted(recurring.items(), key=lambda kv: -kv[1])[:5]),
        "last_style_score": tool_context.state.get(K.USER_LAST_STYLE_SCORE),
    }


def compute_grade(style_score: float, tests: dict, structure_score: float) -> dict:
    summary = tests.get("test_summary", {}) if tests else {}
    total = int(summary.get("total_tests_run", 0) or 0)
    passed = int(summary.get("tests_passed", 0) or 0)
    test_rate = passed / total if total else 0.0
    grade = (config.style_weight * style_score / 100 + config.test_weight * test_rate
             + config.structure_weight * structure_score)
    return {"grade": round(grade, 3), "test_pass_rate": round(test_rate, 3),
            "passed": grade >= config.passing_score_threshold,
            "letter": "A" if grade >= .9 else "B" if grade >= .8 else "C" if grade >= .7 else "D" if grade >= .6 else "F"}


async def update_grading_progress(tool_context: ToolContext) -> dict:
    """Compute the weighted grade (style, tests, structure) and the change since
    this developer's last review."""
    state = tool_context.state
    tests = parse_json_block(state.get(K.TEST_EXECUTION_SUMMARY, ""))
    structure = (state.get(K.CODE_ANALYSIS) or {}).get("structure_score", 0.0)
    style = float(state.get(K.STYLE_SCORE, 0) or 0)
    result = compute_grade(style, tests, structure)
    previous = state.get(K.USER_LAST_STYLE_SCORE)
    result["style_improvement"] = None if previous is None else style - float(previous)
    state[K.GRADING_ATTEMPTS] = int(state.get(K.GRADING_ATTEMPTS, 0)) + 1
    state[K.FINAL_GRADE] = result
    return {"status": "success", **result}


async def save_grading_report(feedback_text: str, tool_context: ToolContext) -> dict:
    """Save the final review as a downloadable markdown artifact and record it in
    the developer's history.

    Args:
        feedback_text: The complete feedback markdown to save.
    """
    state = tool_context.state
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    filename = f"code_review_{stamp}.md"
    saved = None
    try:
        version = await tool_context.save_artifact(
            filename, types.Part.from_bytes(data=feedback_text.encode(), mime_type="text/markdown"))
        saved = {"filename": filename, "version": version}
    except Exception as exc:  # no artifact service
        log.warning("artifact not saved: %s", exc)

    history = list(state.get(K.USER_REVIEW_HISTORY, []) or [])
    history.append({
        "timestamp": stamp,
        "style_score": state.get(K.STYLE_SCORE),
        "grade": (state.get(K.FINAL_GRADE) or {}).get("grade"),
        "issue_codes": sorted({i["code"] for i in state.get(K.STYLE_ISSUES, []) or []}),
    })
    state[K.USER_REVIEW_HISTORY] = history[-20:]
    state[K.USER_LAST_STYLE_SCORE] = state.get(K.STYLE_SCORE)
    return {"status": "success", "artifact": saved, "reviews_on_record": len(history)}


# ====================================================================== fix tools
async def validate_fixed_style(tool_context: ToolContext) -> dict:
    """Run the style check on the fixed code produced by CodeFixer."""
    fixed = extract_code(tool_context.state.get(K.CODE_FIXES, ""))
    if not fixed.strip():
        return {"status": "error", "message": "No fixed code found."}
    try:
        compile(fixed, "<fixed>", "exec")
    except SyntaxError as exc:
        tool_context.state[K.FIXED_STYLE_SCORE] = 0
        return {"status": "error", "message": f"Fixed code has a syntax error on line {exc.lineno}: {exc.msg}"}
    result = await _in_thread(style_report, fixed)
    tool_context.state[K.FIXED_STYLE_SCORE] = result["score"]
    tool_context.state[K.FIXED_STYLE_ISSUES] = result["issues"]
    return result


def classify_fix(original_style: float, fixed_style: float, fix_tests: dict) -> str:
    summary = fix_tests.get("test_summary", {}) if fix_tests else {}
    total = int(summary.get("total_tests_run", 0) or 0)
    failed = int(summary.get("tests_failed", 0) or 0) + int(summary.get("tests_with_errors", 0) or 0)
    tests_ok = total > 0 and failed == 0
    if tests_ok and fixed_style >= 90:
        return "SUCCESSFUL"
    if fixed_style > original_style or (tests_ok and fixed_style >= original_style):
        return "PARTIAL"
    return "FAILED"


async def compile_fix_report(tool_context: ToolContext) -> dict:
    """Compare the original and fixed code (style score, tests) and decide whether
    the fix is SUCCESSFUL, PARTIAL or FAILED."""
    state = tool_context.state
    fix_tests = parse_json_block(state.get(K.FIX_TEST_EXECUTION_SUMMARY, ""))
    original = float(state.get(K.STYLE_SCORE, 0) or 0)
    fixed = float(state.get(K.FIXED_STYLE_SCORE, 0) or 0)
    status = classify_fix(original, fixed, fix_tests)
    attempts = int(state.get(K.FIX_ATTEMPTS, 0)) + 1
    report = {
        "status": status,
        "attempt": attempts,
        "style_before": original,
        "style_after": fixed,
        "style_delta": fixed - original,
        "tests_after_fix": fix_tests.get("test_summary", {}),
        "remaining_style_issues": state.get(K.FIXED_STYLE_ISSUES, [])[:5],
    }
    state[K.FIX_STATUS] = status
    state[K.FIX_ATTEMPTS] = attempts
    state[K.FIX_REPORT] = report
    return report


def exit_fix_loop(tool_context: ToolContext) -> dict:
    """Stop the fix loop. Call ONLY when compile_fix_report returned SUCCESSFUL."""
    if tool_context.state.get(K.FIX_STATUS) != "SUCCESSFUL":
        return {"status": "refused", "message": "Fix is not SUCCESSFUL yet; the loop will retry."}
    tool_context.actions.escalate = True  # LoopAgent stops on escalate
    return {"status": "ok", "message": "Fix complete, exiting loop."}


async def save_fix_report(tool_context: ToolContext) -> dict:
    """Save the fixed code (.py) and the fix report (.md) as artifacts."""
    state = tool_context.state
    fixed = extract_code(state.get(K.CODE_FIXES, ""))
    report = state.get(K.FIX_REPORT, {})
    saved = []
    for name, data, mime in (
        ("fixed_code.py", fixed, "text/x-python"),
        ("fix_report.json", json.dumps(report, indent=2), "application/json"),
    ):
        try:
            await tool_context.save_artifact(name, types.Part.from_bytes(data=data.encode(), mime_type=mime))
            saved.append(name)
        except Exception as exc:
            log.warning("artifact %s not saved: %s", name, exc)
    return {"status": "success", "saved": saved, "fix_status": state.get(K.FIX_STATUS)}
