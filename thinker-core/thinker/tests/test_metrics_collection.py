"""
Unit tests for metrics collection.

This module tests the MetricsCollector class and metrics recording functionality.

Author: Anjan Goswami
"""

import pytest
import tempfile
import time
import os
from pathlib import Path
from unittest.mock import patch, MagicMock
from thinker.observability.metrics import MetricsCollector, record_request
from thinker.observability.storage import MetricsStorage


class TestMetricsCollector:
    """Test cases for MetricsCollector class."""
    
    def test_metrics_collector_initialization(self):
        """Test metrics collector initialization."""
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = os.path.join(temp_dir, "test_metrics.db")
            storage = MetricsStorage(db_path)
            collector = MetricsCollector(storage)
            
            assert collector.storage is storage
            assert collector.enabled is True
    
    def test_record_request_success(self):
        """Test recording successful request."""
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = os.path.join(temp_dir, "test_metrics.db")
            storage = MetricsStorage(db_path)
            collector = MetricsCollector(storage)
            
            # Record a successful request
            collector.record_request(
                request_id="test-123",
                provider="openai",
                model="gpt-4o-mini",
                call_id="openai:gpt-4o-mini.chat",
                input_tokens=100,
                output_tokens=50,
                latency_ms=1500.5,
                cost_usd=0.0001,
                status="success"
            )
            
            # Verify it was recorded
            stats = collector.get_stats()
            assert stats["total_requests"] == 1
            assert stats["successful_requests"] == 1
            assert stats["success_rate"] == 100.0
            assert stats["total_cost"] == 0.0001
    
    def test_record_request_error(self):
        """Test recording failed request."""
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = os.path.join(temp_dir, "test_metrics.db")
            storage = MetricsStorage(db_path)
            collector = MetricsCollector(storage)
            
            # Record a failed request
            collector.record_request(
                request_id="test-456",
                provider="openai",
                model="gpt-4o-mini",
                call_id="openai:gpt-4o-mini.chat",
                input_tokens=100,
                output_tokens=0,
                latency_ms=500.0,
                cost_usd=0.0,
                status="error",
                error_code="API_ERROR"
            )
            
            # Verify it was recorded
            stats = collector.get_stats()
            assert stats["total_requests"] == 1
            assert stats["successful_requests"] == 0
            assert stats["success_rate"] == 0.0
            assert stats["recent_errors"][0]["error"] == "API_ERROR"
    
    def test_get_provider_stats(self):
        """Test getting provider-specific statistics."""
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = os.path.join(temp_dir, "test_metrics.db")
            storage = MetricsStorage(db_path)
            collector = MetricsCollector(storage)
            
            # Record requests for different providers
            collector.record_request(
                request_id="test-1", provider="openai", model="gpt-4o-mini",
                call_id="openai:gpt-4o-mini.chat", input_tokens=100, output_tokens=50,
                latency_ms=1000.0, cost_usd=0.0001, status="success"
            )
            
            collector.record_request(
                request_id="test-2", provider="anthropic", model="claude-3-5-sonnet",
                call_id="anthropic:claude-3-5-sonnet.chat", input_tokens=200, output_tokens=100,
                latency_ms=2000.0, cost_usd=0.0002, status="success"
            )
            
            # Get provider stats
            provider_stats = collector.get_provider_stats()
            
            assert "openai" in provider_stats
            assert "anthropic" in provider_stats
            assert provider_stats["openai"]["total_requests"] == 1
            assert provider_stats["anthropic"]["total_requests"] == 1
            assert provider_stats["openai"]["total_cost"] == 0.0001
            assert provider_stats["anthropic"]["total_cost"] == 0.0002
    
    def test_cleanup_old_data(self):
        """Test cleanup of old data."""
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = os.path.join(temp_dir, "test_metrics.db")
            storage = MetricsStorage(db_path)
            collector = MetricsCollector(storage)
            
            # Record some data
            collector.record_request(
                request_id="test-1", provider="openai", model="gpt-4o-mini",
                call_id="openai:gpt-4o-mini.chat", input_tokens=100, output_tokens=50,
                latency_ms=1000.0, cost_usd=0.0001, status="success"
            )
            
            # Verify data exists
            stats = collector.get_stats()
            assert stats["total_requests"] == 1
            
            # Test cleanup (should not delete recent data)
            deleted_count = collector.cleanup_old_data(days_to_keep=90)
            assert deleted_count == 0  # No old data to delete
            
            # Verify data still exists
            stats = collector.get_stats()
            assert stats["total_requests"] == 1
    
    def test_disabled_metrics(self):
        """Test metrics collection when disabled."""
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = os.path.join(temp_dir, "test_metrics.db")
            storage = MetricsStorage(db_path)
            collector = MetricsCollector(storage)
            collector.enabled = False
            
            # Record request (should be ignored)
            collector.record_request(
                request_id="test-123", provider="openai", model="gpt-4o-mini",
                call_id="openai:gpt-4o-mini.chat", input_tokens=100, output_tokens=50,
                latency_ms=1000.0, cost_usd=0.0001, status="success"
            )
            
            # Verify no data was recorded
            stats = collector.get_stats()
            assert stats["total_requests"] == 0


class TestRecordRequestFunction:
    """Test cases for the record_request function."""
    
    def test_record_request_function(self):
        """Test the record_request convenience function."""
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = os.path.join(temp_dir, "test_metrics.db")
            
            # Mock the global metrics collector
            with patch('thinker.observability.metrics.get_metrics_collector') as mock_get:
                mock_collector = MagicMock()
                mock_get.return_value = mock_collector
                
                # Call record_request
                request_id = record_request(
                    provider="openai",
                    model="gpt-4o-mini",
                    call_id="openai:gpt-4o-mini.chat",
                    input_tokens=100,
                    output_tokens=50,
                    latency_ms=1000.0,
                    cost_usd=0.0001,
                    status="success"
                )
                
                # Verify it was called
                mock_collector.record_request.assert_called_once()
                assert isinstance(request_id, str)
                assert len(request_id) > 0  # Should be a UUID
