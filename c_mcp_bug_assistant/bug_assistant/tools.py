"""Five kinds of ADK tools in one agent:

1. Function tool      — get_current_date
2. Built-in tool      — google_search, wrapped as an agent-as-a-tool
3. Third-party tool   — LangChain StackExchange (StackOverflow) via LangchainTool
4. MCP tool (database)— MCP Toolbox for Databases → PostgreSQL / Cloud SQL tickets DB
5. MCP tool (API)     — GitHub's remote MCP server, filtered to read-only tools

Every external dependency degrades gracefully: if a server or token is
missing, that tool is left out and the agent still starts (useful for CI and
for recording the demo step by step).
"""

from __future__ import annotations

import logging
import os
import urllib.request
from datetime import datetime
from typing import Any, Optional

from google.adk.agents import Agent
from google.adk.tools import google_search
from google.adk.tools.agent_tool import AgentTool

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # pragma: no cover
    pass

log = logging.getLogger(__name__)
FAST_MODEL = os.getenv("FAST_MODEL", "gemini-3.8-flash")


# ---------------------------------------------------------------- 1. function tool
def get_current_date() -> dict:
    """Get today's date (YYYY-MM-DD). Use it for relative dates like 'last week'."""
    return {"current_date": datetime.now().strftime("%Y-%m-%d")}


# ---------------------------------------------------------------- 2. built-in tool
search_agent = Agent(
    model=FAST_MODEL,
    name="search_agent",
    description="Searches the web (Google Search) for error messages, CVEs and known fixes.",
    instruction="You're a specialist in Google Search. Answer with concise findings and source URLs.",
    tools=[google_search],
)
search_tool = AgentTool(agent=search_agent)


# ---------------------------------------------------------------- 3. third-party (LangChain)
def _stackexchange_tool() -> Optional[Any]:
    try:
        from google.adk.tools.langchain_tool import LangchainTool
        from langchain_community.tools import StackExchangeTool
        from langchain_community.utilities import StackExchangeAPIWrapper

        return LangchainTool(StackExchangeTool(api_wrapper=StackExchangeAPIWrapper()))
    except Exception as exc:  # missing optional deps
        log.warning("StackExchange tool disabled: %s", exc)
        return None


langchain_tool = _stackexchange_tool()


# ---------------------------------------------------------------- 4. MCP Toolbox (database)
TOOLBOX_URL = os.getenv("MCP_TOOLBOX_URL", "http://127.0.0.1:5000").rstrip("/")
TOOLBOX_TOOLSET = os.getenv("TOOLBOX_TOOLSET", "tickets_toolset")


def _toolbox_reachable(url: str, toolset: str) -> bool:
    """Cheap liveness probe. (Toolbox 1.x answers '/' with 200; the legacy
    /api/toolset REST route now returns 410 Gone — clients speak MCP.)"""
    try:
        with urllib.request.urlopen(f"{url}/", timeout=2) as resp:
            return resp.status == 200
    except Exception:
        return False


def _toolbox_toolset() -> Optional[Any]:
    if not _toolbox_reachable(TOOLBOX_URL, TOOLBOX_TOOLSET):
        log.warning("MCP Toolbox not reachable at %s — ticket DB tools disabled.", TOOLBOX_URL)
        return None
    from google.adk.tools.toolbox_toolset import ToolboxToolset

    return ToolboxToolset(server_url=TOOLBOX_URL, toolset_name=TOOLBOX_TOOLSET)


toolbox_tools = _toolbox_toolset()


# ---------------------------------------------------------------- 5. GitHub remote MCP (API)
READ_VERBS = ("search_", "list_", "get_")
READ_DOMAINS = ("issue", "pull_request", "repositor", "commit", "release", "code")


def github_read_only(tool: Any, readonly_context: Any = None) -> bool:
    """Tool filter: expose only read-only issue/PR/repo tools.
    Hides write actions from the model and keeps the tool list small."""
    name = getattr(tool, "name", str(tool))
    is_read = name.startswith(READ_VERBS) or name.endswith("_read")
    return is_read and any(d in name for d in READ_DOMAINS)


def _github_toolset() -> Optional[Any]:
    token = os.getenv("GITHUB_PERSONAL_ACCESS_TOKEN")
    if not token:
        log.warning("GITHUB_PERSONAL_ACCESS_TOKEN not set — GitHub MCP tools disabled.")
        return None
    from google.adk.tools.mcp_tool import McpToolset, StreamableHTTPConnectionParams

    return McpToolset(
        connection_params=StreamableHTTPConnectionParams(
            url=os.getenv("GITHUB_MCP_URL", "https://api.githubcopilot.com/mcp/"),
            headers={
                "Authorization": f"Bearer {token}",
                "X-MCP-Readonly": "true",                        # server-side read-only mode
                "X-MCP-Toolsets": "issues,pull_requests,repos",  # only the toolsets we need
            },
        ),
        tool_filter=github_read_only,                            # client-side allow-list too
    )


github_mcp_tools = _github_toolset()


def all_tools() -> list:
    tools: list = [get_current_date, search_tool]
    for optional in (langchain_tool, toolbox_tools, github_mcp_tools):
        if optional is not None:
            tools.append(optional)
    return tools
