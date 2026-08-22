"""ByteDance Volcano Engine Ark Seedream image adapter."""

from __future__ import annotations

import base64
import os
from typing import ClassVar

import httpx

from ..image_models import (
    ImageAdapterError,
    ImageGenerationRequest,
    ProviderImage,
    ProviderImageResult,
    StructuredError,
)
from .base import ImageAdapter
from .image_http import credential, fetch_image_url, http_error, transport_error


class SeedreamImageAdapter(ImageAdapter):
    _OPTIONS: ClassVar[set[str]] = {"watermark", "output_format"}
    _SEEDREAM_5_PRO_SIZES: ClassVar[dict[tuple[str, str], str]] = {
        ("1K", "1:1"): "1024x1024",
        ("1K", "4:3"): "1152x864",
        ("1K", "3:4"): "864x1152",
        ("1K", "16:9"): "1424x800",
        ("1K", "9:16"): "800x1424",
        ("1K", "3:2"): "1248x832",
        ("1K", "2:3"): "832x1248",
        ("1K", "21:9"): "1568x672",
    }

    def generate_image(self, request: ImageGenerationRequest, call) -> ProviderImageResult:
        unknown = set(request.provider_options) - self._OPTIONS
        if unknown:
            raise ImageAdapterError(
                StructuredError(
                    "unsupported_parameter",
                    f"unsupported Seedream options: {sorted(unknown)}",
                    False,
                )
            )
        size = request.size
        if request.aspect_ratio:
            mapped = self._SEEDREAM_5_PRO_SIZES.get((request.size or "", request.aspect_ratio))
            if mapped is None:
                raise ImageAdapterError(
                    StructuredError(
                        "unsupported_parameter",
                        "Seedream aspect_ratio requires a documented resolution/aspect mapping",
                        False,
                    )
                )
            size = mapped
        payload = {
            "model": call.model_id,
            "prompt": request.prompt,
            "response_format": "b64_json",
            "stream": False,
            "sequential_image_generation": "disabled",
            **request.provider_options,
        }
        if size:
            payload["size"] = size
        if call.provider == "seedream":
            default_endpoint = "https://ark.ap-southeast.bytepluses.com/api/v3/images/generations"
            credential_provider = "seedream"
        else:
            default_endpoint = "https://ark.cn-beijing.volces.com/api/v3/images/generations"
            credential_provider = "bytedance"
        try:
            with httpx.Client(timeout=120.0) as client:
                response = client.post(
                    os.getenv(
                        "SEEDREAM_ENDPOINT",
                        default_endpoint,
                    ),
                    headers={"Authorization": f"Bearer {credential(credential_provider)}"},
                    json=payload,
                )
        except ImageAdapterError:
            raise
        except httpx.HTTPError as exc:
            raise transport_error(exc, ambiguous_after_send=True) from exc
        if not response.is_success:
            raise http_error(response)
        try:
            body = response.json()
            outputs = []
            for item in body["data"]:
                if item.get("b64_json"):
                    outputs.append(
                        ProviderImage(base64.b64decode(item["b64_json"]), item.get("mime_type"))
                    )
                elif item.get("url"):
                    raw, mime = fetch_image_url(item["url"])
                    outputs.append(ProviderImage(raw, mime))
                else:
                    raise ValueError("image item has neither b64_json nor url")
        except ImageAdapterError:
            raise
        except Exception as exc:
            raise ImageAdapterError(
                StructuredError(
                    "invalid_response", "Seedream returned an invalid image response", False
                )
            ) from exc
        return ProviderImageResult(
            tuple(outputs),
            body.get("id") or body.get("request_id"),
            body.get("model"),
            payload,
            body.get("usage", {}),
        )
