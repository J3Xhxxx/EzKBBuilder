from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from knowledge_pipeline.core.contracts import KnowledgeSpec, PipelineState, canonical_json, sha256_text  # noqa: E402
from knowledge_pipeline.core.render import DocumentContractError  # noqa: E402
from knowledge_pipeline.core.service import PipelineService  # noqa: E402
from knowledge_pipeline.cli.main import main  # noqa: E402
from knowledge_pipeline.providers.base import ModelRequest  # noqa: E402
from knowledge_pipeline.providers.mock import MockProvider  # noqa: E402
from knowledge_pipeline.providers.openai_compatible import OpenAICompatibleProvider  # noqa: E402
from knowledge_pipeline.sources.loader import SourcePolicy, load_local_sources  # noqa: E402
from knowledge_pipeline.storage.sqlite import WorkspaceStore  # noqa: E402
from knowledge_pipeline.web.server import Workbench  # noqa: E402


class GenericPipelineLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / "guide.md"
        self.source.write_text("# Aurora\n\n运行 `aurora init` 初始化目录。目录非空时先清理或更换目录。", encoding="utf-8")
        self.spec = KnowledgeSpec(
            document_id="DOC-001", title="Aurora 使用指南", profile_id="product_docs",
            audience="开发人员", objective="说明初始化和错误处理", metadata={"version": "1.0"},
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_full_version_bound_review_export_publish_and_evaluation(self) -> None:
        service = PipelineService(self.root / "workspace", MockProvider())
        first = service.run_from_paths(self.spec, [self.source], allowed_roots=[self.root])
        self.assertEqual("PENDING_REVIEW", first["status"])
        with self.assertRaisesRegex(ValueError, "no longer current"):
            service.review(self.spec.document_id, "0" * 64, "reviewer", "approve")
        with self.assertRaisesRegex(ValueError, "requires a reason"):
            service.review(self.spec.document_id, first["document_sha256"], "reviewer", "return")
        service.review(self.spec.document_id, first["document_sha256"], "reviewer", "return", "需要补充版本边界")

        revised = KnowledgeSpec(
            document_id=self.spec.document_id, title="Aurora 初始化与排错", profile_id="product_docs",
            audience=self.spec.audience, objective=self.spec.objective, metadata={"version": "1.1"},
        )
        second = service.run_from_paths(revised, [self.source], allowed_roots=[self.root])
        self.assertEqual(2, second["generation"])
        self.assertNotEqual(first["document_sha256"], second["document_sha256"])
        service.review(revised.document_id, second["document_sha256"], "reviewer", "approve")

        exported = service.export(revised.document_id)
        with zipfile.ZipFile(exported["zip"]) as archive:
            self.assertEqual({"DOC-001.md", "DOC-001.json", "manifest.json"}, set(archive.namelist()))
            manifest = json.loads(archive.read("manifest.json"))
            self.assertEqual(second["document_sha256"], manifest["document_sha256"])
        with self.assertRaisesRegex(ValueError, "explicitly confirmed"):
            service.register_publication(revised.document_id, operator="operator")
        service.register_publication(revised.document_id, operator="operator", confirmed=True)

        checks = {item.id: True for item in service.profiles.get("product_docs").evaluation_checks}
        failed = dict(checks)
        failed["troubleshooting"] = False
        with self.assertRaisesRegex(ValueError, "require reasons"):
            service.evaluate(revised.document_id, failed, "tester")
        fail_result = service.evaluate(
            revised.document_id, failed, "tester",
            failure_reasons={"troubleshooting": "未命中目录非空错误的恢复步骤"},
        )
        self.assertFalse(fail_result.passed)
        failed_status = service.status(revised.document_id)
        self.assertEqual(PipelineState.EVALUATION_FAILED.value, failed_status["state"])
        self.assertEqual("未命中目录非空错误的恢复步骤", failed_status["latest_evaluation"]["failure_reasons"]["troubleshooting"])
        pass_result = service.evaluate(revised.document_id, checks, "tester")
        self.assertTrue(pass_result.passed)
        self.assertEqual(PipelineState.EVALUATION_PASSED.value, service.status(revised.document_id)["state"])

    def test_repair_runs_once_and_is_reaudited(self) -> None:
        failed_audit = json.dumps({
            "passed": False, "repairable": True,
            "findings": [{"code": "MISSING_DETAIL", "severity": "ERROR", "section": "overview", "message": "缺少细节", "source_refs": ["SRC001"]}],
        }, ensure_ascii=False)
        passed_audit = json.dumps({"passed": True, "repairable": False, "findings": []}, ensure_ascii=False)
        provider = MockProvider(responses={"audit": [failed_audit, passed_audit]})
        service = PipelineService(self.root / "repair-workspace", provider)
        result = service.run_from_paths(self.spec, [self.source], allowed_roots=[self.root])
        self.assertEqual("PENDING_REVIEW", result["status"])
        self.assertEqual(2, result["generation"])
        self.assertEqual(["generate", "audit", "repair", "audit"], [item.role for item in provider.requests])
        for request in provider.requests:
            # Repair must retain the same scope and answer objective as generation.
            self.assertEqual(self.spec.to_dict(), json.loads(request.user_prompt)["spec"])
        versions = service.status(self.spec.document_id)["versions"]
        self.assertEqual(2, len(versions))
        self.assertEqual(versions[0]["content_sha256"], versions[1]["document"]["provenance"]["parent_sha256"])


class GeneralKnowledgeCardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / "reading.md"
        self.source.write_text(
            "# 阅读笔记\n\n阅读笔记记录原文要点及对应页码。\n\n复述应区分作者观点与自己的理解。",
            encoding="utf-8",
        )
        self.spec = KnowledgeSpec(document_id="reading.notes-1", title="如何做阅读笔记", audience="读书会成员")
        self.sections = {
            "summary": "阅读笔记记录原文要点及对应页码。[SRC001]",
            "key_points": "复述时区分作者观点与自己的理解。[SRC001]",
        }

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _generated(self, sections: dict) -> str:
        return json.dumps({"sections": sections, "used_source_ids": ["SRC001"]}, ensure_ascii=False)

    @staticmethod
    def _failed_audit(section: str) -> str:
        return json.dumps({
            "passed": False, "repairable": True,
            "findings": [{
                "code": "UNSUPPORTED_CLAIM", "severity": "ERROR", "section": section,
                "message": "来源不支持此表述", "source_refs": ["SRC001"],
            }],
        }, ensure_ascii=False)

    def test_same_default_profile_handles_distinct_nontechnical_domains(self) -> None:
        provider = MockProvider()
        service = PipelineService(self.root / "workspace", provider)
        history_source = self.root / "history.md"
        history_source.write_text(
            "# 历史材料阅读\n\n阅读历史材料时记录作者与写作年代。\n\n比较材料时分别列出相同和不同的表述。",
            encoding="utf-8",
        )
        history_spec = KnowledgeSpec.from_dict({
            "id": "history-2", "title": "历史材料的比较阅读", "audience": "历史兴趣小组",
            "objective": "区分材料的出处与观点", "metadata": {"subject": "历史"},
        })
        for spec, source, excerpt in (
            (self.spec, self.source, "阅读笔记记录原文要点及对应页码。"),
            (history_spec, history_source, "阅读历史材料时记录作者与写作年代。"),
        ):
            with self.subTest(topic=spec.title):
                result = service.run_from_paths(spec, [source], allowed_roots=[self.root])
                self.assertEqual("PENDING_REVIEW", result["status"])
                row = service.status(spec.document_id)
                self.assertEqual("knowledge_card", row["profile_id"])
                document = row["version"]["document"]
                self.assertEqual({"summary", "key_points"}, set(document["sections"]))
                self.assertIn(excerpt, document["sections"]["summary"])
                self.assertNotIn("technique_ids", document["provenance"]["spec"]["metadata"])
                self.assertIsNone(row["latest_review"])
        requests = [json.loads(request.user_prompt) for request in provider.requests if request.role == "generate"]
        self.assertEqual([self.spec.to_dict(), history_spec.to_dict()], [item["spec"] for item in requests])
        self.assertNotEqual(requests[0]["source_bundle"], requests[1]["source_bundle"])

    def test_minimal_card_completes_review_export_publication_and_generic_evaluation(self) -> None:
        provider = MockProvider(responses={"generate": self._generated(self.sections)})
        service = PipelineService(self.root / "workspace", provider)
        result = service.run_from_paths(self.spec, [self.source], allowed_roots=[self.root])
        self.assertEqual("PENDING_REVIEW", result["status"])
        self.assertEqual(["generate", "audit"], [request.role for request in provider.requests])
        with self.assertRaisesRegex(ValueError, "only an approved"):
            service.export(self.spec.document_id)
        service.review(self.spec.document_id, result["document_sha256"], "读书会审核人", "approve")
        exported = service.export(self.spec.document_id)
        with zipfile.ZipFile(exported["zip"]) as archive:
            document = json.loads(archive.read(f"{self.spec.document_id}.json"))
            self.assertEqual(self.sections, document["sections"])
            markdown = archive.read(f"{self.spec.document_id}.md").decode("utf-8")
            self.assertEqual(2, sum(line.startswith("## ") for line in markdown.splitlines()))
            self.assertLess(markdown.index("## 主题与范围"), markdown.index("## 知识正文"))
            saved = service.status(self.spec.document_id)["version"]
            self.assertEqual(markdown, (service.store.root / saved["markdown_path"]).read_text(encoding="utf-8"))
        publication = service.register_publication(self.spec.document_id, operator="整理人", confirmed=True)
        checks = {"groundedness": True, "relevance": True, "clarity": True, "retrieval": True}
        with self.assertRaisesRegex(ValueError, "exactly match"):
            service.evaluate(self.spec.document_id, {"groundedness": True}, "读者")
        failed = {**checks, "retrieval": False}
        with self.assertRaisesRegex(ValueError, "require reasons"):
            service.evaluate(self.spec.document_id, failed, "读者")
        failure = service.evaluate(
            self.spec.document_id, failed, "读者", failure_reasons={"retrieval": "检索阅读笔记未命中此卡"},
        )
        self.assertFalse(failure.passed)
        passed = service.evaluate(self.spec.document_id, checks, "读者")
        self.assertTrue(passed.passed)
        self.assertEqual(publication.export_sha256, passed.publication_sha256)
        self.assertEqual(result["document_sha256"], passed.document_sha256)
        self.assertEqual("EVALUATION_PASSED", service.status(self.spec.document_id)["state"])

    def test_omitted_or_empty_optional_sections_stay_absent_through_audit_and_storage(self) -> None:
        for number, optional in enumerate(({}, {"examples": "", "application": " \n "})):
            with self.subTest(optional=optional):
                provider = MockProvider(responses={"generate": self._generated({**self.sections, **optional})})
                service = PipelineService(self.root / f"workspace-{number}", provider)
                result = service.run_from_paths(self.spec, [self.source], allowed_roots=[self.root])
                self.assertEqual("PENDING_REVIEW", result["status"])
                audit = json.loads(provider.requests[-1].user_prompt)
                self.assertEqual(self.sections, audit["document"]["sections"])
                self.assertEqual(2, sum(line.startswith("## ") for line in audit["rendered_document"].splitlines()))
                self.assertEqual(self.sections, service.status(self.spec.document_id)["version"]["document"]["sections"])

    def test_invalid_generation_stops_before_audit_or_version_creation(self) -> None:
        candidates = (
            ({"summary": self.sections["summary"]}, "missing required section: key_points"),
            ({**self.sections, "summary": " "}, "missing required section: summary"),
            ({**self.sections, "unrequested_section": "额外段落"}, "unexpected sections"),
            ({**self.sections, "examples": ["例子"]}, "section must be a string: examples"),
            ({**self.sections, "examples": None}, "section must be a string: examples"),
            ({**self.sections, "application": {"step": "一"}}, "section must be a string: application"),
            ({**self.sections, "key_points": "不存在的依据。[SRC999]"}, "unknown citations"),
        )
        for number, (sections, error) in enumerate(candidates):
            with self.subTest(sections=sections):
                provider = MockProvider(responses={"generate": self._generated(sections)})
                service = PipelineService(self.root / f"workspace-{number}", provider)
                with self.assertRaisesRegex(DocumentContractError, error):
                    service.run_from_paths(self.spec, [self.source], allowed_roots=[self.root])
                self.assertEqual(["generate"], [request.role for request in provider.requests])
                row = service.status(self.spec.document_id)
                self.assertEqual("STOPPED", row["state"])
                self.assertEqual([], row["versions"])

    def test_unsupported_optional_example_can_be_removed_and_entire_card_is_reaudited(self) -> None:
        unsupported = "每天记录笔记一定能使阅读速度提高三倍。[SRC001]"
        provider = MockProvider(responses={
            "generate": self._generated({**self.sections, "examples": unsupported}),
            "audit": [self._failed_audit("examples"), json.dumps({"passed": True, "repairable": False, "findings": []})],
            "repair": json.dumps({"repaired_sections": {"examples": ""}, "used_source_ids": ["SRC001"]}),
        })
        service = PipelineService(self.root / "workspace", provider)
        result = service.run_from_paths(self.spec, [self.source], allowed_roots=[self.root])
        self.assertEqual("PENDING_REVIEW", result["status"])
        self.assertEqual(2, result["generation"])
        self.assertEqual(["generate", "audit", "repair", "audit"], [request.role for request in provider.requests])
        repair_request = json.loads(provider.requests[2].user_prompt)
        self.assertEqual(["examples"], repair_request["target_sections"])
        reaudit = json.loads(provider.requests[3].user_prompt)
        self.assertEqual(self.sections, reaudit["document"]["sections"])
        self.assertNotIn(unsupported, reaudit["rendered_document"])
        versions = service.status(self.spec.document_id)["versions"]
        self.assertEqual(unsupported, versions[0]["document"]["sections"]["examples"])
        self.assertEqual(self.sections, versions[1]["document"]["sections"])
        self.assertEqual(versions[0]["content_sha256"], versions[1]["document"]["provenance"]["parent_sha256"])
        self.assertEqual(versions[1]["content_sha256"], result["audit"]["document_sha256"])

    def test_repair_cannot_delete_a_required_section(self) -> None:
        for target in self.sections:
            with self.subTest(target=target):
                provider = MockProvider(responses={
                    "generate": self._generated(self.sections),
                    "audit": self._failed_audit(target),
                    "repair": json.dumps({"repaired_sections": {target: ""}, "used_source_ids": ["SRC001"]}),
                })
                service = PipelineService(self.root / target, provider)
                with self.assertRaisesRegex(DocumentContractError, "empty required section"):
                    service.run_from_paths(self.spec, [self.source], allowed_roots=[self.root])
                self.assertEqual(["generate", "audit", "repair"], [request.role for request in provider.requests])
                row = service.status(self.spec.document_id)
                self.assertEqual("STOPPED", row["state"])
                self.assertEqual(1, len(row["versions"]))
                self.assertEqual(self.sections, row["version"]["document"]["sections"])

    def test_saved_card_remains_readable_after_its_template_is_removed(self) -> None:
        app = Workbench(self.root / "workspace", "mock", self.root)
        self.addCleanup(app.close)
        app.service.run_from_paths(self.spec, [self.source], allowed_roots=[self.root])
        original = app.document(self.spec.document_id)
        with patch.object(app.profiles, "get", side_effect=KeyError("template was removed")):
            recovered = app.document(self.spec.document_id)
        self.assertTrue(original["profile_available"])
        self.assertFalse(recovered["profile_available"])
        self.assertEqual(original["profile"], recovered["profile"])
        self.assertEqual(original["version"]["document"], recovered["version"]["document"])
        self.assertEqual(original["version"]["markdown"], recovered["version"]["markdown"])
        self.assertEqual("PENDING_REVIEW", recovered["state"])

    def test_cli_default_demo_produces_a_general_card_waiting_for_human_review(self) -> None:
        output = io.StringIO()
        workspace = self.root / "cli-workspace"
        with patch("knowledge_pipeline.cli.main.load_dotenv"), patch("sys.stdout", output):
            result = main(["--workspace", str(workspace), "--provider", "mock", "demo"])
        self.assertEqual(0, result)
        run = json.loads(output.getvalue())
        self.assertEqual("PENDING_REVIEW", run["status"])
        row = PipelineService(workspace, MockProvider()).status(run["document_id"])
        self.assertEqual("knowledge_card", row["profile_id"])
        self.assertEqual({"summary", "key_points"}, set(row["version"]["document"]["sections"]))
        self.assertIsNone(row["latest_review"])
        self.assertIsNone(row["latest_publication"])


class StorageAndSourceTests(unittest.TestCase):
    def test_restart_marks_unowned_running_task_interrupted(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store = WorkspaceStore(Path(temp))
            spec = KnowledgeSpec(document_id="DOC-RECOVERY", title="恢复测试", profile_id="product_docs")
            store.create_document(spec)
            task, _ = store.create_task(spec.document_id, "run", sha256_text(canonical_json(spec.to_dict())))
            store.update_task(task["task_id"], "RUNNING")
            store.transition(spec.document_id, PipelineState.GENERATING, "test")
            recovered = WorkspaceStore(Path(temp))
            self.assertEqual(PipelineState.INTERRUPTED.value, recovered.get_document(spec.document_id)["state"])
            self.assertEqual("INTERRUPTED", recovered.list_tasks()[0]["status"])

    def test_local_source_cannot_escape_allowed_root(self) -> None:
        with tempfile.TemporaryDirectory() as temp, tempfile.TemporaryDirectory() as other:
            outside = Path(other) / "outside.md"
            outside.write_text("secret", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "outside allowed roots"):
                load_local_sources([outside], SourcePolicy(allowed_roots=(Path(temp),)))


class DirectProviderCapabilityTests(unittest.TestCase):
    def _request(self) -> ModelRequest:
        return ModelRequest(
            role="generate", system_prompt="system", user_prompt="user", trace_id="trace",
            response_schema={"type": "object"}, timeout_seconds=10,
        )

    @patch("knowledge_pipeline.providers.openai_compatible.post_json")
    def test_response_format_is_omitted_without_explicit_capability(self, post_json) -> None:
        post_json.return_value = (
            {"id": "r1", "model": "m", "choices": [{"message": {"content": "{}"}}]},
            SimpleNamespace(headers={}),
        )
        provider = OpenAICompatibleProvider(api_base="https://api.example", api_key="secret", model="m")
        provider.complete(self._request())
        self.assertNotIn("response_format", post_json.call_args.kwargs["body"])

    @patch("knowledge_pipeline.providers.openai_compatible.post_json")
    def test_json_schema_is_sent_when_capability_is_enabled(self, post_json) -> None:
        post_json.return_value = (
            {"id": "r1", "model": "m", "choices": [{"message": {"content": "{}"}}]},
            SimpleNamespace(headers={}),
        )
        provider = OpenAICompatibleProvider(
            api_base="https://api.example", api_key="secret", model="m", supports_json_schema=True,
        )
        provider.complete(self._request())
        self.assertEqual("json_schema", post_json.call_args.kwargs["body"]["response_format"]["type"])


if __name__ == "__main__":
    unittest.main()
