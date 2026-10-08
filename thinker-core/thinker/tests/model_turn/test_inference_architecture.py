"""Task-sized conformance scenarios for the inference architecture."""

import asyncio
import json
import threading

import pytest

from thinker.model_turn.inference_adapters import lift_response
from thinker.model_turn.inference_contract import (
    CapabilityProfile,
    InferencePolicy,
    ThinkingControl,
)
from thinker.model_turn.inference_execution import execute_inference
from thinker.model_turn.inference_plan import lower_request, plan_turn
from thinker.model_turn.models import ModelTurnRequest

SCHEMA = {
    "type": "object",
    "properties": {"answer": {"type": "integer"}},
    "required": ["answer"],
    "additionalProperties": False,
}


def prepare(provider, fake_call, policy=None, cap=None, **updates):
    call = fake_call.model_copy(
        update={
            "call_id": provider + ":test.chat",
            "provider": provider,
            "adapter": provider,
            "model_id": "test-model",
            "caps": ["model_turn_v1", "json_schema", "json_mode"],
        }
    )
    req = ModelTurnRequest(
        request_id="arch-test",
        requested_route=call.call_id,
        system_instruction="frozen instructions",
        messages=[{"role": "user", "content": "frozen corpus"}],
        response_schema=SCHEMA,
        generation={"max_output_tokens": 4096},
        inference_policy=policy or InferencePolicy(expected_model="test-model"),
        **updates,
    )
    profile = cap or CapabilityProfile(
        provider=provider,
        model="test-model",
        structured_output="NATIVE",
        json_mode="NATIVE",
        source="offline fixture",
        effort_values=["low", "high"],
        budget_min=1024,
        budget_max=2048,
        adaptive=True,
        disable_thinking=True,
    )
    return lower_request(req, plan_turn(req, call, profile))


def reply(provider, text='{"answer":7}', finish=None):
    if provider == "openai":
        return {
            "id": "r1",
            "model": "test-model",
            "status": finish or "completed",
            "output": [
                {
                    "type": "reasoning",
                    "summary": [{"text": "thought summary"}],
                    "encrypted_content": "signature",
                },
                {"type": "message", "content": [{"type": "output_text", "text": text}]},
            ],
            "usage": {
                "input_tokens": 10,
                "output_tokens": 8,
                "total_tokens": 18,
                "output_tokens_details": {"reasoning_tokens": 3},
            },
        }
    if provider == "anthropic":
        return {
            "id": "r1",
            "model": "test-model",
            "stop_reason": finish or "end_turn",
            "content": [
                {
                    "type": "thinking",
                    "thinking": "thought summary",
                    "signature": "signature",
                },
                {"type": "text", "text": text},
            ],
            "usage": {"input_tokens": 10, "output_tokens": 8},
        }
    return {
        "responseId": "r1",
        "modelVersion": "test-model",
        "candidates": [
            {
                "finishReason": finish or "STOP",
                "content": {
                    "parts": [
                        {
                            "text": "thought summary",
                            "thought": True,
                            "thoughtSignature": "signature",
                        },
                        {"text": text},
                    ]
                },
            }
        ],
        "usageMetadata": {
            "promptTokenCount": 10,
            "candidatesTokenCount": 5,
            "thoughtsTokenCount": 3,
            "totalTokenCount": 18,
        },
    }


def lift(p, data, status=200):
    return lift_response(p, status, json.dumps(data).encode())


@pytest.mark.parametrize("provider", ["openai", "anthropic", "gemini"])
def test_provider_complete_inference_and_channel_separation(provider, fake_call):
    p = prepare(provider, fake_call)
    body = json.loads(p.body)
    assert body.get("tools", []) == []
    r = lift(p, reply(provider))
    assert r.execution_status == "COMPLETED" and r.structure_status == "VALID"
    assert r.structured_output == {"answer": 7} and r.answer == '{"answer":7}'
    assert r.reasoning_artifacts and r.opaque_artifacts
    assert "thought summary" not in r.model_dump_json() and "signature" not in r.model_dump_json()
    assert r.usage.input_tokens == 10 and r.usage.output_tokens == 8
    assert r.usage.reasoning_tokens == (None if provider == "anthropic" else 3)
    assert r.cost_usd is None


def test_capability_negotiation_and_provider_controls(fake_call):
    controls = {
        "openai": ThinkingControl(mode="effort", effort="high"),
        "anthropic": ThinkingControl(mode="budget", budget_tokens=1024),
        "gemini": ThinkingControl(mode="effort", effort="low"),
    }
    bodies = {
        p: json.loads(
            prepare(p, fake_call, InferencePolicy(expected_model="test-model", thinking=t)).body
        )
        for p, t in controls.items()
    }
    assert bodies["openai"]["reasoning"]["effort"] == "high"
    assert bodies["anthropic"]["thinking"] == {"type": "enabled", "budget_tokens": 1024}
    assert bodies["gemini"]["generationConfig"]["thinkingConfig"]["thinkingLevel"] == "low"
    for provider in controls:
        with pytest.raises(ValueError):
            prepare(provider, fake_call, InferencePolicy(expected_model="wrong"))
        with pytest.raises(ValueError):
            prepare(
                provider,
                fake_call,
                InferencePolicy(
                    expected_model="test-model",
                    thinking=ThinkingControl(mode="effort", effort="unknown"),
                ),
            )
    cap = CapabilityProfile(
        provider="openai",
        model="test-model",
        structured_output="EMULATED",
        json_mode="NATIVE",
        source="fixture",
    )
    with pytest.raises(ValueError):
        prepare("openai", fake_call, cap=cap)
    p = prepare(
        "openai",
        fake_call,
        InferencePolicy(expected_model="test-model", schema_policy="ALLOW_EMULATION"),
        cap,
    )
    assert p.plan.workaround == "JSON_PROMPT" and p.plan.enforcement == "EMULATED"
    assert json.loads(p.body)["text"]["format"] == {"type": "json_object"}


@pytest.mark.parametrize("provider", ["openai", "anthropic", "gemini"])
def test_one_attempt_no_reactive_fallback_with_immutable_audit(provider, fake_call, tmp_path):
    p = prepare(provider, fake_call)
    calls = []

    async def send(url, body, headers, timeout):
        calls.append(body)
        return 400, b'{"error":{"code":"invalid_json_schema"}}'

    r = asyncio.run(
        execute_inference(
            p,
            credentials=lambda _: "test-secret",
            send=send,
            audit_directory=tmp_path / "run",
        )
    )
    assert r.execution_status == "PROVIDER_ERROR" and r.attempt_count == 1 and len(calls) == 1
    assert r.structure_status == "NOT_EVALUATED" and r.usage.input_tokens is None
    assert r.response_sha256 and (tmp_path / "run" / "provider-response.bin").exists()
    with pytest.raises(FileExistsError):
        asyncio.run(
            execute_inference(
                p,
                credentials=lambda _: "test-secret",
                send=send,
                audit_directory=tmp_path / "run",
            )
        )
    assert len(calls) == 1
    assert all(b"test-secret" not in f.read_bytes() for f in (tmp_path / "run").iterdir())


@pytest.mark.parametrize("provider", ["openai", "anthropic", "gemini"])
def test_termination_structure_and_accounting_are_independent(provider, fake_call):
    p = prepare(provider, fake_call)
    stop = {"openai": "incomplete", "anthropic": "max_tokens", "gemini": "MAX_TOKENS"}
    r = lift(p, reply(provider, "{", stop[provider]))
    assert r.execution_status == "INCOMPLETE" and r.structure_status == "NOT_EVALUATED"
    assert r.usage.output_tokens == 8
    r = lift(p, reply(provider, '{"wrong":1}'))
    assert (
        r.execution_status == "COMPLETED"
        and r.structure_status == "INVALID"
        and r.usage.output_tokens == 8
    )
    assert r.structured_output is None
    assert lift(p, reply(provider, '{"answer":1,"answer":2}')).structure_status == "INVALID"
    d = reply(provider)
    if provider == "openai":
        d["output"] = [{"type": "message", "content": [{"type": "refusal", "refusal": "no"}]}]
    elif provider == "anthropic":
        d["stop_reason"] = "refusal"
    else:
        d = {"promptFeedback": {"blockReason": "SAFETY"}}
    assert lift(p, d).execution_status == "REFUSAL"
    d = reply(provider)
    d["model" if provider != "gemini" else "modelVersion"] = "wrong"
    assert lift(p, d).error_code == "MODEL_IDENTITY_MISMATCH"


def test_deadline_cancellation_success_and_unknown_usage(fake_call, tmp_path):
    p = prepare("gemini", fake_call, timeout_ms=30)

    async def slow(*args):
        await asyncio.sleep(1)
        return 200, b"{}"

    r = asyncio.run(
        execute_inference(
            p,
            credentials=lambda _: "secret",
            send=slow,
            audit_directory=tmp_path / "timeout",
        )
    )
    assert (
        r.execution_status == "TIMED_OUT" and r.attempt_count == 1 and r.usage.input_tokens is None
    )
    assert not r.remote_cancellation_confirmed
    assert (tmp_path / "timeout" / "run.json").exists()
    cancel = threading.Event()
    cancel.set()

    async def forbidden(*args):
        raise AssertionError("must not submit")

    r = asyncio.run(
        execute_inference(p, credentials=lambda _: "secret", send=forbidden, cancel_event=cancel)
    )
    assert r.execution_status == "CANCELLED" and r.attempt_count == 0

    async def success(*args):
        return 200, json.dumps(reply("gemini")).encode()

    r = asyncio.run(
        execute_inference(
            p,
            credentials=lambda _: "secret",
            send=success,
            audit_directory=tmp_path / "success",
        )
    )
    assert r.structure_status == "VALID" and r.started_at and r.completed_at and r.deadline_at
    d = reply("gemini")
    d.pop("usageMetadata")
    assert lift(p, d).usage.input_tokens is None


def test_existing_runtime_service_uses_host_profiles_and_preserves_v1(fake_call):
    import httpx

    from thinker.model_turn.runtime import ModelTurnRuntime
    from thinker.model_turn.service import RunningModelTurnServer
    from thinker.registry.store import RegistryStore

    p = prepare("gemini", fake_call)
    call = fake_call.model_copy(
        update={
            "call_id": "gemini:test.chat",
            "provider": "gemini",
            "adapter": "gemini",
            "model_id": "test-model",
            "caps": ["model_turn_v1", "json_schema"],
        }
    )
    store = RegistryStore()
    store.put_call(call)
    store.put_call(fake_call)
    calls = []

    async def send(*args):
        calls.append(args)
        return 200, json.dumps(reply("gemini")).encode()

    rt = ModelTurnRuntime(
        store,
        inference_profiles={call.call_id: p.plan.capabilities},
        inference_sender=send,
        inference_credentials=lambda _: "secret",
        thinker_revision="fixture-revision",
    )
    with RunningModelTurnServer(rt) as server:
        request = json.loads(p.request_json)
        r = httpx.post(server.base_url + "/v1/model-turn", json=request).json()
        assert r["inference_outcome"]["structure_status"] == "VALID"
        assert r["inference_plan"]["revision"] == "fixture-revision"
        assert r["structured_output"] == {"answer": 7} and len(calls) == 1
        request["requested_route"] = "missing"
        r = httpx.post(server.base_url + "/v1/model-turn", json=request).json()
        assert (
            r["inference_outcome"]["execution_status"] == "CONFIGURATION_ERROR" and len(calls) == 1
        )
        legacy = {
            "request_id": "legacy",
            "requested_route": fake_call.call_id,
            "messages": [{"role": "user", "content": "hello"}],
        }
        r = httpx.post(server.base_url + "/v1/model-turn", json=legacy).json()
        assert r["assistant_content"] == "deterministic fake response"


def test_legacy_gemini_thought_text_does_not_enter_final_answer(fake_call):
    from thinker.model_turn.gemini import GeminiModelTurnProvider

    call = fake_call.model_copy(update={"provider": "gemini", "model_id": "test-model"})
    r = GeminiModelTurnProvider()._parse_response(reply("gemini"), call, [])
    assert r.assistant_content == '{"answer":7}'
    assert r.usage.reasoning_tokens == 3


def test_controls_fail_before_network_and_workarounds_are_preselected(fake_call, tmp_path):
    from thinker.model_turn.models import ToolDefinition

    for provider in ("openai", "anthropic", "gemini"):
        with pytest.raises(ValueError):
            prepare(
                provider,
                fake_call,
                tools=[
                    ToolDefinition(name="search", description="x", input_schema={"type": "object"})
                ],
            )
        with pytest.raises(ValueError):
            prepare(
                provider,
                fake_call,
                InferencePolicy(
                    expected_model="test-model",
                    thinking=ThinkingControl(mode="budget", budget_tokens=9999),
                ),
            )
    with pytest.raises(ValueError):
        prepare(
            "gemini",
            fake_call,
            InferencePolicy(expected_model="test-model", thinking=ThinkingControl(mode="adaptive")),
        )
    from thinker.model_turn.inference_plan import check_schema

    with pytest.raises(ValueError):
        check_schema({"$ref": "https://example.org/schema"})
    with pytest.raises(ValueError):
        check_schema({"$id": "https://example.org", "type": "object"})
    p = prepare("openai", fake_call)
    req = ModelTurnRequest.model_validate_json(p.request_json)
    changed = req.model_copy(update={"system_instruction": "changed"})
    with pytest.raises(ValueError):
        lower_request(changed, p.plan)


def test_transport_captures_failures_without_secret_or_usage_loss(fake_call, tmp_path):
    p = prepare("openai", fake_call)
    d = reply("openai", '{"wrong":"credential-sentinel"}')

    async def send(*args):
        return 200, json.dumps(d).encode()

    r = asyncio.run(
        execute_inference(
            p,
            credentials=lambda _: "credential-sentinel",
            send=send,
            audit_directory=tmp_path / "bad",
        )
    )
    assert r.structure_status == "INVALID" and r.usage.output_tokens == 8
    assert r.audit_directory and r.audit_status == "RECORDED"
    for file in (tmp_path / "bad").iterdir():
        assert b"credential-sentinel" not in file.read_bytes()
    assert json.loads((tmp_path / "bad" / "response-capture.json").read_text())[
        "credential_redacted"
    ]
    cancel = threading.Event()

    async def cancel_during(*args):
        cancel.set()
        await asyncio.sleep(1)
        return 200, b"{}"

    r = asyncio.run(
        execute_inference(
            p, credentials=lambda _: "secret", send=cancel_during, cancel_event=cancel
        )
    )
    assert r.execution_status == "CANCELLED" and r.attempt_count == 1

    async def forbidden(*args):
        raise AssertionError("no credentials means no submission")

    r = asyncio.run(execute_inference(p, credentials=lambda _: None, send=forbidden))
    assert r.execution_status == "CONFIGURATION_ERROR" and r.attempt_count == 0


def test_actual_http_edge_uses_one_native_request_and_never_redirects(fake_call):
    import httpx
    import respx

    p = prepare("openai", fake_call)
    with respx.mock(assert_all_called=True) as router:
        route = router.post(p.url).mock(
            return_value=httpx.Response(400, json={"error": {"code": "invalid_json_schema"}})
        )
        r = asyncio.run(execute_inference(p, credentials=lambda _: "secret"))
        assert len(route.calls) == 1 and r.attempt_count == 1
        body = json.loads(route.calls[0].request.content)
        assert body["text"]["format"]["type"] == "json_schema" and body["tools"] == []
        assert route.calls[0].request.headers["Authorization"] == "Bearer secret"
    with respx.mock(assert_all_called=True) as router:
        route = router.post(p.url).mock(
            return_value=httpx.Response(307, headers={"location": "https://example.com"})
        )
        r = asyncio.run(execute_inference(p, credentials=lambda _: "secret"))
        assert len(route.calls) == 1 and r.execution_status == "PROVIDER_ERROR"


def test_partial_response_is_preserved_without_fabricated_structured_answer(fake_call, tmp_path):
    from thinker.model_turn.inference_execution import PartialResponseError

    p = prepare("anthropic", fake_call)

    async def partial(*args):
        raise PartialResponseError(200, b'{"content":[', True)

    r = asyncio.run(
        execute_inference(
            p,
            credentials=lambda _: "secret",
            send=partial,
            audit_directory=tmp_path / "partial",
        )
    )
    assert r.execution_status == "TIMED_OUT" and r.structure_status == "NOT_EVALUATED"
    assert r.structured_output is None and r.usage.input_tokens is None
    assert (tmp_path / "partial" / "provider-response.partial.bin").read_bytes() == b'{"content":['
