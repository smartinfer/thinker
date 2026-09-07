from __future__ import annotations

import json

import pytest

from thinker.local_models import (
    _cached_weights_complete,
    add_local_model,
    apply_local_models,
    load_local_models,
)
from thinker.registry.store import RegistryStore


def test_add_load_and_apply_portable_local_alias(tmp_path):
    path = tmp_path / "local-models.json"
    entry = add_local_model(
        "state",
        "mlx",
        "mlx-community/Qwen3-8B-4bit",
        tools=True,
        path=path,
    )
    assert entry.route == "mlx:state.chat"
    assert load_local_models(path) == [entry]
    assert "/Users/" not in path.read_text()
    store = RegistryStore()
    assert apply_local_models(store, path) == 1
    call = store.get_call("mlx:state.chat")
    assert call.provider == "mlx"
    assert call.adapter == "mlx"
    assert call.model_id == "mlx-community/Qwen3-8B-4bit"
    assert "tools" in call.caps


def test_alias_file_contains_metadata_not_model_weights_or_paths(tmp_path):
    path = tmp_path / "local-models.json"
    add_local_model("state", "mlx", "mlx-community/Qwen3-8B-4bit", path=path)
    value = json.loads(path.read_text())
    assert value == {
        "version": "thinker.local-models.v1",
        "models": [
            {
                "backend": "mlx",
                "model_id": "mlx-community/Qwen3-8B-4bit",
                "name": "state",
                "tools": False,
            }
        ],
    }


@pytest.mark.parametrize("name", ["State", "bad name", "../state", ""])
def test_invalid_alias_names_fail_closed(tmp_path, name):
    with pytest.raises(ValueError):
        add_local_model(name, "mlx", "mlx-community/Qwen3-8B-4bit", path=tmp_path / "x")


def test_unknown_backend_and_local_path_are_rejected(tmp_path):
    with pytest.raises(ValueError):
        add_local_model("state", "ollama", "repo/model", path=tmp_path / "x")
    with pytest.raises(ValueError):
        add_local_model("state", "mlx", "/tmp/model", path=tmp_path / "x")


def test_cache_availability_requires_complete_weights(tmp_path):
    index = tmp_path / "model.safetensors.index.json"
    index.write_text(json.dumps({"weight_map": {"layer": "model-00001.safetensors"}}))
    values = {"model.safetensors.index.json": str(index)}

    def lookup(_model_id, filename):
        return values.get(filename)

    assert not _cached_weights_complete("repo/model", lookup)
    values["model-00001.safetensors"] = str(tmp_path / "model-00001.safetensors")
    assert _cached_weights_complete("repo/model", lookup)


def test_local_overlay_does_not_silently_replace_registry_route(tmp_path):
    path = tmp_path / "local-models.json"
    entry = add_local_model("state", "mlx", "mlx-community/Qwen3-8B-4bit", path=path)
    store = RegistryStore()
    store.put_call(entry.registry_call())
    with pytest.raises(ValueError, match="already exists"):
        apply_local_models(store, path)
