"""Integrate functional planned inference with the existing registry/runtime."""

from __future__ import annotations

import asyncio
import threading
import time
import uuid
from pathlib import Path

from .errors import ModelTurnError, ModelTurnErrorCode
from .http_common import resolve_credential
from .inference_contract import CapabilityProfile, InferenceOutcome
from .inference_execution import execute_inference
from .inference_plan import lower_request, plan_turn
from .models import ModelTurnResponse, Usage


def planned_turn(runtime, request):
    """Synchronous service edge; pure plan/lowering and async one-shot execution."""
    event = threading.Event()
    plan = None
    call = None
    with runtime._lock:
        if request.request_id in runtime._active:
            return runtime._failure_response(
                request,
                time.monotonic(),
                ModelTurnError(
                    code=ModelTurnErrorCode.UNKNOWN_PROVIDER_FAILURE,
                    message="request_id is already active",
                ),
            )
        runtime._active[request.request_id] = event
    try:
        try:
            call = runtime.store.get_call(request.requested_route)
            profile = runtime.inference_profiles.get(request.requested_route)
            if call is None or profile is None:
                raise ValueError("exact route and host capability profile required")
            profile = CapabilityProfile.model_validate(profile)
            plan = plan_turn(request, call, profile, runtime.thinker_revision)
            prepared = lower_request(request, plan)
        except (ValueError, KeyError, TypeError):
            outcome = InferenceOutcome(
                execution_status="CONFIGURATION_ERROR",
                error_code="INCOMPATIBLE_ROUTE_OR_REQUIREMENTS",
            )
        else:
            # Path derives from a host UUID, never a client-supplied file path.
            directory = (
                Path(runtime.inference_audit_root) / uuid.uuid4().hex
                if runtime.inference_audit_root
                else None
            )
            try:
                outcome = asyncio.run(
                    execute_inference(
                        prepared,
                        credentials=runtime.inference_credentials or resolve_credential,
                        send=runtime.inference_sender,
                        audit_directory=directory,
                        cancel_event=event,
                    )
                )
            except OSError:
                outcome = InferenceOutcome(
                    execution_status="CONFIGURATION_ERROR",
                    error_code="AUDIT_INITIALIZATION_FAILED",
                    audit_status="CAPTURE_FAILED",
                )

        usage = outcome.usage
        legacy_usage = None
        if usage.input_tokens is not None and usage.output_tokens is not None:
            legacy_usage = Usage(
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                reasoning_tokens=usage.reasoning_tokens,
                cached_input_tokens=usage.cached_input_tokens,
                provider_details={
                    "reported": usage.raw,
                    "accounting": "see inference_outcome.usage for unaltered provider totals",
                },
            )
        error = None
        if outcome.structure_status == "INVALID":
            code = ModelTurnErrorCode.STRUCTURED_OUTPUT_VIOLATION
        elif outcome.execution_status == "TIMED_OUT":
            code = ModelTurnErrorCode.REQUEST_TIMEOUT
        elif outcome.execution_status == "CANCELLED":
            code = ModelTurnErrorCode.CANCELLED
        elif outcome.execution_status == "CONFIGURATION_ERROR":
            code = ModelTurnErrorCode.MODEL_UNAVAILABLE
        elif outcome.execution_status == "PROVIDER_ERROR":
            code = ModelTurnErrorCode.UNKNOWN_PROVIDER_FAILURE
        else:
            code = None
        if code:
            error = ModelTurnError(
                code=code, message=outcome.error_code or outcome.execution_status
            )
        response = ModelTurnResponse(
            request_id=request.request_id,
            thinker_version=runtime.thinker_version,
            thinker_revision=runtime.thinker_revision,
            requested_route=request.requested_route,
            resolved_route=call.call_id if call else None,
            resolved_provider=call.provider if call else None,
            resolved_model=outcome.resolved_model,
            assistant_content=outcome.answer,
            structured_output=outcome.structured_output,
            usage=legacy_usage,
            cost=None,
            finish_reason=outcome.finish_reason or outcome.execution_status,
            duration_ms=(outcome.elapsed_seconds or 0) * 1000,
            normalized_error=error,
            inference_plan=plan,
            inference_outcome=outcome,
        )
        runtime._observe(response)
        return response
    finally:
        with runtime._lock:
            runtime._active.pop(request.request_id, None)
