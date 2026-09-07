from __future__ import annotations

import json
from dataclasses import dataclass
from threading import Event
from typing import Any

import pytest

from thinker.model_turn.errors import ModelTurnError, ModelTurnErrorCode, ModelTurnProviderException
from thinker.model_turn.mlx import MLXModelTurnProvider
from thinker.model_turn.models import ModelMessage, ToolDefinition, ToolResult
from thinker.model_turn.runtime import ModelTurnRuntime
from thinker.registry.store import RegistryStore

from .conftest import make_request


@dataclass
class FakeResponse:
    text: str
    prompt_tokens: int = 12
    generation_tokens: int = 4
    prompt_tps: float = 100.0
    generation_tps: float = 20.0
    peak_memory: float = 4.25
    finish_reason: str = "stop"


class FakeTokenizer:
    has_chat_template = True
    has_tool_calling = True
    tool_call_start = "<tool_call>"
    tool_call_end = "</tool_call>"

    def __init__(self) -> None:
        self.rendered: list[tuple[list[dict[str, Any]], dict[str, Any]]] = []
        self.tool_parser = lambda value, _tools=None: json.loads(value)

    def apply_chat_template(self, messages, **kwargs):
        self.rendered.append((messages, kwargs))
        return [1, 2, 3]


class FakeBindings:
    def __init__(self, outputs: list[str]) -> None:
        self.outputs = outputs
        self.load_count = 0
        self.tokenizer = FakeTokenizer()
        self.generate_count = 0

    def load(self, model_id: str):
        self.load_count += 1
        return object(), self.tokenizer

    def make_sampler(self, *, temp: float, top_p: float):
        return (temp, top_p)

    def stream_generate(self, model, tokenizer, prompt, **kwargs):
        output = self.outputs[self.generate_count]
        self.generate_count += 1
        yield FakeResponse(text=output)


def mlx_call(fake_call, *, caps=None):
    return fake_call.model_copy(
        update={
            "call_id": "mlx:qwen-test.chat",
            "provider": "mlx",
            "model_id": "mlx-community/Qwen-Test-4bit",
            "adapter": "mlx",
            "caps": caps
            or [
                "model_turn_v1",
                "json_mode",
                "tools",
                "multiple_tool_calls",
                "tool_result_continuation",
            ],
            "aliases": ["mlx:test"],
            "price": fake_call.price.model_copy(update={"input_per_1k": 0.0, "output_per_1k": 0.0}),
        }
    )


def mlx_runtime(monkeypatch, fake_call, bindings, *, caps=None):
    monkeypatch.setattr("thinker.model_turn.mlx.platform.system", lambda: "Darwin")
    monkeypatch.setattr("thinker.model_turn.mlx.platform.machine", lambda: "arm64")
    provider = MLXModelTurnProvider(bindings)
    store = RegistryStore()
    store.put_call(mlx_call(fake_call, caps=caps))
    return ModelTurnRuntime(
        store,
        provider_factories={"mlx": lambda _call: provider},
        thinker_revision="mlx-test",
    )


def test_plain_text_normalization_provenance_usage_and_residency(monkeypatch, fake_call):
    bindings = FakeBindings(["hello", "again"])
    runtime = mlx_runtime(monkeypatch, fake_call, bindings)
    first = runtime.turn(make_request(request_id="mlx-1", requested_route="mlx:test", metadata={}))
    second = runtime.turn(make_request(request_id="mlx-2", requested_route="mlx:test", metadata={}))
    assert first.assistant_content == "hello"
    assert first.resolved_provider == "mlx"
    assert first.resolved_model == "mlx-community/Qwen-Test-4bit"
    assert first.usage.model_dump()["provider_details"] == {
        "backend": "mlx",
        "local": True,
        "model_cache_hit": False,
        "model_load_duration_ms": pytest.approx(0.0, abs=10.0),
        "prompt_tokens_per_second": 100.0,
        "generation_tokens_per_second": 20.0,
        "peak_memory_gb": 4.25,
        "structured_output_enforcement": "not_requested",
    }
    assert second.usage.provider_details["model_cache_hit"] is True
    assert first.cost.amount == 0
    assert bindings.load_count == 1


def test_structured_output_is_locally_validated(monkeypatch, fake_call):
    runtime = mlx_runtime(monkeypatch, fake_call, FakeBindings(['{"kind":"EXPLORE"}']))
    response = runtime.turn(
        make_request(
            requested_route="mlx:test",
            metadata={},
            response_schema={
                "type": "object",
                "properties": {"kind": {"enum": ["EXPLORE", "IMPLEMENT"]}},
                "required": ["kind"],
                "additionalProperties": False,
            },
        )
    )
    assert response.structured_output == {"kind": "EXPLORE"}
    assert response.usage.provider_details["structured_output_enforcement"] == (
        "thinker_local_parse_and_validate"
    )


def test_malformed_structured_output_fails_closed(monkeypatch, fake_call):
    runtime = mlx_runtime(monkeypatch, fake_call, FakeBindings(["not json"]))
    response = runtime.turn(
        make_request(
            requested_route="mlx:test",
            metadata={},
            response_schema={"type": "object"},
        )
    )
    assert response.normalized_error.code == ModelTurnErrorCode.STRUCTURED_OUTPUT_VIOLATION


def test_native_tool_calls_and_message_replay_continuation(monkeypatch, fake_call):
    bindings = FakeBindings(
        [
            '<tool_call>{"name":"lookup_status","arguments":{"name":"cache"}}</tool_call>',
            "cache is ready",
        ]
    )
    runtime = mlx_runtime(monkeypatch, fake_call, bindings)
    definition = ToolDefinition(
        name="lookup_status",
        description="Look up status",
        input_schema={
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
            "additionalProperties": False,
        },
    )
    first = runtime.turn(
        make_request(
            request_id="mlx-tool-1",
            requested_route="mlx:test",
            metadata={},
            tools=(definition,),
        )
    )
    assert first.finish_reason == "tool_calls"
    assert first.tool_calls[0].name == "lookup_status"
    assert first.tool_calls[0].arguments == {"name": "cache"}
    second = runtime.turn(
        make_request(
            request_id="mlx-tool-2",
            requested_route="mlx:test",
            metadata={},
            messages=(
                ModelMessage(role="user", content="check cache"),
                ModelMessage(role="assistant", tool_calls=first.tool_calls),
            ),
            tools=(definition,),
            tool_results=(ToolResult(call_id=first.tool_calls[0].call_id, output="ready"),),
        )
    )
    assert second.assistant_content == "cache is ready"
    replay_messages = bindings.tokenizer.rendered[1][0]
    assert replay_messages[-2]["tool_calls"][0]["id"] == first.tool_calls[0].call_id
    assert replay_messages[-1]["tool_call_id"] == first.tool_calls[0].call_id


def test_line_oriented_tool_name_whitespace_is_normalized(monkeypatch, fake_call):
    # GLM-family chat templates are line-oriented: mlx-lm's tool parser returns
    # the tool name with its trailing newline verbatim ("lookup_status\n").
    # Normalization owns whitespace — the semantically correct call must match
    # the registry tool name instead of failing as an unknown tool.
    bindings = FakeBindings(
        ['<tool_call>{"name":"lookup_status\\n","arguments":{"name":"ledger"}}</tool_call>']
    )
    runtime = mlx_runtime(monkeypatch, fake_call, bindings)
    definition = ToolDefinition(
        name="lookup_status",
        description="Look up status",
        input_schema={
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
            "additionalProperties": False,
        },
    )
    response = runtime.turn(
        make_request(
            request_id="mlx-tool-ws-1",
            requested_route="mlx:test",
            metadata={},
            tools=(definition,),
        )
    )
    assert response.normalized_error is None
    assert response.finish_reason == "tool_calls"
    assert response.tool_calls[0].name == "lookup_status"
    assert response.tool_calls[0].arguments == {"name": "ledger"}


def test_tool_call_ids_are_stable_and_distinct(monkeypatch, fake_call):
    output = (
        '<tool_call>{"name":"lookup","arguments":{"name":"a"}}</tool_call>'
        '<tool_call>{"name":"lookup","arguments":{"name":"b"}}</tool_call>'
    )
    runtime = mlx_runtime(monkeypatch, fake_call, FakeBindings([output]))
    definition = ToolDefinition(
        name="lookup",
        description="Lookup",
        input_schema={"type": "object", "additionalProperties": True},
    )
    response = runtime.turn(
        make_request(requested_route="mlx:test", metadata={}, tools=(definition,))
    )
    assert len(response.tool_calls) == 2
    assert len({call.call_id for call in response.tool_calls}) == 2


def test_unsupported_platform_fails_cleanly(monkeypatch, fake_call):
    monkeypatch.setattr("thinker.model_turn.mlx.platform.system", lambda: "Linux")
    provider = MLXModelTurnProvider(FakeBindings(["unused"]))
    with pytest.raises(ModelTurnProviderException) as raised:
        provider.turn(make_request(), mlx_call(fake_call), Event())
    assert raised.value.error.code == ModelTurnErrorCode.MODEL_UNAVAILABLE


def test_missing_optional_dependency_fails_actionably(monkeypatch, fake_call):
    monkeypatch.setattr("thinker.model_turn.mlx.platform.system", lambda: "Darwin")
    monkeypatch.setattr("thinker.model_turn.mlx.platform.machine", lambda: "arm64")

    def missing():
        raise ModelTurnProviderException(
            ModelTurnError(
                code=ModelTurnErrorCode.MODEL_UNAVAILABLE,
                message="MLX support is unavailable; install thinker-core[mlx] on Apple Silicon",
            )
        )

    monkeypatch.setattr("thinker.model_turn.mlx.NativeMLXBindings", missing)
    with pytest.raises(ModelTurnProviderException) as raised:
        MLXModelTurnProvider().turn(make_request(), mlx_call(fake_call), Event())
    assert raised.value.error.code == ModelTurnErrorCode.MODEL_UNAVAILABLE
    assert "thinker-core[mlx]" in raised.value.error.message


def test_capability_gate_rejects_tools_before_inference(monkeypatch, fake_call):
    bindings = FakeBindings(["unused"])
    runtime = mlx_runtime(monkeypatch, fake_call, bindings, caps=["model_turn_v1", "json_mode"])
    response = runtime.turn(
        make_request(
            requested_route="mlx:test",
            metadata={},
            tools=(
                ToolDefinition(
                    name="lookup",
                    description="Lookup",
                    input_schema={"type": "object", "properties": {}},
                ),
            ),
        )
    )
    assert response.normalized_error.code == ModelTurnErrorCode.MODEL_UNAVAILABLE
    assert bindings.generate_count == 0


def test_generation_failure_is_normalized(monkeypatch, fake_call):
    bindings = FakeBindings(["unused"])

    def fail(*_args, **_kwargs):
        raise RuntimeError("private detail")
        yield

    bindings.stream_generate = fail
    runtime = mlx_runtime(monkeypatch, fake_call, bindings)
    response = runtime.turn(make_request(requested_route="mlx:test", metadata={}))
    assert response.normalized_error.code == ModelTurnErrorCode.UNKNOWN_PROVIDER_FAILURE
    assert "private detail" not in response.normalized_error.message
