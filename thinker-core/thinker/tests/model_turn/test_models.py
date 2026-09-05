from __future__ import annotations

import json

import jsonschema
import pytest
from pydantic import ValidationError

from thinker.model_turn.models import (
    ModelMessage,
    ModelTurnRequest,
    ModelTurnResponse,
    ToolCall,
    ToolDefinition,
    Usage,
)
from thinker.model_turn.schemas import load_schema


def test_request_and_response_round_trip(runtime):
    request = ModelTurnRequest(
        request_id="round-trip",
        requested_route="test:default",
        system_instruction="Be precise.",
        messages=(
            ModelMessage(role="user", content="inspect"),
            ModelMessage(
                role="assistant",
                tool_calls=(ToolCall(call_id="c1", name="read", arguments={"path": "x"}),),
            ),
        ),
        tools=(
            ToolDefinition(
                name="read",
                description="Read one file",
                input_schema={
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                    "additionalProperties": False,
                },
            ),
        ),
    )
    restored = ModelTurnRequest.model_validate_json(request.model_dump_json())
    assert restored == request

    response = runtime.turn(restored)
    assert ModelTurnResponse.model_validate_json(response.model_dump_json()) == response


def test_checked_in_schemas_accept_serialized_models(runtime):
    request = ModelTurnRequest(
        request_id="schema",
        requested_route="test:default",
        messages=(ModelMessage(role="user", content="hello"),),
    )
    response = runtime.turn(request)
    jsonschema.validate(request.model_dump(mode="json"), load_schema("model_turn_request"))
    jsonschema.validate(response.model_dump(mode="json"), load_schema("model_turn_response"))


def test_checked_in_schemas_match_canonical_models():
    assert load_schema("model_turn_request") == ModelTurnRequest.model_json_schema()
    assert load_schema("model_turn_response") == ModelTurnResponse.model_json_schema()


@pytest.mark.parametrize(
    "metadata",
    [
        {"api_key": "secret"},
        {"nested": {"authorization": "Bearer secret"}},
        {"credentials": {"provider": "secret"}},
    ],
)
def test_request_rejects_credential_shaped_metadata(metadata):
    with pytest.raises(ValidationError):
        ModelTurnRequest(
            request_id="unsafe",
            requested_route="test:default",
            messages=(ModelMessage(role="user", content="hello"),),
            metadata=metadata,
        )


def test_tool_schema_must_be_strictly_descriptive_object():
    with pytest.raises(ValidationError):
        ToolDefinition(name="bad", description="Bad", input_schema={"type": "string"})


def test_invalid_json_schemas_are_rejected_at_request_boundary():
    with pytest.raises(ValidationError):
        ToolDefinition(
            name="bad",
            description="Bad",
            input_schema={"type": "object", "properties": {"x": {"type": "not-a-type"}}},
        )
    with pytest.raises(ValidationError):
        ModelTurnRequest(
            request_id="bad-schema",
            requested_route="test:default",
            messages=(ModelMessage(role="user", content="hello"),),
            response_schema={"type": "not-a-type"},
        )


def test_usage_total_is_normalized_and_checked():
    assert Usage(input_tokens=2, output_tokens=3).total_tokens == 5
    with pytest.raises(ValidationError):
        Usage(input_tokens=2, output_tokens=3, total_tokens=4)


def test_models_serialize_to_plain_json(runtime):
    response = runtime.turn(
        ModelTurnRequest(
            request_id="json",
            requested_route="test:default",
            messages=(ModelMessage(role="user", content="hello"),),
        )
    )
    assert json.loads(response.model_dump_json())["protocol_version"] == "thinker.model-turn.v1"
