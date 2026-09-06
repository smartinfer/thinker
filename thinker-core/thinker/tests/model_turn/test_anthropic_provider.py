from __future__ import annotations

import json
from threading import Event

import httpx
import pytest
import respx

from thinker.model_turn.anthropic import AnthropicModelTurnProvider
from thinker.model_turn.errors import ModelTurnErrorCode, ModelTurnProviderException
from thinker.model_turn.models import ModelMessage, ToolDefinition, ToolResult

from .conftest import make_request


def anthropic_call(fake_call):
    return fake_call.model_copy(
        update={"provider": "anthropic", "model_id": "claude-test", "adapter": "anthropic"}
    )


@respx.mock
def test_anthropic_maps_tools_results_schema_identity_and_usage(fake_call):
    route = respx.post("https://api.anthropic.com/v1/messages").mock(
        return_value=httpx.Response(
            200,
            json={
                "model": "claude-resolved",
                "stop_reason": "tool_use",
                "content": [
                    {"type": "text", "text": "checking"},
                    {"type": "tool_use", "id": "a", "name": "read", "input": {"path": "a"}},
                    {"type": "tool_use", "id": "b", "name": "read", "input": {"path": "b"}},
                ],
                "usage": {
                    "input_tokens": 10,
                    "output_tokens": 5,
                    "cache_read_input_tokens": 2,
                },
            },
        )
    )
    provider = AnthropicModelTurnProvider(credential_resolver=lambda _: "secret")
    request = make_request(
        system_instruction="system",
        messages=(ModelMessage(role="user", content="inspect"),),
        tools=(
            ToolDefinition(
                name="read",
                description="Read a file",
                input_schema={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "start_line": {"type": ["integer", "null"], "minimum": 1},
                    },
                    "required": ["path", "start_line"],
                    "additionalProperties": False,
                },
            ),
        ),
        tool_results=(ToolResult(call_id="prior", output={"ok": True}),),
        response_schema={"type": "object"},
    )
    result = provider.turn(request, anthropic_call(fake_call), Event())
    payload = json.loads(route.calls[0].request.content)
    assert payload["system"] == "system"
    assert payload["tools"][0]["input_schema"]["type"] == "object"
    assert payload["tools"][0]["input_schema"]["required"] == ["path"]
    assert payload["tools"][0]["input_schema"]["properties"]["start_line"]["type"] == "integer"
    assert payload["messages"][-1]["content"][0]["tool_use_id"] == "prior"
    assert payload["output_config"]["format"]["type"] == "json_schema"
    assert [(call.call_id, call.name) for call in result.tool_calls] == [
        ("a", "read"),
        ("b", "read"),
    ]
    assert result.tool_calls[0].arguments["start_line"] is None
    assert result.resolved_provider == "anthropic"
    assert result.resolved_model == "claude-resolved"
    assert result.usage.cached_input_tokens == 2


@respx.mock
def test_anthropic_malformed_tool_call_is_typed(fake_call):
    respx.post("https://api.anthropic.com/v1/messages").mock(
        return_value=httpx.Response(
            200,
            json={"content": [{"type": "tool_use", "id": "a", "name": "read", "input": []}]},
        )
    )
    with pytest.raises(ModelTurnProviderException) as raised:
        AnthropicModelTurnProvider(credential_resolver=lambda _: "secret").turn(
            make_request(), anthropic_call(fake_call), Event()
        )
    assert raised.value.error.code == ModelTurnErrorCode.TOOL_CALL_MALFORMED


@pytest.mark.parametrize(
    ("status", "code"),
    [
        (401, ModelTurnErrorCode.AUTHENTICATION_FAILURE),
        (429, ModelTurnErrorCode.RATE_LIMITED),
        (503, ModelTurnErrorCode.PROVIDER_OVERLOADED),
    ],
)
@respx.mock
def test_anthropic_errors_are_typed_and_sanitized(fake_call, status, code):
    respx.post("https://api.anthropic.com/v1/messages").mock(
        return_value=httpx.Response(status, json={"error": {"message": "secret-body"}})
    )
    with pytest.raises(ModelTurnProviderException) as raised:
        AnthropicModelTurnProvider(credential_resolver=lambda _: "credential-secret").turn(
            make_request(), anthropic_call(fake_call), Event()
        )
    assert raised.value.error.code == code
    assert "secret" not in raised.value.error.message


@respx.mock
def test_anthropic_timeout_is_typed(fake_call):
    respx.post("https://api.anthropic.com/v1/messages").mock(side_effect=httpx.ReadTimeout("late"))
    with pytest.raises(ModelTurnProviderException) as raised:
        AnthropicModelTurnProvider(credential_resolver=lambda _: "secret").turn(
            make_request(), anthropic_call(fake_call), Event()
        )
    assert raised.value.error.code == ModelTurnErrorCode.REQUEST_TIMEOUT
