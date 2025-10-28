"""
Anthropic ingestor for Thinker Core.

This module provides model ingestion from Anthropic's /v1/models API endpoint,
normalizing the response into RegistryCall objects with proper metadata,
limits, capabilities, and pricing information.

Author: Anjan Goswami
"""

import httpx
from ..schema import Catalog, RegistryCall, Limits, Price
from ..auth import Credentials, anthropic_client

# Model metadata with limits, capabilities, and pricing
META = {
    "claude-3-5-sonnet-20241022": {
        "kind": "chat",
        "modality": "text",
        "limits": Limits(max_input_tokens=200000, max_output_tokens=8192),
        "caps": ["json_mode", "tools"],
        "price": Price(input_per_1k=0.003, output_per_1k=0.015),
        "payload_style": "messages_v1"
    }
}

def ingest_models(creds: Credentials) -> Catalog:
    """Ingest Anthropic models and return normalized Catalog."""
    calls = []
    
    with anthropic_client(creds) as client:
        response = client.get("/v1/models")
        
        if response.status_code == 401:
            raise RuntimeError("Unauthorized to Anthropic /v1/models")
        
        response.raise_for_status()
        
        for model in response.json().get("data", []):
            model_id = model.get("id")
            
            if model_id in META:
                meta = META[model_id]
                
                call = RegistryCall(
                    call_id=f"anthropic:{model_id}.{meta['kind']}",
                    provider="anthropic",
                    model_id=model_id,
                    kind=meta["kind"],
                    modality=meta["modality"],
                    caps=meta["caps"],
                    limits=meta["limits"],
                    price=meta["price"],
                    adapter="anthropic",
                    payload_style=meta["payload_style"],
                    aliases=["anthropic:sonnet-cheap"]
                )
                
                calls.append(call)
    
    return Catalog(calls=calls)
