"""Root agent: the interactive orchestrator ("primary orchestrator" in the blog).

   user ─▶ InteractiveLeadGenerator (LlmAgent)
             ├─ intent_extractor_agent   (AgentTool → LlmAgent + output_schema)
             ├─ set_research_parameters  (FunctionTool)
             ├─ pattern_discovery_agent  (AgentTool → SequentialAgent w/ dynamic ParallelAgent)
             └─ lead_generation_agent    (AgentTool → SequentialAgent w/ dynamic ParallelAgent)

Run locally:  cd a_lead_generation_agent && adk web
"""

from google.adk.agents import Agent

from .callbacks import after_tool_run, before_agent_run, before_tool_guard
from .config import ADVANCED_MODEL
from .prompts import ROOT_AGENT_INSTRUCTION
from .tools import agent_tools, set_research_parameters

root_agent = Agent(
    name="InteractiveLeadGenerator",
    model=ADVANCED_MODEL,
    description="Deep-research lead generation: learns pre-investment patterns, then finds look-alike leads.",
    instruction=ROOT_AGENT_INSTRUCTION,
    tools=[set_research_parameters, *agent_tools],
    before_agent_callback=before_agent_run,
    before_tool_callback=before_tool_guard,
    after_tool_callback=after_tool_run,
)
