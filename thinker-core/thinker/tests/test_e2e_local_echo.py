"""
End-to-end tests for local echo adapter.

This module tests the complete flow from ThinkerQL request to local echo
adapter response, including token estimation and cost calculation.

Author: Anjan Goswami
"""

import pytest
from unittest.mock import Mock
from thinker.core import Thinker
from thinker.registry.store import RegistryStore
from thinker.registry.schema import RegistryCall, Limits, Price
from thinker.pricebook import PriceBook

@pytest.fixture
def echo_store():
    """Create a registry store with echo model."""
    store = RegistryStore()
    
    call = RegistryCall(
        call_id="local:echo.chat",
        provider="local",
        model_id="echo",
        kind="chat",
        modality="text",
        caps=["json_mode"],
        limits=Limits(max_input_tokens=8192, max_output_tokens=1024),
        price=Price(input_per_1k=0.0, output_per_1k=0.0),
        adapter="local",
        payload_style="echo"
    )
    store.put_call(call)
    
    return store

@pytest.fixture
def pricebook():
    """Create a pricebook."""
    return PriceBook()

def test_e2e_local_echo_basic(echo_store, pricebook):
    """Test basic end-to-end local echo functionality."""
    thinker = Thinker(echo_store, pricebook)
    
    ql = {
        "version": "0.3",
        "intent": "chat",
        "messages": [
            {
                "role": "user",
                "parts": [{"type": "text", "text": "Hello, world!"}]
            }
        ],
        "routing": {"call_id": "local:echo.chat"}
    }
    
    response = thinker.chat_ql(ql)
    
    assert response.text == "echo:Hello, world!"
    assert response.model == "echo"
    assert response.provider == "local"
    assert response.tokens["input"] > 0
    assert response.tokens["output"] > 0
    assert response.cost_usd == 0.0  # Local models are free

def test_e2e_local_echo_multiple_messages(echo_store, pricebook):
    """Test local echo with multiple messages."""
    thinker = Thinker(echo_store, pricebook)
    
    ql = {
        "version": "0.3",
        "intent": "chat",
        "messages": [
            {
                "role": "user",
                "parts": [{"type": "text", "text": "First message"}]
            },
            {
                "role": "assistant",
                "parts": [{"type": "text", "text": "echo:First message"}]
            },
            {
                "role": "user",
                "parts": [{"type": "text", "text": "Second message"}]
            }
        ],
        "routing": {"call_id": "local:echo.chat"}
    }
    
    response = thinker.chat_ql(ql)
    
    assert response.text == "echo:Second message"
    assert response.model == "echo"
    assert response.provider == "local"

def test_e2e_local_echo_json_mode(echo_store, pricebook):
    """Test local echo with JSON mode."""
    thinker = Thinker(echo_store, pricebook)
    
    ql = {
        "version": "0.3",
        "intent": "chat",
        "messages": [
            {
                "role": "user",
                "parts": [{"type": "text", "text": "Generate JSON"}]
            }
        ],
        "instructions": {
            "json_schema": {
                "type": "object",
                "properties": {"result": {"type": "string"}}
            }
        },
        "routing": {"call_id": "local:echo.chat"}
    }
    
    response = thinker.chat_ql(ql)
    
    assert response.text == "echo:Generate JSON"
    assert response.model == "echo"
    assert response.provider == "local"

def test_e2e_local_echo_token_estimation(echo_store, pricebook):
    """Test token estimation accuracy."""
    thinker = Thinker(echo_store, pricebook)
    
    ql = {
        "version": "0.3",
        "intent": "chat",
        "messages": [
            {
                "role": "user",
                "parts": [{"type": "text", "text": "This is a test message with multiple words"}]
            }
        ],
        "routing": {"call_id": "local:echo.chat"}
    }
    
    response = thinker.chat_ql(ql)
    
    # Token counts should be reasonable estimates
    assert response.tokens["input"] > 0
    assert response.tokens["output"] > 0
    assert response.tokens["input"] >= response.tokens["output"]  # Input should be >= output for echo

def test_e2e_local_echo_cost_calculation(echo_store, pricebook):
    """Test cost calculation for local models."""
    thinker = Thinker(echo_store, pricebook)
    
    ql = {
        "version": "0.3",
        "intent": "chat",
        "messages": [
            {
                "role": "user",
                "parts": [{"type": "text", "text": "Test message"}]
            }
        ],
        "routing": {"call_id": "local:echo.chat"}
    }
    
    response = thinker.chat_ql(ql)
    
    # Local models should have zero cost
    assert response.cost_usd == 0.0
