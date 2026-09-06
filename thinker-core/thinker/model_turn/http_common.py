"""Shared HTTP, credential, and error normalization for model-turn providers."""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from typing import cast

import httpx

from thinker.config import get_config
from thinker.registry.secure_credentials import SecureCredentials

from .errors import ModelTurnError, ModelTurnErrorCode, ModelTurnProviderException

CredentialResolver = Callable[[str], str | None]

PROVIDER_ENV_VARS: dict[str, tuple[str, ...]] = {
    "openai": ("OPENAI_API_KEY",),
    "anthropic": ("ANTHROPIC_API_KEY",),
    "gemini": ("GEMINI_API_KEY", "GOOGLE_API_KEY"),
    "google": ("GEMINI_API_KEY", "GOOGLE_API_KEY"),
    "together": ("TOGETHER_API_KEY",),
    "deepseek": ("DEEPSEEK_API_KEY",),
    "moonshot": ("MOONSHOT_API_KEY",),
    "dashscope": ("DASHSCOPE_API_KEY",),
    "zhipu": ("ZHIPU_API_KEY",),
    "groq": ("GROQ_API_KEY",),
    "mistral": ("MISTRAL_API_KEY",),
    "fireworks": ("FIREWORKS_API_KEY",),
    "openrouter": ("OPENROUTER_API_KEY",),
    "xai": ("XAI_API_KEY",),
    "cohere": ("COHERE_API_KEY",),
    "huggingface": ("HF_TOKEN",),
}


def resolve_credential(provider: str) -> str | None:
    """Resolve a provider credential without exposing provider mechanics to callers."""

    for env_name in PROVIDER_ENV_VARS.get(provider, ()):
        value = os.getenv(env_name)
        if value:
            return value
    aliases = ("gemini", "google") if provider in {"gemini", "google"} else (provider,)
    try:
        config = get_config()
        credentials = SecureCredentials(
            storage_type=config.credentials_storage_type,
            keystore_path=config.keystore_path,
        )
        for alias in aliases:
            value = credentials.get(alias)
            if value:
                return cast(str, value)
    except Exception:  # noqa: BLE001 -- credential lookup fails closed
        return None
    return None


def provider_error(
    code: ModelTurnErrorCode,
    message: str,
    retryable: bool = False,
) -> ModelTurnProviderException:
    return ModelTurnProviderException(
        ModelTurnError(code=code, message=message, retryable=retryable)
    )


def http_error(response: httpx.Response, provider: str) -> ModelTurnProviderException:
    """Map provider HTTP status without copying provider bodies or credentials."""

    status = response.status_code
    if status in (401, 403):
        code, summary, retryable = (
            ModelTurnErrorCode.AUTHENTICATION_FAILURE,
            "authentication failed",
            False,
        )
    elif status == 429:
        code, summary, retryable = ModelTurnErrorCode.RATE_LIMITED, "rate limit reached", True
    elif status in (408, 504):
        code, summary, retryable = ModelTurnErrorCode.REQUEST_TIMEOUT, "request timed out", True
    elif status in (502, 503):
        code, summary, retryable = (
            ModelTurnErrorCode.PROVIDER_OVERLOADED,
            "provider is overloaded",
            True,
        )
    elif status == 404:
        code, summary, retryable = (
            ModelTurnErrorCode.MODEL_UNAVAILABLE,
            "model is unavailable",
            False,
        )
    else:
        code, summary, retryable = (
            ModelTurnErrorCode.UNKNOWN_PROVIDER_FAILURE,
            "request failed",
            status >= 500,
        )
    return ModelTurnProviderException(
        ModelTurnError(
            code=code,
            message=f"{provider} {summary}",
            retryable=retryable,
            provider_status=status,
            provider_code=_provider_code(response),
        )
    )


def _provider_code(response: httpx.Response) -> str | None:
    try:
        body = response.json()
        error = body.get("error", {}) if isinstance(body, dict) else {}
        raw = error.get("code") or error.get("type") if isinstance(error, dict) else None
        if not isinstance(raw, str):
            return None
        candidate = raw[:100]
        if candidate and all(char.isalnum() or char in "._-" for char in candidate):
            return candidate
    except (AttributeError, TypeError, ValueError, json.JSONDecodeError):
        return None
    return None
