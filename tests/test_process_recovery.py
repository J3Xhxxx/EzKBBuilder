from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from knowledge_pipeline.core.contracts import KnowledgeSpec, PipelineState  # noqa: E402
from knowledge_pipeline.storage.sqlite import WorkspaceStore  # noqa: E402


@contextmanager
def owned_child() -> Iterator[subprocess.Popen[str]]:
    """Keep one test-owned process alive until its stdin closes."""
    script = (
        "import sys\n"
        "print('ready', flush=True)\n"
        "for line in sys.stdin:\n"
        "    print('ack:' + line.strip(), flush=True)\n"
    )
    child = subprocess.Popen(
        [sys.executable, "-I", "-S", "-u", "-c", script],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True,
    )
    try:
        if child.stdout.readline().strip() != "ready":
            raise AssertionError("test child did not initialize")
        yield child
    finally:
        try:
            child.stdin.close()
        except BrokenPipeError:
            pass
        try:
            child.wait(timeout=5)
        except subprocess.TimeoutExpired:
            # Cleanup is restricted to the subprocess created above.
            child.terminate()
            child.wait(timeout=5)
        child.stdout.close()
        child.stderr.close()


class ProcessRecoveryTests(unittest.TestCase):
    def _assert_responding(self, child: subprocess.Popen[str]) -> None:
        self.assertIsNone(child.poll())
        child.stdin.write("still-running\n")
        child.stdin.flush()
        self.assertEqual("ack:still-running", child.stdout.readline().strip())

    @staticmethod
    def _create_owned_task(store: WorkspaceStore, pid: int) -> tuple[str, str]:
        spec = KnowledgeSpec(document_id="recovery-card", title="进程恢复测试")
        store.create_document(spec)
        task, _ = store.create_task(spec.document_id, "run", "recovery-test-run")
        store.acquire_document_lock(spec.document_id, f"{pid}:{task['task_id']}")
        store.update_task(task["task_id"], "RUNNING")
        store.transition(spec.document_id, PipelineState.GENERATING, "test_generation")
        return spec.document_id, task["task_id"]

    def test_invalid_pids_are_rejected_and_current_process_is_alive(self) -> None:
        for raw_pid in (None, "", "not-a-pid", "-1", "0", str(2**32), str(2**128)):
            with self.subTest(raw_pid=raw_pid):
                self.assertFalse(WorkspaceStore._pid_alive(raw_pid))
        self.assertTrue(WorkspaceStore._pid_alive(str(os.getpid())))

    def test_process_probe_and_second_store_leave_live_task_owner_running(self) -> None:
        with tempfile.TemporaryDirectory() as temp, owned_child() as child:
            self.assertTrue(WorkspaceStore._pid_alive(str(child.pid)))
            self._assert_responding(child)
            store = WorkspaceStore(Path(temp))
            document_id, task_id = self._create_owned_task(store, child.pid)

            reopened = WorkspaceStore(Path(temp))
            self._assert_responding(child)
            self.assertTrue(WorkspaceStore._pid_alive(str(child.pid)))
            self.assertEqual("GENERATING", reopened.get_document(document_id)["state"])
            task = next(item for item in reopened.list_tasks() if item["task_id"] == task_id)
            self.assertEqual("RUNNING", task["status"])
            with self.assertRaisesRegex(RuntimeError, "locked"):
                reopened.acquire_document_lock(document_id, f"{os.getpid()}:second-task")

    def test_exited_owner_is_detected_and_its_task_and_lock_are_recovered(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store = WorkspaceStore(Path(temp))
            with owned_child() as child:
                document_id, task_id = self._create_owned_task(store, child.pid)
                pid = child.pid
            self.assertEqual(0, child.returncode)
            self.assertFalse(WorkspaceStore._pid_alive(str(pid)))

            recovered = WorkspaceStore(Path(temp))
            self.assertEqual("INTERRUPTED", recovered.get_document(document_id)["state"])
            task = next(item for item in recovered.list_tasks() if item["task_id"] == task_id)
            self.assertEqual("INTERRUPTED", task["status"])
            self.assertIsNotNone(task["finished_at"])
            self.assertTrue(task["error"])
            new_owner = f"{os.getpid()}:restarted-task"
            recovered.acquire_document_lock(document_id, new_owner)
            recovered.release_document_lock(document_id, new_owner)
            self.assertEqual(0, recovered.recover_interrupted_tasks())


if __name__ == "__main__":
    unittest.main()
