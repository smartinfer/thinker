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

class BaseAdapter(ABC):
    """Base class for all provider adapters."""
    
    @abstractmethod
    def chat(self, request: Any, call: Any) -> Any:
        """Process a chat request."""
        pass
