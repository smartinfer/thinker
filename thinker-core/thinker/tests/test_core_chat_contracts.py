"""
Unit tests for core chat contracts.

This module tests the core Thinker.chat_ql() method including token feasibility,
autoshrink functionality, cost upper bounds, and JSON schema validation.

Author: Anjan Goswami
"""

import pytest
from unittest.mock import Mock, patch
from thinker.core import Thinker
from thinker.registry.store import RegistryStore
from thinker.registry.schema import RegistryCall, Limits, Price
from thinker.pricebook import PriceBook

@pytest.fixture
def mock_store():
    """Create a mock registry store."""
    store = RegistryStore()
    
    # Add a test call
    call = RegistryCall(
        call_id="local:echo.chat",
        provider="local",
        model_id="echo",
        kind="chat",
        modality="text",
        caps=["json_mode"],
        limits=Limits(max_input_tokens=1000, max_output_tokens=100),
        price=Price(input_per_1k=0.0, output_per_1k=0.0),
        adapter="local",
        payload_style="echo"
    )
    store.put_call(call)
    
    return store

@pytest.fixture
def mock_pricebook():
    """Create a mock pricebook."""
    pricebook = Mock(spec=PriceBook)
    pricebook.cost.return_value = 0.001
    return pricebook

def test_chat_ql_basic_request(mock_store, mock_pricebook):
    """Test basic chat request processing."""
    thinker = Thinker(mock_store, mock_pricebook)
    
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
    
    with patch('thinker.core.get_adapter') as mock_get_adapter:
        mock_adapter = Mock()
        mock_adapter.chat.return_value = Mock(
            text="echo:Hello, world!",
            tokens={"input": 5, "output": 3}
        )
        mock_get_adapter.return_value = mock_adapter
        
        with patch('thinker.core.count_tokens', return_value=5):
            response = thinker.chat_ql(ql)
            
            assert response.text == "echo:Hello, world!"
            assert response.model == "echo"
            assert response.provider == "local"
            assert response.tokens["input"] == 5
            assert response.tokens["output"] == 3
            assert response.cost_usd == 0.001

def test_chat_ql_autoshrink_token_limit_exceeded(mock_store, mock_pricebook):
    """Test autoshrink when token limit is exceeded."""
    thinker = Thinker(mock_store, mock_pricebook)
    
    ql = {
        "version": "0.3",
        "intent": "chat",
        "messages": [
            {
                "role": "user",
                "parts": [{"type": "text", "text": "Very long text that exceeds token limits"}]
            }
        ],
        "routing": {"call_id": "local:echo.chat"},
        "instructions": {"max_output_tokens": 100}
    }
    
    with patch('thinker.core.get_adapter') as mock_get_adapter:
        mock_adapter = Mock()
        mock_adapter.chat.return_value = Mock(
            text="echo:truncated",
            tokens={"input": 50, "output": 10}
        )
        mock_get_adapter.return_value = mock_adapter
        
        # Mock count_tokens to return high token count initially, then lower after truncation
        with patch('thinker.core.count_tokens', side_effect=[1200, 800, 600]):
            with patch('thinker.core.truncate_last_user') as mock_truncate:
                # Create a mock InternalRequest object
                from thinker.thinkerql.parse import InternalRequest
                mock_request = InternalRequest(
                    messages=ql["messages"],
                    documents=[],
                    instructions=ql["instructions"],
                    routing=ql["routing"],
                    need_modality="text",
                    need_caps=[],
                    intent=ql["intent"]
                )
                mock_truncate.return_value = mock_request
                
                response = thinker.chat_ql(ql)
                
                # Should have called truncate_last_user
                assert mock_truncate.called
                assert response.autoshrink_trace is not None

def test_chat_ql_json_schema_validation(mock_store, mock_pricebook):
    """Test JSON schema validation in instructions."""
    thinker = Thinker(mock_store, mock_pricebook)
    
    ql = {
        "version": "0.3",
        "intent": "chat",
        "messages": [
            {
                "role": "user",
                "parts": [{"type": "text", "text": "Generate JSON"}]
            }
        ],
        "routing": {"call_id": "local:echo.chat"},
        "instructions": {
            "json_schema": {
                "type": "object",
                "properties": {
                    "result": {"type": "string"}
                },
                "required": ["result"]
            }
        }
    }
    
    with patch('thinker.core.get_adapter') as mock_get_adapter:
        mock_adapter = Mock()
        mock_adapter.chat.return_value = Mock(
            text='{"result": "success"}',
            tokens={"input": 10, "output": 15}
        )
        mock_get_adapter.return_value = mock_adapter
        
        with patch('thinker.core.count_tokens', return_value=10):
            with patch('thinker.core.validate_json_schema') as mock_validate:
                mock_validate.return_value = True
                
                response = thinker.chat_ql(ql)
                
                # Should have validated JSON schema
                mock_validate.assert_called_once()
                assert response.text == '{"result": "success"}'

def test_chat_ql_cost_calculation(mock_store, mock_pricebook):
    """Test cost calculation from pricebook."""
    thinker = Thinker(mock_store, mock_pricebook)
    
    ql = {
        "version": "0.3",
        "intent": "chat",
        "messages": [
            {
                "role": "user",
                "parts": [{"type": "text", "text": "Hello"}]
            }
        ],
        "routing": {"call_id": "local:echo.chat"}
    }
    
    with patch('thinker.core.get_adapter') as mock_get_adapter:
        mock_adapter = Mock()
        mock_response = Mock()
        mock_response.text = "echo:Hello"
        mock_response.tokens = {"input": 100, "output": 50}
        mock_adapter.chat.return_value = mock_response
        mock_get_adapter.return_value = mock_adapter
        
        with patch('thinker.core.count_tokens', return_value=100):
            response = thinker.chat_ql(ql)
            
            # Should have called pricebook.cost with the call and token counts
            mock_pricebook.cost.assert_called_once()
            call_args = mock_pricebook.cost.call_args[0]
            assert call_args[1] == 100  # input tokens
            assert call_args[2] == 50   # output tokens

def test_chat_ql_from_files():
    """Test Thinker.from_files class method."""
    with patch('thinker.core.load_catalog') as mock_load_catalog:
        with patch('thinker.core.RegistryStore') as mock_store_class:
            with patch('thinker.core.PriceBook') as mock_pricebook_class:
                # Setup mocks
                mock_catalog = Mock()
                mock_load_catalog.return_value = (mock_catalog, "test_sha")
                mock_store = Mock()
                mock_store_class.return_value = mock_store
                mock_pricebook = Mock()
                mock_pricebook_class.from_file.return_value = mock_pricebook
                
                thinker = Thinker.from_files("registry.yaml", "pricebook.yaml")
                
                # Verify calls
                mock_load_catalog.assert_called_once_with("registry.yaml")
                mock_store.apply_catalog.assert_called_once_with(mock_catalog, "test_sha", "registry.yaml")
                mock_pricebook_class.from_file.assert_called_once_with("pricebook.yaml")
                
                assert isinstance(thinker, Thinker)
                assert thinker.store == mock_store
                assert thinker.pricebook == mock_pricebook

def test_chat_ql_capability_mismatch(mock_store, mock_pricebook):
    """Test handling of capability mismatch."""
    thinker = Thinker(mock_store, mock_pricebook)
    
    ql = {
        "version": "0.3",
        "intent": "chat",
        "messages": [
            {
                "role": "user",
                "parts": [
                    {"type": "text", "text": "What's in this image?"},
                    {"type": "image_url", "image_url": {"url": "https://example.com/image.jpg"}}
                ]
            }
        ],
        "routing": {"call_id": "local:echo.chat"}  # This call doesn't support images
    }
    
    with patch('thinker.core.resolve_call') as mock_resolve:
        mock_resolve.side_effect = ValueError("CAPABILITY_MISMATCH")
        
        with pytest.raises(ValueError, match="CAPABILITY_MISMATCH"):
            thinker.chat_ql(ql)
