"""Review pipeline: Analyzer -> StyleChecker -> TestRunner -> FeedbackSynthesizer.

Each agent enriches shared session state; `{key?}` placeholders inject that
state into the next agent's instruction at run time.
"""

from google.adk.agents import Agent, SequentialAgent
from google.adk.code_executors import BuiltInCodeExecutor

from ..config import config, critic_llm, worker_llm
from ..tools import (
    analyze_code_structure, capture_code, check_code_style, save_grading_report,
    search_past_feedback, update_grading_progress,
)

code_analyzer_agent = Agent(
    name="CodeAnalyzer",
    model=worker_llm,
    description="Parses the code with the AST and summarises its structure.",
    instruction=(
        "Call `analyze_code_structure` with no arguments (it reads the submitted code). "
        "If it reports a SyntaxError, state the line and message and stop. Otherwise summarise in "
        "3-5 bullets: what the code does, functions/classes, docstring coverage, complexity hotspots."
    ),
    tools=[analyze_code_structure],
    output_key="structure_analysis_summary",
)

style_checker_agent = Agent(
    name="StyleChecker",
    model=worker_llm,
    description="Runs PEP 8 checks and reports a style score.",
    instruction=(
        "Call `check_code_style` with no arguments. Report the score out of 100 and the most "
        "important violations grouped by type (with line numbers). Be brief."
    ),
    tools=[check_code_style],
    output_key="style_check_summary",
)

test_runner_agent = Agent(
    name="TestRunner",
    model=worker_llm,
    description="Writes and executes tests in a sandbox with the built-in code executor.",
    instruction="""You are a testing specialist. The code under test is:

```python
{code_to_review?}
```

Structure analysis: {structure_analysis_summary?}

1. Infer what the code is meant to do from names, docstrings and structure.
2. Write 10-15 focused test cases: normal behaviour, edge cases, invalid input.
3. EXECUTE them with your code executor (define the code first, then the tests,
   catching exceptions per test so one failure doesn't stop the rest).
4. Decide for each failure whether it is a bug or an intentional design choice.

Output ONLY a JSON object with these keys:
  "test_summary": total_tests_run, tests_passed, tests_failed, tests_with_errors, critical_issues_found
  "critical_issues": list of objects with type, description, example_input, expected_behavior,
                     actual_behavior, severity (high/medium/low)
  "verdict": status (WORKING/BUGGY/BROKEN), confidence, recommendation
Do not print the test code in your final answer.""",
    code_executor=BuiltInCodeExecutor(),
    output_key="test_execution_summary",
)

feedback_synthesizer_agent = Agent(
    name="FeedbackSynthesizer",
    model=critic_llm,
    description="Turns all findings into constructive, educational feedback and saves the report.",
    instruction="""You are a senior engineer mentoring a developer. Findings so far:

STRUCTURE: {structure_analysis_summary?}
STYLE: {style_check_summary?}
TESTS (JSON): {test_execution_summary?}

Steps:
1. Call `search_past_feedback` to see this developer's history and recurring issues.
2. Call `update_grading_progress` to compute the weighted grade.
3. Write the feedback in markdown:
   - **Summary** with the grade (letter + score) and verdict
   - **Strengths** (2-3 specific points)
   - **Critical issues** from the tests — what fails, a minimal fix for each
   - **Style** — top violations and how to fix them
   - **Progress** — compare with past reviews if any (recurring issue codes, score change)
   - **Next steps** — 3 prioritised actions
   Be encouraging but precise; quote line numbers.
4. Call `save_grading_report` with the full markdown as `feedback_text`.
5. Return the same markdown as your final answer.""",
    tools=[search_past_feedback, update_grading_progress, save_grading_report],
    output_key="final_feedback",
)

code_review_pipeline = SequentialAgent(
    name="CodeReviewPipeline",
    description="Full review: structure analysis, PEP 8 style, executed tests, synthesized feedback.",
    sub_agents=[code_analyzer_agent, style_checker_agent, test_runner_agent, feedback_synthesizer_agent],
    before_agent_callback=capture_code,
)
