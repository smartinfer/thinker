"""
Ollama local ingestor for Thinker Core.

This module discovers locally installed Ollama models by querying the
Ollama API endpoint and creates RegistryCall entries for each model.
Supports dynamic model discovery for local development and testing.

Author: Anjan Goswami
"""

import httpx

from ..schema import Catalog, Limits, Price, RegistryCall


def ingest(base_url: str = "http://localhost:11434") -> Catalog:
    try:
        r = httpx.get(f"{base_url}/api/tags", timeout=2)
        r.raise_for_status()
        tags = r.json().get("models", [])
    except Exception:  # noqa: BLE001 -- discovery is deliberately best-effort
        tags = []
    calls = []
    for m in tags:
        mid = m.get("name", "llama")
        route_name = mid.replace(".", "-").replace(":", "-")
        calls.append(
            RegistryCall(
                call_id=f"ollama:{route_name}.chat",
                provider="ollama",
                model_id=mid,
                kind="chat",
                modality="text",
                caps=["model_turn_v1", "json_mode", "json_schema"],
                limits=Limits(max_input_tokens=8192, max_output_tokens=1024),
                price=Price(input_per_1k=0.0, output_per_1k=0.0),
                adapter="ollama",
                payload_style="ollama_chat",
                endpoint=f"{base_url}/api/chat",
                aliases=[f"local:{route_name}"],
            )
        )
    return Catalog(calls=calls)
