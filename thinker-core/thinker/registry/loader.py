"""
Registry loader for Thinker Core.

This module handles loading YAML registry files into Catalog objects,
merging multiple catalogs with primary override logic, and loading
alias rules for dynamic routing. Provides SHA256 checksum verification
for registry integrity.

Author: Anjan Goswami
"""

import yaml, hashlib, json
from pathlib import Path
from .schema import Catalog, RegistryCall, AliasRule

def _sha256_bytes(b: bytes) -> str: return hashlib.sha256(b).hexdigest()

def load_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text())

def load_catalog(registry_path: str) -> tuple[Catalog, str]:
    p = Path(registry_path)
    data = load_yaml(p)
    cat = Catalog(**data)
    checksum = _sha256_bytes(p.read_bytes())
    return cat, checksum

def load_alias_rules(aliases_path: str|None) -> list[AliasRule]:
    if not aliases_path: return []
    raw = load_yaml(Path(aliases_path))
    rules = []
    for name, conf in raw.get("aliases", {}).items():
        rules.append(AliasRule(name=name, **conf))
    return rules

def merge_catalogs(primary: Catalog, *others: Catalog) -> Catalog:
    # key by call_id; last-writer-wins except keep primary overrides
    idx = {c.call_id: c for c in primary.calls}
    for cat in others:
        for c in cat.calls:
            if c.call_id not in idx:
                idx[c.call_id] = c
            else:
                # merge: preserve primary fields if set; otherwise take new
                current = idx[c.call_id].model_dump()
                incoming = c.model_dump()
                merged = {**incoming, **{k:v for k,v in current.items() if v not in (None, [], {}, "")}}
                idx[c.call_id] = RegistryCall(**merged)
    return Catalog(version=primary.version, calls=list(idx.values()), meta=primary.meta)
