"""
Provider adapters for LLM access.

This module provides adapter registry and implementations for various
LLM providers including OpenAI, Anthropic, and local models.

Author: Anjan Goswami
"""

from .local_echo import LocalEchoAdapter
from .openai import OpenaiAdapter
from .ollama import OllamaAdapter
from .openai_image import OpenAIImageAdapter
from .google_image import GoogleImageAdapter
from .seedream_image import SeedreamImageAdapter

# Adapter registry
REGISTRY = {
    "local": LocalEchoAdapter(),
    "openai": OpenaiAdapter(),
    "ollama": OllamaAdapter(),
    "openai_image": OpenAIImageAdapter(),
    "google_image": GoogleImageAdapter(),
    "seedream_image": SeedreamImageAdapter(),
}

def get_adapter(name: str):
    """Get adapter by name."""
    if name not in REGISTRY:
        raise ValueError(f"Unknown adapter: {name}")
    return REGISTRY[name]
