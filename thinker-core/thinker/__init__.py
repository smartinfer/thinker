"""
Thinker Core - Registry-driven LLM access with ThinkerQL specification.

This package provides a unified interface for accessing various LLM providers
through a single request language (ThinkerQL) and a registry-driven routing
system. The MVP supports OpenAI and local Ollama models with schema contracts
and comprehensive testing.

Author: Anjan Goswami
"""

from .core import Thinker
from .image_budget import BudgetLedger
from .image_ledger import CompletionLedger, request_fingerprint
from .image_models import (
    AttemptRecord,
    CostEstimate,
    ImageGenerationRequest,
    ImageGenerationResponse,
    ImageOutput,
    StructuredError,
)
from .image_pricing import estimate_image_request_cost
from .image_runtime import map_images

__all__ = [
    "Thinker", "ImageGenerationRequest", "ImageGenerationResponse", "ImageOutput",
    "AttemptRecord", "StructuredError", "CostEstimate", "CompletionLedger",
    "BudgetLedger", "request_fingerprint", "estimate_image_request_cost", "map_images",
]
__version__ = "0.3.0"
