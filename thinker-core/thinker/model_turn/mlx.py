"""Direct Apple-Silicon MLX implementation of model-turn V1.

The heavy MLX imports are deliberately lazy so every other Thinker provider
remains importable on machines without the optional ``thinker-core[mlx]`` extra.
"""

from __future__ import annotations

import hashlib
import json
import platform
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from threading import Event
from typing import Any, Protocol, cast

from pydantic import JsonValue

from thinker.registry.schema import RegistryCall

from .errors import ModelTurnErrorCode
from .http_common import provider_error
from .models import MessageRole, ModelTurnRequest, ToolCall, Usage
from .provider import ProviderTurnResult


class MLXTokenizer(Protocol):
    has_chat_template: bool
    has_tool_calling: bool
    tool_call_start: str | None
    tool_call_end: str | None
    tool_parser: Callable[..., Any] | None

    def apply_chat_template(self, messages: list[dict[str, Any]], **kwargs: Any) -> Any: ...


class MLXBindings(Protocol):
    def load(self, model_id: str) -> tuple[Any, MLXTokenizer]: ...

    def stream_generate(
        self, model: Any, tokenizer: MLXTokenizer, prompt: Any, **kwargs: Any
    ) -> Iterable[Any]: ...

    def make_sampler(self, *, temp: float, top_p: float) -> Any: ...


@dataclass(frozen=True)
class LoadedMLXModel:
    model: Any
    tokenizer: MLXTokenizer
    load_duration_ms: float


class NativeMLXBindings:
    """Thin lazy bridge to the public mlx-lm Python API."""

    def __init__(self) -> None:
        try:
            from mlx_lm import load, stream_generate
            from mlx_lm.sample_utils import make_sampler
        except ImportError as exc:
            raise provider_error(
                ModelTurnErrorCode.MODEL_UNAVAILABLE,
                "MLX support is unavailable; install thinker-core[mlx] on Apple Silicon",
            ) from exc
        self._load = load
        self._stream_generate = stream_generate
        self._make_sampler = make_sampler

    def load(self, model_id: str) -> tuple[Any, MLXTokenizer]:
        model, tokenizer = self._load(model_id)
        return model, tokenizer

    def stream_generate(
        self, model: Any, tokenizer: MLXTokenizer, prompt: Any, **kwargs: Any
    ) -> Iterable[Any]:
        return cast(Iterable[Any], self._stream_generate(model, tokenizer, prompt, **kwargs))

    def make_sampler(self, *, temp: float, top_p: float) -> Any:
        return self._make_sampler(temp=temp, top_p=top_p)


class MLXModelCache:
    """Small process-local model residency cache keyed by Hugging Face model ID."""

    def __init__(self, bindings: MLXBindings) -> None:
        self._bindings = bindings
        self._models: dict[str, LoadedMLXModel] = {}
        self._lock = threading.Lock()

    def get(self, model_id: str) -> tuple[LoadedMLXModel, bool]:
        with self._lock:
            cached = self._models.get(model_id)
            if cached is not None:
                return cached, True
            started = time.monotonic()
            model, tokenizer = self._bindings.load(model_id)
            loaded = LoadedMLXModel(
                model=model,
                tokenizer=tokenizer,
                load_duration_ms=(time.monotonic() - started) * 1000.0,
            )
            self._models[model_id] = loaded
            return loaded, False


class MLXModelTurnProvider:
    """Run one provider-neutral turn directly through ``mlx-lm``."""

    def __init__(self, bindings: MLXBindings | None = None) -> None:
        self._bindings = bindings
        self._cache: MLXModelCache | None = None
        self._generation_lock = threading.Lock()

    def _runtime(self) -> tuple[MLXBindings, MLXModelCache]:
        if platform.system() != "Darwin" or platform.machine() != "arm64":
            raise provider_error(
                ModelTurnErrorCode.MODEL_UNAVAILABLE,
                "direct MLX inference requires an Apple-Silicon Mac",
            )
        if self._bindings is None:
            self._bindings = NativeMLXBindings()
        if self._cache is None:
            self._cache = MLXModelCache(self._bindings)
        return self._bindings, self._cache

    def turn(
        self,
        request: ModelTurnRequest,
        call: RegistryCall,
        cancel_event: Event,
    ) -> ProviderTurnResult:
        if cancel_event.is_set():
            raise provider_error(ModelTurnErrorCode.CANCELLED, "request cancelled")
        bindings, cache = self._runtime()
        try:
            loaded, cache_hit = cache.get(call.model_id)
            prompt = _render_prompt(loaded.tokenizer, request)
            temperature = request.generation.temperature or 0.0
            top_p = request.generation.top_p or 1.0
            sampler = bindings.make_sampler(temp=temperature, top_p=top_p)
            chunks: list[str] = []
            final: Any | None = None
            with self._generation_lock:
                for response in bindings.stream_generate(
                    loaded.model,
                    loaded.tokenizer,
                    prompt,
                    max_tokens=request.generation.max_output_tokens,
                    sampler=sampler,
                ):
                    if cancel_event.is_set():
                        raise provider_error(ModelTurnErrorCode.CANCELLED, "request cancelled")
                    chunks.append(str(response.text))
                    final = response
                    if _first_stop("".join(chunks), request.generation.stop) is not None:
                        break
            if final is None:
                raise provider_error(
                    ModelTurnErrorCode.MALFORMED_RESPONSE,
                    "mlx-lm produced no generation response",
                )
            raw_text = "".join(chunks)
            stop_at = _first_stop(raw_text, request.generation.stop)
            if stop_at is not None:
                raw_text = raw_text[:stop_at]
            assistant_content, tool_calls = _parse_generated_output(
                raw_text, loaded.tokenizer, request.request_id
            )
            input_tokens = int(getattr(final, "prompt_tokens", 0) or 0)
            output_tokens = int(getattr(final, "generation_tokens", 0) or 0)
            details: dict[str, JsonValue] = {
                "backend": "mlx",
                "local": True,
                "model_cache_hit": cache_hit,
                "model_load_duration_ms": loaded.load_duration_ms if not cache_hit else 0.0,
                "prompt_tokens_per_second": float(getattr(final, "prompt_tps", 0.0) or 0.0),
                "generation_tokens_per_second": float(getattr(final, "generation_tps", 0.0) or 0.0),
                "peak_memory_gb": float(getattr(final, "peak_memory", 0.0) or 0.0),
                "structured_output_enforcement": (
                    "thinker_local_parse_and_validate"
                    if request.response_schema is not None
                    else "not_requested"
                ),
            }
            return ProviderTurnResult(
                resolved_provider=call.provider,
                resolved_model=call.model_id,
                assistant_content=assistant_content,
                tool_calls=tool_calls,
                usage=Usage(
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    total_tokens=input_tokens + output_tokens,
                    provider_details=details,
                ),
                finish_reason="tool_calls" if tool_calls else str(final.finish_reason or "stop"),
                provider_metadata={"backend": "mlx", "local": True},
            )
        except Exception as exc:
            from .errors import ModelTurnProviderException

            if isinstance(exc, ModelTurnProviderException):
                raise
            raise provider_error(
                ModelTurnErrorCode.UNKNOWN_PROVIDER_FAILURE,
                f"mlx-lm failed with {type(exc).__name__}",
            ) from exc


def _render_prompt(tokenizer: MLXTokenizer, request: ModelTurnRequest) -> Any:
    if not getattr(tokenizer, "has_chat_template", True):
        raise provider_error(
            ModelTurnErrorCode.MODEL_UNAVAILABLE,
            "MLX model does not provide a chat template",
        )
    messages: list[dict[str, Any]] = []
    if request.system_instruction:
        messages.append({"role": "system", "content": request.system_instruction})
    if request.response_schema is not None:
        schema = json.dumps(request.response_schema, sort_keys=True, separators=(",", ":"))
        messages.append(
            {
                "role": "system",
                "content": (
                    "Return only one JSON value that validates against this JSON Schema. "
                    f"No markdown or commentary. Schema: {schema}"
                ),
            }
        )
    names: dict[str, str] = {}
    for message in request.messages:
        item: dict[str, Any] = {
            "role": "assistant" if message.role == MessageRole.ASSISTANT else message.role.value,
            "content": message.content or "",
        }
        if message.tool_calls:
            item["tool_calls"] = [
                {
                    "id": tool_call.call_id,
                    "type": "function",
                    "function": {
                        "name": tool_call.name,
                        "arguments": tool_call.arguments,
                    },
                }
                for tool_call in message.tool_calls
            ]
            names.update({tool_call.call_id: tool_call.name for tool_call in message.tool_calls})
        messages.append(item)
    for result in request.tool_results:
        name = names.get(result.call_id)
        if name is None:
            raise provider_error(
                ModelTurnErrorCode.TOOL_CALL_MALFORMED,
                "MLX tool result has no matching replayed tool call",
            )
        value = {"error": result.output} if result.is_error else result.output
        messages.append(
            {
                "role": "tool",
                "name": name,
                "tool_call_id": result.call_id,
                "content": value if isinstance(value, str) else json.dumps(value),
            }
        )
    tools = [
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
    if tools and not getattr(tokenizer, "has_tool_calling", False):
        raise provider_error(
            ModelTurnErrorCode.MODEL_UNAVAILABLE,
            "MLX model chat template does not support native tool calling",
        )
    kwargs: dict[str, Any] = {
        "add_generation_prompt": True,
        "tokenize": True,
        "enable_thinking": False,
    }
    if tools:
        kwargs["tools"] = tools
    return tokenizer.apply_chat_template(messages, **kwargs)


def _parse_generated_output(
    text: str, tokenizer: MLXTokenizer, request_id: str
) -> tuple[str | None, tuple[ToolCall, ...]]:
    start, end, parser = (
        getattr(tokenizer, "tool_call_start", None),
        getattr(tokenizer, "tool_call_end", None),
        getattr(tokenizer, "tool_parser", None),
    )
    if not start or not end or parser is None or start not in text:
        return text.strip() or None, ()
    calls: list[ToolCall] = []
    visible: list[str] = []
    cursor = 0
    while True:
        begin = text.find(start, cursor)
        if begin < 0:
            visible.append(text[cursor:])
            break
        visible.append(text[cursor:begin])
        finish = text.find(end, begin + len(start))
        if finish < 0:
            raise provider_error(
                ModelTurnErrorCode.TOOL_CALL_MALFORMED,
                "MLX model returned an unterminated native tool call",
            )
        raw = text[begin + len(start) : finish]
        try:
            parsed = parser(raw)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise provider_error(
                ModelTurnErrorCode.TOOL_CALL_MALFORMED,
                "MLX model returned malformed native tool arguments",
            ) from exc
        items = parsed if isinstance(parsed, list) else [parsed]
        for item in items:
            if not isinstance(item, dict):
                raise provider_error(
                    ModelTurnErrorCode.TOOL_CALL_MALFORMED,
                    "MLX native tool call is not an object",
                )
            nested = item.get("function")
            function: dict[str, Any] = nested if isinstance(nested, dict) else item
            name = function.get("name")
            arguments = function.get("arguments", {})
            if not isinstance(name, str) or not isinstance(arguments, dict):
                raise provider_error(
                    ModelTurnErrorCode.TOOL_CALL_MALFORMED,
                    "MLX native tool call omitted its name or object arguments",
                )
            # Normalization owns whitespace: line-oriented native formats (e.g.
            # the GLM family) leave the newline after the tool name and mlx-lm's
            # tool parser returns it verbatim, so a semantically perfect call
            # would otherwise fail the registry name match as an unknown tool.
            name = name.strip()
            if not name:
                raise provider_error(
                    ModelTurnErrorCode.TOOL_CALL_MALFORMED,
                    "MLX native tool call omitted its name or object arguments",
                )
            index = len(calls)
            digest = hashlib.sha256(
                json.dumps(
                    [request_id, index, name, arguments], sort_keys=True, separators=(",", ":")
                ).encode()
            ).hexdigest()[:16]
            calls.append(ToolCall(call_id=f"mlx-{digest}-{index}", name=name, arguments=arguments))
        cursor = finish + len(end)
    content = "".join(visible).strip() or None
    return content, tuple(calls)


def _first_stop(text: str, stops: tuple[str, ...]) -> int | None:
    positions = [position for stop in stops if (position := text.find(stop)) >= 0]
    return min(positions) if positions else None
