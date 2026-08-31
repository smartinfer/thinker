"""
Unit tests for Ollama local ingestor.

This module tests the Ollama local model ingestion including API calls,
model normalization, and error handling. Uses mocked network requests
to avoid requiring a running Ollama instance.

Author: Anjan Goswami
"""

import pytest
import respx
from thinker.registry.ingestors.local_ollama import ingest
from thinker.registry.schema import Catalog, RegistryCall

def test_ingest_success():
    """Test successful Ollama model ingestion."""
    mock_response = {
        "models": [
            {
                "name": "llama3:latest",
                "size": 1000000000,
                "digest": "sha256:abc123"
            },
            {
                "name": "phi3:medium",
                "size": 500000000,
                "digest": "sha256:def456"
            }
        ]
    }
    
    with respx.mock:
        respx.get("http://localhost:11434/api/tags").mock(
            return_value=respx.MockResponse(200, json=mock_response)
        )
        
        catalog = ingest()
        
        assert len(catalog.calls) == 2
        
        # Check llama3
        llama_call = next(c for c in catalog.calls if c.model_id == "llama3")
        assert llama_call.call_id == "local:llama3.chat"
        assert llama_call.provider == "local"
        assert llama_call.kind == "chat"
        assert llama_call.modality == "text"
        assert "json_mode" in llama_call.caps
        assert llama_call.limits.max_input_tokens == 8192
        assert llama_call.limits.max_output_tokens == 1024
        assert llama_call.price.input_per_1k == 0.0
        assert llama_call.price.output_per_1k == 0.0
        assert llama_call.adapter == "ollama"
        assert llama_call.payload_style == "ollama_chat"
        assert llama_call.endpoint == "http://localhost:11434/api/chat"
        
        # Check phi3
        phi_call = next(c for c in catalog.calls if c.model_id == "phi3")
        assert phi_call.call_id == "local:phi3.chat"
        assert phi_call.provider == "local"
        assert phi_call.model_id == "phi3"

def test_ingest_custom_base_url():
    """Test Ollama ingestion with custom base URL."""
    mock_response = {
        "models": [{"name": "llama3:latest", "size": 1000000000}]
    }
    
    with respx.mock:
        respx.get("http://custom:8080/api/tags").mock(
            return_value=respx.MockResponse(200, json=mock_response)
        )
        
        catalog = ingest(base_url="http://custom:8080")
        
        assert len(catalog.calls) == 1
        call = catalog.calls[0]
        assert call.endpoint == "http://custom:8080/api/chat"

def test_ingest_connection_failure():
    """Test Ollama ingestion with connection failure."""
    with respx.mock:
        respx.get("http://localhost:11434/api/tags").mock(
            side_effect=Exception("Connection failed")
        )
        
        catalog = ingest()
        
        # Should return empty catalog on failure
        assert len(catalog.calls) == 0

def test_ingest_empty_response():
    """Test Ollama ingestion with empty model list."""
    mock_response = {"models": []}
    
    with respx.mock:
        respx.get("http://localhost:11434/api/tags").mock(
            return_value=respx.MockResponse(200, json=mock_response)
        )
        
        catalog = ingest()
        
        assert len(catalog.calls) == 0

def test_ingest_model_name_extraction():
    """Test proper extraction of model names from tags."""
    mock_response = {
        "models": [
            {"name": "llama3:latest", "size": 1000000000},
            {"name": "phi3:medium", "size": 500000000},
            {"name": "codellama:13b", "size": 2000000000}
        ]
    }
    
    with respx.mock:
        respx.get("http://localhost:11434/api/tags").mock(
            return_value=respx.MockResponse(200, json=mock_response)
        )
        
        catalog = ingest()
        
        model_ids = {call.model_id for call in catalog.calls}
        assert "llama3" in model_ids
        assert "phi3" in model_ids
        assert "codellama" in model_ids
