from __future__ import annotations

import json
import random
import time
from typing import Any, Mapping

from knowledge_pipeline.providers.base import ModelProvider, ModelProviderError, ModelRequest, ModelResponse, ModelResponseError


def parse_json_object(text: str, label: str) -> dict[str, Any]:
    value = str(text or "").strip()
    if value.startswith("```"):
        lines = value.splitlines()
        if lines and lines[0].lstrip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        value = "\n".join(lines).strip()
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ModelResponseError(f"{label} is not one JSON object: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ModelResponseError(f"{label} must be a JSON object")
    return parsed


def complete_with_retry(
    provider: ModelProvider,
    request: ModelRequest,
    *,
    transport_retries: int = 2,
    contract_retries: int = 1,
) -> tuple[dict[str, Any], ModelResponse, int]:
    transport_used = 0
    contract_used = 0
    while True:
        try:
            response = provider.complete(request)
        except ModelProviderError as exc:
            if not exc.retryable or transport_used >= transport_retries:
                raise
            delay = min(0.25 * (2**transport_used) + random.random() * 0.05, 2.0)
            transport_used += 1
            time.sleep(delay)
            continue
        try:
            return parse_json_object(response.text, f"{request.role} response"), response, transport_used + contract_used
        except ModelResponseError:
            if contract_used >= contract_retries:
                raise
            contract_used += 1
            # Repeat the unchanged request. The local validator remains the
            # authority and never converts an invalid response into a PASS.


GENERATION_SCHEMA: Mapping[str, Any] = {
    "type": "object",
    "required": ["sections", "used_source_ids"],
    "properties": {
        "sections": {"type": "object", "additionalProperties": {"type": "string"}},
        "used_source_ids": {"type": "array", "items": {"type": "string"}},
    },
    "additionalProperties": False,
}

AUDIT_SCHEMA: Mapping[str, Any] = {
    "type": "object",
    "required": ["passed", "repairable", "findings"],
    "properties": {
        "passed": {"type": "boolean"}, "repairable": {"type": "boolean"},
        "findings": {"type": "array", "items": {"type": "object"}},
    },
    "additionalProperties": True,
}

REPAIR_SCHEMA: Mapping[str, Any] = {
    "type": "object",
    "required": ["repaired_sections", "used_source_ids"],
    "properties": {
        "repaired_sections": {"type": "object", "additionalProperties": {"type": "string"}},
        "used_source_ids": {"type": "array", "items": {"type": "string"}},
    },
    "additionalProperties": False,
}
