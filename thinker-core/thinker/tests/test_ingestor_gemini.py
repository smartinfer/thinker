"""
Unit tests for Gemini ingestor.

This module tests the Google Gemini model ingestion including API calls,
model normalization, and error handling. Uses mocked network requests
to avoid requiring real API keys.

Author: Anjan Goswami
"""

import pytest
import respx
from unittest.mock import patch
from thinker.registry.ingestors.gemini import ingest_models
from thinker.registry.auth import Credentials
from thinker.registry.schema import Catalog, RegistryCall


def test_ingest_models_success():
    """Test successful Gemini model ingestion."""
    mock_response = {
        "models": [
            {
                "name": "models/gemini-1.5-pro",
                "displayName": "Gemini 1.5 Pro",
                "description": "Multimodal model",
                "supportedGenerationMethods": ["generateContent"],
            },
            {
                "name": "models/gemini-1.5-flash",
                "displayName": "Gemini 1.5 Flash",
                "description": "Fast multimodal model",
                "supportedGenerationMethods": ["generateContent"],
            },
        ]
    }

    with respx.mock:
        respx.get("https://generativelanguage.googleapis.com/v1beta/models").mock(
            return_value=respx.MockResponse(200, json=mock_response)
        )

        with patch.dict("os.environ", {"GOOGLE_API_KEY": "sk-test-key"}):
            creds = Credentials()
            catalog = ingest_models(creds)

            assert len(catalog.calls) == 1  # Only gemini-1.5-pro

            # Check gemini-1-5-pro
            gemini_call = catalog.calls[0]
            assert gemini_call.call_id == "gemini:gemini-1-5-pro.chat"
            assert gemini_call.provider == "gemini"
            assert gemini_call.model_id == "gemini-1-5-pro"
            assert gemini_call.kind == "chat"
            assert gemini_call.modality == "multimodal"
            assert "images_in" in gemini_call.caps
            assert gemini_call.limits.max_input_tokens == 2000000
            assert gemini_call.limits.max_output_tokens == 8192
            assert gemini_call.price.input_per_1k == 0.00125
            assert gemini_call.price.output_per_1k == 0.005
            assert gemini_call.adapter == "gemini"
            assert gemini_call.payload_style == "generateContent_v1"
            assert "gemini:multimodal-cheap" in gemini_call.aliases


def test_ingest_models_unauthorized():
    """Test Gemini ingestion with 401 unauthorized error."""
    with respx.mock:
        respx.get("https://generativelanguage.googleapis.com/v1beta/models").mock(
            return_value=respx.MockResponse(401, json={"error": "Unauthorized"})
        )

        with patch.dict("os.environ", {"GOOGLE_API_KEY": "sk-invalid-key"}):
            creds = Credentials()

            with pytest.raises(RuntimeError, match="Unauthorized to Google /v1beta/models"):
                ingest_models(creds)


def test_ingest_models_empty_response():
    """Test Gemini ingestion with empty model list."""
    mock_response = {"models": []}

    with respx.mock:
        respx.get("https://generativelanguage.googleapis.com/v1beta/models").mock(
            return_value=respx.MockResponse(200, json=mock_response)
        )

        with patch.dict("os.environ", {"GOOGLE_API_KEY": "sk-test-key"}):
            creds = Credentials()
            catalog = ingest_models(creds)

            assert len(catalog.calls) == 0
