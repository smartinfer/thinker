"""SQLite completion ledger and canonical request fingerprints."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from dataclasses import replace
from pathlib import Path

from .image_models import ImageGenerationRequest, ImageGenerationResponse


def request_fingerprint(request: ImageGenerationRequest) -> str:
    immutable = {
        "call_id": request.call_id,
        "prompt": request.prompt,
        "n": request.n,
        "size": request.size,
        "aspect_ratio": request.aspect_ratio,
        "quality": request.quality,
        "seed": request.seed,
        "provider_options": request.provider_options,
        "caller_metadata": request.caller_metadata,
    }
    encoded = json.dumps(
        immutable, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class CompletionLedger:
    """Durable request-ID store. Image BLOBs are retained to permit exact replay."""

    def __init__(self, path: str):
        self.path = path
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.execute("PRAGMA journal_mode=WAL")
        return connection

    def _initialize(self) -> None:
        with self._connect() as db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS image_completions (
                    request_id TEXT PRIMARY KEY,
                    fingerprint TEXT NOT NULL,
                    status TEXT NOT NULL,
                    response_json TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """)

    def get(self, request_id: str) -> tuple[str, str, ImageGenerationResponse | None] | None:
        with self._lock, self._connect() as db:
            row = db.execute(
                "SELECT fingerprint, status, response_json FROM image_completions WHERE request_id = ?",
                (request_id,),
            ).fetchone()
        if row is None:
            return None
        response = ImageGenerationResponse.from_dict(json.loads(row[2])) if row[2] else None
        return row[0], row[1], response

    def claim(self, request_id: str, fingerprint: str, *, retry_failed: bool = False) -> str:
        """Atomically claim a request. Returns new, success, failure, conflict, or in_progress."""
        with self._lock, self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT fingerprint, status FROM image_completions WHERE request_id = ?",
                (request_id,),
            ).fetchone()
            if row is None:
                db.execute(
                    "INSERT INTO image_completions(request_id, fingerprint, status) VALUES (?, ?, 'in_progress')",
                    (request_id, fingerprint),
                )
                db.commit()
                return "new"
            if row[0] != fingerprint:
                db.commit()
                return "conflict"
            if row[1] == "success":
                db.commit()
                return "success"
            if row[1] == "failure" and retry_failed:
                db.execute(
                    "UPDATE image_completions SET status='in_progress', updated_at=CURRENT_TIMESTAMP WHERE request_id=?",
                    (request_id,),
                )
                db.commit()
                return "new"
            db.commit()
            return "failure" if row[1] == "failure" else "in_progress"

    def complete(self, fingerprint: str, response: ImageGenerationResponse) -> None:
        status = "success" if response.status == "success" else "failure"
        payload = json.dumps(
            response.to_dict(include_data=True), sort_keys=True, separators=(",", ":")
        )
        with self._lock, self._connect() as db:
            db.execute(
                """UPDATE image_completions
                   SET fingerprint=?, status=?, response_json=?, updated_at=CURRENT_TIMESTAMP
                   WHERE request_id=?""",
                (fingerprint, status, payload, response.request_id),
            )

    def replay(self, request_id: str) -> ImageGenerationResponse | None:
        record = self.get(request_id)
        if not record or not record[2]:
            return None
        return replace(record[2], replayed=True)
