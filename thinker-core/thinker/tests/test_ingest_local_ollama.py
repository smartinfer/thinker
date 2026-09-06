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
from thinker.adapters import get_adapter
from thinker.adapters.base import AdapterResponse
from thinker.adapters.ollama import OllamaAdapter
from thinker.thinkerql.parse import InternalRequest


def test_ingest_local_ollama_success():
    """Test successful Ollama ingestion with mocked response."""
    mock_response = {
        "models": [
            {"name": "llama2:latest", "size": 1000000000},
            {"name": "mistral:7b", "size": 500000000},
            {"name": "codellama:13b", "size": 2000000000},
        ]
    }

    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = mock_response
        mock_get.return_value.raise_for_status.return_value = None

        catalog = ingest()

        assert len(catalog.calls) == 3

        # Check llama2 call
        llama_call = next(c for c in catalog.calls if c.model_id == "llama2:latest")
        assert llama_call.call_id == "ollama:llama2-latest.chat"
        assert llama_call.provider == "ollama"
        assert llama_call.kind == "chat"
        assert llama_call.modality == "text"
        assert "json_mode" in llama_call.caps
        assert llama_call.adapter == "ollama"
        assert llama_call.payload_style == "ollama_chat"
        assert llama_call.endpoint == "http://localhost:11434/api/chat"


def test_ingest_local_ollama_failure():
    """Test Ollama ingestion with connection failure."""
    with patch("httpx.get") as mock_get:
        mock_get.side_effect = httpx.ConnectError("Connection failed")

        catalog = ingest()

        # Should return empty catalog on failure
        assert len(catalog.calls) == 0


def test_ingest_local_ollama_custom_url():
    """Test Ollama ingestion with custom base URL."""
    mock_response = {"models": [{"name": "test:latest"}]}

    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = mock_response
        mock_get.return_value.raise_for_status.return_value = None

        catalog = ingest(base_url="http://custom:8080")

        call = catalog.calls[0]
        assert call.endpoint == "http://custom:8080/api/chat"


def test_ollama_integration_with_store():
    """Test full integration: ingest -> store -> resolve."""
    mock_response = {"models": [{"name": "llama2:latest"}]}

    with patch("httpx.get") as mock_get:
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
            call_id=None,
            model="ollama/llama2:latest",
            alias=None,
            intent="chat",
            need_modality="text",
            need_caps=["json_mode"],
        )

        assert call.call_id == "ollama:llama2-latest.chat"
        assert call.provider == "ollama"
        assert call.model_id == "llama2:latest"

        adapter = get_adapter(call.adapter, call.endpoint)
        assert isinstance(adapter, OllamaAdapter)
        assert adapter._chat_url() == "http://localhost:11434/api/chat"

        request = InternalRequest(
            messages=[{"role": "user", "parts": [{"type": "text", "text": "hello"}]}],
            documents=[],
            instructions={},
            routing={},
            need_modality="text",
            need_caps=[],
            intent="chat",
        )
        ollama_response = Mock()
        ollama_response.status_code = 200
        ollama_response.json.return_value = {
            "message": {"content": "hello back"},
            "prompt_eval_count": 1,
            "eval_count": 2,
        }
        with patch("httpx.Client.post", return_value=ollama_response) as mock_post:
            response = adapter.chat(request, call)

        mock_post.assert_called_once_with(
            "http://localhost:11434/api/chat", json=mock_post.call_args.kwargs["json"]
        )
        assert isinstance(response, AdapterResponse)
        assert not isinstance(response, Mock)
