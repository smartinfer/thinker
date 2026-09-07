"""Portable local-model aliases and Hugging Face cache operations."""

from __future__ import annotations

import json
import os
import platform
from dataclasses import asdict, dataclass
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

from thinker.registry.schema import Limits, Price, RegistryCall
from thinker.registry.store import RegistryStore

LOCAL_MODELS_VERSION = "thinker.local-models.v1"


@dataclass(frozen=True)
class LocalModel:
    name: str
    backend: str
    model_id: str
    tools: bool = False

    @property
    def route(self) -> str:
        return f"{self.backend}:{self.name}.chat"

    def registry_call(self) -> RegistryCall:
        caps = ["model_turn_v1", "json_mode", "json_schema"]
        if self.tools:
            caps.extend(["tools", "multiple_tool_calls", "tool_result_continuation"])
        return RegistryCall(
            call_id=self.route,
            provider=self.backend,
            model_id=self.model_id,
            kind="chat",
            modality="text",
            caps=caps,
            limits=Limits(max_input_tokens=32768, max_output_tokens=4096),
            price=Price(input_per_1k=0.0, output_per_1k=0.0),
            adapter=self.backend,
            payload_style="mlx_lm_python_v1" if self.backend == "mlx" else self.backend,
            aliases=[f"{self.backend}:{self.name}"],
        )


def default_local_models_path() -> Path:
    configured = os.getenv("THINKER_LOCAL_MODELS_CONFIG")
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".config" / "thinker" / "local-models.json"


def load_local_models(path: Path | None = None) -> list[LocalModel]:
    source = path or default_local_models_path()
    if not source.exists():
        return []
    raw = json.loads(source.read_text())
    if not isinstance(raw, dict) or raw.get("version") != LOCAL_MODELS_VERSION:
        raise ValueError("unsupported Thinker local-model configuration")
    models = raw.get("models")
    if not isinstance(models, list):
        raise TypeError("Thinker local-model configuration requires a models list")
    entries: list[LocalModel] = []
    for value in models:
        if not isinstance(value, dict):
            raise TypeError("local-model entry must be an object")
        entry = LocalModel(
            name=_safe_name(value.get("name")),
            backend=str(value.get("backend", "")),
            model_id=str(value.get("model_id", "")),
            tools=bool(value.get("tools", False)),
        )
        if entry.backend != "mlx" or "/" not in entry.model_id:
            raise ValueError("local model must use backend=mlx and a Hugging Face model ID")
        entries.append(entry)
    if len({entry.route for entry in entries}) != len(entries):
        raise ValueError("duplicate local-model route")
    return entries


def save_local_models(entries: list[LocalModel], path: Path | None = None) -> Path:
    target = path or default_local_models_path()
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    target.write_text(
        json.dumps(
            {"version": LOCAL_MODELS_VERSION, "models": [asdict(entry) for entry in entries]},
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    return target


def add_local_model(
    name: str,
    backend: str,
    model_id: str,
    *,
    tools: bool = False,
    path: Path | None = None,
) -> LocalModel:
    entry = LocalModel(name=_safe_name(name), backend=backend, model_id=model_id, tools=tools)
    if backend != "mlx":
        raise ValueError("the local alias registry currently supports backend=mlx")
    if "/" not in model_id or model_id.startswith("/"):
        raise ValueError("model must be a portable Hugging Face repository ID")
    entries = [existing for existing in load_local_models(path) if existing.name != entry.name]
    entries.append(entry)
    entries.sort(key=lambda item: item.route)
    save_local_models(entries, path)
    return entry


def apply_local_models(store: RegistryStore, path: Path | None = None) -> int:
    entries = load_local_models(path)
    for entry in entries:
        if store.get_call(entry.route) is not None:
            raise ValueError(f"local model route already exists: {entry.route}")
        store.put_call(entry.registry_call())
    return len(entries)


def pull_model(model_id: str) -> Path:
    try:
        from huggingface_hub import snapshot_download
    except ImportError as exc:
        raise RuntimeError("install thinker-core[mlx] to pull local MLX models") from exc
    return Path(snapshot_download(repo_id=model_id))


def model_cache_status(model_id: str) -> dict[str, Any]:
    try:
        from huggingface_hub import (
            scan_cache_dir,
            try_to_load_from_cache,
        )
    except ImportError:
        return {"available": False, "size_bytes": None}
    config = try_to_load_from_cache(model_id, "config.json")
    available = isinstance(config, str) and _cached_weights_complete(
        model_id, try_to_load_from_cache
    )
    size: int | None = None
    if available:
        try:
            repo = next(repo for repo in scan_cache_dir().repos if repo.repo_id == model_id)
            size = int(repo.size_on_disk)
        except (StopIteration, OSError):
            pass
    return {"available": available, "size_bytes": size}


def _cached_weights_complete(model_id: str, lookup: Any) -> bool:
    if isinstance(lookup(model_id, "model.safetensors"), str):
        return True
    index_path = lookup(model_id, "model.safetensors.index.json")
    if not isinstance(index_path, str):
        return False
    try:
        weight_map = json.loads(Path(index_path).read_text()).get("weight_map")
    except (AttributeError, json.JSONDecodeError, OSError):
        return False
    if not isinstance(weight_map, dict) or not weight_map:
        return False
    files = {value for value in weight_map.values() if isinstance(value, str)}
    return bool(files) and all(isinstance(lookup(model_id, name), str) for name in files)


def local_doctor(path: Path | None = None) -> dict[str, Any]:
    system, architecture = platform.system(), platform.machine()
    configured = load_local_models(path)
    return {
        "apple_silicon": system == "Darwin" and architecture == "arm64",
        "system": system,
        "architecture": architecture,
        "mlx_available": _package_version("mlx") is not None,
        "mlx_version": _package_version("mlx"),
        "mlx_lm_available": _package_version("mlx-lm") is not None,
        "mlx_lm_version": _package_version("mlx-lm"),
        "hugging_face_cache": _hugging_face_cache(),
        "configured_models": len(configured),
        "configured_routes": [entry.route for entry in configured],
    }


def _safe_name(value: object) -> str:
    name = str(value or "")
    if not name or any(
        character not in "abcdefghijklmnopqrstuvwxyz0123456789-_" for character in name
    ):
        raise ValueError("local model name must use lowercase letters, digits, '-' or '_'")
    return name


def _package_version(package: str) -> str | None:
    try:
        return version(package)
    except PackageNotFoundError:
        return None


def _hugging_face_cache() -> str:
    try:
        from huggingface_hub.constants import HF_HUB_CACHE
    except ImportError:
        return "unavailable (install thinker-core[mlx])"
    return str(HF_HUB_CACHE)
