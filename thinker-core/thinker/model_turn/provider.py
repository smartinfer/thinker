"""Provider contract behind the model-turn protocol."""

from __future__ import annotations

from dataclasses import dataclass, field
from threading import Event
from typing import Protocol

from pydantic import JsonValue

from thinker.registry.schema import RegistryCall

from .models import Continuation, ModelTurnRequest, ToolCall, Usage


@dataclass(frozen=True)
class ProviderTurnResult:
    resolved_provider: str | None = None
    resolved_model: str | None = None
    assistant_content: str | None = None
    tool_calls: tuple[ToolCall, ...] = ()
    structured_output: JsonValue | None = None
    continuation: Continuation | None = None
    usage: Usage = field(default_factory=Usage)
    finish_reason: str | None = None
    provider_metadata: dict[str, JsonValue] = field(default_factory=dict)


class ModelTurnProvider(Protocol):
    def turn(
        self,
        request: ModelTurnRequest,
        call: RegistryCall,
        cancel_event: Event,
    ) -> ProviderTurnResult:
        """Perform one model turn. Tool execution remains with the caller."""
        ...
