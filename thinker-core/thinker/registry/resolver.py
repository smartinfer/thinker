"""
Registry resolver for Thinker Core.

This module provides call resolution logic for routing requests to appropriate
models based on call_id, model specification, or aliases. Includes capability
matching and intent-to-kind conversion for deterministic routing decisions.

Author: Anjan Goswami
"""

from typing import Optional, List
from .schema import RegistryCall
from .store import RegistryStore

def _intent_to_kind(intent: str) -> str:
    return "embed" if intent=="embed" else ("docai" if intent in {"ocr","doc_extract","docai_extract"} else "chat")

def _satisfies_caps(call: RegistryCall, need_modality: str, need_caps: List[str]) -> bool:
    # Check modality compatibility
    if call.modality == "multimodal":
        # Multimodal can handle text and multimodal requests
        if need_modality not in {"text", "multimodal"}:
            return False
    elif call.modality == need_modality:
        # Exact match
        pass
    elif need_modality == "text" and call.modality in {"text", "multimodal"}:
        # Text requests can be handled by text or multimodal models
        pass
    else:
        # Modality mismatch
        return False
    
    # Check capabilities
    return all(cap in call.caps for cap in need_caps)

def resolve_call(
    store: RegistryStore,
    *, call_id: Optional[str],
    model: Optional[str],
    alias: Optional[str],
    intent: str,
    need_modality: str,
    need_caps: List[str]
) -> RegistryCall:
    # 1) call_id
    if call_id:
        c = store.get_call(call_id)
        if not c: raise ValueError(f"CALL_NOT_FOUND: {call_id}")
        if not _satisfies_caps(c, need_modality, need_caps): raise ValueError("CAPABILITY_MISMATCH")
        return c
    # 2) model "provider/model"
    if model:
        provider, model_id = model.split("/", 1)
        candidates = [c for c in store.by_model(provider, model_id) if c.kind == _intent_to_kind(intent)]
        candidates = [c for c in candidates if _satisfies_caps(c, need_modality, need_caps)]
        if candidates: return candidates[0]
        raise ValueError("MODEL_NOT_FOUND_OR_UNSUITABLE")
    # 3) alias
    if alias:
        candidates = [c for c in store.find_alias(alias) if _satisfies_caps(c, need_modality, need_caps)]
        if candidates: return candidates[0]
        raise ValueError("ALIAS_UNRESOLVED")
    raise ValueError("ROUTING_INSUFFICIENT")  # policy-based selection handled elsewhere


def resolve_image_call(store: RegistryStore, call_id: str) -> RegistryCall:
    """Resolve an exact image call without policy or alias routing."""
    call = store.get_call(call_id)
    if call is None:
        raise ValueError(f"CALL_NOT_FOUND: {call_id}")
    if call.kind != "image_generation" or call.modality != "image_output":
        raise ValueError(f"CALL_NOT_IMAGE_GENERATION: {call_id}")
    return call
