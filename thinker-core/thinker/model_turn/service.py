"""Loopback HTTP/JSON service for ``thinker.model-turn.v1``."""

from __future__ import annotations

import json
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Self
from urllib.parse import unquote, urlparse

from pydantic import ValidationError

from .models import PROTOCOL_VERSION, ModelTurnRequest
from .runtime import ModelTurnRuntime

MAX_REQUEST_BYTES = 2 * 1024 * 1024


class ModelTurnHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, server_address: tuple[str, int], runtime: ModelTurnRuntime) -> None:
        host = server_address[0]
        if host not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("model-turn V1 binds loopback only")
        self.runtime = runtime
        super().__init__(server_address, ModelTurnRequestHandler)


class ModelTurnRequestHandler(BaseHTTPRequestHandler):
    server: ModelTurnHTTPServer

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/health":
            self._send(
                HTTPStatus.OK,
                {
                    "status": "ok",
                    "protocol_version": PROTOCOL_VERSION,
                    "thinker_version": self.server.runtime.thinker_version,
                    "thinker_revision": self.server.runtime.thinker_revision,
                },
            )
            return
        if path == "/v1/model-turn":
            self._send(
                HTTPStatus.OK,
                {
                    "protocol_version": PROTOCOL_VERSION,
                    "request_schema": "/v1/schemas/model-turn-request",
                    "response_schema": "/v1/schemas/model-turn-response",
                },
            )
            return
        if path == "/v1/schemas/model-turn-request":
            self._send(HTTPStatus.OK, ModelTurnRequest.model_json_schema())
            return
        if path == "/v1/schemas/model-turn-response":
            from .models import ModelTurnResponse

            self._send(HTTPStatus.OK, ModelTurnResponse.model_json_schema())
            return
        self._send(HTTPStatus.NOT_FOUND, {"error": "not_found"})

    def do_POST(self) -> None:
        if urlparse(self.path).path != "/v1/model-turn":
            self._send(HTTPStatus.NOT_FOUND, {"error": "not_found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = -1
        if length <= 0 or length > MAX_REQUEST_BYTES:
            self._send(HTTPStatus.BAD_REQUEST, {"error": "invalid_request_size"})
            return
        try:
            raw = self.rfile.read(length)
            payload = json.loads(raw)
            request = ModelTurnRequest.model_validate(payload)
        except (json.JSONDecodeError, UnicodeDecodeError, ValidationError, TypeError, ValueError):
            self._send(HTTPStatus.BAD_REQUEST, {"error": "invalid_model_turn_request"})
            return
        response = self.server.runtime.turn(request)
        self._send(HTTPStatus.OK, response.model_dump(mode="json", exclude_none=True))

    def do_DELETE(self) -> None:
        prefix = "/v1/model-turn/"
        path = urlparse(self.path).path
        if not path.startswith(prefix):
            self._send(HTTPStatus.NOT_FOUND, {"error": "not_found"})
            return
        request_id = unquote(path[len(prefix) :])
        if not request_id:
            self._send(HTTPStatus.BAD_REQUEST, {"error": "missing_request_id"})
            return
        cancelled = self.server.runtime.cancel(request_id)
        self._send(
            HTTPStatus.ACCEPTED if cancelled else HTTPStatus.NOT_FOUND,
            {"request_id": request_id, "cancelled": cancelled},
        )

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _send(self, status: HTTPStatus, payload: object) -> None:
        body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        self.send_response(status.value)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class RunningModelTurnServer:
    """Context manager used by embedding hosts and deterministic tests."""

    def __init__(
        self,
        runtime: ModelTurnRuntime,
        host: str = "127.0.0.1",
        port: int = 0,
    ) -> None:
        self.server = ModelTurnHTTPServer((host, port), runtime)
        self._thread = threading.Thread(
            target=self.server.serve_forever,
            name="thinker-model-turn-http",
            daemon=True,
        )

    @property
    def address(self) -> tuple[str, int]:
        host, port = self.server.server_address[:2]
        return str(host), int(port)

    @property
    def base_url(self) -> str:
        host, port = self.address
        return f"http://{host}:{port}"

    def start(self) -> Self:
        self._thread.start()
        return self

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self._thread.join(timeout=2.0)

    def __enter__(self) -> Self:
        return self.start()

    def __exit__(self, *exc: object) -> None:
        self.close()
