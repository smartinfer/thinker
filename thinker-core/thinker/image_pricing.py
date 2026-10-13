"""Pre-call image price estimation from immutable registry data."""

from __future__ import annotations

from .image_models import CostEstimate, ImageGenerationRequest
from .registry.schema import RegistryCall


def estimate_image_request_cost(
    request: ImageGenerationRequest, call: RegistryCall
) -> CostEstimate:
    price = call.image_price
    if price is None or not price.known:
        return CostEstimate(None, "registry image price is unknown", False)

    unit: float | None = None
    basis = ""
    size_quality = f"{request.size or 'default'}|{request.quality or 'default'}"
    if size_quality in price.per_image_by_size_quality:
        unit, basis = price.per_image_by_size_quality[size_quality], f"size_quality:{size_quality}"
    elif request.size and request.size in price.per_image_by_size:
        unit, basis = price.per_image_by_size[request.size], f"size:{request.size}"
    elif request.quality and request.quality in price.per_image_by_quality:
        unit, basis = price.per_image_by_quality[request.quality], f"quality:{request.quality}"
    elif price.per_image is not None:
        unit, basis = price.per_image, "per_image"
    if unit is None:
        return CostEstimate(None, "no registry price matches effective parameters", False)

    # UTF-8 bytes are a deliberately conservative upper bound on prompt tokens.
    prompt_bound = len(request.prompt.encode("utf-8")) * price.prompt_input_per_1m / 1_000_000
    total = request.n * unit + prompt_bound
    return CostEstimate(round(total, 9), f"{basis}+utf8_prompt_upper_bound", True)
