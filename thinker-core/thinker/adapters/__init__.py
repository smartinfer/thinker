"""
Provider adapters for LLM access.

This module provides adapter registry and implementations for various
LLM providers including OpenAI, Anthropic, and local models.

Author: Anjan Goswami
"""

from .base import ImageAdapter
from .local_echo import LocalEchoAdapter
from .openai import OpenaiAdapter
from .ollama import OllamaAdapter
from .openai_image import OpenAIImageAdapter
from .google_image import GoogleImageAdapter
from .seedream_image import SeedreamImageAdapter

# Adapter class registry. Adapters are instantiated per call so endpoint
# configuration cannot leak between registry entries.
REGISTRY = {
    "local": LocalEchoAdapter,
    "openai": OpenaiAdapter,
    "ollama": OllamaAdapter,
    "openai_image": OpenAIImageAdapter,
    "google_image": GoogleImageAdapter,
    "seedream_image": SeedreamImageAdapter,
}

def get_adapter(name: str, endpoint: str | None = None):
    """Create an adapter for one registry call."""
    if name not in REGISTRY:
        raise ValueError(f"Unknown adapter: {name}")
    adapter_class = REGISTRY[name]
    if name == "local" or issubclass(adapter_class, ImageAdapter):
        return adapter_class()
    return adapter_class(base_url=endpoint)
