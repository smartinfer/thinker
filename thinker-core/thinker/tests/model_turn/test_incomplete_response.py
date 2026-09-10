"""Incomplete provider-response normalization (V1): a valid but non-actionable
response (status=incomplete, no content) becomes a typed incomplete outcome with
usage preserved — never a handler/validator crash, never MalformedResponse."""
from __future__ import annotations

from threading import Event

import httpx
import respx

from thinker.model_turn.errors import ModelTurnErrorCode
from thinker.model_turn.models import (
    GenerationParameters, ModelMessage, ModelTurnRequest, ReasoningEffort,
)
from thinker.model_turn.openai import OpenAIModelTurnProvider
from thinker.model_turn.provider import ProviderTurnResult
from thinker.model_turn.models import Usage
from thinker.model_turn.runtime import ModelTurnRuntime
from thinker.registry.schema import Limits, Price, RegistryCall
from thinker.registry.store import RegistryStore


def _call(*, caps, adapter="openai", reasoning_effort=None, max_output_tokens=None, call_id="openai:gpt-5-1.chat") -> RegistryCall:
    return RegistryCall(
        call_id=call_id, provider="openai", model_id="gpt-5.1", kind="chat", modality="text",
        caps=caps, limits=Limits(max_input_tokens=400000, max_output_tokens=128000),
        price=Price(input_per_1k=0.0, output_per_1k=0.0), adapter=adapter,
        payload_style="chat_completions_v1", reasoning_effort=reasoning_effort, max_output_tokens=max_output_tokens,
    )


def _req(route="openai:gpt-5-1.chat", **gen) -> ModelTurnRequest:
    return ModelTurnRequest(request_id="r", requested_route=route,
        messages=(ModelMessage(role="user", content="hi"),),
        generation=GenerationParameters(**gen), timeout_ms=1000)


CAPS = ["model_turn_v1", "tools", "reasoning_effort"]


def test_payload_applies_route_output_floor():
    p = OpenAIModelTurnProvider(credential_resolver=lambda _: "x")
    # request asks for a small budget; the route floor raises it.
    pay = p._payload(_req(max_output_tokens=4096), _call(caps=CAPS, max_output_tokens=32768))
    assert pay["max_output_tokens"] == 32768
    # a larger request is not lowered by the floor.
    pay2 = p._payload(_req(max_output_tokens=40000), _call(caps=CAPS, max_output_tokens=32768))
    assert pay2["max_output_tokens"] == 40000
    # no route floor => request value unchanged.
    pay3 = p._payload(_req(max_output_tokens=4096), _call(caps=CAPS))
    assert pay3["max_output_tokens"] == 4096


@respx.mock
def test_openai_incomplete_max_output_parses_as_typed_incomplete():
    respx.post("https://api.openai.com/v1/responses").mock(return_value=httpx.Response(200, json={
        "id": "resp_x", "model": "gpt-5.1", "status": "incomplete",
        "incomplete_details": {"reason": "max_output_tokens"},
        "output": [{"type": "reasoning", "summary": []}],
        "usage": {"input_tokens": 500, "output_tokens": 32768, "total_tokens": 33268,
                  "output_tokens_details": {"reasoning_tokens": 32768}},
    }))
    p = OpenAIModelTurnProvider(credential_resolver=lambda _: "x")
    res = p.turn(_req(), _call(caps=CAPS), Event())
    assert res.incomplete_reason == "max_output_tokens"
    assert res.assistant_content is None and not res.tool_calls
    assert res.usage.input_tokens == 500 and res.usage.output_tokens == 32768  # usage survives


class _IncompleteProvider:
    def __init__(self, reason="max_output_tokens"):
        self.reason = reason

    def turn(self, request, call, cancel_event):
        return ProviderTurnResult(resolved_provider="openai", resolved_model="gpt-5.1",
            usage=Usage(input_tokens=500, output_tokens=32768, total_tokens=33268),
            finish_reason=self.reason, incomplete_reason=self.reason)


def _incomplete_runtime(reason="max_output_tokens") -> ModelTurnRuntime:
    call = _call(caps=CAPS, adapter="inc", reasoning_effort="high", call_id="openai:gpt-5-1.chat")
    store = RegistryStore(); store.put_call(call)
    records: list[dict] = []
    rt = ModelTurnRuntime(store, thinker_revision="t", observer=records.append,
        provider_factories={"inc": lambda c: _IncompleteProvider(reason)})
    rt.observed = records  # type: ignore[attr-defined]
    return rt


def test_runtime_incomplete_is_typed_usage_preserved_not_malformed():
    rt = _incomplete_runtime()
    resp = rt.turn(_req())
    assert resp.normalized_error is not None
    assert resp.normalized_error.code == ModelTurnErrorCode.INCOMPLETE
    assert resp.normalized_error.code != ModelTurnErrorCode.MALFORMED_RESPONSE
    assert "max_output_tokens" in resp.normalized_error.message
    assert resp.usage.input_tokens == 500 and resp.usage.output_tokens == 32768  # usage preserved
    assert rt.observed[-1]["reasoning_effort"] == "high"  # provenance preserved (server-side, off wire)


def test_runtime_incomplete_other_reason_preserved_generically():
    resp = _incomplete_runtime(reason="content_filter").turn(_req())
    assert resp.normalized_error is not None
    assert resp.normalized_error.code == ModelTurnErrorCode.INCOMPLETE
    assert "content_filter" in resp.normalized_error.message
