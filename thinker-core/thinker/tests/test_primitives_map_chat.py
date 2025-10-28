"""
Unit tests for batch map_chat functionality.

This module tests the batch processing of multiple ThinkerQL requests,
including order preservation, budget guards, and error handling.

Author: Anjan Goswami
"""

import pytest
from unittest.mock import Mock, patch
from thinker.primitives import map_chat
from thinker.core import Thinker
from thinker.registry.store import RegistryStore
from thinker.registry.schema import RegistryCall, Limits, Price
from thinker.pricebook import PriceBook

@pytest.fixture
def mock_thinker():
    """Create a mock Thinker instance."""
    store = RegistryStore()
    pricebook = PriceBook()
    thinker = Thinker(store, pricebook)
    
    # Mock the chat_ql method
    thinker.chat_ql = Mock()
    
    return thinker

@pytest.fixture
def sample_ql_requests():
    """Create sample ThinkerQL requests."""
    return [
        {
            "version": "0.3",
            "intent": "chat",
            "messages": [{"role": "user", "parts": [{"type": "text", "text": "Hello 1"}]}],
            "routing": {"call_id": "local:echo.chat"}
        },
        {
            "version": "0.3",
            "intent": "chat",
            "messages": [{"role": "user", "parts": [{"type": "text", "text": "Hello 2"}]}],
            "routing": {"call_id": "local:echo.chat"}
        },
        {
            "version": "0.3",
            "intent": "chat",
            "messages": [{"role": "user", "parts": [{"type": "text", "text": "Hello 3"}]}],
            "routing": {"call_id": "local:echo.chat"}
        }
    ]

def test_map_chat_order_preservation(mock_thinker, sample_ql_requests):
    """Test that map_chat preserves order of requests."""
    # Mock responses
    mock_responses = [
        Mock(text="echo:Hello 1", tokens={"input": 2, "output": 2}, cost_usd=0.0),
        Mock(text="echo:Hello 2", tokens={"input": 2, "output": 2}, cost_usd=0.0),
        Mock(text="echo:Hello 3", tokens={"input": 2, "output": 2}, cost_usd=0.0)
    ]
    
    mock_thinker.chat_ql.side_effect = mock_responses
    
    results = map_chat(mock_thinker, sample_ql_requests)
    
    # Verify order is preserved
    assert len(results) == 3
    assert results[0].text == "echo:Hello 1"
    assert results[1].text == "echo:Hello 2"
    assert results[2].text == "echo:Hello 3"
    
    # Verify all requests were called
    assert mock_thinker.chat_ql.call_count == 3

def test_map_chat_budget_guard(mock_thinker, sample_ql_requests):
    """Test budget guard rejects requests after exceeding budget."""
    # Mock responses with high cost
    mock_responses = [
        Mock(text="echo:Hello 1", tokens={"input": 2, "output": 2}, cost_usd=0.1),
        Mock(text="echo:Hello 2", tokens={"input": 2, "output": 2}, cost_usd=0.1),
        Mock(text="echo:Hello 3", tokens={"input": 2, "output": 2}, cost_usd=0.1)
    ]
    
    mock_thinker.chat_ql.side_effect = mock_responses
    
    # Set budget to 0.15 (should reject after 1 request)
    with pytest.raises(Exception, match="Budget exceeded"):
        map_chat(mock_thinker, sample_ql_requests, budget_usd=0.15)

def test_map_chat_error_handling(mock_thinker, sample_ql_requests):
    """Test error handling per item."""
    # Mock responses with one error
    mock_responses = [
        Mock(text="echo:Hello 1", tokens={"input": 2, "output": 2}, cost_usd=0.0),
        Exception("API Error"),
        Mock(text="echo:Hello 3", tokens={"input": 2, "output": 2}, cost_usd=0.0)
    ]
    
    mock_thinker.chat_ql.side_effect = mock_responses
    
    results = map_chat(mock_thinker, sample_ql_requests)
    
    # Verify successful requests are processed
    assert len(results) == 3
    assert results[0].text == "echo:Hello 1"
    assert results[2].text == "echo:Hello 3"
    
    # Verify error is captured
    assert isinstance(results[1], Exception)
    assert str(results[1]) == "API Error"

def test_map_chat_empty_list(mock_thinker):
    """Test map_chat with empty request list."""
    results = map_chat(mock_thinker, [])
    
    assert results == []
    assert mock_thinker.chat_ql.call_count == 0

def test_map_chat_single_request(mock_thinker):
    """Test map_chat with single request."""
    ql = {
        "version": "0.3",
        "intent": "chat",
        "messages": [{"role": "user", "parts": [{"type": "text", "text": "Single request"}]}],
        "routing": {"call_id": "local:echo.chat"}
    }
    
    mock_response = Mock(text="echo:Single request", tokens={"input": 2, "output": 2}, cost_usd=0.0)
    mock_thinker.chat_ql.return_value = mock_response
    
    results = map_chat(mock_thinker, [ql])
    
    assert len(results) == 1
    assert results[0].text == "echo:Single request"
    assert mock_thinker.chat_ql.call_count == 1

def test_map_chat_large_batch(mock_thinker):
    """Test map_chat with large batch (25 requests)."""
    # Create 25 requests
    requests = []
    for i in range(25):
        requests.append({
            "version": "0.3",
            "intent": "chat",
            "messages": [{"role": "user", "parts": [{"type": "text", "text": f"Request {i}"}]}],
            "routing": {"call_id": "local:echo.chat"}
        })
    
    # Mock responses
    mock_responses = [
        Mock(text=f"echo:Request {i}", tokens={"input": 2, "output": 2}, cost_usd=0.0)
        for i in range(25)
    ]
    
    mock_thinker.chat_ql.side_effect = mock_responses
    
    results = map_chat(mock_thinker, requests)
    
    # Verify all 25 requests were processed in order
    assert len(results) == 25
    for i, result in enumerate(results):
        assert result.text == f"echo:Request {i}"
    
    assert mock_thinker.chat_ql.call_count == 25
