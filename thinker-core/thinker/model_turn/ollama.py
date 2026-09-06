"""Ollama native chat implementation of model-turn V1."""

from __future__ import annotations

import base64
import hashlib
import json
from threading import Event
from typing import Any

import httpx

from thinker.registry.schema import RegistryCall

from .errors import ModelTurnErrorCode, ModelTurnProviderException
from .http_common import http_error, provider_error
from .models import Continuation, MessageRole, ModelTurnRequest, ToolCall, Usage
from .provider import ProviderTurnResult

_CONTINUATION_PREFIX = "ollama-native-v1:"


class OllamaModelTurnProvider:
    """Use Ollama's local `/api/chat`; tool execution remains with the caller."""

    def __init__(self, base_url: str | None = None) -> None:
        self.base_url = (base_url or "http://127.0.0.1:11434").rstrip("/")

    def _chat_url(self) -> str:
        if self.base_url.endswith("/api/chat"):
            return self.base_url
        return f"{self.base_url}/api/chat"

    def turn(
        self,
        request: ModelTurnRequest,
        call: RegistryCall,
        cancel_event: Event,
    ) -> ProviderTurnResult:
        if cancel_event.is_set():
            raise provider_error(ModelTurnErrorCode.CANCELLED, "request cancelled")
        try:
            with httpx.Client(timeout=request.timeout_ms / 1000.0) as client:
                response = client.post(self._chat_url(), json=self._payload(request, call))
        except httpx.TimeoutException as exc:
            raise provider_error(
                ModelTurnErrorCode.REQUEST_TIMEOUT,
                "ollama request timed out",
                retryable=True,
            ) from exc
        except httpx.ConnectError as exc:
            raise provider_error(
                ModelTurnErrorCode.MODEL_UNAVAILABLE,
                "ollama endpoint is unavailable",
                retryable=True,
            ) from exc
        except httpx.HTTPError as exc:
            raise provider_error(
                ModelTurnErrorCode.UNKNOWN_PROVIDER_FAILURE,
                "ollama transport failed",
                retryable=True,
            ) from exc
        if cancel_event.is_set():
            raise provider_error(ModelTurnErrorCode.CANCELLED, "request cancelled")
        if not response.is_success:
            raise http_error(response, call.provider)
        try:
            return self._parse_response(response.json(), call)
        except ModelTurnProviderException:
            raise
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise provider_error(
                ModelTurnErrorCode.MALFORMED_RESPONSE,
                "ollama returned a malformed response",
            ) from exc

    def _payload(self, request: ModelTurnRequest, call: RegistryCall) -> dict[str, Any]:
        state = _decode_continuation(request.continuation)
        previous_message = state.get("message")
        names = state.get("names", {})
        if not isinstance(names, dict):
            raise provider_error(
                ModelTurnErrorCode.MALFORMED_RESPONSE,
                "ollama continuation state is malformed",
            )
        messages: list[dict[str, Any]] = []
        if request.system_instruction:
            messages.append({"role": "system", "content": request.system_instruction})
        has_calls = False
        for message in request.messages:
            item: dict[str, Any] = {
                "role": (
                    "assistant" if message.role == MessageRole.ASSISTANT else message.role.value
                ),
                "content": message.content or "",
            }
            if message.tool_calls:
                item["tool_calls"] = [
                    {
                        "function": {
                            "name": tool_call.name,
                            "arguments": tool_call.arguments,
                        }
                    }
                    for tool_call in message.tool_calls
                ]
                for tool_call in message.tool_calls:
                    names[tool_call.call_id] = tool_call.name
                has_calls = True
            messages.append(item)
        if request.tool_results:
            if isinstance(previous_message, dict) and not has_calls:
                messages.append(previous_message)
            for result in request.tool_results:
                name = names.get(result.call_id)
                if not isinstance(name, str):
                    raise provider_error(
                        ModelTurnErrorCode.TOOL_CALL_MALFORMED,
                        "ollama tool result has no matching call metadata",
                    )
                value = {"error": result.output} if result.is_error else result.output
                content = (
                    value if isinstance(value, str) else json.dumps(value, separators=(",", ":"))
                )
                messages.append({"role": "tool", "tool_name": name, "content": content})
        payload: dict[str, Any] = {
            "model": call.model_id,
            "messages": messages,
            "stream": False,
            "options": {"num_predict": request.generation.max_output_tokens},
        }
        if request.tools:
            payload["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": tool.input_schema,
                    },
                }
                for tool in request.tools
            ]
        if request.response_schema is not None:
            payload["format"] = request.response_schema
        options = payload["options"]
        if isinstance(options, dict):
            if request.generation.temperature is not None:
                options["temperature"] = request.generation.temperature
            if request.generation.top_p is not None:
                options["top_p"] = request.generation.top_p
            if request.generation.seed is not None:
                options["seed"] = request.generation.seed
            if request.generation.stop:
                options["stop"] = list(request.generation.stop)
        return payload

    def _parse_response(self, data: dict[str, Any], call: RegistryCall) -> ProviderTurnResult:
        message = data.get("message")
        if not isinstance(message, dict):
            raise provider_error(
                ModelTurnErrorCode.MALFORMED_RESPONSE,
                "ollama response omitted its message",
            )
        content = message.get("content")
        if content is not None and not isinstance(content, str):
            raise ValueError("message content is not text")
        raw_calls = message.get("tool_calls") or []
        if not isinstance(raw_calls, list):
            raise TypeError("tool_calls is not a list")
        response_identity = _response_digest(data)
        calls: list[ToolCall] = []
        names: dict[str, str] = {}
        normalized_calls: list[dict[str, Any]] = []
        for index, raw_call in enumerate(raw_calls):
            function = raw_call.get("function") if isinstance(raw_call, dict) else None
            if not isinstance(function, dict):
                raise provider_error(
                    ModelTurnErrorCode.TOOL_CALL_MALFORMED,
                    "ollama returned a malformed tool call",
                )
            name, arguments = function.get("name"), function.get("arguments", {})
            if not isinstance(name, str) or not isinstance(arguments, dict):
                raise provider_error(
                    ModelTurnErrorCode.TOOL_CALL_MALFORMED,
                    "ollama returned a malformed tool call",
                )
            call_id = f"ollama-{response_identity}-{index}"
            names[call_id] = name
            normalized_calls.append({"function": {"name": name, "arguments": arguments}})
            calls.append(ToolCall(call_id=call_id, name=name, arguments=arguments))
        input_tokens = int(data.get("prompt_eval_count", 0) or 0)
        output_tokens = int(data.get("eval_count", 0) or 0)
        continuation = None
        if calls:
            replay_message = {
                "role": "assistant",
                "content": content or "",
                "tool_calls": normalized_calls,
            }
            continuation = Continuation(
                token=_encode_continuation({"message": replay_message, "names": names})
            )
        return ProviderTurnResult(
            resolved_provider=call.provider,
            resolved_model=str(data.get("model") or call.model_id),
            assistant_content=content or None,
            tool_calls=tuple(calls),
            continuation=continuation,
            usage=Usage(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_tokens=input_tokens + output_tokens,
            ),
            finish_reason=str(data.get("done_reason") or ("tool_calls" if calls else "stop")),
        )


def _encode_continuation(state: dict[str, Any]) -> str:
    raw = json.dumps(state, sort_keys=True, separators=(",", ":")).encode()
    return _CONTINUATION_PREFIX + base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _decode_continuation(continuation: Continuation | None) -> dict[str, Any]:
    if continuation is None:
        return {}
    if not continuation.token.startswith(_CONTINUATION_PREFIX):
        raise provider_error(
            ModelTurnErrorCode.MALFORMED_RESPONSE,
            "ollama continuation token is invalid",
        )
    encoded = continuation.token.removeprefix(_CONTINUATION_PREFIX)
    try:
        state = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
    except (ValueError, json.JSONDecodeError) as exc:
        raise provider_error(
            ModelTurnErrorCode.MALFORMED_RESPONSE,
            "ollama continuation token is invalid",
        ) from exc
    if not isinstance(state, dict):
        raise provider_error(
            ModelTurnErrorCode.MALFORMED_RESPONSE,
            "ollama continuation token is invalid",
        )
    return state


def _response_digest(data: dict[str, Any]) -> str:
    encoded = json.dumps(data, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()[:16]
