from __future__ import annotations

import struct
from unittest.mock import Mock, patch

import pytest

from thinker import (
    BudgetLedger,
    CompletionLedger,
    ImageGenerationRequest,
    ImageGenerationResponse,
    Thinker,
    map_images,
    request_fingerprint,
)
from thinker.image_inspect import inspect_image, normalize_image_output
from thinker.image_models import (
    ImageAdapterError,
    ProviderImage,
    ProviderImageResult,
    StructuredError,
)
from thinker.pricebook import PriceBook
from thinker.registry.schema import Catalog, ImageLimits, ImagePrice, Limits, Price, RegistryCall
from thinker.registry.store import RegistryStore


def png(width=3, height=2):
    return (
        b"\x89PNG\r\n\x1a\n"
        + b"\x00\x00\x00\rIHDR"
        + struct.pack(">II", width, height)
        + b"\x08\x02\x00\x00\x00"
    )


def image_call(*, known=True, max_n=1):
    return RegistryCall(
        call_id="fake:model.image_generation",
        provider="fake",
        model_id="model",
        kind="image_generation",
        modality="image_output",
        caps=["images_out"],
        limits=Limits(max_input_tokens=0, max_output_tokens=0),
        price=Price(input_per_1k=0, output_per_1k=0),
        adapter="fake_image",
        payload_style="fake",
        image_limits=ImageLimits(supported_sizes=["1K"], max_n=max_n),
        image_price=ImagePrice(known=known, per_image=0.25 if known else None),
    )


def thinker(tmp_path, call=None):
    store = RegistryStore()
    store.apply_catalog(Catalog(calls=[call or image_call()]), "registry-sha", "test")
    return Thinker(store, Mock(spec=PriceBook), CompletionLedger(str(tmp_path / "ledger.db")))


def request(**changes):
    values = {
        "request_id": "req-1",
        "call_id": "fake:model.image_generation",
        "prompt": "Draw a square",
        "size": "1K",
    }
    values.update(changes)
    return ImageGenerationRequest(**values)


def success_result(count=1):
    return ProviderImageResult(
        tuple(ProviderImage(png(), "image/jpeg") for _ in range(count)),
        "provider-response",
        "provider-model",
        {"model": "model", "size": "1K"},
        {"generated_images": count},
    )


def test_typed_request_roundtrip_and_validation():
    original = request(provider_options={"flag": True}, caller_metadata={"condition": "c1"})
    assert ImageGenerationRequest.from_dict(original.to_dict()) == original
    with pytest.raises(ValueError):
        request(request_id="")
    with pytest.raises(ValueError):
        request(provider_options={"bad": object()})


def test_typed_response_roundtrip():
    output = normalize_image_output(0, png())
    response = ImageGenerationResponse(
        "r",
        "fake:model.image_generation",
        "fake",
        "model",
        "model",
        "provider-model",
        "sha",
        "response",
        {"size": "1K"},
        (output,),
        {},
        0.25,
        1.0,
        (),
        {},
        "success",
        None,
    )
    assert ImageGenerationResponse.from_dict(response.to_dict()) == response


def test_request_fingerprint_stable_and_parameter_sensitive():
    first = request_fingerprint(request())
    assert first == request_fingerprint(request())
    assert first != request_fingerprint(request(size=None))
    assert first != request_fingerprint(request(prompt="Different"))
    assert first != request_fingerprint(request(caller_metadata={"replicate": "2"}))
    assert first == request_fingerprint(request(max_cost_usd=99, max_attempts=1))


def test_png_mime_dimensions_and_sha_override_claim():
    mime, width, height = inspect_image(png(7, 5))
    assert (mime, width, height) == ("image/png", 7, 5)
    result = normalize_image_output(0, png(7, 5), "image/jpeg")
    assert result.mime_type == "image/png"
    assert len(result.sha256) == 64


def test_registry_image_kind_and_exact_resolver(tmp_path):
    instance = thinker(tmp_path)
    call = instance.store.get_call("fake:model.image_generation")
    assert call.kind == "image_generation"
    assert call.modality == "image_output"


def test_success_provenance_effective_parameters_and_metadata(tmp_path):
    instance = thinker(tmp_path)
    adapter = Mock()
    adapter.generate_image.return_value = success_result()
    with patch("thinker.image_runtime.get_adapter", return_value=adapter):
        response = instance.generate_image(
            request(caller_metadata={"experiment": "opaque"}, max_cost_usd=0.3)
        )
    assert response.status == "success"
    assert response.registry_sha256 == "registry-sha"
    assert response.requested_model_id == response.resolved_model_id == "model"
    assert response.provider_model_id == "provider-model"
    assert response.provider_response_id == "provider-response"
    assert response.effective_parameters["size"] == "1K"
    assert response.caller_metadata == {"experiment": "opaque"}
    assert response.outputs[0].mime_type == "image/png"


def test_observability_failure_does_not_hide_authoritative_response(tmp_path):
    instance = thinker(tmp_path)
    adapter = Mock(generate_image=Mock(return_value=success_result()))
    with (
        patch("thinker.image_runtime.get_adapter", return_value=adapter),
        patch("thinker.image_runtime.record_request", side_effect=OSError("metrics unavailable")),
    ):
        response = instance.generate_image(request())
    assert response.status == "success"
    assert response.outputs[0].data == png()


def test_hard_budget_rejects_before_provider(tmp_path):
    instance = thinker(tmp_path)
    adapter = Mock()
    with patch("thinker.image_runtime.get_adapter", return_value=adapter):
        response = instance.generate_image(request(max_cost_usd=0.2))
    assert response.error.category == "budget_rejected"
    adapter.generate_image.assert_not_called()


def test_unknown_cost_fails_closed_before_provider(tmp_path):
    instance = thinker(tmp_path, image_call(known=False))
    adapter = Mock()
    with patch("thinker.image_runtime.get_adapter", return_value=adapter):
        response = instance.generate_image(request())
    assert response.error.category == "budget_rejected"
    adapter.generate_image.assert_not_called()


def test_unsupported_parameter_rejected_before_provider(tmp_path):
    instance = thinker(tmp_path)
    adapter = Mock()
    with patch("thinker.image_runtime.get_adapter", return_value=adapter):
        response = instance.generate_image(request(seed=4))
    assert response.error.category == "unsupported_parameter"
    adapter.generate_image.assert_not_called()


def test_run_budget_reservation_commit_release():
    ledger = BudgetLedger(1.0)
    assert ledger.reserve("a", 0.6)
    assert not ledger.reserve("b", 0.5)
    ledger.commit("a", 0.55)
    assert ledger.committed_usd == pytest.approx(0.55)
    assert ledger.reserve("b", 0.4)
    ledger.release("b")
    assert ledger.remaining_usd == pytest.approx(0.45)


def test_429_retry_and_attempt_recording(tmp_path):
    instance = thinker(tmp_path)
    adapter = Mock()
    adapter.generate_image.side_effect = [
        ImageAdapterError(
            StructuredError("rate_limit", "slow down", True, 429, retry_after_seconds=0)
        ),
        success_result(),
    ]
    with (
        patch("thinker.image_runtime.get_adapter", return_value=adapter),
        patch("thinker.image_runtime.time.sleep"),
    ):
        response = instance.generate_image(request())
    assert response.status == "success"
    assert [attempt.status for attempt in response.attempts] == ["error", "success"]


def test_5xx_retries_but_4xx_does_not(tmp_path):
    instance = thinker(tmp_path)
    adapter = Mock()
    adapter.generate_image.side_effect = [
        ImageAdapterError(
            StructuredError("provider_5xx", "bad gateway", True, 502, retry_after_seconds=0)
        ),
        success_result(),
    ]
    with patch("thinker.image_runtime.get_adapter", return_value=adapter):
        assert instance.generate_image(request()).status == "success"
    instance2 = thinker(tmp_path / "other")
    adapter2 = Mock()
    adapter2.generate_image.side_effect = ImageAdapterError(
        StructuredError("provider_4xx", "bad", False, 400)
    )
    with patch("thinker.image_runtime.get_adapter", return_value=adapter2):
        response = instance2.generate_image(request())
    assert len(response.attempts) == 1


def test_ambiguous_timeout_is_not_retried(tmp_path):
    instance = thinker(tmp_path)
    adapter = Mock()
    adapter.generate_image.side_effect = ImageAdapterError(
        StructuredError("timeout", "unknown completion", False, ambiguous_after_send=True)
    )
    with patch("thinker.image_runtime.get_adapter", return_value=adapter):
        response = instance.generate_image(request())
    assert len(response.attempts) == 1


def test_idempotent_success_replay_without_provider(tmp_path):
    instance = thinker(tmp_path)
    adapter = Mock()
    adapter.generate_image.return_value = success_result()
    with patch("thinker.image_runtime.get_adapter", return_value=adapter):
        first = instance.generate_image(request())
        second = instance.generate_image(request())
    assert first.outputs[0].data == second.outputs[0].data
    assert second.replayed
    assert adapter.generate_image.call_count == 1


def test_request_id_fingerprint_conflict(tmp_path):
    instance = thinker(tmp_path)
    adapter = Mock(generate_image=Mock(return_value=success_result()))
    with patch("thinker.image_runtime.get_adapter", return_value=adapter):
        assert instance.generate_image(request()).status == "success"
        conflict = instance.generate_image(request(prompt="Changed"))
    assert conflict.error.category == "idempotency_conflict"
    assert adapter.generate_image.call_count == 1


def test_failure_requires_explicit_resume(tmp_path):
    instance = thinker(tmp_path)
    adapter = Mock()
    adapter.generate_image.side_effect = [
        ImageAdapterError(StructuredError("provider_4xx", "bad", False, 400)),
        success_result(),
    ]
    with patch("thinker.image_runtime.get_adapter", return_value=adapter):
        first = instance.generate_image(request())
        replay = instance.generate_image(request())
        resumed = instance.generate_image(request(retry_failed=True))
    assert first.status == replay.status == "error"
    assert replay.replayed
    assert resumed.status == "success"
    assert adapter.generate_image.call_count == 2


def test_batch_association_budget_and_no_duplicate(tmp_path):
    instance = thinker(tmp_path, image_call(max_n=1))
    adapter = Mock(generate_image=Mock(return_value=success_result()))
    requests = [request(request_id="a"), request(request_id="b")]
    with patch("thinker.image_runtime.get_adapter", return_value=adapter):
        first = map_images(instance, requests, budget_usd=0.5)
        second = map_images(instance, requests, budget_usd=0.5)
    assert [item.request_id for item in first] == ["a", "b"]
    assert all(item.status == "success" for item in first)
    assert all(item.replayed for item in second)
    assert adapter.generate_image.call_count == 2


def test_batch_budget_stops_before_second_provider_call(tmp_path):
    instance = thinker(tmp_path)
    adapter = Mock(generate_image=Mock(return_value=success_result()))
    with patch("thinker.image_runtime.get_adapter", return_value=adapter):
        responses = map_images(
            instance, [request(request_id="a"), request(request_id="b")], budget_usd=0.25
        )
    assert [item.status for item in responses] == ["success", "error"]
    assert responses[1].error.category == "budget_rejected"
    assert adapter.generate_image.call_count == 1
