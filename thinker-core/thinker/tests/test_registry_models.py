"""
Unit tests for registry models.

This module tests the Pydantic models in the registry schema including
RegistryCall validation, Limits/Price constraints, and Catalog construction.
Ensures type safety and data integrity throughout the registry system.

Author: Anjan Goswami
"""

import pytest
from thinker.registry.schema import RegistryCall, Limits, Price, Catalog

def test_registry_call_validation():
    """Test RegistryCall construction and validation."""
    # Valid call
    call = RegistryCall(
        call_id="openai:gpt-4o-mini.chat",
        provider="openai",
        model_id="gpt-4o-mini",
        kind="chat",
        modality="multimodal",
        caps=["json_mode", "tools"],
        limits=Limits(max_input_tokens=128000, max_output_tokens=16384),
        price=Price(input_per_1k=0.00015, output_per_1k=0.00060),
        adapter="openai",
        payload_style="chat_completions_v1"
    )
    assert call.call_id == "openai:gpt-4o-mini.chat"
    assert call.provider == "openai"
    assert call.kind == "chat"
    assert call.modality == "multimodal"
    assert "json_mode" in call.caps

def test_call_id_validation():
    """Test call_id format validation."""
    # Valid format
    call = RegistryCall(
        call_id="provider:model.kind",
        provider="test", model_id="test", kind="chat", modality="text",
        limits=Limits(max_input_tokens=1000, max_output_tokens=1000),
        price=Price(input_per_1k=0.0, output_per_1k=0.0),
        adapter="test", payload_style="test"
    )
    assert call.call_id == "provider:model.kind"
    
    # Invalid formats
    with pytest.raises(ValueError, match="call_id must be provider:model.kind"):
        RegistryCall(
            call_id="invalid_format",
            provider="test", model_id="test", kind="chat", modality="text",
            limits=Limits(max_input_tokens=1000, max_output_tokens=1000),
            price=Price(input_per_1k=0.0, output_per_1k=0.0),
            adapter="test", payload_style="test"
        )

def test_limits_validation():
    """Test Limits field validation."""
    # Valid limits
    limits = Limits(max_input_tokens=1000, max_output_tokens=1000)
    assert limits.max_input_tokens == 1000
    assert limits.max_output_tokens == 1000
    
    # Invalid negative values
    with pytest.raises(ValueError):
        Limits(max_input_tokens=-1, max_output_tokens=1000)

def test_price_validation():
    """Test Price field validation."""
    # Valid price
    price = Price(input_per_1k=0.001, output_per_1k=0.002)
    assert price.input_per_1k == 0.001
    assert price.output_per_1k == 0.002
    
    # Invalid negative values
    with pytest.raises(ValueError):
        Price(input_per_1k=-0.001, output_per_1k=0.002)

def test_catalog_construction():
    """Test Catalog construction."""
    call1 = RegistryCall(
        call_id="test:model1.chat",
        provider="test", model_id="model1", kind="chat", modality="text",
        limits=Limits(max_input_tokens=1000, max_output_tokens=1000),
        price=Price(input_per_1k=0.0, output_per_1k=0.0),
        adapter="test", payload_style="test"
    )
    
    catalog = Catalog(calls=[call1], version="1", meta={"test": "value"})
    assert len(catalog.calls) == 1
    assert catalog.version == "1"
    assert catalog.meta["test"] == "value"
