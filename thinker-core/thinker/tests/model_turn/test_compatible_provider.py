from __future__ import annotations

import json
from threading import Event

import httpx
import pytest
import respx

from thinker.model_turn.errors import ModelTurnErrorCode, ModelTurnProviderException
from thinker.model_turn.models import ToolDefinition, ToolResult
from thinker.model_turn.openai_compatible import OpenAICompatibleModelTurnProvider

from .conftest import make_request


def deepseek_call(fake_call):
    return fake_call.model_copy(
        update={
            "provider": "deepseek",
            "model_id": "deepseek-chat",
            "adapter": "openai_compatible",
            "endpoint": "https://api.deepseek.com",
            "caps": [*fake_call.caps, "json_schema"],
        }
    )


@respx.mock
def test_compatible_preserves_provider_identity_tools_results_and_usage(fake_call):
    route = respx.post("https://api.deepseek.com/v1/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json={
                "model": "deepseek-resolved",
                "choices": [
                    {
                        "finish_reason": "tool_calls",
                        "message": {
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "a",
                                    "type": "function",
                                    "function": {"name": "read", "arguments": '{"path":"a"}'},
                                },
                                {
                                    "id": "b",
                                    "type": "function",
                                    "function": {"name": "read", "arguments": '{"path":"b"}'},
                                },
                            ],
                        },
                    }
                ],
                "usage": {"prompt_tokens": 9, "completion_tokens": 3, "total_tokens": 12},
            },
        )
    )
    provider = OpenAICompatibleModelTurnProvider(
        "https://api.deepseek.com", credential_resolver=lambda _: "secret"
    )
    result = provider.turn(
        make_request(
            tools=(
                ToolDefinition(
                    name="read",
                    description="Read",
                    input_schema={"type": "object", "properties": {}},
                ),
            ),
            tool_results=(ToolResult(call_id="prior", output="done"),),
            response_schema={"type": "object"},
        ),
        deepseek_call(fake_call),
        Event(),
    )
    payload = json.loads(route.calls[0].request.content)
    assert payload["messages"][-1]["tool_call_id"] == "prior"
    assert payload["response_format"]["type"] == "json_schema"
    assert [call.call_id for call in result.tool_calls] == ["a", "b"]
    assert result.resolved_provider == "deepseek"
    assert result.resolved_model == "deepseek-resolved"
    assert result.usage.total_tokens == 12


@respx.mock
def test_compatible_malformed_arguments_and_errors_are_typed(fake_call):
    url = "https://api.deepseek.com/v1/chat/completions"
    route = respx.post(url).mock(
        return_value=httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "tool_calls": [
                                {"id": "a", "function": {"name": "read", "arguments": "{"}}
                            ]
                        }
                    }
                ]
            },
        )
    )
    provider = OpenAICompatibleModelTurnProvider(
        "https://api.deepseek.com", credential_resolver=lambda _: "secret"
    )
    with pytest.raises(ModelTurnProviderException) as raised:
        provider.turn(make_request(), deepseek_call(fake_call), Event())
    assert raised.value.error.code == ModelTurnErrorCode.TOOL_CALL_MALFORMED

    route.mock(return_value=httpx.Response(503, json={"error": {"message": "private"}}))
    with pytest.raises(ModelTurnProviderException) as raised:
        provider.turn(make_request(), deepseek_call(fake_call), Event())
    assert raised.value.error.code == ModelTurnErrorCode.PROVIDER_OVERLOADED
    assert "private" not in raised.value.error.message


@respx.mock
def test_compatible_json_mode_receives_schema_instruction(fake_call):
    route = respx.post("https://api.deepseek.com/v1/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json={
                "choices": [{"finish_reason": "stop", "message": {"content": '{"x":1}'}}],
                "usage": {},
            },
        )
    )
    call = deepseek_call(fake_call).model_copy(
        update={"caps": [cap for cap in fake_call.caps if cap != "json_schema"]}
    )
    OpenAICompatibleModelTurnProvider(
        "https://api.deepseek.com", credential_resolver=lambda _: "secret"
    ).turn(make_request(response_schema={"type": "object"}), call, Event())
    payload = json.loads(route.calls[0].request.content)
    assert payload["response_format"] == {"type": "json_object"}
    assert "JSON Schema" in payload["messages"][0]["content"]
