"""Exercise image dispatch through the adapter factory shared with chat."""

import base64
import struct

import httpx
import pytest
import respx

from thinker import CompletionLedger, ImageGenerationRequest, Thinker
from thinker.adapters import get_adapter
from thinker.adapters.google_image import GoogleImageAdapter
from thinker.adapters.openai_image import OpenAIImageAdapter
from thinker.adapters.seedream_image import SeedreamImageAdapter
from thinker.pricebook import PriceBook
from thinker.registry.loader import load_catalog
from thinker.registry.store import RegistryStore


@pytest.mark.parametrize("name, expected", [
    ("openai_image", OpenAIImageAdapter),
    ("google_image", GoogleImageAdapter),
    ("seedream_image", SeedreamImageAdapter),
])
def test_image_adapters_use_per_call_factory(name, expected):
    first, second = get_adapter(name), get_adapter(name)
    assert isinstance(first, expected)
    assert isinstance(second, expected)
    assert first is not second


@respx.mock
def test_public_image_dispatch_and_replay_use_real_adapter(tmp_path, monkeypatch):
    catalog, checksum = load_catalog("spec/registry.yaml")
    store = RegistryStore()
    store.apply_catalog(catalog, checksum, "spec/registry.yaml")
    instance = Thinker(store, PriceBook(), CompletionLedger(str(tmp_path / "images.db")))
    png = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + struct.pack(">II", 4, 3) + b"\x08\x02\x00\x00\x00"
    monkeypatch.setenv("OPENAI_BASE", "https://image-provider.test")
    monkeypatch.setattr("thinker.adapters.openai_image.credential", lambda provider: "test-key")
    route = respx.post("https://image-provider.test/v1/images/generations").mock(
        return_value=httpx.Response(200, json={"data": [{"b64_json": base64.b64encode(png).decode()}]})
    )
    request = ImageGenerationRequest(
        request_id="integrated-image", call_id="openai:gpt-image-1-5.image_generation",
        prompt="A square", size="1024x1024", allow_unknown_cost=True,
    )
    result = instance.generate_image(request)
    assert result.status == "success", result.error
    assert result.outputs[0].data == png
    assert result.registry_sha256 == checksum
    replay = instance.generate_image(request)
    assert replay.status == "success"
    assert replay.outputs[0].data == png
    assert route.call_count == 1
