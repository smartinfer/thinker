"""
Thinker Core - Registry-driven LLM access with ThinkerQL specification.

This package provides a unified interface for accessing various LLM providers
through a single request language (ThinkerQL) and a registry-driven routing
system. The MVP supports OpenAI and local Ollama models with schema contracts
and comprehensive testing.

Author: Anjan Goswami
"""

from .core import Thinker

__all__ = ["Thinker"]
__version__ = "0.2.0"
