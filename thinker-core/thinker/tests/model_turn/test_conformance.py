from __future__ import annotations

import threading
import time

import pytest

from thinker.model_turn.errors import ModelTurnErrorCode
from thinker.model_turn.models import (
    Continuation,
    ModelMessage,
    ToolDefinition,
    ToolResult,
)

from .conftest import make_request


def tool(name: str) -> ToolDefinition:
    return ToolDefinition(
        name=name,
        description=f"Execute {name}",
        input_schema={
            "type": "object",
            "properties": {},
            "additionalProperties": True,
        },
    )


def test_basic_system_user_and_assistant_text(runtime):
    response = runtime.turn(
        make_request(
            system_instruction="system",
            messages=(
                ModelMessage(role="system", content="policy"),
                ModelMessage(role="user", content="question"),
            ),
        )
    )
    assert response.normalized_error is None
    assert response.assistant_content == "deterministic fake response"
    assert response.finish_reason == "stop"


def test_multi_turn_message_replay(runtime):
    response = runtime.turn(
        make_request(
            request_id="multi-turn",
            messages=(
                ModelMessage(role="user", content="one"),
                ModelMessage(role="assistant", content="two"),
                ModelMessage(role="user", content="three"),
            ),
        )
    )
    assert response.normalized_error is None


def test_one_tool_call_has_stable_structured_identity(runtime):
    response = runtime.turn(make_request("one_tool_call", tools=(tool("read_file"),)))
    assert [(call.call_id, call.name) for call in response.tool_calls] == [("call-1", "read_file")]
    assert response.tool_calls[0].arguments == {"path": "README.md"}


def test_multiple_tool_calls_remain_distinct(runtime):
    response = runtime.turn(
        make_request(
            "multiple_tool_calls",
            tools=(tool("read_file"), tool("run_tests")),
        )
    )
    assert [call.call_id for call in response.tool_calls] == ["call-1", "call-2"]


def test_tool_result_continuation_reaches_final_answer(runtime):
    first = runtime.turn(
        make_request(
            "tool_result_continuation",
            request_id="tool-first",
            tools=(tool("read_file"), tool("run_tests")),
        )
    )
    assert first.finish_reason == "tool_calls"
    second = runtime.turn(
        make_request(
            "tool_result_continuation",
            request_id="tool-second",
            tools=(tool("read_file"), tool("run_tests")),
            tool_results=(
                ToolResult(call_id="call-1", output={"text": "contents"}),
                ToolResult(call_id="call-2", output={"passed": True}),
            ),
            continuation=first.continuation,
        )
    )
    assert [call.call_id for call in first.tool_calls] == ["call-1", "call-2"]
    assert second.assistant_content == "received tool results: call-1,call-2"
    assert second.finish_reason == "stop"


def test_fake_route_can_freeze_tool_loop_without_caller_test_metadata(fake_call):
    from thinker.model_turn.runtime import ModelTurnRuntime
    from thinker.registry.store import RegistryStore

    frozen_call = fake_call.model_copy(
        update={
            "call_id": "fake:one-tool-loop",
            "model_id": "scenario-one_tool_call",
            "aliases": [],
        }
    )
    store = RegistryStore()
    store.put_call(frozen_call)
    runtime = ModelTurnRuntime(store, thinker_revision="frozen-test-revision")
    first = runtime.turn(
        make_request(
            request_id="route-loop-first",
            requested_route=frozen_call.call_id,
            metadata={},
            tools=(tool("read_file"),),
        )
    )
    second = runtime.turn(
        make_request(
            request_id="route-loop-second",
            requested_route=frozen_call.call_id,
            metadata={},
            tools=(tool("read_file"),),
            tool_results=(ToolResult(call_id="call-1", output="contents"),),
            continuation=first.continuation,
        )
    )
    assert first.tool_calls[0].name == "read_file"
    assert second.assistant_content == "received tool results: call-1"


def test_fake_network_probe_is_transport_only(runtime):
    response = runtime.turn(make_request("network_attempt", tools=(tool("run_command"),)))
    assert response.finish_reason == "tool_calls"
    assert response.tool_calls[0].name == "run_command"
    assert response.tool_calls[0].call_id == "call-network"
    assert response.tool_calls[0].arguments["argv"][0] == "/usr/bin/python3"


def test_valid_structured_output(runtime):
    response = runtime.turn(
        make_request(
            "structured_valid",
            response_schema={
                "type": "object",
                "properties": {"result": {"const": "ok"}},
                "required": ["result"],
                "additionalProperties": False,
            },
        )
    )
    assert response.structured_output == {"result": "ok"}
    assert response.normalized_error is None


def test_invalid_structured_output_is_typed_failure(runtime):
    response = runtime.turn(
        make_request(
            "structured_invalid",
            response_schema={
                "type": "object",
                "properties": {"result": {"type": "string"}},
                "required": ["result"],
                "additionalProperties": False,
            },
        )
    )
    assert response.structured_output is None
    assert response.normalized_error.code == ModelTurnErrorCode.STRUCTURED_OUTPUT_VIOLATION


def test_provider_continuation_is_opaque(runtime):
    response = runtime.turn(make_request(continuation=Continuation(token="provider-token")))
    assert response.continuation.token == "fake-response-request-plain_text"


@pytest.mark.parametrize(
    ("scenario", "code"),
    [
        ("model_unavailable", ModelTurnErrorCode.MODEL_UNAVAILABLE),
        ("authentication_failure", ModelTurnErrorCode.AUTHENTICATION_FAILURE),
        ("rate_limit", ModelTurnErrorCode.RATE_LIMITED),
        ("overload", ModelTurnErrorCode.PROVIDER_OVERLOADED),
        ("malformed_response", ModelTurnErrorCode.MALFORMED_RESPONSE),
        ("malformed_tool_arguments", ModelTurnErrorCode.TOOL_CALL_MALFORMED),
    ],
)
def test_error_taxonomy(runtime, scenario, code):
    response = runtime.turn(make_request(scenario))
    assert response.normalized_error.code == code
    assert response.assistant_content is None


def test_per_request_timeout(runtime):
    response = runtime.turn(make_request("timeout", timeout_ms=20))
    assert response.normalized_error.code == ModelTurnErrorCode.REQUEST_TIMEOUT


def test_external_cancellation(runtime):
    request = make_request("cancellation", request_id="cancel-me", timeout_ms=1_000)
    responses = []
    worker = threading.Thread(target=lambda: responses.append(runtime.turn(request)))
    worker.start()
    for _ in range(100):
        if runtime.cancel(request.request_id):
            break
        time.sleep(0.005)
    worker.join(timeout=1.0)
    assert not worker.is_alive()
    assert responses[0].normalized_error.code == ModelTurnErrorCode.CANCELLED


def test_route_and_model_provenance_and_cost(runtime):
    response = runtime.turn(make_request(requested_route="test:default", request_id="alias-route"))
    assert response.requested_route == "test:default"
    assert response.resolved_route == "fake:deterministic.chat"
    assert response.resolved_provider == "fake"
    assert response.resolved_model == "deterministic-v1"
    assert response.thinker_revision == "test-revision"
    assert response.usage.total_tokens == 10
    assert response.cost.amount == pytest.approx(0.000013)


def test_observer_receives_metadata_but_no_content(fake_call):
    from thinker.model_turn.runtime import ModelTurnRuntime
    from thinker.registry.store import RegistryStore

    observations = []
    store = RegistryStore()
    store.put_call(fake_call)
    runtime = ModelTurnRuntime(store, observer=observations.append)
    runtime.turn(
        make_request(
            metadata={"fake_scenario": "plain_text", "fake_text": "private response"},
            messages=(ModelMessage(role="user", content="private prompt"),),
        )
    )
    serialized = str(observations)
    assert "private prompt" not in serialized
    assert "private response" not in serialized
    assert observations[0]["request_id"] == "request-plain_text"
