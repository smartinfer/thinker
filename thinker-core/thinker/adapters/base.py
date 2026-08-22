"""
Base adapter class for Thinker Core.

This module provides the base adapter interface that all provider
adapters must implement.

Author: Anjan Goswami
"""

from abc import ABC, abstractmethod
from typing import Any

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
