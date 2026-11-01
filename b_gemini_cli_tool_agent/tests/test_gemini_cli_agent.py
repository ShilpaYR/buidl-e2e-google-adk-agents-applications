import json
import time

import pytest

from common.adk_testing import ScriptedLlm, call_response, last_part, run_agent, swap_models, system_text, text_response
from conftest import FAKE_LOG
from gemini_cli_agent.agent import cli_calls_used, repo_analysis_pipeline, root_agent
from gemini_cli_agent.tools import clone_repository, gemini_cli, parse_github_url

URL = "https://github.com/octo/demo"


class FakeToolContext:
    def __init__(self):
        self.state = {}


@pytest.mark.parametrize("url,ok", [
    ("https://github.com/google/adk-python", True),
    ("https://github.com/google/adk-python.git", True),
    ("https://github.com/a/b; rm -rf /", False),
    ("https://github.com/a/b$(whoami)", False),
    ("git@github.com:a/b.git", False),
    ("file:///etc/passwd", False),
    ("https://evil.com/a/b", False),
    ("https://github.com/../..", False),
])
def test_url_allow_list(url, ok):
    assert (parse_github_url(url) is not None) is ok


@pytest.mark.asyncio
async def test_gemini_cli_tool_contract():
    ctx = FakeToolContext()
    cloned = await clone_repository(URL, ctx)
    assert cloned["status"] == "ok" and cloned["file_count"] == 2
    result = await gemini_cli("Explain this codebase", ctx)
    assert result["status"] == "ok"
    assert "octo__demo" in result["response"]
    call = json.loads(FAKE_LOG.read_text().strip().splitlines()[-1])
    assert call["argv"][:2] == ["-p", "Explain this codebase"]
    assert "--approval-mode" in call["argv"] and "plan" in call["argv"]   # read-only by default
    assert "-o" in call["argv"] and "json" in call["argv"]
    assert call["cwd"].endswith("octo__demo")


@pytest.mark.asyncio
async def test_gemini_cli_requires_repo():
    result = await gemini_cli("Explain", FakeToolContext())
    assert result["status"] == "error"


@pytest.mark.asyncio
async def test_rejects_malicious_url_without_running_anything():
    result = await clone_repository("https://github.com/a/b;touch /tmp/pwned", FakeToolContext())
    assert result["status"] == "error"


def _pipeline_responder(req):
    text = system_text(req)
    part = last_part(req)
    responded = part.function_response if part and part.function_response else None
    if "clone_repository" in text and "Find the GitHub repository URL" in text:
        if not responded:
            return call_response("clone_repository", {"github_url": URL})
        return text_response("octo/demo, 2 files, .py/.md")
    if "Call `gemini_cli` exactly once" in text:
        if not responded:
            return call_response("gemini_cli", {"task": text.split("task:")[1].split("Then")[0].strip()})
        return text_response("## Section\n" + responded.response.get("response", ""))
    if "onboarding report" in text:
        return text_response("FINAL REPORT")
    return None


@pytest.mark.asyncio
async def test_pipeline_runs_three_cli_analysts_in_parallel():
    llm = ScriptedLlm(requests=[], responder=_pipeline_responder)
    swap_models(repo_analysis_pipeline, llm)
    before = len(FAKE_LOG.read_text().splitlines()) if FAKE_LOG.exists() else 0

    start = time.monotonic()
    _, state, runner, sid = await run_agent(repo_analysis_pipeline, f"Analyse {URL}")
    elapsed = time.monotonic() - start

    calls = FAKE_LOG.read_text().splitlines()[before:]
    assert len(calls) == 3                       # one Gemini CLI run per analyst
    assert elapsed < 2.5, f"analysts ran sequentially ({elapsed:.1f}s)"  # each fake call sleeps 1s
    for key in ("architecture_report", "test_plan_report", "quality_report"):
        assert "Gemini CLI analysed octo__demo" in state[key]
    assert state["final_repo_report"] == "FINAL REPORT"
    assert cli_calls_used(state) == 3            # no lost updates across parallel branches
    keys = await runner.artifact_service.list_artifact_keys(app_name="test_app", user_id="tester", session_id=sid)
    assert "repo_analysis_report.md" in keys


def test_root_agent_tools():
    names = {getattr(t, "name", getattr(t, "__name__", "")) for t in root_agent.tools}
    assert names == {"clone_repository", "gemini_cli", "repo_analysis_pipeline"}
