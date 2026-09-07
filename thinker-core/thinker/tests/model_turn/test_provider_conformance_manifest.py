from __future__ import annotations

from pathlib import Path

import yaml


def test_provider_conformance_manifest_is_complete_and_uses_declared_statuses():
    path = Path(__file__).parents[4] / "spec" / "model-turn-provider-conformance.yaml"
    manifest = yaml.safe_load(path.read_text())
    assert manifest["protocol"] == "thinker.model-turn.v1"
    statuses = set(manifest["status_values"])
    required = {
        "text",
        "structured_output",
        "one_tool_call",
        "multiple_tool_calls",
        "tool_result_continuation",
        "provider_continuation",
        "timeout",
        "cancellation",
        "usage",
        "model_identity",
        "normalized_errors",
    }
    assert set(manifest["families"]) == {
        "openai-responses",
        "anthropic-messages",
        "gemini-native",
        "openai-compatible",
        "ollama-native",
        "mlx-direct",
    }
    for family in manifest["families"].values():
        assert set(family["capabilities"]) == required
        assert set(family["capabilities"].values()) <= statuses


def test_real_tested_status_has_a_recorded_smoke():
    path = Path(__file__).parents[4] / "spec" / "model-turn-provider-conformance.yaml"
    manifest = yaml.safe_load(path.read_text())
    real_providers = {
        provider
        for family in manifest["families"].values()
        if "REAL_TESTED" in family["capabilities"].values()
        for provider in family["provider_identities"]
    }
    assert real_providers == set(manifest["real_smokes"])
