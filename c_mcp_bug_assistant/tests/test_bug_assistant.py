"""Unit tests run anywhere. Integration tests run when MCP Toolbox is up:
    toolbox --config mcp-toolbox/tools.local.yaml --port 5002
They drive the REAL agent → ToolboxToolset → MCP Toolbox → PostgreSQL path,
with only the LLM replaced by a scripted fake."""

import json

import pytest
import yaml
from pathlib import Path

from common.adk_testing import ScriptedLlm, call_response, declared_tools, last_part, run_agent, swap_models, text_response
from bug_assistant import tools as bug_tools
from bug_assistant.agent import root_agent

ROOT = Path(__file__).resolve().parent.parent
toolbox_up = bug_tools._toolbox_reachable(bug_tools.TOOLBOX_URL, bug_tools.TOOLBOX_TOOLSET)
needs_toolbox = pytest.mark.skipif(not toolbox_up, reason="MCP Toolbox not running")


def test_function_tool():
    assert len(bug_tools.get_current_date()["current_date"]) == 10


@pytest.mark.parametrize("name,allowed", [
    ("search_issues", True), ("list_issues", True), ("issue_read", True),
    ("get_pull_request", True), ("search_repositories", True), ("pull_request_read", True),
    ("create_issue", False), ("merge_pull_request", False), ("delete_file", False),
    ("get_me", False),
])
def test_github_read_only_filter(name, allowed):
    class T:  # stand-in for an MCP tool
        pass
    t = T()
    t.name = name
    assert bug_tools.github_read_only(t) is allowed


@pytest.mark.parametrize("cfg", ["tools.local.yaml", "tools.yaml"])
def test_toolbox_configs_are_consistent(cfg):
    data = yaml.safe_load((ROOT / "mcp-toolbox" / cfg).read_text())
    tools = data["tools"]
    assert len(tools) == 9
    for name, tool in tools.items():
        n_params = len(tool.get("parameters", []))
        for i in range(1, n_params + 1):
            assert f"${i}" in tool["statement"], f"{cfg}:{name} never uses ${i}"
        assert f"${n_params + 1}" not in tool["statement"]
    readonly = set(data["toolsets"]["tickets_readonly"])
    assert not any(t.startswith(("create", "update")) for t in readonly)


def test_agent_starts_with_degraded_dependencies():
    names = {getattr(t, "name", getattr(t, "__name__", type(t).__name__)) for t in root_agent.tools}
    assert {"get_current_date", "search_agent"} <= names


@needs_toolbox
@pytest.mark.asyncio
async def test_e2e_duplicate_search_through_mcp():
    seen = {}

    def responder(req):
        if "search-tickets" not in declared_tools(req):
            return None
        part = last_part(req)
        if part and part.function_response:
            seen["result"] = part.function_response.response
            return text_response("Found a likely duplicate.")
        return call_response("search-tickets", {"query": "login page freezes after failed attempts"})

    swap_models(root_agent, ScriptedLlm(requests=[], responder=responder))
    events, *_ = await run_agent(root_agent, "Users say login freezes after 3 bad passwords. Duplicate?")
    payload = json.dumps(seen["result"])
    assert "Login Page Freezes After Multiple Failed Attempts" in payload
    assert events[-1].content.parts[0].text == "Found a likely duplicate."


@needs_toolbox
@pytest.mark.asyncio
async def test_e2e_create_and_update_ticket_through_mcp():
    results = []

    def responder(req):
        if "create-new-ticket" not in declared_tools(req):
            return None
        part = last_part(req)
        if not (part and part.function_response):
            return call_response("create-new-ticket", {
                "title": "Grinder jams on dark roast", "description": "Burr grinder stalls with oily beans.",
                "assignee": "frank.white@example.com", "priority": "P2 - Medium", "status": "Open"})
        results.append(part.function_response.response)
        if len(results) == 1:
            new_id = json.loads(json.dumps(results[0]))
            ticket_id = _extract_id(new_id)
            return call_response("update-ticket-status", {"status": "In Progress", "ticket_id": ticket_id})
        return text_response("done")

    swap_models(root_agent, ScriptedLlm(requests=[], responder=responder))
    await run_agent(root_agent, "File a ticket: grinder jams on dark roast, then mark it in progress")
    assert "In Progress" in json.dumps(results[-1])


def _extract_id(response) -> int:
    text = json.dumps(response)
    import re
    return int(re.search(r'ticket_id\\?"?\s*:\s*(\d+)', text).group(1))
