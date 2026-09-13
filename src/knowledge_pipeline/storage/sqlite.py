from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import threading
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Mapping

from knowledge_pipeline.core.contracts import (
    AuditReport,
    EvaluationResult,
    KnowledgeDocument,
    KnowledgeSpec,
    PipelineState,
    PublicationRecord,
    ReviewDecision,
    SourceBundle,
    canonical_json,
    now_iso,
    sha256_text,
)


_SCHEMA_LOCK = threading.Lock()


TRANSITIONS: dict[PipelineState, set[PipelineState]] = {
    PipelineState.READY: {PipelineState.GENERATING, PipelineState.STOPPED},
    PipelineState.GENERATING: {PipelineState.GENERATED, PipelineState.STOPPED, PipelineState.INTERRUPTED},
    PipelineState.GENERATED: {PipelineState.AUDITING, PipelineState.STOPPED},
    PipelineState.AUDITING: {PipelineState.PENDING_REVIEW, PipelineState.REPAIRING, PipelineState.STOPPED, PipelineState.INTERRUPTED},
    PipelineState.REPAIRING: {PipelineState.GENERATED, PipelineState.STOPPED, PipelineState.INTERRUPTED},
    PipelineState.PENDING_REVIEW: {PipelineState.APPROVED, PipelineState.RETURNED, PipelineState.STOPPED},
    PipelineState.RETURNED: {PipelineState.GENERATING, PipelineState.STOPPED},
    PipelineState.APPROVED: {PipelineState.EXPORTED, PipelineState.RETURNED},
    PipelineState.EXPORTED: {PipelineState.PENDING_PUBLICATION, PipelineState.PUBLISHED, PipelineState.RETURNED},
    PipelineState.PENDING_PUBLICATION: {PipelineState.PUBLISHED, PipelineState.RETURNED},
    PipelineState.PUBLISHED: {PipelineState.EVALUATION_PASSED, PipelineState.EVALUATION_FAILED, PipelineState.RETURNED},
    PipelineState.EVALUATION_FAILED: {PipelineState.EVALUATION_PASSED, PipelineState.RETURNED},
    PipelineState.EVALUATION_PASSED: {PipelineState.EVALUATION_FAILED, PipelineState.RETURNED},
    PipelineState.STOPPED: {PipelineState.GENERATING, PipelineState.AUDITING},
    PipelineState.INTERRUPTED: {PipelineState.GENERATING, PipelineState.AUDITING, PipelineState.REPAIRING, PipelineState.STOPPED},
}


class WorkspaceStore:
    """SQLite state plus immutable document files in a caller-selected workspace."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.documents_dir = self.root / "documents"
        self.bundles_dir = self.root / "bundles"
        self.exports_dir = self.root / "exports"
        for directory in (self.documents_dir, self.bundles_dir, self.exports_dir):
            directory.mkdir(parents=True, exist_ok=True)
        self.database = self.root / "pipeline.sqlite3"
        self._local = threading.local()
        # Concurrent first-open attempts can race while enabling WAL, before
        # ordinary SQLite busy-timeout handling can serialize transactions.
        with _SCHEMA_LOCK:
            self._init_schema()
        self.recover_interrupted_tasks()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _init_schema(self) -> None:
        with self.connect() as db:
            db.executescript(
                """
                PRAGMA journal_mode = WAL;
                CREATE TABLE IF NOT EXISTS documents (
                    document_id TEXT PRIMARY KEY,
                    profile_id TEXT NOT NULL,
                    spec_json TEXT NOT NULL,
                    state TEXT NOT NULL,
                    current_version_id TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS bundles (
                    bundle_sha256 TEXT PRIMARY KEY,
                    document_id TEXT NOT NULL REFERENCES documents(document_id),
                    bundle_json TEXT NOT NULL,
                    path TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS versions (
                    version_id TEXT PRIMARY KEY,
                    document_id TEXT NOT NULL REFERENCES documents(document_id),
                    generation INTEGER NOT NULL,
                    content_sha256 TEXT NOT NULL UNIQUE,
                    source_bundle_sha256 TEXT NOT NULL,
                    document_json TEXT NOT NULL,
                    markdown_path TEXT NOT NULL,
                    audit_json TEXT,
                    created_at TEXT NOT NULL,
                    UNIQUE(document_id, generation)
                );
                CREATE TABLE IF NOT EXISTS events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    document_id TEXT NOT NULL REFERENCES documents(document_id),
                    from_state TEXT,
                    to_state TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS tasks (
                    task_id TEXT PRIMARY KEY,
                    document_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    status TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    result_json TEXT,
                    error TEXT,
                    uncertain INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT
                );
                CREATE TABLE IF NOT EXISTS reviews (
                    review_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    document_id TEXT NOT NULL REFERENCES documents(document_id),
                    document_sha256 TEXT NOT NULL,
                    review_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS publications (
                    publication_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    document_id TEXT NOT NULL REFERENCES documents(document_id),
                    document_sha256 TEXT NOT NULL,
                    publication_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS evaluations (
                    evaluation_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    document_id TEXT NOT NULL REFERENCES documents(document_id),
                    document_sha256 TEXT NOT NULL,
                    evaluation_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS repair_budget (
                    document_id TEXT NOT NULL,
                    cycle_key TEXT NOT NULL,
                    attempts INTEGER NOT NULL,
                    PRIMARY KEY(document_id, cycle_key)
                );
                CREATE TABLE IF NOT EXISTS document_locks (
                    document_id TEXT PRIMARY KEY,
                    owner TEXT NOT NULL,
                    acquired_at TEXT NOT NULL
                );
                """
            )

    def _atomic_write(self, path: Path, payload: bytes) -> None:
        target = path.resolve()
        if self.root != target and self.root not in target.parents:
            raise ValueError("workspace write escaped its root")
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix=target.name + ".", suffix=".tmp", dir=target.parent)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, target)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)

    def create_document(self, spec: KnowledgeSpec) -> dict[str, Any]:
        timestamp = now_iso()
        with self.connect() as db:
            current = db.execute("SELECT * FROM documents WHERE document_id = ?", (spec.document_id,)).fetchone()
            if current:
                saved = json.loads(current["spec_json"])
                if saved != spec.to_dict():
                    state = PipelineState(current["state"])
                    if state not in {PipelineState.READY, PipelineState.RETURNED, PipelineState.STOPPED, PipelineState.INTERRUPTED}:
                        raise ValueError(f"document ID already exists with a different active spec: {spec.document_id}")
                    if current["profile_id"] != spec.profile_id:
                        raise ValueError("an existing document cannot switch profiles; use a new document ID")
                    timestamp = now_iso()
                    db.execute(
                        "UPDATE documents SET spec_json=?,updated_at=? WHERE document_id=?",
                        (canonical_json(spec.to_dict()), timestamp, spec.document_id),
                    )
                    db.execute(
                        "INSERT INTO events(document_id,from_state,to_state,reason,metadata_json,created_at) VALUES(?,?,?,?,?,?)",
                        (
                            spec.document_id, state.value, state.value, "spec_updated",
                            canonical_json({"previous_spec_sha256": sha256_text(canonical_json(saved)), "spec_sha256": sha256_text(canonical_json(spec.to_dict()))}),
                            timestamp,
                        ),
                    )
                    updated = dict(current)
                    updated["spec_json"] = canonical_json(spec.to_dict())
                    updated["updated_at"] = timestamp
                    return updated
                return dict(current)
            db.execute(
                "INSERT INTO documents VALUES (?, ?, ?, ?, NULL, ?, ?)",
                (spec.document_id, spec.profile_id, canonical_json(spec.to_dict()), PipelineState.READY.value, timestamp, timestamp),
            )
            db.execute(
                "INSERT INTO events(document_id,from_state,to_state,reason,metadata_json,created_at) VALUES(?,NULL,?,?,?,?)",
                (spec.document_id, PipelineState.READY.value, "document_created", "{}", timestamp),
            )
        return self.get_document(spec.document_id)

    def get_document(self, document_id: str) -> dict[str, Any]:
        with self.connect() as db:
            row = db.execute("SELECT * FROM documents WHERE document_id = ?", (document_id,)).fetchone()
            if not row:
                raise KeyError(f"unknown document: {document_id}")
            value = dict(row)
            value["spec"] = json.loads(value.pop("spec_json"))
            if value.get("current_version_id"):
                version = db.execute("SELECT * FROM versions WHERE version_id = ?", (value["current_version_id"],)).fetchone()
                value["version"] = self._version_row(version) if version else None
            else:
                value["version"] = None
            value["events"] = [dict(item) for item in db.execute("SELECT * FROM events WHERE document_id = ? ORDER BY event_id", (document_id,))]
            for event in value["events"]:
                event["metadata"] = json.loads(event.pop("metadata_json"))
            return value

    def list_documents(self) -> list[dict[str, Any]]:
        with self.connect() as db:
            return [
                {**dict(row), "spec": json.loads(row["spec_json"])}
                for row in db.execute("SELECT * FROM documents ORDER BY updated_at DESC")
            ]

    def transition(self, document_id: str, target: PipelineState, reason: str, metadata: Mapping[str, Any] | None = None, *, force: bool = False) -> None:
        timestamp = now_iso()
        with self.connect() as db:
            row = db.execute("SELECT state FROM documents WHERE document_id = ?", (document_id,)).fetchone()
            if not row:
                raise KeyError(f"unknown document: {document_id}")
            current = PipelineState(row["state"])
            if current == target:
                return
            if not force and target not in TRANSITIONS.get(current, set()):
                raise ValueError(f"invalid pipeline transition: {current.value} -> {target.value}")
            db.execute("UPDATE documents SET state=?, updated_at=? WHERE document_id=?", (target.value, timestamp, document_id))
            db.execute(
                "INSERT INTO events(document_id,from_state,to_state,reason,metadata_json,created_at) VALUES(?,?,?,?,?,?)",
                (document_id, current.value, target.value, reason, canonical_json(dict(metadata or {})), timestamp),
            )

    def save_bundle(self, bundle: SourceBundle) -> Path:
        path = self.bundles_dir / bundle.document_id / f"{bundle.bundle_sha256}.json"
        payload = (json.dumps(bundle.to_dict(), ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        if path.exists() and path.read_bytes() != payload:
            raise RuntimeError("immutable bundle path already contains different bytes")
        if not path.exists():
            self._atomic_write(path, payload)
        with self.connect() as db:
            db.execute(
                "INSERT OR IGNORE INTO bundles VALUES(?,?,?,?,?)",
                (bundle.bundle_sha256, bundle.document_id, canonical_json(bundle.to_dict()), str(path.relative_to(self.root)), bundle.built_at),
            )
        return path

    def load_bundle(self, bundle_sha256: str) -> SourceBundle:
        with self.connect() as db:
            row = db.execute("SELECT bundle_json FROM bundles WHERE bundle_sha256 = ?", (bundle_sha256,)).fetchone()
            if not row:
                raise KeyError(f"unknown source bundle: {bundle_sha256}")
            return SourceBundle.from_dict(json.loads(row["bundle_json"]))

    def next_generation(self, document_id: str) -> int:
        with self.connect() as db:
            row = db.execute("SELECT COALESCE(MAX(generation),0)+1 AS n FROM versions WHERE document_id=?", (document_id,)).fetchone()
            return int(row["n"])

    def save_version(self, document: KnowledgeDocument, markdown: str) -> dict[str, Any]:
        version_id = f"{document.document_id}-v{document.generation:04d}"
        path = self.documents_dir / document.document_id / f"v{document.generation:04d}-{document.content_sha256[:12]}.md"
        payload = markdown.encode("utf-8")
        if path.exists() and path.read_bytes() != payload:
            raise RuntimeError("immutable document path already contains different bytes")
        if not path.exists():
            self._atomic_write(path, payload)
        with self.connect() as db:
            db.execute(
                "INSERT INTO versions VALUES(?,?,?,?,?,?,?,?,?)",
                (version_id, document.document_id, document.generation, document.content_sha256,
                 document.source_bundle_sha256, canonical_json(document.to_dict()), str(path.relative_to(self.root)), None, document.created_at),
            )
            db.execute("UPDATE documents SET current_version_id=?, updated_at=? WHERE document_id=?", (version_id, now_iso(), document.document_id))
        return self.get_version_by_id(version_id)

    @staticmethod
    def _version_row(row: sqlite3.Row) -> dict[str, Any]:
        value = dict(row)
        value["document"] = json.loads(value.pop("document_json"))
        value["audit"] = json.loads(value.pop("audit_json")) if value.get("audit_json") else None
        return value

    def get_version_by_id(self, version_id: str) -> dict[str, Any]:
        with self.connect() as db:
            row = db.execute("SELECT * FROM versions WHERE version_id=?", (version_id,)).fetchone()
            if not row:
                raise KeyError(f"unknown version: {version_id}")
            return self._version_row(row)

    def get_current_version(self, document_id: str) -> dict[str, Any]:
        document = self.get_document(document_id)
        if not document.get("version"):
            raise RuntimeError(f"document has no generated version: {document_id}")
        return document["version"]

    def list_versions(self, document_id: str) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT * FROM versions WHERE document_id=? ORDER BY generation",
                (document_id,),
            ).fetchall()
            return [self._version_row(row) for row in rows]

    def save_audit(self, report: AuditReport) -> None:
        with self.connect() as db:
            row = db.execute("SELECT version_id FROM versions WHERE content_sha256=?", (report.document_sha256,)).fetchone()
            if not row:
                raise ValueError("audit references an unknown document version")
            db.execute("UPDATE versions SET audit_json=? WHERE version_id=?", (canonical_json(report.to_dict()), row["version_id"]))

    def save_review(self, review: ReviewDecision) -> None:
        with self.connect() as db:
            current = db.execute(
                "SELECT v.content_sha256,v.source_bundle_sha256 FROM documents d JOIN versions v ON v.version_id=d.current_version_id WHERE d.document_id=?",
                (review.document_id,),
            ).fetchone()
            if not current or current["content_sha256"] != review.document_sha256 or current["source_bundle_sha256"] != review.source_bundle_sha256:
                raise ValueError("reviewed document or source bundle is no longer current")
            db.execute(
                "INSERT INTO reviews(document_id,document_sha256,review_json,created_at) VALUES(?,?,?,?)",
                (review.document_id, review.document_sha256, canonical_json(review.to_dict()), review.reviewed_at),
            )

    def latest_review(self, document_id: str) -> ReviewDecision | None:
        with self.connect() as db:
            row = db.execute("SELECT review_json FROM reviews WHERE document_id=? ORDER BY review_id DESC LIMIT 1", (document_id,)).fetchone()
            return ReviewDecision(**json.loads(row["review_json"])) if row else None

    def save_publication(self, publication: PublicationRecord) -> None:
        with self.connect() as db:
            db.execute(
                "INSERT INTO publications(document_id,document_sha256,publication_json,created_at) VALUES(?,?,?,?)",
                (publication.document_id, publication.document_sha256, canonical_json(publication.to_dict()), publication.recorded_at),
            )

    def latest_publication(self, document_id: str) -> PublicationRecord | None:
        with self.connect() as db:
            row = db.execute("SELECT publication_json FROM publications WHERE document_id=? ORDER BY publication_id DESC LIMIT 1", (document_id,)).fetchone()
            return PublicationRecord(**json.loads(row["publication_json"])) if row else None

    def save_evaluation(self, evaluation: EvaluationResult) -> None:
        with self.connect() as db:
            db.execute(
                "INSERT INTO evaluations(document_id,document_sha256,evaluation_json,created_at) VALUES(?,?,?,?)",
                (evaluation.document_id, evaluation.document_sha256, canonical_json(evaluation.to_dict()), evaluation.evaluated_at),
            )

    def latest_evaluation(self, document_id: str) -> EvaluationResult | None:
        with self.connect() as db:
            row = db.execute("SELECT evaluation_json FROM evaluations WHERE document_id=? ORDER BY evaluation_id DESC LIMIT 1", (document_id,)).fetchone()
            return EvaluationResult(**json.loads(row["evaluation_json"])) if row else None

    def repair_attempts(self, document_id: str, cycle_key: str) -> int:
        with self.connect() as db:
            row = db.execute("SELECT attempts FROM repair_budget WHERE document_id=? AND cycle_key=?", (document_id, cycle_key)).fetchone()
            return int(row["attempts"]) if row else 0

    def consume_repair(self, document_id: str, cycle_key: str, maximum: int) -> int:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT attempts FROM repair_budget WHERE document_id=? AND cycle_key=?", (document_id, cycle_key)).fetchone()
            current = int(row["attempts"]) if row else 0
            if current >= maximum:
                raise RuntimeError("repair budget exhausted")
            updated = current + 1
            db.execute(
                "INSERT INTO repair_budget VALUES(?,?,?) ON CONFLICT(document_id,cycle_key) DO UPDATE SET attempts=excluded.attempts",
                (document_id, cycle_key, updated),
            )
            return updated

    def acquire_document_lock(self, document_id: str, owner: str) -> None:
        with self.connect() as db:
            try:
                db.execute(
                    "INSERT INTO document_locks(document_id,owner,acquired_at) VALUES(?,?,?)",
                    (document_id, owner, now_iso()),
                )
            except sqlite3.IntegrityError as exc:
                row = db.execute("SELECT owner FROM document_locks WHERE document_id=?", (document_id,)).fetchone()
                detail = row["owner"] if row else "another task"
                raise RuntimeError(f"document is locked by {detail}") from exc

    def release_document_lock(self, document_id: str, owner: str) -> None:
        with self.connect() as db:
            db.execute("DELETE FROM document_locks WHERE document_id=? AND owner=?", (document_id, owner))

    def create_task(self, document_id: str, action: str, idempotency_key: str) -> tuple[dict[str, Any], bool]:
        with self.connect() as db:
            existing = db.execute("SELECT * FROM tasks WHERE idempotency_key=?", (idempotency_key,)).fetchone()
            if existing:
                return dict(existing), True
            task_id = uuid.uuid4().hex
            timestamp = now_iso()
            db.execute(
                "INSERT INTO tasks(task_id,document_id,action,status,idempotency_key,created_at) VALUES(?,?,?,?,?,?)",
                (task_id, document_id, action, "QUEUED", idempotency_key, timestamp),
            )
            row = db.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone()
            return dict(row), False

    def update_task(self, task_id: str, status: str, *, result: Mapping[str, Any] | None = None, error: str = "", uncertain: bool = False) -> None:
        if status not in {"QUEUED", "RUNNING", "SUCCEEDED", "FAILED", "INTERRUPTED", "UNCERTAIN"}:
            raise ValueError(f"invalid task status: {status}")
        changes = ["status=?", "uncertain=?"]
        values: list[Any] = [status, int(uncertain)]
        if status == "RUNNING":
            changes.append("started_at=?")
            values.append(now_iso())
        if status in {"SUCCEEDED", "FAILED", "INTERRUPTED", "UNCERTAIN"}:
            changes.append("finished_at=?")
            values.append(now_iso())
        if result is not None:
            changes.append("result_json=?")
            values.append(canonical_json(dict(result)))
        if error:
            changes.append("error=?")
            values.append(error[:8000])
        values.append(task_id)
        with self.connect() as db:
            db.execute(f"UPDATE tasks SET {', '.join(changes)} WHERE task_id=?", values)

    def list_tasks(self, limit: int = 100) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = [dict(row) for row in db.execute("SELECT * FROM tasks ORDER BY created_at DESC LIMIT ?", (limit,))]
            for row in rows:
                row["result"] = json.loads(row.pop("result_json")) if row.get("result_json") else None
                row["uncertain"] = bool(row["uncertain"])
            return rows

    def recover_interrupted_tasks(self) -> int:
        with self.connect() as db:
            rows = db.execute("SELECT task_id,document_id FROM tasks WHERE status IN ('QUEUED','RUNNING')").fetchall()
            locks = {
                row["document_id"]: row["owner"]
                for row in db.execute("SELECT document_id,owner FROM document_locks")
            }
            timestamp = now_iso()
            recovered = 0
            for row in rows:
                owner = locks.get(row["document_id"], "")
                owner_pid, separator, owner_task = owner.partition(":")
                if separator and owner_task == row["task_id"] and self._pid_alive(owner_pid):
                    continue
                db.execute(
                    "UPDATE tasks SET status='INTERRUPTED',error='process stopped before task completion',finished_at=? WHERE task_id=?",
                    (timestamp, row["task_id"]),
                )
                recovered += 1
                document = db.execute("SELECT state FROM documents WHERE document_id=?", (row["document_id"],)).fetchone()
                if document and document["state"] in {PipelineState.GENERATING.value, PipelineState.AUDITING.value, PipelineState.REPAIRING.value}:
                    db.execute("UPDATE documents SET state=?,updated_at=? WHERE document_id=?", (PipelineState.INTERRUPTED.value, timestamp, row["document_id"]))
            for document_id, owner in locks.items():
                owner_pid = owner.partition(":")[0]
                if not self._pid_alive(owner_pid):
                    db.execute("DELETE FROM document_locks WHERE document_id=? AND owner=?", (document_id, owner))
            return recovered

    @staticmethod
    def _pid_alive(raw_pid: str) -> bool:
        try:
            pid = int(raw_pid)
        except (TypeError, ValueError):
            return False
        if pid <= 0 or pid > 0xFFFFFFFF:
            return False
        if pid == os.getpid():
            return True
        if os.name == "nt":
            # os.kill(pid, 0) calls TerminateProcess on Windows; it is not a
            # read-only existence probe. A zero-time wait never signals the
            # process and also distinguishes an exited process from a live one.
            import ctypes
            from ctypes import wintypes

            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            kernel.OpenProcess.restype = wintypes.HANDLE
            kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
            kernel.WaitForSingleObject.restype = wintypes.DWORD
            kernel.CloseHandle.argtypes = [wintypes.HANDLE]
            kernel.CloseHandle.restype = wintypes.BOOL
            handle = kernel.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE
            if not handle:
                # ERROR_INVALID_PARAMETER means no such PID. Access denied or
                # another uncertain result must not reclaim an active task.
                return ctypes.get_last_error() != 87
            try:
                return kernel.WaitForSingleObject(handle, 0) != 0  # WAIT_OBJECT_0
            finally:
                kernel.CloseHandle(handle)
        try:
            os.kill(pid, 0)
        except PermissionError:
            return True
        except OSError:
            return False
        return True
