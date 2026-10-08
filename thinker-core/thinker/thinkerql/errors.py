"""Provider failure vocabulary and pure classification for ThinkerQL 0.4.

Hermes' FailoverReason categories (dde8800) are covered without importing its
agent policies. Classification never retries, modifies prompts, rotates keys,
or judges model output. Callers retain native codes and choose recovery.
"""

import asyncio
import errno
import json
import math
import ssl
from collections.abc import Mapping
from concurrent.futures import CancelledError
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any

# Every Hermes reason has a stable representation; provider-specific distinctions
# remain visible rather than being collapsed into a generic HTTP error.
HERMES_REASON_CODES = {
    "auth": "AUTHENTICATION_FAILED",
    "auth_permanent": "AUTHENTICATION_PERMANENT",
    "billing": "QUOTA_EXHAUSTED",
    "rate_limit": "RATE_LIMITED",
    "upstream_rate_limit": "UPSTREAM_RATE_LIMITED",
    "upstream_blocked": "UPSTREAM_BLOCKED",
    "overloaded": "PROVIDER_OVERLOADED",
    "server_error": "PROVIDER_ERROR",
    "timeout": "TIMEOUT",
    "ssl_cert_verification": "TLS_ERROR",
    "context_overflow": "CONTEXT_LIMIT",
    "payload_too_large": "PAYLOAD_TOO_LARGE",
    "image_too_large": "IMAGE_TOO_LARGE",
    "image_corrupt": "IMAGE_CORRUPT",
    "model_not_found": "MODEL_UNAVAILABLE",
    "provider_policy_blocked": "PROVIDER_POLICY_BLOCKED",
    "content_policy_blocked": "CONTENT_BLOCKED",
    "model_entitlement": "MODEL_ENTITLEMENT",
    "incomplete_response": "INCOMPLETE_RESPONSE",
    "format_error": "REQUEST_REJECTED",
    "role_alternation": "ROLE_ALTERNATION",
    "invalid_encrypted_content": "CONTINUATION_REJECTED",
    "multimodal_tool_content_unsupported": "MULTIMODAL_TOOL_CONTENT_UNSUPPORTED",
    "reasoning_mandatory": "REASONING_REQUIRED",
    "thinking_signature": "THINKING_SIGNATURE_INVALID",
    "long_context_tier": "LONG_CONTEXT_TIER",
    "oauth_long_context_beta_forbidden": "OAUTH_LONG_CONTEXT_BETA_FORBIDDEN",
    "llama_cpp_grammar_pattern": "GRAMMAR_UNSUPPORTED",
    "unknown": "UNKNOWN",
}
ERROR_CODES = frozenset(HERMES_REASON_CODES.values()) | {
    "CONNECTION_ERROR",
    "CONFLICT",
    "CANCELLED",
    "BROKEN_STREAM",
    "PROTOCOL_ERROR",
    "ACCESS_DENIED",
    "LOCAL_RESOURCE_EXHAUSTED",
}

_CODE_GROUPS = {
    "QUOTA_EXHAUSTED": (
        "insufficient_quota",
        "billing_not_active",
        "payment_required",
        "insufficient_credits",
        "no_usable_credits",
        "balance_depleted",
        "model_not_supported_on_free_tier",
        "member_spend_cap_exceeded",
        "terminal_quota_exhausted",
        "credit_balance_exhausted",
        "organization_spend_limit_exceeded",
        "organization_usage_limit_exceeded",
        "project_spend_limit_exceeded",
        "insufficient_credits_for_paid_model",
        "personal-team-blocked:spending-limit",
    ),
    "RATE_LIMITED": ("resource_exhausted", "throttled", "rate_limit_exceeded", "rate_limit_error"),
    "PROVIDER_OVERLOADED": ("overloaded_error", "server_overload", "upstream_unavailable"),
    "PROVIDER_ERROR": ("server_error", "api_error"),
    "CONTEXT_LIMIT": ("context_length_exceeded", "max_tokens_exceeded"),
    "MODEL_UNAVAILABLE": ("model_not_found", "model_not_available", "invalid_model"),
    "REQUEST_REJECTED": ("unknown_parameter", "unsupported_parameter", "invalid_request_body"),
    "AUTHENTICATION_FAILED": ("authentication_error", "invalid_api_key"),
    "ACCESS_DENIED": ("permission_error", "permission_denied"),
    "CONTINUATION_REJECTED": ("invalid_encrypted_content", "thinking_signature_invalid"),
    "PAYLOAD_TOO_LARGE": ("request_too_large", "request_too_large_error"),
    "CONTENT_BLOCKED": ("content_policy_violation", "content_filter", "safety_rejection"),
    "LOCAL_RESOURCE_EXHAUSTED": (
        "prefill_memory_exceeded",
        "prefill_memory_aborted",
        "omlx_prefill_memory_exceeded",
    ),
    "BROKEN_STREAM": ("stream_interrupted", "incomplete_stream", "empty_stream"),
    "CANCELLED": ("cancelled", "canceled", "request_cancelled", "request_aborted"),
    "PROTOCOL_ERROR": ("provider_stream_non_json_data", "malformed_provider_response"),
}
_NATIVE_CODES = {native: category for category, codes in _CODE_GROUPS.items() for native in codes}
_PROVIDER_CODES = {
    "gemini": {
        "unavailable": "PROVIDER_OVERLOADED",
        "deadline_exceeded": "TIMEOUT",
        "internal": "PROVIDER_ERROR",
    },
}

# Narrow fallback phrases for relays that discard native codes. No heuristic
# based on conversation length, no generic 'max_tokens' match, and no output repair.
_MESSAGE_RULES = (
    (
        "CONTENT_BLOCKED",
        (
            "content policy",
            "safety policy",
            "safety filter",
            "flagged for possible cybersecurity risk",
            "violates our usage policies",
            "prompt was flagged by our safety",
            "content_filter",
            "responsibleaipolicyviolation",
            "content exists risk",
        ),
    ),
    (
        "PROVIDER_POLICY_BLOCKED",
        (
            "data policy",
            "privacy policy",
            "account has been banned",
            "blocked for a previous policy violation",
            "no endpoints available matching your guardrail",
        ),
    ),
    (
        "OAUTH_LONG_CONTEXT_BETA_FORBIDDEN",
        ("oauth authentication does not support long context", "oauth long context beta"),
    ),
    ("LONG_CONTEXT_TIER", ("extra usage is required for long context",)),
    (
        "MODEL_ENTITLEMENT",
        (
            "not supported when using codex with a chatgpt account",
            "not entitled to this model",
            "do not have an active grok subscription",
        ),
    ),
    (
        "THINKING_SIGNATURE_INVALID",
        (
            "invalid signature in thinking",
            "invalid thinking signature",
            "invalid signature in thinking block",
        ),
    ),
    (
        "CONTINUATION_REJECTED",
        (
            "invalid_encrypted_content",
            "conflicting authenticated continuation identities",
            "could not decrypt the provided encrypted_content",
        ),
    ),
    (
        "GRAMMAR_UNSUPPORTED",
        ("error parsing grammar", "json-schema-to-grammar", "regex lookaround is not supported"),
    ),
    (
        "REASONING_REQUIRED",
        (
            "reasoning is mandatory",
            "reasoning cannot be disabled",
            "does not support disabling reasoning",
        ),
    ),
    ("ROLE_ALTERNATION", ("roles must alternate", "roles must strictly alternate")),
    (
        "MULTIMODAL_TOOL_CONTENT_UNSUPPORTED",
        ("tool message content must be a string", "tool content must be a string"),
    ),
    (
        "IMAGE_TOO_LARGE",
        ("image too large", "image exceeds", "image dimensions exceed", "media exceeds size limit"),
    ),
    (
        "IMAGE_CORRUPT",
        (
            "invalid png image",
            "invalid jpeg image",
            "base64 string of provided image cannot be decoded",
        ),
    ),
    (
        "LOCAL_RESOURCE_EXHAUSTED",
        ("out of memory", "memory guard", "prefill safety cap", "predicted peak would"),
    ),
    (
        "REQUEST_REJECTED",
        ("unsupported parameter", "unknown parameter", "request validation failed"),
    ),
    (
        "CONTEXT_LIMIT",
        (
            "context length exceeded",
            "maximum context length",
            "context window exceeded",
            "prompt is too long",
            "input is too long",
        ),
    ),
    ("PAYLOAD_TOO_LARGE", ("request entity too large", "payload too large")),
    (
        "PROVIDER_OVERLOADED",
        (
            "temporarily overloaded",
            "server overloaded",
            "server is overloaded",
            "at capacity",
            "upstream model provider is temporarily unavailable",
        ),
    ),
    ("RATE_LIMITED", ("rate limit", "too many requests", "throttlingexception")),
    (
        "QUOTA_EXHAUSTED",
        (
            "insufficient credits",
            "credit balance",
            "hard billing limit",
            "budget limit exceeded",
            "out of funds",
        ),
    ),
    ("MODEL_UNAVAILABLE", ("model not found", "model does not exist")),
    (
        "TLS_ERROR",
        (
            "certificate verify failed",
            "certificate_verify_failed",
            "certificate verification failed",
            "unable to get local issuer certificate",
            "self-signed certificate",
            "certificate has expired",
        ),
    ),
    (
        "AUTHENTICATION_FAILED",
        (
            "invalid api key",
            "invalid_api_key",
            "invalid token",
            "token expired",
            "token revoked",
            "failed to extract accountid from token",
        ),
    ),
    ("TIMEOUT", ("timed out", "deadline exceeded")),
    (
        "CONNECTION_ERROR",
        (
            "connection refused",
            "econnrefused",
            "no route to host",
            "network is unreachable",
            "network unreachable",
            "name or service not known",
            "temporary failure in name resolution",
            "nodename nor servname provided",
            "getaddrinfo failed",
            "getaddrinfo enotfound",
            "eai_again",
            "upstream connect error",
            "broken pipe",
            "connection reset by peer",
            "peer closed connection",
            "server disconnected",
            "unexpected eof",
            "incomplete chunked read",
        ),
    ),
)
_CONNECTION_ERRNOS = {
    errno.EPIPE,
    errno.ECONNRESET,
    errno.ECONNREFUSED,
    errno.ECONNABORTED,
    errno.ENETUNREACH,
    errno.EHOSTUNREACH,
}


def _seconds(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        try:
            when = parsedate_to_datetime(str(value))
            if when.tzinfo is None:
                when = when.replace(tzinfo=UTC)
            number = (when - datetime.now(UTC)).total_seconds()
        except (TypeError, ValueError, OverflowError):
            return None
    return max(0.0, number) if math.isfinite(number) else None


def _exception_category(error: BaseException | None, stream_started: bool) -> str | None:
    seen = set()
    fallback = None
    while error is not None and id(error) not in seen:
        seen.add(id(error))
        name = type(error).__name__
        if isinstance(error, (asyncio.CancelledError, CancelledError)) or name == "AbortError":
            return "CANCELLED"
        if isinstance(error, ssl.SSLCertVerificationError):
            return "TLS_ERROR"
        if isinstance(error, TimeoutError) or name in {
            "APITimeoutError",
            "ConnectTimeout",
            "ReadTimeout",
            "WriteTimeout",
            "PoolTimeout",
            "TimeoutException",
        }:
            return "TIMEOUT"
        if (
            isinstance(error, (ConnectionError, ssl.SSLError))
            or getattr(error, "errno", None) in _CONNECTION_ERRNOS
            or name
            in {
                "APIConnectionError",
                "ConnectError",
                "ReadError",
                "WriteError",
                "RemoteProtocolError",
                "ServerDisconnectedError",
                "gaierror",
                "SSLError",
            }
        ):
            if (
                "certificate_verify_failed" in str(error).lower()
                or "certificate verify failed" in str(error).lower()
            ):
                return "TLS_ERROR"
            fallback = "BROKEN_STREAM" if stream_started else "CONNECTION_ERROR"
            # A wrapper may conceal a deterministic certificate failure.
            cause = error.__cause__ or error.__context__
            if cause is not None and id(cause) not in seen:
                error = cause
                continue
            return fallback
        if name in {
            "EmptyStreamError",
            "IncompleteRead",
            "IncompleteReadError",
            "ChunkedEncodingError",
        }:
            return "BROKEN_STREAM"
        if isinstance(error, EOFError) and stream_started:
            return "BROKEN_STREAM"
        if isinstance(error, json.JSONDecodeError) or name == "DecodingError":
            return "PROTOCOL_ERROR"
        error = error.__cause__ or error.__context__
    return fallback


def classify_error(
    error: BaseException | None = None,
    *,
    provider: str | None = None,
    http_status: int | None = None,
    body: Any = None,
    headers: Mapping[str, Any] | None = None,
    stream_started: bool = False,
) -> dict[str, Any]:
    """Describe a provider/transport failure; never call this on assistant text.

    Body is an error envelope, including an SSE error event behind HTTP 200.
    Unknown native codes remain in provider_code; unknown categories stay UNKNOWN.
    retryable is a transient-failure hint, not authorization to replay a request.
    """
    response = getattr(error, "response", None)
    http_status = http_status if http_status is not None else getattr(error, "status_code", None)
    if http_status is None:
        http_status = getattr(response, "status_code", None)
    body = body if body is not None else getattr(error, "body", None)
    if body is None and response is not None:
        try:
            body = response.json()
        except (AttributeError, ValueError):
            pass
    if isinstance(body, str):
        try:
            body = json.loads(body)
        except ValueError:
            body = {"message": body}
    data = body if isinstance(body, dict) else {}
    detail = data.get("error", data)
    detail = detail if isinstance(detail, dict) else {"message": str(detail)}
    native_code = detail.get("code", detail.get("error_code", detail.get("errorCode")))
    native_type = detail.get("type", detail.get("status"))
    code = str(native_code or native_type or "").lower()
    if http_status is None:
        for value in (
            detail.get("status_code"),
            detail.get("http_status"),
            native_code,
            data.get("code"),
        ):
            if str(value).isdigit() and 100 <= int(value) <= 599:
                http_status = int(value)
                break
    message = str(
        detail.get("message")
        or data.get("message")
        or data.get("detail")
        or (str(error) if error is not None else "Provider request failed")
    )
    lower = message.lower()
    provider_family = {
        "google": "gemini",
        "google-gemini": "gemini",
        "vertex": "gemini",
        "openai-codex": "openai",
    }.get(provider, provider)
    hdrs = headers if headers is not None else getattr(response, "headers", {}) or {}
    retry_after = _seconds(next((v for k, v in hdrs.items() if k.lower() == "retry-after"), None))
    if retry_after is None:
        retry_after = _seconds(detail.get("retry_after", data.get("retry_after")))
    if retry_after is None:
        retry_after = _seconds(detail.get("resets_in_seconds", data.get("resets_in_seconds")))

    category = _exception_category(error, stream_started)
    if category is None:
        for candidate in (code, str(native_type or "").lower()):
            category = (
                _NATIVE_CODES.get(candidate)
                or HERMES_REASON_CODES.get(candidate)
                or _PROVIDER_CODES.get(provider_family, {}).get(candidate)
            )
            if category is not None:
                break
    if category is None:
        if http_status == 400 and "long context beta" in lower and "not yet available" in lower:
            category = "OAUTH_LONG_CONTEXT_BETA_FORBIDDEN"
        elif http_status == 429 and "extra usage" in lower and "long context" in lower:
            category = "LONG_CONTEXT_TIER"
        elif (
            http_status == 400
            and "thinking" in lower
            and any(
                p in lower for p in ("signature", "cannot be modified", "must remain as they were")
            )
        ):
            category = "THINKING_SIGNATURE_INVALID"
    if category is None:
        category = next(
            (c for c, patterns in _MESSAGE_RULES if any(p in lower for p in patterns)), None
        )
    if category == "CONNECTION_ERROR" and stream_started:
        category = "BROKEN_STREAM"
    if http_status == 403 and category is None:
        category = (
            "UPSTREAM_BLOCKED"
            if any(p in lower for p in ("cloudflare", "captcha", "waf", "access denied by proxy"))
            else "ACCESS_DENIED"
        )
    if http_status == 429 and category in (None, "RATE_LIMITED"):
        metadata = detail.get("metadata", {})
        if (
            provider == "openrouter"
            and isinstance(metadata, dict)
            and metadata.get("provider_name")
        ):
            category = "UPSTREAM_RATE_LIMITED"
        elif code == "usage_limit_reached":
            category = (
                "RATE_LIMITED"
                if retry_after is not None
                or any(p in lower for p in ("reset", "try again", "rate limit"))
                else "QUOTA_EXHAUSTED"
            )
    if http_status == 402 and category is None and retry_after is not None:
        category = "RATE_LIMITED"
    if category is None:
        category = {
            400: "REQUEST_REJECTED",
            401: "AUTHENTICATION_FAILED",
            402: "QUOTA_EXHAUSTED",
            404: "MODEL_UNAVAILABLE",
            408: "TIMEOUT",
            409: "CONFLICT",
            413: "PAYLOAD_TOO_LARGE",
            422: "REQUEST_REJECTED",
            429: "RATE_LIMITED",
            503: "PROVIDER_OVERLOADED",
            504: "TIMEOUT",
            529: "PROVIDER_OVERLOADED",
        }.get(http_status)
    if category is None:
        category = (
            "PROVIDER_ERROR"
            if http_status is not None and 500 <= http_status < 600
            else "REQUEST_REJECTED"
            if http_status is not None and 400 <= http_status < 500
            else "UNKNOWN"
        )
    return {
        "code": category,
        "message": message,
        "provider_code": str(native_code) if native_code is not None else None,
        "provider_type": str(native_type) if native_type is not None else None,
        "http_status": http_status,
        "retry_after_seconds": retry_after,
        "retryable": category
        in {
            "RATE_LIMITED",
            "UPSTREAM_RATE_LIMITED",
            "PROVIDER_OVERLOADED",
            "PROVIDER_ERROR",
            "TIMEOUT",
            "CONNECTION_ERROR",
            "BROKEN_STREAM",
            "CONFLICT",
        },
        "partial_response": stream_started,
    }
