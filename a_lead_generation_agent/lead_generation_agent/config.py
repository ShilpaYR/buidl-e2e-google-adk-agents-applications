"""Central configuration (models and limits) read from the environment."""

import os

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # pragma: no cover
    pass

# Cheap, fast model for extraction / formatting / validation.
FAST_MODEL = os.getenv("FAST_MODEL", "gemini-3.8-flash")
# Stronger model for open-ended web research and synthesis.
ADVANCED_MODEL = os.getenv("ADVANCED_MODEL", "gemini-3.8-flash")

MIN_ITEMS = 1
MAX_ITEMS = 10  # hard cap on parallel fan-out (cost + rate-limit protection)
DEFAULT_COMPANIES = 3
DEFAULT_LEADS = 3
