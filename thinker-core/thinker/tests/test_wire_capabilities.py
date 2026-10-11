"""Token-limit route capability and portable reasoning (mirrors thinker-go
wire_capability_test.go; Python/Go agreement is checked by tools/check_go_parity.py)."""

import pytest

from thinker.tests.test_modelcalls import packet, route
from thinker.thinkerql import parse_request
from thinker.thinkerql.wire import UnsupportedRequest, prepare


def body_for(protocol, reasoning=None, **route_changes):
    request = packet(instructions={"max_output_tokens": 4096})
    if reasoning is not None:
        request["routing"] = {"reasoning": reasoning}
    return prepare(parse_request(request), route(protocol, **route_changes))[2]


def test_chat_token_limit_field_emits_max_completion_tokens_only():
    body = body_for("chat", token_limit_field="max_completion_tokens")
    assert body["max_completion_tokens"] == 4096
    assert "max_tokens" not in body


def test_default_token_limit_fields_unchanged():
    assert body_for("chat")["max_tokens"] == 4096
    assert body_for("responses")["max_output_tokens"] == 4096


@pytest.mark.parametrize("key", ["max_tokens", "max_completion_tokens", "max_output_tokens"])
@pytest.mark.parametrize("protocol", ["chat", "responses"])
def test_provider_options_cannot_override_token_limit(protocol, key):
    request = packet(provider_options={key: 100})
    with pytest.raises(UnsupportedRequest, match="cannot override the token limit"):
        prepare(parse_request(request), route(protocol))


@pytest.mark.parametrize(
    "protocol, field, message",
    [
        ("responses", "max_completion_tokens", "only verified for Chat Completions"),
        ("chat", "max_new_tokens", "unsupported route token_limit_field"),
        ("anthropic", "max_completion_tokens", "not verified for this protocol"),
        ("gemini", "max_completion_tokens", "not verified for this protocol"),
    ],
)
def test_token_limit_field_rejected_where_unverified(protocol, field, message):
    with pytest.raises(UnsupportedRequest, match=message):
        body_for(protocol, token_limit_field=field)


def test_portable_effort_none_maps_per_protocol():
    chat = body_for("chat", {"effort": "none"})
    assert chat["reasoning_effort"] == "none" and "reasoning" not in chat
    responses = body_for("responses", {"effort": "none"})
    assert responses["reasoning"] == {"effort": "none"} and "reasoning_effort" not in responses
    assert body_for("anthropic", {"effort": "none"})["thinking"] == {"type": "disabled"}
    gemini = body_for("gemini", {"effort": "none"})
    assert gemini["generationConfig"]["thinkingConfig"] == {"thinkingBudget": 0}


@pytest.mark.parametrize("protocol", ["anthropic", "gemini"])
def test_portable_effort_other_than_none_needs_native_budget(protocol):
    with pytest.raises(UnsupportedRequest, match="only effort none is portable"):
        body_for(protocol, {"effort": "high"})


def test_chat_maps_any_portable_effort():
    assert body_for("chat", {"effort": "high"})["reasoning_effort"] == "high"


@pytest.mark.parametrize(
    "protocol, native, field",
    [
        ("chat", {"effort": "low", "summary": "auto"}, lambda b: b["reasoning"]),
        ("responses", {"effort": "low", "summary": "auto"}, lambda b: b["reasoning"]),
        ("anthropic", {"type": "enabled", "budget_tokens": 2048}, lambda b: b["thinking"]),
        ("gemini", {"thinkingBudget": 512}, lambda b: b["generationConfig"]["thinkingConfig"]),
    ],
)
def test_native_reasoning_passes_through_unchanged(protocol, native, field):
    body = body_for(protocol, native)
    assert field(body) == native
    assert "reasoning_effort" not in body


def test_portable_effort_conflicts_with_native_option():
    request = packet(routing={"reasoning": {"effort": "none"}}, provider_options={"reasoning_effort": "low"})
    with pytest.raises(UnsupportedRequest, match="Duplicate common and native provider settings"):
        prepare(parse_request(request), route("chat"))
