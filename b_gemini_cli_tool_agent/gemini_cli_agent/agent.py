"""Gemini CLI inside an ADK pipeline.

  user ─▶ GeminiCliDevAssistant (root LlmAgent)
            ├─ clone_repository         (async FunctionTool, git)
            ├─ gemini_cli               (async FunctionTool → `gemini -p ... -o json`)
            └─ repo_analysis_pipeline   (AgentTool → SequentialAgent)
                  1. RepoIntakeAgent         clone + overview
                  2. CodebaseAnalysisSquad   ParallelAgent of 3 analysts, each calls gemini_cli
                        ArchitectureAnalyst · TestPlanner · QualityAuditor
                  3. ReportSynthesizer       merges into one report (+ saved artifact)

The ADK agent does the planning and orchestration; Gemini CLI does the
repository-scale work (it decides which files to read, greps, follows imports).

Run locally:  cd b_gemini_cli_tool_agent && adk web
"""

import os
from typing import Any, Optional

from google.adk.agents import Agent, LlmAgent, ParallelAgent, SequentialAgent
from google.adk.agents.callback_context import CallbackContext
from google.adk.tools.agent_tool import AgentTool
from google.adk.tools.base_tool import BaseTool
from google.adk.tools.tool_context import ToolContext
from google.genai import types

from .tools import clone_repository, gemini_cli

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # pragma: no cover
    pass

FAST_MODEL = os.getenv("FAST_MODEL", "gemini-3.8-flash")
ADVANCED_MODEL = os.getenv("ADVANCED_MODEL", "gemini-3.8-flash")
MAX_CLI_CALLS_PER_SESSION = int(os.getenv("GEMINI_CLI_MAX_CALLS", "12"))


COUNTER_PREFIX = "gemini_cli_calls__"


def cli_calls_used(state: Any) -> int:
    data = state.to_dict() if hasattr(state, "to_dict") else dict(state)
    return sum(int(v) for k, v in data.items() if k.startswith(COUNTER_PREFIX))


def cli_budget_guard(tool: BaseTool, args: dict, tool_context: ToolContext) -> Optional[dict]:
    """Cost guardrail: each Gemini CLI call is itself an agent run.

    Each agent increments its OWN counter key. A single shared counter would
    lose updates when the ParallelAgent branches write it concurrently
    (state deltas merge last-writer-wins)."""
    if tool.name != "gemini_cli":
        return None
    if cli_calls_used(tool_context.state) >= MAX_CLI_CALLS_PER_SESSION:
        return {"status": "error", "message": "Gemini CLI call budget for this session is used up."}
    key = COUNTER_PREFIX + tool_context.agent_name
    tool_context.state[key] = int(tool_context.state.get(key, 0)) + 1
    return None


# ---------------------------------------------------------------- pipeline
repo_intake_agent = LlmAgent(
    name="RepoIntakeAgent",
    model=FAST_MODEL,
    instruction=(
        "Find the GitHub repository URL in the user's request and call `clone_repository` with it. "
        "If there is no URL, reply 'NO_REPOSITORY' and stop. Otherwise reply with a 3-line overview: "
        "repository name, number of files, and the main file types."
    ),
    tools=[clone_repository],
    output_key="repo_overview",
    before_tool_callback=cli_budget_guard,
)


def _analyst(name: str, task: str, output_key: str, heading: str) -> LlmAgent:
    return LlmAgent(
        name=name,
        model=FAST_MODEL,
        instruction=(
            f"You delegate repository analysis to Gemini CLI. Call `gemini_cli` exactly once with "
            f"this task:\n\n{task}\n\nThen return the result under the heading '## {heading}'. Keep "
            "Gemini CLI's file paths and code blocks; trim filler. If the tool returns an error, "
            "report the error message instead and do not invent an analysis."
        ),
        tools=[gemini_cli],
        output_key=output_key,
        include_contents="none",
        before_tool_callback=cli_budget_guard,
    )


architecture_analyst = _analyst(
    "ArchitectureAnalyst",
    "Explain this codebase: its purpose, main components and how they interact, entry points, "
    "key dependencies and the main data flow. Reference concrete file paths.",
    "architecture_report",
    "Architecture",
)
test_planner = _analyst(
    "TestPlanner",
    "Create a test plan for this codebase: list the critical paths, then a table of unit and "
    "integration test cases (name, target file/function, scenario, expected result). Finally write "
    "three example unit tests for the most important module. Do not modify any files.",
    "test_plan_report",
    "Test plan",
)
quality_auditor = _analyst(
    "QualityAuditor",
    "Review this codebase for code quality and security. Report the top 5 issues, each with "
    "file path (and line if possible), severity (high/medium/low), why it matters and a concrete fix. "
    "Include dependency and configuration risks.",
    "quality_report",
    "Quality & security",
)

analysis_squad = ParallelAgent(
    name="CodebaseAnalysisSquad",
    description="Three Gemini CLI analysts working on the same repository concurrently.",
    sub_agents=[architecture_analyst, test_planner, quality_auditor],
)


async def save_report(callback_context: CallbackContext) -> Optional[types.Content]:
    report = callback_context.state.get("final_repo_report")
    if report:
        try:
            await callback_context.save_artifact(
                "repo_analysis_report.md",
                types.Part.from_bytes(data=report.encode(), mime_type="text/markdown"),
            )
        except ValueError:
            pass
    return None


report_synthesizer = LlmAgent(
    name="ReportSynthesizer",
    model=ADVANCED_MODEL,
    instruction=(
        "Combine the analyses below into one onboarding report for {repo_name?}.\n\n"
        "Overview:\n{repo_overview?}\n\n{architecture_report?}\n\n{test_plan_report?}\n\n"
        "{quality_report?}\n\n"
        "Structure: 1) TL;DR (3 bullets) 2) Architecture 3) Test plan 4) Quality & security "
        "(table: issue, severity, file, fix) 5) Recommended next steps (top 3). Keep file paths. "
        "Only use the material above; if a section reported an error, say so."
    ),
    output_key="final_repo_report",
    include_contents="none",
    after_agent_callback=save_report,
)

repo_analysis_pipeline = SequentialAgent(
    name="repo_analysis_pipeline",
    description=(
        "Full repository analysis: clones the GitHub repo, runs three Gemini CLI analysts in parallel "
        "(architecture, test plan, quality/security) and returns one consolidated report. Pass the "
        "GitHub URL in the request."
    ),
    sub_agents=[repo_intake_agent, analysis_squad, report_synthesizer],
)


# ---------------------------------------------------------------- root
root_agent = Agent(
    name="GeminiCliDevAssistant",
    model=ADVANCED_MODEL,
    description="Software-engineering assistant that uses Gemini CLI as a tool to analyse GitHub repositories.",
    instruction=(
        "You are a world-class software engineer. Gemini CLI is your power tool: it can read and "
        "reason over an entire repository.\n"
        "Current repository: {repo_name?}\n\n"
        "- For a full analysis, onboarding report or review of a repository, call "
        "`repo_analysis_pipeline` with a request that includes the GitHub URL.\n"
        "- For a specific question (explain a module, find where X happens, draft tests for a file), "
        "call `clone_repository` if needed, then `gemini_cli` with a precise, self-contained task.\n"
        "- Always ground answers in tool output and say they came from Gemini CLI. Never invent file "
        "names. If no repository is known, ask for a GitHub URL."
    ),
    tools=[clone_repository, gemini_cli, AgentTool(agent=repo_analysis_pipeline)],
    before_tool_callback=cli_budget_guard,
)
