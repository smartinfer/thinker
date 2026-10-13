"""Task-level qualification scenarios: no provider calls or credentials."""

import copy
import json
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import pytest

from thinker.core import Thinker
from thinker.pricebook import PriceBook
from thinker.qualification import describe_target, require_pass, run_suite
from thinker.registry.schema import Limits, Price, RegistryCall
from thinker.registry.store import RegistryStore


def setup(tmp_path):
    import sys

    from thinker.qualification import file_hash

    validator = tmp_path / "validator.py"
    validator.write_text(
        'import json,sys\nx=json.load(sys.stdin)\nprint(json.dumps({"status":x["output"]["decision"],"diagnostics":[]}))\n'
    )
    ql = {
        "version": "0.3",
        "intent": "chat",
        "messages": [{"role": "user", "parts": [{"type": "text", "text": "Frozen input"}]}],
        "instructions": {
            "max_output_tokens": 32,
            "json_schema": {
                "type": "object",
                "properties": {"decision": {"type": "string", "enum": ["PASS", "FAIL", "REVIEW"]}},
                "required": ["decision"],
                "additionalProperties": False,
            },
        },
    }
    profile = {
        "version": "thinker.qualification.v1",
        "profile_id": "test-contract",
        "contract_version": "v1",
        "provider": "openai",
        "repeats": 2,
        "requirements": ["native_schema", "no_tools", "single_attempt", "reported_model"],
        "cases": [{"case_id": "case-1", "ql": ql, "context": {}}],
        "validator": {"command": [sys.executable, str(validator)], "timeout_seconds": 5},
        "artifacts": {
            str(validator): file_hash(validator),
            str(Path(sys.executable).resolve()): file_hash(Path(sys.executable).resolve()),
        },
    }
    p = tmp_path / "profile.json"
    p.write_text(json.dumps(profile))
    store = RegistryStore()
    call = RegistryCall(
        call_id="openai:test.chat",
        provider="openai",
        model_id="test",
        kind="chat",
        modality="text",
        caps=["json_mode", "structured_output"],
        limits=Limits(max_input_tokens=10000, max_output_tokens=1000),
        price=Price(input_per_1k=0, output_per_1k=0),
        adapter="openai",
        payload_style="model_turn_v1",
    )
    store.put_call(call)
    thinker = Thinker(
        store,
        PriceBook(),
        qualification_profile=p,
        qualification_root=tmp_path / "records",
        qualification_config={
            "capability_profile": {
                "provider": "openai",
                "model": "test",
                "source": "test fixture",
                "structured_output": "NATIVE",
            }
        },
    )
    return thinker, call, p, ql


def outcome(decision="PASS"):
    from thinker.model_turn.inference_contract import InferenceOutcome, UsageReport

    return InferenceOutcome(
        execution_status="COMPLETED",
        structure_status="VALID",
        answer=json.dumps({"decision": decision}),
        structured_output={"decision": decision},
        resolved_model="test",
        attempt_count=1,
        audit_status="RECORDED",
        usage=UsageReport(input_tokens=3, output_tokens=4),
    )


@contextmanager
def provider(decision="PASS"):
    async def fake(*args, **kwargs):
        return (
            200,
            json.dumps(
                {
                    "status": "completed",
                    "model": "test",
                    "output": [
                        {
                            "type": "message",
                            "content": [
                                {"type": "output_text", "text": json.dumps({"decision": decision})}
                            ],
                        }
                    ],
                    "usage": {"input_tokens": 3, "output_tokens": 4, "total_tokens": 7},
                }
            ).encode(),
        )

    with (
        patch("thinker.adapters.planned.resolve_credential", return_value="fixture-secret"),
        patch("thinker.model_turn.inference_execution.send_http", side_effect=fake) as transport,
    ):
        yield transport


def test_gate_blocks_before_adapter_and_replay_never_qualifies(tmp_path):
    thinker, call, _p, ql = setup(tmp_path)
    request = {**ql, "routing": {"alias": "frozen"}}
    call.aliases = ["frozen"]
    with patch("thinker.core.get_adapter") as adapter:
        with pytest.raises(ValueError, match="NOT_QUALIFIED"):
            thinker.chat_ql(request)
        adapter.assert_not_called()
    report = run_suite(
        thinker,
        call.call_id,
        mode="REPLAY",
        fixtures={
            "case-1/1": outcome().model_dump(mode="json"),
            "case-1/2": outcome().model_dump(mode="json"),
        },
    )
    assert report["status"] == "OFFLINE_PASS"
    with pytest.raises(ValueError, match="NOT_QUALIFIED"):
        require_pass(thinker, call)
    # Request JSON cannot authorize a probe.
    with pytest.raises(ValueError):
        thinker.chat_ql({**request, "qualification_probe": True})


def test_authorized_probe_same_path_record_and_invalidation(tmp_path):
    thinker, call, _p, ql = setup(tmp_path)
    target = describe_target(thinker, call)
    with pytest.raises(ValueError, match="AUTHORIZATION"):
        run_suite(thinker, call.call_id, mode="LIVE_PROBE")
    with provider() as transport:
        report = run_suite(thinker, call.call_id, mode="LIVE_PROBE", authorize_target=target["key"])
        assert transport.call_count == 2
        assert report["status"] == "PASS"
        assert require_pass(thinker, call)["key"] == target["key"]
        response = thinker.chat_ql({**ql, "routing": {"call_id": call.call_id}})
        assert response.qualification["application_status"] == "PASS"
        assert transport.call_count == 3
        # Payload changes are not covered by this frozen application profile.
        changed = copy.deepcopy(ql)
        changed["messages"][0]["parts"][0]["text"] = "Different contract"
        with pytest.raises(ValueError, match="CONTRACT_REQUEST_MISMATCH"):
            thinker.chat_ql({**changed, "routing": {"call_id": call.call_id}})
        assert transport.call_count == 3
    for field, new in [
        ("model_id", "other"),
        ("endpoint", "https://example.invalid"),
        ("payload_style", "chat_completions_v1"),
    ]:
        old = getattr(call, field)
        setattr(call, field, new)
        with pytest.raises(ValueError):
            require_pass(thinker, call)
        setattr(call, field, old)
    old = copy.deepcopy(thinker.qualification_config)
    thinker.qualification_config["capability_profile"]["source"] = "changed profile"
    with pytest.raises(ValueError):
        require_pass(thinker, call)
    thinker.qualification_config = old
    path = thinker.qualification_root / "records" / (report["record_id"] + ".json")
    data = json.loads(path.read_text())
    data["slots"][0]["application_status"] = "FAIL"
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        require_pass(thinker, call)


@pytest.mark.parametrize("decision,status", [("FAIL", "FAIL"), ("REVIEW", "REVIEW_REQUIRED")])
def test_external_validator_controls_admission_and_latest_probe_revokes(tmp_path, decision, status):
    thinker, call, _p, _ql = setup(tmp_path)
    key = describe_target(thinker, call)["key"]
    with provider():
        assert (
            run_suite(thinker, call.call_id, mode="LIVE_PROBE", authorize_target=key)["status"]
            == "PASS"
        )
    with provider(decision):
        result = run_suite(thinker, call.call_id, mode="LIVE_PROBE", authorize_target=key)
    assert result["status"] == status
    with pytest.raises(ValueError):
        require_pass(thinker, call)


def test_structural_failure_validator_failure_and_drift_stay_visible(tmp_path):
    thinker, call, _p, _ql = setup(tmp_path)
    bad = outcome().model_dump(mode="json")
    bad["answer"] = "not json"
    report = run_suite(thinker, call.call_id, mode="REPLAY", fixtures={"case-1/1": bad})
    assert report["status"] == "FAIL" and len(report["slots"]) == 2
    assert report["slots"][0]["structural_status"] == "INVALID"
    assert report["slots"][1]["error"] == "MISSING_FIXTURE"
    (tmp_path / "validator.py").write_text('raise RuntimeError("changed")')
    with pytest.raises(ValueError, match="ARTIFACT_DRIFT"):
        describe_target(thinker, call)


def test_unconfigured_remote_and_legacy_native_downgrade_blocked(tmp_path):
    thinker, call, _p, ql = setup(tmp_path)
    with patch("thinker.core.get_adapter") as adapter:
        with pytest.raises(ValueError, match="NOT_QUALIFIED"):
            Thinker(thinker.store, thinker.pricebook).chat_ql(
                {**ql, "routing": {"call_id": call.call_id}}
            )
        adapter.assert_not_called()
    call.payload_style = "chat_completions_v1"
    with pytest.raises(ValueError, match="CAPABILITY"):
        run_suite(thinker, call.call_id, mode="LIVE_PROBE", authorize_target="irrelevant")


def test_content_addressed_history_and_exact_live_probe_links(tmp_path):
    from thinker.qualification_store import digest, execution, history

    thinker, call, _p, _ql = setup(tmp_path)
    key = describe_target(thinker, call)["key"]
    with provider():
        first = run_suite(thinker, call.call_id, mode="LIVE_PROBE", authorize_target=key)
    old = {str(p): p.read_bytes() for p in thinker.qualification_root.rglob("*") if p.is_file()}
    with provider("FAIL"):
        second = run_suite(thinker, call.call_id, mode="LIVE_PROBE", authorize_target=key)
    assert first["record_id"] != second["record_id"]
    assert second["previous_record"] == first["record_id"]
    assert [(r["status"]) for _, r in history(thinker.qualification_root, key)] == ["PASS", "FAIL"]
    for path, data in old.items():
        assert Path(path).read_bytes() == data
    for report in (first, second):
        raw = (
            thinker.qualification_root / "records" / (report["record_id"] + ".json")
        ).read_bytes()
        assert digest(raw) == report["record_id"]
        assert len({s["execution_ref"] for s in report["slots"]}) == 2
        for slot in report["slots"]:
            result = execution(thinker.qualification_root, slot["execution_ref"])
            assert result["mode"] == "LIVE_PROBE" and result["repeat"] == slot["repeat"]
    with pytest.raises(ValueError):
        require_pass(thinker, call)


def test_validator_timeout_malformed_and_configuration_drift(tmp_path):
    from thinker.qualification_store import file_hash

    thinker, call, p, _ql = setup(tmp_path)
    validator = tmp_path / "validator.py"
    for source, error in [
        ("import time; time.sleep(5)", "VALIDATOR_TIMED_OUT"),
        ('print("not-json")', None),
    ]:
        validator.write_text(source)
        data = json.loads(p.read_text())
        data["validator"]["timeout_seconds"] = 1
        data["artifacts"][str(validator)] = file_hash(validator)
        p.write_text(json.dumps(data))
        result = run_suite(
            thinker,
            call.call_id,
            mode="REPLAY",
            fixtures={"case-1/1": outcome().model_dump(mode="json")},
        )
        assert result["slots"][0]["application_status"] == "VALIDATOR_ERROR"
        assert result["status"] == "FAIL"
        if error:
            assert result["slots"][0]["error"] == error
    # A mutated profile during collection halts subsequent submissions and still records slots.
    validator.write_text(
        'import json,sys; x=json.load(sys.stdin); print(json.dumps({"status":"PASS","diagnostics":[]}))'
    )
    data = json.loads(p.read_text())
    data["artifacts"][str(validator)] = file_hash(validator)
    p.write_text(json.dumps(data))
    key = describe_target(thinker, call)["key"]

    async def change(*a, **kw):
        validator.write_text("# changed after submission")
        return (
            200,
            json.dumps(
                {
                    "status": "completed",
                    "model": "test",
                    "output": [
                        {
                            "type": "message",
                            "content": [{"type": "output_text", "text": '{"decision":"PASS"}'}],
                        }
                    ],
                }
            ).encode(),
        )

    with (
        patch("thinker.adapters.planned.resolve_credential", return_value="fixture"),
        patch("thinker.model_turn.inference_execution.send_http", side_effect=change) as send,
    ):
        result = run_suite(thinker, call.call_id, mode="LIVE_PROBE", authorize_target=key)
    assert send.call_count == 1 and len(result["slots"]) == 2 and result["status"] == "FAIL"
    assert result["slots"][1]["error"] == "NOT_EXECUTED_CONTRACT_DRIFT"


def test_standalone_default_runtime_cannot_bypass_qualification(tmp_path):
    from thinker.model_turn.models import ModelMessage, ModelTurnRequest
    from thinker.model_turn.runtime import ModelTurnRuntime

    thinker, call, _p, _ql = setup(tmp_path)
    call.caps.append("model_turn_v1")
    request = ModelTurnRequest(
        request_id="bypass",
        requested_route=call.call_id,
        messages=(ModelMessage(role="user", content="no bypass"),),
    )
    runtime = ModelTurnRuntime(thinker.store)
    with patch.object(runtime, "_provider") as resolve_provider:
        result = runtime.turn(request)
        assert "QUALIFICATION_REQUIRED" in result.normalized_error.message
        resolve_provider.assert_not_called()
    from thinker.model_turn.inference_contract import InferencePolicy

    request = request.model_copy(
        update={"inference_policy": InferencePolicy(expected_model=call.model_id)}
    )
    with patch("thinker.model_turn.inference_runtime.planned_turn") as send:
        result = runtime.turn(request)
        assert "QUALIFICATION_REQUIRED" in result.normalized_error.message
        send.assert_not_called()


def test_qualification_cli_inspect_and_replay_never_grant_live(tmp_path, capsys):
    import yaml

    from thinker.cli import main

    thinker, call, p, _ql = setup(tmp_path)
    registry = tmp_path / "registry.yaml"
    registry.write_text(yaml.safe_dump({"version": "1", "calls": [call.model_dump(mode="json")]}))
    config = tmp_path / "config.json"
    config.write_text(json.dumps(thinker.qualification_config))
    args = [
        "thinker",
        "qualify",
        "inspect",
        "--registry",
        str(registry),
        "--profile",
        str(p),
        "--records",
        str(thinker.qualification_root),
        "--configuration",
        str(config),
        "--call",
        call.call_id,
    ]
    with patch("sys.argv", args):
        main()
    inspected = json.loads(capsys.readouterr().out)
    assert inspected["key"] == describe_target(thinker, call)["key"]
    fixtures = tmp_path / "fixtures.json"
    fixtures.write_text(
        json.dumps(
            {
                "case-1/1": outcome().model_dump(mode="json"),
                "case-1/2": outcome().model_dump(mode="json"),
            }
        )
    )
    args[2] = "replay"
    args += ["--fixtures", str(fixtures)]
    with patch("sys.argv", args):
        main()
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "OFFLINE_PASS"
    with pytest.raises(ValueError):
        require_pass(thinker, call)
