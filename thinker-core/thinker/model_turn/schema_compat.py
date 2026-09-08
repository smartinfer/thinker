"""Shared provider-schema compatibility helpers for Model-Turn adapters.

Thinker's V1 boundary treats OpenAI-style strict schemas as the canonical
arriving form: previously-optional fields are listed in ``required`` with a
``type: [T, "null"]`` union. Providers that do not implement that convention
(Anthropic, Gemini) get two symmetric, semantics-preserving conversions:

- request side: render each required-nullable field as an ordinary optional
  field (recursively, including nested objects and array item schemas);
- response side: restore each omitted required-nullable field to ``null``
  before the common Model-Turn validation runs, so a model that legitimately
  omits an optional field is not rejected by the strict declared schema.

The common validator itself is never loosened.
"""
from __future__ import annotations

import json
from typing import Any

from .models import ToolDefinition


def render_nullable_as_optional(schema: dict[str, Any]) -> dict[str, Any]:
    """Deep-copy ``schema`` and rewrite required-nullable fields as optional."""

    rendered = json.loads(json.dumps(schema))
    _rewrite_nullable_object_fields(rendered)
    return rendered


def _rewrite_nullable_object_fields(schema: dict[str, Any]) -> None:
    properties = schema.get("properties")
    if isinstance(properties, dict):
        required = schema.get("required")
        required_names = list(required) if isinstance(required, list) else []
        optional: set[str] = set()
        for name, value in properties.items():
            if not isinstance(value, dict):
                continue
            allowed = value.get("type")
            if isinstance(allowed, list) and "null" in allowed:
                non_null = [item for item in allowed if item != "null"]
                value["type"] = non_null[0] if len(non_null) == 1 else non_null
                optional.add(name)
            _rewrite_nullable_object_fields(value)
        if optional:
            schema["required"] = [name for name in required_names if name not in optional]
    items = schema.get("items")
    if isinstance(items, dict):
        _rewrite_nullable_object_fields(items)


def restore_omitted_nullable_arguments(
    tools: tuple[ToolDefinition, ...] | list[ToolDefinition],
    tool_name: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """Restore omitted required-nullable fields to ``null``, recursively.

    Recursion matters for nested object arguments (e.g. an embedded
    obligation object): the declared strict schema marks nested
    previously-optional fields required-nullable too, and a provider that
    omits them would otherwise fail the declared-schema validation.
    """

    definition = next((tool for tool in tools if tool.name == tool_name), None)
    if definition is None:
        return dict(arguments)
    return _restore_for_schema(definition.input_schema, arguments)


def _restore_for_schema(schema: dict[str, Any], arguments: dict[str, Any]) -> dict[str, Any]:
    restored = dict(arguments)
    required = schema.get("required")
    properties = schema.get("properties")
    if not isinstance(required, list) or not isinstance(properties, dict):
        return restored
    for name in required:
        if not isinstance(name, str):
            continue
        value = properties.get(name)
        allowed = value.get("type") if isinstance(value, dict) else None
        nullable = isinstance(allowed, list) and "null" in allowed
        if name not in restored:
            if nullable:
                restored[name] = None
            continue
        current = restored[name]
        if isinstance(current, dict) and isinstance(value, dict):
            restored[name] = _restore_for_schema(value, current)
    return restored
