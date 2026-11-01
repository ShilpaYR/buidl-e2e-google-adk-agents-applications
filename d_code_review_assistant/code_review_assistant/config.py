"""Typed, validated configuration (pydantic-settings): env vars or .env."""

from typing import Optional

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


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
