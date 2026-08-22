"""
Authentication layer for Thinker Core.

This module provides authenticated HTTP clients for various LLM providers
including OpenAI, Anthropic, Together, Mistral, and Google Gemini. Handles
credential management from environment variables and JSON files with proper
error handling and no secret leakage.

Author: Anjan Goswami
"""

import os
import json
from pathlib import Path
import httpx
from .secure_credentials import SecureCredentials

class Credentials:
    """Manages API credentials from environment variables and JSON files."""
    
    def __init__(self, secrets_path: str | None = None):
        self._secrets = {}
        if secrets_path and Path(secrets_path).exists():
            self._secrets = json.loads(Path(secrets_path).read_text())
    
    def get(self, provider: str, key_name: str = "api_key") -> str | None:
        """Get API key for provider from environment or secrets file."""
        env = {
            "openai": "OPENAI_API_KEY",
            "anthropic": "ANTHROPIC_API_KEY", 
            "together": "TOGETHER_API_KEY",
            "mistral": "MISTRAL_API_KEY",
            "google": "GOOGLE_API_KEY",
            "bytedance": "ARK_API_KEY",
            "seedream": "ARK_API_KEY"
        }.get(provider)
        
        if env and os.getenv(env):
            return os.getenv(env)
        
        return self._secrets.get(provider, {}).get(key_name)


def get_credentials(storage_type: str = "auto") -> Credentials:
    """
    Get credentials instance with secure storage fallback.
    
    Args:
        storage_type: Storage type ("keyring", "encrypted_file", "env", or "auto")
        
    Returns:
        Credentials instance
    """
    if storage_type in ["keyring", "encrypted_file", "auto"]:
        return SecureCredentials(storage_type=storage_type)
    else:
        return Credentials()

def openai_client(creds: Credentials, timeout: float = 20.0) -> httpx.Client:
    """Create authenticated OpenAI client."""
    key = creds.get("openai")
    assert key, "OPENAI_API_KEY not set"
    
    base = os.getenv("OPENAI_BASE", "https://api.openai.com")
    return httpx.Client(
        base_url=base,
        headers={"Authorization": f"Bearer {key}"},
        timeout=timeout
    )

def anthropic_client(creds: Credentials, timeout: float = 20.0) -> httpx.Client:
    """Create authenticated Anthropic client."""
    key = creds.get("anthropic")
    assert key, "ANTHROPIC_API_KEY not set"
    
    base = os.getenv("ANTHROPIC_BASE", "https://api.anthropic.com")
    return httpx.Client(
        base_url=base,
        headers={"x-api-key": key},
        timeout=timeout
    )

def together_client(creds: Credentials, timeout: float = 20.0) -> httpx.Client:
    """Create authenticated Together client."""
    key = creds.get("together")
    assert key, "TOGETHER_API_KEY not set"
    
    base = os.getenv("TOGETHER_BASE", "https://api.together.xyz")
    return httpx.Client(
        base_url=base,
        headers={"Authorization": f"Bearer {key}"},
        timeout=timeout
    )

def mistral_client(creds: Credentials, timeout: float = 20.0) -> httpx.Client:
    """Create authenticated Mistral client."""
    key = creds.get("mistral")
    assert key, "MISTRAL_API_KEY not set"
    
    base = os.getenv("MISTRAL_BASE", "https://api.mistral.ai")
    return httpx.Client(
        base_url=base,
        headers={"Authorization": f"Bearer {key}"},
        timeout=timeout
    )

def gemini_client(creds: Credentials, timeout: float = 20.0) -> httpx.Client:
    """Create authenticated Google Gemini client."""
    key = creds.get("google")
    assert key, "GOOGLE_API_KEY not set"
    
    base = os.getenv("GOOGLE_BASE", "https://generativelanguage.googleapis.com")
    return httpx.Client(
        base_url=base,
        params={"key": key},
        timeout=timeout
    )


def _test_provider_key(provider: str, key: str) -> tuple[bool, str]:
    """
    Test if a provider key works by making a test API call.
    
    Args:
        provider: Provider name
        key: API key to test
        
    Returns:
        (success, message) tuple
    """
    try:
        if provider == "openai":
            with httpx.Client() as client:
                response = client.get(
                    "https://api.openai.com/v1/models",
                    headers={"Authorization": f"Bearer {key}"},
                    timeout=10.0
                )
                if response.status_code == 200:
                    return True, "OpenAI key is valid"
                elif response.status_code == 401:
                    return False, "OpenAI key is invalid or expired"
                else:
                    return False, f"OpenAI API error: {response.status_code}"
        
        elif provider == "anthropic":
            with httpx.Client() as client:
                response = client.get(
                    "https://api.anthropic.com/v1/models",
                    headers={"x-api-key": key, "anthropic-version": "2023-06-01"},
                    timeout=10.0
                )
                if response.status_code == 200:
                    return True, "Anthropic key is valid"
                elif response.status_code == 401:
                    return False, "Anthropic key is invalid or expired"
                else:
                    return False, f"Anthropic API error: {response.status_code}"
        
        elif provider == "google":
            with httpx.Client() as client:
                response = client.get(
                    "https://generativelanguage.googleapis.com/v1beta/models",
                    params={"key": key},
                    timeout=10.0
                )
                if response.status_code == 200:
                    return True, "Google key is valid"
                elif response.status_code == 401:
                    return False, "Google key is invalid or expired"
                else:
                    return False, f"Google API error: {response.status_code}"
        
        else:
            return False, f"Unknown provider: {provider}"
            
    except httpx.TimeoutException:
        return False, "API test timed out"
    except httpx.ConnectError:
        return False, "Failed to connect to API"
    except Exception as e:
        return False, f"Test failed: {str(e)}"
