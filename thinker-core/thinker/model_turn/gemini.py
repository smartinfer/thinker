"""Google Gemini native GenerateContent implementation of model-turn V1."""

from __future__ import annotations

import base64
import hashlib
import json
from threading import Event
from typing import Any
from urllib.parse import quote

import httpx

from thinker.registry.schema import RegistryCall

from .errors import ModelTurnErrorCode, ModelTurnProviderException
from .http_common import CredentialResolver, http_error, provider_error, resolve_credential
from . import schema_compat
from .models import Continuation, MessageRole, ModelTurnRequest, ToolCall, Usage, json_safe
from .provider import ProviderTurnResult

_CONTINUATION_PREFIX = "gemini-native-v1:"


class GeminiModelTurnProvider:
    """Transport V1 turns over Gemini's native GenerateContent API.

    Gemini tool-loop state (including provider-issued thought signatures) is
    carried only inside the opaque V1 continuation token. It is transport state,
    not caller-visible memory or reasoning.
    """

    def __init__(
        self,
        base_url: str | None = None,
        credential_resolver: CredentialResolver | None = None,
    ) -> None:
        self.base_url = (base_url or "https://generativelanguage.googleapis.com").rstrip("/")
        self._credential_resolver = credential_resolver or resolve_credential

    def _generate_url(self, model: str) -> str:
        if self.base_url.endswith(":generateContent"):
            return self.base_url
        return f"{self.base_url}/v1beta/models/{quote(model, safe='')}:generateContent"

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
        headers = {"Content-Type": "application/json", "x-goog-api-key": key}
        try:
            with httpx.Client(timeout=request.timeout_ms / 1000.0) as client:
                payload = self._payload(request, call)
                response = client.post(
                    self._generate_url(call.model_id), json=payload, headers=headers
                )
        except httpx.TimeoutException as exc:
            raise provider_error(
                ModelTurnErrorCode.REQUEST_TIMEOUT,
                "gemini request timed out",
                retryable=True,
            ) from exc
        except httpx.ConnectError as exc:
            raise provider_error(
                ModelTurnErrorCode.MODEL_UNAVAILABLE,
                "gemini endpoint is unavailable",
                retryable=True,
            ) from exc
        except httpx.HTTPError as exc:
            raise provider_error(
                ModelTurnErrorCode.UNKNOWN_PROVIDER_FAILURE,
                "gemini transport failed",
                retryable=True,
            ) from exc
        if cancel_event.is_set():
            raise provider_error(ModelTurnErrorCode.CANCELLED, "request cancelled")
        if not response.is_success:
            raise http_error(response, call.provider)
        try:
            return self._parse_response(response.json(), call, payload["contents"], request)
        except ModelTurnProviderException:
            raise
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise provider_error(
                ModelTurnErrorCode.MALFORMED_RESPONSE,
                "gemini returned a malformed response",
            ) from exc

    def _payload(self, request: ModelTurnRequest, call: RegistryCall) -> dict[str, Any]:
        state = _decode_continuation(request.continuation)
        prior_contents = state.get("contents", [])
        if not isinstance(prior_contents, list):
            raise provider_error(
                ModelTurnErrorCode.MALFORMED_RESPONSE,
                "gemini continuation state is malformed",
            )
        names = state.get("names", {})
        if not isinstance(names, dict):
            raise provider_error(
                ModelTurnErrorCode.MALFORMED_RESPONSE,
                "gemini continuation state is malformed",
            )
        contents: list[dict[str, Any]] = list(prior_contents)
        system_parts = [request.system_instruction] if request.system_instruction else []
        for message in request.messages:
            if message.role == MessageRole.SYSTEM:
                if message.content:
                    system_parts.append(message.content)
                continue
            parts: list[dict[str, Any]] = []
            if message.content is not None:
                parts.append({"text": message.content})
            for tool_call in message.tool_calls:
                function_call: dict[str, Any] = {
                    "id": tool_call.call_id,
                    "name": tool_call.name,
                    "args": tool_call.arguments,
                }
                signature = _call_signature(prior_contents, tool_call.call_id)
                part: dict[str, Any] = {"functionCall": function_call}
                if signature is not None:
                    part["thoughtSignature"] = signature
                parts.append(part)
                names[tool_call.call_id] = tool_call.name
            contents.append(
                {
                    "role": "model" if message.role == MessageRole.ASSISTANT else "user",
                    "parts": parts,
                }
            )
        if request.tool_results:
            result_parts: list[dict[str, Any]] = []
            for result in request.tool_results:
                name = names.get(result.call_id)
                if not name:
                    raise provider_error(
                        ModelTurnErrorCode.TOOL_CALL_MALFORMED,
                        "gemini tool result has no matching call metadata",
                    )
                output = {"error": result.output} if result.is_error else result.output
                response = output if isinstance(output, dict) else {"result": output}
                result_parts.append(
                    {
                        "functionResponse": {
                            "id": result.call_id,
                            "name": name,
                            "response": response,
                        }
                    }
                )
            contents.append({"role": "function", "parts": result_parts})
        payload: dict[str, Any] = {"contents": contents}
        if system_parts:
            payload["systemInstruction"] = {
                "parts": [{"text": text} for text in system_parts if text]
            }
        if request.tools:
            payload["tools"] = [
                {
                    "functionDeclarations": [
                        {
                            "name": tool.name,
                            "description": tool.description,
                            # Gemini's function calling does not implement the
                            # OpenAI strict required-nullable convention: render
                            # those fields as ordinary optionals; the response
                            # path restores omissions to null before the common
                            # Model-Turn validation (schema_compat).
                            "parametersJsonSchema": schema_compat.render_nullable_as_optional(
                                tool.input_schema
                            ),
                        }
                        for tool in request.tools
                    ]
                }
            ]
        generation: dict[str, Any] = {"maxOutputTokens": request.generation.max_output_tokens}
        if request.response_schema is not None:
            generation["responseMimeType"] = "application/json"
            generation["responseJsonSchema"] = request.response_schema
        if request.generation.temperature is not None:
            generation["temperature"] = request.generation.temperature
        if request.generation.top_p is not None:
            generation["topP"] = request.generation.top_p
        if request.generation.stop:
            generation["stopSequences"] = list(request.generation.stop)
        payload["generationConfig"] = generation
        return payload

    def _parse_response(
        self,
        data: dict[str, Any],
        call: RegistryCall,
        request_contents: object,
        request: ModelTurnRequest | None = None,
    ) -> ProviderTurnResult:
        candidates = data.get("candidates")
        if (
            not isinstance(candidates, list)
            or not candidates
            or not isinstance(candidates[0], dict)
        ):
            raise provider_error(
                ModelTurnErrorCode.MALFORMED_RESPONSE,
                "gemini response omitted candidates",
            )
        candidate = candidates[0]
        content = candidate.get("content")
        parts = content.get("parts") if isinstance(content, dict) else None
        if not isinstance(parts, list):
            raise provider_error(
                ModelTurnErrorCode.MALFORMED_RESPONSE,
                "gemini response content is malformed",
            )
        texts: list[str] = []
        calls: list[ToolCall] = []
        continuation_parts: list[dict[str, Any]] = []
        response_identity = str(data.get("responseId") or _response_digest(data))
        for index, part in enumerate(parts):
            if not isinstance(part, dict):
                raise TypeError("part is not an object")
            retained: dict[str, Any] = {}
            if isinstance(part.get("text"), str):
                texts.append(part["text"])
                retained["text"] = part["text"]
            function = part.get("functionCall")
            if function is not None:
                if not isinstance(function, dict):
                    raise provider_error(
                        ModelTurnErrorCode.TOOL_CALL_MALFORMED,
                        "gemini returned a malformed function call",
                    )
                call_id = function.get("id") or f"gemini-{response_identity}-{index}"
                name, arguments = function.get("name"), function.get("args", {})
                if (
                    not isinstance(call_id, str)
                    or not isinstance(name, str)
                    or not isinstance(arguments, dict)
                ):
                    raise provider_error(
                        ModelTurnErrorCode.TOOL_CALL_MALFORMED,
                        "gemini returned a malformed function call",
                    )
                if request is not None:
                    arguments = schema_compat.restore_omitted_nullable_arguments(
                        request.tools, name, arguments
                    )
                normalized = {"id": call_id, "name": name, "args": arguments}
                retained["functionCall"] = normalized
                calls.append(ToolCall(call_id=call_id, name=name, arguments=arguments))
            signature = part.get("thoughtSignature")
            if isinstance(signature, str):
                retained["thoughtSignature"] = signature
            if retained:
                continuation_parts.append(retained)
        raw_usage = data.get("usageMetadata") or {}
        if not isinstance(raw_usage, dict):
            raise TypeError("usageMetadata is not an object")
        input_tokens = int(raw_usage.get("promptTokenCount", 0) or 0)
        visible_output_tokens = int(raw_usage.get("candidatesTokenCount", 0) or 0)
        reasoning_tokens = int(raw_usage.get("thoughtsTokenCount", 0) or 0)
        reported_total = int(raw_usage.get("totalTokenCount", 0) or 0)
        output_tokens = max(
            visible_output_tokens + reasoning_tokens,
            max(0, reported_total - input_tokens),
        )
        known = {
            "promptTokenCount",
            "candidatesTokenCount",
            "totalTokenCount",
            "cachedContentTokenCount",
            "thoughtsTokenCount",
        }
        details = {key: json_safe(value) for key, value in raw_usage.items() if key not in known}
        details["visible_output_tokens"] = visible_output_tokens
        continuation = None
        if calls:
            if not isinstance(request_contents, list):
                raise TypeError("request contents are not a list")
            names = {call.call_id: call.name for call in calls}
            history = [*request_contents, {"role": "model", "parts": continuation_parts}]
            continuation = Continuation(
                token=_encode_continuation({"contents": history, "names": names})
            )
        return ProviderTurnResult(
            resolved_provider=call.provider,
            resolved_model=str(data.get("modelVersion") or call.model_id),
            assistant_content="".join(texts) if texts else None,
            tool_calls=tuple(calls),
            continuation=continuation,
            usage=Usage(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_tokens=input_tokens + output_tokens,
                cached_input_tokens=_optional_int(raw_usage.get("cachedContentTokenCount")),
                reasoning_tokens=reasoning_tokens,
                provider_details=details,
            ),
            finish_reason=str(candidate.get("finishReason") or "STOP"),
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
            "gemini continuation token is invalid",
        )
    encoded = continuation.token.removeprefix(_CONTINUATION_PREFIX)
    try:
        raw = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
        state = json.loads(raw)
    except (ValueError, json.JSONDecodeError) as exc:
        raise provider_error(
            ModelTurnErrorCode.MALFORMED_RESPONSE,
            "gemini continuation token is invalid",
        ) from exc
    if not isinstance(state, dict):
        raise provider_error(
            ModelTurnErrorCode.MALFORMED_RESPONSE,
            "gemini continuation token is invalid",
        )
    return state


def _call_signature(contents: list[object], call_id: str) -> str | None:
    for content in contents:
        parts = content.get("parts", []) if isinstance(content, dict) else []
        if not isinstance(parts, list):
            continue
        for part in parts:
            if not isinstance(part, dict):
                continue
            function = part.get("functionCall")
            if isinstance(function, dict) and function.get("id") == call_id:
                signature = part.get("thoughtSignature")
                return signature if isinstance(signature, str) else None
    return None


def _response_digest(data: dict[str, Any]) -> str:
    encoded = json.dumps(data, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()[:16]


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    if not isinstance(value, (str, bytes, bytearray, int, float)):
        raise TypeError("token count is not numeric")
    return int(value)
