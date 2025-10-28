"""
Unit tests for registry loader and merging.

This module tests the catalog loading and merging functionality including
primary override logic, conflict resolution, and catalog combination.
Ensures deterministic behavior in multi-source registry scenarios.

Author: Anjan Goswami
"""

import pytest
from thinker.registry.loader import load_catalog, merge_catalogs
from thinker.registry.schema import Catalog, RegistryCall, Limits, Price

def test_load_catalog():
    """Test loading catalog from YAML."""
    # This would test with actual YAML file in integration tests
    # For unit tests, we'll test the merge functionality
    pass

def test_merge_catalogs():
    """Test merging catalogs with primary overrides."""
    # Create primary catalog
    primary_call = RegistryCall(
        call_id="test:model.chat",
        provider="test", model_id="model", kind="chat", modality="text",
        caps=["json_mode"],
        limits=Limits(max_input_tokens=1000, max_output_tokens=1000),
        price=Price(input_per_1k=0.001, output_per_1k=0.002),
        adapter="test", payload_style="test",
        aliases=["test:cheap"]
    )
    primary = Catalog(calls=[primary_call], version="1")
    
    # Create secondary catalog with same call_id but different values
    secondary_call = RegistryCall(
        call_id="test:model.chat",
        provider="test", model_id="model", kind="chat", modality="text",
        caps=["tools"],  # Different caps
        limits=Limits(max_input_tokens=2000, max_output_tokens=2000),  # Different limits
        price=Price(input_per_1k=0.005, output_per_1k=0.010),  # Different price
        adapter="test", payload_style="test",
        aliases=["test:expensive"]  # Different aliases
    )
    secondary = Catalog(calls=[secondary_call], version="2")
    
    # Merge - primary should win
    merged = merge_catalogs(primary, secondary)
    assert len(merged.calls) == 1
    
    merged_call = merged.calls[0]
    assert merged_call.call_id == "test:model.chat"
    # Primary values should be preserved
    assert merged_call.caps == ["json_mode"]  # Primary caps
    assert merged_call.limits.max_input_tokens == 1000  # Primary limits
    assert merged_call.price.input_per_1k == 0.001  # Primary price
    assert merged_call.aliases == ["test:cheap"]  # Primary aliases

def test_merge_catalogs_new_calls():
    """Test merging catalogs with new call_ids."""
    # Primary catalog
    primary_call = RegistryCall(
        call_id="test:model1.chat",
        provider="test", model_id="model1", kind="chat", modality="text",
        limits=Limits(max_input_tokens=1000, max_output_tokens=1000),
        price=Price(input_per_1k=0.001, output_per_1k=0.002),
        adapter="test", payload_style="test"
    )
    primary = Catalog(calls=[primary_call], version="1")
    
    # Secondary catalog with different call_id
    secondary_call = RegistryCall(
        call_id="test:model2.chat",
        provider="test", model_id="model2", kind="chat", modality="text",
        limits=Limits(max_input_tokens=2000, max_output_tokens=2000),
        price=Price(input_per_1k=0.005, output_per_1k=0.010),
        adapter="test", payload_style="test"
    )
    secondary = Catalog(calls=[secondary_call], version="2")
    
    # Merge - should have both calls
    merged = merge_catalogs(primary, secondary)
    assert len(merged.calls) == 2
    
    call_ids = {call.call_id for call in merged.calls}
    assert "test:model1.chat" in call_ids
    assert "test:model2.chat" in call_ids
