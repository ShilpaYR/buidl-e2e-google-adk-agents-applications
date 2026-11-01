"""Callbacks = deterministic code around the LLM.

* before_agent_run   — initialises session state every turn (dates, defaults).
* before_tool_guard  — guardrail: blocks lead generation until patterns exist.
* after_tool_run     — reads tool results and advances the `stage` state machine.

Note: an AgentTool is matched by `tool.name` (the wrapped agent's name).
Comparing `tool is some_agent` never matches, because the object passed in is
the AgentTool wrapper, not the agent.
"""

from datetime import datetime
from typing import Any, Optional

from google.adk.agents.callback_context import CallbackContext
from google.adk.tools.base_tool import BaseTool
from google.adk.tools.tool_context import ToolContext
from google.genai.types import Content

from .config import DEFAULT_COMPANIES, DEFAULT_LEADS

DEFAULTS: dict[str, Any] = {
    "country": "",
    "industry": "",
    "stage": "initial",
    "k": DEFAULT_COMPANIES,
    "m": DEFAULT_LEADS,
    "patterns_ready": False,
}


def before_agent_run(callback_context: CallbackContext) -> Optional[Content]:
    now = datetime.now()
    callback_context.state["current_date"] = now.strftime("%Y-%m-%d")
    callback_context.state["current_year"] = now.year
    for key, value in DEFAULTS.items():
        if key not in callback_context.state:
            callback_context.state[key] = value
    return None


def before_tool_guard(tool: BaseTool, args: dict, tool_context: ToolContext) -> Optional[dict]:
    if tool.name == "lead_generation_agent" and not tool_context.state.get("patterns_ready"):
        return {
            "status": "blocked",
            "message": "Pattern discovery must run (and be confirmed) before lead generation.",
        }
    if tool.name in ("pattern_discovery_agent", "lead_generation_agent"):
        if not tool_context.state.get("country") or not tool_context.state.get("industry"):
            return {"status": "blocked", "message": "Country and industry are required first."}
    return None


def after_tool_run(tool: BaseTool, args: dict, tool_context: ToolContext,
                   tool_response: Any) -> Optional[dict]:
    state = tool_context.state
    if tool.name == "intent_extractor_agent" and isinstance(tool_response, dict):
        if tool_response.get("country"):
            state["country"] = tool_response["country"]
        if tool_response.get("industry"):
            state["industry"] = tool_response["industry"]
        state["stage"] = tool_response.get("stage", state.get("stage"))
    elif tool.name == "pattern_discovery_agent" and state.get("discovered_patterns"):
        state["stage"] = "patterns_shown"
        state["patterns_ready"] = True
    elif tool.name == "lead_generation_agent" and state.get("final_lead_report"):
        state["stage"] = "report_ready"
    return None  # keep the original tool response
