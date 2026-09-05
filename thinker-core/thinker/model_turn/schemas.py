"""Access to the checked-in V1 JSON schemas."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast


def load_schema(name: str) -> dict[str, Any]:
    if name not in {"model_turn_request", "model_turn_response"}:
        raise ValueError(f"unknown model-turn schema {name!r}")
    path = Path(__file__).parent / "schema" / f"{name}.schema.json"
    return cast(dict[str, Any], json.loads(path.read_text(encoding="utf-8")))
