"""Pure provider response lifting. No semantic interpretation or JSON repair."""

from __future__ import annotations

import json

import jsonschema

from .inference_contract import InferenceOutcome, PreparedInference, UsageReport
from .inference_plan import digest


def strict_json(raw):
    def unique(pairs):
        out = {}
        for k, v in pairs:
            if k in out:
                raise ValueError("duplicate key")
            out[k] = v
        return out

    return json.loads(
        raw,
        object_pairs_hook=unique,
        parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite number")),
    )


def usage_report(provider, data):
    raw = data.get("usageMetadata" if provider == "gemini" else "usage")
    if not isinstance(raw, dict):
        return UsageReport()

    def n(key, obj=raw):
        v = obj.get(key) if isinstance(obj, dict) else None
        return v if type(v) is int and v >= 0 else None

    if provider == "gemini":
        # Gemini's proto3 JSON omits zero-valued counts: an absent field means 0,
        # a present but invalid value stays unknown.
        def n0(key):
            return 0 if key not in raw else n(key)

        visible = n("candidatesTokenCount")
        reasoning = n0("thoughtsTokenCount")
        output = visible + reasoning if visible is not None and reasoning is not None else None
        return UsageReport(
            input_tokens=n("promptTokenCount"),
            output_tokens=output,
            visible_output_tokens=visible,
            total_tokens=n("totalTokenCount"),
            reasoning_tokens=reasoning,
            cached_input_tokens=n0("cachedContentTokenCount"),
            raw=raw,
        )
    return UsageReport(
        input_tokens=n("input_tokens"),
        output_tokens=n("output_tokens"),
        total_tokens=n("total_tokens"),
        reasoning_tokens=(
            n("reasoning_tokens", raw.get("output_tokens_details", {}))
            if provider == "openai"
            else None
        ),
        cached_input_tokens=(
            n("cached_tokens", raw.get("input_tokens_details", {}))
            if provider == "openai"
            else n("cache_read_input_tokens")
        ),
        raw=raw,
    )


def http_error(status):
    if status in (401, 403):
        return "AUTHENTICATION_FAILURE"
    if status == 429:
        return "RATE_LIMITED"
    if status in (408, 504):
        return "REQUEST_TIMEOUT"
    if status in (502, 503):
        return "PROVIDER_OVERLOADED"
    if status == 404:
        return "MODEL_UNAVAILABLE"
    return "PROVIDER_REJECTED_REQUEST" if status < 500 else "PROVIDER_FAILURE"


def openai_parts(data):
    texts = []
    thoughts = opaque = 0
    refused = False
    for item in data.get("output", []):
        kind = item["type"]
        if kind == "reasoning":
            thoughts += 1
            opaque += bool(item.get("encrypted_content"))
            continue
        if kind != "message":
            raise ValueError("TOOL_OR_UNKNOWN_OUTPUT")
        for part in item.get("content", []):
            if part["type"] == "refusal":
                refused = True
            elif part["type"] == "output_text":
                texts.append(part["text"])
            else:
                raise ValueError("UNKNOWN_OUTPUT_PART")
    state = {
        "completed": "COMPLETED",
        "incomplete": "INCOMPLETE",
        "failed": "PROVIDER_ERROR",
        "cancelled": "CANCELLED",
        "in_progress": "INCOMPLETE",
        "queued": "INCOMPLETE",
    }.get(data.get("status"), "PROVIDER_ERROR")
    reason = data.get("status")
    if isinstance(data.get("incomplete_details"), dict):
        reason = data["incomplete_details"].get("reason", reason)
    return texts, thoughts, opaque, "REFUSAL" if refused else state, reason


def anthropic_parts(data):
    texts = []
    thoughts = opaque = 0
    for part in data.get("content", []):
        kind = part["type"]
        if kind == "text":
            texts.append(part["text"])
        elif kind in {"thinking", "redacted_thinking"}:
            thoughts += 1
            opaque += bool(part.get("signature") or part.get("data"))
        else:
            raise ValueError("TOOL_OR_UNKNOWN_OUTPUT")
    reason = data.get("stop_reason")
    state = {
        "end_turn": "COMPLETED",
        "stop_sequence": "COMPLETED",
        "refusal": "REFUSAL",
        "max_tokens": "INCOMPLETE",
        "pause_turn": "INCOMPLETE",
    }.get(reason, "PROVIDER_ERROR")
    return texts, thoughts, opaque, state, reason


def gemini_parts(data):
    feedback = data.get("promptFeedback", {})
    if feedback.get("blockReason") not in (None, "", "BLOCK_REASON_UNSPECIFIED"):
        return [], 0, 0, "REFUSAL", feedback["blockReason"]
    candidates = data.get("candidates", [])
    if len(candidates) != 1:
        raise ValueError("CANDIDATE_COUNT")
    c = candidates[0]
    texts = []
    thoughts = opaque = 0
    if c.get("groundingMetadata") or c.get("urlContextMetadata"):
        raise ValueError("OUTSIDE_EVIDENCE")
    for part in c.get("content", {}).get("parts", []):
        if set(part) - {"text", "thought", "thoughtSignature"}:
            raise ValueError("TOOL_OR_UNKNOWN_OUTPUT")
        if "thought" in part and type(part["thought"]) is not bool:
            raise ValueError("MALFORMED_THOUGHT_FLAG")
        opaque += bool(part.get("thoughtSignature"))
        if part.get("thought") is True:
            thoughts += 1
        elif isinstance(part.get("text"), str):
            texts.append(part["text"])
        else:
            raise ValueError("MALFORMED_OUTPUT_PART")
    reason = c.get("finishReason")
    refused = {
        "SAFETY",
        "RECITATION",
        "BLOCKLIST",
        "PROHIBITED_CONTENT",
        "SPII",
        "IMAGE_SAFETY",
    }
    state = (
        "REFUSAL"
        if reason in refused
        else {"STOP": "COMPLETED", "MAX_TOKENS": "INCOMPLETE"}.get(reason, "PROVIDER_ERROR")
    )
    return texts, thoughts, opaque, state, reason


def lift_response(
    prepared: PreparedInference, http_status: int, raw_body: bytes
) -> InferenceOutcome:
    base = {
        "execution_status": "PROVIDER_ERROR",
        "http_status": http_status,
        "response_sha256": digest(raw_body),
    }
    try:
        data = strict_json(raw_body)
        if not isinstance(data, dict):
            raise TypeError("object envelope required")
    except (ValueError, UnicodeError, TypeError):
        return InferenceOutcome(**base, error_code="MALFORMED_PROVIDER_RESPONSE")
    base["usage"] = usage_report(prepared.plan.provider, data)
    if not 200 <= http_status < 300 or data.get("error"):
        return InferenceOutcome(**base, error_code=http_error(http_status))
    provider = prepared.plan.provider
    model = data.get("modelVersion" if provider == "gemini" else "model")
    base["resolved_model"] = model if isinstance(model, str) else None
    if model is not None and model != prepared.plan.model:
        return InferenceOutcome(**base, error_code="MODEL_IDENTITY_MISMATCH")
    try:
        texts, thoughts, opaque, state, reason = {
            "openai": openai_parts,
            "anthropic": anthropic_parts,
            "gemini": gemini_parts,
        }[provider](data)
        if any(not isinstance(t, str) for t in texts):
            raise ValueError("nontext output")
    except (ValueError, KeyError, TypeError, AttributeError):
        return InferenceOutcome(**base, error_code="UNEXPECTED_PROVIDER_OUTPUT")
    base.update(
        execution_status=state,
        answer="".join(texts) or None,
        reasoning_artifacts=thoughts,
        opaque_artifacts=opaque,
        finish_reason=reason,
    )
    if state != "COMPLETED":
        return InferenceOutcome(**base)
    if model is None:
        return InferenceOutcome(
            **dict(base, execution_status="PROVIDER_ERROR"),
            error_code="MODEL_IDENTITY_MISSING",
        )
    if not texts:
        return InferenceOutcome(
            **dict(base, execution_status="PROVIDER_ERROR"),
            error_code="FINAL_ANSWER_MISSING",
        )
    schema = json.loads(prepared.request_json).get("response_schema")
    if schema is None:
        return InferenceOutcome(**base)
    try:
        result = strict_json(base["answer"])
        jsonschema.Draft202012Validator(schema).validate(result)
    except (
        ValueError,
        jsonschema.ValidationError,
        jsonschema.exceptions._WrappedReferencingError,
    ):  # local structural boundary
        return InferenceOutcome(
            **base, structure_status="INVALID", error_code="STRUCTURED_OUTPUT_VIOLATION"
        )
    return InferenceOutcome(**base, structure_status="VALID", structured_output=result)
