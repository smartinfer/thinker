from __future__ import annotations

import base64
import struct
from unittest.mock import patch

import httpx
import pytest
import respx

from thinker.adapters.google_image import GoogleImageAdapter
from thinker.adapters.openai_image import OpenAIImageAdapter
from thinker.adapters.seedream_image import SeedreamImageAdapter
from thinker.image_models import ImageAdapterError, ImageGenerationRequest
from thinker.image_pricing import estimate_image_request_cost
from thinker.registry.loader import load_catalog


def png(width=4, height=3):
    return (
        b"\x89PNG\r\n\x1a\n"
        + b"\x00\x00\x00\rIHDR"
        + struct.pack(">II", width, height)
        + b"\x08\x02\x00\x00\x00"
    )


def request(call_id, **changes):
    values = {"request_id": "r", "call_id": call_id, "prompt": "A simple structured diagram"}
    values.update(changes)
    return ImageGenerationRequest(**values)


def registry_calls():
    catalog, _ = load_catalog("spec/registry.yaml")
    wanted = {
        "openai": "gpt-image-1.5",
        "google": "gemini-3.1-flash-image",
        "bytedance": "doubao-seedream-4-0-250828",
    }
    return {
        provider: next(call for call in catalog.calls if call.model_id == model_id)
        for provider, model_id in wanted.items()
    }


def registry_call(model_id):
    catalog, _ = load_catalog("spec/registry.yaml")
    return next(call for call in catalog.calls if call.model_id == model_id)


@respx.mock
def test_openai_endpoint_parameters_binary_and_ids():
    call = registry_calls()["openai"]
    route = respx.post("https://api.openai.com/v1/images/generations").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "img-response",
                "model": "gpt-image-1.5",
                "data": [{"b64_json": base64.b64encode(png()).decode()}],
                "usage": {"input_tokens": 5, "output_tokens": 10},
            },
        )
    )
    with patch("thinker.adapters.openai_image.credential", return_value="secret"):
        result = OpenAIImageAdapter().generate_image(
            request(call.call_id, size="1024x1024", quality="high"), call
        )
    payload = route.calls[0].request.content
    assert b'"model":"gpt-image-1.5"' in payload
    assert b'"size":"1024x1024"' in payload
    assert b'"quality":"high"' in payload
    assert result.outputs[0].data == png()
    assert result.provider_response_id == "img-response"
    assert result.provider_model_id == "gpt-image-1.5"


@respx.mock
def test_openai_url_fallback_fetches_bytes():
    call = registry_calls()["openai"]
    respx.post("https://api.openai.com/v1/images/generations").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": [{"url": "https://temporary.example/image"}],
            },
        )
    )
    respx.get("https://temporary.example/image").mock(
        return_value=httpx.Response(200, content=png(), headers={"content-type": "image/png"})
    )
    with patch("thinker.adapters.openai_image.credential", return_value="secret"):
        result = OpenAIImageAdapter().generate_image(request(call.call_id), call)
    assert result.outputs[0].data == png()


def test_openai_unknown_provider_option_rejected_without_http():
    call = registry_calls()["openai"]
    with pytest.raises(ImageAdapterError) as raised:
        OpenAIImageAdapter().generate_image(
            request(call.call_id, provider_options={"api_key": "bad"}), call
        )
    assert raised.value.error.category == "unsupported_parameter"


@respx.mock
def test_google_generate_content_mapping_and_inline_image():
    call = registry_calls()["google"]
    route = respx.post(
        "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.1-flash-image:generateContent"
    ).mock(
        return_value=httpx.Response(
            200,
            json={
                "responseId": "google-response",
                "modelVersion": "gemini-3.1-flash-image-001",
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {
                                    "inlineData": {
                                        "mimeType": "image/png",
                                        "data": base64.b64encode(png()).decode(),
                                    }
                                }
                            ]
                        }
                    }
                ],
                "usageMetadata": {"promptTokenCount": 4},
            },
        )
    )
    with patch("thinker.adapters.google_image.credential", return_value="secret"):
        result = GoogleImageAdapter().generate_image(
            request(call.call_id, size="1K", aspect_ratio="16:9"), call
        )
    body = route.calls[0].request.content
    assert b'"responseModalities":["IMAGE"]' in body
    assert b'"aspectRatio":"16:9"' in body
    assert b'"imageSize":"1K"' in body
    assert result.provider_response_id == "google-response"
    assert result.provider_model_id == "gemini-3.1-flash-image-001"


def test_google_provider_options_rejected():
    call = registry_calls()["google"]
    with pytest.raises(ImageAdapterError) as raised:
        GoogleImageAdapter().generate_image(
            request(call.call_id, provider_options={"unknown": 1}), call
        )
    assert raised.value.error.category == "unsupported_parameter"


@respx.mock
def test_seedream_documented_ark_mapping_and_binary():
    call = registry_calls()["bytedance"]
    route = respx.post("https://ark.cn-beijing.volces.com/api/v3/images/generations").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "ark-response",
                "model": "doubao-seedream-4-0-250828",
                "data": [{"b64_json": base64.b64encode(png()).decode(), "mime_type": "image/png"}],
                "usage": {"generated_images": 1},
            },
        )
    )
    with patch("thinker.adapters.seedream_image.credential", return_value="secret"):
        result = SeedreamImageAdapter().generate_image(
            request(call.call_id, size="2K", provider_options={"watermark": False}), call
        )
    payload = route.calls[0].request.content
    assert b'"response_format":"b64_json"' in payload
    assert b'"sequential_image_generation":"disabled"' in payload
    assert b'"watermark":false' in payload
    assert result.provider_response_id == "ark-response"
    assert result.outputs[0].data == png()


@respx.mock
def test_seedream_5_pro_byteplus_maps_1k_16_9_and_png():
    call = registry_call("dola-seedream-5-0-pro-260628")
    route = respx.post(
        "https://ark.ap-southeast.bytepluses.com/api/v3/images/generations"
    ).mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "seedream-response",
                "model": call.model_id,
                "data": [{"b64_json": base64.b64encode(png()).decode()}],
            },
        )
    )
    with patch("thinker.adapters.seedream_image.credential", return_value="secret"):
        result = SeedreamImageAdapter().generate_image(
            request(
                call.call_id,
                size="1K",
                aspect_ratio="16:9",
                provider_options={"output_format": "png"},
            ),
            call,
        )
    payload = route.calls[0].request.content
    assert b'"model":"dola-seedream-5-0-pro-260628"' in payload
    assert b'"size":"1424x800"' in payload
    assert b'"output_format":"png"' in payload
    assert b'"stream":false' in payload
    assert b'"sequential_image_generation":"disabled"' in payload
    assert result.provider_response_id == "seedream-response"
    assert result.effective_parameters["size"] == "1424x800"


def test_exact_smoke_registry_rows_and_preflight_prices():
    openai = registry_call("gpt-image-2-2026-04-21")
    google = registry_call("gemini-3-pro-image-preview")
    seedream = registry_call("dola-seedream-5-0-pro-260628")
    assert openai.call_id == "openai:gpt-image-2-2026-04-21.image_generation"
    assert openai.image_price.prompt_input_per_1m == 5.0
    assert openai.image_price.image_output_per_1m == 30.0
    assert not estimate_image_request_cost(
        request(openai.call_id, size="1536x1024", quality="medium"), openai
    ).known
    google_cost = estimate_image_request_cost(
        request(google.call_id, size="1K", aspect_ratio="16:9"), google
    )
    assert google_cost.known and 0.134 < google_cost.estimated_max_usd < 0.135
    seedream_cost = estimate_image_request_cost(
        request(seedream.call_id, size="1K", aspect_ratio="16:9"), seedream
    )
    assert seedream_cost.known and seedream_cost.estimated_max_usd == 0.045


@respx.mock
def test_provider_errors_are_structured_and_retry_after_preserved():
    call = registry_calls()["openai"]
    respx.post("https://api.openai.com/v1/images/generations").mock(
        return_value=httpx.Response(
            429,
            headers={"retry-after": "2"},
            json={"error": {"code": "rate_limit", "message": "slow down"}},
        )
    )
    with (
        patch("thinker.adapters.openai_image.credential", return_value="secret"),
        pytest.raises(ImageAdapterError) as raised,
    ):
        OpenAIImageAdapter().generate_image(request(call.call_id), call)
    assert raised.value.error.category == "rate_limit"
    assert raised.value.error.retryable
    assert raised.value.error.retry_after_seconds == 2


def test_google_registry_pricing_uses_size_and_prompt_upper_bound():
    call = registry_calls()["google"]
    estimate = estimate_image_request_cost(request(call.call_id, size="2K"), call)
    assert estimate.known
    assert estimate.estimated_max_usd > 0.101
    assert estimate.estimated_max_usd < 0.102


def test_openai_and_seedream_registry_prices_fail_closed():
    calls = registry_calls()
    assert not estimate_image_request_cost(request(calls["openai"].call_id), calls["openai"]).known
    assert not estimate_image_request_cost(
        request(calls["bytedance"].call_id), calls["bytedance"]
    ).known
