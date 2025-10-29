"""
Dashboard for Thinker Core observability.

This module provides a rich terminal-based dashboard for viewing
Thinker metrics and statistics in real-time.

Author: Anjan Goswami
"""

import time
from typing import Optional, Dict, Any
from rich.console import Console
from rich.table import Table
from rich.live import Live
from rich.panel import Panel
from rich.text import Text
from rich.layout import Layout
from rich.align import Align
from .metrics import get_metrics_collector


class Dashboard:
    """Rich terminal dashboard for Thinker metrics."""
    
    def __init__(self):
        """Initialize dashboard."""
        self.console = Console()
        self.metrics = get_metrics_collector()
    
    def show_stats(
        self,
        provider: Optional[str] = None,
        since: Optional[float] = None,
        live: bool = False
    ):
        """
        Show statistics dashboard.
        
        Args:
            provider: Filter by provider (optional)
            since: Start timestamp (default: 24 hours ago)
            live: Whether to show live updating dashboard
        """
        if live:
            self._show_live_dashboard(provider, since)
        else:
            self._show_static_dashboard(provider, since)
    
    def _show_static_dashboard(self, provider: Optional[str], since: Optional[float]):
        """Show static dashboard."""
        stats = self.metrics.get_stats(since=since, provider=provider)
        provider_stats = self.metrics.get_provider_stats(since=since)
        
        # Create main layout
        layout = Layout()
        layout.split_column(
            Layout(self._create_header_panel(stats), size=3),
            Layout(self._create_overview_table(stats), size=8),
            Layout(self._create_provider_table(provider_stats), size=10),
            Layout(self._create_models_table(stats), size=6),
            Layout(self._create_errors_table(stats), size=6)
        )
        
        self.console.print(layout)
    
    def _show_live_dashboard(self, provider: Optional[str], since: Optional[float]):
        """Show live updating dashboard."""
        def generate_layout():
            stats = self.metrics.get_stats(since=since, provider=provider)
            provider_stats = self.metrics.get_provider_stats(since=since)
            
            layout = Layout()
            layout.split_column(
                Layout(self._create_header_panel(stats), size=3),
                Layout(self._create_overview_table(stats), size=8),
                Layout(self._create_provider_table(provider_stats), size=10),
                Layout(self._create_models_table(stats), size=6),
                Layout(self._create_errors_table(stats), size=6)
            )
            return layout
        
        with Live(generate_layout, refresh_per_second=0.5, console=self.console) as live:
            try:
                while True:
                    live.update(generate_layout())
                    time.sleep(2)
            except KeyboardInterrupt:
                pass
    
    def _create_header_panel(self, stats: Dict[str, Any]) -> Panel:
        """Create header panel with key metrics."""
        total_requests = stats.get("total_requests", 0)
        success_rate = stats.get("success_rate", 0)
        total_cost = stats.get("total_cost", 0)
        
        header_text = Text()
        header_text.append("Thinker Core Dashboard", style="bold blue")
        header_text.append(f" | Requests: {total_requests:,}", style="white")
        header_text.append(f" | Success: {success_rate:.1f}%", style="green" if success_rate >= 95 else "yellow")
        header_text.append(f" | Cost: ${total_cost:.4f}", style="cyan")
        
        return Panel(Align.center(header_text), style="blue")
    
    def _create_overview_table(self, stats: Dict[str, Any]) -> Table:
        """Create overview statistics table."""
        table = Table(title="Overview", show_header=True, header_style="bold magenta")
        table.add_column("Metric", style="cyan", no_wrap=True)
        table.add_column("Value", style="white")
        table.add_column("Details", style="dim")
        
        # Basic metrics
        table.add_row("Total Requests", f"{stats.get('total_requests', 0):,}", "")
        table.add_row("Successful", f"{stats.get('successful_requests', 0):,}", 
                     f"{stats.get('success_rate', 0):.1f}% success rate")
        table.add_row("Total Tokens", f"{stats.get('total_input_tokens', 0) + stats.get('total_output_tokens', 0):,}", 
                     f"{stats.get('total_input_tokens', 0):,} in + {stats.get('total_output_tokens', 0):,} out")
        table.add_row("Total Cost", f"${stats.get('total_cost', 0):.4f}", "")
        
        # Performance metrics
        avg_latency = stats.get('avg_latency', 0)
        min_latency = stats.get('min_latency', 0)
        max_latency = stats.get('max_latency', 0)
        table.add_row("Avg Latency", f"{avg_latency:.1f}ms", f"min: {min_latency:.1f}ms, max: {max_latency:.1f}ms")
        
        # Requests per minute
        rpm = stats.get('requests_per_minute', {})
        table.add_row("Requests/min", f"{rpm.get('last_15min', 0):.1f}", 
                     f"5min: {rpm.get('last_5min', 0):.1f}, 60min: {rpm.get('last_60min', 0):.1f}")
        
        return table
    
    def _create_provider_table(self, provider_stats: Dict[str, Dict[str, Any]]) -> Table:
        """Create provider statistics table."""
        table = Table(title="Provider Statistics", show_header=True, header_style="bold magenta")
        table.add_column("Provider", style="cyan", no_wrap=True)
        table.add_column("Requests", justify="right", style="white")
        table.add_column("Success %", justify="right", style="green")
        table.add_column("Tokens", justify="right", style="white")
        table.add_column("Cost", justify="right", style="yellow")
        table.add_column("Avg Latency", justify="right", style="blue")
        
        for provider, stats in provider_stats.items():
            success_rate = stats.get('success_rate', 0)
            total_tokens = stats.get('total_input_tokens', 0) + stats.get('total_output_tokens', 0)
            
            table.add_row(
                provider,
                f"{stats.get('total_requests', 0):,}",
                f"{success_rate:.1f}%",
                f"{total_tokens:,}",
                f"${stats.get('total_cost', 0):.4f}",
                f"{stats.get('avg_latency', 0):.1f}ms"
            )
        
        if not provider_stats:
            table.add_row("No data", "", "", "", "", "")
        
        return table
    
    def _create_models_table(self, stats: Dict[str, Any]) -> Table:
        """Create top models table."""
        table = Table(title="Top Models", show_header=True, header_style="bold magenta")
        table.add_column("Model", style="cyan", no_wrap=True)
        table.add_column("Requests", justify="right", style="white")
        
        top_models = stats.get('top_models', [])
        for model_info in top_models[:5]:  # Show top 5
            table.add_row(
                model_info.get('model', 'Unknown'),
                f"{model_info.get('count', 0):,}"
            )
        
        if not top_models:
            table.add_row("No data", "")
        
        return table
    
    def _create_errors_table(self, stats: Dict[str, Any]) -> Table:
        """Create recent errors table."""
        table = Table(title="Recent Errors", show_header=True, header_style="bold magenta")
        table.add_column("Error", style="red", no_wrap=True)
        table.add_column("Count", justify="right", style="white")
        
        recent_errors = stats.get('recent_errors', [])
        for error_info in recent_errors[:5]:  # Show top 5
            table.add_row(
                error_info.get('error', 'Unknown'),
                f"{error_info.get('count', 0):,}"
            )
        
        if not recent_errors:
            table.add_row("No errors", "")
        
        return table
