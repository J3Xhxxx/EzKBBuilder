"""Configurable, auditable knowledge-document production pipeline."""

from .core.contracts import (
    AuditReport,
    EvaluationResult,
    KnowledgeDocument,
    KnowledgeSpec,
    PipelineState,
    PublicationRecord,
    ReviewDecision,
    SourceBundle,
    SourceRecord,
)
from .core.service import PipelineService

__all__ = [
    "AuditReport",
    "EvaluationResult",
    "KnowledgeDocument",
    "KnowledgeSpec",
    "PipelineService",
    "PipelineState",
    "PublicationRecord",
    "ReviewDecision",
    "SourceBundle",
    "SourceRecord",
]

__version__ = "0.1.0"
