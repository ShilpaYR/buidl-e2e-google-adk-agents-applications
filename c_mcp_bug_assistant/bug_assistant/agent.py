"""QuantumRoast software bug assistant.

  user ─▶ software_bug_assistant (LlmAgent)
            ├─ get_current_date            FunctionTool
            ├─ search_agent                AgentTool(google_search)
            ├─ StackExchange               LangchainTool
            ├─ ToolboxToolset              MCP Toolbox ─▶ PostgreSQL / Cloud SQL (tickets)
            └─ McpToolset                  GitHub remote MCP server (read-only)

Run locally:
  toolbox --config mcp-toolbox/tools.local.yaml --port 5000   # terminal 1
  adk web                                                     # terminal 2
"""

import os

from google.adk.agents import Agent

from .prompt import agent_instruction
from .tools import ADVANCED_MODEL, all_tools

root_agent = Agent(
    model=ADVANCED_MODEL,
    name="software_bug_assistant",
    description="Triages and debugs software tickets using a ticket database, web, StackOverflow and GitHub.",
    instruction=agent_instruction,
    tools=all_tools(),
)
