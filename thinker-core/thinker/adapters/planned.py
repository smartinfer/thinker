"""Thin ThinkerQL adapter over the existing native inference functions.
No provider HTTP implementation or application semantics are defined here.
"""

import asyncio
import uuid

from ..model_turn.http_common import resolve_credential
from ..model_turn.inference_contract import CapabilityProfile, InferencePolicy
from ..model_turn.inference_execution import execute_inference
from ..model_turn.inference_plan import lower_request, plan_turn
from ..model_turn.models import GenerationParameters, ModelMessage, ModelTurnRequest
from .base import AdapterResponse, BaseAdapter


def prepare_request(request, call, config):
    if set(config) != {"capability_profile"}:
        raise ValueError("UNKNOWN_ADAPTER_CONFIGURATION")
    allowed = {"json_schema", "max_output_tokens", "timeout_ms", "thinking"}
    if set(request.instructions) - allowed:
        raise ValueError("UNSUPPORTED_QUALIFIED_INSTRUCTION")
    if request.documents or any(p["type"] != "text" for m in request.messages for p in m["parts"]):
        raise ValueError("QUALIFIED_TEXT_ONLY")
    profile = CapabilityProfile.model_validate(config["capability_profile"])
    message = ModelTurnRequest(
        request_id="qualification-" + uuid.uuid4().hex,
        requested_route=call.call_id,
        messages=tuple(
            ModelMessage(role=m["role"], content="\n".join(p["text"] for p in m["parts"]))
            for m in request.messages
        ),
        response_schema=request.instructions["json_schema"],
        generation=GenerationParameters(
            max_output_tokens=request.instructions.get("max_output_tokens", 256)
        ),
        timeout_ms=request.instructions.get("timeout_ms", 120000),
        inference_policy=InferencePolicy(
            expected_model=call.model_id,
            thinking=request.instructions.get("thinking", {"mode": "provider_default"}),
        ),
    )
    return lower_request(message, plan_turn(message, call, profile, "qualification-v1"))


class PlannedAdapter(BaseAdapter):
    def __init__(self, config, audit_directory):
        self.config = config
        self.audit_directory = audit_directory

    def chat(self, request, call):
        prepared = prepare_request(request, call, self.config)
        outcome = asyncio.run(
            execute_inference(
                prepared, credentials=resolve_credential, audit_directory=self.audit_directory
            )
        )
        return AdapterResponse(
            text=outcome.answer or "",
            tokens={
                "input": outcome.usage.input_tokens or 0,
                "output": outcome.usage.output_tokens or 0,
            },
            model=outcome.resolved_model or call.model_id,
            provider=call.provider,
            error=outcome.error_code,
            execution=outcome.model_dump(mode="json"),
        )
