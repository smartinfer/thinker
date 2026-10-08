"""Finite qualification leases and append-only drift evidence, never runtime renewal."""

import uuid
from datetime import UTC, datetime
from pathlib import Path

from .qualification_store import digest, execution, locked, now, put, read


def identity(target):
    # Quarantine survives contract/code changes. Only an explicit fresh probe can
    # acknowledge it; changing the application version cannot erase drift.
    return digest({k: target[k] for k in ("provider", "model", "adapter", "adapter_configuration")})


def events(root, target):
    previous = {}
    for path in sorted((Path(root) / "drift").glob("*.json")):
        event = read(root, "drift", path.stem)
        if event["identity"] == identity(target):
            if event["previous_event"] in previous:
                raise ValueError("DRIFT_HISTORY_FORK")
            execution(root, event["execution_ref"])
            previous[event["previous_event"]] = (path.stem, event)
    ordered = []
    head = None
    while head in previous:
        item = previous.pop(head)
        ordered.append(item)
        head = item[0]
    if previous:
        raise ValueError("DRIFT_HISTORY_INCOMPLETE")
    return ordered


def event_head(root, target):
    items = events(root, target)
    return items[-1][0] if items else None


def record_drift(root, target, state, execution_ref, reason):
    if state not in {"SUSPECT", "QUARANTINED"} or not reason:
        raise ValueError("INVALID_DRIFT_STATE")
    evidence = execution(root, execution_ref)
    if identity(evidence["target"]) != identity(target):
        raise ValueError("DRIFT_TARGET_MISMATCH")
    with locked(root):
        items = events(root, target)
        # A later weaker signal cannot clear quarantine.
        if items and items[-1][1]["state"] == "QUARANTINED":
            state = "QUARANTINED"
        event = {
            "version": "thinker.drift.v1",
            "identity": identity(target),
            "state": state,
            "reason": reason,
            "execution_ref": execution_ref,
            "previous_event": items[-1][0] if items else None,
            "recorded_at": now(),
            "nonce": uuid.uuid4().hex,
        }
        return put(root, "drift", event)


def assert_applicable(root, target, record, at=None):
    instant = at or datetime.now(UTC)
    # Historical records without an explicit bounded lease fail closed.
    try:
        start = datetime.fromisoformat(record["started_at"])
        end = datetime.fromisoformat(record["expires_at"])
        seconds = record["validity_seconds"]
        valid = (
            start.tzinfo is not None
            and end.tzinfo is not None
            and 60 <= seconds <= 604800
            and (end - start).total_seconds() == seconds
            and start <= instant < end
        )
    except (KeyError, ValueError, TypeError):
        valid = False
    if not valid:
        raise ValueError("QUALIFICATION_STALE: fresh authorized probes required")
    items = events(root, target)
    head = items[-1][0] if items else None
    if record.get("invalidation_head") != head:
        raise ValueError("TARGET_" + (items[-1][1]["state"] if items else "SUSPECT"))
    # An acknowledged event must precede this fresh qualification's start.
    if items and datetime.fromisoformat(items[-1][1]["recorded_at"]) >= start:
        raise ValueError("TARGET_" + items[-1][1]["state"])
