"""
Configuration management for Thinker Core.

This module provides configuration settings for the Thinker system,
including observability settings and storage paths.

Author: Anjan Goswami
"""

import os
from pathlib import Path
from typing import Optional


class ThinkerConfig:
    """Configuration settings for Thinker Core."""
    
    def __init__(
        self,
        enable_metrics: bool = True,
        metrics_db_path: Optional[str] = None,
        credentials_storage_type: str = "auto",
        keystore_path: Optional[str] = None
    ):
        """
        Initialize Thinker configuration.
        
        Args:
            enable_metrics: Whether to collect and store metrics
            metrics_db_path: Path to SQLite metrics database
            credentials_storage_type: "keyring", "encrypted_file", or "auto"
            keystore_path: Path for encrypted keystore file
        """
        self.enable_metrics = enable_metrics
        self.metrics_db_path = metrics_db_path or str(Path.home() / ".thinker" / "metrics.db")
        self.credentials_storage_type = credentials_storage_type
        self.keystore_path = keystore_path or str(Path.home() / ".thinker" / "keystore.encrypted")
        
        # Ensure .thinker directory exists
        Path(self.metrics_db_path).parent.mkdir(parents=True, exist_ok=True)
        Path(self.keystore_path).parent.mkdir(parents=True, exist_ok=True)
    
    @classmethod
    def from_env(cls) -> "ThinkerConfig":
        """Create configuration from environment variables."""
        return cls(
            enable_metrics=os.getenv("THINKER_ENABLE_METRICS", "true").lower() == "true",
            metrics_db_path=os.getenv("THINKER_METRICS_DB"),
            credentials_storage_type=os.getenv("THINKER_CREDENTIALS_STORAGE", "auto"),
            keystore_path=os.getenv("THINKER_KEYSTORE_PATH")
        )
    
    def to_dict(self) -> dict:
        """Convert configuration to dictionary."""
        return {
            "enable_metrics": self.enable_metrics,
            "metrics_db_path": self.metrics_db_path,
            "credentials_storage_type": self.credentials_storage_type,
            "keystore_path": self.keystore_path
        }


# Global configuration instance
_config: Optional[ThinkerConfig] = None


def get_config() -> ThinkerConfig:
    """Get the global configuration instance."""
    global _config
    if _config is None:
        _config = ThinkerConfig.from_env()
    return _config


def set_config(config: ThinkerConfig) -> None:
    """Set the global configuration instance."""
    global _config
    _config = config
