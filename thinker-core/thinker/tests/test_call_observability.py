"""Trace persistence and existing metrics integration at the public call boundary."""

import asyncio
import base64
import hashlib
import io
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import Mock
from uuid import uuid4

import httpx
import pytest
from rich.console import Console

from thinker import model_call
from thinker.observability import (
    GCSTraceStore,
    MetricsCollector,
    MetricsStorage,
    S3TraceStore,
    TraceRecorder,
)
from thinker.observability.dashboard import Dashboard
from thinker.observability.trace import LocalTraceStore, redact, trace_key
from thinker.tests.test_modelcalls import BrokenBody, packet, response, route

SECRET = "test-provider-credential-DO-NOT-STORE"


def run_call(**kwargs):
    data = kwargs.pop("data", response())
    request = kwargs.pop("request", packet())
    transport = kwargs.pop(
        "transport", httpx.MockTransport(lambda _: httpx.Response(200, json=data))
    )

    async def run():
        async with httpx.AsyncClient(
            transport=transport, headers={"Cookie": "session=private-cookie-value"}
        ) as client:
            return await model_call(
                request, route=route(), client=client, credentials=lambda _: SECRET, **kwargs
            )

    return asyncio.run(run())


def read_trace(result):
    receipt = result["metadata"]["observability"]["trace"]
    assert receipt["status"] == "recorded"
    raw = Path(receipt["uri"]).read_bytes()
    assert hashlib.sha256(raw).hexdigest() == receipt["sha256"]
    return json.loads(raw)


def test_debug_snapshot_is_complete_redacted_and_leaves_result_unchanged(tmp_path):
    recorder = TraceRecorder.local(tmp_path)
    data = response(text=f"Answer echoes {SECRET} and private-cookie-value")
    request = packet(
        metadata={"api_key": SECRET},
        messages=[{"role": "user", "parts": [{"type": "text", "text": "Question with " + SECRET}]}],
    )
    result = run_call(request=request, data=data, recorder=recorder, debug=True, metrics=False)
    snapshot = read_trace(result)
    assert snapshot["mode"] == "debug"
    assert snapshot["ids"]["invocation_id"] == result["invocations"][0]["call_id"]
    assert snapshot["ids"]["job_id"] == "j1"
    assert snapshot["request"]["messages"][0]["parts"][0]["text"] == "Question with [REDACTED]"
    assert (
        snapshot["native_request"]["body"]["messages"][0]["content"][0]["text"]
        == "Question with [REDACTED]"
    )
    assert snapshot["native_request"]["headers"]["authorization"] == "[REDACTED]"
    raw_response = base64.b64decode(snapshot["native_response"]["raw_body_base64"]).decode()
    serialized = json.dumps(snapshot)
    for secret in (SECRET, "private-cookie-value"):
        assert secret not in serialized
        assert secret not in raw_response
    assert snapshot["response"]["content"].count("[REDACTED]") == 2
    assert result["content"] == data["choices"][0]["message"]["content"]
    assert request["metadata"]["api_key"] == SECRET
    assert Path(result["metadata"]["observability"]["trace"]["uri"]).stat().st_mode & 0o777 == 0o600


def test_summary_recording_has_no_prompt_or_response_content(tmp_path):
    result = run_call(recorder=TraceRecorder.local(tmp_path), metrics=False)
    snapshot = read_trace(result)
    assert snapshot["mode"] == "summary"
    assert not {"request", "response", "native_request", "native_response"} & snapshot.keys()
    assert "Read the file" not in json.dumps(snapshot)
    assert snapshot["status"] == "completed"


def test_debug_default_location_and_disabled_mode(tmp_path, monkeypatch):
    monkeypatch.setenv("THINKER_TRACE_DIR", str(tmp_path / "traces"))
    result = run_call(metrics=False)
    assert result["metadata"]["observability"]["trace"]["status"] == "disabled"
    assert not (tmp_path / "traces").exists()
    result = run_call(debug=True, metrics=False)
    assert read_trace(result)["mode"] == "debug"
    assert len(list(tmp_path.rglob("*.json"))) == 1


def test_recording_failures_do_not_replace_success_or_expose_exception_text():
    failing = Mock()
    failing.record.side_effect = OSError(SECRET)
    failing.record_packet.side_effect = OSError(SECRET)
    result = run_call(recorder=failing, metrics=failing, debug=True)
    receipt = result["metadata"]["observability"]
    assert result["status"] == "completed"
    assert receipt["trace"] == {"status": "failed", "error_type": "OSError"}
    assert receipt["metrics"] == {"status": "failed", "error_type": "OSError"}
    assert SECRET not in json.dumps(receipt)
    assert len(result["invocations"]) == 1


def test_metrics_reuses_database_and_preserves_unknowns_and_ids(tmp_path):
    storage = MetricsStorage(str(tmp_path / "metrics.db"))
    collector = MetricsCollector(storage)
    result = run_call(metrics=collector)
    assert result["metadata"]["observability"]["metrics"]["status"] == "recorded"
    with sqlite3.connect(storage.db_path) as conn:
        row = conn.execute(
            "SELECT request_id,job_id,turn_id,invocation_id,input_tokens,output_tokens,cost_usd,outcome FROM requests"
        ).fetchone()
    assert row == (
        "r1",
        "j1",
        "t1",
        result["invocations"][0]["call_id"],
        None,
        None,
        None,
        "completed",
    )
    stats = storage.get_stats()
    assert stats["total_requests"] == 1
    assert stats["total_cost"] is None
    assert stats["total_input_tokens"] is None
    assert stats["unknown_cost_requests"] == 1
    assert storage.get_provider_stats()["openrouter"]["total_cost"] is None
    dashboard = Dashboard()
    dashboard.metrics = collector
    output = io.StringIO()
    console = Console(file=output, width=160)
    console.print(dashboard._create_header_panel(stats))
    console.print(dashboard._create_overview_table(stats))
    console.print(dashboard._create_provider_table(storage.get_provider_stats()))
    assert "unknown" in output.getvalue()


def test_old_not_null_database_migration_preserves_history(tmp_path):
    path = str(tmp_path / "old.db")
    with sqlite3.connect(path) as conn:
        conn.execute("""CREATE TABLE requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT, request_id TEXT NOT NULL,
            timestamp REAL NOT NULL, provider TEXT NOT NULL, model TEXT NOT NULL,
            call_id TEXT NOT NULL, input_tokens INTEGER NOT NULL, output_tokens INTEGER NOT NULL,
            latency_ms REAL NOT NULL, cost_usd REAL NOT NULL, status TEXT NOT NULL, error_code TEXT)""")
        conn.execute(
            "INSERT INTO requests VALUES (7,'old',123,'openai','old-model','old-route',10,20,30,0.5,'success',NULL)"
        )
    # Concurrent initialization must serialize the one migration.
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda _: MetricsStorage(path), range(4)))
    result = run_call(metrics=MetricsCollector(MetricsStorage(path)))
    assert result["status"] == "completed"
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT * FROM requests WHERE id=7").fetchone()[:12] == (
            7,
            "old",
            123.0,
            "openai",
            "old-model",
            "old-route",
            10,
            20,
            30.0,
            0.5,
            "success",
            None,
        )
        assert conn.execute("SELECT COUNT(*) FROM requests").fetchone()[0] == 2
        assert not any(
            row[3]
            for row in conn.execute("PRAGMA table_info(requests)")
            if row[1] in {"input_tokens", "output_tokens", "cost_usd"}
        )


def test_concurrent_calls_share_recorder_without_overwriting(tmp_path):
    recorder = TraceRecorder.local(tmp_path / "traces")
    collector = MetricsCollector(MetricsStorage(str(tmp_path / "metrics.db")))

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _: httpx.Response(200, json=response()))
        ) as client:
            return await asyncio.gather(
                *(
                    model_call(
                        packet(),
                        route=route(),
                        client=client,
                        credentials=lambda _: SECRET,
                        recorder=recorder,
                        debug=True,
                        metrics=collector,
                    )
                    for _ in range(16)
                )
            )

    results = asyncio.run(run())
    snapshots = [read_trace(result) for result in results]
    ids = {s["ids"]["invocation_id"] for s in snapshots}
    assert len(ids) == 16
    assert len(list((tmp_path / "traces").rglob("*.json"))) == 16
    assert collector.get_stats()["total_requests"] == 16
    assert all(s["ids"]["request_id"] == "r1" for s in snapshots)


@pytest.mark.parametrize(
    "error,status",
    [(httpx.ReadError("reset"), "incomplete"), (asyncio.CancelledError(), "cancelled")],
)
def test_interrupted_calls_are_recorded_with_redacted_partial_bytes(tmp_path, error, status):
    stream = BrokenBody(error)
    result = run_call(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, stream=stream)),
        recorder=TraceRecorder.local(tmp_path),
        debug=True,
        metrics=False,
    )
    snapshot = read_trace(result)
    assert snapshot["status"] == status
    assert snapshot["response"]["status"] == status
    assert base64.b64decode(snapshot["native_response"]["raw_body_base64"]).endswith(b"partial\xe2")
    assert snapshot["native_response"]["http_status"] == 200


def test_invalid_request_records_without_claiming_a_provider_attempt(tmp_path):
    result = run_call(
        request=packet(version="bad"),
        recorder=TraceRecorder.local(tmp_path),
        debug=True,
        metrics=False,
    )
    snapshot = read_trace(result)
    assert snapshot["status"] == "failed"
    assert snapshot["response"]["invocations"] == []
    assert snapshot["native_request"]["body"] is None


def test_store_paths_are_safe_and_existing_snapshots_are_not_overwritten(tmp_path):
    store = LocalTraceStore(tmp_path)
    key = trace_key("../../outside", "/absolute", str(uuid4()))
    assert ".." not in key
    store.write(key, b"first")
    with pytest.raises(FileExistsError):
        store.write(key, b"second")
    assert (tmp_path / key).read_bytes() == b"first"
    with pytest.raises(ValueError):
        store.write("../outside", b"bad")


def test_cloud_stores_use_conditional_creation_with_injected_clients():
    client = Mock()
    uri = S3TraceStore(client, "bucket", "prefix").write("jobs/j/turns/t/id.json", b"{}")
    client.put_object.assert_called_once_with(
        Bucket="bucket",
        Key="prefix/jobs/j/turns/t/id.json",
        Body=b"{}",
        ContentType="application/json",
        IfNoneMatch="*",
    )
    assert uri == "s3://bucket/prefix/jobs/j/turns/t/id.json"
    bucket = Mock()
    bucket.name = "bucket"
    uri = GCSTraceStore(bucket).write("jobs/j/turns/t/id.json", b"{}")
    bucket.blob.assert_called_once_with("thinker/jobs/j/turns/t/id.json")
    bucket.blob.return_value.upload_from_string.assert_called_once_with(
        b"{}", content_type="application/json", if_generation_match=0, timeout=30.0, retry=None
    )
    assert uri == "gs://bucket/thinker/jobs/j/turns/t/id.json"


def test_redaction_covers_known_credential_encodings_and_custom_fields():
    encoded = base64.b64encode(SECRET.encode()).decode()
    raw = f"Broken socket after Authorization: Bearer {SECRET}".encode()
    source = {
        "password": SECRET,
        "nested": [{"Authorization": "Bearer " + SECRET}],
        "text": SECRET + " " + encoded,
        "url": "https://user:pass@host/path?api_key=" + SECRET,
        "raw_body_base64": base64.b64encode(raw).decode(),
    }
    safe = redact(source, secrets=(SECRET,))
    assert SECRET not in json.dumps(safe)
    assert encoded not in json.dumps(safe)
    assert b"Bearer [REDACTED]" in base64.b64decode(
        safe["raw_body_base64"]
    ) or SECRET.encode() not in base64.b64decode(safe["raw_body_base64"])
    assert "user:pass" not in safe["url"]
    assert source["password"] == SECRET


def test_custom_redactor_runs_before_the_store_receives_data(tmp_path):
    def strip_content(snapshot):
        snapshot["request"]["messages"] = "[APPLICATION REDACTION]"
        return snapshot

    result = run_call(
        recorder=TraceRecorder.local(tmp_path, redactor=strip_content), debug=True, metrics=False
    )
    assert read_trace(result)["request"]["messages"] == "[APPLICATION REDACTION]"


def test_signed_url_credentials_are_redacted_without_removing_model_signatures():
    source = {
        "url": "https://host/file?key=embedded-secret&X-Amz-Signature=signed-secret",
        "thoughtSignature": "opaque-model-continuation",
    }
    safe = redact(source)
    assert safe["url"] == "https://host/file?key=[REDACTED]&X-Amz-Signature=[REDACTED]"
    assert safe["thoughtSignature"] == "opaque-model-continuation"
