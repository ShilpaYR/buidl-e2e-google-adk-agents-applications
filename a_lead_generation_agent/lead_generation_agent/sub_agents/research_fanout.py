"""Dynamic parallel fan-out — the heart of the deep-research design.

`ParallelResearchOrchestrator` is a custom `BaseAgent`. At run time it reads the
list of companies/leads from session state and builds, *for each item*, a
three-step `SequentialAgent` mini-pipeline:

    Validator (fast model, structured)  ->  Researcher (google_search)
                                         ->  Formatter (fast model, structured)

All mini-pipelines run concurrently inside one `ParallelAgent`, each in its
own branch so they don't see each other's conversation.

`FindingsConsolidator` then merges the per-item results into one text block.
It writes state through `EventActions.state_delta` (not by mutating
`ctx.session.state`), so the changes are persisted by the session service and
propagate back through `AgentTool` to the root agent.
"""

from __future__ import annotations

from typing import Any, AsyncGenerator, Literal, Union

from google.adk.agents import BaseAgent, LlmAgent, ParallelAgent, SequentialAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.agents.readonly_context import ReadonlyContext
from google.adk.events import Event, EventActions
from google.adk.models import BaseLlm
from google.adk.tools import google_search
from google.genai.types import Content, Part

from ..config import ADVANCED_MODEL, FAST_MODEL, MAX_ITEMS
from ..schemas import LeadSignalAnalyzerOutput, SignalSearcherOutput, ValidationResult

Mode = Literal["pattern", "lead"]
ModelRef = Union[str, BaseLlm]


def _describe(item: dict[str, Any]) -> str:
    lines = []
    for key, value in item.items():
        if isinstance(value, list):
            value = "; ".join(str(v) for v in value)
        lines.append(f"- {key.replace('_', ' ')}: {value}")
    return "\n".join(lines)


def _criteria(mode: Mode, country: str, industry: str) -> str:
    if mode == "pattern":
        return (
            f"1. Headquartered OUTSIDE {country}.\n"
            f"2. Made a concrete investment into {country} within the last 24 months.\n"
            f"3. Operates in or adjacent to the {industry} sector."
        )
    return (
        f"1. Headquartered OUTSIDE {country}.\n"
        f"2. Has NOT already made a major investment into {country}.\n"
        f"3. Operates in or adjacent to the {industry} sector.\n"
        f"4. Shows at least one current expansion signal."
    )


def keys_for(mode: Mode, i: int) -> dict[str, str]:
    p = "" if mode == "pattern" else "lead_"
    return {
        "validation": f"{p}validation_result_{i}",
        "raw": f"{p}research_raw_{i}",
        "findings": f"{p}research_findings_{i}",
    }


def build_item_pipeline(mode: Mode, i: int, item: dict[str, Any],
                        fast_model: ModelRef, advanced_model: ModelRef) -> SequentialAgent:
    keys = keys_for(mode, i)
    tag = "Company" if mode == "pattern" else "Lead"
    details = _describe(item)

    def validator_instruction(ctx: ReadonlyContext) -> str:
        s = ctx.state
        return (
            f"Validate this company against ALL criteria.\n\nCompany:\n{details}\n\n"
            f"Criteria:\n{_criteria(mode, s.get('country', ''), s.get('industry', ''))}\n\n"
            "Use only well-known facts and the information given. If unsure about a "
            "criterion, mark the company invalid and explain why."
        )

    def researcher_instruction(ctx: ReadonlyContext) -> str:
        s = ctx.state
        verdict = s.get(keys["validation"]) or {}
        if isinstance(verdict, dict) and verdict.get("is_valid") is False:
            return "This company failed validation. Reply with exactly: SKIPPED"
        country, industry = s.get("country", ""), s.get("industry", "")
        if mode == "pattern":
            task = (
                f"Research what this company did in the 6-18 months BEFORE its investment "
                f"into {country}: hiring for {country} roles, partnerships, regulatory "
                f"filings or licences, executive visits, funding, events, pilots."
            )
        else:
            patterns = s.get("discovered_patterns", "")
            task = (
                f"Assess how strongly this company currently matches these pre-investment "
                f"patterns for {country} ({industry}):\n{patterns}\n\n"
                "Give a fit score from 1 to 10 and a concrete outreach angle."
            )
        return (
            f"Today is {s.get('current_date', '')}.\n{task}\n\nCompany:\n{details}\n\n"
            "Use Google Search. Every claim needs a source URL. Be concise."
        )

    def formatter_instruction(ctx: ReadonlyContext) -> str:
        raw = ctx.state.get(keys["raw"], "")
        if isinstance(raw, str) and raw.strip().upper().startswith("SKIPPED"):
            extra = "The company was skipped: use summary 'SKIPPED', empty lists"
            extra += ", fit_score 1." if mode == "lead" else "."
        else:
            extra = "Do not add facts that are not in the notes."
        return f"Convert these research notes into the required schema. {extra}\n\nNotes:\n{raw}"

    return SequentialAgent(
        name=f"{tag}ResearchPipeline_{i}",
        sub_agents=[
            LlmAgent(
                name=f"{tag}Validator_{i}",
                model=fast_model,
                instruction=validator_instruction,
                output_schema=ValidationResult,
                output_key=keys["validation"],
                include_contents="none",
                disallow_transfer_to_parent=True,
                disallow_transfer_to_peers=True,
            ),
            LlmAgent(
                name=f"{tag}Researcher_{i}",
                model=advanced_model,
                instruction=researcher_instruction,
                tools=[google_search],
                output_key=keys["raw"],
                include_contents="none",
            ),
            LlmAgent(
                name=f"{tag}Formatter_{i}",
                model=fast_model,
                instruction=formatter_instruction,
                output_schema=SignalSearcherOutput if mode == "pattern" else LeadSignalAnalyzerOutput,
                output_key=keys["findings"],
                include_contents="none",
                disallow_transfer_to_parent=True,
                disallow_transfer_to_peers=True,
            ),
        ],
    )


def _items(ctx: InvocationContext, items_state_key: str, items_field: str) -> list[dict]:
    container = ctx.session.state.get(items_state_key) or {}
    if not isinstance(container, dict):
        return []
    return list(container.get(items_field) or [])[:MAX_ITEMS]


class ParallelResearchOrchestrator(BaseAgent):
    """Builds one mini-pipeline per item and runs them all in parallel."""

    mode: Mode
    items_state_key: str
    items_field: str
    fast_model: ModelRef = FAST_MODEL
    advanced_model: ModelRef = ADVANCED_MODEL

    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        items = _items(ctx, self.items_state_key, self.items_field)
        if not items:
            yield Event(
                author=self.name,
                invocation_id=ctx.invocation_id,
                content=Content(role="model", parts=[Part(text="No items to research.")]),
            )
            return

        pipelines = [
            build_item_pipeline(self.mode, i, item, self.fast_model, self.advanced_model)
            for i, item in enumerate(items)
        ]
        fan_out = ParallelAgent(name=f"{self.name}Parallel", sub_agents=pipelines)
        fan_out.parent_agent = self
        async for event in fan_out.run_async(ctx):
            yield event


class FindingsConsolidator(BaseAgent):
    """Merges per-item validation + research into one block for the synthesizer."""

    mode: Mode
    items_state_key: str
    items_field: str
    output_state_key: str

    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        state = ctx.session.state
        items = _items(ctx, self.items_state_key, self.items_field)
        blocks: list[tuple[int, str]] = []
        names: list[str] = []
        valid = 0

        for i, item in enumerate(items):
            keys = keys_for(self.mode, i)
            name = item.get("company_name", f"item {i + 1}")
            names.append(name)
            verdict = state.get(keys["validation"]) or {}
            findings = state.get(keys["findings"]) or {}
            is_valid = bool(verdict.get("is_valid"))
            valid += is_valid

            if self.mode == "lead" and not is_valid:
                continue  # only validated leads go into the final report
            score = int(findings.get("fit_score", 0) or 0)
            text = [f"--- {name} ({item.get('country_of_origin', '?')}) ---",
                    f"Validation: {'VALID' if is_valid else 'INVALID'} — {verdict.get('reasoning', 'n/a')}",
                    f"Summary: {findings.get('summary', 'n/a')}"]
            if self.mode == "pattern":
                text.append("Signals: " + "; ".join(findings.get("signals", []) or []))
            else:
                text += [f"Fit score: {score}/10",
                         "Matched patterns: " + "; ".join(findings.get("matched_patterns", []) or []),
                         f"Outreach angle: {findings.get('recommended_outreach_angle', 'n/a')}"]
            text.append("Sources: " + ", ".join(findings.get("sources", []) or [item.get("source_url", "")]))
            blocks.append((score, "\n".join(text)))

        if self.mode == "lead":
            blocks.sort(key=lambda b: b[0], reverse=True)  # rank by fit score
        merged = "\n\n".join(b for _, b in blocks) or "No validated results."

        delta: dict[str, Any] = {self.output_state_key: merged}
        if self.mode == "pattern":
            delta["analysed_companies"] = ", ".join(names)
            delta["validated_company_count"] = valid
        else:
            delta["validated_lead_count"] = valid

        yield Event(
            author=self.name,
            invocation_id=ctx.invocation_id,
            content=Content(role="model", parts=[Part(text=f"Consolidated {len(items)} items ({valid} valid).")]),
            actions=EventActions(state_delta=delta),
        )
