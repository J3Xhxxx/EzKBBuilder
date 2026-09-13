from __future__ import annotations

from typing import Any

import requests

from .base import (
    ModelAuthenticationError,
    ModelRateLimitError,
    ModelResponseError,
    ModelTimeoutError,
    ModelTransportError,
)


def post_json(url: str, *, headers: dict[str, str], body: dict[str, Any], timeout: int, verify_tls: bool) -> tuple[dict[str, Any], requests.Response]:
    try:
        response = requests.post(url, headers=headers, json=body, timeout=timeout, verify=verify_tls)
    except requests.Timeout as exc:
        raise ModelTimeoutError(f"model request timed out after {timeout}s") from exc
    except requests.RequestException as exc:
        raise ModelTransportError(f"model request failed: {type(exc).__name__}: {exc}") from exc
    if response.status_code in {401, 403}:
        raise ModelAuthenticationError(f"model service rejected credentials (HTTP {response.status_code})")
    if response.status_code == 429:
        raise ModelRateLimitError("model service rate limit exceeded")
    if not response.ok:
        retryable = response.status_code >= 500
        error = ModelTransportError if retryable else ModelResponseError
        # Upstream bodies can echo prompts or internal diagnostics. Keep the
        # persisted/UI error useful without copying remote content into logs.
        raise error(f"model service returned HTTP {response.status_code}")
    try:
        value = response.json()
    except ValueError as exc:
        raise ModelResponseError("model service returned a non-JSON HTTP body") from exc
    if not isinstance(value, dict):
        raise ModelResponseError("model service JSON body must be an object")
    return value, response


def chat_content(payload: dict[str, Any]) -> str:
    try:
        content = payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ModelResponseError("response has no choices[0].message.content") from exc
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text") if isinstance(item.get("text"), str) else item.get("content")
                if isinstance(text, str):
                    parts.append(text)
        return "".join(parts)
    return str(content or "")


def safe_usage(payload: dict[str, Any]) -> dict[str, int] | None:
    usage = payload.get("usage")
    if not isinstance(usage, dict):
        return None
    result: dict[str, int] = {}
    for key in ("prompt_tokens", "completion_tokens", "total_tokens", "input_tokens", "output_tokens"):
        if isinstance(usage.get(key), int):
            result[key] = usage[key]
    return result or None
