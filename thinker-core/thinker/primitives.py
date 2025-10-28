"""
Primitive functions for Thinker Core.

This module provides batch processing and other primitive functions
for handling multiple ThinkerQL requests efficiently.

Author: Anjan Goswami
"""

from typing import List, Dict, Any, Union
from .core import Thinker

def map_chat(thinker: Thinker, ql_requests: List[Dict[str, Any]], budget_usd: float = None) -> List[Union[Any, Exception]]:
    """
    Process multiple ThinkerQL requests in order.
    
    Args:
        thinker: Thinker instance to use for processing
        ql_requests: List of ThinkerQL request dictionaries
        budget_usd: Optional budget limit in USD
        
    Returns:
        List of responses or exceptions, preserving order
        
    Raises:
        Exception: If budget is exceeded before processing all requests
    """
    if not ql_requests:
        return []
    
    results = []
    total_cost = 0.0
    
    for i, ql in enumerate(ql_requests):
        try:
            # Process the request first
            response = thinker.chat_ql(ql)
            
            # Check budget after processing
            if budget_usd is not None:
                if hasattr(response, 'cost_usd'):
                    total_cost += response.cost_usd
                    if total_cost > budget_usd:
                        raise Exception(f"Budget exceeded: {total_cost:.4f} > {budget_usd}")
            
            results.append(response)
            
        except Exception as e:
            # If it's a budget exception, re-raise it
            if budget_usd is not None and "Budget exceeded" in str(e):
                raise e
            # Otherwise, capture error but continue processing
            results.append(e)
    
    return results
