"""Provider error fixtures share the vocabulary with future Go consumers."""

import asyncio
import errno
import json
import socket
import ssl
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from http.client import IncompleteRead
from pathlib import Path

import httpx
import pytest

from thinker.thinkerql import classify_error, parse_response
from thinker.thinkerql.errors import ERROR_CODES, HERMES_REASON_CODES

CASES = json.loads((Path(__file__).parent / "fixtures/thinkerql/errors.json").read_text())


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["name"])
def test_native_error_classification(case):
    result = classify_error(**case["input"])
    assert result["code"] == case["expected"]
    assert result["code"] in ERROR_CODES
    packet = {"version": "0.4", "request_id": "r", "status": "failed", "error": result}
    assert parse_response(packet)["error"] == result


def test_hermes_category_coverage_is_complete():
    # Snapshot of ALL FailoverReason members in Hermes dde8800, independent of
    # our mapping. This also catches a missing fixture for an accepted category.
    expected = {
        "auth",
        "auth_permanent",
        "billing",
        "rate_limit",
        "upstream_rate_limit",
        "upstream_blocked",
        "overloaded",
        "server_error",
        "timeout",
        "ssl_cert_verification",
        "context_overflow",
        "payload_too_large",
        "image_too_large",
        "image_corrupt",
        "model_not_found",
        "provider_policy_blocked",
        "content_policy_blocked",
        "model_entitlement",
        "incomplete_response",
        "format_error",
        "role_alternation",
        "invalid_encrypted_content",
        "multimodal_tool_content_unsupported",
        "reasoning_mandatory",
        "thinking_signature",
        "long_context_tier",
        "oauth_long_context_beta_forbidden",
        "llama_cpp_grammar_pattern",
        "unknown",
    }
    assert set(HERMES_REASON_CODES) == expected
    assert set(HERMES_REASON_CODES.values()) <= {c["expected"] for c in CASES}


@pytest.mark.parametrize(
    "error,stream_started,code",
    [
        (ConnectionError("network unavailable"), False, "CONNECTION_ERROR"),
        (BrokenPipeError(errno.EPIPE, "broken socket"), False, "CONNECTION_ERROR"),
        (ConnectionResetError("reset"), True, "BROKEN_STREAM"),
        (socket.gaierror("DNS failed"), False, "CONNECTION_ERROR"),
        (httpx.ConnectError("connection refused"), False, "CONNECTION_ERROR"),
        (httpx.RemoteProtocolError("peer closed connection"), True, "BROKEN_STREAM"),
        (httpx.ReadTimeout("read timed out"), True, "TIMEOUT"),
        (TimeoutError("deadline"), False, "TIMEOUT"),
        (asyncio.CancelledError(), True, "CANCELLED"),
        (IncompleteRead(b"partial"), True, "BROKEN_STREAM"),
        (EOFError(), True, "BROKEN_STREAM"),
        (ssl.SSLCertVerificationError("bad certificate"), False, "TLS_ERROR"),
        (json.JSONDecodeError("bad provider envelope", "<html>", 0), True, "PROTOCOL_ERROR"),
    ],
)
def test_connection_timeout_cancellation_and_broken_streams(error, stream_started, code):
    result = classify_error(error, stream_started=stream_started)
    assert result["code"] == code
    assert result["partial_response"] == stream_started


def test_connection_wrapper_keeps_tls_cause():
    error = httpx.ConnectError("connect failed")
    error.__cause__ = ssl.SSLCertVerificationError("certificate verify failed")
    result = classify_error(error)
    assert result["code"] == "TLS_ERROR"
    assert result["retryable"] is False


def test_unknown_connection_cause_does_not_erase_transport_category():
    error = httpx.ConnectError("connect failed")
    error.__cause__ = OSError("backend failed")
    assert classify_error(error)["code"] == "CONNECTION_ERROR"


def test_httpx_exception_preserves_native_code_and_retry_header():
    request = httpx.Request("POST", "https://provider.invalid/call")
    response = httpx.Response(
        429,
        request=request,
        headers={"Retry-After": "120"},
        json={"error": {"code": "rate_limit_exceeded", "message": "slow down"}},
    )
    error = httpx.HTTPStatusError("429", request=request, response=response)
    result = classify_error(error)
    assert result["http_status"] == 429
    assert result["provider_code"] == "rate_limit_exceeded"
    assert result["retry_after_seconds"] == 120


def test_http_date_retry_after_is_not_arbitrarily_capped():
    when = datetime.now(UTC) + timedelta(seconds=600)
    result = classify_error(
        http_status=503, headers={"retry-after": format_datetime(when, usegmt=True)}
    )
    assert 598 <= result["retry_after_seconds"] <= 600


@pytest.mark.parametrize("value", ["NaN", "Infinity", "not a date", True])
def test_invalid_retry_after_stays_unknown(value):
    assert (
        classify_error(http_status=429, headers={"Retry-After": value})["retry_after_seconds"]
        is None
    )


def test_cancellation_dominates_underlying_provider_status():
    result = classify_error(asyncio.CancelledError(), http_status=503)
    assert result["code"] == "CANCELLED"
    assert result["retryable"] is False


def test_unknown_error_keeps_native_details_without_guessing_retry():
    result = classify_error(
        body={
            "error": {
                "code": "new_provider_failure",
                "type": "future_kind",
                "message": "new condition",
            }
        }
    )
    assert result["code"] == "UNKNOWN"
    assert result["provider_code"] == "new_provider_failure"
    assert result["provider_type"] == "future_kind"
    assert result["retryable"] is False
