"""Turns a free-text request into structured parameters (country, industry, stage)."""

from google.adk.agents import LlmAgent

from ..config import FAST_MODEL
from ..prompts import INTENT_EXTRACTOR_PROMPT
from ..schemas import IntentExtractionResult

intent_extractor_agent = LlmAgent(
    name="intent_extractor_agent",
    model=FAST_MODEL,
    description="Extracts target country, industry and conversation stage from the user's request.",
    instruction=INTENT_EXTRACTOR_PROMPT,
    output_schema=IntentExtractionResult,
    output_key="intent_extraction_result",
)
