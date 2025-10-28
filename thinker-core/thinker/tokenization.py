"""
Tokenization utilities for Thinker Core.

This module provides token counting functionality for ThinkerQL requests
and responses, supporting various tokenization methods.

Author: Anjan Goswami
"""

import tiktoken
from typing import Dict, Any, List

def count_tokens(request: Any) -> int:
    """Count tokens in a ThinkerQL request."""
    # Simple implementation - count tokens in all text content
    total_tokens = 0
    
    if hasattr(request, 'messages'):
        for message in request.messages:
            for part in message.get("parts", []):
                if part.get("type") == "text":
                    text = part.get("text", "")
                    total_tokens += len(text.split())  # Rough word-based estimate
    
    if hasattr(request, 'documents'):
        for doc in request.documents:
            content = doc.get("content", "")
            total_tokens += len(content.split())  # Rough word-based estimate
    
    return total_tokens

def truncate_last_user(request: Any) -> Any:
    """Truncate the last user message to reduce token count."""
    if not hasattr(request, 'messages') or not request.messages:
        return request
    
    # Find the last user message
    last_user_idx = -1
    for i in range(len(request.messages) - 1, -1, -1):
        if request.messages[i].get("role") == "user":
            last_user_idx = i
            break
    
    if last_user_idx == -1:
        return request
    
    # Create a copy of the request to avoid modifying the original
    from copy import deepcopy
    truncated_request = deepcopy(request)
    
    # Truncate the last user message
    last_message = truncated_request.messages[last_user_idx]
    for part in last_message.get("parts", []):
        if part.get("type") == "text":
            text = part.get("text", "")
            # Truncate to roughly half the length
            words = text.split()
            if len(words) > 10:  # Only truncate if reasonably long
                truncated_text = " ".join(words[:len(words)//2])
                part["text"] = truncated_text
    
    return truncated_request

def validate_json_schema(response_text: str, schema: Dict[str, Any]) -> bool:
    """Validate response text against JSON schema."""
    import json
    import jsonschema
    
    try:
        # Try to parse as JSON
        data = json.loads(response_text)
        
        # Validate against schema
        jsonschema.validate(data, schema)
        return True
    except (json.JSONDecodeError, jsonschema.ValidationError):
        return False
