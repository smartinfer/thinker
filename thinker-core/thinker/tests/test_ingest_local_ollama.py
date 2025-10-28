"""
Integration tests for local Ollama ingestor.

This module tests the Ollama local ingestor including successful model
discovery, connection failure handling, custom URL support, and full
integration with the registry store and resolver.

Author: Anjan Goswami
"""

import pytest
import httpx
from unittest.mock import patch, Mock
from thinker.registry.ingestors.local_ollama import ingest
from thinker.registry.store import RegistryStore
from thinker.registry.resolver import resolve_call

def test_ingest_local_ollama_success():
    """Test successful Ollama ingestion with mocked response."""
    mock_response = {
        "models": [
            {"name": "llama2:latest", "size": 1000000000},
            {"name": "mistral:7b", "size": 500000000},
            {"name": "codellama:13b", "size": 2000000000}
        ]
    }
    
    with patch('httpx.get') as mock_get:
        mock_get.return_value.json.return_value = mock_response
        mock_get.return_value.raise_for_status.return_value = None
        
        catalog = ingest()
        
        assert len(catalog.calls) == 3
        
        # Check llama2 call
        llama_call = next(c for c in catalog.calls if c.model_id == "llama2")
        assert llama_call.call_id == "local:llama2.chat"
        assert llama_call.provider == "local"
        assert llama_call.kind == "chat"
        assert llama_call.modality == "text"
        assert "json_mode" in llama_call.caps
        assert llama_call.adapter == "local"
        assert llama_call.payload_style == "ollama_chat"
        assert llama_call.endpoint == "http://localhost:11434/api/chat"

def test_ingest_local_ollama_failure():
    """Test Ollama ingestion with connection failure."""
    with patch('httpx.get') as mock_get:
        mock_get.side_effect = httpx.ConnectError("Connection failed")
        
        catalog = ingest()
        
        # Should return empty catalog on failure
        assert len(catalog.calls) == 0

def test_ingest_local_ollama_custom_url():
    """Test Ollama ingestion with custom base URL."""
    mock_response = {"models": [{"name": "test:latest"}]}
    
    with patch('httpx.get') as mock_get:
        mock_get.return_value.json.return_value = mock_response
        mock_get.return_value.raise_for_status.return_value = None
        
        catalog = ingest(base_url="http://custom:8080")
        
        call = catalog.calls[0]
        assert call.endpoint == "http://custom:8080/api/chat"

def test_ollama_integration_with_store():
    """Test full integration: ingest -> store -> resolve."""
    mock_response = {"models": [{"name": "llama2:latest"}]}
    
    with patch('httpx.get') as mock_get:
        mock_get.return_value.json.return_value = mock_response
        mock_get.return_value.raise_for_status.return_value = None
        
        # Ingest
        catalog = ingest()
        
        # Apply to store
        store = RegistryStore()
        store.apply_catalog(catalog, "test_version", "ollama_ingest")
        
        # Resolve
        call = resolve_call(
            store,
            call_id=None, model="local/llama2", alias=None,
            intent="chat", need_modality="text", need_caps=["json_mode"]
        )
        
        assert call.call_id == "local:llama2.chat"
        assert call.provider == "local"
        assert call.model_id == "llama2"
