"""
Base adapter class for Thinker Core.

This module provides the base adapter interface that all provider
adapters must implement.

Author: Anjan Goswami
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

@dataclass
class AdapterResponse:
    """Normalized production response returned by provider adapters."""

    text: str
    tokens: Dict[str, int]
    model: str
    provider: str
    cost_usd: float = 0.0
    autoshrink_trace: Optional[List[Dict[str, Any]]] = None
    error: Optional[str] = None
    execution: Optional[Dict[str, Any]] = None
    qualification: Optional[Dict[str, Any]] = None

class BaseAdapter(ABC):
    """Base class for all provider adapters."""
    
    @abstractmethod
    def chat(self, request: Any, call: Any) -> Any:
        """Process a chat request."""
        pass


class ImageAdapter(ABC):
    """Provider-neutral interface for image-generation adapters."""

    @abstractmethod
    def generate_image(self, request: Any, call: Any) -> Any:
        """Execute one external image-generation request."""
        pass
