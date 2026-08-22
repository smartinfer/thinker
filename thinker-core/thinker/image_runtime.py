"""Execution, retries, budgets, idempotency, and batching for image calls."""

from __future__ import annotations

import hashlib
import random
import time
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from pathlib import Path

from .adapters import get_adapter
from .image_budget import BudgetLedger
from .image_inspect import normalize_image_output
from .image_ledger import CompletionLedger, request_fingerprint
from .image_models import (
    AttemptRecord,
    ImageAdapterError,
    ImageGenerationRequest,
    ImageGenerationResponse,
    StructuredError,
)
from .image_pricing import estimate_image_request_cost
from .observability.metrics import record_request
from .registry.resolver import resolve_image_call


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _error_response(request, call, registry_sha, error, attempts=(), latency_ms=0.0):
    provider = call.provider if call else "unknown"
    model = call.model_id if call else "unknown"
    return ImageGenerationResponse(
        request_id=request.request_id,
        call_id=request.call_id,
        provider=provider,
        requested_model_id=model,
        resolved_model_id=model,
        provider_model_id=None,
        registry_sha256=registry_sha,
        provider_response_id=None,
        effective_parameters={},
        outputs=(),
        usage={},
        cost_usd=None,
        latency_ms=latency_ms,
        attempts=tuple(attempts),
        caller_metadata=dict(request.caller_metadata),
        status="error",
        error=error,
    )


def _validate_parameters(request, call) -> StructuredError | None:
    limits = call.image_limits
    if limits is None:
        return StructuredError("adapter_error", "image registry row has no image_limits", False)
    if request.n > limits.max_n:
        return StructuredError(
            "unsupported_parameter", f"n={request.n} exceeds registry max_n={limits.max_n}", False
        )
    if request.size and limits.supported_sizes and request.size not in limits.supported_sizes:
        return StructuredError("unsupported_parameter", f"unsupported size: {request.size}", False)
    if (
        request.aspect_ratio
        and limits.supported_aspect_ratios
        and request.aspect_ratio not in limits.supported_aspect_ratios
    ):
        return StructuredError(
            "unsupported_parameter", f"unsupported aspect_ratio: {request.aspect_ratio}", False
        )
    if request.aspect_ratio and not limits.supported_aspect_ratios:
        return StructuredError(
            "unsupported_parameter", "aspect_ratio is unsupported for this call", False
        )
    if request.quality and (
        not limits.quality_supported or request.quality not in limits.supported_qualities
    ):
        return StructuredError(
            "unsupported_parameter", f"unsupported quality: {request.quality}", False
        )
    if request.seed is not None and not limits.seed_supported:
        return StructuredError("unsupported_parameter", "seed is unsupported for this call", False)
    return None


def _record_metrics(response: ImageGenerationResponse) -> None:
    """Best-effort secondary telemetry; the returned response remains authoritative."""
    try:
        record_request(
            provider=response.provider,
            model=response.resolved_model_id,
            call_id=response.call_id,
            input_tokens=0,
            output_tokens=0,
            latency_ms=response.latency_ms,
            cost_usd=response.cost_usd or 0.0,
            status=response.status,
            error_code=response.error.category if response.error else None,
        )
    except Exception:  # noqa: BLE001 - observability cannot invalidate a paid result
        return


class ImageGateway:
    def __init__(
        self,
        store,
        *,
        ledger: CompletionLedger,
        sleep: Callable[[float], None] = time.sleep,
        random_value: Callable[[], float] = random.random,
    ):
        self.store = store
        self.ledger = ledger
        self.sleep = sleep
        self.random_value = random_value

    def generate(
        self, request: ImageGenerationRequest, *, budget_ledger: BudgetLedger | None = None
    ) -> ImageGenerationResponse:
        started = time.monotonic()
        fingerprint = request_fingerprint(request)
        version = self.store.version()
        registry_sha = version.version_id if version else "unknown"
        claim = self.ledger.claim(
            request.request_id, fingerprint, retry_failed=request.retry_failed
        )
        if claim == "success" or claim == "failure":
            replay = self.ledger.replay(request.request_id)
            if replay is not None:
                return replay
        if claim == "conflict":
            return _error_response(
                request,
                None,
                registry_sha,
                StructuredError(
                    "idempotency_conflict",
                    "request_id was previously used with different immutable parameters",
                    False,
                ),
            )
        if claim == "in_progress":
            return _error_response(
                request,
                None,
                registry_sha,
                StructuredError("idempotency_conflict", "request_id is already in progress", False),
            )

        try:
            call = resolve_image_call(self.store, request.call_id)
        except ValueError as exc:
            response = _error_response(
                request, None, registry_sha, StructuredError("adapter_error", str(exc), False)
            )
            self.ledger.complete(fingerprint, response)
            return response
        parameter_error = _validate_parameters(request, call)
        if parameter_error:
            response = _error_response(request, call, registry_sha, parameter_error)
            self.ledger.complete(fingerprint, response)
            return response

        estimate = estimate_image_request_cost(request, call)
        if not estimate.known and not request.allow_unknown_cost:
            response = _error_response(
                request,
                call,
                registry_sha,
                StructuredError(
                    "budget_rejected", "image price is unknown; execution fails closed", False
                ),
            )
            self.ledger.complete(fingerprint, response)
            return response
        if request.max_cost_usd is not None and (
            not estimate.known
            or estimate.estimated_max_usd is None
            or estimate.estimated_max_usd > request.max_cost_usd + 1e-12
        ):
            response = _error_response(
                request,
                call,
                registry_sha,
                StructuredError(
                    "budget_rejected", "estimated image cost exceeds the per-call budget", False
                ),
            )
            self.ledger.complete(fingerprint, response)
            return response
        reserved = False
        if budget_ledger is not None:
            if not estimate.known or estimate.estimated_max_usd is None:
                response = _error_response(
                    request,
                    call,
                    registry_sha,
                    StructuredError(
                        "budget_rejected", "run budget cannot reserve an unknown image price", False
                    ),
                )
                self.ledger.complete(fingerprint, response)
                return response
            reserved = budget_ledger.reserve(request.request_id, estimate.estimated_max_usd)
            if not reserved:
                response = _error_response(
                    request,
                    call,
                    registry_sha,
                    StructuredError(
                        "budget_rejected", "remaining run budget is insufficient", False
                    ),
                )
                self.ledger.complete(fingerprint, response)
                return response

        attempts: list[AttemptRecord] = []
        provider_result = None
        error = None
        for attempt_number in range(1, request.max_attempts + 1):
            attempt_started = time.monotonic()
            attempt_time = _now()
            try:
                provider_result = get_adapter(call.adapter).generate_image(request, call)
                attempts.append(
                    AttemptRecord(
                        attempt_number,
                        attempt_time,
                        (time.monotonic() - attempt_started) * 1000,
                        "success",
                    )
                )
                break
            except ImageAdapterError as exc:
                error = exc.error
            except Exception:  # noqa: BLE001 - adapter boundary must become a structured result
                error = StructuredError("adapter_error", "unclassified adapter failure", False)
            attempts.append(
                AttemptRecord(
                    attempt_number,
                    attempt_time,
                    (time.monotonic() - attempt_started) * 1000,
                    "error",
                    error.category,
                    error.provider_status_code,
                )
            )
            if (
                not error.retryable
                or error.ambiguous_after_send
                or attempt_number >= request.max_attempts
            ):
                break
            delay = error.retry_after_seconds
            if delay is None:
                delay = min(8.0, 0.25 * (2 ** (attempt_number - 1))) + 0.1 * self.random_value()
            self.sleep(delay)

        latency_ms = (time.monotonic() - started) * 1000
        if provider_result is None:
            response = _error_response(
                request,
                call,
                registry_sha,
                error or StructuredError("unknown", "image call failed", False),
                attempts,
                latency_ms,
            )
            if reserved:
                budget_ledger.release(request.request_id)
        else:
            try:
                outputs = tuple(
                    normalize_image_output(i, item.data, item.claimed_mime_type)
                    for i, item in enumerate(provider_result.outputs)
                )
                if len(outputs) != request.n:
                    raise ValueError(f"provider returned {len(outputs)} outputs for n={request.n}")
                usage = dict(provider_result.usage)
                usage["pricing_basis"] = estimate.pricing_basis
                usage["cost_is_preflight_estimate"] = estimate.known
                usage["prompt_sha256"] = hashlib.sha256(request.prompt.encode("utf-8")).hexdigest()
                response = ImageGenerationResponse(
                    request.request_id,
                    request.call_id,
                    call.provider,
                    call.model_id,
                    call.model_id,
                    provider_result.provider_model_id,
                    registry_sha,
                    provider_result.provider_response_id,
                    provider_result.effective_parameters,
                    outputs,
                    usage,
                    estimate.estimated_max_usd,
                    latency_ms,
                    tuple(attempts),
                    dict(request.caller_metadata),
                    "success",
                    None,
                )
                if reserved:
                    budget_ledger.commit(request.request_id, response.cost_usd)
            except Exception:  # noqa: BLE001 - untrusted provider bytes must not escape unstructured
                response = _error_response(
                    request,
                    call,
                    registry_sha,
                    StructuredError(
                        "invalid_response", "provider image bytes failed local validation", False
                    ),
                    attempts,
                    latency_ms,
                )
                if reserved:
                    budget_ledger.release(request.request_id)
        _record_metrics(response)
        self.ledger.complete(fingerprint, response)
        return response


def default_ledger_path() -> str:
    import os

    return os.getenv(
        "THINKER_IMAGE_LEDGER_DB", str(Path.home() / ".thinker" / "image_completions.db")
    )


def map_images(
    thinker,
    requests: Iterable[ImageGenerationRequest],
    *,
    budget_usd: float,
    max_concurrency: int = 1,
):
    """Serial, resumable image batch. The bounded-concurrency surface is reserved for a later version."""
    if max_concurrency != 1:
        raise ValueError("v0.3 map_images supports max_concurrency=1 only")
    budget = BudgetLedger(budget_usd)
    return [thinker.generate_image(request, budget_ledger=budget) for request in requests]
