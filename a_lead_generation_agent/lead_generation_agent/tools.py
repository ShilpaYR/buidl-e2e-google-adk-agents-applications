"""Tools exposed to the root agent: one function tool + three agents-as-tools."""

from google.adk.tools import ToolContext
from google.adk.tools.agent_tool import AgentTool

from .config import MAX_ITEMS, MIN_ITEMS
from .sub_agents import intent_extractor_agent, lead_generation_agent, pattern_discovery_agent


def set_research_parameters(num_companies: int, num_leads: int, tool_context: ToolContext) -> dict:
    """Store how many past entrants to analyse and how many new leads to find.

    Args:
        num_companies: Companies to analyse for pattern discovery (1-10).
        num_leads: New leads to find (1-10).
    """
    k = max(MIN_ITEMS, min(MAX_ITEMS, int(num_companies)))
    m = max(MIN_ITEMS, min(MAX_ITEMS, int(num_leads)))
    tool_context.state["k"] = k
    tool_context.state["m"] = m
    tool_context.state["stage"] = "pattern_discovery"
    return {"status": "ok", "k": k, "m": m}


# AgentTool lets the root agent call a whole multi-step workflow like a
# function; state written inside the workflow flows back to the root session.
agent_tools = [
    AgentTool(agent=intent_extractor_agent),
    AgentTool(agent=pattern_discovery_agent),
    AgentTool(agent=lead_generation_agent),
]
