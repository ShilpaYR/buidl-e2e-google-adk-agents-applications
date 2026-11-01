"""Fix pipeline: LoopAgent(CodeFixer -> FixTestRunner -> FixValidator) x <= N,
then FixSynthesizer.

The loop ends either when FixValidator calls `exit_fix_loop` (which sets
`tool_context.actions.escalate = True`) after a SUCCESSFUL fix, or when
`max_iterations` is reached — a hard stop against runaway cost.
"""

from google.adk.agents import Agent, LoopAgent, SequentialAgent
from google.adk.code_executors import BuiltInCodeExecutor

from ..config import config, critic_llm, worker_llm
from ..tools import compile_fix_report, exit_fix_loop, reset_fix_state, save_fix_report, validate_fixed_style

code_fixer_agent = Agent(
    name="CodeFixer",
    model=worker_llm,
    description="Rewrites the code to fix the reported bugs and style issues.",
    instruction="""Fix this Python code.

ORIGINAL CODE:
```python
{code_to_review?}
```
TEST FINDINGS: {test_execution_summary?}
STYLE ISSUES: {style_issues?}
PREVIOUS ATTEMPT (if any): {fix_report?}

Rules: fix every critical issue first, then style (PEP 8, max line 100). Keep the
public interface (function names/signatures) unless it is the bug. Add docstrings.
You may run the code with your executor to check it. Output ONLY the complete
fixed code in one ```python block — no explanation.""",
    code_executor=BuiltInCodeExecutor(),
    output_key="code_fixes",
)

fix_test_runner_agent = Agent(
    name="FixTestRunner",
    model=worker_llm,
    description="Re-runs the same kinds of tests against the fixed code.",
    instruction="""Test the FIXED code:

{code_fixes?}

Original test findings to re-check: {test_execution_summary?}

Write and EXECUTE 10-15 tests (including every previously failing scenario).
Output ONLY a JSON object with "test_summary" (total_tests_run, tests_passed,
tests_failed, tests_with_errors), "regressions" (list) and "verdict".""",
    code_executor=BuiltInCodeExecutor(),
    output_key="fix_test_execution_summary",
)

fix_validator_agent = Agent(
    name="FixValidator",
    model=worker_llm,
    description="Scores the fix and decides whether to stop the loop.",
    instruction="""Validate the latest fix attempt.
1. Call `validate_fixed_style` (no arguments).
2. Call `compile_fix_report` (no arguments). It returns status SUCCESSFUL, PARTIAL or FAILED.
3. If and only if the status is SUCCESSFUL, call `exit_fix_loop`.
4. Reply with: status, style before -> after, tests passed/total, remaining issues.""",
    tools=[validate_fixed_style, compile_fix_report, exit_fix_loop],
    output_key="final_fix_report",
)

fix_attempt_loop = LoopAgent(
    name="FixAttemptLoop",
    sub_agents=[code_fixer_agent, fix_test_runner_agent, fix_validator_agent],
    max_iterations=config.max_fix_attempts,
)

fix_synthesizer_agent = Agent(
    name="FixSynthesizer",
    model=critic_llm,
    description="Presents the final fixed code and what changed.",
    instruction="""Present the fix result to the developer.
Fix report: {fix_report?}
Fixed code:
{code_fixes?}

1. Call `save_fix_report` (no arguments) to store the fixed code and report.
2. Reply in markdown: status badge (SUCCESSFUL / PARTIAL / FAILED) and attempts used,
   a before/after table (style score, tests passed), a bullet list of what changed and why,
   the complete fixed code, and anything that still needs manual attention.""",
    tools=[save_fix_report],
    output_key="fix_summary",
)

code_fix_pipeline = SequentialAgent(
    name="CodeFixPipeline",
    description="Automatically fixes the reviewed code with up to N validated attempts.",
    sub_agents=[fix_attempt_loop, fix_synthesizer_agent],
    before_agent_callback=reset_fix_state,
)
