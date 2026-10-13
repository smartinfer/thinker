"""Typed contracts for Thinker's provider-neutral image generation path."""

from __future__ import annotations

import base64
import json
from dataclasses import asdict, dataclass, field
from typing import Any, Literal, TypeAlias

JSONValue: TypeAlias = None | bool | int | float | str | list["JSONValue"] | dict[str, "JSONValue"]
ErrorCategory: TypeAlias = Literal[
    "rate_limit",
    "timeout",
    "connection",
    "provider_5xx",
    "provider_4xx",
    "safety_rejection",
    "invalid_response",
    "budget_rejected",
    "unsupported_parameter",
    "adapter_error",
    "idempotency_conflict",
    "unknown",
]


def _json_object(value: dict[str, Any], name: str) -> None:
    try:
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must contain only JSON-serializable values") from exc


@dataclass(frozen=True)
class StructuredError:
    category: ErrorCategory
    message: str
    retryable: bool
    provider_status_code: int | None = None
    provider_error_code: str | None = None
    retry_after_seconds: float | None = None
    ambiguous_after_send: bool = False


@dataclass(frozen=True)
class AttemptRecord:
    attempt: int
    started_at: str
    latency_ms: float
    status: str
    error_category: str | None = None
    provider_status_code: int | None = None


@dataclass(frozen=True)
class ImageOutput:
    index: int
    data: bytes
    mime_type: str
    width: int | None
    height: int | None
    sha256: str

    def to_dict(self, *, include_data: bool = True) -> dict[str, Any]:
        result = asdict(self)
        result["data"] = base64.b64encode(self.data).decode("ascii") if include_data else None
        return result

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> ImageOutput:
        encoded = value.get("data")
        return cls(
            index=int(value["index"]),
            data=base64.b64decode(encoded) if encoded else b"",
            mime_type=str(value["mime_type"]),
            width=value.get("width"),
            height=value.get("height"),
            sha256=str(value["sha256"]),
        )


@dataclass(frozen=True)
class ImageGenerationRequest:
    request_id: str
    call_id: str
    prompt: str
    n: int = 1
    size: str | None = None
    aspect_ratio: str | None = None
    quality: str | None = None
    seed: int | None = None
    provider_options: dict[str, JSONValue] = field(default_factory=dict)
    caller_metadata: dict[str, str] = field(default_factory=dict)
    max_cost_usd: float | None = None
    allow_unknown_cost: bool = False
    max_attempts: int = 3
    retry_failed: bool = False

    def __post_init__(self) -> None:
        if not self.request_id.strip():
            raise ValueError("request_id must be nonempty")
        if not self.call_id.strip():
            raise ValueError("call_id must be nonempty")
        if not self.prompt.strip():
            raise ValueError("prompt must be nonempty")
        if self.n < 1:
            raise ValueError("n must be at least 1")
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        if self.max_cost_usd is not None and self.max_cost_usd < 0:
            raise ValueError("max_cost_usd cannot be negative")
        if any(
            not isinstance(k, str) or not isinstance(v, str)
            for k, v in self.caller_metadata.items()
        ):
            raise ValueError("caller_metadata must be string/string")
        _json_object(self.provider_options, "provider_options")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> ImageGenerationRequest:
        return cls(**value)


@dataclass(frozen=True)
class ImageGenerationResponse:
    request_id: str
    call_id: str
    provider: str
    requested_model_id: str
    resolved_model_id: str
    provider_model_id: str | None
    registry_sha256: str
    provider_response_id: str | None
    effective_parameters: dict[str, JSONValue]
    outputs: tuple[ImageOutput, ...]
    usage: dict[str, JSONValue]
    cost_usd: float | None
    latency_ms: float
    attempts: tuple[AttemptRecord, ...]
    caller_metadata: dict[str, str]
    status: str
    error: StructuredError | None
    replayed: bool = False

    def to_dict(self, *, include_data: bool = True) -> dict[str, Any]:
        return {
            **asdict(self),
            "outputs": [output.to_dict(include_data=include_data) for output in self.outputs],
            "attempts": [asdict(attempt) for attempt in self.attempts],
            "error": asdict(self.error) if self.error else None,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> ImageGenerationResponse:
        return cls(
            **{
                key: val
                for key, val in value.items()
                if key not in {"outputs", "attempts", "error"}
            },
            outputs=tuple(ImageOutput.from_dict(item) for item in value.get("outputs", [])),
            attempts=tuple(AttemptRecord(**item) for item in value.get("attempts", [])),
            error=StructuredError(**value["error"]) if value.get("error") else None,
        )


@dataclass(frozen=True)
class CostEstimate:
    estimated_max_usd: float | None
    pricing_basis: str
    known: bool


@dataclass(frozen=True)
class ProviderImage:
    data: bytes
    claimed_mime_type: str | None = None


@dataclass(frozen=True)
class ProviderImageResult:
    outputs: tuple[ProviderImage, ...]
    provider_response_id: str | None
    provider_model_id: str | None
    effective_parameters: dict[str, JSONValue]
    usage: dict[str, JSONValue]


class ImageAdapterError(Exception):
    """An adapter failure with stable machine-readable classification."""

    def __init__(self, error: StructuredError):
        super().__init__(error.message)
        self.error = error
