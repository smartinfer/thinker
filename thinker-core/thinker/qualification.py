"""Generic frozen-contract qualification on the existing ThinkerQL execution path.
No financial concepts, application imports, model prompts or semantic heuristics.
"""

from __future__ import annotations

import copy
import importlib.metadata
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Literal

import jsonschema
from pydantic import BaseModel, ConfigDict, Field

from .qualification_external import invoke_validator
from .qualification_store import (
    append_decision,
    begin_execution,
    digest,
    encode,
    execution,
    file_hash,
    history,
    now,
    seal_execution,
    strict_json,
    write_new,
)


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Validator(ContractModel):
    command: tuple[str, ...] = Field(min_length=2)
    timeout_seconds: int = Field(ge=1, le=120)


class Case(ContractModel):
    case_id: str = Field(min_length=1)
    ql: dict
    context: dict


class Profile(ContractModel):
    version: Literal["thinker.qualification.v1"]
    profile_id: str = Field(min_length=1)
    contract_version: str = Field(min_length=1)
    provider: str
    repeats: int = Field(ge=1, le=100)
    requirements: tuple[
        Literal["native_schema", "no_tools", "single_attempt", "reported_model"], ...
    ]
    cases: tuple[Case, ...] = Field(min_length=1)
    validator: Validator
    artifacts: dict[str, str] = Field(min_length=1)
    validity_seconds: int = Field(default=86400, ge=60, le=604800)


def load_profile(path):
    profile = Profile.model_validate(strict_json(Path(path).read_bytes()))
    for filename, checksum in profile.artifacts.items():
        if not Path(filename).is_absolute() or file_hash(filename) != checksum:
            raise ValueError("CONTRACT_ARTIFACT_DRIFT: " + filename)
    # Explicit executable and entry script are both bound; application owns transitive manifest.
    for arg in profile.validator.command[:2]:
        resolved = str(Path(arg).resolve())
        if resolved not in profile.artifacts:
            raise ValueError("UNBOUND_VALIDATOR_COMMAND")
    ids = [case.case_id for case in profile.cases]
    if len(ids) != len(set(ids)):
        raise ValueError("DUPLICATE_CASE_ID")
    from .thinkerql.parse import parse_thinkerql

    for case in profile.cases:
        if "routing" in case.ql or set(case.ql) - {"version", "intent", "messages", "instructions"}:
            raise ValueError("CONTRACT_REQUEST_FIELDS")
        parsed = parse_thinkerql(case.ql)
        if parsed.intent != "chat" or parsed.need_modality != "text":
            raise ValueError("CONTRACT_TEXT_CHAT_REQUIRED")
        schema = parsed.instructions.get("json_schema")
        if not schema:
            raise ValueError("CONTRACT_SCHEMA_REQUIRED")
        from .model_turn.inference_plan import check_schema

        check_schema(schema)
    return profile


def describe_target(thinker, call):
    if thinker.qualification_profile is None or thinker.qualification_root is None:
        raise ValueError("NOT_QUALIFIED: host profile and evidence store required")
    profile = load_profile(thinker.qualification_profile)
    if profile.provider != call.provider:
        raise ValueError("CONTRACT_PROVIDER_MISMATCH")
    source = Path(__file__).parent
    code = {
        str(p.relative_to(source)): file_hash(p)
        for p in sorted(source.rglob("*"))
        if p.suffix in {".py", ".json"} and "tests" not in p.parts and "__pycache__" not in p.parts
    }
    target = {
        "profile_id": profile.profile_id,
        "contract_version": profile.contract_version,
        "contract_sha256": digest(profile.model_dump(mode="json")),
        "provider": call.provider,
        "model": call.model_id,
        "adapter": call.adapter,
        "registry_call": call.model_dump(mode="json"),
        "adapter_configuration": copy.deepcopy(thinker.qualification_config),
        "implementation_sha256": digest(code),
        "runtime": {
            "python": sys.version,
            "packages": {
                n: importlib.metadata.version(n) for n in ("pydantic", "jsonschema", "httpx")
            },
        },
    }
    return {**target, "key": digest(target)}


def preflight(thinker, call):
    target = describe_target(thinker, call)
    profile = load_profile(thinker.qualification_profile)
    # The legacy Chat Completions adapter offers JSON mode, not native schema/no-retry
    # qualification evidence. Reuse the native implementation via a thin QL adapter.
    if (
        call.payload_style != "model_turn_v1"
        or call.provider not in {"openai", "anthropic", "gemini"}
        or call.adapter != call.provider
        or call.kind != "chat"
    ):
        raise ValueError("QUALIFICATION_CAPABILITY_UNSUPPORTED")
    from .adapters.planned import prepare_request

    for case in profile.cases:
        from .thinkerql.parse import parse_thinkerql

        prepare_request(parse_thinkerql(case.ql), call, thinker.qualification_config)
    return target


def decide_slots(slots, mode):
    if any(
        s["execution_status"] != "COMPLETED"
        or s["structural_status"] != "VALID"
        or s["application_status"] not in {"PASS", "REVIEW"}
        for s in slots
    ):
        return "FAIL"
    if any(s["application_status"] == "REVIEW" for s in slots):
        return "REVIEW_REQUIRED"
    return "PASS" if mode == "LIVE_PROBE" else "OFFLINE_PASS"


def require_pass(thinker, call):
    target = describe_target(thinker, call)
    records = history(thinker.qualification_root, target["key"])
    if not records:
        raise ValueError("NOT_QUALIFIED: no live qualification record for exact target")
    key, record = records[-1]
    from .qualification_state import assert_applicable

    assert_applicable(thinker.qualification_root, target, record)
    profile = load_profile(thinker.qualification_profile)
    expected = [(c.case_id, n) for c in profile.cases for n in range(1, profile.repeats + 1)]
    actual = [(s["case_id"], s["repeat"]) for s in record["slots"]]
    if actual != expected or record["target"] != target or record["status"] != "PASS":
        raise ValueError("NOT_QUALIFIED: current record did not pass")
    verified = []
    for slot in record["slots"]:
        evidence = execution(thinker.qualification_root, slot["execution_ref"])
        if evidence["target"] != target or evidence["mode"] != "LIVE_PROBE":
            raise ValueError("NOT_QUALIFIED: mismatched probe evidence")
        if any(
            slot[k] != evidence[k]
            for k in (
                "case_id",
                "repeat",
                "execution_status",
                "structural_status",
                "application_status",
            )
        ):
            raise ValueError("NOT_QUALIFIED: decision/evidence mismatch")
        verified.append(evidence)
    if decide_slots(verified, "LIVE_PROBE") != "PASS":
        raise ValueError("NOT_QUALIFIED: evidence does not pass")
    return {**target, "record_id": key}


def match_case(profile, ql):
    body = {k: v for k, v in ql.items() if k != "routing"}
    for case in profile.cases:
        if body == case.ql:
            return case
    raise ValueError("CONTRACT_REQUEST_MISMATCH")


def begin(thinker, ql, call, probe=None):
    """Called after normal resolution, before credentials, adapter, or autoshrink."""
    profile = load_profile(thinker.qualification_profile)
    target = describe_target(thinker, call)
    case = match_case(profile, ql)
    if probe is not None:
        if (
            probe is not getattr(thinker, "_qualification_probe", None)
            or probe["key"] != target["key"]
        ):
            raise ValueError("INVALID_QUALIFICATION_PROBE")
        if case.case_id != probe["case_id"] or probe["consumed"]:
            raise ValueError("INVALID_QUALIFICATION_PROBE_SLOT")
        probe["consumed"] = True
        mode = "LIVE_PROBE"
        repeat = probe["repeat"]
        record_id = None
    else:
        from .registry.resolver import resolve_qualification

        selected = resolve_qualification(thinker, call)
        mode = "APPLICATION"
        repeat = 0
        record_id = selected["record_id"]
    directory = begin_execution(thinker.qualification_root)
    write_new(directory / "request.json", encode(ql))
    write_new(directory / "target.json", encode(target))
    return {
        "profile": profile,
        "target": target,
        "case": case,
        "mode": mode,
        "repeat": repeat,
        "directory": directory,
        "qualification_record": record_id,
    }


def assess(thinker, context, output=None, failure=None):
    directory = context["directory"]
    case = context["case"]
    result = {
        "target": context["target"],
        "case_id": case.case_id,
        "repeat": context["repeat"],
        "mode": context["mode"],
        "qualification_record": context.get("qualification_record"),
        "execution_status": "FAILED",
        "structural_status": "NOT_EVALUATED",
        "application_status": "NOT_EVALUATED",
        "recorded_at": now(),
        "error": failure,
    }
    if output is not None:
        write_new(directory / "adapter-output.json", encode(output))
        result["execution_status"] = output.get("execution_status", "FAILED")
        if result["execution_status"] == "COMPLETED":
            try:
                if (
                    output.get("resolved_model") != context["target"]["model"]
                    or output.get("attempt_count") != 1
                    or (context["mode"] != "REPLAY" and output.get("audit_status") != "RECORDED")
                ):
                    raise ValueError("EXECUTION_CONTRACT_VIOLATION")
                value = strict_json(output["answer"])
                schema = case.ql["instructions"]["json_schema"]
                jsonschema.validators.validator_for(schema)(schema).validate(value)
                if output.get("structure_status") != "VALID":
                    raise ValueError("ADAPTER_STRUCTURE_NOT_VALID")
                result["structural_status"] = "VALID"
            except (ValueError, KeyError, TypeError, jsonschema.ValidationError) as exc:
                result["structural_status"] = "INVALID"
                result["error"] = str(exc)
            else:
                try:
                    profile = load_profile(thinker.qualification_profile)
                    validation = invoke_validator(
                        profile.validator.command,
                        {
                            "profile_id": profile.profile_id,
                            "contract_version": profile.contract_version,
                            "provider": context["target"]["provider"],
                            "case_id": case.case_id,
                            "context": case.context,
                            "output": value,
                        },
                        profile.validator.timeout_seconds,
                        directory,
                    )
                    write_new(directory / "application-validation.json", encode(validation))
                    result["application_status"] = validation["status"]
                except (ValueError, OSError) as exc:
                    result["application_status"] = "VALIDATOR_ERROR"
                    result["error"] = str(exc)
    try:
        current = describe_target(
            thinker, thinker.store.get_call(context["target"]["registry_call"]["call_id"])
        )
        if current != context["target"]:
            result["application_status"] = "CONTRACT_DRIFT"
    except (ValueError, OSError):
        result["application_status"] = "CONTRACT_DRIFT"
    result["execution_ref"] = seal_execution(thinker.qualification_root, directory, result)
    if context["mode"] == "APPLICATION" and (
        result["execution_status"] != "COMPLETED"
        or result["structural_status"] != "VALID"
        or result["application_status"] not in {"PASS", "REVIEW"}
    ):
        from .qualification_state import record_drift

        state = (
            "QUARANTINED"
            if (
                result["execution_status"] == "COMPLETED"
                or (output or {}).get("error_code")
                in {
                    "MODEL_IDENTITY_MISMATCH",
                    "MODEL_IDENTITY_MISSING",
                    "UNEXPECTED_PROVIDER_OUTPUT",
                }
            )
            else "SUSPECT"
        )
        record_drift(
            thinker.qualification_root,
            context["target"],
            state,
            result["execution_ref"],
            "RUNTIME_CONTRACT_FAILURE",
        )
    return result


def run_suite(thinker, call_id, *, mode, authorize_target=None, fixtures=None):
    """No retry. Only host-authorized probes bypass ordinary qualification admission."""
    if mode not in {"REPLAY", "LIVE_PROBE"}:
        raise ValueError("UNKNOWN_QUALIFICATION_MODE")
    call = thinker.store.get_call(call_id)
    if call is None:
        raise ValueError("CALL_NOT_FOUND")
    target = preflight(thinker, call)
    if mode == "LIVE_PROBE" and (authorize_target != target["key"] or fixtures is not None):
        raise ValueError("LIVE_PROBE_AUTHORIZATION_REQUIRED")
    if getattr(thinker, "_qualification_probe", None) is not None:
        raise ValueError("QUALIFICATION_ALREADY_RUNNING")
    profile = load_profile(thinker.qualification_profile)
    from .qualification_state import event_head

    invalidation_head = event_head(thinker.qualification_root, target)
    started_at = now()
    slots = []
    drift = False
    for case in profile.cases:
        for repeat in range(1, profile.repeats + 1):
            ql = copy.deepcopy(case.ql)
            ql["routing"] = {"call_id": call_id}
            if drift:
                directory = begin_execution(thinker.qualification_root)
                write_new(directory / "request.json", encode(ql))
                result = assess(
                    thinker,
                    {
                        "profile": profile,
                        "target": target,
                        "case": case,
                        "mode": mode,
                        "repeat": repeat,
                        "directory": directory,
                    },
                    failure="NOT_EXECUTED_CONTRACT_DRIFT",
                )
            elif mode == "REPLAY":
                directory = begin_execution(thinker.qualification_root)
                write_new(directory / "request.json", encode(ql))
                context = {
                    "profile": profile,
                    "target": target,
                    "case": case,
                    "mode": mode,
                    "repeat": repeat,
                    "directory": directory,
                }
                output = (fixtures or {}).get(f"{case.case_id}/{repeat}")
                result = assess(
                    thinker, context, output, failure="MISSING_FIXTURE" if output is None else None
                )
                # Missing data remains visible as failed, never fabricated model output.
            else:
                probe = {
                    "key": target["key"],
                    "case_id": case.case_id,
                    "repeat": repeat,
                    "consumed": False,
                }
                thinker._qualification_probe = probe
                try:
                    response = thinker._chat_ql(ql, probe=probe)
                    result = response.qualification
                except Exception as exc:  # noqa: BLE001 -- preserve failed probe evidence; never retry
                    # Core preserves stage evidence when execution began.
                    result = probe.get("result")
                    if result is None:
                        directory = begin_execution(thinker.qualification_root)
                        write_new(directory / "request.json", encode(ql))
                        result = assess(
                            thinker,
                            {
                                "profile": profile,
                                "target": target,
                                "case": case,
                                "mode": mode,
                                "repeat": repeat,
                                "directory": directory,
                            },
                            failure=type(exc).__name__ + ": " + str(exc),
                        )
                finally:
                    thinker._qualification_probe = None
            slots.append(result)
            drift = drift or result["application_status"] == "CONTRACT_DRIFT"
    try:
        drift = drift or describe_target(thinker, call) != target
    except (ValueError, OSError):
        drift = True
    return append_decision(
        thinker.qualification_root,
        {
            "version": "thinker.qualification-record.v1",
            "target": target,
            "mode": mode,
            "status": "FAIL" if drift else decide_slots(slots, mode),
            "slots": slots,
            "started_at": started_at,
            "expires_at": (
                datetime.fromisoformat(started_at) + timedelta(seconds=profile.validity_seconds)
            ).isoformat(),
            "validity_seconds": profile.validity_seconds,
            "invalidation_head": invalidation_head,
        },
    )
