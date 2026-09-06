"""Anthropic Messages API implementation of model-turn V1."""

from __future__ import annotations

import json
from threading import Event
from typing import Any

import httpx

from thinker.registry.schema import RegistryCall

from .errors import ModelTurnErrorCode, ModelTurnProviderException
from .http_common import CredentialResolver, http_error, provider_error, resolve_credential
from .models import MessageRole, ModelMessage, ModelTurnRequest, ToolCall, Usage, json_safe
from .provider import ProviderTurnResult


class AnthropicModelTurnProvider:
    """Transport V1 turns over Anthropic's native Messages API."""

    def __init__(
        self,
        base_url: str | None = None,
        credential_resolver: CredentialResolver | None = None,
    ) -> None:
        self.base_url = (base_url or "https://api.anthropic.com").rstrip("/")
        self._credential_resolver = credential_resolver or resolve_credential

    def _messages_url(self) -> str:
        if self.base_url.endswith("/v1/messages"):
            return self.base_url
        return f"{self.base_url}/v1/messages"

    def turn(
        self,
        request: ModelTurnRequest,
        call: RegistryCall,
        cancel_event: Event,
    ) -> ProviderTurnResult:
        if cancel_event.is_set():
            raise provider_error(ModelTurnErrorCode.CANCELLED, "request cancelled")
        key = self._credential_resolver(call.provider)
        if not key:
            raise provider_error(
                ModelTurnErrorCode.AUTHENTICATION_FAILURE,
                f"{call.provider} credential is unavailable",
            )
        headers = {
            "Content-Type": "application/json",
            "x-api-key": key,
            "anthropic-version": "2023-06-01",
        }
        try:
            with httpx.Client(timeout=request.timeout_ms / 1000.0) as client:
                response = client.post(
                    self._messages_url(), json=self._payload(request, call), headers=headers
                )
        except httpx.TimeoutException as exc:
            raise provider_error(
                ModelTurnErrorCode.REQUEST_TIMEOUT,
                "anthropic request timed out",
                retryable=True,
            ) from exc
        except httpx.ConnectError as exc:
            raise provider_error(
                ModelTurnErrorCode.MODEL_UNAVAILABLE,
                "anthropic endpoint is unavailable",
                retryable=True,
            ) from exc
        except httpx.HTTPError as exc:
            raise provider_error(
                ModelTurnErrorCode.UNKNOWN_PROVIDER_FAILURE,
                "anthropic transport failed",
                retryable=True,
            ) from exc
        if cancel_event.is_set():
            raise provider_error(ModelTurnErrorCode.CANCELLED, "request cancelled")
        if not response.is_success:
            raise http_error(response, call.provider)
        try:
            return self._parse_response(response.json(), call, request)
        except ModelTurnProviderException:
            raise
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise provider_error(
                ModelTurnErrorCode.MALFORMED_RESPONSE,
                "anthropic returned a malformed response",
            ) from exc

    def _payload(self, request: ModelTurnRequest, call: RegistryCall) -> dict[str, Any]:
        messages, system = _messages_and_system(request)
        payload: dict[str, Any] = {
            "model": call.model_id,
            "max_tokens": request.generation.max_output_tokens,
            "messages": messages,
        }
        if system:
            payload["system"] = system
        if request.tools:
            payload["tools"] = [
                {
                    "name": tool.name,
                    "description": tool.description,
                    "input_schema": _anthropic_tool_schema(tool.input_schema),
                    "strict": tool.strict,
                }
                for tool in request.tools
            ]
        if request.response_schema is not None:
            payload["output_config"] = {
                "format": {"type": "json_schema", "schema": request.response_schema}
            }
        if request.generation.temperature is not None:
            payload["temperature"] = request.generation.temperature
        if request.generation.top_p is not None:
            payload["top_p"] = request.generation.top_p
        if request.generation.stop:
            payload["stop_sequences"] = list(request.generation.stop)
        return payload

    def _parse_response(
        self, data: dict[str, Any], call: RegistryCall, request: ModelTurnRequest
    ) -> ProviderTurnResult:
        content = data.get("content")
        if not isinstance(content, list):
            raise provider_error(
                ModelTurnErrorCode.MALFORMED_RESPONSE,
                "anthropic response content is not a list",
            )
        texts: list[str] = []
        calls: list[ToolCall] = []
        for block in content:
            if not isinstance(block, dict):
                raise provider_error(
                    ModelTurnErrorCode.MALFORMED_RESPONSE,
                    "anthropic response contains an invalid content block",
                )
            if block.get("type") == "text" and isinstance(block.get("text"), str):
                texts.append(block["text"])
            elif block.get("type") == "tool_use":
                call_id, name, arguments = block.get("id"), block.get("name"), block.get("input")
                if (
                    not isinstance(call_id, str)
                    or not isinstance(name, str)
                    or not isinstance(arguments, dict)
                ):
                    raise provider_error(
                        ModelTurnErrorCode.TOOL_CALL_MALFORMED,
                        "anthropic returned a malformed tool call",
                    )
                calls.append(
                    ToolCall(
                        call_id=call_id,
                        name=name,
                        arguments=_restore_nullable_arguments(request, name, arguments),
                    )
                )
        raw_usage = data.get("usage") or {}
        if not isinstance(raw_usage, dict):
            raise TypeError("usage is not an object")
        input_tokens = int(raw_usage.get("input_tokens", 0) or 0)
        output_tokens = int(raw_usage.get("output_tokens", 0) or 0)
        cache_read = _optional_int(raw_usage.get("cache_read_input_tokens"))
        details = {
            key: json_safe(value)
            for key, value in raw_usage.items()
            if key not in {"input_tokens", "output_tokens", "cache_read_input_tokens"}
        }
        return ProviderTurnResult(
            resolved_provider=call.provider,
            resolved_model=str(data.get("model") or call.model_id),
            assistant_content="".join(texts) if texts else None,
            tool_calls=tuple(calls),
            usage=Usage(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_tokens=input_tokens + output_tokens,
                cached_input_tokens=cache_read,
                provider_details=details,
            ),
            finish_reason=str(data.get("stop_reason") or "end_turn"),
        )


def _messages_and_system(request: ModelTurnRequest) -> tuple[list[dict[str, Any]], str]:
    system_parts = [request.system_instruction] if request.system_instruction else []
    messages: list[dict[str, Any]] = []
    for message in request.messages:
        if message.role == MessageRole.SYSTEM:
            if message.content:
                system_parts.append(message.content)
            continue
        content = _message_content(message)
        messages.append({"role": message.role.value, "content": content})
    if request.tool_results:
        result_blocks = []
        for result in request.tool_results:
            output = result.output if isinstance(result.output, str) else json.dumps(result.output)
            result_blocks.append(
                {
                    "type": "tool_result",
                    "tool_use_id": result.call_id,
                    "content": output,
                    "is_error": result.is_error,
                }
            )
        messages.append({"role": "user", "content": result_blocks})
    return messages, "\n\n".join(system_parts)


def _message_content(message: ModelMessage) -> list[dict[str, Any]]:
    content: list[dict[str, Any]] = []
    if message.content is not None:
        content.append({"type": "text", "text": message.content})
    for call in message.tool_calls:
        content.append(
            {"type": "tool_use", "id": call.call_id, "name": call.name, "input": call.arguments}
        )
    return content


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    if not isinstance(value, (str, bytes, bytearray, int, float)):
        raise TypeError("token count is not numeric")
    return int(value)


def _anthropic_tool_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Render canonical required-nullable fields as Anthropic optional fields.

    Anthropic rejects the JSON Schema ``type: [T, "null"]`` form used by
    strict OpenAI-style tool schemas. Omitting such fields is semantically
    equivalent at the V1 boundary because the response normalizer restores
    each omitted required-nullable field to ``null`` before local validation.
    """

    rendered = json.loads(json.dumps(schema))
    _rewrite_nullable_object_fields(rendered)
    return rendered


def _rewrite_nullable_object_fields(schema: dict[str, Any]) -> None:
    properties = schema.get("properties")
    if isinstance(properties, dict):
        required = schema.get("required")
        required_names = list(required) if isinstance(required, list) else []
        optional: set[str] = set()
        for name, value in properties.items():
            if not isinstance(value, dict):
                continue
            allowed = value.get("type")
            if isinstance(allowed, list) and "null" in allowed:
                non_null = [item for item in allowed if item != "null"]
                value["type"] = non_null[0] if len(non_null) == 1 else non_null
                optional.add(name)
            _rewrite_nullable_object_fields(value)
        if optional:
            schema["required"] = [name for name in required_names if name not in optional]
    items = schema.get("items")
    if isinstance(items, dict):
        _rewrite_nullable_object_fields(items)


def _restore_nullable_arguments(
    request: ModelTurnRequest, tool_name: str, arguments: dict[str, Any]
) -> dict[str, Any]:
    restored = dict(arguments)
    definition = next((tool for tool in request.tools if tool.name == tool_name), None)
    if definition is None:
        return restored
    required = definition.input_schema.get("required")
    properties = definition.input_schema.get("properties")
    if not isinstance(required, list) or not isinstance(properties, dict):
        return restored
    for name in required:
        if name in restored or not isinstance(name, str):
            continue
        value = properties.get(name)
        allowed = value.get("type") if isinstance(value, dict) else None
        if isinstance(allowed, list) and "null" in allowed:
            restored[name] = None
    return restored
