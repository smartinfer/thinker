"""Registry-backed orchestration for one provider-neutral model turn."""

from __future__ import annotations

import json
import os
import queue
import threading
import time
from collections.abc import Callable
from importlib.metadata import PackageNotFoundError, version
from typing import Any

import jsonschema
from pydantic import JsonValue

from thinker.pricebook import PriceBook
from thinker.registry.resolver import resolve_call
from thinker.registry.schema import RegistryCall
from thinker.registry.store import RegistryStore

from .errors import ModelTurnError, ModelTurnErrorCode, ModelTurnProviderException
from .fake import FakeModelTurnProvider
from .models import Cost, ModelTurnRequest, ModelTurnResponse, Usage, json_safe
from .openai import OpenAIModelTurnProvider
from .provider import ModelTurnProvider, ProviderTurnResult

ProviderFactory = Callable[[RegistryCall], ModelTurnProvider]
ObservationSink = Callable[[dict[str, Any]], None]


class ModelTurnRuntime:
    """Resolve a route, invoke one provider, and normalize the result.

    Provider work runs in a daemon thread. Cancellation is cooperative: V1
    returns ``Cancelled`` promptly, signals the provider, and discards any late
    result. A non-cooperative network provider remains bounded by ``timeout_ms``.
    """

    def __init__(
        self,
        store: RegistryStore,
        pricebook: PriceBook | None = None,
        *,
        provider_factories: dict[str, ProviderFactory] | None = None,
        observer: ObservationSink | None = None,
        thinker_revision: str | None = None,
    ) -> None:
        self.store = store
        self.pricebook = pricebook or PriceBook()
        self._provider_factories = provider_factories or {}
        self._observer = observer
        self._active: dict[str, threading.Event] = {}
        self._lock = threading.Lock()
        self.thinker_version = _package_version()
        self.thinker_revision: str = (
            thinker_revision or os.getenv("THINKER_REVISION") or f"package:{self.thinker_version}"
        )

    def cancel(self, request_id: str) -> bool:
        with self._lock:
            event = self._active.get(request_id)
            if event is None:
                return False
            event.set()
            return True

    def turn(self, request: ModelTurnRequest) -> ModelTurnResponse:
        started = time.monotonic()
        call: RegistryCall | None = None
        cancel_event = threading.Event()
        with self._lock:
            if request.request_id in self._active:
                return self._failure_response(
                    request,
                    started,
                    ModelTurnError(
                        code=ModelTurnErrorCode.UNKNOWN_PROVIDER_FAILURE,
                        message="request_id is already active",
                    ),
                )
            self._active[request.request_id] = cancel_event

        try:
            try:
                call = self._resolve(request)
                provider = self._provider(call)
            except (KeyError, ValueError):
                return self._failure_response(
                    request,
                    started,
                    ModelTurnError(
                        code=ModelTurnErrorCode.MODEL_UNAVAILABLE,
                        message="requested model route is unavailable or lacks required capabilities",
                    ),
                )

            result_queue: queue.Queue[ProviderTurnResult | BaseException] = queue.Queue(maxsize=1)

            def invoke() -> None:
                try:
                    result_queue.put(provider.turn(request, call, cancel_event))
                except Exception as exc:  # noqa: BLE001 -- provider boundary normalizes failures
                    result_queue.put(exc)

            worker = threading.Thread(
                target=invoke,
                name=f"thinker-model-turn-{request.request_id}",
                daemon=True,
            )
            worker.start()
            deadline = started + request.timeout_ms / 1000.0
            while True:
                if cancel_event.is_set():
                    return self._failure_response(
                        request,
                        started,
                        ModelTurnError(
                            code=ModelTurnErrorCode.CANCELLED,
                            message="request cancelled",
                        ),
                        call,
                    )
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    cancel_event.set()
                    return self._failure_response(
                        request,
                        started,
                        ModelTurnError(
                            code=ModelTurnErrorCode.REQUEST_TIMEOUT,
                            message="model turn exceeded timeout_ms",
                            retryable=True,
                        ),
                        call,
                    )
                try:
                    outcome = result_queue.get(timeout=min(0.02, remaining))
                    break
                except queue.Empty:
                    continue

            if isinstance(outcome, ModelTurnProviderException):
                return self._failure_response(request, started, outcome.error, call)
            if isinstance(outcome, BaseException):
                return self._failure_response(
                    request,
                    started,
                    ModelTurnError(
                        code=ModelTurnErrorCode.UNKNOWN_PROVIDER_FAILURE,
                        message=f"provider failed with {type(outcome).__name__}",
                    ),
                    call,
                )

            structured = self._validate_structured_output(request, outcome)
            cost = self.pricebook.cost(
                call,
                outcome.usage.input_tokens,
                outcome.usage.output_tokens,
            )
            response = ModelTurnResponse(
                request_id=request.request_id,
                thinker_version=self.thinker_version,
                thinker_revision=self.thinker_revision,
                requested_route=request.requested_route,
                resolved_route=call.call_id,
                resolved_provider=outcome.resolved_provider or call.provider,
                resolved_model=outcome.resolved_model or call.model_id,
                assistant_content=outcome.assistant_content,
                tool_calls=outcome.tool_calls,
                structured_output=structured,
                continuation=outcome.continuation,
                usage=outcome.usage,
                cost=Cost(amount=cost),
                finish_reason=outcome.finish_reason,
                duration_ms=(time.monotonic() - started) * 1000.0,
            )
            self._observe(response)
            return response
        except ModelTurnProviderException as exc:
            return self._failure_response(request, started, exc.error, call)
        finally:
            with self._lock:
                if self._active.get(request.request_id) is cancel_event:
                    self._active.pop(request.request_id, None)

    def _resolve(self, request: ModelTurnRequest) -> RegistryCall:
        caps: list[str] = []
        if request.tools:
            caps.append("tools")
        if request.response_schema is not None:
            caps.append("json_mode")
        if self.store.get_call(request.requested_route):
            return resolve_call(
                self.store,
                call_id=request.requested_route,
                model=None,
                alias=None,
                intent="chat",
                need_modality="text",
                need_caps=caps,
            )
        if "/" in request.requested_route:
            return resolve_call(
                self.store,
                call_id=None,
                model=request.requested_route,
                alias=None,
                intent="chat",
                need_modality="text",
                need_caps=caps,
            )
        return resolve_call(
            self.store,
            call_id=None,
            model=None,
            alias=request.requested_route,
            intent="chat",
            need_modality="text",
            need_caps=caps,
        )

    def _provider(self, call: RegistryCall) -> ModelTurnProvider:
        factory = self._provider_factories.get(call.adapter)
        if factory:
            return factory(call)
        if call.adapter == "openai":
            return OpenAIModelTurnProvider(call.endpoint)
        if call.adapter in {"local", "fake"}:
            return FakeModelTurnProvider()
        raise ValueError(f"no V1 model-turn provider for adapter {call.adapter!r}")

    def _validate_structured_output(
        self, request: ModelTurnRequest, result: ProviderTurnResult
    ) -> JsonValue | None:
        if request.response_schema is None:
            return result.structured_output
        candidate = result.structured_output
        if candidate is None:
            try:
                candidate = json.loads(result.assistant_content or "")
            except (TypeError, json.JSONDecodeError) as exc:
                raise ModelTurnProviderException(
                    ModelTurnError(
                        code=ModelTurnErrorCode.STRUCTURED_OUTPUT_VIOLATION,
                        message="model output is not valid JSON",
                    )
                ) from exc
        try:
            jsonschema.validate(candidate, request.response_schema)
        except jsonschema.ValidationError as exc:
            raise ModelTurnProviderException(
                ModelTurnError(
                    code=ModelTurnErrorCode.STRUCTURED_OUTPUT_VIOLATION,
                    message="model output violates response_schema",
                )
            ) from exc
        return json_safe(candidate)

    def _failure_response(
        self,
        request: ModelTurnRequest,
        started: float,
        error: ModelTurnError,
        call: RegistryCall | None = None,
    ) -> ModelTurnResponse:
        response = ModelTurnResponse(
            request_id=request.request_id,
            thinker_version=self.thinker_version,
            thinker_revision=self.thinker_revision,
            requested_route=request.requested_route,
            resolved_route=call.call_id if call else None,
            resolved_provider=call.provider if call else None,
            resolved_model=call.model_id if call else None,
            usage=Usage(),
            cost=Cost(),
            finish_reason="error",
            duration_ms=(time.monotonic() - started) * 1000.0,
            normalized_error=error,
        )
        self._observe(response)
        return response

    def _observe(self, response: ModelTurnResponse) -> None:
        if self._observer is None:
            return
        self._observer(
            {
                "request_id": response.request_id,
                "protocol_version": response.protocol_version,
                "requested_route": response.requested_route,
                "resolved_route": response.resolved_route,
                "resolved_provider": response.resolved_provider,
                "resolved_model": response.resolved_model,
                "duration_ms": response.duration_ms,
                "usage": response.usage.model_dump(mode="json"),
                "cost": response.cost.model_dump(mode="json"),
                "finish_reason": response.finish_reason,
                "normalized_error": (
                    response.normalized_error.model_dump(mode="json")
                    if response.normalized_error
                    else None
                ),
            }
        )


def _package_version() -> str:
    try:
        return str(version("thinker-core"))
    except PackageNotFoundError:
        from thinker import __version__

        return __version__
