from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

from knowledge_pipeline.core.contracts import KnowledgeSpec
from knowledge_pipeline.core.profile import ProfileRegistry
from knowledge_pipeline.core.service import PipelineService
from knowledge_pipeline.providers.factory import provider_from_env


def _value(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    value = json.loads(text) if path.suffix.lower() == ".json" else yaml.safe_load(text)
    if not isinstance(value, dict):
        raise ValueError(f"file root must be an object: {path}")
    return value


def _json(value: Any) -> None:
    if hasattr(value, "to_dict"):
        value = value.to_dict()
    print(json.dumps(value, ensure_ascii=False, indent=2, default=str))


def _checks(values: list[str]) -> dict[str, bool]:
    result: dict[str, bool] = {}
    for item in values:
        key, separator, raw = item.partition("=")
        if not separator or raw.lower() not in {"true", "false"}:
            raise ValueError("checks must use id=true or id=false")
        result[key.strip()] = raw.lower() == "true"
    return result


def _key_values(values: list[str], label: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for item in values:
        key, separator, raw = item.partition("=")
        if not separator or not key.strip() or not raw.strip():
            raise ValueError(f"{label} must use id=text")
        result[key.strip()] = raw.strip()
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="knowledge-pipeline", description="Configurable, auditable knowledge-document pipeline")
    parser.add_argument("--workspace", type=Path, default=Path(os.getenv("KP_WORKSPACE", ".knowledge-workspace")))
    parser.add_argument("--provider", choices=["mock", "openai-compatible", "fastgpt"], default=os.getenv("KP_PROVIDER", "mock"))
    parser.add_argument("--profile-root", type=Path, action="append", help="Additional directory containing profile subdirectories")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("profiles", help="List built-in profiles")

    index = sub.add_parser("index", help="Build local embedding index from eligible current card versions")
    index.add_argument("--sandbox", action="store_true", help="Separate evaluation index including PENDING_REVIEW drafts")
    search = sub.add_parser("search", help="Retrieve versioned knowledge evidence")
    search.add_argument("question")
    search.add_argument("--top-k", type=int, default=5)
    search.add_argument("--sandbox", action="store_true")
    search.add_argument("--answer", action="store_true", help="Call the configured model to answer with retrieved citations")

    run = sub.add_parser("run", help="Build, generate, and audit one document")
    run.add_argument("--spec", type=Path, required=True)
    run.add_argument("--source", type=Path, action="append", default=[])
    run.add_argument("--source-url", action="append", default=[], help="Allowlisted HTTPS source URL")
    run.add_argument("--source-root", type=Path, action="append")
    run.add_argument("--allow-host", action="append", default=[], help="Host allowed for --source-url")

    demo = sub.add_parser("demo", help="Run the offline general knowledge-card example")
    demo.add_argument("--example-root", type=Path)

    status = sub.add_parser("status", help="Show workspace status")
    status.add_argument("--document")

    review = sub.add_parser("review", help="Approve or return the exact current version")
    review.add_argument("--document", required=True)
    review.add_argument("--sha", required=True)
    review.add_argument("--reviewer", required=True)
    review.add_argument("--decision", choices=["approve", "return"], required=True)
    review.add_argument("--reason", default="")

    export = sub.add_parser("export", help="Export an approved document")
    export.add_argument("--document", required=True)

    publish = sub.add_parser("publish", help="Register a completed manual publication")
    publish.add_argument("--document", required=True)
    publish.add_argument("--operator", required=True)
    publish.add_argument("--target", default="manual")
    publish.add_argument("--external-id", default="")
    publish.add_argument("--note", default="")
    publish.add_argument("--confirmed", action="store_true")

    evaluate = sub.add_parser("evaluate", help="Record configured post-publication checks")
    evaluate.add_argument("--document", required=True)
    evaluate.add_argument("--evaluator", required=True)
    evaluate.add_argument("--check", action="append", default=[])
    evaluate.add_argument("--reason", action="append", default=[], help="Failure reason in id=text form")
    evaluate.add_argument("--notes", default="")

    serve = sub.add_parser("serve", help="Start the generic local Web workbench")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8770)
    serve.add_argument("--source-root", type=Path)
    serve.add_argument("--allow-host", action="append", default=[], help="Host allowed for Web source URLs")
    serve.add_argument("--open-browser", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    # The launcher changes to the project root. Installed CLI users configure
    # their current directory, without accidentally loading a parent project's key.
    load_dotenv(Path.cwd() / ".env", override=False, encoding="utf-8-sig")
    args = build_parser().parse_args(argv)
    profiles = ProfileRegistry(args.profile_root)
    if args.command == "profiles":
        _json([profile.to_dict() for profile in profiles.list()])
        return 0
    if args.command in {"index", "search"}:
        from knowledge_pipeline.retrieval import EmbeddingProvider, KnowledgeIndex, answer_question
        from knowledge_pipeline.storage.sqlite import WorkspaceStore

        store = WorkspaceStore(args.workspace)
        index = KnowledgeIndex(args.workspace, EmbeddingProvider.from_env(), sandbox=args.sandbox)
        if args.command == "index":
            _json(index.rebuild(store))
        else:
            hits = index.search(args.question, top_k=args.top_k, store=store)
            result = {"question": args.question, "sandbox": args.sandbox, "hits": hits}
            if args.answer:
                result["answer"] = answer_question(provider_from_env(args.provider), args.question, hits)
            _json(result)
        return 0
    if args.command == "serve":
        from knowledge_pipeline.web.server import serve

        serve(
            workspace=args.workspace, provider_name=args.provider,
            host=args.host, port=args.port, source_root=args.source_root,
            open_browser=args.open_browser, profile_roots=args.profile_root,
            allowed_hosts=args.allow_host,
        )
        return 0
    service = PipelineService(args.workspace, provider_from_env(args.provider), profiles)
    if args.command == "run":
        spec = KnowledgeSpec.from_dict(_value(args.spec))
        _json(service.run_from_inputs(
            spec, args.source, args.source_url,
            allowed_roots=args.source_root, allowed_hosts=args.allow_host,
        ))
    elif args.command == "demo":
        # Keep the default fixture inside the package so installed wheels can
        # run the offline demo without a checkout or a particular working dir.
        package_root = Path(__file__).resolve().parents[1]
        example = (args.example_root or package_root / "demo_data").resolve()
        spec = KnowledgeSpec.from_dict(_value(example / "spec.yaml"))
        _json(service.run_from_paths(spec, [example / "source.md"], allowed_roots=[example]))
    elif args.command == "status":
        _json(service.status(args.document))
    elif args.command == "review":
        _json(service.review(args.document, args.sha, args.reviewer, args.decision, args.reason))
    elif args.command == "export":
        _json(service.export(args.document))
    elif args.command == "publish":
        _json(service.register_publication(
            args.document, operator=args.operator, target=args.target,
            external_id=args.external_id, note=args.note, confirmed=args.confirmed,
        ))
    elif args.command == "evaluate":
        _json(service.evaluate(
            args.document, _checks(args.check), args.evaluator, args.notes,
            failure_reasons=_key_values(args.reason, "reasons"),
        ))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, RuntimeError, KeyError) as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(2)
