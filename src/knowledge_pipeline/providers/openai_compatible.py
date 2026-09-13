from __future__ import annotations

from typing import Any, Mapping
from urllib.parse import urlsplit, urlunsplit

from .base import ModelProviderError, ModelRequest, ModelResponse
from .http import chat_content, post_json, safe_usage


def _chat_completions_url(api_base: str) -> str:
    try:
        parts = urlsplit(api_base)
        valid_host = bool(parts.hostname)
        _ = parts.port
    except ValueError:
        raise ModelProviderError("KP_API_BASE must be a valid HTTP(S) API address") from None
    if parts.scheme not in {"http", "https"} or not valid_host:
        raise ModelProviderError("KP_API_BASE must be a valid HTTP(S) API address")
    if parts.username is not None or parts.password is not None or parts.query or parts.fragment:
        raise ModelProviderError("KP_API_BASE must not contain credentials, a query, or a fragment; put the key in KP_API_KEY")
    path = parts.path.rstrip("/")
    if not path:
        path = "/v1/chat/completions"
    elif not path.endswith("/chat/completions"):
        path += "/chat/completions"
    return urlunsplit((parts.scheme, parts.netloc, path, "", ""))


class OpenAICompatibleProvider:
    name = "openai-compatible"

    def __init__(
        self, *, api_base: str, api_key: str, model: str,
        role_models: Mapping[str, str] | None = None,
        verify_tls: bool = True, supports_json_schema: bool = False,
        enable_thinking: bool | None = None, thinking_budget: int | None = None,
    ) -> None:
        self.api_base = str(api_base or "").strip().rstrip("/")
        self.api_key = str(api_key or "").strip()
        self.model = str(model or "").strip()
        self.role_models = {str(key): str(value).strip() for key, value in dict(role_models or {}).items() if str(value).strip()}
        self.verify_tls = bool(verify_tls)
        self.supports_json_schema = bool(supports_json_schema)
        self.enable_thinking = enable_thinking
        if thinking_budget is not None and not 128 <= thinking_budget <= 32768:
            raise ValueError("thinking_budget must be between 128 and 32768")
        self.thinking_budget = thinking_budget
        if not self.api_base or not self.api_key or not self.model:
            raise ModelProviderError("direct API requires api_base, api_key, and model")
        self.chat_completions_url = _chat_completions_url(self.api_base)

    def complete(self, request: ModelRequest) -> ModelResponse:
        selected_model = self.role_models.get(request.role) or self.model
        body: dict[str, Any] = {
            "model": selected_model,
            "messages": [
                {"role": "system", "content": request.system_prompt},
                {"role": "user", "content": request.user_prompt},
            ],
            "stream": False,
            "temperature": request.temperature,
        }
        if request.max_output_tokens:
            body["max_tokens"] = request.max_output_tokens
        if self.enable_thinking is not None:
            body["enable_thinking"] = self.enable_thinking
        if self.thinking_budget is not None:
            body["thinking_budget"] = self.thinking_budget
        # Some OpenAI-compatible services reject response_format entirely.  Only
        # advertise JSON Schema when the operator has explicitly enabled that
        # capability; the local contract validator remains authoritative in both
        # modes.
        if request.response_schema and self.supports_json_schema:
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": f"kp_{request.role}",
                    "strict": True,
                    "schema": dict(request.response_schema),
                },
            }
        payload, response = post_json(
            self.chat_completions_url,
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json", "X-Trace-Id": request.trace_id},
            body=body,
            timeout=request.timeout_seconds,
            verify_tls=self.verify_tls,
        )
        return ModelResponse(
            text=chat_content(payload), provider=self.name,
            model=str(payload.get("model") or selected_model),
            request_id=str(payload.get("id") or response.headers.get("x-request-id") or ""),
            usage=safe_usage(payload), metadata={"trace_id": request.trace_id},
        )
