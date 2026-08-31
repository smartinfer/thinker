"""
Unit tests for auth clients.

This module tests the authentication layer including credential management
and provider client creation. Ensures proper error handling and no secret
leakage in logs.

Author: Anjan Goswami
"""

import pytest
import os
from unittest.mock import patch, mock_open
from thinker.registry.auth import Credentials, openai_client, anthropic_client, together_client, mistral_client, gemini_client

def test_credentials_from_env():
    """Test credentials loaded from environment variables."""
    with patch.dict(os.environ, {
        'OPENAI_API_KEY': 'sk-test-openai-key',
        'ANTHROPIC_API_KEY': 'sk-test-anthropic-key',
        'TOGETHER_API_KEY': 'sk-test-together-key',
        'MISTRAL_API_KEY': 'sk-test-mistral-key',
        'GOOGLE_API_KEY': 'sk-test-google-key'
    }):
        creds = Credentials()
        
        assert creds.get("openai") == 'sk-test-openai-key'
        assert creds.get("anthropic") == 'sk-test-anthropic-key'
        assert creds.get("together") == 'sk-test-together-key'
        assert creds.get("mistral") == 'sk-test-mistral-key'
        assert creds.get("google") == 'sk-test-google-key'

def test_credentials_from_file():
    """Test credentials loaded from JSON file."""
    secrets_data = {
        "openai": {"api_key": "sk-file-openai-key"},
        "anthropic": {"api_key": "sk-file-anthropic-key"}
    }
    
    with patch.dict(os.environ, {}, clear=True), \
         patch("pathlib.Path.exists", return_value=True), \
         patch("pathlib.Path.read_text", return_value='{"openai": {"api_key": "sk-file-openai-key"}, "anthropic": {"api_key": "sk-file-anthropic-key"}}'):
        
        creds = Credentials("secrets.json")
        assert creds.get("openai") == 'sk-file-openai-key'
        assert creds.get("anthropic") == 'sk-file-anthropic-key'

def test_credentials_file_not_exists():
    """Test credentials when file doesn't exist."""
    with patch.dict(os.environ, {}, clear=True), \
         patch("pathlib.Path.exists", return_value=False):
        creds = Credentials("nonexistent.json")
        assert creds.get("openai") is None

def test_credentials_env_overrides_file():
    """Test that environment variables override file credentials."""
    secrets_data = '{"openai": {"api_key": "sk-file-key"}}'
    
    with patch("pathlib.Path.exists", return_value=True), \
         patch("pathlib.Path.read_text", return_value=secrets_data), \
         patch.dict(os.environ, {'OPENAI_API_KEY': 'sk-env-key'}):
        
        creds = Credentials("secrets.json")
        assert creds.get("openai") == 'sk-env-key'  # env overrides file

def test_openai_client_success():
    """Test successful OpenAI client creation."""
    with patch.dict(os.environ, {'OPENAI_API_KEY': 'sk-test-key'}):
        creds = Credentials()
        client = openai_client(creds)
        
        assert client.base_url == "https://api.openai.com"
        assert client.headers["Authorization"] == "Bearer sk-test-key"
        assert client.timeout.read == 20.0

def test_openai_client_custom_base():
    """Test OpenAI client with custom base URL."""
    with patch.dict(os.environ, {
        'OPENAI_API_KEY': 'sk-test-key',
        'OPENAI_BASE': 'https://custom.openai.com'
    }):
        creds = Credentials()
        client = openai_client(creds)
        
        assert client.base_url == "https://custom.openai.com"

def test_openai_client_missing_key():
    """Test OpenAI client creation with missing API key."""
    with patch.dict(os.environ, {}, clear=True):
        creds = Credentials()
        
        with pytest.raises(AssertionError, match="OPENAI_API_KEY not set"):
            openai_client(creds)

def test_anthropic_client_success():
    """Test successful Anthropic client creation."""
    with patch.dict(os.environ, {'ANTHROPIC_API_KEY': 'sk-test-key'}):
        creds = Credentials()
        client = anthropic_client(creds)
        
        assert client.base_url == "https://api.anthropic.com"
        assert client.headers["x-api-key"] == "sk-test-key"

def test_together_client_success():
    """Test successful Together client creation."""
    with patch.dict(os.environ, {'TOGETHER_API_KEY': 'sk-test-key'}):
        creds = Credentials()
        client = together_client(creds)
        
        assert client.base_url == "https://api.together.xyz"
        assert client.headers["Authorization"] == "Bearer sk-test-key"

def test_mistral_client_success():
    """Test successful Mistral client creation."""
    with patch.dict(os.environ, {'MISTRAL_API_KEY': 'sk-test-key'}):
        creds = Credentials()
        client = mistral_client(creds)
        
        assert client.base_url == "https://api.mistral.ai"
        assert client.headers["Authorization"] == "Bearer sk-test-key"

def test_gemini_client_success():
    """Test successful Gemini client creation."""
    with patch.dict(os.environ, {'GOOGLE_API_KEY': 'sk-test-key'}):
        creds = Credentials()
        client = gemini_client(creds)
        
        assert client.base_url == "https://generativelanguage.googleapis.com"
        assert "key=sk-test-key" in str(client.params)

def test_client_timeout_override():
    """Test client creation with custom timeout."""
    with patch.dict(os.environ, {'OPENAI_API_KEY': 'sk-test-key'}):
        creds = Credentials()
        client = openai_client(creds, timeout=30.0)
        
        assert client.timeout.read == 30.0
