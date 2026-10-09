"""Stateless ThinkerQL 0.4 model calls. The caller owns routing and recovery."""

import asyncio
import base64
import hashlib
import time
import uuid
from datetime import UTC, datetime

import httpx

from .model_turn.http_common import resolve_credential
from .model_turn.inference_adapters import strict_json
from .observability.trace import observe_call
from .registry.schema import RegistryCall
from .thinkerql import classify_error, parse_request, parse_response
from .thinkerql.wire import UnsupportedRequest, lift, prepare


async def model_call(
    packet: dict,
    *,
    route: RegistryCall,
    credentials=resolve_credential,
    client: httpx.AsyncClient | None = None,
    recorder=None,
    debug: bool = False,
    metrics=True,
) -> dict:
    """Perform at most one HTTP request and return a ThinkerQL 0.4 result.

    The host supplies a resolved registry route and, optionally, its async HTTP
    client and credential resolver. No history, qualification admission, output
    validation, retry, schema downgrade, tool execution, or provider fallback is
    performed. Existing qualified APIs are separate and unchanged.

    Optional instructions.json_schema and json_schema_strict are provider request
    settings owned by the caller, never local output acceptance conditions.

    Cancel this coroutine's task to abort the transport and receive a CANCELLED
    packet. A caller-owned client remains open. The default client has no retries
    or redirects. This function buffers one non-SSE response; received bytes
    survive a timeout/disconnect/cancellation in provider_data.raw_body_base64.

    Metrics use the existing collector by default; metrics=False disables them.
    A recorder writes summaries unless debug=True enables full redacted packet
    snapshots. Debug without a recorder uses THINKER_TRACE_DIR or ~/.thinker/traces.
    Storage failures appear in metadata.observability, not the model error.
    """
    started = datetime.now(UTC).isoformat()
    invocation_id = str(uuid.uuid4())
    clock = time.monotonic()
    source = packet if isinstance(packet, dict) else {}
    result = {
        "version": "0.4",
        "request_id": source.get("request_id")
        if isinstance(source.get("request_id"), str) and source["request_id"]
        else str(uuid.uuid4()),
        "status": "failed",
        "usage": None,
        "cost_usd": None,
    }
    for field in ("job_id", "turn_id"):
        if isinstance(source.get(field), str) and source[field]:
            result[field] = source[field]
    raw = bytearray()
    status, headers = None, {}
    provider, protocol, body = None, None, None
    url, key = None, None
    request_headers = dict(client.headers) if client is not None else {}
    request_raw = None
    own_client = client is None
    phase = "request"
    try:
        packet = parse_request(packet)
        route = route.model_copy(deep=True)
        provider = route.provider
        protocol, url, body = prepare(packet, route)
        result["resolved_route"] = {
            "route_id": route.call_id,
            "provider": provider,
            "model": route.model_id,
            "settings": {
                "payload_style": route.payload_style,
                "endpoint": url,
                "instructions": packet.get("instructions", {}),
                "provider_options": packet.get("provider_options", {}),
                "reasoning": packet.get("routing", {}).get("reasoning"),
            },
        }
        timeout = packet.get("timeout_ms", 30000) / 1000
        phase = "credentials"
        async with asyncio.timeout(timeout):
            # Credential lookup can consult a synchronous OS keychain; do not
            # block other model calls while it runs.
            key = await asyncio.to_thread(credentials, provider)
            local = route.adapter == "openai_compatible_local"
            if not key and not local:
                result["error"] = classify_error(
                    http_status=401, body={"message": f"Credential unavailable for {provider}"}
                )
            else:
                auth_headers = {"Content-Type": "application/json"}
                if protocol == "anthropic":
                    auth_headers.update({"x-api-key": key, "anthropic-version": "2023-06-01"})
                elif protocol == "gemini":
                    auth_headers["x-goog-api-key"] = key
                elif key:
                    auth_headers["Authorization"] = "Bearer " + key
                request_headers.update(auth_headers)
                if client is None:
                    client = httpx.AsyncClient(
                        timeout=timeout, follow_redirects=False, trust_env=False
                    )
                phase = "transport"
                async with client.stream(
                    "POST",
                    url,
                    json=body,
                    headers=auth_headers,
                    timeout=timeout,
                    follow_redirects=False,
                ) as response:
                    status = response.status_code
                    request_headers = dict(response.request.headers)
                    if debug:
                        request_raw = response.request.content
                    headers = dict(response.headers)
                    async for chunk in response.aiter_bytes():
                        raw.extend(chunk)
                phase = "response"
                try:
                    data = strict_json(raw)
                except (ValueError, UnicodeError):
                    if 200 <= status < 300:
                        raise
                    data = {"message": raw.decode("utf-8", errors="replace")}
                result["provider_data"] = data
                if not 200 <= status < 300 or (
                    isinstance(data, dict) and (data.get("error") or data.get("status") == "failed")
                ):
                    result["error"] = classify_error(
                        provider=provider, http_status=status, body=data, headers=headers
                    )
                else:
                    normalized = lift(data, protocol)
                    parse_response({**result, **normalized})
                    result.update(normalized)
                    actual_model = data.get("modelVersion" if protocol == "gemini" else "model")
                    if isinstance(actual_model, str):
                        result["resolved_route"]["model"] = actual_model
    except asyncio.CancelledError as error:
        result.update(status="cancelled", error=classify_error(error, stream_started=bool(raw)))
    except Exception as error:  # noqa: BLE001 -- public boundary returns an error packet
        if phase == "request":
            result["error"] = {
                "code": "UNSUPPORTED_REQUEST"
                if isinstance(error, UnsupportedRequest)
                else "REQUEST_REJECTED",
                "message": str(error),
                "retryable": False,
            }
        elif phase == "response":
            result["error"] = {
                "code": "PROTOCOL_ERROR",
                "message": "Could not decode provider response envelope",
                "retryable": False,
            }
        else:
            result["error"] = classify_error(
                error,
                provider=provider,
                http_status=status,
                headers=headers,
                stream_started=bool(raw),
            )
        result["status"] = "incomplete" if raw and phase == "transport" else "failed"
    finally:
        if own_client and client is not None:
            await client.aclose()
    if result.get("error") and raw:
        # Base64 preserves exact bytes, including a truncated UTF-8 sequence.
        result["provider_data"] = {
            "response": result.get("provider_data"),
            "raw_body_base64": base64.b64encode(raw).decode(),
        }
    result.update(
        started_at=started,
        completed_at=datetime.now(UTC).isoformat(),
        duration_ms=(time.monotonic() - clock) * 1000,
    )
    invocation = {
        key: value
        for key, value in result.items()
        if key not in {"version", "request_id", "job_id", "turn_id"}
    }
    invocation.update(call_id=invocation_id, stage="direct")
    # A failed preparation/credential lookup made no model invocation.
    result["invocations"] = [invocation] if phase in {"transport", "response"} else []
    result = parse_response(result)
    native_request = (
        {
            "protocol": protocol,
            "url": url,
            "headers": request_headers,
            "body": body,
            "raw_body_base64": base64.b64encode(request_raw).decode()
            if request_raw is not None
            else None,
            "raw_sha256": hashlib.sha256(request_raw).hexdigest()
            if request_raw is not None
            else None,
        }
        if debug
        else None
    )
    native_response = (
        {
            "http_status": status,
            "headers": headers,
            "raw_body_base64": base64.b64encode(raw).decode(),
            "raw_sha256": hashlib.sha256(raw).hexdigest(),
        }
        if debug
        else None
    )
    receipt = await observe_call(
        result,
        invocation_id=invocation_id,
        packet=packet,
        native_request=native_request,
        native_response=native_response,
        recorder=recorder,
        debug=debug,
        metrics=metrics,
        secrets=(key,),
    )
    result.setdefault("metadata", {})["observability"] = receipt
    return parse_response(result)
