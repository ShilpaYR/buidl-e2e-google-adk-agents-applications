"""Per-request user identity, kept OUT of the LLM's hands.

The web server sets these context variables for each request (after verifying
the Google ID token). Toolbox reads them when a tool is invoked:

* AUTH_MODE=google — the raw ID token is forwarded; MCP Toolbox verifies it and
  extracts `email` itself (authServices in tools.yaml).
* AUTH_MODE=demo   — the email is bound client-side (bound_params), hidden from
  the model's function declarations.

Context variables are per-asyncio-task, so concurrent users never see each
other's identity.
"""

import contextvars
import os

current_id_token: contextvars.ContextVar[str] = contextvars.ContextVar("current_id_token", default="")
current_user_email: contextvars.ContextVar[str] = contextvars.ContextVar("current_user_email", default="")

DEMO_USER_EMAIL = os.getenv("DEMO_USER_EMAIL", "alex.rivera@example.com")


def get_user_email() -> str:
    """Bound-param getter for demo mode (falls back to the demo user in `adk web`)."""
    return current_user_email.get() or DEMO_USER_EMAIL


async def get_id_token() -> str:
    """Auth-token getter for production mode."""
    return current_id_token.get()
