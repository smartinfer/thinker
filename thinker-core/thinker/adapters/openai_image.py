"""OpenAI Images API adapter."""

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


class OpenAIImageAdapter(ImageAdapter):
    _OPTIONS: ClassVar[set[str]] = {
        "output_format",
        "background",
        "moderation",
        "output_compression",
    }

    def generate_image(self, request: ImageGenerationRequest, call) -> ProviderImageResult:
        unknown = set(request.provider_options) - self._OPTIONS
        if unknown:
            raise ImageAdapterError(
                StructuredError(
                    "unsupported_parameter", f"unsupported OpenAI options: {sorted(unknown)}", False
                )
            )
        payload = {
            "model": call.model_id,
            "prompt": request.prompt,
            "n": request.n,
            **request.provider_options,
        }
        if request.size:
            payload["size"] = request.size
        if request.quality:
            payload["quality"] = request.quality
        payload.setdefault("output_format", "png")
        try:
            with httpx.Client(timeout=120.0) as client:
                response = client.post(
                    f"{os.getenv('OPENAI_BASE', 'https://api.openai.com').rstrip('/')}/v1/images/generations",
                    headers={"Authorization": f"Bearer {credential('openai')}"},
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
                        ProviderImage(
                            base64.b64decode(item["b64_json"]), f"image/{payload['output_format']}"
                        )
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
                    "invalid_response", "OpenAI returned an invalid image response", False
                )
            ) from exc
        return ProviderImageResult(
            tuple(outputs),
            body.get("id"),
            body.get("model"),
            payload,
            body.get("usage", {}) if isinstance(body.get("usage", {}), dict) else {},
        )
