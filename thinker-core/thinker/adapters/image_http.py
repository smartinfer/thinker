"""Shared HTTP failure normalization for image adapters."""

from __future__ import annotations

import httpx

from ..image_models import ImageAdapterError, StructuredError


def credential(provider: str) -> str:
    from ..registry.auth import get_credentials

    key = get_credentials("auto").get(provider)
    if not key:
        raise ImageAdapterError(
            StructuredError("adapter_error", f"credential not configured for {provider}", False)
        )
    return key


def http_error(response: httpx.Response) -> ImageAdapterError:
    status = response.status_code
    code = None
    message = f"provider returned HTTP {status}"
    try:
        body = response.json()
        error = body.get("error", body) if isinstance(body, dict) else {}
        if isinstance(error, dict):
            code = str(error.get("code")) if error.get("code") is not None else None
            if error.get("message"):
                message = str(error["message"])
    except (TypeError, ValueError):
        pass
    if status == 429:
        category, retryable = "rate_limit", True
    elif 500 <= status <= 599:
        category, retryable = "provider_5xx", status in {500, 502, 503, 504}
    elif status in {400, 422} and code and "safety" in code.lower():
        category, retryable = "safety_rejection", False
    else:
        category, retryable = "provider_4xx", False
    retry_after = response.headers.get("retry-after")
    try:
        retry_after_seconds = float(retry_after) if retry_after is not None else None
    except ValueError:
        retry_after_seconds = None
    return ImageAdapterError(
        StructuredError(
            category=category,
            message=message,
            retryable=retryable,
            provider_status_code=status,
            provider_error_code=code,
            retry_after_seconds=retry_after_seconds,
        )
    )


def transport_error(exc: Exception, *, ambiguous_after_send: bool = True) -> ImageAdapterError:
    if isinstance(exc, httpx.TimeoutException):
        category = "timeout"
    elif isinstance(exc, (httpx.ConnectError, httpx.NetworkError)):
        category = "connection"
    else:
        category = "adapter_error"
    if isinstance(exc, httpx.ConnectError):
        ambiguous_after_send = False
    return ImageAdapterError(
        StructuredError(
            category=category,
            message=f"{category} while invoking provider",
            retryable=category in {"timeout", "connection"} and not ambiguous_after_send,
            ambiguous_after_send=ambiguous_after_send,
        )
    )


def fetch_image_url(url: str, headers: dict[str, str] | None = None) -> tuple[bytes, str | None]:
    try:
        with httpx.Client(follow_redirects=True, timeout=60.0) as client:
            response = client.get(url, headers=headers)
    except httpx.HTTPError as exc:
        raise transport_error(exc, ambiguous_after_send=False) from exc
    if not response.is_success:
        raise http_error(response)
    return response.content, response.headers.get("content-type", "").split(";", 1)[0] or None
