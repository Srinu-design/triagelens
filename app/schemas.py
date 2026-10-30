import re
from typing import Any, Literal

from fastapi import Request
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.errors import ApiError

ERROR_CODE_PATTERN = re.compile(r"^[A-Z][A-Z0-9]*(_[A-Z0-9]+)+$")


def require_json(request: Request) -> None:
    content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if content_type != "application/json":
        raise ApiError(
            422, "VALIDATION_ERROR", "Invalid Content-Type", "Content-Type must be application/json"
        )


class TriageSuggestion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    category: Literal["BUG_REPORT", "FEATURE_REQUEST", "CLIENT_ENQUIRY"]
    severity: Literal["CRITICAL", "HIGH", "MEDIUM", "LOW"]
    subsystem: str = Field(min_length=1, max_length=40)
    sentiment: Literal["POSITIVE", "NEUTRAL", "FRICTION"] = "NEUTRAL"
    summary: str = Field(default="", max_length=120)


class ItemIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=3, max_length=200)
    description: str = Field(default="", max_length=4000)
    error_code: str | None = Field(default=None, max_length=64)
    category: Literal["BUG_REPORT", "FEATURE_REQUEST", "CLIENT_ENQUIRY"]
    action: Literal["NEW", "LINK", "VOTE", "ANSWER"]
    master_code: str | None = Field(default=None, max_length=20)

    @field_validator("error_code", mode="before")
    @classmethod
    def validate_error_code(cls, v: Any) -> str | None:
        if v is None:
            return None
        if isinstance(v, str):
            cleaned = v.strip()
            if not cleaned:
                return None
            cleaned = cleaned.upper()
            if len(cleaned) > 64 or not ERROR_CODE_PATTERN.match(cleaned):
                raise ApiError(
                    status=422,
                    code="INVALID_ERROR_CODE",
                    title="Invalid Error Code",
                    detail="error_code does not match the required pattern",
                )
            return cleaned
        raise ApiError(
            status=422,
            code="INVALID_ERROR_CODE",
            title="Invalid Error Code",
            detail="error_code must be a string",
        )


class ItemOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ticket_code: str
    category: Literal["BUG_REPORT", "FEATURE_REQUEST", "CLIENT_ENQUIRY"]
    title: str
    description: str
    error_code: str | None
    severity: Literal["CRITICAL", "HIGH", "MEDIUM", "LOW"]
    subsystem: str
    status: Literal["OPEN", "DUPLICATE", "RESOLVED_AUTO"]
    master: str | None
    resolution_note: str | None
    count_shown: int


class SessionResetOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    deleted_items: int
    deleted_votes: int


class PreviewIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=3, max_length=200)
    description: str = Field(default="", max_length=4000)
    error_code: str | None = Field(default=None, max_length=64)
    category_override: Literal["BUG_REPORT", "FEATURE_REQUEST", "CLIENT_ENQUIRY"] | None = None

    @field_validator("error_code", mode="before")
    @classmethod
    def validate_error_code(cls, value: Any) -> str | None:
        return ItemIn.validate_error_code(value)
