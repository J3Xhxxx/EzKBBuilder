from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol, runtime_checkable


class ModelProviderError(RuntimeError):
    """Base error for provider calls; never represents a semantic PASS."""

    kind = "provider_error"
    retryable = False


class ModelAuthenticationError(ModelProviderError):
    kind = "authentication"


class ModelRateLimitError(ModelProviderError):
    kind = "rate_limit"
    retryable = True


class ModelTimeoutError(ModelProviderError):
    kind = "timeout"
    retryable = True


class ModelTransportError(ModelProviderError):
    kind = "transport"
    retryable = True


class ModelResponseError(ModelProviderError):
    kind = "response_contract"


@dataclass(frozen=True)
class ModelRequest:
    role: str
    system_prompt: str
    user_prompt: str
    trace_id: str
    response_schema: Mapping[str, Any] | None = None
    timeout_seconds: int = 180
    max_output_tokens: int | None = None
    temperature: float = 0.0
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.role not in {"generate", "audit", "repair", "answer"}:
            raise ValueError(f"unsupported model role: {self.role}")
        if not self.user_prompt.strip() or not self.trace_id.strip():
            raise ValueError("model request requires user_prompt and trace_id")
        if self.timeout_seconds <= 0:
            raise ValueError("model timeout must be positive")


@dataclass(frozen=True)
class ModelResponse:
    text: str
    provider: str
    model: str = ""
    request_id: str = ""
    usage: Mapping[str, int] | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.text.strip():
            raise ModelResponseError("model response is empty")


@runtime_checkable
class ModelProvider(Protocol):
    name: str

    def complete(self, request: ModelRequest) -> ModelResponse:
        ...
