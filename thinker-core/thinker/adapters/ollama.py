"""
Ollama adapter for Thinker Core.

This adapter sends chat requests to a local Ollama server, supporting
any locally available model (e.g., qwen2.5, phi4, llama, etc.).

Author: Anjan Goswami
"""

import os
from typing import Any, Dict, List
import httpx
from unittest.mock import Mock

from .base import BaseAdapter


class OllamaAdapter(BaseAdapter):
    """Adapter to interact with Ollama's chat API."""

    def __init__(self, base_url: str | None = None):
        # Prefer 127.0.0.1 to avoid some resolver delays on localhost
        self.base_url = base_url or os.getenv("OLLAMA_BASE", "http://127.0.0.1:11434")

    def _convert_messages(self, messages: List[Dict[str, Any]]) -> List[Dict[str, str]]:
        """Convert ThinkerQL-style messages to Ollama's role/content format."""
        out: List[Dict[str, str]] = []
        for m in messages:
            role = m.get("role", "user")
            # Concatenate text parts; ignore images for now (basic MVP)
            parts = m.get("parts", [])
            content_texts = []
            for p in parts:
                if p.get("type") == "text":
                    content_texts.append(p.get("text", ""))
            content = "\n".join([t for t in content_texts if t])
            out.append({"role": role, "content": content})
        return out

    def chat(self, request: Any, call: Any) -> Any:
        """Send a chat request to Ollama and return a Thinker-style response."""
        messages = self._convert_messages(request.messages)

        # Allow GPU hint & keep-alive via envs; fallback safe defaults
        num_gpu = os.getenv("OLLAMA_NUM_GPU")
        try:
            num_gpu_val = int(num_gpu) if num_gpu is not None else None
        except ValueError:
            num_gpu_val = None

        payload: Dict[str, Any] = {
            "model": call.model_id,  # e.g., "qwen2.5:1.5b", "phi4:14b"
            "messages": messages,
            "options": {
                "num_ctx": min(call.limits.max_input_tokens, 32768),
                "temperature": request.instructions.get("temperature", 0.7),
            },
            "stream": False,
            # Keep model in memory for a few minutes to avoid reload cost
            "keep_alive": os.getenv("OLLAMA_KEEP_ALIVE", "5m"),
        }

        if num_gpu_val is not None and num_gpu_val >= 0:
            payload["options"]["num_gpu"] = num_gpu_val

        url = f"{self.base_url}/api/chat"
        # Large models can take time on first load; allow longer timeout or env override
        timeout_s = float(os.getenv("OLLAMA_TIMEOUT", "300"))
        with httpx.Client(timeout=timeout_s) as client:
            resp = client.post(url, json=payload)
            if resp.status_code != 200:
                raise Exception(f"Ollama API error: {resp.status_code} - {resp.text}")
            data = resp.json()

        # Ollama returns response in { message: {role, content}, eval_count, prompt_eval_count }
        msg = data.get("message", {})
        content = msg.get("content", "")
        input_tokens = int(data.get("prompt_eval_count", 0) or 0)
        output_tokens = int(data.get("eval_count", 0) or 0)

        result = Mock()
        result.text = content
        result.tokens = {"input": input_tokens, "output": output_tokens}
        result.model = call.model_id
        result.provider = call.provider
        result.cost_usd = 0.0  # local models assumed $0
        result.autoshrink_trace = None
        result.error = None
        return result


