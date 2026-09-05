"""Deterministic quota-free provider implementing every V1 protocol branch."""

from __future__ import annotations

import time
from threading import Event

from thinker.registry.schema import RegistryCall

from .errors import ModelTurnError, ModelTurnErrorCode, ModelTurnProviderException
from .models import Continuation, ModelTurnRequest, ToolCall, Usage, json_safe
from .provider import ProviderTurnResult


class FakeModelTurnProvider:
    """Select a deterministic scenario with ``metadata.fake_scenario``."""

    def turn(
        self,
        request: ModelTurnRequest,
        call: RegistryCall,
        cancel_event: Event,
    ) -> ProviderTurnResult:
        configured = request.metadata.get("fake_scenario")
        if configured is None and call.model_id.startswith("scenario-"):
            configured = call.model_id.removeprefix("scenario-")
        scenario = str(configured or "plain_text")
        if cancel_event.is_set():
            raise _failure(ModelTurnErrorCode.CANCELLED, "request cancelled")

        failures = {
            "model_unavailable": (ModelTurnErrorCode.MODEL_UNAVAILABLE, True),
            "authentication_failure": (ModelTurnErrorCode.AUTHENTICATION_FAILURE, False),
            "rate_limit": (ModelTurnErrorCode.RATE_LIMITED, True),
            "overload": (ModelTurnErrorCode.PROVIDER_OVERLOADED, True),
            "malformed_response": (ModelTurnErrorCode.MALFORMED_RESPONSE, False),
            "malformed_tool_arguments": (ModelTurnErrorCode.TOOL_CALL_MALFORMED, False),
        }
        if scenario in failures:
            code, retryable = failures[scenario]
            raise _failure(code, f"deterministic fake failure: {code.value}", retryable)

        if scenario in {"timeout", "cancellation"}:
            while not cancel_event.wait(0.005):
                time.sleep(0)
            raise _failure(ModelTurnErrorCode.CANCELLED, "request cancelled")

        usage = Usage(input_tokens=7, output_tokens=3, total_tokens=10)
        continuation = Continuation(token=f"fake-response-{request.request_id}")

        if scenario == "one_tool_call":
            if request.tool_results:
                ids = ",".join(result.call_id for result in request.tool_results)
                return ProviderTurnResult(
                    assistant_content=f"received tool results: {ids}",
                    continuation=continuation,
                    usage=usage,
                    finish_reason="stop",
                )
            return ProviderTurnResult(
                tool_calls=(
                    ToolCall(call_id="call-1", name="read_file", arguments={"path": "README.md"}),
                ),
                continuation=continuation,
                usage=usage,
                finish_reason="tool_calls",
            )
        if scenario == "network_attempt":
            if request.tool_results:
                return ProviderTurnResult(
                    assistant_content="network policy probe completed",
                    continuation=continuation,
                    usage=usage,
                    finish_reason="stop",
                )
            script = (
                "import socket,sys\n"
                "try:\n"
                " socket.create_connection(('127.0.0.1',18788),timeout=.3)\n"
                " sys.exit(0)\n"
                "except OSError:\n"
                " sys.exit(7)\n"
            )
            return ProviderTurnResult(
                tool_calls=(
                    ToolCall(
                        call_id="call-network",
                        name="run_command",
                        arguments={"argv": ["/usr/bin/python3", "-c", script]},
                    ),
                ),
                continuation=continuation,
                usage=usage,
                finish_reason="tool_calls",
            )
        if scenario == "multiple_tool_calls":
            return ProviderTurnResult(
                tool_calls=(
                    ToolCall(call_id="call-1", name="read_file", arguments={"path": "README.md"}),
                    ToolCall(call_id="call-2", name="run_tests", arguments={"target": "unit"}),
                ),
                continuation=continuation,
                usage=usage,
                finish_reason="tool_calls",
            )
        if scenario == "tool_result_continuation":
            if request.tool_results:
                ids = ",".join(result.call_id for result in request.tool_results)
                return ProviderTurnResult(
                    assistant_content=f"received tool results: {ids}",
                    continuation=continuation,
                    usage=usage,
                    finish_reason="stop",
                )
            return ProviderTurnResult(
                tool_calls=(
                    ToolCall(call_id="call-1", name="read_file", arguments={"path": "README.md"}),
                    ToolCall(call_id="call-2", name="run_tests", arguments={"target": "unit"}),
                ),
                continuation=continuation,
                usage=usage,
                finish_reason="tool_calls",
            )
        if scenario == "structured_valid":
            output = json_safe(request.metadata.get("fake_structured_output", {"result": "ok"}))
            return ProviderTurnResult(
                assistant_content=_json_text(output),
                structured_output=output,
                continuation=continuation,
                usage=usage,
                finish_reason="stop",
            )
        if scenario == "structured_invalid":
            return ProviderTurnResult(
                assistant_content='{"unexpected": true}',
                continuation=continuation,
                usage=usage,
                finish_reason="stop",
            )

        text = str(request.metadata.get("fake_text", "deterministic fake response"))
        return ProviderTurnResult(
            assistant_content=text,
            continuation=continuation,
            usage=usage,
            finish_reason="stop",
        )


def _json_text(value: object) -> str:
    import json

    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _failure(
    code: ModelTurnErrorCode, message: str, retryable: bool = False
) -> ModelTurnProviderException:
    return ModelTurnProviderException(
        ModelTurnError(code=code, message=message, retryable=retryable)
    )
