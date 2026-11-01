"""Finn root agent.

  browser ─▶ app.py (FastAPI: Google Sign-In verification, streaming /chat)
              └▶ Runner ─▶ finn (LlmAgent)
                            └─ ToolboxToolset ─MCP─▶ MCP Toolbox (Cloud Run)
                                                       └─▶ AlloyDB (vector search, orders)

Local dev:
  toolbox --config toolbox/tools.local.yaml --port 5001
  adk web            # dev UI, acts as DEMO_USER_EMAIL
  uvicorn app:app    # real web app with sign-in / demo-user picker
"""

import os

from google.adk.agents import Agent
from google.adk.tools.toolbox_toolset import ToolboxToolset

from .identity import get_id_token, get_user_email
from .prompt import FINN_INSTRUCTION

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # pragma: no cover
    pass

TOOLBOX_URL = os.getenv("ECOMMERCE_TOOLBOX_URL", "http://127.0.0.1:5001")
AUTH_MODE = os.getenv("AUTH_MODE", "demo").lower()


def build_toolset() -> ToolboxToolset:
    if AUTH_MODE == "google":
        # Toolbox verifies the Google ID token and fills user_email itself.
        return ToolboxToolset(server_url=TOOLBOX_URL, toolset_name="finn_toolset",
                              auth_token_getters={"google_signin": get_id_token})
    # Demo/local: bind user_email client-side; it is removed from the schema the model sees.
    return ToolboxToolset(server_url=TOOLBOX_URL, toolset_name="finn_toolset",
                          bound_params={"user_email": get_user_email})


root_agent = Agent(
    name="finn",
    model=os.getenv("FAST_MODEL", "gemini-3.8-flash"),
    description="GenAI Sports shopping assistant: products, shopping list, stores, orders.",
    instruction=FINN_INSTRUCTION,
    tools=[build_toolset()],
)
