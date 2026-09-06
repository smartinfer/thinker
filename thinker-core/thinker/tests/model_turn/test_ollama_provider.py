from __future__ import annotations

import json
from threading import Event

import httpx
import pytest
import respx

from thinker.model_turn.errors import ModelTurnErrorCode, ModelTurnProviderException
from thinker.model_turn.models import ToolDefinition, ToolResult
from thinker.model_turn.ollama import OllamaModelTurnProvider

from .conftest import make_request


def ollama_call(fake_call):
    return fake_call.model_copy(
        update={"provider": "ollama", "model_id": "qwen-test", "adapter": "ollama"}
    )


@respx.mock
def test_ollama_tools_continuation_schema_and_usage(fake_call):
    route = respx.post("http://127.0.0.1:11434/api/chat").mock(
        return_value=httpx.Response(
            200,
            json={
                "model": "qwen-resolved",
                "done_reason": "stop",
                "message": {
                    "content": "",
                    "tool_calls": [
                        {"function": {"name": "read", "arguments": {"path": "a"}}},
                        {"function": {"name": "read", "arguments": {"path": "b"}}},
                    ],
                },
                "prompt_eval_count": 6,
                "eval_count": 2,
            },
        )
    )
    provider = OllamaModelTurnProvider()
    first = provider.turn(
        make_request(
            tools=(
                ToolDefinition(
                    name="read",
                    description="Read",
                    input_schema={"type": "object", "properties": {}},
                ),
            ),
            response_schema={"type": "object"},
        ),
        ollama_call(fake_call),
        Event(),
    )
    payload = json.loads(route.calls[0].request.content)
    assert payload["format"] == {"type": "object"}
    assert len(first.tool_calls) == 2
    assert len({call.call_id for call in first.tool_calls}) == 2
    assert first.resolved_provider == "ollama"
    assert first.resolved_model == "qwen-resolved"
    assert first.usage.total_tokens == 8

    route.mock(
        return_value=httpx.Response(
            200, json={"model": "qwen-resolved", "message": {"content": "done"}}
        )
    )
    provider.turn(
        make_request(
            continuation=first.continuation,
            tool_results=tuple(
                ToolResult(call_id=call.call_id, output="ok") for call in first.tool_calls
            ),
        ),
        ollama_call(fake_call),
        Event(),
    )
    continued = json.loads(route.calls[1].request.content)
    assert continued["messages"][-2]["role"] == "tool"
    assert continued["messages"][-1]["role"] == "tool"


@respx.mock
def test_ollama_timeout_is_typed(fake_call):
    respx.post("http://127.0.0.1:11434/api/chat").mock(side_effect=httpx.ReadTimeout("late"))
    with pytest.raises(ModelTurnProviderException) as raised:
        OllamaModelTurnProvider().turn(make_request(), ollama_call(fake_call), Event())
    assert raised.value.error.code == ModelTurnErrorCode.REQUEST_TIMEOUT
