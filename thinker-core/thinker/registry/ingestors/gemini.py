"""
Google Gemini ingestor for Thinker Core.

This module provides model ingestion from Google's Gemini API endpoint,
normalizing the response into RegistryCall objects with proper metadata,
limits, capabilities, and pricing information.

Author: Anjan Goswami
"""

import httpx
from ..schema import Catalog, RegistryCall, Limits, Price
from ..auth import Credentials, gemini_client

# Model metadata with limits, capabilities, and pricing
META = {
    "gemini-1-5-pro": {
        "kind": "chat",
        "modality": "multimodal",
        "limits": Limits(max_input_tokens=2000000, max_output_tokens=8192),
        "caps": ["images_in"],
        "price": Price(input_per_1k=0.00125, output_per_1k=0.005),
        "payload_style": "generateContent_v1"
    }
}

def ingest_models(creds: Credentials) -> Catalog:
    """Ingest Google Gemini models and return normalized Catalog."""
    calls = []
    
    with gemini_client(creds) as client:
        response = client.get("/v1beta/models")
        
        if response.status_code == 401:
            raise RuntimeError("Unauthorized to Google /v1beta/models")
        
        response.raise_for_status()
        
        for model in response.json().get("models", []):
            model_name = model.get("name", "")
            # Extract model ID from "models/gemini-1.5-pro" format and normalize
            model_id = model_name.replace("models/", "") if model_name.startswith("models/") else model_name
            # Replace dots with hyphens to avoid call_id validation issues
            model_id = model_id.replace(".", "-")
            
            if model_id in META:
                meta = META[model_id]
                
                call = RegistryCall(
                    call_id=f"google:{model_id}.{meta['kind']}",
                    provider="google",
                    model_id=model_id,
                    kind=meta["kind"],
                    modality=meta["modality"],
                    caps=meta["caps"],
                    limits=meta["limits"],
                    price=meta["price"],
                    adapter="google",
                    payload_style=meta["payload_style"],
                    aliases=["google:multimodal-cheap"]
                )
                
                calls.append(call)
    
    return Catalog(calls=calls)
