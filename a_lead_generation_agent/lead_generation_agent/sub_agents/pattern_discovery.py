"""Workflow 1 — "learn from the past" (the Research Squad).

SequentialAgent:
  CompanyFinder (google_search) -> CompanyFormatter (schema)
  -> ResearchOrchestrator (dynamic ParallelAgent: validate + research each company)
  -> SynthesizerOrchestrator (merge) -> PatternSynthesizer (markdown patterns)
"""

from google.adk.agents import LlmAgent, SequentialAgent
from google.adk.tools import google_search

from ..config import ADVANCED_MODEL, FAST_MODEL
from ..prompts import COMPANY_FINDER_PROMPT, COMPANY_FORMATTER_PROMPT, PATTERN_SYNTHESIZER_PROMPT
from ..schemas import CompanyFinderOutput
from .research_fanout import FindingsConsolidator, ParallelResearchOrchestrator

company_finder_agent = LlmAgent(
    name="CompanyFinderAgent",
    model=ADVANCED_MODEL,
    description="Searches the web for companies that recently entered the target market.",
    instruction=COMPANY_FINDER_PROMPT,
    tools=[google_search],
    output_key="company_finder_output",
)

company_formatter_agent = LlmAgent(
    name="CompanyFormatterAgent",
    model=FAST_MODEL,
    description="Normalises the finder's notes into CompanyFinderOutput JSON.",
    instruction=COMPANY_FORMATTER_PROMPT,
    output_schema=CompanyFinderOutput,
    output_key="companies_found_structured",
    include_contents="none",
)

research_orchestrator_agent = ParallelResearchOrchestrator(
    name="ResearchOrchestrator",
    description="Validates and researches every found company in parallel.",
    mode="pattern",
    items_state_key="companies_found_structured",
    items_field="companies_found",
)

synthesizer_orchestrator_agent = FindingsConsolidator(
    name="SynthesizerOrchestrator",
    description="Merges the parallel research results into one block.",
    mode="pattern",
    items_state_key="companies_found_structured",
    items_field="companies_found",
    output_state_key="all_research_findings",
)

pattern_synthesizer_agent = LlmAgent(
    name="PatternSynthesizerAgent",
    model=ADVANCED_MODEL,
    description="Finds the common pre-investment patterns, with citations.",
    instruction=PATTERN_SYNTHESIZER_PROMPT,
    output_key="discovered_patterns",
    include_contents="none",
)

pattern_discovery_agent = SequentialAgent(
    name="pattern_discovery_agent",
    description=(
        "Finds k companies that recently invested in the target country/industry, validates and "
        "researches each in parallel, and returns the common pre-investment patterns with sources."
    ),
    sub_agents=[
        company_finder_agent,
        company_formatter_agent,
        research_orchestrator_agent,
        synthesizer_orchestrator_agent,
        pattern_synthesizer_agent,
    ],
)
