"""Google Gemini generateContent image adapter."""

from __future__ import annotations

import base64
import os
from urllib.parse import quote

import httpx

from ..image_models import (
    ImageAdapterError,
    ImageGenerationRequest,
    ProviderImage,
    ProviderImageResult,
    StructuredError,
)
from .base import ImageAdapter
from .image_http import credential, http_error, transport_error


class GoogleImageAdapter(ImageAdapter):
    def generate_image(self, request: ImageGenerationRequest, call) -> ProviderImageResult:
        if request.provider_options:
            raise ImageAdapterError(
                StructuredError(
                    "unsupported_parameter", "Google adapter has no provider_options in v0.3", False
                )
            )
        image_config = {}
        if request.aspect_ratio:
            image_config["aspectRatio"] = request.aspect_ratio
        if request.size:
            image_config["imageSize"] = request.size
        generation_config = {"responseModalities": ["IMAGE"]}
        if image_config:
            generation_config["imageConfig"] = image_config
        payload = {
            "contents": [{"parts": [{"text": request.prompt}]}],
            "generationConfig": generation_config,
        }
        base = os.getenv("GOOGLE_BASE", "https://generativelanguage.googleapis.com").rstrip("/")
        url = f"{base}/v1beta/models/{quote(call.model_id, safe='')}:generateContent"
        try:
            with httpx.Client(timeout=120.0) as client:
                response = client.post(
                    url, headers={"x-goog-api-key": credential("google")}, json=payload
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
            for candidate in body.get("candidates", []):
                for part in candidate.get("content", {}).get("parts", []):
                    inline = part.get("inlineData") or part.get("inline_data")
                    if inline and inline.get("data"):
                        outputs.append(
                            ProviderImage(
                                base64.b64decode(inline["data"]),
                                inline.get("mimeType") or inline.get("mime_type"),
                            )
                        )
            if not outputs:
                raise ValueError("no inline image")
        except Exception as exc:
            raise ImageAdapterError(
                StructuredError(
                    "invalid_response", "Google returned an invalid image response", False
                )
            ) from exc
        usage = body.get("usageMetadata", {})
        effective = {"model": call.model_id, "n": 1, "generation_config": generation_config}
        return ProviderImageResult(
            tuple(outputs), body.get("responseId"), body.get("modelVersion"), effective, usage
        )
