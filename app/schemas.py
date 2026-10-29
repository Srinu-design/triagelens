from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class TriageSuggestion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    category: Literal["BUG_REPORT", "FEATURE_REQUEST", "CLIENT_ENQUIRY"]
    severity: Literal["CRITICAL", "HIGH", "MEDIUM", "LOW"]
    subsystem: str = Field(min_length=1, max_length=40)
    sentiment: Literal["POSITIVE", "NEUTRAL", "FRICTION"] = "NEUTRAL"
    summary: str = Field(default="", max_length=120)
