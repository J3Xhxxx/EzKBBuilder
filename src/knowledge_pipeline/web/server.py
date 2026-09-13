from __future__ import annotations

import json
import mimetypes
import threading
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from knowledge_pipeline.core.contracts import KnowledgeSpec
from knowledge_pipeline.core.profile import ProfileRegistry
from knowledge_pipeline.core.service import PipelineService
from knowledge_pipeline.providers.factory import provider_from_env


MAX_REQUEST_BYTES = 1024 * 1024
STATIC_ROOT = Path(__file__).with_name("static")


class Workbench:
    def __init__(
        self, workspace: Path, provider_name: str, source_root: Path,
        profile_roots: list[Path] | None = None, allowed_hosts: list[str] | None = None,
    ) -> None:
        self.profiles = ProfileRegistry(profile_roots)
        self.service = PipelineService(workspace, provider_from_env(provider_name), self.profiles)
        self.provider_name = provider_name
        self.source_root = source_root.expanduser().resolve()
        self.allowed_hosts = tuple(str(item).lower() for item in (allowed_hosts or []))
        self.executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="knowledge-pipeline")
        self._active: set[str] = set()
        self._guard = threading.Lock()

    def bootstrap(self) -> dict[str, Any]:
        return {
            "workspace": str(self.service.store.root),
            "source_root": str(self.source_root),
            "provider": self.provider_name,
            "allowed_hosts": list(self.allowed_hosts),
            "profiles": [item.to_dict() for item in self.profiles.list()],
            **self.service.status(),
        }

    def start_run(self, value: dict[str, Any]) -> dict[str, Any]:
        spec = KnowledgeSpec.from_dict(dict(value.get("spec") or {}))
        raw_paths = value.get("source_paths") or []
        raw_urls = value.get("source_urls") or []
        if not isinstance(raw_paths, list) or not isinstance(raw_urls, list) or (not raw_paths and not raw_urls):
            raise ValueError("at least one local or web source is required")
        paths = [self._source_path(str(item)) for item in raw_paths]
        with self._guard:
            if spec.document_id in self._active:
                raise ValueError("this document already has a running task")
            task, bundle = self.service.enqueue_inputs(
                spec, paths, [str(item) for item in raw_urls],
                allowed_roots=[self.source_root], allowed_hosts=self.allowed_hosts,
            )
            if task["status"] in {"QUEUED", "RUNNING"}:
                self._active.add(spec.document_id)
                self.executor.submit(self._run, spec, bundle, str(task["task_id"]))
        return {"task_id": task["task_id"], "document_id": spec.document_id, "status": task["status"]}

    def _source_path(self, raw: str) -> Path:
        candidate = Path(raw).expanduser()
        if not candidate.is_absolute():
            candidate = self.source_root / candidate
        candidate = candidate.resolve()
        if candidate != self.source_root and self.source_root not in candidate.parents:
            raise ValueError("source path escaped the configured source root")
        return candidate

    def _run(self, spec: KnowledgeSpec, bundle: Any, task_id: str) -> None:
        try:
            self.service.run(spec, bundle, task_id=task_id)
        except Exception:
            # PipelineService persists the classified error for the UI.
            pass
        finally:
            with self._guard:
                self._active.discard(spec.document_id)

    def document(self, document_id: str) -> dict[str, Any]:
        row = self.service.status(document_id)
        version = row.get("version")
        try:
            row["profile"] = self.profiles.get(row["profile_id"]).to_dict()
            row["profile_available"] = True
        except KeyError:
            # A removed template must not make saved knowledge unreadable.
            row["profile"] = None
            row["profile_available"] = False
        if version:
            markdown_path = (self.service.store.root / version["markdown_path"]).resolve()
            if self.service.store.root == markdown_path or self.service.store.root in markdown_path.parents:
                version["markdown"] = markdown_path.read_text(encoding="utf-8")
            saved_profile = version["document"].get("provenance", {}).get("profile")
            if saved_profile:
                row["profile"] = saved_profile
        return row

    def close(self) -> None:
        self.executor.shutdown(wait=False, cancel_futures=False)


class WorkbenchServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], app: Workbench) -> None:
        self.app = app
        super().__init__(address, Handler)


class Handler(BaseHTTPRequestHandler):
    server: WorkbenchServer

    def log_message(self, format: str, *args: Any) -> None:
        print(f"[knowledge-pipeline] {self.address_string()} {format % args}")

    def _json(self, value: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
        payload = (json.dumps(value, ensure_ascii=False, default=str) + "\n").encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _error(self, exc: Exception, status: HTTPStatus = HTTPStatus.BAD_REQUEST) -> None:
        self._json({"ok": False, "error": str(exc), "error_type": type(exc).__name__}, status)

    def _body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > MAX_REQUEST_BYTES:
            raise ValueError("request body size is invalid")
        raw = self.rfile.read(length)
        value = json.loads(raw.decode("utf-8"))
        if not isinstance(value, dict):
            raise ValueError("request body must be a JSON object")
        return value

    def _check_request(self, *, write: bool = False) -> bool:
        """Keep browser requests on this server's origin, including loopback.

        CLI clients may omit Origin, but browsers cannot use simple form/text
        requests to trigger mutations. Host validation also blocks DNS rebinding.
        This is a browser boundary, not authentication for a public deployment.
        """
        try:
            hosts = self.headers.get_all("Host", [])
            if len(hosts) != 1:
                raise ValueError("exactly one Host header is required")
            host = urlparse("//" + hosts[0])
            bound_host, bound_port = self.server.server_address[:2]
            allowed = {str(bound_host).lower(), self.server.server_name.lower()}
            if bound_host in {"127.0.0.1", "0.0.0.0", "::1", "::"}:
                allowed.update({"localhost", "127.0.0.1", "::1"})
            if (host.hostname not in allowed or (host.port or 80) != bound_port
                    or host.username is not None or host.password is not None
                    or host.path or host.query or host.fragment):
                raise ValueError("Host does not match this workbench")
            origins = self.headers.get_all("Origin", [])
            if len(origins) > 1:
                raise ValueError("multiple Origin headers are not allowed")
            if origins:
                origin = urlparse(origins[0])
                if (origin.scheme != "http" or origin.hostname != host.hostname
                        or (origin.port or 80) != bound_port
                        or origin.username is not None or origin.password is not None
                        or origin.path or origin.query or origin.fragment):
                    raise ValueError("cross-origin requests are not allowed")
            if self.headers.get("Sec-Fetch-Site") == "cross-site":
                raise ValueError("cross-site requests are not allowed")
        except ValueError as exc:
            self.close_connection = True
            self._error(exc, HTTPStatus.FORBIDDEN)
            return False
        if write and self.headers.get_content_type() != "application/json":
            self.close_connection = True
            self._error(ValueError("Content-Type must be application/json"), HTTPStatus.UNSUPPORTED_MEDIA_TYPE)
            return False
        return True

    def do_GET(self) -> None:  # noqa: N802
        if not self._check_request():
            return
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/api/bootstrap":
                self._json(self.server.app.bootstrap())
                return
            if parsed.path == "/api/status":
                self._json(self.server.app.service.status())
                return
            if parsed.path.startswith("/api/documents/"):
                document_id = unquote(parsed.path.removeprefix("/api/documents/"))
                self._json(self.server.app.document(document_id))
                return
            if parsed.path == "/api/download":
                query = parse_qs(parsed.query)
                self._download(str((query.get("document") or [""])[0]), str((query.get("kind") or ["zip"])[0]))
                return
            self._static(parsed.path)
        except KeyError as exc:
            self._error(exc, HTTPStatus.NOT_FOUND)
        except Exception as exc:
            self._error(exc)

    def do_POST(self) -> None:  # noqa: N802
        if not self._check_request(write=True):
            return
        parsed = urlparse(self.path)
        try:
            body = self._body()
            if parsed.path == "/api/run":
                result = self.server.app.start_run(body)
                self._json({"ok": True, **result}, HTTPStatus.ACCEPTED)
            elif parsed.path in {"/api/retrieval/index", "/api/retrieval/search"}:
                from knowledge_pipeline.retrieval import EmbeddingProvider, KnowledgeIndex, answer_question

                service = self.server.app.service
                sandbox = body.get("sandbox") is True
                index = KnowledgeIndex(service.store.root, EmbeddingProvider.from_env(), sandbox=sandbox)
                if parsed.path.endswith("/index"):
                    self._json({"ok": True, **index.rebuild(service.store)})
                else:
                    question = str(body.get("question") or "").strip()
                    hits = index.search(question, top_k=5, store=service.store)
                    result = {"ok": True, "sandbox": sandbox, "hits": hits}
                    if body.get("answer") is True:
                        result["answer"] = answer_question(service.provider, question, hits[:3])
                    self._json(result)
            elif parsed.path == "/api/review":
                result = self.server.app.service.review(
                    str(body.get("document_id") or ""), str(body.get("document_sha256") or ""),
                    str(body.get("reviewer") or ""), str(body.get("decision") or ""), str(body.get("reason") or ""),
                )
                self._json({"ok": True, "review": result.to_dict()})
            elif parsed.path == "/api/export":
                self._json({"ok": True, "export": self.server.app.service.export(str(body.get("document_id") or ""))})
            elif parsed.path == "/api/publish":
                result = self.server.app.service.register_publication(
                    str(body.get("document_id") or ""), operator=str(body.get("operator") or ""),
                    target=str(body.get("target") or "manual"), external_id=str(body.get("external_id") or ""),
                    note=str(body.get("note") or ""), confirmed=body.get("confirmed") is True,
                )
                self._json({"ok": True, "publication": result.to_dict()})
            elif parsed.path == "/api/evaluate":
                result = self.server.app.service.evaluate(
                    str(body.get("document_id") or ""), dict(body.get("checks") or {}),
                    str(body.get("evaluator") or ""), str(body.get("notes") or ""),
                    failure_reasons=dict(body.get("failure_reasons") or {}),
                )
                self._json({"ok": True, "evaluation": result.to_dict()})
            else:
                self._json({"ok": False, "error": "route not found"}, HTTPStatus.NOT_FOUND)
        except KeyError as exc:
            self._error(exc, HTTPStatus.NOT_FOUND)
        except Exception as exc:
            self._error(exc)

    def _static(self, request_path: str) -> None:
        relative = "index.html" if request_path in {"", "/"} else unquote(request_path).lstrip("/")
        target = (STATIC_ROOT / relative).resolve()
        if STATIC_ROOT.resolve() not in target.parents or not target.is_file():
            self._json({"ok": False, "error": "resource not found"}, HTTPStatus.NOT_FOUND)
            return
        payload = target.read_bytes()
        content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type + ("; charset=utf-8" if content_type.startswith("text/") else ""))
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _download(self, document_id: str, kind: str) -> None:
        if kind not in {"zip", "json", "md"}:
            raise ValueError("download kind must be zip, json, or md")
        version = self.server.app.service.store.get_current_version(document_id)
        root = (self.server.app.service.store.exports_dir / document_id / version["content_sha256"][:12]).resolve()
        target = (root / f"{document_id}.{kind}").resolve()
        if root not in target.parents or not target.is_file():
            raise KeyError("export file is unavailable")
        payload = target.read_bytes()
        content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Disposition", f'attachment; filename="{target.name}"')
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def serve(
    *, workspace: Path, provider_name: str = "mock", host: str = "127.0.0.1",
    port: int = 8770, source_root: Path | None = None, open_browser: bool = False,
    profile_roots: list[Path] | None = None,
    allowed_hosts: list[str] | None = None,
) -> None:
    resolved_source_root = (source_root or Path.cwd()).expanduser().resolve()
    if not resolved_source_root.is_dir():
        raise ValueError(f"source root is not a directory: {resolved_source_root}")
    app = Workbench(workspace, provider_name, resolved_source_root, profile_roots, allowed_hosts)
    server = WorkbenchServer((host, port), app)
    url = f"http://{host}:{server.server_port}/"
    print(f"EzKBBuilder workbench: {url}")
    print(f"Workspace: {app.service.store.root}")
    print(f"Source root: {resolved_source_root}")
    if app.allowed_hosts:
        print(f"Allowed web hosts: {', '.join(app.allowed_hosts)}")
    if open_browser:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        app.close()
