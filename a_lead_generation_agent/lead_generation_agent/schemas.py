"""Pydantic contracts between agents. Structured output makes every hand-off
machine-checkable instead of "LLM text in, LLM text out"."""

from typing import List, Literal, Optional

from pydantic import BaseModel, Field


# ---------- intent ----------
class IntentExtractionResult(BaseModel):
    country: Optional[str] = Field(default=None, description="Target market country, e.g. 'Thailand'.")
    industry: Optional[str] = Field(default=None, description="Target industry, e.g. 'fintech'.")
    stage: Literal["pattern_discovery", "lead_generation", "follow_up", "chitchat"] = Field(
        description="Which workflow the user is asking for right now."
    )
    intent: Literal["find_leads", "find_patterns", "company_research", "general_chat"]
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning: str


# ---------- pattern discovery ----------
class Company(BaseModel):
    company_name: str
    country_of_origin: str = Field(description="Headquarters country.")
    investment_type: str = Field(description="New office, factory, acquisition, JV, licence, ...")
    investment_date: str = Field(description="Approximate date, e.g. '2025-Q4'.")
    source_url: str
    business_description: str


class CompanyFinderOutput(BaseModel):
    companies_found: List[Company]


class ValidationResult(BaseModel):
    company_name: str
    is_valid: bool = Field(description="True only if every validation criterion is met.")
    reasoning: str
    corrected_country_of_origin: Optional[str] = None


class SignalSearcherOutput(BaseModel):
    summary: str = Field(description="What the company did in the 6-18 months before investing.")
    signals: List[str] = Field(description="Discrete pre-investment signals, one per item.")
    sources: List[str] = Field(description="URLs backing the signals.")


# ---------- lead generation ----------
class Lead(BaseModel):
    company_name: str
    country_of_origin: str
    business_description: str
    observed_signals: List[str]
    source_url: str


class LeadFinderOutput(BaseModel):
    potential_leads: List[Lead]


class LeadSignalAnalyzerOutput(BaseModel):
    summary: str
    matched_patterns: List[str]
    fit_score: int = Field(ge=1, le=10, description="1 = weak lead, 10 = textbook match.")
    recommended_outreach_angle: str
    sources: List[str]
