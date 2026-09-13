from __future__ import annotations

from typing import Mapping

from .base import ModelProviderError, ModelRequest, ModelResponse
from .http import chat_content, post_json, safe_usage


class FastGPTProvider:
    """Compatibility adapter for existing FastGPT App endpoints."""

    name = "fastgpt"

    def __init__(self, *, api_base: str, api_keys: Mapping[str, str], app_ids: Mapping[str, str], verify_tls: bool = True) -> None:
        self.api_base = str(api_base or "").strip().rstrip("/")
        self.api_keys = {str(key): str(value).strip() for key, value in api_keys.items()}
        self.app_ids = {str(key): str(value).strip() for key, value in app_ids.items()}
        self.verify_tls = bool(verify_tls)
        if not self.api_base:
            raise ModelProviderError("FastGPT provider requires api_base")

    def complete(self, request: ModelRequest) -> ModelResponse:
        key = self.api_keys.get(request.role) or self.api_keys.get("default") or ""
        app_id = self.app_ids.get(request.role) or ""
        if not key or not app_id:
            raise ModelProviderError(f"FastGPT provider is missing {request.role} app ID or API key")
        # Existing FastGPT applications own their System Prompt. It is included in
        # trace metadata locally but is not duplicated as a user message here.
        body = {
            "appId": app_id, "stream": False, "detail": False,
            "messages": [{"role": "user", "content": request.user_prompt}],
        }
        payload, response = post_json(
            self.api_base + "/v1/chat/completions",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json", "X-Trace-Id": request.trace_id},
            body=body, timeout=request.timeout_seconds, verify_tls=self.verify_tls,
        )
        return ModelResponse(
            text=chat_content(payload), provider=self.name,
            model=str(payload.get("model") or "fastgpt-app"),
            request_id=str(payload.get("id") or response.headers.get("x-request-id") or ""),
            usage=safe_usage(payload),
            metadata={"trace_id": request.trace_id, "app_id": app_id, "system_prompt_managed_remotely": True},
        )
