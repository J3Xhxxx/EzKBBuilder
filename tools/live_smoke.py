"""Opt-in real API smoke test. Never approves or publishes generated cards."""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from knowledge_pipeline.core.contracts import KnowledgeSpec
from knowledge_pipeline.core.service import PipelineService
from knowledge_pipeline.providers.base import ModelProviderError, ModelResponse
from knowledge_pipeline.providers.factory import provider_from_env


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", choices=["language", "daily", "library", "astronomy"], required=True)
    parser.add_argument("--workspace", type=Path)
    parser.add_argument("--source-file", type=Path, help="Use a prepared local source instead of the case's default source")
    parser.add_argument("--fault-probe", action="store_true", help="Inject a local false language-card candidate; use real API audit and repair")
    args = parser.parse_args()
    if args.fault_probe and args.case != "language":
        parser.error("--fault-probe requires --case language")
    load_dotenv(ROOT / ".env", override=False, encoding="utf-8-sig")
    provider = provider_from_env()
    if provider.name == "mock":
        parser.error("Configure a real provider in the project .env before running this opt-in test")
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    label = "language-fault-probe" if args.fault_probe else args.case
    output = ROOT / "output" / "live-smoke" / f"{stamp}-{label}"
    output.mkdir(parents=True)
    workspace = args.workspace or Path(".knowledge-workspace-live-probes" if args.fault_probe else os.getenv("KP_WORKSPACE", ".knowledge-workspace"))
    if not workspace.is_absolute():
        workspace = ROOT / workspace
    fixtures = ROOT / "examples" / "knowledge_card_demo"
    names = {
        "language": ("spec.yaml", "source.md"),
        "daily": ("daily_method_spec.yaml", "daily_method_source.md"),
        "library": ("library_faq_spec.yaml", "library_faq_source.md"),
        "astronomy": ("astronomy_spec.yaml", "https://spaceplace.nasa.gov/light-year/en/"),
    }
    spec_name, source_name = names[args.case]
    source_path = args.source_file
    if source_path and not source_path.is_absolute():
        source_path = ROOT / source_path
    web_source = source_path is None and source_name.startswith("https://")
    if not web_source and source_path is None:
        source_path = fixtures / source_name
    spec_value = yaml.safe_load((fixtures / spec_name).read_text(encoding="utf-8"))
    spec_value["document_id"] = f"LIVE-{label.upper()}-{stamp}"
    spec = KnowledgeSpec.from_dict(spec_value)
    receipt = {
        "case": label, "document_id": spec.document_id,
        "synthetic_generation": args.fault_probe,
        "workspace": str(workspace.resolve()), "source": source_name if web_source else str(source_path.resolve()),
        "started_at": datetime.now().astimezone().isoformat(),
        "provider": provider.name, "call_limit": 6, "calls": [],
        "human_review_performed": False, "publication_performed": False,
    }

    def redact(value: str) -> str:
        for key, secret in os.environ.items():
            if key.startswith("KP_") and ("KEY" in key or "BASE" in key) and secret:
                value = value.replace(secret, "[redacted]")
        return value

    def save() -> None:
        (output / "receipt.json").write_text(
            redact(json.dumps(receipt, ensure_ascii=False, indent=2)), encoding="utf-8"
        )

    class MeasuredProvider:
        name = provider.name

        def complete(self, request):
            if args.fault_probe and request.role == "generate":
                candidate = {
                    "sections": {
                        "summary": "本卡整理项目演示资料采用的同义词与近义词划分。[SRC001]",
                        "key_points": "本资料中，同义词与近义词是完全互斥的分类；词义相近的两个词可以在所有句子里任意替换。[SRC001]",
                    },
                    "used_source_ids": ["SRC001"],
                }
                text = json.dumps(candidate, ensure_ascii=False)
                (output / "injected-candidate.json").write_text(text, encoding="utf-8")
                print("INJECTED local false candidate; generation API skipped", flush=True)
                return ModelResponse(text=text, provider="test-fixture", model="deliberately-false-candidate")
            if len(receipt["calls"]) >= receipt["call_limit"]:
                raise ModelProviderError("live smoke call limit reached")
            entry = {"role": request.role, "started_at": datetime.now().astimezone().isoformat()}
            receipt["calls"].append(entry)
            save()
            print(f"CALL {len(receipt['calls'])}: {request.role}", flush=True)
            started = time.monotonic()
            try:
                response = provider.complete(request)
                entry.update(model=response.model, usage=response.usage, status="responded")
                if request.role == "audit":
                    try:
                        entry["audit_passed"] = json.loads(response.text).get("passed")
                    except (ValueError, AttributeError):
                        pass
                (output / f"{len(receipt['calls']):02d}-{request.role}.txt").write_text(
                    redact(response.text), encoding="utf-8"
                )
                return response
            except Exception as exc:
                entry.update(status="error", error_type=type(exc).__name__, error=redact(str(exc)))
                raise
            finally:
                entry["seconds"] = round(time.monotonic() - started, 2)
                save()
                print(json.dumps(entry, ensure_ascii=True), flush=True)

    save()
    started = time.monotonic()
    exit_code = 0
    try:
        service = PipelineService(workspace, MeasuredProvider())
        if web_source:
            result = service.run_from_inputs(spec, source_urls=[source_name], allowed_hosts=["spaceplace.nasa.gov"])
        else:
            result = service.run_from_paths(spec, [source_path], allowed_roots=[source_path.resolve().parent])
        receipt["result"] = result
        receipt["status"] = result["status"]
        status = service.status(spec.document_id)
        receipt["markdown_path"] = str(service.store.root / status["version"]["markdown_path"])
        receipt["no_review_or_publication"] = status["latest_review"] is None and status["latest_publication"] is None
        (output / "document.json").write_text(
            redact(json.dumps(status["version"]["document"], ensure_ascii=False, indent=2)), encoding="utf-8"
        )
        exit_code = 0 if result["status"] == "PENDING_REVIEW" else 1
        if args.fault_probe:
            audits = [call.get("audit_passed") for call in receipt["calls"] if call["role"] == "audit" and call.get("status") == "responded"]
            receipt["fault_probe_verified"] = (
                audits == [False, True]
                and any(call["role"] == "repair" for call in receipt["calls"])
                and result["status"] == "PENDING_REVIEW"
            )
            if not receipt["fault_probe_verified"]:
                exit_code = 1
    except Exception as exc:
        receipt.update(status="ERROR", error_type=type(exc).__name__, error=redact(str(exc)))
        exit_code = 1
    finally:
        receipt["seconds"] = round(time.monotonic() - started, 2)
        save()
    print(json.dumps({k: receipt.get(k) for k in ("document_id", "status", "seconds", "markdown_path", "error_type", "error")}, ensure_ascii=True), flush=True)
    print(f"RECEIPT {output / 'receipt.json'}", flush=True)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
