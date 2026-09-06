"""OpenAI Responses API implementation of the model-turn V1 provider contract."""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from threading import Event
from typing import Any, cast

import httpx

from thinker.registry.schema import RegistryCall

from .errors import ModelTurnError, ModelTurnErrorCode, ModelTurnProviderException
from .models import Continuation, ModelMessage, ModelTurnRequest, ToolCall, Usage, json_safe
from .provider import ProviderTurnResult

CredentialResolver = Callable[[str], str | None]


class OpenAIModelTurnProvider:
    """Transport V1 turns over OpenAI's Responses API.

    The provider returns tool calls but never executes them.  The optional
    continuation token maps to ``previous_response_id`` only inside this adapter.
    """

    def __init__(
        self,
        base_url: str | None = None,
        credential_resolver: CredentialResolver | None = None,
    ) -> None:
        self.base_url = (base_url or "https://api.openai.com").rstrip("/")
        self._credential_resolver = credential_resolver or _resolve_credential

    def _responses_url(self) -> str:
        if self.base_url.endswith("/v1/responses"):
            return self.base_url
        return f"{self.base_url}/v1/responses"

    def turn(
        self,
        request: ModelTurnRequest,
        call: RegistryCall,
        cancel_event: Event,
    ) -> ProviderTurnResult:
        if cancel_event.is_set():
            raise _provider_error(ModelTurnErrorCode.CANCELLED, "request cancelled")

        payload = self._payload(request, call)
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self.base_url == "https://api.openai.com":
            key = self._credential_resolver("openai")
            if not key:
                raise _provider_error(
                    ModelTurnErrorCode.AUTHENTICATION_FAILURE,
                    "OpenAI credential is unavailable",
                )
            headers["Authorization"] = f"Bearer {key}"

        try:
            with httpx.Client(timeout=request.timeout_ms / 1000.0) as client:
                response = client.post(self._responses_url(), json=payload, headers=headers)
                if (
                    request.response_schema is not None
                    and response.status_code == 400
                    and _response_error_code(response) == "invalid_json_schema"
                    and not cancel_event.is_set()
                ):
                    fallback = self._payload(request, call, native_schema=False)
                    response = client.post(self._responses_url(), json=fallback, headers=headers)
        except httpx.TimeoutException as exc:
            raise _provider_error(
                ModelTurnErrorCode.REQUEST_TIMEOUT,
                "OpenAI request timed out",
                retryable=True,
            ) from exc
        except httpx.ConnectError as exc:
            raise _provider_error(
                ModelTurnErrorCode.MODEL_UNAVAILABLE,
                "OpenAI endpoint is unavailable",
                retryable=True,
            ) from exc
        except httpx.HTTPError as exc:
            raise _provider_error(
                ModelTurnErrorCode.UNKNOWN_PROVIDER_FAILURE,
                "OpenAI transport failed",
                retryable=True,
            ) from exc

        if cancel_event.is_set():
            raise _provider_error(ModelTurnErrorCode.CANCELLED, "request cancelled")
        if not response.is_success:
            raise _http_error(response)

        try:
            data = response.json()
            return self._parse_response(data)
        except ModelTurnProviderException:
            raise
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise _provider_error(
                ModelTurnErrorCode.MALFORMED_RESPONSE,
                "OpenAI returned a malformed response",
            ) from exc

    def _payload(
        self,
        request: ModelTurnRequest,
        call: RegistryCall,
        *,
        native_schema: bool = True,
    ) -> dict[str, Any]:
        input_items: list[dict[str, Any]] = []
        for message in request.messages:
            input_items.extend(_message_items(message))
        for result in request.tool_results:
            input_items.append(
                {
                    "type": "function_call_output",
                    "call_id": result.call_id,
                    "output": _tool_output(result.output, result.is_error),
                }
            )

        payload: dict[str, Any] = {
            "model": call.model_id,
            "input": input_items,
            "max_output_tokens": request.generation.max_output_tokens,
        }
        if request.system_instruction:
            payload["instructions"] = request.system_instruction
        if request.tools:
            payload["tools"] = [
                {
                    "type": "function",
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.input_schema,
                    "strict": tool.strict,
                }
                for tool in request.tools
            ]
        if request.response_schema is not None:
            if native_schema and ("json_schema" in call.caps or "structured_output" in call.caps):
                payload["text"] = {
                    "format": {
                        "type": "json_schema",
                        "name": "thinker_model_turn_response",
                        "schema": request.response_schema,
                        "strict": True,
                    }
                }
            else:
                payload["text"] = {"format": {"type": "json_object"}}
                schema_instruction = _schema_instruction(request.response_schema)
                existing = payload.get("instructions")
                payload["instructions"] = (
                    f"{existing}\n\n{schema_instruction}" if existing else schema_instruction
                )
        if request.continuation:
            payload["previous_response_id"] = request.continuation.token
        if request.generation.temperature is not None:
            payload["temperature"] = request.generation.temperature
        if request.generation.top_p is not None:
            payload["top_p"] = request.generation.top_p
        if request.generation.seed is not None:
            payload["seed"] = request.generation.seed
        if request.generation.stop:
            payload["stop"] = list(request.generation.stop)
        return payload

    def _parse_response(self, data: dict[str, Any]) -> ProviderTurnResult:
        response_id = data.get("id")
        if not isinstance(response_id, str) or not response_id:
            raise _provider_error(
                ModelTurnErrorCode.MALFORMED_RESPONSE,
                "OpenAI response omitted its response ID",
            )

        texts: list[str] = []
        calls: list[ToolCall] = []
        output = data.get("output")
        if not isinstance(output, list):
            raise _provider_error(
                ModelTurnErrorCode.MALFORMED_RESPONSE,
                "OpenAI response output is not a list",
            )
        for item in output:
            if not isinstance(item, dict):
                raise _provider_error(
                    ModelTurnErrorCode.MALFORMED_RESPONSE,
                    "OpenAI response contains an invalid output item",
                )
            item_type = item.get("type")
            if item_type == "function_call":
                raw = item.get("arguments", "")
                try:
                    arguments = json.loads(raw)
                except (TypeError, json.JSONDecodeError) as exc:
                    raise _provider_error(
                        ModelTurnErrorCode.TOOL_CALL_MALFORMED,
                        "OpenAI returned malformed tool-call arguments",
                    ) from exc
                if not isinstance(arguments, dict):
                    raise _provider_error(
                        ModelTurnErrorCode.TOOL_CALL_MALFORMED,
                        "OpenAI tool-call arguments must be a JSON object",
                    )
                call_id = item.get("call_id") or item.get("id")
                name = item.get("name")
                if not isinstance(call_id, str) or not isinstance(name, str):
                    raise _provider_error(
                        ModelTurnErrorCode.TOOL_CALL_MALFORMED,
                        "OpenAI tool call omitted its ID or name",
                    )
                calls.append(ToolCall(call_id=call_id, name=name, arguments=arguments))
            elif item_type == "message":
                content = item.get("content", [])
                if not isinstance(content, list):
                    raise _provider_error(
                        ModelTurnErrorCode.MALFORMED_RESPONSE,
                        "OpenAI message content is not a list",
                    )
                for part in content:
                    if isinstance(part, dict) and part.get("type") == "output_text":
                        text = part.get("text")
                        if isinstance(text, str):
                            texts.append(text)

        usage_raw = data.get("usage") or {}
        input_details = usage_raw.get("input_tokens_details") or {}
        output_details = usage_raw.get("output_tokens_details") or {}
        input_tokens = int(usage_raw.get("input_tokens", 0) or 0)
        output_tokens = int(usage_raw.get("output_tokens", 0) or 0)
        known_usage_keys = {
            "input_tokens",
            "output_tokens",
            "total_tokens",
            "input_tokens_details",
            "output_tokens_details",
        }
        details = {
            key: json_safe(value) for key, value in usage_raw.items() if key not in known_usage_keys
        }
        usage = Usage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=int(usage_raw.get("total_tokens", input_tokens + output_tokens) or 0),
            cached_input_tokens=_optional_int(input_details.get("cached_tokens")),
            reasoning_tokens=_optional_int(output_details.get("reasoning_tokens")),
            provider_details=details,
        )
        status = str(data.get("status") or "completed")
        finish_reason = status
        incomplete = data.get("incomplete_details")
        if isinstance(incomplete, dict) and incomplete.get("reason"):
            finish_reason = str(incomplete["reason"])

        return ProviderTurnResult(
            resolved_provider="openai",
            resolved_model=(str(data["model"]) if data.get("model") else None),
            assistant_content="".join(texts) if texts else None,
            tool_calls=tuple(calls),
            continuation=Continuation(token=response_id),
            usage=usage,
            finish_reason=finish_reason,
        )


def _message_items(message: ModelMessage) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    if message.content is not None:
        items.append(
            {
                "role": message.role.value,
                "content": [{"type": "input_text", "text": message.content}],
            }
        )
    for call in message.tool_calls:
        items.append(
            {
                "type": "function_call",
                "call_id": call.call_id,
                "name": call.name,
                "arguments": json.dumps(call.arguments, sort_keys=True, separators=(",", ":")),
            }
        )
    return items


def _tool_output(output: object, is_error: bool) -> str:
    body = {"error": output} if is_error else output
    if isinstance(body, str):
        return body
    return json.dumps(body, sort_keys=True, separators=(",", ":"))


def _schema_instruction(schema: dict[str, Any]) -> str:
    encoded = json.dumps(schema, sort_keys=True, separators=(",", ":"))
    return f"Return only a JSON value that satisfies this JSON Schema: {encoded}"


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, (int, float, str)):
        return int(value)
    raise ValueError("provider token detail is not numeric")


def _resolve_credential(provider: str) -> str | None:
    env_name = {"openai": "OPENAI_API_KEY"}.get(provider)
    if env_name and os.getenv(env_name):
        return os.getenv(env_name)
    try:
        from thinker.config import get_config
        from thinker.registry.secure_credentials import SecureCredentials

        config = get_config()
        return cast(
            str | None,
            SecureCredentials(
                storage_type=config.credentials_storage_type,
                keystore_path=config.keystore_path,
            ).get(provider),
        )
    except Exception:  # noqa: BLE001 -- credential lookup fails closed
        return None


def _http_error(response: httpx.Response) -> ModelTurnProviderException:
    status = response.status_code
    provider_code = _response_error_code(response)

    if status in (401, 403):
        code, message, retryable = (
            ModelTurnErrorCode.AUTHENTICATION_FAILURE,
            "OpenAI authentication failed",
            False,
        )
    elif status == 429:
        code, message, retryable = (
            ModelTurnErrorCode.RATE_LIMITED,
            "OpenAI rate limit reached",
            True,
        )
    elif status in (408, 504):
        code, message, retryable = (
            ModelTurnErrorCode.REQUEST_TIMEOUT,
            "OpenAI request timed out",
            True,
        )
    elif status in (502, 503):
        code, message, retryable = (
            ModelTurnErrorCode.PROVIDER_OVERLOADED,
            "OpenAI provider is overloaded",
            True,
        )
    elif status == 404:
        code, message, retryable = (
            ModelTurnErrorCode.MODEL_UNAVAILABLE,
            "OpenAI model is unavailable",
            False,
        )
    else:
        code, message, retryable = (
            ModelTurnErrorCode.UNKNOWN_PROVIDER_FAILURE,
            "OpenAI request failed",
            status >= 500,
        )
    return ModelTurnProviderException(
        ModelTurnError(
            code=code,
            message=message,
            retryable=retryable,
            provider_status=status,
            provider_code=provider_code,
        )
    )


def _response_error_code(response: httpx.Response) -> str | None:
    try:
        error = response.json().get("error", {})
        raw_code = error.get("code") if isinstance(error, dict) else None
        if isinstance(raw_code, str):
            candidate: str = raw_code[:100]
            if candidate and all(char.isalnum() or char in "._-" for char in candidate):
                return candidate
    except (AttributeError, TypeError, ValueError, json.JSONDecodeError):
        pass
    return None


def _provider_error(
    code: ModelTurnErrorCode,
    message: str,
    retryable: bool = False,
) -> ModelTurnProviderException:
    return ModelTurnProviderException(
        ModelTurnError(code=code, message=message, retryable=retryable)
    )
