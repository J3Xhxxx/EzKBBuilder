from __future__ import annotations

import os

from .base import ModelProvider
from .fastgpt import FastGPTProvider
from .mock import MockProvider
from .openai_compatible import OpenAICompatibleProvider


def _bool_env(name: str, default: bool = True) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def provider_from_env(name: str | None = None) -> ModelProvider:
    selected = str(name or os.getenv("KP_PROVIDER", "mock")).strip().lower()
    if selected == "mock":
        return MockProvider()
    if selected in {"openai", "openai-compatible", "direct"}:
        return OpenAICompatibleProvider(
            api_base=os.getenv("KP_API_BASE", ""), api_key=os.getenv("KP_API_KEY", ""),
            model=os.getenv("KP_MODEL", ""), verify_tls=_bool_env("KP_VERIFY_TLS", True),
            role_models={
                "generate": os.getenv("KP_GENERATE_MODEL", ""),
                "audit": os.getenv("KP_AUDIT_MODEL", ""),
                "repair": os.getenv("KP_REPAIR_MODEL", ""),
            },
            supports_json_schema=_bool_env("KP_SUPPORTS_JSON_SCHEMA", False),
            enable_thinking=_bool_env("KP_ENABLE_THINKING") if os.getenv("KP_ENABLE_THINKING", "").strip() else None,
            thinking_budget=int(os.environ["KP_THINKING_BUDGET"]) if os.getenv("KP_THINKING_BUDGET", "").strip() else None,
        )
    if selected == "fastgpt":
        default_key = os.getenv("KP_FASTGPT_API_KEY", "")
        return FastGPTProvider(
            api_base=os.getenv("KP_FASTGPT_BASE_URL", ""),
            api_keys={
                "default": default_key,
                "generate": os.getenv("KP_FASTGPT_GENERATE_API_KEY", default_key),
                "audit": os.getenv("KP_FASTGPT_AUDIT_API_KEY", default_key),
                "repair": os.getenv("KP_FASTGPT_REPAIR_API_KEY", default_key),
            },
            app_ids={
                "generate": os.getenv("KP_FASTGPT_GENERATE_APP_ID", ""),
                "audit": os.getenv("KP_FASTGPT_AUDIT_APP_ID", ""),
                "repair": os.getenv("KP_FASTGPT_REPAIR_APP_ID", ""),
            }, verify_tls=_bool_env("KP_VERIFY_TLS", True),
        )
    raise ValueError(f"unknown model provider: {selected}")
