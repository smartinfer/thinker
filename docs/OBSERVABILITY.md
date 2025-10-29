# Observability Guide

This document describes the observability features and monitoring capabilities of Thinker Core.

## Overview

Thinker Core includes comprehensive observability features to help you monitor:

- **Usage Statistics**: Request counts, success rates, token usage
- **Performance Metrics**: Latency, throughput, response times
- **Cost Tracking**: Per-request costs, budget monitoring
- **Error Analysis**: Error rates, failure patterns, debugging info
- **Provider Comparison**: Performance across different LLM providers

## Metrics Collection

### What's Tracked

For each request, Thinker automatically collects:

- **Request ID**: Unique identifier for tracing
- **Timestamp**: When the request was made
- **Provider**: Which LLM provider (openai, anthropic, etc.)
- **Model**: Specific model used
- **Call ID**: Registry call identifier
- **Token Usage**: Input and output token counts
- **Latency**: Request processing time in milliseconds
- **Cost**: Estimated cost in USD
- **Status**: Success or error
- **Error Code**: Specific error if request failed

### Storage

Metrics are stored in a SQLite database at `~/.thinker/metrics.db` with:

- **Automatic indexing** for fast queries
- **Data retention** (90 days by default, configurable)
- **Privacy-first design** (no prompt content stored)
- **Minimal overhead** (<5ms per request)

## CLI Dashboard

### Basic Statistics

View overall usage statistics:

```bash
# Show last 24 hours
thinker stats

# Show today's usage
thinker stats --today

# Show last hour
thinker stats --last-hour

# Filter by provider
thinker stats --provider openai
```

### Live Dashboard

Monitor usage in real-time (like `top` or `htop`):

```bash
# Live updating dashboard
thinker stats --live

# Live dashboard for specific provider
thinker stats --live --provider anthropic
```

The live dashboard shows:
- **Real-time request rates** (last 5, 15, 60 minutes)
- **Success rates** and error counts
- **Token usage** by provider
- **Cost tracking** (today, this week, this month)
- **Average latency** by provider
- **Top models** by usage
- **Recent errors** and their frequency

### Dashboard Layout

The dashboard is organized into sections:

1. **Header**: Key metrics at a glance
2. **Overview**: Overall statistics and performance
3. **Provider Stats**: Breakdown by LLM provider
4. **Top Models**: Most-used models
5. **Recent Errors**: Error analysis and debugging

## Configuration

### Enable/Disable Metrics

Metrics collection can be controlled via environment variables:

```bash
# Disable metrics collection
export THINKER_ENABLE_METRICS=false

# Custom metrics database path
export THINKER_METRICS_DB=/custom/path/metrics.db
```

### Data Retention

Control how long metrics are kept:

```python
from thinker.observability.metrics import get_metrics_collector

# Keep data for 30 days instead of 90
collector = get_metrics_collector()
deleted_count = collector.cleanup_old_data(days_to_keep=30)
print(f"Deleted {deleted_count} old records")
```

## Programmatic Access

### Using the Metrics API

```python
from thinker.observability.metrics import get_metrics_collector

# Get metrics collector
collector = get_metrics_collector()

# Get overall statistics
stats = collector.get_stats()
print(f"Total requests: {stats['total_requests']}")
print(f"Success rate: {stats['success_rate']}%")
print(f"Total cost: ${stats['total_cost']:.4f}")

# Get provider-specific statistics
provider_stats = collector.get_provider_stats()
for provider, data in provider_stats.items():
    print(f"{provider}: {data['total_requests']} requests, ${data['total_cost']:.4f}")

# Get statistics for specific time range
import time
since = time.time() - 3600  # Last hour
recent_stats = collector.get_stats(since=since)
```

### Custom Dashboards

Build custom monitoring solutions:

```python
from thinker.observability.dashboard import Dashboard
from thinker.observability.metrics import get_metrics_collector

# Create custom dashboard
dashboard = Dashboard()

# Show statistics for specific time range
since = time.time() - 86400  # Last 24 hours
dashboard.show_stats(since=since, provider="openai")

# Show live dashboard
dashboard.show_stats(live=True)
```

## Monitoring Use Cases

### 1. Cost Monitoring

Track spending across providers:

```bash
# Daily cost summary
thinker stats --today

# Monitor live spending
thinker stats --live
```

### 2. Performance Optimization

Identify slow providers or models:

```bash
# Compare provider performance
thinker stats --provider openai
thinker stats --provider anthropic

# Check recent performance
thinker stats --last-hour
```

### 3. Error Debugging

Analyze failure patterns:

```bash
# View recent errors
thinker stats

# Check specific provider errors
thinker stats --provider openai
```

### 4. Usage Planning

Understand usage patterns:

```bash
# Daily usage summary
thinker stats --today

# Peak usage times
thinker stats --live
```

## Integration with External Tools

### Prometheus/Grafana

Export metrics to Prometheus:

```python
from thinker.observability.storage import MetricsStorage
import time

# Query metrics data
storage = MetricsStorage("~/.thinker/metrics.db")
stats = storage.get_stats()

# Export to Prometheus format
print(f"thinker_requests_total {stats['total_requests']}")
print(f"thinker_requests_successful {stats['successful_requests']}")
print(f"thinker_cost_usd_total {stats['total_cost']}")
```

### Log Aggregation

Send metrics to log aggregation systems:

```python
import json
from thinker.observability.metrics import get_metrics_collector

collector = get_metrics_collector()
stats = collector.get_stats()

# Send to logging system
log_data = {
    "timestamp": time.time(),
    "service": "thinker-core",
    "metrics": stats
}
print(json.dumps(log_data))
```

## Troubleshooting

### No Data Showing

If the dashboard shows no data:

1. **Check if metrics are enabled**:
   ```bash
   echo $THINKER_ENABLE_METRICS
   ```

2. **Verify database exists**:
   ```bash
   ls -la ~/.thinker/metrics.db
   ```

3. **Make a test request**:
   ```bash
   thinker chat-ql --registry spec/registry.yaml --pricebook spec/pricebook.yaml --ql examples/01_text_chat_ql.yaml
   ```

### Performance Issues

If the dashboard is slow:

1. **Check database size**:
   ```bash
   du -h ~/.thinker/metrics.db
   ```

2. **Clean up old data**:
   ```python
   from thinker.observability.metrics import get_metrics_collector
   collector = get_metrics_collector()
   collector.cleanup_old_data(days_to_keep=30)
   ```

3. **Rebuild database indexes**:
   ```python
   from thinker.observability.storage import MetricsStorage
   storage = MetricsStorage("~/.thinker/metrics.db")
   # Indexes are automatically created
   ```

### Missing Metrics

If some metrics are missing:

1. **Check error logs**:
   ```bash
   tail -f ~/.thinker/metrics.log
   ```

2. **Verify request processing**:
   ```bash
   thinker stats --last-hour
   ```

3. **Test metrics collection**:
   ```python
   from thinker.observability.metrics import record_request
   record_request("test", "test-model", "test-call", 100, 50, 1000.0, 0.001, "success")
   ```

## Privacy and Security

### Data Privacy

- **No prompt content** is stored in metrics
- **No response content** is stored in metrics
- **Only metadata** and performance data is collected
- **Personal data** should not be included in prompts

### Data Security

- **Database file** has restrictive permissions (600)
- **Audit logs** track access to metrics
- **Data retention** automatically removes old data
- **Local storage only** (no external transmission)

### Compliance

- **GDPR compliant** (no personal data stored)
- **SOC 2 ready** (audit logging included)
- **HIPAA considerations** (no PHI in metrics)

## Best Practices

### 1. Regular Monitoring

Set up regular monitoring:

```bash
# Daily cost check
thinker stats --today

# Weekly performance review
thinker stats --provider openai
thinker stats --provider anthropic
```

### 2. Alert Thresholds

Monitor for unusual patterns:

- High error rates (>5%)
- Unusual cost spikes
- Performance degradation
- Provider outages

### 3. Data Retention

Balance storage vs. historical data:

- **Development**: 30 days
- **Production**: 90 days
- **Compliance**: 1 year (with external storage)

### 4. Performance Optimization

Use metrics to optimize:

- Switch to faster providers
- Adjust token limits
- Optimize request patterns
- Balance cost vs. performance

## Support

For observability issues:

1. Check the metrics database: `~/.thinker/metrics.db`
2. Review error logs: `~/.thinker/metrics.log`
3. Test with simple requests
4. Verify configuration settings
