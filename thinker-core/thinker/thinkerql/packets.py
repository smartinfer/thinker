"""ThinkerQL 0.4 packet boundaries, independent of model execution.

Only the envelope is checked. Model content, tool arguments and opaque provider
state are never parsed, repaired or tested against a requested output schema.
The returned copy belongs to the invocation, not to the caller's mutable input.
"""

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import jsonschema

VERSION = "0.4"


def packet_schema(kind: str) -> dict[str, Any]:
    """Return a fresh, standalone request or response schema for other languages."""
    if kind not in {"request", "response"}:
        raise ValueError("packet kind must be request or response")
    path = Path(__file__).with_name(f"{kind}_v0_4.schema.json")
    return json.loads(path.read_text())


def _parse(packet: dict[str, Any], kind: str) -> dict[str, Any]:
    try:
        # Reject Python-only values and non-finite numbers at the wire boundary.
        if json.loads(json.dumps(packet, allow_nan=False)) != packet:
            raise ValueError("Packet contains values that cannot round-trip through JSON")
        jsonschema.Draft202012Validator(packet_schema(kind)).validate(packet)
    except (TypeError, ValueError, jsonschema.ValidationError):
        # Do not echo packet contents (which can contain private application data).
        raise ValueError(f"Invalid ThinkerQL {VERSION} {kind} envelope") from None
    return deepcopy(packet)


def parse_request(packet: dict[str, Any]) -> dict[str, Any]:
    """Check a request envelope without executing, routing, or changing its content."""
    return _parse(packet, "request")


def parse_response(packet: dict[str, Any]) -> dict[str, Any]:
    """Check a response envelope; imperfect model output is valid packet content."""
    return _parse(packet, "response")
