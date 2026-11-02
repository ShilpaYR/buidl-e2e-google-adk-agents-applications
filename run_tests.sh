#!/usr/bin/env bash
# Run every agent's test suite. Optional live MCP integration tests run when
# Toolbox is up on :5002 (bug assistant) and :5001 (e-commerce).
set -uo pipefail
cd "$(dirname "$0")"
status=0
for dir in a_lead_generation_agent b_gemini_cli_tool_agent c_mcp_bug_assistant d_code_review_assistant e_ecommerce_agent; do
  echo "=== $dir"
  (cd "$dir" && python3 -m pytest -q -rs) || status=1
done
exit $status
