"""Pure capability negotiation and provider-specific request lowering."""

from __future__ import annotations

import hashlib
import json
from urllib.parse import quote

import jsonschema

from thinker.registry.schema import RegistryCall

from .inference_contract import CapabilityProfile, InferencePlan, PreparedInference
from .models import ModelTurnRequest


def encode(value):
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode()


def digest(value):
    return hashlib.sha256(value if isinstance(value, bytes) else encode(value)).hexdigest()


def check_schema(schema):
    jsonschema.Draft202012Validator.check_schema(schema)

    def walk(x):
        if isinstance(x, dict):
            if "$ref" in x and (not isinstance(x["$ref"], str) or not x["$ref"].startswith("#/")):
                raise ValueError("external schema references prohibited")
            if "$id" in x:
                raise ValueError("schema base URI changes prohibited")
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)

    walk(schema)


def plan_turn(
    request: ModelTurnRequest,
    registry_call: RegistryCall,
    capability_profile: CapabilityProfile,
    revision: str = "unversioned",
) -> InferencePlan:
    policy = request.inference_policy
    call = registry_call
    cap = capability_profile
    if policy is None:
        raise ValueError("explicit inference policy required")
    if request.requested_route != call.call_id:
        raise ValueError("exact registry call required, no alias routing")
    if call.provider not in {"openai", "anthropic", "gemini"} or call.adapter != call.provider:
        raise ValueError("native provider adapter required")
    if (
        call.provider != cap.provider
        or call.model_id != cap.model
        or policy.expected_model != call.model_id
    ):
        raise ValueError("capability profile/model identity mismatch")
    if call.endpoint is not None:
        raise ValueError("planned path currently requires official provider endpoint")
    if (
        request.tools
        or request.tool_results
        or request.continuation
        or any(m.tool_calls for m in request.messages)
    ):
        raise ValueError("planned no-tool single-turn policy violated")
    if not request.messages:
        raise ValueError("messages required")
    if request.generation.max_output_tokens > call.limits.max_output_tokens:
        raise ValueError("output budget exceeds model limit")
    # Sampling behavior varies by thinking model. Do not silently ignore it.
    if (
        any(getattr(request.generation, k) is not None for k in ("temperature", "top_p", "seed"))
        or request.generation.stop
    ):
        raise ValueError("sampling overrides not supported in planned profile")
    t = policy.thinking
    if t.mode == "disabled" and not cap.disable_thinking:
        raise ValueError("thinking cannot be disabled")
    if t.mode == "adaptive" and (cap.provider != "anthropic" or not cap.adaptive):
        raise ValueError("adaptive mode unsupported")
    if t.effort is not None and t.effort not in cap.effort_values:
        raise ValueError("unsupported model effort")
    if cap.provider == "anthropic" and t.mode == "effort":
        raise ValueError("select adaptive thinking explicitly with effort")
    if t.mode == "budget":
        if (
            cap.provider == "openai"
            or cap.budget_min is None
            or not cap.budget_min <= t.budget_tokens <= cap.budget_max
        ):
            raise ValueError("unsupported thinking budget")
        if (
            cap.provider == "anthropic"
            and not 1024 <= t.budget_tokens < request.generation.max_output_tokens
        ):
            raise ValueError("Anthropic thinking budget must be >=1024 and below output limit")
    enforcement = "NOT_REQUESTED"
    workaround = None
    if request.response_schema is not None:
        check_schema(request.response_schema)
        if cap.structured_output == "NATIVE" and {
            "json_schema",
            "structured_output",
        } & set(call.caps):
            enforcement = "NATIVE"
        elif (
            policy.schema_policy == "ALLOW_EMULATION"
            and cap.structured_output in {"NATIVE", "EMULATED"}
            and cap.json_mode == "NATIVE"
            and "json_mode" in call.caps
            and cap.provider == "openai"
        ):
            enforcement = "EMULATED"
            workaround = "JSON_PROMPT"
        else:
            raise ValueError("required schema capability unavailable; no downgrade allowed")
    return InferencePlan(
        request_id=request.request_id,
        registry_sha256=digest(call),
        provider=cap.provider,
        route=call.call_id,
        model=call.model_id,
        enforcement=enforcement,
        workaround=workaround,
        capabilities=cap,
        thinking=t,
        request_sha256=digest(request),
        schema_sha256=digest(request.response_schema),
        prompt_sha256=digest(
            {
                "system": request.system_instruction,
                "messages": [m.model_dump(mode="json") for m in request.messages],
            }
        ),
        settings_sha256=digest(
            {
                "policy": policy.model_dump(mode="json"),
                "capabilities": cap.model_dump(mode="json"),
                "generation": request.generation.model_dump(mode="json"),
                "timeout_ms": request.timeout_ms,
            }
        ),
        revision=revision,
    )


def lower_request(request: ModelTurnRequest, plan: InferencePlan) -> PreparedInference:
    if digest(request) != plan.request_sha256:
        raise ValueError("request changed after planning")
    system = "\n\n".join(
        x
        for x in [
            request.system_instruction,
            *[m.content for m in request.messages if m.role.value == "system"],
        ]
        if x
    )
    messages = [
        {"role": m.role.value, "content": m.content}
        for m in request.messages
        if m.role.value != "system"
    ]
    if any(m["content"] is None for m in messages):
        raise ValueError("text-only messages required")
    model = plan.model
    schema = request.response_schema
    limit = request.generation.max_output_tokens
    t = plan.thinking
    if plan.provider == "openai":
        url = "https://api.openai.com/v1/responses"
        body = {
            "model": model,
            "input": messages,
            "max_output_tokens": limit,
            "store": False,
            "tools": [],
            "tool_choice": "none",
        }
        if system:
            body["instructions"] = system
        if schema is not None:
            if plan.enforcement == "NATIVE":
                body["text"] = {
                    "format": {
                        "type": "json_schema",
                        "name": "thinker_inference",
                        "strict": True,
                        "schema": schema,
                    }
                }
            else:
                body["text"] = {"format": {"type": "json_object"}}
                body["instructions"] = (
                    system
                    + "\n\nReturn only JSON satisfying this schema: "
                    + encode(schema).decode()
                )
        if t.mode in {"effort", "disabled"}:
            body["reasoning"] = {"effort": "none" if t.mode == "disabled" else t.effort}
    elif plan.provider == "anthropic":
        url = "https://api.anthropic.com/v1/messages"
        body = {"model": model, "messages": messages, "max_tokens": limit, "tools": []}
        if system:
            body["system"] = system
        if schema is not None:
            body["output_config"] = {"format": {"type": "json_schema", "schema": schema}}
        if t.mode == "budget":
            body["thinking"] = {"type": "enabled", "budget_tokens": t.budget_tokens}
        elif t.mode == "disabled":
            body["thinking"] = {"type": "disabled"}
        elif t.mode == "adaptive":
            body["thinking"] = {"type": "adaptive"}
            if t.effort:
                body.setdefault("output_config", {})["effort"] = t.effort
    else:
        url = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            + quote(model, safe="")
            + ":generateContent"
        )
        body = {
            "contents": [
                {
                    "role": "model" if m["role"] == "assistant" else "user",
                    "parts": [{"text": m["content"]}],
                }
                for m in messages
            ],
            "tools": [],
            "generationConfig": {"maxOutputTokens": limit, "candidateCount": 1},
        }
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}
        g = body["generationConfig"]
        if schema is not None:
            g.update(responseMimeType="application/json", responseJsonSchema=schema)
        if t.mode == "effort":
            g["thinkingConfig"] = {"thinkingLevel": t.effort}
        elif t.mode in {"budget", "disabled"}:
            g["thinkingConfig"] = {"thinkingBudget": 0 if t.mode == "disabled" else t.budget_tokens}
    raw = encode(body)
    return PreparedInference(plan, encode(request), raw, url, digest(raw))
