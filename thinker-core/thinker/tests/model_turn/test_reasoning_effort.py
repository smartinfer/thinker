"""Reasoning-effort seam (V1): canonical field, OpenAI Responses mapping,
capability gate, provenance, continuation. Deterministic (no network)."""
from __future__ import annotations

from threading import Event

import pytest

from thinker.model_turn.errors import ModelTurnErrorCode, ModelTurnProviderException
from thinker.model_turn.models import (
    GenerationParameters,
    ModelMessage,
    ModelTurnRequest,
    ReasoningEffort,
)
from thinker.model_turn.openai import OpenAIModelTurnProvider
from thinker.model_turn.runtime import ModelTurnRuntime
from thinker.registry.schema import Limits, Price, RegistryCall
from thinker.registry.store import RegistryStore


def _openai_call(*, caps, reasoning_effort=None) -> RegistryCall:
    return RegistryCall(
        call_id="openai:gpt-5-1.chat", provider="openai", model_id="gpt-5.1",
        kind="chat", modality="text", caps=caps,
        limits=Limits(max_input_tokens=400000, max_output_tokens=128000),
        price=Price(input_per_1k=0.0, output_per_1k=0.0),
        adapter="openai", payload_style="chat_completions_v1",
        reasoning_effort=reasoning_effort,
    )


CAPS = ["model_turn_v1", "tools", "reasoning_effort"]


def _req(**gen) -> ModelTurnRequest:
    return ModelTurnRequest(
        request_id="r", requested_route="openai:gpt-5-1.chat",
        messages=(ModelMessage(role="user", content="hi"),),
        generation=GenerationParameters(**gen), timeout_ms=1000,
    )


def test_canonical_request_accepts_high():
    assert GenerationParameters(reasoning_effort="high").reasoning_effort is ReasoningEffort.HIGH


def test_invalid_reasoning_effort_rejected():
    with pytest.raises(Exception):
        GenerationParameters(reasoning_effort="ultra")


def test_openai_payload_emits_reasoning_effort_from_request():
    p = OpenAIModelTurnProvider(credential_resolver=lambda _: "x")
    payload = p._payload(_req(reasoning_effort=ReasoningEffort.HIGH), _openai_call(caps=CAPS))
    assert payload["reasoning"] == {"effort": "high"}


def test_openai_payload_uses_route_default_when_request_absent():
    p = OpenAIModelTurnProvider(credential_resolver=lambda _: "x")
    payload = p._payload(_req(), _openai_call(caps=CAPS, reasoning_effort="high"))
    assert payload["reasoning"] == {"effort": "high"}


def test_openai_payload_absent_preserves_prior_behavior():
    p = OpenAIModelTurnProvider(credential_resolver=lambda _: "x")
    payload = p._payload(_req(temperature=0.4), _openai_call(caps=CAPS))
    assert "reasoning" not in payload
    assert payload["temperature"] == 0.4  # untouched when effort absent


def test_reasoning_effort_with_temperature_fails_closed():
    p = OpenAIModelTurnProvider(credential_resolver=lambda _: "x")
    with pytest.raises(ModelTurnProviderException) as ei:
        p._payload(_req(reasoning_effort=ReasoningEffort.HIGH, temperature=0.4), _openai_call(caps=CAPS))
    assert ei.value.error.code == ModelTurnErrorCode.MODEL_UNAVAILABLE


def _fake_call(*, caps, reasoning_effort=None) -> RegistryCall:
    return RegistryCall(
        call_id="fake:deterministic.chat", provider="fake", model_id="deterministic-v1",
        kind="chat", modality="text", caps=caps,
        limits=Limits(max_input_tokens=8192, max_output_tokens=4096),
        price=Price(input_per_1k=0.0, output_per_1k=0.0),
        adapter="fake", payload_style="model_turn_v1_fake", reasoning_effort=reasoning_effort,
    )


def _runtime(call: RegistryCall) -> ModelTurnRuntime:
    store = RegistryStore()
    store.put_call(call)
    return ModelTurnRuntime(store, thinker_revision="test")


def _fake_req(effort=None) -> ModelTurnRequest:
    gen = GenerationParameters(reasoning_effort=effort) if effort else GenerationParameters()
    return ModelTurnRequest(
        request_id="r", requested_route="fake:deterministic.chat",
        messages=(ModelMessage(role="user", content="hi"),),
        metadata={"fake_scenario": "plain_text"}, generation=gen, timeout_ms=1000,
    )


def test_capability_gate_rejects_effort_on_unsupported_route():
    # Route WITHOUT the reasoning_effort cap + explicit effort => fail closed.
    rt = _runtime(_fake_call(caps=["model_turn_v1"]))
    resp = rt.turn(_fake_req(effort=ReasoningEffort.HIGH))
    assert resp.normalized_error is not None
    assert resp.normalized_error.code == ModelTurnErrorCode.MODEL_UNAVAILABLE
    assert "reasoning_effort" in resp.normalized_error.message


def test_provenance_reports_effort_and_continuation_preserves_it():
    rt = _runtime(_fake_call(caps=["model_turn_v1", "reasoning_effort"]))
    r1 = rt.turn(_fake_req(effort=ReasoningEffort.HIGH))
    assert r1.normalized_error is None
    assert r1.reasoning_effort is ReasoningEffort.HIGH  # provenance unambiguous
    # A later turn configured high stays high (effort does not disappear).
    r2 = rt.turn(ModelTurnRequest(
        request_id="r2", requested_route="fake:deterministic.chat",
        messages=(ModelMessage(role="user", content="again"),),
        metadata={"fake_scenario": "plain_text"},
        generation=GenerationParameters(reasoning_effort=ReasoningEffort.HIGH), timeout_ms=1000))
    assert r2.reasoning_effort is ReasoningEffort.HIGH


def test_route_default_effort_reported_in_provenance():
    # No request effort, route default high, cap present => honored + reported.
    rt = _runtime(_fake_call(caps=["model_turn_v1", "reasoning_effort"], reasoning_effort="high"))
    r = rt.turn(_fake_req())
    assert r.normalized_error is None
    assert r.reasoning_effort is ReasoningEffort.HIGH


def test_absent_effort_reported_as_none():
    rt = _runtime(_fake_call(caps=["model_turn_v1"]))
    r = rt.turn(_fake_req())
    assert r.normalized_error is None
    assert r.reasoning_effort is None  # "default" distinct from "high"
