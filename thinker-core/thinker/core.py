"""
Core Thinker class for unified LLM access.

This module provides the main Thinker class that orchestrates the registry-driven
routing system and ThinkerQL request processing.

Author: Anjan Goswami
"""

import time
import copy
from typing import Dict, Any
from .thinkerql.parse import parse_thinkerql
from .registry.resolver import resolve_call
from .registry.store import RegistryStore
from .registry.loader import load_catalog
from .pricebook import PriceBook
from .tokenization import count_tokens, truncate_last_user, validate_json_schema
from .observability.metrics import record_request

class Thinker:
    """Main Thinker class for unified LLM access."""
    
    def __init__(self, store: RegistryStore, pricebook: PriceBook, *,
                 qualification_profile=None, qualification_root=None, qualification_config=None):
        """Initialize Thinker instance."""
        self.store = store
        self.pricebook = pricebook
        self.qualification_profile = qualification_profile
        self.qualification_root = qualification_root
        self.qualification_config = qualification_config or {}
        self._qualification_probe = None
    
    @classmethod
    def from_files(cls, registry_path: str):
        """Create a Thinker instance from a registry file."""
        cat, sha = load_catalog(registry_path)
        store = RegistryStore()
        store.apply_catalog(cat, sha, registry_path)
        pricebook = PriceBook()
        return cls(store, pricebook)
    
    def chat_ql(self, ql: Dict[str, Any]):
        """Ordinary execution never grants itself permission to qualify a target."""
        return self._chat_ql(ql)

    def _chat_ql(self, ql: Dict[str, Any], probe=None):
        """Existing resolver/adapter/record path with host qualification admission."""
        ql = copy.deepcopy(ql)
        context = None
        recorded = False
        start_time = time.time()
        
        try:
            # Parse the request
            req = parse_thinkerql(ql)
            
            # Resolve the call
            call = resolve_call(
                self.store,
                call_id=req.routing.get("call_id"),
                model=req.routing.get("model"),
                alias=req.routing.get("alias"),
                intent=req.intent,
                need_modality=req.need_modality,
                need_caps=req.need_caps
            )
            
            # Only the deterministic local echo is exempt. Even local model servers
            # require qualification; public request fields cannot authorize probes.
            if self.qualification_profile is not None:
                from .qualification import begin
                call = call.model_copy(deep=True)
                context = begin(self, ql, call, probe)
            elif not (call.provider == "local" and call.adapter == "local" and call.model_id == "echo"):
                raise ValueError("NOT_QUALIFIED: configure a frozen application profile")

            # Autoshrink if needed
            max_output_tokens = req.instructions.get("max_output_tokens", 256)
            in_tokens = count_tokens(req)
            
            if context is not None and (
                in_tokens > call.limits.max_input_tokens or max_output_tokens > call.limits.max_output_tokens
            ):
                raise ValueError("FROZEN_CONTRACT_EXCEEDS_CONTEXT: autoshrink prohibited")
            autoshrink_trace = []
            step = 0
            
            while in_tokens + max_output_tokens > (call.limits.max_input_tokens + call.limits.max_output_tokens):
                step += 1
                tokens_before = in_tokens
                
                req = truncate_last_user(req)
                in_tokens = count_tokens(req)
                
                autoshrink_trace.append({
                    "step": step,
                    "tokens_before": tokens_before,
                    "tokens_after": in_tokens,
                    "action": "truncate_last_user"
                })
                
                # Safety check to prevent infinite loop
                if step > 10:
                    break
            
            # Get adapter and make the call
            if context is not None:
                from .adapters.planned import PlannedAdapter
                adapter = PlannedAdapter(context['target']['adapter_configuration'], context['directory'] / 'transport')
            else:
                adapter = get_adapter(call.adapter, call.endpoint)
            resp = adapter.chat(req, call)
            
            if context is not None:
                from .qualification import assess
                resp.qualification = assess(self, context, resp.execution)
                recorded = True
                if probe is not None:
                    probe['result'] = resp.qualification
                if resp.qualification['application_status'] != 'PASS':
                    resp.error = 'APPLICATION_' + resp.qualification['application_status']

            # Calculate cost
            resp.cost_usd = self.pricebook.cost(call, resp.tokens["input"], resp.tokens["output"])
            resp.model = call.model_id
            resp.provider = call.provider
            resp.autoshrink_trace = autoshrink_trace if autoshrink_trace else None
            
            # Validate JSON schema if present
            if context is None and req.instructions.get("json_schema"):
                is_valid = validate_json_schema(resp.text, req.instructions["json_schema"])
                if not is_valid:
                    resp.error = "JSON_SCHEMA_VALIDATION_FAILED"
            
            # Record metrics for successful request
            latency_ms = (time.time() - start_time) * 1000
            record_request(
                provider=call.provider,
                model=call.model_id,
                call_id=call.call_id,
                input_tokens=resp.tokens["input"],
                output_tokens=resp.tokens["output"],
                latency_ms=latency_ms,
                cost_usd=resp.cost_usd,
                status="error" if context is not None and resp.error else "success",
                error_code=resp.error if context is not None else None
            )
            
            return resp
            
        except Exception as e:
            if context is not None and not recorded:
                from .qualification import assess
                evidence = assess(self, context, failure=type(e).__name__ + ': ' + str(e))
                if probe is not None:
                    probe['result'] = evidence
            # Record metrics for failed request
            latency_ms = (time.time() - start_time) * 1000
            record_request(
                provider=getattr(call, 'provider', 'unknown') if 'call' in locals() else 'unknown',
                model=getattr(call, 'model_id', 'unknown') if 'call' in locals() else 'unknown',
                call_id=getattr(call, 'call_id', 'unknown') if 'call' in locals() else 'unknown',
                input_tokens=0,
                output_tokens=0,
                latency_ms=latency_ms,
                cost_usd=0.0,
                status="error",
                error_code=str(e)
            )
            raise

def get_adapter(name: str, endpoint: str | None = None):
    """Get adapter by name."""
    # This will be implemented in Task D
    from .adapters import get_adapter as _get_adapter
    return _get_adapter(name, endpoint)
