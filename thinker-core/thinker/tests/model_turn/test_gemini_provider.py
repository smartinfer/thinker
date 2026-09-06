from __future__ import annotations

import json
from threading import Event

import httpx
import pytest
import respx

from thinker.model_turn.errors import ModelTurnErrorCode, ModelTurnProviderException
from thinker.model_turn.gemini import GeminiModelTurnProvider
from thinker.model_turn.models import ToolDefinition, ToolResult

from .conftest import make_request


def gemini_call(fake_call):
    return fake_call.model_copy(
        update={"provider": "gemini", "model_id": "gemini-test", "adapter": "gemini"}
    )


def gemini_response(*parts):
    return {
        "responseId": "response-1",
        "modelVersion": "gemini-resolved",
        "candidates": [{"content": {"parts": list(parts)}, "finishReason": "STOP"}],
        "usageMetadata": {
            "promptTokenCount": 8,
            "candidatesTokenCount": 4,
            "cachedContentTokenCount": 1,
            "thoughtsTokenCount": 2,
        },
    }


@respx.mock
def test_gemini_tool_calls_continuation_schema_identity_and_usage(fake_call):
    url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-test:generateContent"
    route = respx.post(url).mock(
        return_value=httpx.Response(
            200,
            json=gemini_response(
                {"functionCall": {"id": "a", "name": "read", "args": {"path": "a"}}},
                {"functionCall": {"id": "b", "name": "read", "args": {"path": "b"}}},
            ),
        )
    )
    provider = GeminiModelTurnProvider(credential_resolver=lambda _: "secret")
    request = make_request(
        system_instruction="system",
        tools=(
            ToolDefinition(
                name="read",
                description="Read",
                input_schema={"type": "object", "properties": {"path": {"type": "string"}}},
            ),
        ),
        response_schema={"type": "object"},
    )
    first = provider.turn(request, gemini_call(fake_call), Event())
    payload = json.loads(route.calls[0].request.content)
    assert payload["systemInstruction"]["parts"][0]["text"] == "system"
    assert (
        payload["tools"][0]["functionDeclarations"][0]["parametersJsonSchema"]["type"] == "object"
    )
    assert payload["generationConfig"]["responseMimeType"] == "application/json"
    assert [(call.call_id, call.name) for call in first.tool_calls] == [
        ("a", "read"),
        ("b", "read"),
    ]
    assert first.resolved_provider == "gemini"
    assert first.resolved_model == "gemini-resolved"
    assert first.usage.cached_input_tokens == 1
    assert first.usage.reasoning_tokens == 2
    assert first.usage.output_tokens == 6
    assert first.usage.provider_details["visible_output_tokens"] == 4

    route.mock(return_value=httpx.Response(200, json=gemini_response({"text": "done"})))
    provider.turn(
        make_request(
            continuation=first.continuation,
            tool_results=(
                ToolResult(call_id="a", output={"text": "a"}),
                ToolResult(call_id="b", output={"text": "b"}),
            ),
        ),
        gemini_call(fake_call),
        Event(),
    )
    continued = json.loads(route.calls[1].request.content)
    assert continued["contents"][0]["role"] == "user"
    assert continued["contents"][1]["role"] == "model"
    responses = continued["contents"][-1]["parts"]
    assert continued["contents"][-1]["role"] == "function"
    assert [part["functionResponse"]["id"] for part in responses] == ["a", "b"]


@respx.mock
def test_gemini_malformed_call_and_http_errors_are_typed(fake_call):
    url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-test:generateContent"
    route = respx.post(url).mock(
        return_value=httpx.Response(200, json=gemini_response({"functionCall": {"args": []}}))
    )
    provider = GeminiModelTurnProvider(credential_resolver=lambda _: "secret")
    with pytest.raises(ModelTurnProviderException) as raised:
        provider.turn(make_request(), gemini_call(fake_call), Event())
    assert raised.value.error.code == ModelTurnErrorCode.TOOL_CALL_MALFORMED

    route.mock(return_value=httpx.Response(429, json={"error": {"message": "secret-body"}}))
    with pytest.raises(ModelTurnProviderException) as raised:
        provider.turn(make_request(), gemini_call(fake_call), Event())
    assert raised.value.error.code == ModelTurnErrorCode.RATE_LIMITED
    assert "secret-body" not in raised.value.error.message


@respx.mock
def test_gemini_timeout_is_typed(fake_call):
    respx.post(
        "https://generativelanguage.googleapis.com/v1beta/models/gemini-test:generateContent"
    ).mock(side_effect=httpx.ReadTimeout("late"))
    with pytest.raises(ModelTurnProviderException) as raised:
        GeminiModelTurnProvider(credential_resolver=lambda _: "secret").turn(
            make_request(), gemini_call(fake_call), Event()
        )
    assert raised.value.error.code == ModelTurnErrorCode.REQUEST_TIMEOUT
