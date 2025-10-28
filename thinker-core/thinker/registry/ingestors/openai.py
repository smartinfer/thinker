"""
OpenAI ingestor for Thinker Core.

This module provides model ingestion from OpenAI's /v1/models API endpoint,
normalizing the response into RegistryCall objects with proper metadata,
limits, capabilities, and pricing information.

Author: Anjan Goswami
"""

import httpx
from ..schema import Catalog, RegistryCall, Limits, Price
from ..auth import Credentials, openai_client

# Model metadata with limits, capabilities, and pricing
META = {
    "gpt-4o-mini": {
        "kind": "chat",
        "modality": "multimodal",
        "limits": Limits(max_input_tokens=128000, max_output_tokens=16384),
        "caps": ["json_mode", "tools", "images_in"],
        "price": Price(input_per_1k=0.00015, output_per_1k=0.00060),
        "payload_style": "chat_completions_v1"
    },
    "text-embedding-3-large": {
        "kind": "embed",
        "modality": "embed",
        "limits": Limits(max_input_tokens=8192, max_output_tokens=0),
        "caps": [],
        "price": Price(input_per_1k=0.00002, output_per_1k=0.0),
        "payload_style": "embeddings_v1"
    }
}

def ingest_models(creds: Credentials) -> Catalog:
    """Ingest OpenAI models and return normalized Catalog."""
    calls = []
    
    with openai_client(creds) as client:
        response = client.get("/v1/models")
        
        if response.status_code == 401:
            raise RuntimeError("Unauthorized to OpenAI /v1/models")
        
        response.raise_for_status()
        
        for model in response.json().get("data", []):
            model_id = model.get("id")
            
            if model_id in META:
                meta = META[model_id]
                
                # Determine alias based on modality
                if meta["modality"] == "embed":
                    aliases = ["openai:embed-cheap"]
                else:
                    aliases = ["openai:multimodal-cheap"]
                
                call = RegistryCall(
                    call_id=f"openai:{model_id}.{meta['kind']}",
                    provider="openai",
                    model_id=model_id,
                    kind=meta["kind"],
                    modality=meta["modality"],
                    caps=meta["caps"],
                    limits=meta["limits"],
                    price=meta["price"],
                    adapter="openai",
                    payload_style=meta["payload_style"],
                    aliases=aliases
                )
                
                calls.append(call)
    
    return Catalog(calls=calls)
