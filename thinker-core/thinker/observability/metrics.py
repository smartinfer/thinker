"""
Metrics collection for Thinker Core.

This module provides metrics collection and recording functionality
for monitoring Thinker usage, performance, and costs.

Author: Anjan Goswami
"""

import time
import uuid
from typing import Optional, Dict, Any
from .storage import MetricsStorage
from ..config import get_config


class MetricsCollector:
    """Collects and records metrics for Thinker operations."""
    
    def __init__(self, storage: Optional[MetricsStorage] = None):
        """
        Initialize metrics collector.
        
        Args:
            storage: Metrics storage instance (optional)
        """
        self.config = get_config()
        self.storage = storage or MetricsStorage(self.config.metrics_db_path)
        self.enabled = self.config.enable_metrics
    
    def record_request(
        self,
        request_id: str,
        provider: str,
        model: str,
        call_id: str,
        input_tokens: int,
        output_tokens: int,
        latency_ms: float,
        cost_usd: float,
        status: str,
        error_code: Optional[str] = None
    ):
        """
        Record a request in metrics storage.
        
        Args:
            request_id: Unique request identifier
            provider: Provider name (openai, anthropic, etc.)
            model: Model name
            call_id: Registry call ID
            input_tokens: Number of input tokens
            output_tokens: Number of output tokens
            latency_ms: Request latency in milliseconds
            cost_usd: Cost in USD
            status: Request status (success, error)
            error_code: Error code if status is error
        """
        if not self.enabled:
            return
        
        try:
            self.storage.record_request(
                request_id=request_id,
                provider=provider,
                model=model,
                call_id=call_id,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                latency_ms=latency_ms,
                cost_usd=cost_usd,
                status=status,
                error_code=error_code
            )
        except Exception:
            # Silently fail metrics collection to not break main functionality
            pass
    
    def get_stats(
        self,
        since: Optional[float] = None,
        until: Optional[float] = None,
        provider: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Get aggregated statistics.
        
        Args:
            since: Start timestamp (default: 24 hours ago)
            until: End timestamp (default: now)
            provider: Filter by provider (optional)
            
        Returns:
            Dictionary with aggregated statistics
        """
        return self.storage.get_stats(since=since, until=until, provider=provider)
    
    def get_provider_stats(self, since: Optional[float] = None) -> Dict[str, Dict[str, Any]]:
        """
        Get statistics grouped by provider.
        
        Args:
            since: Start timestamp (default: 24 hours ago)
            
        Returns:
            Dictionary with provider statistics
        """
        return self.storage.get_provider_stats(since=since)
    
    def cleanup_old_data(self, days_to_keep: int = 90) -> int:
        """
        Remove old data to keep database size manageable.
        
        Args:
            days_to_keep: Number of days of data to keep
            
        Returns:
            Number of records deleted
        """
        return self.storage.cleanup_old_data(days_to_keep)


# Global metrics collector instance
_metrics_collector: Optional[MetricsCollector] = None


def get_metrics_collector() -> MetricsCollector:
    """Get the global metrics collector instance."""
    global _metrics_collector
    if _metrics_collector is None:
        _metrics_collector = MetricsCollector()
    return _metrics_collector


def record_request(
    provider: str,
    model: str,
    call_id: str,
    input_tokens: int,
    output_tokens: int,
    latency_ms: float,
    cost_usd: float,
    status: str,
    error_code: Optional[str] = None
) -> str:
    """
    Record a request and return the request ID.
    
    Args:
        provider: Provider name
        model: Model name
        call_id: Registry call ID
        input_tokens: Number of input tokens
        output_tokens: Number of output tokens
        latency_ms: Request latency in milliseconds
        cost_usd: Cost in USD
        status: Request status (success, error)
        error_code: Error code if status is error
        
    Returns:
        Generated request ID
    """
    request_id = str(uuid.uuid4())
    collector = get_metrics_collector()
    collector.record_request(
        request_id=request_id,
        provider=provider,
        model=model,
        call_id=call_id,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        latency_ms=latency_ms,
        cost_usd=cost_usd,
        status=status,
        error_code=error_code
    )
    return request_id
