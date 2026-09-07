"""Opt-in real Apple-Silicon MLX smoke; never runs in the ordinary suite."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from thinker.model_turn.models import (
    GenerationParameters,
    ModelMessage,
    ModelTurnRequest,
    ToolDefinition,
    ToolResult,
)
from thinker.model_turn.runtime import ModelTurnRuntime
from thinker.registry.loader import load_catalog
from thinker.registry.store import RegistryStore

pytestmark = pytest.mark.skipif(
    os.getenv("THINKER_RUN_MLX_REAL") != "1",
    reason="set THINKER_RUN_MLX_REAL=1 for the explicit local-model smoke",
)

ROUTE = "mlx:qwen3-8b-4bit.chat"


def _report(label: str, response) -> None:
    print(
        json.dumps(
            {
                "label": label,
                "provider": response.resolved_provider,
                "model": response.resolved_model,
                "duration_ms": response.duration_ms,
                "usage": response.usage.model_dump(mode="json"),
                "finish_reason": response.finish_reason,
                "tool_calls": [call.name for call in response.tool_calls],
                "structured_output": response.structured_output,
            },
            sort_keys=True,
        )
    )


@pytest.fixture(scope="module")
def mlx_runtime() -> ModelTurnRuntime:
    registry = Path(__file__).parents[4] / "spec" / "registry.yaml"
    catalog, checksum = load_catalog(registry)
    store = RegistryStore()
    store.apply_catalog(catalog, checksum, str(registry))
    return ModelTurnRuntime(store, thinker_revision="mlx-real-smoke")


def test_real_mlx_structured_state_derivation(mlx_runtime: ModelTurnRuntime) -> None:
    response = mlx_runtime.turn(
        ModelTurnRequest(
            request_id="mlx-real-structured",
            requested_route=ROUTE,
            messages=(
                ModelMessage(
                    role="user",
                    content=(
                        "Current task: repair cache initialization. Latest actions: read cache.go, "
                        "search for NewCache, read config.go. Classify the next work."
                    ),
                ),
            ),
            response_schema={
                "type": "object",
                "properties": {
                    "kind": {"enum": ["EXPLORE", "IMPLEMENT", "VERIFY", "REPAIR"]},
                    "description": {"type": "string", "minLength": 1, "maxLength": 120},
                },
                "required": ["kind", "description"],
                "additionalProperties": False,
            },
            generation=GenerationParameters(max_output_tokens=64, temperature=0),
            timeout_ms=600_000,
        )
    )
    assert response.normalized_error is None, response.normalized_error
    assert isinstance(response.structured_output, dict)
    assert response.structured_output["kind"] in {"EXPLORE", "IMPLEMENT", "VERIFY", "REPAIR"}
    assert response.resolved_provider == "mlx"
    _report("structured", response)


def test_real_mlx_tool_call_and_replay_continuation(mlx_runtime: ModelTurnRuntime) -> None:
    tool = ToolDefinition(
        name="lookup_status",
        description="Look up the current status for the named component.",
        input_schema={
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
            "additionalProperties": False,
        },
    )
    first = mlx_runtime.turn(
        ModelTurnRequest(
            request_id="mlx-real-tool-1",
            requested_route=ROUTE,
            system_instruction="Use the supplied tool whenever status must be looked up.",
            messages=(
                ModelMessage(
                    role="user",
                    content="Use lookup_status to find the status of cache, then wait for its result.",
                ),
            ),
            tools=(tool,),
            generation=GenerationParameters(max_output_tokens=96, temperature=0),
            timeout_ms=600_000,
        )
    )
    assert first.normalized_error is None, first.normalized_error
    assert len(first.tool_calls) == 1
    assert first.tool_calls[0].name == "lookup_status"
    assert first.tool_calls[0].arguments == {"name": "cache"}
    _report("tool_call", first)
    second = mlx_runtime.turn(
        ModelTurnRequest(
            request_id="mlx-real-tool-2",
            requested_route=ROUTE,
            messages=(
                ModelMessage(role="user", content="Find the status of cache."),
                ModelMessage(role="assistant", tool_calls=first.tool_calls),
            ),
            tools=(tool,),
            tool_results=(
                ToolResult(call_id=first.tool_calls[0].call_id, output={"status": "ready"}),
            ),
            generation=GenerationParameters(max_output_tokens=64, temperature=0),
            timeout_ms=600_000,
        )
    )
    assert second.normalized_error is None, second.normalized_error
    assert "ready" in (second.assistant_content or "").lower()
    assert second.resolved_provider == "mlx"
    _report("continuation", second)
