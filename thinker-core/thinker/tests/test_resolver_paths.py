"""
Unit tests for registry resolver.

This module tests the call resolution logic including call_id, model,
and alias resolution paths. Tests capability matching, intent-to-kind
conversion, and error handling for various routing scenarios.

Author: Anjan Goswami
"""

import pytest
from thinker.registry.resolver import resolve_call, _intent_to_kind, _satisfies_caps
from thinker.registry.store import RegistryStore
from thinker.registry.schema import RegistryCall, Limits, Price

@pytest.fixture
def sample_store():
    """Create a sample registry store for testing."""
    store = RegistryStore()
    
    # Add multimodal chat call
    multimodal_call = RegistryCall(
        call_id="openai:gpt-4o-mini.chat",
        provider="openai", model_id="gpt-4o-mini", kind="chat", modality="multimodal",
        caps=["json_mode", "tools", "images_in"],
        limits=Limits(max_input_tokens=128000, max_output_tokens=16384),
        price=Price(input_per_1k=0.00015, output_per_1k=0.00060),
        adapter="openai", payload_style="chat_completions_v1",
        aliases=["openai:multimodal-cheap"]
    )
    
    # Add embedding call
    embed_call = RegistryCall(
        call_id="openai:text-embedding-3-large.embed",
        provider="openai", model_id="text-embedding-3-large", kind="embed", modality="embed",
        caps=[],
        limits=Limits(max_input_tokens=8192, max_output_tokens=0),
        price=Price(input_per_1k=0.00002, output_per_1k=0.0),
        adapter="openai", payload_style="embeddings_v1",
        aliases=["openai:embed-cheap"]
    )
    
    # Add local call
    local_call = RegistryCall(
        call_id="local:llama.chat",
        provider="local", model_id="llama", kind="chat", modality="text",
        caps=["json_mode"],
        limits=Limits(max_input_tokens=8192, max_output_tokens=1024),
        price=Price(input_per_1k=0.0, output_per_1k=0.0),
        adapter="local", payload_style="ollama_chat",
        aliases=["local:cheap"]
    )
    
    store.put_call(multimodal_call)
    store.put_call(embed_call)
    store.put_call(local_call)
    
    return store

def test_intent_to_kind():
    """Test intent to kind conversion."""
    assert _intent_to_kind("embed") == "embed"
    assert _intent_to_kind("ocr") == "docai"
    assert _intent_to_kind("doc_extract") == "docai"
    assert _intent_to_kind("docai_extract") == "docai"
    assert _intent_to_kind("chat") == "chat"
    assert _intent_to_kind("generate") == "chat"

def test_satisfies_caps():
    """Test capability satisfaction checking."""
    call = RegistryCall(
        call_id="test:model.chat",
        provider="test", model_id="model", kind="chat", modality="multimodal",
        caps=["json_mode", "tools"],
        limits=Limits(max_input_tokens=1000, max_output_tokens=1000),
        price=Price(input_per_1k=0.0, output_per_1k=0.0),
        adapter="test", payload_style="test"
    )
    
    # Should satisfy multimodal with required caps
    assert _satisfies_caps(call, "multimodal", ["json_mode"])
    assert _satisfies_caps(call, "multimodal", ["json_mode", "tools"])
    
    # Should satisfy text requests (multimodal can handle text)
    assert _satisfies_caps(call, "text", ["json_mode"])
    
    # Should not satisfy vision-only requests
    assert not _satisfies_caps(call, "vision", ["json_mode"])
    
    # Should not satisfy missing caps
    assert not _satisfies_caps(call, "multimodal", ["missing_cap"])

def test_resolve_by_call_id(sample_store):
    """Test resolution by call_id."""
    # Valid call_id
    call = resolve_call(
        sample_store,
        call_id="openai:gpt-4o-mini.chat",
        model=None, alias=None,
        intent="chat", need_modality="multimodal", need_caps=["json_mode"]
    )
    assert call.call_id == "openai:gpt-4o-mini.chat"
    
    # Invalid call_id
    with pytest.raises(ValueError, match="CALL_NOT_FOUND"):
        resolve_call(
            sample_store,
            call_id="nonexistent:model.chat",
            model=None, alias=None,
            intent="chat", need_modality="text", need_caps=[]
        )

def test_resolve_by_model(sample_store):
    """Test resolution by model."""
    # Valid model
    call = resolve_call(
        sample_store,
        call_id=None,
        model="openai/gpt-4o-mini", alias=None,
        intent="chat", need_modality="multimodal", need_caps=["json_mode"]
    )
    assert call.model_id == "gpt-4o-mini"
    
    # Model with wrong intent
    with pytest.raises(ValueError, match="MODEL_NOT_FOUND_OR_UNSUITABLE"):
        resolve_call(
            sample_store,
            call_id=None,
            model="openai/gpt-4o-mini", alias=None,
            intent="embed", need_modality="embed", need_caps=[]
        )

def test_resolve_by_alias(sample_store):
    """Test resolution by alias."""
    # Valid alias
    call = resolve_call(
        sample_store,
        call_id=None, model=None,
        alias="openai:multimodal-cheap",
        intent="chat", need_modality="multimodal", need_caps=["json_mode"]
    )
    assert call.call_id == "openai:gpt-4o-mini.chat"
    
    # Invalid alias
    with pytest.raises(ValueError, match="ALIAS_UNRESOLVED"):
        resolve_call(
            sample_store,
            call_id=None, model=None,
            alias="nonexistent:alias",
            intent="chat", need_modality="text", need_caps=[]
        )

def test_resolve_insufficient():
    """Test resolution with insufficient parameters."""
    store = RegistryStore()
    
    with pytest.raises(ValueError, match="ROUTING_INSUFFICIENT"):
        resolve_call(
            store,
            call_id=None, model=None, alias=None,
            intent="chat", need_modality="text", need_caps=[]
        )
