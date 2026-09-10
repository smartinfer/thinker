"""Stable error vocabulary for the model-turn V1 machine boundary."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict


class ModelTurnErrorCode(str, Enum):
    MODEL_UNAVAILABLE = "ModelUnavailable"
    AUTHENTICATION_FAILURE = "AuthenticationFailure"
    RATE_LIMITED = "RateLimited"
    PROVIDER_OVERLOADED = "ProviderOverloaded"
    REQUEST_TIMEOUT = "RequestTimeout"
    CANCELLED = "Cancelled"
    MALFORMED_RESPONSE = "MalformedResponse"
    TOOL_CALL_MALFORMED = "ToolCallMalformed"
    STRUCTURED_OUTPUT_VIOLATION = "StructuredOutputViolation"
    UNKNOWN_PROVIDER_FAILURE = "UnknownProviderFailure"
    INCOMPLETE = "Incomplete"


class ModelTurnError(BaseModel):
    """A sanitized provider-neutral failure returned instead of an exception."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    code: ModelTurnErrorCode
    message: str
    retryable: bool = False
    provider_status: int | None = None
    provider_code: str | None = None


class ModelTurnProviderException(Exception):
    """Internal typed exception; it never crosses the JSON boundary directly."""

    def __init__(self, error: ModelTurnError) -> None:
        self.error = error
        super().__init__(error.message)
