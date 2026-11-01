"""Workflow 2 — "predict the future" (the Hunter Squad).

Mirrors pattern discovery and reuses the same fan-out components:
  LeadFinder (google_search, uses discovered patterns) -> LeadFormatter (schema)
  -> LeadResearchOrchestrator (parallel validate + fit-score each lead)
  -> ReportOrchestrator (merge + rank) -> ReportCompiler (final markdown report)
"""

from typing import Optional

from google.adk.agents import LlmAgent, SequentialAgent
from google.adk.agents.callback_context import CallbackContext
from google.adk.tools import google_search
from google.genai import types

from ..config import ADVANCED_MODEL, FAST_MODEL
from ..prompts import LEAD_FINDER_PROMPT, LEAD_FORMATTER_PROMPT, REPORT_COMPILER_PROMPT
from ..schemas import LeadFinderOutput
from .research_fanout import FindingsConsolidator, ParallelResearchOrchestrator

lead_finder_agent = LlmAgent(
    name="LeadFinderAgent",
    model=ADVANCED_MODEL,
    description="Uses the discovered patterns as search criteria to find look-alike companies.",
    instruction=LEAD_FINDER_PROMPT,
    tools=[google_search],
    output_key="lead_finder_output",
)

lead_formatter_agent = LlmAgent(
    name="LeadFormatterAgent",
    model=FAST_MODEL,
    instruction=LEAD_FORMATTER_PROMPT,
    output_schema=LeadFinderOutput,
    output_key="leads_found_structured",
    include_contents="none",
)

lead_research_orchestrator_agent = ParallelResearchOrchestrator(
    name="LeadResearchOrchestrator",
    description="Validates each lead and scores its fit against the patterns, in parallel.",
    mode="lead",
    items_state_key="leads_found_structured",
    items_field="potential_leads",
)

report_orchestrator_agent = FindingsConsolidator(
    name="ReportOrchestrator",
    description="Keeps only validated leads and ranks them by fit score.",
    mode="lead",
    items_state_key="leads_found_structured",
    items_field="potential_leads",
    output_state_key="all_lead_findings",
)


async def save_report_artifact(callback_context: CallbackContext) -> Optional[types.Content]:
    """Persist the final report as a downloadable artifact (visible in adk web)."""
    report = callback_context.state.get("final_lead_report")
    if report:
        try:
            await callback_context.save_artifact(
                "lead_report.md", types.Part.from_bytes(data=report.encode(), mime_type="text/markdown")
            )
        except ValueError:
            pass  # no artifact service configured (e.g. some test runners)
    return None


report_compiler_agent = LlmAgent(
    name="ReportCompilerAgent",
    model=ADVANCED_MODEL,
    instruction=REPORT_COMPILER_PROMPT,
    output_key="final_lead_report",
    include_contents="none",
    after_agent_callback=save_report_artifact,
)

lead_generation_agent = SequentialAgent(
    name="lead_generation_agent",
    description=(
        "Uses previously discovered patterns to find m new leads, validates and scores each in "
        "parallel, and returns a ranked report with sources. Requires pattern discovery first."
    ),
    sub_agents=[
        lead_finder_agent,
        lead_formatter_agent,
        lead_research_orchestrator_agent,
        report_orchestrator_agent,
        report_compiler_agent,
    ],
)
