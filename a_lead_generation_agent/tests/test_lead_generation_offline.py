"""Offline end-to-end tests: real ADK graph, scripted model, no API calls."""

import json

import pytest

from common.adk_testing import (
    ScriptedLlm, call_response, last_part, run_agent, swap_models, system_text, text_response,
)
from lead_generation_agent.agent import root_agent
from lead_generation_agent.callbacks import before_tool_guard
from lead_generation_agent.sub_agents import lead_generation_agent, pattern_discovery_agent

BASE_STATE = {"country": "Thailand", "industry": "fintech", "k": 2, "m": 2,
              "current_date": "2026-10-28", "stage": "pattern_discovery", "patterns_ready": False}


def test_root_agent_wiring():
    names = {getattr(t, "name", getattr(t, "__name__", "")) for t in root_agent.tools}
    assert names == {"set_research_parameters", "intent_extractor_agent",
                     "pattern_discovery_agent", "lead_generation_agent"}
    assert [a.name for a in pattern_discovery_agent.sub_agents] == [
        "CompanyFinderAgent", "CompanyFormatterAgent", "ResearchOrchestrator",
        "SynthesizerOrchestrator", "PatternSynthesizerAgent"]


@pytest.mark.asyncio
async def test_pattern_discovery_fans_out_per_company():
    llm = ScriptedLlm(requests=[])
    swap_models(pattern_discovery_agent, llm)
    _, state, *_ = await run_agent(pattern_discovery_agent, "find fintech entrants", dict(BASE_STATE))

    companies = state["companies_found_structured"]["companies_found"]
    assert len(companies) == 2                     # schema sample produces 2 items
    for i in range(2):                             # one validator + researcher + formatter each
        assert state[f"validation_result_{i}"]["is_valid"] is True
        assert "summary" in state[f"research_findings_{i}"]
    assert "VALID" in state["all_research_findings"]
    assert state["discovered_patterns"] == "OK"
    assert state["validated_company_count"] == 2
    # 2 sequential LLM steps + 3 per company * 2 companies + synthesizer = 9 calls
    assert len(llm.requests) == 9


@pytest.mark.asyncio
async def test_lead_generation_ranks_only_valid_leads():
    def responder(req):
        # mark the second lead invalid to prove filtering works
        if "Validate this company" in system_text(req) and "sample_company_name" in system_text(req):
            if getattr(responder, "seen", 0) == 1:
                return text_response(json.dumps({"company_name": "x", "is_valid": False, "reasoning": "already invested"}))
            responder.seen = 1
        return None

    llm = ScriptedLlm(requests=[], responder=responder)
    swap_models(lead_generation_agent, llm)
    state = dict(BASE_STATE, patterns_ready=True, discovered_patterns="1. Hiring local compliance staff")
    _, final, runner, sid = await run_agent(lead_generation_agent, "find leads", state)

    assert final["validated_lead_count"] == 1
    assert final["all_lead_findings"].count("--- ") == 1
    assert final["final_lead_report"] == "OK"
    artifacts = await runner.artifact_service.list_artifact_keys(
        app_name="test_app", user_id="tester", session_id=sid)
    assert "lead_report.md" in artifacts


def test_guardrail_blocks_leads_before_patterns():
    class Ctx:  # minimal ToolContext stand-in
        state = {"patterns_ready": False, "country": "Thailand", "industry": "fintech"}

    class Tool:
        name = "lead_generation_agent"

    assert before_tool_guard(Tool(), {}, Ctx())["status"] == "blocked"
    Ctx.state["patterns_ready"] = True
    assert before_tool_guard(Tool(), {}, Ctx()) is None


@pytest.mark.asyncio
async def test_root_agent_state_machine_end_to_end():
    """Root calls intent extractor -> set params -> pattern discovery, and the
    after_tool callback advances the stage."""

    def responder(req):
        text = system_text(req)
        if "InteractiveLeadGenerator" not in text:
            return None  # sub-agents: default schema/text behaviour
        part = last_part(req)
        done = part.function_response.name if part and part.function_response else None
        if done is None:
            return call_response("intent_extractor_agent", {"request": "fintech leads in Thailand"})
        if done == "intent_extractor_agent":
            return call_response("set_research_parameters", {"num_companies": 2, "num_leads": 2})
        if done == "set_research_parameters":
            return call_response("pattern_discovery_agent", {"request": "discover patterns"})
        return text_response("Here are the patterns. Shall I use these patterns to find leads?")

    llm = ScriptedLlm(requests=[], responder=responder)
    swap_models(root_agent, llm)
    _, state, *_ = await run_agent(root_agent, "Find fintech leads in Thailand")

    assert state["k"] == 2 and state["m"] == 2
    assert state["country"].startswith("sample_")   # value came from the extractor's structured output
    assert state["stage"] == "patterns_shown"
    assert state["patterns_ready"] is True
    assert state["discovered_patterns"] == "OK"
