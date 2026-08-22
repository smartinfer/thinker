"""
Core Thinker class for unified LLM access.

This module provides the main Thinker class that orchestrates the registry-driven
routing system and ThinkerQL request processing.

Author: Anjan Goswami
"""

import time
from typing import TYPE_CHECKING, Dict, Any
from .thinkerql.parse import parse_thinkerql
from .registry.resolver import resolve_call
from .registry.store import RegistryStore
from .registry.loader import load_catalog
from .pricebook import PriceBook
from .tokenization import count_tokens, truncate_last_user, validate_json_schema
from .observability.metrics import record_request

if TYPE_CHECKING:
    from .image_budget import BudgetLedger
    from .image_models import ImageGenerationRequest, ImageGenerationResponse

class Thinker:
    """Main Thinker class for unified LLM access."""
    
    def __init__(self, store: RegistryStore, pricebook: PriceBook, image_ledger=None):
        """Initialize Thinker instance."""
        self.store = store
        self.pricebook = pricebook
        self._image_ledger = image_ledger
        self._image_gateway = None
    
    @classmethod
    def from_files(cls, registry_path: str, pricebook_path: str):
        """Create Thinker instance from registry and pricebook files."""
        cat, sha = load_catalog(registry_path)
        store = RegistryStore()
        store.apply_catalog(cat, sha, registry_path)
        pricebook = PriceBook.from_file(pricebook_path)
        return cls(store, pricebook)
    
    def chat_ql(self, ql: Dict[str, Any]):
        """Process ThinkerQL request."""
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
            
            # Autoshrink if needed
            max_output_tokens = req.instructions.get("max_output_tokens", 256)
            in_tokens = count_tokens(req)
            
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
            adapter = get_adapter(call.adapter)
            resp = adapter.chat(req, call)
            
            # Calculate cost
            resp.cost_usd = self.pricebook.cost(call, resp.tokens["input"], resp.tokens["output"])
            resp.model = call.model_id
            resp.provider = call.provider
            resp.autoshrink_trace = autoshrink_trace if autoshrink_trace else None
            
            # Validate JSON schema if present
            if req.instructions.get("json_schema"):
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
                status="success"
            )
            
            return resp
            
        except Exception as e:
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

    def generate_image(
        self,
        request: "ImageGenerationRequest",
        *,
        budget_ledger: "BudgetLedger | None" = None,
    ) -> "ImageGenerationResponse":
        """Generate image bytes through an exact registry call with durable idempotency."""
        if self._image_gateway is None:
            from .image_ledger import CompletionLedger
            from .image_runtime import ImageGateway, default_ledger_path

            ledger = self._image_ledger or CompletionLedger(default_ledger_path())
            self._image_gateway = ImageGateway(self.store, ledger=ledger)
        return self._image_gateway.generate(request, budget_ledger=budget_ledger)

def get_adapter(name: str):
    """Get adapter by name."""
    # This will be implemented in Task D
    from .adapters import get_adapter as _get_adapter
    return _get_adapter(name)
