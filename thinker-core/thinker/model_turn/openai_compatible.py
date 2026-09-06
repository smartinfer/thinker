"""OpenAI-compatible Chat Completions transport preserving provider identity."""

from __future__ import annotations

import json
from threading import Event
from typing import Any

import httpx

from thinker.registry.schema import RegistryCall

from .errors import ModelTurnErrorCode, ModelTurnProviderException
from .http_common import CredentialResolver, http_error, provider_error, resolve_credential
from .models import ModelTurnRequest, ToolCall, Usage, json_safe
from .provider import ProviderTurnResult


class OpenAICompatibleModelTurnProvider:
    """Use Chat Completions wire semantics without relabeling the provider."""

    def __init__(
        self,
        base_url: str | None,
        credential_resolver: CredentialResolver | None = None,
        *,
        credential_required: bool = True,
    ) -> None:
        self.base_url = (base_url or "").rstrip("/")
        self._credential_resolver = credential_resolver or resolve_credential
        self.credential_required = credential_required

    def _chat_url(self) -> str:
        if self.base_url.endswith("/chat/completions"):
            return self.base_url
        if self.base_url.endswith("/v1"):
            return f"{self.base_url}/chat/completions"
        return f"{self.base_url}/v1/chat/completions"

    def turn(
        self,
        request: ModelTurnRequest,
        call: RegistryCall,
        cancel_event: Event,
    ) -> ProviderTurnResult:
        if cancel_event.is_set():
            raise provider_error(ModelTurnErrorCode.CANCELLED, "request cancelled")
        if not self.base_url:
            raise provider_error(
                ModelTurnErrorCode.MODEL_UNAVAILABLE,
                f"{call.provider} endpoint is not configured",
            )
        headers = {"Content-Type": "application/json"}
        key = self._credential_resolver(call.provider)
        if self.credential_required and not key:
            raise provider_error(
                ModelTurnErrorCode.AUTHENTICATION_FAILURE,
                f"{call.provider} credential is unavailable",
            )
        if key:
            headers["Authorization"] = f"Bearer {key}"
        try:
            with httpx.Client(timeout=request.timeout_ms / 1000.0) as client:
                response = client.post(
                    self._chat_url(), json=self._payload(request, call), headers=headers
                )
        except httpx.TimeoutException as exc:
            raise provider_error(
                ModelTurnErrorCode.REQUEST_TIMEOUT,
                f"{call.provider} request timed out",
                retryable=True,
            ) from exc
        except httpx.ConnectError as exc:
            raise provider_error(
                ModelTurnErrorCode.MODEL_UNAVAILABLE,
                f"{call.provider} endpoint is unavailable",
                retryable=True,
            ) from exc
        except httpx.HTTPError as exc:
            raise provider_error(
                ModelTurnErrorCode.UNKNOWN_PROVIDER_FAILURE,
                f"{call.provider} transport failed",
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
                f"{call.provider} returned a malformed response",
            ) from exc

    def _payload(self, request: ModelTurnRequest, call: RegistryCall) -> dict[str, Any]:
        messages: list[dict[str, Any]] = []
        if request.system_instruction:
            messages.append({"role": "system", "content": request.system_instruction})
        for message in request.messages:
            item: dict[str, Any] = {"role": message.role.value}
            if message.content is not None:
                item["content"] = message.content
            if message.tool_calls:
                item["tool_calls"] = [
                    {
                        "id": tool_call.call_id,
                        "type": "function",
                        "function": {
                            "name": tool_call.name,
                            "arguments": json.dumps(tool_call.arguments, separators=(",", ":")),
                        },
                    }
                    for tool_call in message.tool_calls
                ]
            messages.append(item)
        for result in request.tool_results:
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": result.call_id,
                    "content": _tool_output(result.output, result.is_error),
                }
            )
        payload: dict[str, Any] = {
            "model": call.model_id,
            "messages": messages,
            "max_tokens": request.generation.max_output_tokens,
        }
        if request.tools:
            payload["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": tool.input_schema,
                        "strict": tool.strict,
                    },
                }
                for tool in request.tools
            ]
        if request.response_schema is not None:
            if "json_schema" in call.caps or "structured_output" in call.caps:
                payload["response_format"] = {
                    "type": "json_schema",
                    "json_schema": {
                        "name": "thinker_model_turn_response",
                        "schema": request.response_schema,
                        "strict": True,
                    },
                }
            else:
                payload["response_format"] = {"type": "json_object"}
                schema_instruction = _schema_instruction(request.response_schema)
                if messages and messages[0].get("role") == "system":
                    messages[0][
                        "content"
                    ] = f"{messages[0].get('content', '')}\n\n{schema_instruction}"
                else:
                    messages.insert(0, {"role": "system", "content": schema_instruction})
        if request.generation.temperature is not None:
            payload["temperature"] = request.generation.temperature
        if request.generation.top_p is not None:
            payload["top_p"] = request.generation.top_p
        if request.generation.seed is not None:
            payload["seed"] = request.generation.seed
        if request.generation.stop:
            payload["stop"] = list(request.generation.stop)
        return payload

    def _parse_response(self, data: dict[str, Any], call: RegistryCall) -> ProviderTurnResult:
        choices = data.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            raise provider_error(
                ModelTurnErrorCode.MALFORMED_RESPONSE,
                f"{call.provider} response omitted choices",
            )
        choice = choices[0]
        message = choice.get("message")
        if not isinstance(message, dict):
            raise TypeError("message is not an object")
        content = message.get("content")
        if content is not None and not isinstance(content, str):
            raise ValueError("message content is not text")
        calls: list[ToolCall] = []
        raw_calls = message.get("tool_calls") or []
        if not isinstance(raw_calls, list):
            raise TypeError("tool_calls is not a list")
        for raw_call in raw_calls:
            if not isinstance(raw_call, dict) or not isinstance(raw_call.get("function"), dict):
                raise provider_error(
                    ModelTurnErrorCode.TOOL_CALL_MALFORMED,
                    f"{call.provider} returned a malformed tool call",
                )
            call_id, function = raw_call.get("id"), raw_call["function"]
            name, arguments_raw = function.get("name"), function.get("arguments")
            if not isinstance(call_id, str) or not isinstance(name, str):
                raise provider_error(
                    ModelTurnErrorCode.TOOL_CALL_MALFORMED,
                    f"{call.provider} tool call omitted its ID or name",
                )
            if isinstance(arguments_raw, str):
                try:
                    arguments = json.loads(arguments_raw)
                except json.JSONDecodeError as exc:
                    raise provider_error(
                        ModelTurnErrorCode.TOOL_CALL_MALFORMED,
                        f"{call.provider} returned malformed tool arguments",
                    ) from exc
            else:
                arguments = arguments_raw
            if not isinstance(arguments, dict):
                raise provider_error(
                    ModelTurnErrorCode.TOOL_CALL_MALFORMED,
                    f"{call.provider} tool arguments are not an object",
                )
            calls.append(ToolCall(call_id=call_id, name=name, arguments=arguments))
        raw_usage = data.get("usage") or {}
        if not isinstance(raw_usage, dict):
            raise TypeError("usage is not an object")
        input_tokens = int(raw_usage.get("prompt_tokens", 0) or 0)
        output_tokens = int(raw_usage.get("completion_tokens", 0) or 0)
        prompt_details = raw_usage.get("prompt_tokens_details") or {}
        completion_details = raw_usage.get("completion_tokens_details") or {}
        details = {
            key: json_safe(value)
            for key, value in raw_usage.items()
            if key
            not in {
                "prompt_tokens",
                "completion_tokens",
                "total_tokens",
                "prompt_tokens_details",
                "completion_tokens_details",
            }
        }
        return ProviderTurnResult(
            resolved_provider=call.provider,
            resolved_model=str(data.get("model") or call.model_id),
            assistant_content=content,
            tool_calls=tuple(calls),
            usage=Usage(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_tokens=input_tokens + output_tokens,
                cached_input_tokens=_detail_int(prompt_details, "cached_tokens"),
                reasoning_tokens=_detail_int(completion_details, "reasoning_tokens"),
                provider_details=details,
            ),
            finish_reason=str(choice.get("finish_reason") or "stop"),
        )


def _tool_output(output: object, is_error: bool) -> str:
    value = {"error": output} if is_error else output
    return value if isinstance(value, str) else json.dumps(value, separators=(",", ":"))


def _schema_instruction(schema: dict[str, Any]) -> str:
    encoded = json.dumps(schema, sort_keys=True, separators=(",", ":"))
    return f"Return only a JSON value that satisfies this JSON Schema: {encoded}"


def _detail_int(details: object, name: str) -> int | None:
    if not isinstance(details, dict) or details.get(name) is None:
        return None
    return int(details[name])
