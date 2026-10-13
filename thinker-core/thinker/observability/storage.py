"""
Metrics storage for Thinker Core.

This module provides SQLite-based storage for metrics data with
automatic database initialization and cleanup.

Author: Anjan Goswami
"""

import sqlite3
import time
from pathlib import Path
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta


class MetricsStorage:
    """SQLite-based storage for metrics data."""
    
    def __init__(self, db_path: str):
        """
        Initialize metrics storage.
        
        Args:
            db_path: Path to SQLite database file
        """
        self.db_path = db_path
        self._init_database()
    
    def _init_database(self):
        """Initialize the database schema."""
        schema = """(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            request_id TEXT NOT NULL, timestamp REAL NOT NULL,
            provider TEXT NOT NULL, model TEXT NOT NULL, call_id TEXT NOT NULL,
            input_tokens INTEGER, output_tokens INTEGER,
            latency_ms REAL NOT NULL, cost_usd REAL,
            status TEXT NOT NULL, error_code TEXT,
            job_id TEXT, turn_id TEXT, invocation_id TEXT, outcome TEXT
        )"""
        with sqlite3.connect(self.db_path, timeout=30) as conn:
            # Serialize migration with concurrent collector construction.
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("CREATE TABLE IF NOT EXISTS requests " + schema)
            columns = conn.execute("PRAGMA table_info(requests)").fetchall()
            old_names = {column[1] for column in columns}
            nullable = {"input_tokens", "output_tokens", "cost_usd"}
            if any(column[1] in nullable and column[3] for column in columns):
                # SQLite cannot drop NOT NULL directly. Rebuild atomically,
                # preserving original IDs, timestamps, rows, and known fields.
                conn.execute("CREATE TABLE requests_migration " + schema)
                names = ", ".join('"' + column[1].replace('"', '""') + '"' for column in columns)
                conn.execute(f"INSERT INTO requests_migration ({names}) SELECT {names} FROM requests")
                conn.execute("DROP TABLE requests")
                conn.execute("ALTER TABLE requests_migration RENAME TO requests")
            else:
                for name in ("job_id", "turn_id", "invocation_id", "outcome"):
                    if name not in old_names:
                        conn.execute(f"ALTER TABLE requests ADD COLUMN {name} TEXT")
            for name in ("timestamp", "provider", "status", "request_id", "job_id", "turn_id"):
                conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{name} ON requests({name})")
            conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_invocation_id ON requests(invocation_id)")

    def record_request(
        self,
        request_id: str,
        provider: str,
        model: str,
        call_id: str,
        input_tokens: Optional[int],
        output_tokens: Optional[int],
        latency_ms: float,
        cost_usd: Optional[float],
        status: str,
        error_code: Optional[str] = None,
        *, job_id: Optional[str] = None, turn_id: Optional[str] = None,
        invocation_id: Optional[str] = None, outcome: Optional[str] = None
    ):
        """
        Record a request in the database.
        
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
        with sqlite3.connect(self.db_path, timeout=30) as conn:
            conn.execute("""
                INSERT INTO requests (
                    request_id, timestamp, provider, model, call_id,
                    input_tokens, output_tokens, latency_ms, cost_usd,
                    status, error_code, job_id, turn_id, invocation_id, outcome
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                request_id, time.time(), provider, model, call_id,
                input_tokens, output_tokens, latency_ms, cost_usd,
                status, error_code, job_id, turn_id, invocation_id, outcome
            ))
            conn.commit()
    
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
        if since is None:
            since = time.time() - 86400  # 24 hours ago
        if until is None:
            until = time.time()
        
        with sqlite3.connect(self.db_path, timeout=30) as conn:
            # Base query
            where_clauses = ["timestamp >= ?", "timestamp <= ?"]
            params = [since, until]
            
            if provider:
                where_clauses.append("provider = ?")
                params.append(provider)
            
            where_sql = " AND ".join(where_clauses)
            
            # Get basic stats
            cursor = conn.execute(f"""
                SELECT 
                    COUNT(*) as total_requests,
                    SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) as successful_requests,
                    SUM(input_tokens) as total_input_tokens,
                    SUM(output_tokens) as total_output_tokens,
                    SUM(cost_usd) as total_cost,
                    AVG(latency_ms) as avg_latency,
                    MIN(latency_ms) as min_latency,
                    MAX(latency_ms) as max_latency,
                    SUM(input_tokens IS NULL), SUM(output_tokens IS NULL), SUM(cost_usd IS NULL)
                FROM requests 
                WHERE {where_sql}
            """, params)
            
            row = cursor.fetchone()
            if not row:
                return self._empty_stats()
            
            total_requests, successful_requests, total_input_tokens, total_output_tokens, total_cost, avg_latency, min_latency, max_latency, unknown_input, unknown_output, unknown_cost = row
            
            # Get success rate
            success_rate = (successful_requests / total_requests * 100) if total_requests > 0 else 0
            
            # Get requests per minute (last 5, 15, 60 minutes)
            now = time.time()
            requests_5min = self._get_requests_in_period(conn, now - 300, now, where_clauses, params)
            requests_15min = self._get_requests_in_period(conn, now - 900, now, where_clauses, params)
            requests_60min = self._get_requests_in_period(conn, now - 3600, now, where_clauses, params)
            
            # Get top models
            cursor = conn.execute(f"""
                SELECT model, COUNT(*) as count
                FROM requests 
                WHERE {where_sql}
                GROUP BY model
                ORDER BY count DESC
                LIMIT 5
            """, params)
            top_models = [{"model": row[0], "count": row[1]} for row in cursor.fetchall()]
            
            # Get recent errors
            cursor = conn.execute(f"""
                SELECT error_code, COUNT(*) as count
                FROM requests 
                WHERE {where_sql} AND status = 'error'
                GROUP BY error_code
                ORDER BY count DESC
                LIMIT 5
            """, params)
            recent_errors = [{"error": row[0], "count": row[1]} for row in cursor.fetchall()]
            
            return {
                "total_requests": total_requests or 0,
                "successful_requests": successful_requests or 0,
                "success_rate": round(success_rate, 2),
                "total_input_tokens": None if unknown_input else total_input_tokens or 0,
                "total_output_tokens": None if unknown_output else total_output_tokens or 0,
                "total_cost": None if unknown_cost else round(total_cost or 0, 4),
                "unknown_input_requests": unknown_input or 0,
                "unknown_output_requests": unknown_output or 0,
                "unknown_cost_requests": unknown_cost or 0,
                "avg_latency": round(avg_latency or 0, 2),
                "min_latency": round(min_latency or 0, 2),
                "max_latency": round(max_latency or 0, 2),
                "requests_per_minute": {
                    "last_5min": round(requests_5min / 5, 2),
                    "last_15min": round(requests_15min / 15, 2),
                    "last_60min": round(requests_60min / 60, 2)
                },
                "top_models": top_models,
                "recent_errors": recent_errors
            }
    
    def get_provider_stats(self, since: Optional[float] = None) -> Dict[str, Dict[str, Any]]:
        """
        Get statistics grouped by provider.
        
        Args:
            since: Start timestamp (default: 24 hours ago)
            
        Returns:
            Dictionary with provider statistics
        """
        if since is None:
            since = time.time() - 86400  # 24 hours ago
        
        with sqlite3.connect(self.db_path, timeout=30) as conn:
            cursor = conn.execute("""
                SELECT 
                    provider,
                    COUNT(*) as total_requests,
                    SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) as successful_requests,
                    SUM(input_tokens) as total_input_tokens,
                    SUM(output_tokens) as total_output_tokens,
                    SUM(cost_usd) as total_cost,
                    AVG(latency_ms) as avg_latency,
                    SUM(input_tokens IS NULL), SUM(output_tokens IS NULL), SUM(cost_usd IS NULL)
                FROM requests 
                WHERE timestamp >= ?
                GROUP BY provider
                ORDER BY total_requests DESC
            """, (since,))
            
            provider_stats = {}
            for row in cursor.fetchall():
                provider, total_requests, successful_requests, total_input_tokens, total_output_tokens, total_cost, avg_latency, unknown_input, unknown_output, unknown_cost = row
                
                success_rate = (successful_requests / total_requests * 100) if total_requests > 0 else 0
                
                provider_stats[provider] = {
                    "total_requests": total_requests,
                    "successful_requests": successful_requests,
                    "success_rate": round(success_rate, 2),
                    "total_input_tokens": None if unknown_input else total_input_tokens or 0,
                    "total_output_tokens": None if unknown_output else total_output_tokens or 0,
                    "total_cost": None if unknown_cost else round(total_cost or 0, 4),
                    "unknown_input_requests": unknown_input or 0,
                    "unknown_output_requests": unknown_output or 0,
                    "unknown_cost_requests": unknown_cost or 0,
                    "avg_latency": round(avg_latency or 0, 2)
                }
            
            return provider_stats
    
    def cleanup_old_data(self, days_to_keep: int = 90):
        """
        Remove old data to keep database size manageable.
        
        Args:
            days_to_keep: Number of days of data to keep
        """
        cutoff_time = time.time() - (days_to_keep * 86400)
        
        with sqlite3.connect(self.db_path, timeout=30) as conn:
            cursor = conn.execute("DELETE FROM requests WHERE timestamp < ?", (cutoff_time,))
            deleted_count = cursor.rowcount
            conn.commit()
            
            # Vacuum the database to reclaim space
            conn.execute("VACUUM")
            
            return deleted_count
    
    def _get_requests_in_period(
        self, 
        conn: sqlite3.Connection, 
        start: float, 
        end: float, 
        where_clauses: List[str], 
        params: List[Any]
    ) -> int:
        """Get number of requests in a specific time period."""
        period_where = where_clauses + ["timestamp >= ?", "timestamp <= ?"]
        period_params = params + [start, end]
        
        cursor = conn.execute(f"""
            SELECT COUNT(*)
            FROM requests 
            WHERE {' AND '.join(period_where)}
        """, period_params)
        
        return cursor.fetchone()[0] or 0
    
    def _empty_stats(self) -> Dict[str, Any]:
        """Return empty statistics structure."""
        return {
            "total_requests": 0,
            "successful_requests": 0,
            "success_rate": 0.0,
            "total_input_tokens": 0,
            "total_output_tokens": 0,
            "total_cost": 0.0,
            "avg_latency": 0.0,
            "min_latency": 0.0,
            "max_latency": 0.0,
            "requests_per_minute": {
                "last_5min": 0.0,
                "last_15min": 0.0,
                "last_60min": 0.0
            },
            "top_models": [],
            "recent_errors": []
        }
