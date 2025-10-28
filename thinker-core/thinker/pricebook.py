"""
Pricebook for Thinker Core.

This module provides cost calculation functionality based on model pricing
and token usage.

Author: Anjan Goswami
"""

from typing import Dict, Any
from .registry.schema import RegistryCall

class PriceBook:
    """Pricebook for calculating costs based on model pricing."""
    
    def __init__(self, pricing_data: Dict[str, Any] = None):
        """Initialize pricebook with pricing data."""
        self.pricing_data = pricing_data or {}
    
    @classmethod
    def from_file(cls, file_path: str) -> 'PriceBook':
        """Load pricebook from file."""
        import json
        from pathlib import Path
        
        path = Path(file_path)
        if path.exists():
            with open(path) as f:
                data = json.load(f)
        else:
            data = {}
        
        return cls(data)
    
    def cost(self, call: RegistryCall, input_tokens: int, output_tokens: int) -> float:
        """Calculate cost for a call based on token usage."""
        # Use pricing from the registry call
        input_cost = (input_tokens / 1000) * call.price.input_per_1k
        output_cost = (output_tokens / 1000) * call.price.output_per_1k
        
        return input_cost + output_cost
