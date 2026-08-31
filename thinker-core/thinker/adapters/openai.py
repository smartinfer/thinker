"""
OpenAI adapter for Thinker Core.

This module provides an adapter for OpenAI's chat completions API,
handling message mapping, image processing, and JSON mode.

Author: Anjan Goswami
"""

import httpx
from typing import Dict, Any, List
from .base import AdapterResponse, BaseAdapter

class OpenaiAdapter(BaseAdapter):
    """OpenAI adapter for chat completions."""
    
    def __init__(self, base_url: str | None = None):
        """Initialize OpenAI adapter."""
        self.base_url = (base_url or "https://api.openai.com").rstrip("/")

    def _chat_url(self) -> str:
        if self.base_url.endswith("/v1/chat/completions"):
            return self.base_url
        return f"{self.base_url}/v1/chat/completions"
    
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
        headers = {}
        if self.base_url == "https://api.openai.com":
            headers["Authorization"] = f"Bearer {self._get_api_key()}"
        with httpx.Client() as client:
            response = client.post(
                self._chat_url(),
                json=payload,
                headers=headers,
                timeout=30.0
            )
            
            if response.status_code != 200:
                raise Exception(f"OpenAI API error: {response.status_code} - {response.text}")
            
            data = response.json()
            
            # Extract response
            choice = data["choices"][0]
            usage = data["usage"]
            
            return AdapterResponse(
                text=choice["message"]["content"],
                tokens={
                    "input": usage["prompt_tokens"],
                    "output": usage["completion_tokens"],
                },
                model=call.model_id,
                provider=call.provider,
            )
    
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
    
    def _get_api_key(self, required: bool = True) -> str | None:
        """Get OpenAI API key from env or Thinker secure storage."""
        import os
        key = os.getenv("OPENAI_API_KEY")
        if not key:
            # Try secure keyring/encrypted store first
            try:
                from thinker.registry.secure_credentials import SecureCredentials
                key = SecureCredentials().get("openai")
            except Exception:
                key = None
        if not key:
            # Fallback to legacy plain Credentials (env/json)
            try:
                from thinker.registry.auth import Credentials
                key = Credentials().get("openai")
            except Exception:
                key = None
        if not key and required:
            raise Exception("OPENAI_API_KEY environment variable not set")
        return key
