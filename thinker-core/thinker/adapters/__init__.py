"""
Provider adapters for LLM access.

This module provides adapter registry and implementations for various
LLM providers including OpenAI, Anthropic, and local models.

Author: Anjan Goswami
"""

from .local_echo import LocalEchoAdapter
from .openai import OpenaiAdapter

# Adapter registry
REGISTRY = {
    "local": LocalEchoAdapter(),
    "openai": OpenaiAdapter()
}

def get_adapter(name: str):
    """Get adapter by name."""
    if name not in REGISTRY:
        raise ValueError(f"Unknown adapter: {name}")
    return REGISTRY[name]
