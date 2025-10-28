"""
Unit tests for OpenAI ingestor.

This module tests the OpenAI model ingestion including API calls,
model normalization, and error handling. Uses mocked network requests
to avoid requiring real API keys.

Author: Anjan Goswami
"""

import pytest
import respx
from unittest.mock import patch
from thinker.registry.ingestors.openai import ingest_models
from thinker.registry.auth import Credentials
from thinker.registry.schema import Catalog, RegistryCall

def test_ingest_models_success():
    """Test successful OpenAI model ingestion."""
    mock_response = {
        "data": [
            {
                "id": "gpt-4o-mini",
                "object": "model",
                "created": 1234567890,
                "owned_by": "openai"
            },
            {
                "id": "text-embedding-3-large", 
                "object": "model",
                "created": 1234567890,
                "owned_by": "openai"
            },
            {
                "id": "gpt-3.5-turbo",
                "object": "model", 
                "created": 1234567890,
                "owned_by": "openai"
            }
        ]
    }
    
    with respx.mock:
        respx.get("https://api.openai.com/v1/models").mock(
            return_value=respx.MockResponse(200, json=mock_response)
        )
        
        with patch.dict("os.environ", {"OPENAI_API_KEY": "sk-test-key"}):
            creds = Credentials()
            catalog = ingest_models(creds)
            
            assert len(catalog.calls) == 2  # Only gpt-4o-mini and text-embedding-3-large
            
            # Check gpt-4o-mini
            gpt_call = next(c for c in catalog.calls if c.model_id == "gpt-4o-mini")
            assert gpt_call.call_id == "openai:gpt-4o-mini.chat"
            assert gpt_call.provider == "openai"
            assert gpt_call.kind == "chat"
            assert gpt_call.modality == "multimodal"
            assert "json_mode" in gpt_call.caps
            assert "tools" in gpt_call.caps
            assert "images_in" in gpt_call.caps
            assert gpt_call.limits.max_input_tokens == 128000
            assert gpt_call.limits.max_output_tokens == 16384
            assert gpt_call.price.input_per_1k == 0.00015
            assert gpt_call.price.output_per_1k == 0.00060
            assert gpt_call.adapter == "openai"
            assert gpt_call.payload_style == "chat_completions_v1"
            assert "openai:multimodal-cheap" in gpt_call.aliases
            
            # Check text-embedding-3-large
            embed_call = next(c for c in catalog.calls if c.model_id == "text-embedding-3-large")
            assert embed_call.call_id == "openai:text-embedding-3-large.embed"
            assert embed_call.provider == "openai"
            assert embed_call.kind == "embed"
            assert embed_call.modality == "embed"
            assert embed_call.caps == []
            assert embed_call.limits.max_input_tokens == 8192
            assert embed_call.limits.max_output_tokens == 0
            assert embed_call.price.input_per_1k == 0.00002
            assert embed_call.price.output_per_1k == 0.0
            assert embed_call.adapter == "openai"
            assert embed_call.payload_style == "embeddings_v1"
            assert "openai:embed-cheap" in embed_call.aliases

def test_ingest_models_unauthorized():
    """Test OpenAI ingestion with 401 unauthorized error."""
    with respx.mock:
        respx.get("https://api.openai.com/v1/models").mock(
            return_value=respx.MockResponse(401, json={"error": "Unauthorized"})
        )
        
        with patch.dict("os.environ", {"OPENAI_API_KEY": "sk-invalid-key"}):
            creds = Credentials()
            
            with pytest.raises(RuntimeError, match="Unauthorized to OpenAI /v1/models"):
                ingest_models(creds)

def test_ingest_models_network_error():
    """Test OpenAI ingestion with network error."""
    with respx.mock:
        respx.get("https://api.openai.com/v1/models").mock(
            side_effect=Exception("Network error")
        )
        
        with patch.dict("os.environ", {"OPENAI_API_KEY": "sk-test-key"}):
            creds = Credentials()
            
            with pytest.raises(Exception, match="Network error"):
                ingest_models(creds)

def test_ingest_models_empty_response():
    """Test OpenAI ingestion with empty model list."""
    mock_response = {"data": []}
    
    with respx.mock:
        respx.get("https://api.openai.com/v1/models").mock(
            return_value=respx.MockResponse(200, json=mock_response)
        )
        
        with patch.dict("os.environ", {"OPENAI_API_KEY": "sk-test-key"}):
            creds = Credentials()
            catalog = ingest_models(creds)
            
            assert len(catalog.calls) == 0

def test_ingest_models_custom_base_url():
    """Test OpenAI ingestion with custom base URL."""
    mock_response = {
        "data": [{"id": "gpt-4o-mini", "object": "model", "created": 1234567890, "owned_by": "openai"}]
    }
    
    with respx.mock:
        respx.get("https://custom.openai.com/v1/models").mock(
            return_value=respx.MockResponse(200, json=mock_response)
        )
        
        with patch.dict("os.environ", {
            "OPENAI_API_KEY": "sk-test-key",
            "OPENAI_BASE": "https://custom.openai.com"
        }):
            creds = Credentials()
            catalog = ingest_models(creds)
            
            assert len(catalog.calls) == 1
            assert catalog.calls[0].model_id == "gpt-4o-mini"

def test_ingest_models_unknown_model():
    """Test OpenAI ingestion with unknown model (not in META)."""
    mock_response = {
        "data": [
            {"id": "unknown-model", "object": "model", "created": 1234567890, "owned_by": "openai"}
        ]
    }
    
    with respx.mock:
        respx.get("https://api.openai.com/v1/models").mock(
            return_value=respx.MockResponse(200, json=mock_response)
        )
        
        with patch.dict("os.environ", {"OPENAI_API_KEY": "sk-test-key"}):
            creds = Credentials()
            catalog = ingest_models(creds)
            
            assert len(catalog.calls) == 0  # Unknown model should be ignored
