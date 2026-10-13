"""Append-only, content-addressed qualification evidence. Host-owned local store."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path


def encode(value):
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode()


def digest(value):
    return hashlib.sha256(value if isinstance(value, bytes) else encode(value)).hexdigest()


def file_hash(path):
    return digest(Path(path).read_bytes())


def now():
    return datetime.now(UTC).isoformat()


def strict_json(text):
    def unique(pairs):
        out = {}
        for k, v in pairs:
            if k in out:
                raise ValueError("DUPLICATE_JSON_KEY")
            out[k] = v
        return out

    return json.loads(
        text,
        object_pairs_hook=unique,
        parse_constant=lambda _: (_ for _ in ()).throw(ValueError("NONFINITE_JSON")),
    )


def address(value):
    if not isinstance(value, str) or not re.fullmatch("[0-9a-f]{64}", value):
        raise ValueError("INVALID_ARTIFACT_ADDRESS")
    return value


def write_new(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def put_blob(root, namespace, raw):
    key = digest(raw)
    path = Path(root) / namespace / (key + (".bin" if namespace == "blobs" else ".json"))
    temporary = Path(root) / ".pending" / uuid.uuid4().hex
    write_new(temporary, raw)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        # Atomic publish without overwrite; a crash cannot expose a half-written record.
        os.link(temporary, path)
    except FileExistsError:
        if path.read_bytes() != raw:
            raise ValueError("ARTIFACT_HASH_MISMATCH") from None
    finally:
        temporary.unlink()
    return key


def put(root, namespace, value):
    return put_blob(root, namespace, encode(value))


def blob(root, key):
    raw = (Path(root) / "blobs" / (address(key) + ".bin")).read_bytes()
    if digest(raw) != key:
        raise ValueError("ARTIFACT_HASH_MISMATCH")
    return raw


def read(root, namespace, key):
    raw = (Path(root) / namespace / (address(key) + ".json")).read_bytes()
    if digest(raw) != key:
        raise ValueError("ARTIFACT_HASH_MISMATCH")
    return strict_json(raw)


def begin_execution(root):
    path = Path(root) / "executions" / uuid.uuid4().hex
    path.mkdir(parents=True, exist_ok=False, mode=0o700)
    return path


def seal_execution(root, directory, outcome):
    """Manifest addresses the exact bytes of every available stage, including raw transport."""
    directory = Path(directory)
    write_new(directory / "outcome.json", encode(outcome))
    artifacts = {
        str(p.relative_to(directory)): put_blob(root, "blobs", p.read_bytes())
        for p in sorted(directory.rglob("*"))
        if p.is_file()
    }
    return put(
        root,
        "evidence",
        {"version": "thinker.execution-evidence.v1", "artifacts": artifacts, "outcome": outcome},
    )


def execution(root, key):
    manifest = read(root, "evidence", key)
    for checksum in manifest["artifacts"].values():
        blob(root, checksum)
    if strict_json(blob(root, manifest["artifacts"]["outcome.json"])) != manifest["outcome"]:
        raise ValueError("EXECUTION_OUTCOME_CHANGED")
    return manifest["outcome"]


@contextmanager
def locked(root):
    Path(root).mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(Path(root) / ".append.lock", os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def history(root, target_key, live_only=True):
    records = []
    for path in sorted((Path(root) / "records").glob("*.json")):
        record = read(root, "records", path.stem)
        if record["target"]["key"] == target_key and (
            not live_only or record["mode"] == "LIVE_PROBE"
        ):
            records.append((path.stem, record))
    # Explicit append chain, not an overwritten current-PASS pointer or wall-clock ordering.
    by_previous = {}
    for key, record in records:
        previous = record["previous_record"]
        if previous in by_previous:
            raise ValueError("QUALIFICATION_HISTORY_FORK")
        by_previous[previous] = (key, record)
    ordered = []
    previous = None
    while previous in by_previous:
        item = by_previous.pop(previous)
        ordered.append(item)
        previous = item[0]
    if by_previous:
        raise ValueError("QUALIFICATION_HISTORY_INCOMPLETE")
    return ordered


def append_decision(root, record):
    with locked(root):
        # Replay has its own chain and can never revoke or qualify a live target.
        prior = history(root, record["target"]["key"]) if record["mode"] == "LIVE_PROBE" else []
        record = {
            **record,
            "previous_record": prior[-1][0] if prior else None,
            "recorded_at": now(),
            "nonce": uuid.uuid4().hex,
        }
        key = put(root, "records", record)
    return {**record, "record_id": key}
