from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import sysconfig
import tempfile
import unittest
import zipfile


ROOT = Path(__file__).resolve().parents[1]


class InstalledWheelTests(unittest.TestCase):
    """Exercise a wheel outside the checkout, without editable-install paths.

    The build interpreter needs setuptools and wheel. Set KP_TEST_BUILD_PYTHON
    to a local interpreter that has them if the application venv does not.
    Builds and installs never download dependencies or load a .env file.
    """

    @classmethod
    def setUpClass(cls) -> None:
        builder = os.environ.get("KP_TEST_BUILD_PYTHON", sys.executable)
        available = subprocess.run(
            [builder, "-I", "-c", "import setuptools, wheel"],
            capture_output=True, text=True, timeout=30,
        )
        if available.returncode:
            raise unittest.SkipTest("wheel tests need setuptools and wheel; set KP_TEST_BUILD_PYTHON")
        cls.temp = tempfile.TemporaryDirectory(prefix="kp-wheel-test-")
        cls.addClassCleanup(cls.temp.cleanup)
        cls.root = Path(cls.temp.name)
        project = cls.root / "project"
        project.mkdir()
        for name in ("pyproject.toml", "README.md", "LICENSE"):
            shutil.copy2(ROOT / name, project / name)
        shutil.copytree(
            ROOT / "src", project / "src",
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.egg-info"),
        )
        wheels = cls.root / "wheels"
        wheels.mkdir()
        cls._checked([
            builder, "-I", "-X", "utf8", "-c",
            "from setuptools.build_meta import build_wheel; import sys; build_wheel(sys.argv[1])",
            str(wheels),
        ], cwd=project)
        cls.wheel = next(wheels.glob("*.whl"))
        cls.installed = cls.root / "installed"
        cls._checked([
            sys.executable, "-I", "-X", "utf8", "-m", "pip", "--isolated", "install",
            "--no-index", "--no-deps", "--no-compile", "--target", str(cls.installed), str(cls.wheel),
        ])
        # Do not leave a source tree for the smoke process to find accidentally.
        if not project.resolve().is_relative_to(cls.root.resolve()):
            raise AssertionError("temporary build tree escaped its test directory")
        shutil.rmtree(project)

    @classmethod
    def _checked(cls, command: list[str], *, cwd: Path | None = None) -> str:
        result = subprocess.run(
            command, cwd=cwd or cls.root, capture_output=True,
            text=True, encoding="utf-8", timeout=60,
        )
        if result.returncode:
            raise AssertionError(f"command failed ({result.returncode}):\n{result.stdout}\n{result.stderr}")
        return result.stdout

    def _cli(self, workspace: Path, *arguments: str):
        # -I -S ignores PYTHONPATH and site .pth files. Add only the wheel and
        # the existing third-party dependencies, never the checkout's src.
        probe = """
import pathlib, sys
sys.path[:0] = [sys.argv[1], sys.argv[2]]
import knowledge_pipeline
assert pathlib.Path(knowledge_pipeline.__file__).resolve().is_relative_to(pathlib.Path(sys.argv[1]).resolve())
from unittest.mock import patch
from knowledge_pipeline.cli.main import main
with patch('knowledge_pipeline.cli.main.load_dotenv'), patch('socket.socket.connect', side_effect=AssertionError('offline smoke must not use the network')):
    raise SystemExit(main(sys.argv[3:]))
"""
        output = self._checked([
            sys.executable, "-I", "-S", "-X", "utf8", "-c", probe,
            str(self.installed), sysconfig.get_path("purelib"),
            "--provider", "mock", "--workspace", str(workspace), *arguments,
        ])
        return json.loads(output)

    def test_installed_wheel_lists_profiles_and_runs_default_demo(self) -> None:
        with zipfile.ZipFile(self.wheel) as archive:
            names = set(archive.namelist())
            license_name = next(name for name in names if ".dist-info/" in name and name.endswith("/LICENSE"))
            self.assertEqual((ROOT / "LICENSE").read_bytes(), archive.read(license_name))
            entrypoints = archive.read(next(name for name in names if name.endswith("/entry_points.txt"))).decode("utf-8")
            self.assertIn("ezkb-builder = knowledge_pipeline.cli.main:main", entrypoints)
            self.assertIn("knowledge-pipeline = knowledge_pipeline.cli.main:main", entrypoints)
        self.assertIn("knowledge_pipeline/demo_data/spec.yaml", names)
        self.assertIn("knowledge_pipeline/demo_data/source.md", names)
        self.assertIn("knowledge_pipeline/web/static/index.html", names)
        workspace = self.root / "default-workspace"
        profiles = self._cli(workspace, "profiles")
        self.assertEqual({"knowledge_card", "product_docs"}, {item["id"] for item in profiles})
        run = self._cli(workspace, "demo")
        self.assertEqual("PENDING_REVIEW", run["status"])
        self.assertEqual("CARD-OFFLINE-DEMO", run["document_id"])
        row = self._cli(workspace, "status", "--document", run["document_id"])
        self.assertEqual("knowledge_card", row["profile_id"])
        self.assertEqual({"summary", "key_points"}, set(row["version"]["document"]["sections"]))
        self.assertIsNone(row["latest_review"])
        self.assertIsNone(row["latest_publication"])

    def test_installed_demo_accepts_custom_example_root(self) -> None:
        example = self.root / "custom-example"
        example.mkdir()
        (example / "spec.yaml").write_text(
            "document_id: CUSTOM-DEMO\ntitle: Custom example\nprofile_id: product_docs\n",
            encoding="utf-8",
        )
        (example / "source.md").write_text("# Custom example\n\nCustom supplied material.", encoding="utf-8")
        workspace = self.root / "custom-workspace"
        run = self._cli(workspace, "demo", "--example-root", str(example))
        self.assertEqual("CUSTOM-DEMO", run["document_id"])
        self.assertEqual("PENDING_REVIEW", run["status"])
        row = self._cli(workspace, "status", "--document", "CUSTOM-DEMO")
        self.assertEqual("product_docs", row["profile_id"])
        self.assertIn("Custom supplied material.", "\n".join(row["version"]["document"]["sections"].values()))


if __name__ == "__main__":
    unittest.main()
