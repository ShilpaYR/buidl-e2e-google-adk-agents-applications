"""Typed, validated configuration (pydantic-settings): env vars or .env."""

import os
from typing import Optional

from dotenv import load_dotenv

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# pydantic-settings reads .env into `config` only; the Gemini client reads GOOGLE_API_KEY
# from the process environment, so load .env there too (agent folder or its parent).
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env"))


class AgentConfig(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8",
                                      case_sensitive=False, extra="ignore")

    # models: cheap "worker" for mechanical steps, stronger "critic" for judgement
    worker_model: str = Field(default="gemini-3.8-flash", alias="FAST_MODEL")
    critic_model: str = Field(default="gemini-3.8-flash", alias="ADVANCED_MODEL")

    # grading
    passing_score_threshold: float = 0.8
    style_weight: float = 0.3
    test_weight: float = 0.5
    structure_weight: float = 0.2
    max_fix_attempts: int = Field(default=3, ge=1, le=5)
    max_code_chars: int = 40_000  # reject huge inputs (cost / prompt-injection surface)

    # deployment
    google_cloud_project: Optional[str] = None
    google_cloud_location: str = "us-central1"
    artifact_bucket: Optional[str] = None

    @model_validator(mode="after")
    def weights_sum_to_one(self) -> "AgentConfig":
        total = self.style_weight + self.test_weight + self.structure_weight
        if abs(total - 1.0) > 1e-6:
            raise ValueError(f"grading weights must sum to 1.0 (got {total})")
        return self


config = AgentConfig()


def gemini(name: str):
    """Gemini model with automatic retries + exponential backoff on 429/5xx
    (rate limits and 'model is experiencing high demand' 503s are usually brief)."""
    from google.adk.models import Gemini
    from google.genai import types

    return Gemini(model=name, retry_options=types.HttpRetryOptions(
        attempts=int(os.getenv("GEMINI_RETRY_ATTEMPTS", "6")), initial_delay=2.0, max_delay=30.0,
        exp_base=2.0, http_status_codes=[408, 429, 500, 502, 503, 504]))


worker_llm = gemini(config.worker_model)
critic_llm = gemini(config.critic_model)
