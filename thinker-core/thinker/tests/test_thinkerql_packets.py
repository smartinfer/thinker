"""Offline packet contracts, including lossless host-driven correction turns."""

import json
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path

import jsonschema
import pytest

from thinker.thinkerql import packet_schema, parse_request, parse_response
from thinker.thinkerql.parse import parse_thinkerql

FIXTURES = Path(__file__).parent / "fixtures" / "thinkerql"


def request():
    return {
        "version": "0.4",
        "request_id": "request-1",
        "job_id": "job-1",
        "turn_id": "turn-1",
        "intent": "chat",
        "messages": [{"role": "user", "parts": [{"type": "text", "text": "Read main.c"}]}],
        "tools": [{"name": "read_file", "input_schema": {"type": "object", "required": ["path"]}}],
        "instructions": {"json_schema": {"type": "object"}, "max_output_tokens": 100},
        "routing": {
            "difficulty": "hard",
            "reasoning": {"effort": "high"},
            "local_mode": "assist",
            "local_route_id": "local:coder.chat",
        },
        "timeout_ms": 30000,
        "provider_options": {"custom_option": 7},
    }


def test_schemas_are_standalone_and_valid():
    for kind in ("request", "response"):
        jsonschema.Draft202012Validator.check_schema(packet_schema(kind))


def test_full_request_is_preserved_and_detached_from_caller():
    original = request()
    parsed = parse_request(original)
    assert parsed == original
    parsed["messages"][0]["parts"][0]["text"] = "changed"
    parsed["routing"]["reasoning"]["effort"] = "low"
    assert original == request()


def test_malformed_proposal_and_correction_round_trip():
    packet = json.loads((FIXTURES / "malformed_response.json").read_text())
    response = parse_response(packet)
    assert response == packet
    # The caller can replay the ORIGINAL invalid proposal, opaque signature and
    # an error tool result. Thinker must neither parse nor 'fix' the arguments.
    followup = request()
    followup["messages"].append({"role": "assistant", "tool_calls": response["tool_calls"]})
    followup["tool_results"] = [
        {
            "call_id": "tool-1",
            "is_error": True,
            "output": "Invalid JSON: trailing comma. Correct this call.",
        }
    ]
    followup["continuation"] = response["continuation"]
    assert parse_request(followup) == followup
    assert response["tool_calls"][0]["arguments"] == '{"path":"main.c",}'


@pytest.mark.parametrize(
    "content",
    ['```json\n{"answer":7}\n```', '{"answer":7,}', '{"answer":"seven"}', '{"answer":', "", None],
)
def test_content_is_not_an_output_acceptance_gate(content):
    packet = {"version": "0.4", "request_id": "r", "status": "completed", "content": content}
    assert parse_response(packet) == packet


@pytest.mark.parametrize("arguments", ['{"path":', [1, 2], 7, False, None, {"wrong": "field"}])
def test_bad_tool_proposals_are_not_transport_errors(arguments):
    packet = {
        "version": "0.4",
        "request_id": "r",
        "status": "completed",
        "tool_calls": [{"name": None, "call_id": None, "arguments": arguments}],
    }
    assert parse_response(packet) == packet


def test_partial_result_keeps_usage_continuation_and_unknown_cost():
    packet = json.loads((FIXTURES / "partial_response.json").read_text())
    assert parse_response(packet) == packet
    assert packet["usage"]["output_tokens"] is None
    assert packet["invocations"][0]["usage"]["output_tokens"] == 9


@pytest.mark.parametrize("status", ["completed", "incomplete", "refused", "cancelled"])
def test_terminal_outcomes_do_not_require_fabricated_usage(status):
    packet = {"version": "0.4", "request_id": "r", "status": status}
    assert parse_response(packet) == packet


@pytest.mark.parametrize(
    "mutation",
    [
        {"version": "0.5"},
        {"request_id": ""},
        {"timeout_ms": 0},
        {"routing": {"route_id": "a", "model": "b"}},
        {"routing": {"call_id": "ambiguous-old-name"}},
        {"instructions": {"json_schema_strict": True}},
        {"instructions": {"json_schema": {}, "json_schema_strict": "true"}},
        {"messages": [{"role": "user", "tool_calls": [{"name": "read_file"}]}]},
        {"provider_options": {"temperature": float("nan")}},
        {"provider_options": {"non_json_tuple": (1, 2)}},
        {"provider_options": {7: "non_string_key"}},
    ],
)
def test_invalid_request_envelopes_are_rejected(mutation):
    packet = request() | mutation
    with pytest.raises(ValueError, match="request envelope"):
        parse_request(packet)


def test_failed_response_requires_error_but_accepts_future_provider_category():
    packet = {"version": "0.4", "request_id": "r", "status": "failed"}
    with pytest.raises(ValueError, match="response envelope"):
        parse_response(packet)
    packet["error"] = {"code": "FUTURE_PROVIDER_CATEGORY", "message": "diagnostic"}
    assert parse_response(packet) == packet


def test_failed_subcall_requires_its_own_error_even_when_later_call_succeeds():
    packet = {
        "version": "0.4",
        "request_id": "r",
        "status": "completed",
        "content": "Cloud answer",
        "invocations": [{"call_id": "local-1", "stage": "local", "status": "failed"}],
    }
    with pytest.raises(ValueError, match="response envelope"):
        parse_response(packet)
    packet["invocations"][0]["error"] = {
        "code": "LOCAL_RESOURCE_EXHAUSTED",
        "message": "Memory guard",
    }
    assert parse_response(packet) == packet


def test_concurrent_packet_parsing_has_no_shared_request_state():
    packets = [request() | {"request_id": f"r-{i}"} for i in range(16)]
    before = deepcopy(packets)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(parse_request, packets))
    results[0]["messages"][0]["parts"][0]["text"] = "changed"
    assert packets == before
    assert results[1:] == before[1:]


def test_legacy_parser_remains_explicitly_v03():
    legacy = {
        "version": "0.3",
        "intent": "chat",
        "messages": [{"role": "user", "parts": [{"type": "text", "text": "hello"}]}],
    }
    assert parse_thinkerql(legacy).messages == legacy["messages"]
    with pytest.raises(ValueError):
        parse_thinkerql(request())
    with pytest.raises(ValueError):
        parse_request(legacy)
