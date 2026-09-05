from __future__ import annotations

import json
from threading import Event

import httpx
import pytest
import respx

from thinker.model_turn.errors import ModelTurnErrorCode, ModelTurnProviderException
from thinker.model_turn.models import (
    Continuation,
    ModelMessage,
    ToolCall,
    ToolDefinition,
    ToolResult,
)
from thinker.model_turn.openai import OpenAIModelTurnProvider

from .conftest import make_request


def openai_response(*output):
    return {
        "id": "resp_123",
        "model": "gpt-actual-2026-08-01",
        "status": "completed",
        "output": list(output),
        "usage": {
            "input_tokens": 11,
            "output_tokens": 7,
            "total_tokens": 18,
            "input_tokens_details": {"cached_tokens": 2},
            "output_tokens_details": {"reasoning_tokens": 3},
        },
    }


@respx.mock
def test_openai_responses_maps_system_messages_tools_results_and_continuation(fake_call):
    route = respx.post("https://api.openai.com/v1/responses").mock(
        return_value=httpx.Response(
            200,
            json=openai_response(
                {
                    "type": "message",
                    "content": [{"type": "output_text", "text": "done"}],
                }
            ),
        )
    )
    provider = OpenAIModelTurnProvider(credential_resolver=lambda _: "secret-value")
    request = make_request(
        system_instruction="system",
        messages=(
            ModelMessage(role="user", content="question"),
            ModelMessage(
                role="assistant",
                tool_calls=(ToolCall(call_id="c0", name="read_file", arguments={"path": "x"}),),
            ),
        ),
        tools=(
            ToolDefinition(
                name="read_file",
                description="Read",
                input_schema={"type": "object", "properties": {}},
            ),
        ),
        tool_results=(ToolResult(call_id="c0", output={"text": "x"}),),
        continuation=Continuation(token="resp_previous"),
        timeout_ms=1234,
    )
    result = provider.turn(request, fake_call.model_copy(update={"provider": "openai"}), Event())
    payload = json.loads(route.calls[0].request.content)
    assert payload["instructions"] == "system"
    assert payload["previous_response_id"] == "resp_previous"
    assert payload["tools"][0]["strict"] is True
    assert any(item.get("type") == "function_call_output" for item in payload["input"])
    assert route.calls[0].request.headers["Authorization"] == "Bearer secret-value"
    assert result.assistant_content == "done"
    assert result.continuation.token == "resp_123"
    assert result.resolved_model == "gpt-actual-2026-08-01"
    assert result.usage.cached_input_tokens == 2
    assert result.usage.reasoning_tokens == 3


@respx.mock
def test_openai_multiple_tool_calls(fake_call):
    respx.post("https://api.openai.com/v1/responses").mock(
        return_value=httpx.Response(
            200,
            json=openai_response(
                {"type": "function_call", "call_id": "a", "name": "read", "arguments": '{"x":1}'},
                {"type": "function_call", "call_id": "b", "name": "test", "arguments": '{"y":2}'},
            ),
        )
    )
    provider = OpenAIModelTurnProvider(credential_resolver=lambda _: "secret")
    result = provider.turn(make_request(), fake_call, Event())
    assert [(call.call_id, call.arguments) for call in result.tool_calls] == [
        ("a", {"x": 1}),
        ("b", {"y": 2}),
    ]


@respx.mock
def test_openai_strict_structured_output_payload(fake_call):
    route = respx.post("https://api.openai.com/v1/responses").mock(
        return_value=httpx.Response(
            200,
            json=openai_response(
                {"type": "message", "content": [{"type": "output_text", "text": '{"ok":true}'}]}
            ),
        )
    )
    provider = OpenAIModelTurnProvider(credential_resolver=lambda _: "secret")
    request = make_request(response_schema={"type": "object"})
    provider.turn(request, fake_call, Event())
    payload = json.loads(route.calls[0].request.content)
    assert payload["text"]["format"]["type"] == "json_schema"
    assert payload["text"]["format"]["strict"] is True


@respx.mock
def test_openai_malformed_tool_arguments_are_typed(fake_call):
    respx.post("https://api.openai.com/v1/responses").mock(
        return_value=httpx.Response(
            200,
            json=openai_response(
                {"type": "function_call", "call_id": "a", "name": "read", "arguments": "{"}
            ),
        )
    )
    provider = OpenAIModelTurnProvider(credential_resolver=lambda _: "secret")
    with pytest.raises(ModelTurnProviderException) as raised:
        provider.turn(make_request(), fake_call, Event())
    assert raised.value.error.code == ModelTurnErrorCode.TOOL_CALL_MALFORMED


@pytest.mark.parametrize(
    ("status", "code"),
    [
        (401, ModelTurnErrorCode.AUTHENTICATION_FAILURE),
        (429, ModelTurnErrorCode.RATE_LIMITED),
        (503, ModelTurnErrorCode.PROVIDER_OVERLOADED),
        (504, ModelTurnErrorCode.REQUEST_TIMEOUT),
        (404, ModelTurnErrorCode.MODEL_UNAVAILABLE),
        (500, ModelTurnErrorCode.UNKNOWN_PROVIDER_FAILURE),
    ],
)
@respx.mock
def test_openai_http_error_mapping_does_not_leak_body(fake_call, status, code):
    respx.post("https://api.openai.com/v1/responses").mock(
        return_value=httpx.Response(status, json={"error": {"message": "secret-value"}})
    )
    provider = OpenAIModelTurnProvider(credential_resolver=lambda _: "credential-secret")
    with pytest.raises(ModelTurnProviderException) as raised:
        provider.turn(make_request(), fake_call, Event())
    assert raised.value.error.code == code
    assert "secret-value" not in raised.value.error.message
    assert "credential-secret" not in raised.value.error.message


@respx.mock
def test_openai_timeout_is_typed(fake_call):
    respx.post("https://api.openai.com/v1/responses").mock(side_effect=httpx.ReadTimeout("late"))
    provider = OpenAIModelTurnProvider(credential_resolver=lambda _: "secret")
    with pytest.raises(ModelTurnProviderException) as raised:
        provider.turn(make_request(), fake_call, Event())
    assert raised.value.error.code == ModelTurnErrorCode.REQUEST_TIMEOUT
