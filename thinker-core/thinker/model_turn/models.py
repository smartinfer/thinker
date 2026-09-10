"""Canonical JSON-safe types for ``thinker.model-turn.v1``."""

from __future__ import annotations

from enum import Enum
from typing import Any, Final, Literal

import jsonschema
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    ValidationInfo,
    field_validator,
    model_validator,
)

from .errors import ModelTurnError

PROTOCOL_VERSION: Final = "thinker.model-turn.v1"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class MessageRole(str, Enum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"

class ReasoningEffort(str, Enum):
    """Provider-neutral reasoning-effort level for a model turn. Absence (field
    unset / None) preserves prior behavior; NONE explicitly disables reasoning.
    Provider mapping is capability-gated at the route (see runtime dispatch)."""
    NONE = "none"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ToolCall(StrictModel):
    call_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    arguments: dict[str, JsonValue]


class ModelMessage(StrictModel):
    role: MessageRole
    content: str | None = None
    tool_calls: tuple[ToolCall, ...] = ()

    @model_validator(mode="after")
    def require_content_or_calls(self) -> ModelMessage:
        if self.content is None and not self.tool_calls:
            raise ValueError("message requires content or tool_calls")
        if self.role != MessageRole.ASSISTANT and self.tool_calls:
            raise ValueError("only assistant messages may contain tool_calls")
        return self


class ToolDefinition(StrictModel):
    name: str = Field(min_length=1, pattern=r"^[A-Za-z0-9_-]+$")
    description: str = Field(min_length=1)
    input_schema: dict[str, JsonValue]
    strict: bool = True

    @field_validator("input_schema")
    @classmethod
    def require_object_schema(cls, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        if value.get("type") != "object":
            raise ValueError("tool input_schema must declare type=object")
        try:
            jsonschema.validators.validator_for(value).check_schema(value)
        except jsonschema.SchemaError as exc:
            raise ValueError("tool input_schema is not a valid JSON Schema") from exc
        return value


class ToolResult(StrictModel):
    call_id: str = Field(min_length=1)
    output: JsonValue
    is_error: bool = False


class GenerationParameters(StrictModel):
    max_output_tokens: int = Field(default=1024, ge=1)
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    top_p: float | None = Field(default=None, gt=0.0, le=1.0)
    seed: int | None = None
    stop: tuple[str, ...] = ()
    # Optional, provider-neutral reasoning effort. None = unspecified (preserve
    # prior behavior / provider default); a set value is honored only by routes
    # that declare the "reasoning_effort" capability (fail-closed otherwise).
    reasoning_effort: ReasoningEffort | None = None


class Continuation(StrictModel):
    """Opaque provider transport state, never agent or project memory."""

    token: str = Field(min_length=1)


class Usage(StrictModel):
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0, validate_default=True)
    cached_input_tokens: int | None = Field(default=None, ge=0)
    reasoning_tokens: int | None = Field(default=None, ge=0)
    provider_details: dict[str, JsonValue] = Field(default_factory=dict)

    @field_validator("total_tokens")
    @classmethod
    def normalize_total(cls, value: int, info: ValidationInfo) -> int:
        expected = int(info.data.get("input_tokens", 0)) + int(info.data.get("output_tokens", 0))
        if value not in (0, expected):
            raise ValueError("total_tokens must equal input_tokens + output_tokens")
        return expected if value == 0 else value


class Cost(StrictModel):
    amount: float = Field(default=0.0, ge=0.0)
    currency: Literal["USD"] = "USD"


_SENSITIVE_METADATA_KEYS = {
    "api_key",
    "apikey",
    "authorization",
    "auth_header",
    "password",
    "credential",
    "credentials",
    "access_token",
    "refresh_token",
}


def _validate_safe_metadata(value: JsonValue, path: str = "metadata") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if key.lower() in _SENSITIVE_METADATA_KEYS:
                raise ValueError(f"{path} contains prohibited credential field {key!r}")
            _validate_safe_metadata(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _validate_safe_metadata(child, f"{path}[{index}]")


class ModelTurnRequest(StrictModel):
    protocol_version: Literal["thinker.model-turn.v1"] = "thinker.model-turn.v1"
    request_id: str = Field(min_length=1, max_length=200)
    requested_route: str = Field(min_length=1)
    messages: tuple[ModelMessage, ...]
    system_instruction: str | None = None
    tools: tuple[ToolDefinition, ...] = ()
    tool_results: tuple[ToolResult, ...] = ()
    response_schema: dict[str, JsonValue] | None = None
    generation: GenerationParameters = Field(default_factory=GenerationParameters)
    continuation: Continuation | None = None
    timeout_ms: int = Field(default=30_000, ge=1, le=3_600_000)
    metadata: dict[str, JsonValue] = Field(default_factory=dict)

    @field_validator("metadata")
    @classmethod
    def safe_metadata(cls, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        _validate_safe_metadata(value)
        return value

    @field_validator("response_schema")
    @classmethod
    def valid_response_schema(
        cls, value: dict[str, JsonValue] | None
    ) -> dict[str, JsonValue] | None:
        if value is not None:
            try:
                jsonschema.validators.validator_for(value).check_schema(value)
            except jsonschema.SchemaError as exc:
                raise ValueError("response_schema is not a valid JSON Schema") from exc
        return value

    @model_validator(mode="after")
    def validate_tool_result_ids(self) -> ModelTurnRequest:
        ids = [result.call_id for result in self.tool_results]
        if len(ids) != len(set(ids)):
            raise ValueError("tool result call_id values must be unique")
        names = [tool.name for tool in self.tools]
        if len(names) != len(set(names)):
            raise ValueError("tool names must be unique")
        return self


class ModelTurnResponse(StrictModel):
    protocol_version: Literal["thinker.model-turn.v1"] = "thinker.model-turn.v1"
    request_id: str
    thinker_version: str
    thinker_revision: str
    requested_route: str
    resolved_route: str | None = None
    resolved_provider: str | None = None
    resolved_model: str | None = None
    # Effective reasoning effort applied to this turn (request value, else the
    # route default). None = none/absent, so "gpt-5.1 default" and "gpt-5.1 high"
    # are distinguishable in provenance.
    reasoning_effort: ReasoningEffort | None = None
    assistant_content: str | None = None
    tool_calls: tuple[ToolCall, ...] = ()
    structured_output: JsonValue | None = None
    continuation: Continuation | None = None
    usage: Usage = Field(default_factory=Usage)
    cost: Cost = Field(default_factory=Cost)
    finish_reason: str | None = None
    duration_ms: float = Field(default=0.0, ge=0.0)
    normalized_error: ModelTurnError | None = None

    @model_validator(mode="after")
    def success_has_output(self) -> ModelTurnResponse:
        if (
            self.normalized_error is None
            and self.assistant_content is None
            and not self.tool_calls
            and self.structured_output is None
        ):
            raise ValueError(
                "successful response requires content, tool calls, or structured output"
            )
        return self


def json_safe(value: Any) -> JsonValue:
    """Validate that an arbitrary provider value is representable on the wire."""

    from pydantic import TypeAdapter

    return TypeAdapter(JsonValue).validate_python(value)
