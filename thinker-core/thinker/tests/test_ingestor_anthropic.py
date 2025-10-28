"""
Unit tests for Anthropic ingestor.

This module tests the Anthropic model ingestion including API calls,
model normalization, and error handling. Uses mocked network requests
to avoid requiring real API keys.

Author: Anjan Goswami
"""

import pytest
import respx
from unittest.mock import patch
from thinker.registry.ingestors.anthropic import ingest_models
from thinker.registry.auth import Credentials
from thinker.registry.schema import Catalog, RegistryCall

def test_ingest_models_success():
    """Test successful Anthropic model ingestion."""
    mock_response = {
        "data": [
            {
                "id": "claude-3-5-sonnet-20241022",
                "object": "model",
                "created": 1234567890,
                "owned_by": "anthropic"
            },
            {
                "id": "claude-3-haiku-20240307",
                "object": "model",
                "created": 1234567890,
                "owned_by": "anthropic"
            }
        ]
    }
    
    with respx.mock:
        respx.get("https://api.anthropic.com/v1/models").mock(
            return_value=respx.MockResponse(200, json=mock_response)
        )
        
        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "sk-test-key"}):
            creds = Credentials()
            catalog = ingest_models(creds)
            
            assert len(catalog.calls) == 1  # Only claude-3-5-sonnet
            
            # Check claude-3-5-sonnet
            claude_call = catalog.calls[0]
            assert claude_call.call_id == "anthropic:claude-3-5-sonnet-20241022.chat"
            assert claude_call.provider == "anthropic"
            assert claude_call.model_id == "claude-3-5-sonnet-20241022"
            assert claude_call.kind == "chat"
            assert claude_call.modality == "text"
            assert "json_mode" in claude_call.caps
            assert "tools" in claude_call.caps
            assert claude_call.limits.max_input_tokens == 200000
            assert claude_call.limits.max_output_tokens == 8192
            assert claude_call.price.input_per_1k == 0.003
            assert claude_call.price.output_per_1k == 0.015
            assert claude_call.adapter == "anthropic"
            assert claude_call.payload_style == "messages_v1"
            assert "anthropic:sonnet-cheap" in claude_call.aliases

def test_ingest_models_unauthorized():
    """Test Anthropic ingestion with 401 unauthorized error."""
    with respx.mock:
        respx.get("https://api.anthropic.com/v1/models").mock(
            return_value=respx.MockResponse(401, json={"error": "Unauthorized"})
        )
        
        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "sk-invalid-key"}):
            creds = Credentials()
            
            with pytest.raises(RuntimeError, match="Unauthorized to Anthropic /v1/models"):
                ingest_models(creds)

def test_ingest_models_empty_response():
    """Test Anthropic ingestion with empty model list."""
    mock_response = {"data": []}
    
    with respx.mock:
        respx.get("https://api.anthropic.com/v1/models").mock(
            return_value=respx.MockResponse(200, json=mock_response)
        )
        
        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "sk-test-key"}):
            creds = Credentials()
            catalog = ingest_models(creds)
            
            assert len(catalog.calls) == 0
