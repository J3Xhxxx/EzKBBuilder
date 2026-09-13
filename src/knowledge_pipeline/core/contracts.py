from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping


IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
SOURCE_ID_RE = re.compile(r"^SRC\d{3,6}$")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_text(value: str) -> str:
    return sha256_bytes(value.encode("utf-8"))


def _identifier(value: str, label: str) -> str:
    normalized = str(value or "").strip()
    if not IDENTIFIER_RE.fullmatch(normalized):
        raise ValueError(f"{label} must match {IDENTIFIER_RE.pattern}")
    return normalized


def _nonempty(value: str, label: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{label} is required")
    return normalized


class PipelineState(str, Enum):
    READY = "READY"
    GENERATING = "GENERATING"
    GENERATED = "GENERATED"
    AUDITING = "AUDITING"
    REPAIRING = "REPAIRING"
    PENDING_REVIEW = "PENDING_REVIEW"
    APPROVED = "APPROVED"
    RETURNED = "RETURNED"
    EXPORTED = "EXPORTED"
    PENDING_PUBLICATION = "PENDING_PUBLICATION"
    PUBLISHED = "PUBLISHED"
    EVALUATION_PASSED = "EVALUATION_PASSED"
    EVALUATION_FAILED = "EVALUATION_FAILED"
    STOPPED = "STOPPED"
    INTERRUPTED = "INTERRUPTED"


@dataclass(frozen=True)
class KnowledgeSpec:
    document_id: str
    title: str
    profile_id: str = "knowledge_card"
    audience: str = ""
    objective: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "document_id", _identifier(self.document_id, "document_id"))
        object.__setattr__(self, "profile_id", _identifier(self.profile_id, "profile_id"))
        object.__setattr__(self, "title", _nonempty(self.title, "title"))
        object.__setattr__(self, "metadata", dict(self.metadata or {}))

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "KnowledgeSpec":
        return cls(
            document_id=str(value.get("document_id") or value.get("id") or ""),
            title=str(value.get("title") or ""),
            profile_id=str(value.get("profile_id") or value.get("profile") or "knowledge_card"),
            audience=str(value.get("audience") or ""),
            objective=str(value.get("objective") or ""),
            metadata=dict(value.get("metadata") or {}),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SourceRecord:
    source_id: str
    title: str
    kind: str
    content: str
    locator: str
    raw_sha256: str
    normalized_sha256: str
    collected_at: str
    truncated: bool = False
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not SOURCE_ID_RE.fullmatch(str(self.source_id or "")):
            raise ValueError(f"invalid source_id: {self.source_id}")
        _nonempty(self.content, "source content")
        object.__setattr__(self, "metadata", dict(self.metadata or {}))

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "SourceRecord":
        return cls(**{key: value[key] for key in cls.__dataclass_fields__ if key in value})

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SourceBundle:
    bundle_id: str
    document_id: str
    profile_id: str
    profile_version: str
    sources: tuple[SourceRecord, ...]
    built_at: str
    bundle_sha256: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "bundle_id", _identifier(self.bundle_id, "bundle_id"))
        object.__setattr__(self, "document_id", _identifier(self.document_id, "document_id"))
        if not self.sources:
            raise ValueError("source bundle requires at least one source")
        ids = [item.source_id for item in self.sources]
        if len(ids) != len(set(ids)):
            raise ValueError("source bundle contains duplicate source IDs")
        expected = sha256_text(canonical_json(self.payload_for_hash()))
        if self.bundle_sha256 and self.bundle_sha256 != expected:
            raise ValueError("source bundle hash mismatch")
        object.__setattr__(self, "bundle_sha256", expected)

    def payload_for_hash(self) -> dict[str, Any]:
        return {
            "bundle_id": self.bundle_id,
            "document_id": self.document_id,
            "profile_id": self.profile_id,
            "profile_version": self.profile_version,
            "sources": [item.to_dict() for item in self.sources],
            "built_at": self.built_at,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "SourceBundle":
        return cls(
            bundle_id=str(value["bundle_id"]),
            document_id=str(value["document_id"]),
            profile_id=str(value["profile_id"]),
            profile_version=str(value["profile_version"]),
            sources=tuple(SourceRecord.from_dict(item) for item in value.get("sources", [])),
            built_at=str(value["built_at"]),
            bundle_sha256=str(value.get("bundle_sha256") or ""),
        )

    def to_dict(self) -> dict[str, Any]:
        return {**self.payload_for_hash(), "bundle_sha256": self.bundle_sha256}


@dataclass(frozen=True)
class KnowledgeDocument:
    document_id: str
    title: str
    profile_id: str
    profile_version: str
    schema_version: str
    sections: Mapping[str, str]
    used_source_ids: tuple[str, ...]
    source_bundle_sha256: str
    created_at: str
    generation: int = 1
    provenance: Mapping[str, Any] = field(default_factory=dict)
    content_sha256: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "document_id", _identifier(self.document_id, "document_id"))
        normalized = {str(key): str(value).strip() for key, value in dict(self.sections).items()}
        if not normalized or any(not body for body in normalized.values()):
            raise ValueError("document sections must be non-empty")
        object.__setattr__(self, "sections", normalized)
        object.__setattr__(self, "used_source_ids", tuple(dict.fromkeys(str(item).upper() for item in self.used_source_ids)))
        object.__setattr__(self, "provenance", dict(self.provenance or {}))
        expected = sha256_text(canonical_json(self.payload_for_hash()))
        if self.content_sha256 and self.content_sha256 != expected:
            raise ValueError("knowledge document hash mismatch")
        object.__setattr__(self, "content_sha256", expected)

    def payload_for_hash(self) -> dict[str, Any]:
        return {
            "document_id": self.document_id,
            "title": self.title,
            "profile_id": self.profile_id,
            "profile_version": self.profile_version,
            "schema_version": self.schema_version,
            "sections": dict(self.sections),
            "used_source_ids": list(self.used_source_ids),
            "source_bundle_sha256": self.source_bundle_sha256,
            "created_at": self.created_at,
            "generation": self.generation,
            "provenance": dict(self.provenance),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "KnowledgeDocument":
        return cls(
            document_id=str(value["document_id"]), title=str(value["title"]),
            profile_id=str(value["profile_id"]), profile_version=str(value["profile_version"]),
            schema_version=str(value["schema_version"]), sections=dict(value["sections"]),
            used_source_ids=tuple(value.get("used_source_ids", [])),
            source_bundle_sha256=str(value["source_bundle_sha256"]),
            created_at=str(value["created_at"]), generation=int(value.get("generation", 1)),
            provenance=dict(value.get("provenance") or {}),
            content_sha256=str(value.get("content_sha256") or ""),
        )

    def to_dict(self) -> dict[str, Any]:
        return {**self.payload_for_hash(), "content_sha256": self.content_sha256}


@dataclass(frozen=True)
class AuditFinding:
    code: str
    severity: str
    section: str
    message: str
    source_refs: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "AuditFinding":
        return cls(
            code=str(value.get("code") or "UNSPECIFIED"),
            severity=str(value.get("severity") or "ERROR").upper(),
            section=str(value.get("section") or ""),
            message=str(value.get("message") or value.get("detail") or ""),
            source_refs=tuple(str(item) for item in value.get("source_refs", [])),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class AuditReport:
    document_id: str
    document_sha256: str
    source_bundle_sha256: str
    profile_id: str
    profile_version: str
    passed: bool
    repairable: bool
    findings: tuple[AuditFinding, ...]
    audited_at: str
    provider: str
    model: str = ""
    request_id: str = ""

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "AuditReport":
        return cls(
            document_id=str(value["document_id"]), document_sha256=str(value["document_sha256"]),
            source_bundle_sha256=str(value["source_bundle_sha256"]), profile_id=str(value["profile_id"]),
            profile_version=str(value["profile_version"]), passed=bool(value.get("passed")),
            repairable=bool(value.get("repairable")),
            findings=tuple(AuditFinding.from_dict(item) for item in value.get("findings", [])),
            audited_at=str(value["audited_at"]), provider=str(value.get("provider") or "unknown"),
            model=str(value.get("model") or ""), request_id=str(value.get("request_id") or ""),
        )

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["findings"] = [item.to_dict() for item in self.findings]
        return value


@dataclass(frozen=True)
class ReviewDecision:
    document_id: str
    document_sha256: str
    source_bundle_sha256: str
    reviewer: str
    decision: str
    reason: str
    reviewed_at: str

    def __post_init__(self) -> None:
        if self.decision not in {"approve", "return"}:
            raise ValueError("review decision must be approve or return")
        _nonempty(self.reviewer, "reviewer")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PublicationRecord:
    document_id: str
    document_sha256: str
    export_sha256: str
    target: str
    mode: str
    status: str
    operator: str
    recorded_at: str
    external_id: str = ""
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class EvaluationResult:
    document_id: str
    document_sha256: str
    publication_sha256: str
    suite_id: str
    checks: Mapping[str, bool]
    passed: bool
    evaluator: str
    notes: str
    evaluated_at: str
    failure_reasons: Mapping[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
