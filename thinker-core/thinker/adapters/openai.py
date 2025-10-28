"""
OpenAI adapter for Thinker Core.

This module provides an adapter for OpenAI's chat completions API,
handling message mapping, image processing, and JSON mode.

Author: Anjan Goswami
"""

import httpx
import json
from typing import Dict, Any, List
from unittest.mock import Mock
from .base import BaseAdapter

class OpenaiAdapter(BaseAdapter):
    """OpenAI adapter for chat completions."""
    
    def __init__(self, base_url: str = "https://api.openai.com"):
        """Initialize OpenAI adapter."""
        self.base_url = base_url
    
    def chat(self, request: Any, call: Any) -> Any:
        """Process chat request with OpenAI API."""
        # Convert ThinkerQL messages to OpenAI format
        messages = self._convert_messages(request.messages)
        
        # Prepare request payload
        payload = {
            "model": call.model_id,
            "messages": messages,
            "max_tokens": request.instructions.get("max_output_tokens", 256),
            "temperature": request.instructions.get("temperature", 0.7)
        }
        
        # Add JSON mode if requested
        if request.instructions.get("json_schema"):
            payload["response_format"] = {"type": "json_object"}
        
        # Make API call
        with httpx.Client() as client:
            response = client.post(
                f"{self.base_url}/v1/chat/completions",
                json=payload,
                headers={"Authorization": f"Bearer {self._get_api_key()}"},
                timeout=30.0
            )
            
            if response.status_code != 200:
                raise Exception(f"OpenAI API error: {response.status_code} - {response.text}")
            
            data = response.json()
            
            # Extract response
            choice = data["choices"][0]
            usage = data["usage"]
            
            # Create response object
            result = Mock()
            result.text = choice["message"]["content"]
            result.tokens = {
                "input": usage["prompt_tokens"],
                "output": usage["completion_tokens"]
            }
            result.model = call.model_id
            result.provider = call.provider
            
            return result
    
    def _convert_messages(self, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Convert ThinkerQL messages to OpenAI format."""
        openai_messages = []
        
        for message in messages:
            role = message["role"]
            parts = message["parts"]
            
            # Convert parts to OpenAI format
            content = []
            for part in parts:
                if part["type"] == "text":
                    content.append({"type": "text", "text": part["text"]})
                elif part["type"] == "image_url":
                    content.append({
                        "type": "image_url",
                        "image_url": part["image_url"]
                    })
            
            openai_messages.append({
                "role": role,
                "content": content
            })
        
        return openai_messages
    
    def _get_api_key(self) -> str:
        """Get OpenAI API key from environment."""
        import os
        key = os.getenv("OPENAI_API_KEY")
        if not key:
            raise Exception("OPENAI_API_KEY environment variable not set")
        return key
