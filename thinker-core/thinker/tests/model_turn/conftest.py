from __future__ import annotations

import pytest

from thinker.model_turn.models import ModelMessage, ModelTurnRequest
from thinker.model_turn.runtime import ModelTurnRuntime
from thinker.registry.schema import Limits, Price, RegistryCall
from thinker.registry.store import RegistryStore


@pytest.fixture
def fake_call() -> RegistryCall:
    return RegistryCall(
        call_id="fake:deterministic.chat",
        provider="fake",
        model_id="deterministic-v1",
        kind="chat",
        modality="text",
        caps=["tools", "json_mode", "json_schema"],
        limits=Limits(max_input_tokens=8192, max_output_tokens=4096),
        price=Price(input_per_1k=0.001, output_per_1k=0.002),
        adapter="fake",
        payload_style="model_turn_v1_fake",
        aliases=["test:default"],
    )


@pytest.fixture
def runtime(fake_call: RegistryCall) -> ModelTurnRuntime:
    store = RegistryStore()
    store.put_call(fake_call)
    return ModelTurnRuntime(store, thinker_revision="test-revision")


def make_request(scenario: str = "plain_text", **updates: object) -> ModelTurnRequest:
    values: dict[str, object] = {
        "request_id": f"request-{scenario}",
        "requested_route": "fake:deterministic.chat",
        "messages": (ModelMessage(role="user", content="hello"),),
        "metadata": {"fake_scenario": scenario},
        "timeout_ms": 1_000,
    }
    values.update(updates)
    return ModelTurnRequest(**values)
