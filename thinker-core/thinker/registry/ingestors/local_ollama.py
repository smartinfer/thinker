"""
Ollama local ingestor for Thinker Core.

This module discovers locally installed Ollama models by querying the
Ollama API endpoint and creates RegistryCall entries for each model.
Supports dynamic model discovery for local development and testing.

Author: Anjan Goswami
"""

import httpx
from ..schema import Catalog, RegistryCall, Limits, Price

def ingest(base_url: str = "http://localhost:11434") -> Catalog:
    try:
        r = httpx.get(f"{base_url}/api/tags", timeout=2)
        r.raise_for_status()
        tags = r.json().get("models", [])
    except Exception:
        tags = []
    calls = []
    for m in tags:
        mid = m.get("name","llama").split(":")[0]
        calls.append(RegistryCall(
            call_id=f"local:{mid}.chat",
            provider="local", model_id=mid, kind="chat", modality="text",
            caps=["json_mode"],
            limits=Limits(max_input_tokens=8192, max_output_tokens=1024),
            price=Price(input_per_1k=0.0, output_per_1k=0.0),
            adapter="local", payload_style="ollama_chat",
            endpoint=f"{base_url}/api/chat"
        ))
    return Catalog(calls=calls)
