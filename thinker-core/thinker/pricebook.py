"""
Pricebook for Thinker Core.

This module provides cost calculation functionality based on model pricing
and token usage.

Author: Anjan Goswami
"""

from .registry.schema import RegistryCall

class PriceBook:
    """Pricebook for calculating costs based on model pricing."""
    
    def cost(self, call: RegistryCall, input_tokens: int, output_tokens: int) -> float:
        """Calculate cost using the authoritative price on the registry call."""
        input_cost = (input_tokens / 1000) * call.price.input_per_1k
        output_cost = (output_tokens / 1000) * call.price.output_per_1k
        
        return input_cost + output_cost
