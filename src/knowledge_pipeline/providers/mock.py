from __future__ import annotations

import json
import re
from collections import defaultdict, deque
from typing import Callable, Iterable, Mapping

from .base import ModelRequest, ModelResponse


class MockProvider:
    """Deterministic offline provider for demos and contract tests."""

    name = "mock"

    def __init__(self, responses: Mapping[str, str | Iterable[str]] | None = None, handler: Callable[[ModelRequest], str] | None = None) -> None:
        self._responses: dict[str, deque[str]] = defaultdict(deque)
        for role, values in (responses or {}).items():
            if isinstance(values, str):
                self._responses[role].append(values)
            else:
                self._responses[role].extend(str(item) for item in values)
        self.handler = handler
        self.requests: list[ModelRequest] = []

    def complete(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        if self.handler:
            text = self.handler(request)
        elif self._responses[request.role]:
            text = self._responses[request.role].popleft()
        else:
            text = self._default(request)
        return ModelResponse(text=text, provider=self.name, model="deterministic-offline", request_id=f"mock-{len(self.requests):04d}", usage=None)

    @staticmethod
    def _default(request: ModelRequest) -> str:
        metadata = dict(request.metadata)
        if request.role == "generate":
            section_ids = list(metadata.get("required_section_ids", metadata.get("section_ids")) or [])
            source_ids = list(metadata.get("source_ids") or [])
            source = source_ids[0] if source_ids else ""
            citation = f" [{source}]" if source else ""
            # Quote supplied material for the demo. This is not model synthesis
            # or a factual audit, and it never pads absent optional sections.
            try:
                payload = json.loads(request.user_prompt)
                sources = payload.get("source_bundle", {}).get("sources", [])
                content = str(sources[0]["content"]) if sources else ""
            except (ValueError, TypeError, KeyError, IndexError, AttributeError):
                content = ""
            paragraphs = [
                part.strip() for part in re.split(r"\n\s*\n", content)
                if part.strip() and not part.lstrip().startswith("#")
            ]
            sections = {
                section: (
                    "离线来源摘录（未进行模型归纳）：\n\n"
                    + paragraphs[min(index, len(paragraphs) - 1)] + citation
                    if paragraphs else f"离线演示内容：{section}。仅用于验证管线合同。{citation}"
                )
                for index, section in enumerate(section_ids)
            }
            return json.dumps({"sections": sections, "used_source_ids": source_ids[:1]}, ensure_ascii=False)
        if request.role == "audit":
            return json.dumps({"passed": True, "repairable": False, "findings": []}, ensure_ascii=False)
        current = dict(metadata.get("current_sections") or {})
        targets = list(metadata.get("target_sections") or current)
        return json.dumps({"repaired_sections": {key: current.get(key, "") for key in targets}, "used_source_ids": list(metadata.get("source_ids") or [])}, ensure_ascii=False)
