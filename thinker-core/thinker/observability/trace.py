"""Opt-in, redacted invocation snapshots with interchangeable object stores."""

import base64
import hashlib
import json
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from urllib.parse import quote, quote_plus
from uuid import UUID

_REDACTED = "[REDACTED]"
_SENSITIVE = {
    "authorization",
    "proxy_authorization",
    "api_key",
    "apikey",
    "x_api_key",
    "x_goog_api_key",
    "access_token",
    "refresh_token",
    "id_token",
    "password",
    "passwd",
    "secret",
    "client_secret",
    "cookie",
    "set_cookie",
    "private_key",
    "x_amz_security_token",
}
_AUTH = re.compile(r"(?i)\b(Bearer|Basic)\s+[^\s\"'<>;,]+")
_LABELED = re.compile(
    r"""(?i)((?:["']?)(?:api[_-]?key|access_token|refresh_token|password|client_secret)(?:["']?)\s*[:=]\s*["']?)([^\s"'&,;}]+)"""
)
_URL_AUTH = re.compile(r"(https?://)[^/@\s]+:[^/@\s]+@", re.IGNORECASE)
_URL_QUERY_SECRET = re.compile(
    r"(?i)([?&](?:key|token|api[_-]?key|access_token|refresh_token|x-amz-signature|x-amz-credential|x-amz-security-token|x-goog-signature|x-goog-credential)=)([^&#\s\"']*)"
)


def _sensitive(name):
    return str(name).lower().replace("-", "_") in _SENSITIVE


def redact(value, secrets=()):
    """Redact known credential values and labeled authentication fields.

    Model/application content is retained. This is credential redaction, not PII
    detection. Applications may supply extra secrets and a custom redactor.
    """
    known = list(secrets)

    def discover(item):
        if isinstance(item, dict):
            for key, val in item.items():
                if _sensitive(key) and isinstance(val, str):
                    known.append(val)
                    if val.lower().startswith(("bearer ", "basic ")):
                        known.append(val.split(" ", 1)[1])
                    if "cookie" in key.lower():
                        known.extend(
                            part.split("=", 1)[1].strip() for part in val.split(";") if "=" in part
                        )
                discover(val)
        elif isinstance(item, list):
            for val in item:
                discover(val)

    discover(value)
    variants = set()
    for secret in known:
        if isinstance(secret, str) and secret:
            variants.update(
                (
                    secret,
                    quote(secret, safe=""),
                    quote_plus(secret),
                    json.dumps(secret)[1:-1],
                    base64.b64encode(secret.encode()).decode(),
                )
            )

    def text(value):
        for secret in sorted(variants, key=len, reverse=True):
            value = value.replace(secret, _REDACTED)
        value = _AUTH.sub(lambda m: m[1] + " " + _REDACTED, value)
        value = _LABELED.sub(lambda m: m[1] + _REDACTED, value)
        value = _URL_QUERY_SECRET.sub(lambda m: m[1] + _REDACTED, value)
        return _URL_AUTH.sub(r"\1[REDACTED]@", value)

    def walk(item):
        if isinstance(item, dict):
            out = {}
            for key, val in item.items():
                if _sensitive(key):
                    out[text(str(key))] = _REDACTED
                elif key == "raw_body_base64" and isinstance(val, str):
                    raw = base64.b64decode(val, validate=True)
                    # surrogateescape keeps non-UTF8 bytes intact through redaction.
                    safe = text(raw.decode("utf-8", errors="surrogateescape"))
                    out[key] = base64.b64encode(
                        safe.encode("utf-8", errors="surrogateescape")
                    ).decode()
                else:
                    out[text(str(key))] = walk(val)
            return out
        if isinstance(item, list):
            return [walk(x) for x in item]
        return text(item) if isinstance(item, str) else item

    return walk(value)


def _digest(value):
    return hashlib.sha256(str(value).encode()).hexdigest()


def trace_key(job_id, turn_id, invocation_id):
    """Return a safe object key without exposing caller IDs in paths."""
    return f"jobs/{_digest(job_id)}/turns/{_digest(turn_id)}/{UUID(invocation_id)}.json"


def _object_key(prefix, key):
    path = PurePosixPath(prefix) / key if prefix else PurePosixPath(key)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("Trace object key must stay within its configured root")
    return str(path)


@dataclass(frozen=True)
class LocalTraceStore:
    """Publish complete local snapshots atomically, without overwriting files."""

    root: str | Path

    def write(self, key, data):
        path = Path(self.root).expanduser().resolve() / _object_key("", key)
        if not path.parent.resolve().is_relative_to(Path(self.root).expanduser().resolve()):
            raise ValueError("Trace path escapes configured root")
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=".trace-", dir=path.parent)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.link(temporary, path)  # Atomic create; fails if the destination exists.
        finally:
            Path(temporary).unlink(missing_ok=True)
        return str(path)


@dataclass(frozen=True)
class S3TraceStore:
    """Use a caller-configured boto3 S3 client; no cloud dependency in core."""

    client: object
    bucket: str
    prefix: str = "thinker"

    def write(self, key, data):
        key = _object_key(self.prefix, key)
        self.client.put_object(
            Bucket=self.bucket, Key=key, Body=data, ContentType="application/json", IfNoneMatch="*"
        )
        return f"s3://{self.bucket}/{key}"


@dataclass(frozen=True)
class GCSTraceStore:
    """Use a caller-configured google.cloud.storage bucket."""

    bucket: object
    prefix: str = "thinker"
    timeout: float = 30.0

    def write(self, key, data):
        key = _object_key(self.prefix, key)
        self.bucket.blob(key).upload_from_string(
            data,
            content_type="application/json",
            if_generation_match=0,
            timeout=self.timeout,
            retry=None,
        )
        return f"gs://{self.bucket.name}/{key}"


@dataclass(frozen=True)
class TraceRecorder:
    """One immutable snapshot per library invocation, redacted before storage."""

    store: object
    extra_secrets: tuple[str, ...] = ()
    redactor: object = None

    @classmethod
    def local(cls, root, **kwargs):
        """Create a recorder backed by a local directory."""
        return cls(LocalTraceStore(root), **kwargs)

    def record(self, snapshot, *, secrets=()):
        identifiers = snapshot["ids"]
        key = trace_key(
            identifiers["job_id"] or identifiers["request_id"],
            identifiers["turn_id"] or identifiers["request_id"],
            identifiers["invocation_id"],
        )
        secrets = tuple(secrets) + tuple(self.extra_secrets)
        safe = redact(snapshot, secrets)
        if self.redactor is not None:
            safe = redact(self.redactor(safe), secrets)
        data = json.dumps(safe, ensure_ascii=True, allow_nan=False, sort_keys=True).encode()
        uri = self.store.write(key, data)
        return {"status": "recorded", "uri": uri, "sha256": hashlib.sha256(data).hexdigest()}


async def observe_call(
    result,
    *,
    invocation_id,
    packet,
    native_request,
    native_response,
    recorder,
    debug,
    metrics,
    secrets=(),
):
    """Persist observations off the event loop; report failures independently."""
    import asyncio
    import time

    from ..config import get_config
    from .metrics import get_metrics_collector

    started = time.monotonic()
    receipt = {
        "invocation_id": invocation_id,
        "metrics": {"status": "disabled"},
        "trace": {"status": "disabled"},
    }

    def record_metrics():
        if metrics is True:
            if not get_config().enable_metrics:
                return False
            collector = get_metrics_collector()
        else:
            collector = metrics
        # Metrics receive scalar observations only, never prompt/response bodies.
        measured = {
            name: result.get(name)
            for name in ("request_id", "job_id", "turn_id", "duration_ms", "cost_usd", "status")
        }
        measured["resolved_route"] = {
            name: (result.get("resolved_route") or {}).get(name)
            for name in ("route_id", "provider", "model")
        }
        measured["usage"] = {
            name: (result.get("usage") or {}).get(name)
            for name in ("input_tokens", "output_tokens")
        }
        measured["error"] = {"code": (result.get("error") or {}).get("code")}
        return collector.record_packet(redact(measured, secrets), invocation_id)

    if metrics:
        try:
            saved = await asyncio.to_thread(record_metrics)
            receipt["metrics"] = {"status": "recorded" if saved else "disabled"}
        except asyncio.CancelledError:
            receipt["metrics"] = {"status": "interrupted"}
        except Exception as exc:  # noqa: BLE001 -- telemetry cannot replace the model result
            receipt["metrics"] = {"status": "failed", "error_type": type(exc).__name__}
    if recorder is not None or debug:
        try:
            if recorder is None:
                recorder = TraceRecorder.local(
                    os.getenv("THINKER_TRACE_DIR", str(Path.home() / ".thinker" / "traces"))
                )
            usage = result.get("usage") or {}
            route = result.get("resolved_route") or {}
            snapshot = {
                "schema_version": "thinker.trace.v1",
                "ids": {
                    "request_id": result["request_id"],
                    "job_id": result.get("job_id"),
                    "turn_id": result.get("turn_id"),
                    "invocation_id": invocation_id,
                },
                "mode": "debug" if debug else "summary",
                "started_at": result["started_at"],
                "completed_at": result["completed_at"],
                "duration_ms": result["duration_ms"],
                "status": result["status"],
                "route": {key: route.get(key) for key in ("route_id", "provider", "model")},
                "usage": {key: value for key, value in usage.items() if key != "provider_data"},
                "cost_usd": result.get("cost_usd"),
                "error_code": (result.get("error") or {}).get("code"),
                "metrics": receipt["metrics"],
            }
            if debug:
                snapshot.update(
                    request=packet,
                    native_request=native_request,
                    native_response=native_response,
                    response=result,
                )
            receipt["trace"] = await asyncio.to_thread(recorder.record, snapshot, secrets=secrets)
        except asyncio.CancelledError:
            receipt["trace"] = {"status": "interrupted"}
        except Exception as exc:  # noqa: BLE001 -- never echo storage errors containing credentials
            receipt["trace"] = {"status": "failed", "error_type": type(exc).__name__}
    receipt["duration_ms"] = (time.monotonic() - started) * 1000
    return receipt
