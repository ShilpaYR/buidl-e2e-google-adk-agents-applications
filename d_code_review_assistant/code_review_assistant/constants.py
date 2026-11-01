"""Single source of truth for state keys — no typo'd strings across agents."""


class StateKeys:
    # review pipeline (session scope)
    CODE_TO_REVIEW = "code_to_review"
    CODE_ANALYSIS = "code_analysis"
    CODE_LINE_COUNT = "code_line_count"
    SYNTAX_ERROR = "syntax_error"
    STYLE_SCORE = "style_score"
    STYLE_ISSUES = "style_issues"
    STYLE_ISSUE_COUNT = "style_issue_count"
    STRUCTURE_SUMMARY = "structure_analysis_summary"   # output_key
    STYLE_SUMMARY = "style_check_summary"              # output_key
    TEST_EXECUTION_SUMMARY = "test_execution_summary"  # output_key
    FINAL_FEEDBACK = "final_feedback"                  # output_key
    FINAL_GRADE = "final_grade"
    GRADING_ATTEMPTS = "grading_attempts"

    # fix pipeline
    CODE_FIXES = "code_fixes"                          # output_key
    FIX_TEST_EXECUTION_SUMMARY = "fix_test_execution_summary"
    FIXED_STYLE_SCORE = "fixed_style_score"
    FIXED_STYLE_ISSUES = "fixed_style_issues"
    FIX_REPORT = "fix_report"
    FIX_STATUS = "fix_status"
    FIX_ATTEMPTS = "fix_attempts"
    FINAL_FIX_REPORT = "final_fix_report"              # output_key

    # user scope — the `user:` prefix makes ADK persist it across sessions
    USER_REVIEW_HISTORY = "user:review_history"
    USER_LAST_STYLE_SCORE = "user:last_style_score"
