"""
Registry store for Thinker Core.

This module provides an in-memory store for registry data with CRUD operations,
query methods, and version tracking. The store maintains a dictionary of
RegistryCall objects indexed by call_id and supports provider/model/alias queries.

Author: Anjan Goswami
"""

from dataclasses import dataclass
from typing import Dict, List, Optional
from .schema import Catalog, RegistryCall

@dataclass
class RegistryVersion:
    version_id: str          # e.g., sha256 of catalog yaml
    source: str              # path or "ingestor:openai_seed@2025-10-26"
    notes: str = ""

class RegistryStore:
    def __init__(self):
        self._calls: Dict[str, RegistryCall] = {}
        self._aliases: Dict[str, List[str]] = {}
        self._version: Optional[RegistryVersion] = None

    def apply_catalog(self, catalog: Catalog, version_id: str, source: str):
        self._calls = {c.call_id: c for c in catalog.calls}
        self._version = RegistryVersion(version_id, source)

    def put_call(self, c: RegistryCall):
        self._calls[c.call_id] = c

    def get_call(self, call_id: str) -> Optional[RegistryCall]:
        return self._calls.get(call_id)

    def list_calls(self) -> List[RegistryCall]:
        return list(self._calls.values())

    def by_provider(self, provider: str) -> List[RegistryCall]:
        return [c for c in self._calls.values() if c.provider == provider]

    def by_model(self, provider: str, model_id: str) -> List[RegistryCall]:
        return [c for c in self._calls.values() if c.provider == provider and c.model_id == model_id]

    def find_alias(self, alias: str) -> List[RegistryCall]:
        # simple alias index: alias string present in call.aliases
        return [c for c in self._calls.values() if alias in c.aliases]

    def version(self) -> Optional[RegistryVersion]:
        return self._version
