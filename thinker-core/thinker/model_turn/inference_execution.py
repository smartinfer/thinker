"""Effect boundary for planned inference: one HTTP attempt, optional private audit."""

from __future__ import annotations

import asyncio
import json
import os
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx

from .inference_adapters import lift_response
from .inference_contract import InferenceOutcome, PreparedInference
from .inference_plan import digest, encode


class PartialResponseError(Exception):
    """Only bytes actually received before transport failure; never repaired."""

    def __init__(self, status, raw, timed_out):
        self.status = status
        self.raw = raw
        self.timed_out = timed_out
        super().__init__("partial provider response")


async def send_http(url, body, headers, timeout):
    # Default transport has no retries and follows no redirects.
    async with httpx.AsyncClient(
        timeout=timeout, follow_redirects=False, trust_env=False
    ) as client:
        chunks = []
        status = None
        try:
            async with client.stream("POST", url, content=body, headers=headers) as response:
                status = response.status_code
                async for chunk in response.aiter_bytes():
                    chunks.append(chunk)
                return status, b"".join(chunks)
        except httpx.HTTPError as exc:
            if chunks:
                raise PartialResponseError(
                    status, b"".join(chunks), isinstance(exc, httpx.TimeoutException)
                ) from None
            raise


def write_once(directory, name, content):
    fd = os.open(directory / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(content)


async def execute_inference(
    prepared: PreparedInference, *, credentials, send=None, audit_directory=None, cancel_event=None
) -> InferenceOutcome:
    if (
        digest(prepared.body) != prepared.body_sha256
        or digest(prepared.request_json) != prepared.plan.request_sha256
    ):
        raise ValueError("prepared request mutated")
    request = json.loads(prepared.request_json)
    started = datetime.now(UTC)
    clock = time.monotonic()
    seconds = request["timeout_ms"] / 1000
    directory = Path(audit_directory) if audit_directory is not None else None
    if directory is not None:
        # Refuse an existing run, including symlinks, before resolving credentials or sending.
        directory.mkdir(mode=0o700, parents=True, exist_ok=False)
        write_once(
            directory,
            "identity.json",
            encode(
                {
                    "request_id": request["request_id"],
                    "started_at": started.isoformat(),
                    "request_sha256": prepared.plan.request_sha256,
                }
            ),
        )
    attempts = 0
    redacted = False
    status = "DISABLED" if directory is None else "RECORDED"
    result = InferenceOutcome(execution_status="PROVIDER_ERROR", error_code="EXECUTION_NOT_STARTED")
    task = None
    key = None
    try:
        if cancel_event is not None and cancel_event.is_set():
            result = InferenceOutcome(execution_status="CANCELLED")
        else:
            key = credentials(prepared.plan.provider)
            if directory:

                def redact(raw):
                    nonlocal redacted
                    safe = raw.replace(key.encode(), b"[REDACTED_CREDENTIAL]") if key else raw
                    redacted = redacted or safe != raw
                    return safe

                write_once(directory, "plan.json", redact(encode(prepared.plan)))
                write_once(directory, "canonical-request.json", redact(prepared.request_json))
                write_once(directory, "provider-request.json", redact(prepared.body))
            if not key:
                result = InferenceOutcome(
                    execution_status="CONFIGURATION_ERROR",
                    error_code="CREDENTIAL_UNAVAILABLE",
                )
            else:
                headers = {"Content-Type": "application/json"}
                if prepared.plan.provider == "openai":
                    headers["Authorization"] = "Bearer " + key
                elif prepared.plan.provider == "anthropic":
                    headers.update({"x-api-key": key, "anthropic-version": "2023-06-01"})
                else:
                    headers["x-goog-api-key"] = key
                remaining = seconds - (time.monotonic() - clock)
                if remaining <= 0:
                    raise TimeoutError
                attempts = 1
                if directory:
                    write_once(
                        directory,
                        "attempt.json",
                        encode(
                            {
                                "attempt": 1,
                                "started_at": started.isoformat(),
                                "endpoint": prepared.url,
                            }
                        ),
                    )
                task = asyncio.create_task(
                    (send or send_http)(prepared.url, prepared.body, headers, remaining)
                )
                while not task.done():
                    if cancel_event is not None and cancel_event.is_set():
                        task.cancel()
                        result = InferenceOutcome(execution_status="CANCELLED")
                        break
                    remaining = seconds - (time.monotonic() - clock)
                    if remaining <= 0:
                        raise TimeoutError
                    await asyncio.wait({task}, timeout=min(0.02, remaining))
                else:
                    http_status, raw = await task
                    # Lift first so known usage survives a subsequent audit-write failure.
                    result = lift_response(prepared, http_status, raw)
                    if directory:
                        safe = raw.replace(key.encode(), b"[REDACTED_CREDENTIAL]")
                        redacted = redacted or safe != raw
                        write_once(directory, "provider-response.bin", safe)
                        write_once(
                            directory,
                            "response-capture.json",
                            encode(
                                {
                                    "http_status": http_status,
                                    "credential_redacted": redacted,
                                    "original_sha256": digest(raw),
                                    "stored_sha256": digest(safe),
                                }
                            ),
                        )
                    if time.monotonic() - clock >= seconds:
                        result = result.model_copy(
                            update={
                                "execution_status": "TIMED_OUT",
                                "structure_status": "NOT_EVALUATED",
                                "structured_output": None,
                                "error_code": "DEADLINE_EXCEEDED",
                            }
                        )
    except PartialResponseError as exc:
        result = InferenceOutcome(
            execution_status="TIMED_OUT" if exc.timed_out else "PROVIDER_ERROR",
            error_code="PARTIAL_TRANSPORT_RESPONSE",
            http_status=exc.status,
            response_sha256=digest(exc.raw),
        )
        if directory:
            try:
                safe = exc.raw.replace(key.encode(), b"[REDACTED_CREDENTIAL]") if key else exc.raw
                redacted = redacted or safe != exc.raw
                write_once(directory, "provider-response.partial.bin", safe)
            except OSError:
                status = "CAPTURE_FAILED"
    except (TimeoutError, httpx.TimeoutException):
        result = InferenceOutcome(execution_status="TIMED_OUT", error_code="DEADLINE_EXCEEDED")
    except asyncio.CancelledError:
        result = InferenceOutcome(execution_status="CANCELLED")
    except httpx.HTTPError:
        result = InferenceOutcome(execution_status="PROVIDER_ERROR", error_code="TRANSPORT_ERROR")
    except OSError:
        status = "CAPTURE_FAILED"
        # Keep already received provider output/usage even if artifact persistence fails.
        result = result.model_copy(update={"error_code": result.error_code or "AUDIT_IO_ERROR"})
    # Sanitize unforeseen boundary errors rather than leak provider/credential data.
    except Exception:  # noqa: BLE001
        result = InferenceOutcome(execution_status="PROVIDER_ERROR", error_code="EXECUTION_ERROR")
    finally:
        if task is not None and not task.done():
            task.cancel()
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)
    result = result.model_copy(
        update={
            "attempt_count": attempts,
            "started_at": started.isoformat(),
            "deadline_at": (started + timedelta(seconds=seconds)).isoformat(),
            "completed_at": datetime.now(UTC).isoformat(),
            "elapsed_seconds": time.monotonic() - clock,
            "audit_status": status,
            "audit_directory": str(directory.resolve()) if directory else None,
        }
    )
    if directory:
        try:
            record = encode(
                {
                    "plan": prepared.plan.model_dump(mode="json"),
                    "outcome": result.model_dump(mode="json"),
                    "credential_redacted": redacted,
                }
            )
            if key:
                safe = record.replace(key.encode(), b"[REDACTED_CREDENTIAL]")
                if safe != record:
                    data = json.loads(safe)
                    data["credential_redacted"] = True
                    record = encode(data)
            write_once(directory, "run.json", record)
        except OSError:
            result = result.model_copy(update={"audit_status": "CAPTURE_FAILED"})
    return result
