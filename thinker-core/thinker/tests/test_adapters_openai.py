"""
Unit tests for OpenAI adapter.

This module tests the OpenAI adapter including message mapping,
image handling, usage counting, and JSON mode support.

Author: Anjan Goswami
"""

import pytest
from unittest.mock import Mock, patch
import os
import respx
import httpx
from thinker.adapters import get_adapter
from thinker.adapters.base import AdapterResponse
from thinker.adapters.openai import OpenaiAdapter
from thinker.thinkerql.parse import InternalRequest
from thinker.registry.schema import RegistryCall, Limits, Price

@pytest.fixture
def mock_call():
    """Create a mock registry call."""
    return RegistryCall(
        call_id="openai:gpt-4o-mini.chat",
        provider="openai",
        model_id="gpt-4o-mini",
        kind="chat",
        modality="multimodal",
        caps=["json_mode", "tools", "images_in"],
        limits=Limits(max_input_tokens=128000, max_output_tokens=16384),
        price=Price(input_per_1k=0.00015, output_per_1k=0.00060),
        adapter="openai",
        payload_style="chat_completions_v1"
    )

@pytest.fixture
def mock_request():
    """Create a mock internal request."""
    return InternalRequest(
        messages=[
            {
                "role": "user",
                "parts": [
                    {"type": "text", "text": "Hello, world!"}
                ]
            }
        ],
        documents=[],
        instructions={},
        routing={},
        need_modality="text",
        need_caps=[],
        intent="chat"
    )

def test_openai_adapter_basic_text(mock_call, mock_request):
    """Test basic text message processing."""
    adapter = OpenaiAdapter()
    
    with patch.dict('os.environ', {'OPENAI_API_KEY': 'sk-test-key'}):
        with respx.mock:
            # Mock OpenAI API response
            respx.post("https://api.openai.com/v1/chat/completions").mock(
                return_value=httpx.Response(
                    200,
                    json={
                        "choices": [{"message": {"content": "Hello! How can I help you?"}}],
                        "usage": {"prompt_tokens": 10, "completion_tokens": 8}
                    }
                )
            )
            
            response = adapter.chat(mock_request, mock_call)
            
            assert not isinstance(response, Mock)
            assert isinstance(response, AdapterResponse)
            assert response.text == "Hello! How can I help you?"
            assert response.tokens["input"] == 10
            assert response.tokens["output"] == 8
            assert response.model == "gpt-4o-mini"
            assert response.provider == "openai"

def test_openai_adapter_uses_custom_endpoint_without_api_key(mock_call, mock_request):
    """A registry endpoint reaches an OpenAI-compatible local server."""
    adapter = OpenaiAdapter("http://127.0.0.1:8080")

    with patch.dict('os.environ', {}, clear=True):
        with respx.mock:
            route = respx.post("http://127.0.0.1:8080/v1/chat/completions").mock(
                return_value=httpx.Response(
                    200,
                    json={
                        "choices": [{"message": {"content": "local response"}}],
                        "usage": {"prompt_tokens": 3, "completion_tokens": 2},
                    },
                )
            )

            response = adapter.chat(mock_request, mock_call)

            assert route.called
            assert "authorization" not in route.calls[0].request.headers
            assert response.text == "local response"

def test_get_adapter_creates_fresh_endpoint_scoped_instances():
    """Adapter instances and endpoint state are scoped to one call."""
    first = get_adapter("openai", "http://127.0.0.1:8080")
    second = get_adapter("openai", "http://127.0.0.1:9090")

    assert first is not second
    assert first.base_url == "http://127.0.0.1:8080"
    assert second.base_url == "http://127.0.0.1:9090"

def test_openai_adapter_image_mapping(mock_call):
    """Test image message mapping to OpenAI format."""
    adapter = OpenaiAdapter()
    
    request = InternalRequest(
        messages=[
            {
                "role": "user",
                "parts": [
                    {"type": "text", "text": "What's in this image?"},
                    {"type": "image_url", "image_url": {"url": "https://example.com/image.jpg"}}
                ]
            }
        ],
        documents=[],
        instructions={},
        routing={},
        need_modality="multimodal",
        need_caps=["images_in"],
        intent="chat"
    )
    
    with patch.dict('os.environ', {'OPENAI_API_KEY': 'sk-test-key'}):
        with respx.mock:
            respx.post("https://api.openai.com/v1/chat/completions").mock(
                return_value=httpx.Response(
                    200,
                    json={
                        "choices": [{"message": {"content": "I can see an image"}}],
                        "usage": {"prompt_tokens": 15, "completion_tokens": 6}
                    }
                )
            )
            
            response = adapter.chat(request, mock_call)
            
            # Verify the request was made with correct format
            request_data = respx.calls[0].request.content
            assert b"image_url" in request_data
            assert b"https://example.com/image.jpg" in request_data

def test_openai_adapter_json_mode(mock_call):
    """Test JSON mode header and field handling."""
    adapter = OpenaiAdapter()
    
    request = InternalRequest(
        messages=[
            {
                "role": "user",
                "parts": [{"type": "text", "text": "Generate JSON"}]
            }
        ],
        documents=[],
        instructions={
            "json_schema": {
                "type": "object",
                "properties": {"result": {"type": "string"}}
            }
        },
        routing={},
        need_modality="text",
        need_caps=["json_mode"],
        intent="chat"
    )
    
    with patch.dict('os.environ', {'OPENAI_API_KEY': 'sk-test-key'}):
        with respx.mock:
            respx.post("https://api.openai.com/v1/chat/completions").mock(
                return_value=httpx.Response(
                    200,
                    json={
                        "choices": [{"message": {"content": '{"result": "success"}'}}],
                        "usage": {"prompt_tokens": 12, "completion_tokens": 10}
                    }
                )
            )
            
            response = adapter.chat(request, mock_call)
            
            # Verify JSON mode was enabled
            request_data = respx.calls[0].request.content
            assert b"response_format" in request_data
            assert b"json_object" in request_data

def test_openai_adapter_usage_counting(mock_call, mock_request):
    """Test proper usage token counting."""
    adapter = OpenaiAdapter()
    
    with patch.dict('os.environ', {'OPENAI_API_KEY': 'sk-test-key'}):
        with respx.mock:
            respx.post("https://api.openai.com/v1/chat/completions").mock(
                return_value=httpx.Response(
                    200,
                    json={
                        "choices": [{"message": {"content": "Response text"}}],
                        "usage": {"prompt_tokens": 25, "completion_tokens": 12}
                    }
                )
            )
            
            response = adapter.chat(mock_request, mock_call)
            
            assert response.tokens["input"] == 25
            assert response.tokens["output"] == 12

def test_openai_adapter_error_handling(mock_call, mock_request):
    """Test error handling for API failures."""
    adapter = OpenaiAdapter()
    
    with patch.dict('os.environ', {'OPENAI_API_KEY': 'sk-test-key'}):
        with respx.mock:
            respx.post("https://api.openai.com/v1/chat/completions").mock(
                return_value=httpx.Response(500, json={"error": "Internal server error"})
            )
            
            with pytest.raises(Exception, match="OpenAI API error"):
                adapter.chat(mock_request, mock_call)
