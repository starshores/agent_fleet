"""The CLI and HTTP surface share read-only workbench projections."""
from contextlib import redirect_stdout, redirect_stderr
from io import StringIO
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from masteragent.cli import main
from masteragent.workbench_query import WorkbenchQueries
from masteragent.service import Platform


ROOT = Path(__file__).resolve().parents[1]


class WorkbenchCliTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="ma31-cli-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.state = self.root / "state"

    def call(self, *args):
        out, err = StringIO(), StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main(["--state-root", str(self.state), *args])
        payload = out.getvalue() if code == 0 else err.getvalue()
        return code, json.loads(payload)

    def test_empty_query_never_creates_state_or_launches_runtime(self):
        with patch("subprocess.Popen", side_effect=AssertionError("Runtime must not start")):
            code, payload = self.call("workbench-query", "deployment")
        self.assertEqual(0, code)
        self.assertFalse(payload["data"]["initialized"])
        self.assertIsNone(payload["revision"])
        self.assertFalse(self.state.exists())

    def test_scoped_query_matches_shared_projection_and_keeps_bytes(self):
        workspace = self.root / "workspace"
        workspace.mkdir()
        platform = Platform(self.state)
        platform.install("a", ROOT / "examples/v3/business-a.json", workspace)
        platform.activate("a")
        task = platform.submit("a", "reviewer", "正文只能在详情里读取。")
        before = platform.db_path.read_bytes()
        code, payload = self.call("workbench-query", "tasks", "--installation-id", "a", "--limit", "1")
        self.assertEqual(0, code)
        expected = WorkbenchQueries(self.state).tasks("a", limit=1)
        self.assertEqual(expected["data"], payload["data"])
        self.assertNotIn("text", payload["data"]["items"][0])
        code, detail = self.call("workbench-query", "task", "--installation-id", "a", "--task-id", task["task_id"])
        self.assertEqual(0, code)
        self.assertEqual("正文只能在详情里读取。", detail["data"]["task"]["text"])
        self.assertEqual(before, platform.db_path.read_bytes())

    def test_invalid_filters_are_rejected_without_initialization(self):
        for args in (("tasks",), ("deployment", "--generation", "0"),
                     ("overview", "--installation-id", "a", "--limit", "1"),
                     ("task", "--installation-id", "a"),
                     ("connections", "--installation-id", "a", "--task-id", "x"),
                     ("task-history", "--installation-id", "a"),
                     ("task", "--installation-id", "a", "--task-id", "x", "--section", "attempts"),
                     ("task-history", "--installation-id", "a", "--task-id", "x", "--state", "queued")):
            with self.subTest(args=args):
                code, payload = self.call("workbench-query", *args)
                self.assertEqual(2, code)
                self.assertEqual("invalid_query", payload["error"]["code"])
        self.assertFalse(self.state.exists())

    def test_task_history_cli_matches_projection_and_preserves_old_task_contract(self):
        workspace = self.root / "workspace"
        workspace.mkdir()
        platform = Platform(self.state)
        platform.install("a", ROOT / "examples/v3/business-a.json", workspace)
        platform.activate("a")
        task = platform.submit("a", "reviewer", "跨代待处理任务")
        platform.deactivate("a")
        platform.activate("a")
        platform.resume_task("a", task["task_id"], "synthetic continuation")
        before = platform.db_path.read_bytes()
        args = ("workbench-query", "task-history", "--installation-id", "a", "--task-id", task["task_id"])
        code, first = self.call(*args, "--generation", "1", "--limit", "1")
        self.assertEqual(0, code)
        self.assertEqual(1, first["source"]["generation"])
        self.assertTrue(first["data"]["readonly"])
        code, tail = self.call(*args, "--generation", "1", "--cursor", first["data"]["next_cursor"])
        self.assertEqual(0, code)
        self.assertEqual([1], [row["generation"] for row in tail["data"]["items"]])
        code, edges = self.call(*args, "--section", "continuations", "--record-generation", "1")
        self.assertEqual(0, code)
        expected = WorkbenchQueries(self.state).task_history("a", task["task_id"], section="continuations", record_generation=1)
        self.assertEqual(expected["data"], edges["data"])
        code, old = self.call("workbench-query", "task", "--installation-id", "a", "--task-id", task["task_id"], "--generation", "1")
        self.assertEqual(2, code)
        self.assertEqual("task_not_found", old["error"]["code"])
        code, missing = self.call(*args, "--section", "attempts", "--record-generation", "0")
        self.assertEqual(2, code)
        self.assertEqual("history_generation_not_found", missing["error"]["code"])
        self.assertEqual(before, platform.db_path.read_bytes())

    def test_workbench_rejects_missing_state_and_runtime_config(self):
        for args in (("workbench",), ("--state-root", str(self.state), "--runtime-config", "unread-private.json", "workbench"),
                     ("--state-root", str(self.state), "--runtime-config", "unread-private.json", "workbench-query", "deployment")):
            out, err = StringIO(), StringIO()
            with redirect_stdout(out), redirect_stderr(err), patch("masteragent.cli.load_runtime_config", side_effect=AssertionError("Do not load configuration")):
                code = main(args)
            self.assertEqual(2, code)
            self.assertIn(json.loads(err.getvalue())["error"]["code"], {"missing_state_root", "invalid_command"})
        self.assertFalse(self.state.exists())

    def test_demo_creates_only_new_synthetic_destination(self):
        destination = self.root / "demo"
        command = [sys.executable, "-B", str(ROOT / "scripts/create_workbench_demo.py"), "--directory", str(destination)]
        first = subprocess.run(command, capture_output=True, encoding="utf-8", timeout=30)
        self.assertEqual(0, first.returncode, first.stderr)
        info = json.loads(first.stdout)
        self.assertEqual(0, info["models_started"])
        self.assertTrue(info["synthetic_only"])
        query = WorkbenchQueries(info["state_root"])
        self.assertEqual(2, query.deployment()["data"]["installations"][0]["generation"])
        self.assertIsNotNone(query.tasks("demo-a", limit=20)["data"]["next_cursor"])
        db = Path(info["state_root"]) / "state.sqlite3"
        before = db.read_bytes()
        repeated = subprocess.run(command, capture_output=True, encoding="utf-8", timeout=30)
        self.assertEqual(2, repeated.returncode)
        self.assertEqual(before, db.read_bytes())


if __name__ == "__main__":
    unittest.main()
