from __future__ import annotations

import threading
import time

import httpx

from thinker.model_turn.models import ToolDefinition
from thinker.model_turn.service import RunningModelTurnServer

from .conftest import make_request


def post(client: httpx.Client, request):
    return client.post("/v1/model-turn", json=request.model_dump(mode="json"))


def test_server_health_version_valid_request_and_shutdown(runtime):
    server = RunningModelTurnServer(runtime)
    with server, httpx.Client(base_url=server.base_url) as client:
        health = client.get("/health")
        assert health.status_code == 200
        assert health.json()["protocol_version"] == "thinker.model-turn.v1"
        version = client.get("/v1/model-turn")
        assert version.status_code == 200
        response = post(client, make_request())
        assert response.status_code == 200
        assert response.json()["assistant_content"] == "deterministic fake response"
    assert not server._thread.is_alive()


def test_server_rejects_invalid_request_without_echoing_secret(runtime):
    with RunningModelTurnServer(runtime) as server:
        response = httpx.post(
            f"{server.base_url}/v1/model-turn",
            json={"api_key": "secret-value"},
        )
    assert response.status_code == 400
    assert "secret-value" not in response.text


def test_server_schema_endpoints(runtime):
    with RunningModelTurnServer(runtime) as server:
        request_schema = httpx.get(f"{server.base_url}/v1/schemas/model-turn-request")
        response_schema = httpx.get(f"{server.base_url}/v1/schemas/model-turn-response")
    assert request_schema.json()["title"] == "ModelTurnRequest"
    assert response_schema.json()["title"] == "ModelTurnResponse"


def test_server_tool_loop_and_structured_output(runtime):
    with (
        RunningModelTurnServer(runtime) as server,
        httpx.Client(base_url=server.base_url) as client,
    ):
        tools = post(
            client,
            make_request(
                "multiple_tool_calls",
                tools=(
                    ToolDefinition(
                        name="read_file",
                        description="Read",
                        input_schema={"type": "object", "additionalProperties": True},
                    ),
                    ToolDefinition(
                        name="run_tests",
                        description="Test",
                        input_schema={"type": "object", "additionalProperties": True},
                    ),
                ),
            ),
        )
        structured = post(
            client,
            make_request(
                "structured_valid",
                request_id="service-structured",
                response_schema={"type": "object", "required": ["result"]},
            ),
        )
    assert len(tools.json()["tool_calls"]) == 2
    assert structured.json()["structured_output"] == {"result": "ok"}


def test_server_timeout_and_typed_error(runtime):
    with RunningModelTurnServer(runtime) as server:
        timeout = httpx.post(
            f"{server.base_url}/v1/model-turn",
            json=make_request("timeout", timeout_ms=20).model_dump(mode="json"),
        )
        unavailable = httpx.post(
            f"{server.base_url}/v1/model-turn",
            json=make_request("model_unavailable", request_id="unavailable").model_dump(
                mode="json"
            ),
        )
    assert timeout.json()["normalized_error"]["code"] == "RequestTimeout"
    assert unavailable.json()["normalized_error"]["code"] == "ModelUnavailable"


def test_server_cancellation(runtime):
    request = make_request("cancellation", request_id="service-cancel", timeout_ms=1_000)
    results = []
    with RunningModelTurnServer(runtime) as server:
        worker = threading.Thread(
            target=lambda: results.append(
                httpx.post(
                    f"{server.base_url}/v1/model-turn",
                    json=request.model_dump(mode="json"),
                )
            )
        )
        worker.start()
        cancelled = None
        for _ in range(100):
            cancelled = httpx.delete(f"{server.base_url}/v1/model-turn/{request.request_id}")
            if cancelled.status_code == 202:
                break
            time.sleep(0.005)
        worker.join(timeout=1.0)
    assert cancelled.status_code == 202
    assert results[0].json()["normalized_error"]["code"] == "Cancelled"
