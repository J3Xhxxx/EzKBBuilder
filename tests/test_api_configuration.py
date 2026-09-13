from __future__ import annotations

import io
import os
import sys
import tempfile
import unittest
from contextlib import chdir, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from knowledge_pipeline.cli.main import main  # noqa: E402
from knowledge_pipeline.providers.base import ModelProviderError, ModelRequest  # noqa: E402
from knowledge_pipeline.providers.openai_compatible import OpenAICompatibleProvider  # noqa: E402


class APIEndpointConfigurationTests(unittest.TestCase):
    @patch("knowledge_pipeline.providers.openai_compatible.post_json")
    def test_reasoning_controls_are_explicit_and_do_not_change_default_requests(self, post_json) -> None:
        post_json.return_value = ({"choices": [{"message": {"content": "{}"}}]}, SimpleNamespace(headers={}))
        default = OpenAICompatibleProvider(api_base="https://api.example/v1", api_key="fixture", model="model")
        default.complete(self._request("audit"))
        self.assertNotIn("enable_thinking", post_json.call_args.kwargs["body"])
        self.assertNotIn("thinking_budget", post_json.call_args.kwargs["body"])
        configured = OpenAICompatibleProvider(api_base="https://api.example/v1", api_key="fixture", model="model", enable_thinking=False)
        configured.complete(self._request("audit"))
        self.assertIs(False, post_json.call_args.kwargs["body"]["enable_thinking"])
        with self.assertRaises(ValueError):
            OpenAICompatibleProvider(api_base="https://api.example/v1", api_key="fixture", model="model", thinking_budget=0)

    @staticmethod
    def _request(role: str) -> ModelRequest:
        return ModelRequest(role=role, system_prompt="system", user_prompt="user", trace_id=f"test-{role}")

    @patch("knowledge_pipeline.providers.openai_compatible.post_json")
    def test_endpoint_forms_work_for_each_role_and_preserve_model_selection(self, post_json) -> None:
        post_json.return_value = (
            {"id": "test-response", "choices": [{"message": {"content": "{}"}}]},
            SimpleNamespace(headers={}),
        )
        endpoints = (
            ("https://api.example", "https://api.example/v1/chat/completions"),
            ("https://api.example/", "https://api.example/v1/chat/completions"),
            ("https://api.example/v1", "https://api.example/v1/chat/completions"),
            ("https://api.example/v1/", "https://api.example/v1/chat/completions"),
            ("https://api.example/v1/chat/completions", "https://api.example/v1/chat/completions"),
            ("https://api.example/v1/chat/completions/", "https://api.example/v1/chat/completions"),
            ("https://api.example/api/v3", "https://api.example/api/v3/chat/completions"),
            ("https://api.example/api/v3/", "https://api.example/api/v3/chat/completions"),
            ("http://localhost:1234/v1", "http://localhost:1234/v1/chat/completions"),
        )
        models = {"generate": "default-model", "audit": "audit-model", "repair": "repair-model"}
        for configured, expected in endpoints:
            provider = OpenAICompatibleProvider(
                api_base=configured, api_key="unused-test-placeholder", model="default-model",
                role_models={"audit": "audit-model", "repair": "repair-model"},
            )
            for role, selected in models.items():
                with self.subTest(endpoint=configured, role=role):
                    response = provider.complete(self._request(role))
                    self.assertEqual(expected, post_json.call_args.args[0])
                    self.assertEqual(selected, post_json.call_args.kwargs["body"]["model"])
                    self.assertEqual(selected, response.model)
                    self.assertEqual(f"test-{role}", post_json.call_args.kwargs["headers"]["X-Trace-Id"])

    @patch("knowledge_pipeline.providers.openai_compatible.post_json")
    def test_invalid_or_credential_bearing_urls_fail_without_echoing_the_url(self, post_json) -> None:
        invalid = (
            "ftp://api.example/v1",
            "file:///private-marker/api",
            "api.example/v1",
            "https://url-user:credential-marker@api.example/v1",
            "https://api.example/v1?token=query-marker",
            "https://api.example/v1#fragment-marker",
        )
        for configured in invalid:
            with self.subTest(endpoint=configured):
                with self.assertRaises(ModelProviderError) as caught:
                    OpenAICompatibleProvider(
                        api_base=configured, api_key="unused-test-placeholder", model="default-model",
                    )
                message = str(caught.exception)
                self.assertNotIn(configured, message)
                for marker in ("credential-marker", "query-marker", "fragment-marker", "private-marker"):
                    self.assertNotIn(marker, message)
        post_json.assert_not_called()


class LocalEnvironmentConfigurationTests(unittest.TestCase):
    def _run_profiles(self, directory: Path) -> None:
        # An accidental return to dotenv's implicit search can only discover
        # this test's parent file, never the developer's real configuration.
        with chdir(directory), patch("dotenv.main.find_dotenv", return_value=str(directory.parent / ".env")):
            with redirect_stdout(io.StringIO()):
                self.assertEqual(0, main(["profiles"]))

    def test_current_directory_utf8_bom_configuration_is_loaded(self) -> None:
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {}, clear=True):
            root = Path(temp)
            project = root / "project"
            project.mkdir()
            (root / ".env").write_text("KP_MODEL=parent-model\n", encoding="utf-8")
            (project / ".env").write_text(
                "KP_API_BASE=https://project.example/v1\nKP_MODEL=project-model\nKP_PROVIDER=mock\n",
                encoding="utf-8-sig",
            )
            self._run_profiles(project)
            self.assertEqual("https://project.example/v1", os.environ.get("KP_API_BASE"))
            self.assertEqual("project-model", os.environ.get("KP_MODEL"))
            self.assertEqual("mock", os.environ.get("KP_PROVIDER"))

    def test_existing_process_configuration_takes_priority_over_local_file(self) -> None:
        environment = {"KP_API_BASE": "https://environment.example/v1", "KP_MODEL": "environment-model"}
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, environment, clear=True):
            project = Path(temp) / "project"
            project.mkdir()
            (project / ".env").write_text(
                "KP_API_BASE=https://file.example/v1\nKP_MODEL=file-model\nKP_PROVIDER=mock\n",
                encoding="utf-8",
            )
            self._run_profiles(project)
            self.assertEqual("https://environment.example/v1", os.environ.get("KP_API_BASE"))
            self.assertEqual("environment-model", os.environ.get("KP_MODEL"))
            self.assertEqual("mock", os.environ.get("KP_PROVIDER"))

    def test_missing_local_file_does_not_load_parent_directory_configuration(self) -> None:
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {}, clear=True):
            root = Path(temp)
            project = root / "project"
            project.mkdir()
            (root / ".env").write_text(
                "KP_API_BASE=https://parent.example/v1\nKP_MODEL=parent-model\nKP_PROVIDER=mock\n",
                encoding="utf-8",
            )
            self._run_profiles(project)
            self.assertNotIn("KP_API_BASE", os.environ)
            self.assertNotIn("KP_MODEL", os.environ)
            self.assertNotIn("KP_PROVIDER", os.environ)


if __name__ == "__main__":
    unittest.main()
