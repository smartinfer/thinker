"""Typed inference intent, capability snapshots and results; no provider I/O."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator


class InferenceRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


Support = Literal["NATIVE", "EMULATED", "UNSUPPORTED"]
Provider = Literal["openai", "anthropic", "gemini"]


class ThinkingControl(InferenceRecord):
    mode: Literal["provider_default", "disabled", "effort", "budget", "adaptive"] = (
        "provider_default"
    )
    effort: str | None = None
    budget_tokens: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def consistent(self):
        if (self.mode == "budget") != (self.budget_tokens is not None):
            raise ValueError("budget mode requires token budget")
        if self.mode == "effort" and not self.effort:
            raise ValueError("effort required")
        if self.effort is not None and self.mode not in {"effort", "adaptive"}:
            raise ValueError("effort not applicable")
        return self


class InferencePolicy(InferenceRecord):
    """Opt-in planned execution; never implicitly applied to legacy V1 calls."""

    version: Literal["thinker.inference-policy.v1"] = "thinker.inference-policy.v1"
    expected_model: str = Field(min_length=1)
    schema_policy: Literal["NATIVE_REQUIRED", "ALLOW_EMULATION"] = "NATIVE_REQUIRED"
    max_attempts: Literal[1] = 1
    tool_policy: Literal["NONE"] = "NONE"
    thinking: ThinkingControl = Field(default_factory=ThinkingControl)


class CapabilityProfile(InferenceRecord):
    provider: Provider
    model: str = Field(min_length=1)
    source: str = Field(min_length=1)
    structured_output: Support = "UNSUPPORTED"
    json_mode: Support = "UNSUPPORTED"
    tools: Support = "UNSUPPORTED"
    vision: Support = "UNSUPPORTED"
    streaming: Support = "UNSUPPORTED"
    effort_values: tuple[str, ...] = ()
    budget_min: int | None = Field(default=None, ge=0)
    budget_max: int | None = Field(default=None, ge=0)
    adaptive: bool = False
    disable_thinking: bool = False

    @model_validator(mode="after")
    def bounds(self):
        if (self.budget_min is None) != (self.budget_max is None):
            raise ValueError("both budget bounds required")
        if self.budget_min is not None and self.budget_max < self.budget_min:
            raise ValueError("invalid budget bounds")
        return self


class InferencePlan(InferenceRecord):
    version: Literal["thinker.inference-plan.v1"] = "thinker.inference-plan.v1"
    request_id: str
    registry_sha256: str
    provider: Provider
    route: str
    model: str
    enforcement: Literal["NATIVE", "EMULATED", "NOT_REQUESTED"]
    workaround: Literal["JSON_PROMPT"] | None = None
    capabilities: CapabilityProfile
    thinking: ThinkingControl
    request_sha256: str
    schema_sha256: str
    prompt_sha256: str
    settings_sha256: str
    revision: str = "unversioned"


@dataclass(frozen=True)
class PreparedInference:
    plan: InferencePlan
    request_json: bytes
    body: bytes
    url: str
    body_sha256: str


class UsageReport(InferenceRecord):
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)
    visible_output_tokens: int | None = Field(default=None, ge=0)
    reasoning_tokens: int | None = Field(default=None, ge=0)
    cached_input_tokens: int | None = Field(default=None, ge=0)
    raw: dict[str, JsonValue] = Field(default_factory=dict)


class InferenceOutcome(InferenceRecord):
    version: Literal["thinker.inference-outcome.v1"] = "thinker.inference-outcome.v1"
    execution_status: Literal[
        "COMPLETED",
        "REFUSAL",
        "INCOMPLETE",
        "PROVIDER_ERROR",
        "TIMED_OUT",
        "CANCELLED",
        "CONFIGURATION_ERROR",
    ]
    structure_status: Literal["VALID", "INVALID", "NOT_EVALUATED"] = "NOT_EVALUATED"
    answer: str | None = None
    structured_output: JsonValue | None = None
    reasoning_artifacts: int = 0
    opaque_artifacts: int = 0
    usage: UsageReport = Field(default_factory=UsageReport)
    cost_usd: float | None = None
    cost_status: Literal["UNKNOWN"] = "UNKNOWN"
    resolved_model: str | None = None
    finish_reason: str | None = None
    error_code: str | None = None
    http_status: int | None = None
    attempt_count: int = 0
    response_sha256: str | None = None
    started_at: str | None = None
    deadline_at: str | None = None
    completed_at: str | None = None
    elapsed_seconds: float | None = None
    remote_cancellation_confirmed: bool = False
    audit_status: str = "DISABLED"
    audit_directory: str | None = None
