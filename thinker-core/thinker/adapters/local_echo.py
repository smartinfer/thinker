"""
Local echo adapter for Thinker Core.

This module provides a simple echo adapter that returns the last user message
prefixed with "echo:" for testing and development purposes.

Author: Anjan Goswami
"""

from typing import Dict, Any
from unittest.mock import Mock
from .base import BaseAdapter

class LocalEchoAdapter(BaseAdapter):
    """Local echo adapter for testing."""
    
    def chat(self, request: Any, call: Any) -> Any:
        """Echo the last user message."""
        # Find the last user message
        last_user_text = ""
        for message in reversed(request.messages):
            if message.get("role") == "user":
                for part in message.get("parts", []):
                    if part.get("type") == "text":
                        last_user_text = part.get("text", "")
                        break
                break
        
        # Create response object
        response = Mock()
        response.text = f"echo:{last_user_text}"
        
        # Estimate tokens properly
        input_tokens = self._estimate_tokens(request)
        output_tokens = len(response.text.split())  # Count words in response
        
        response.tokens = {
            "input": input_tokens,
            "output": output_tokens
        }
        response.model = call.model_id
        response.provider = call.provider
        
        return response
    
    def _estimate_tokens(self, content: Any) -> int:
        """Estimate token count for content."""
        if hasattr(content, 'messages'):
            # Count tokens in messages
            total = 0
            for message in content.messages:
                for part in message.get("parts", []):
                    if part.get("type") == "text":
                        text = part.get("text", "")
                        total += len(text.split())  # Rough word-based estimate
            return total
        elif hasattr(content, 'text'):
            # Count tokens in text
            return len(content.text.split())
        elif isinstance(content, dict) and 'text' in content:
            # Handle dict with text field
            return len(content['text'].split())
        else:
            return 0
