"""Central configuration (models and limits) read from the environment."""

import os

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # pragma: no cover
    pass

# Cheap, fast model for extraction / formatting / validation.
def gemini(name: str):
    """Gemini model with automatic retries + exponential backoff on 429/5xx
    (rate limits and 'model is experiencing high demand' 503s are usually brief)."""
    from google.adk.models import Gemini
    from google.genai import types

    return Gemini(model=name, retry_options=types.HttpRetryOptions(
        attempts=int(os.getenv("GEMINI_RETRY_ATTEMPTS", "6")), initial_delay=2.0, max_delay=30.0,
        exp_base=2.0, http_status_codes=[408, 429, 500, 502, 503, 504]))


FAST_MODEL = gemini(os.getenv("FAST_MODEL", "gemini-3.8-flash"))
# Stronger model for open-ended web research and synthesis.
ADVANCED_MODEL = gemini(os.getenv("ADVANCED_MODEL", "gemini-3.8-flash"))

MIN_ITEMS = 1
MAX_ITEMS = 10  # hard cap on parallel fan-out (cost + rate-limit protection)
DEFAULT_COMPANIES = 3
DEFAULT_LEADS = 3
