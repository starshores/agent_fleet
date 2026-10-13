"""Coordinator-to-process integration using isolated synthetic Python children."""
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest

from masteragent.codex_exec import execute
from masteragent.runtime import CodexRunner
from masteragent.runtime_config import load_runtime_config
from masteragent.service import Platform


CHILD = r'''
import json, pathlib, subprocess, sys, time
mode = sys.argv[1]
prompt = sys.stdin.read()
root = pathlib.Path.cwd()
def emit(value):
    print(json.dumps(value), flush=True)
if mode == 'cancel':
    descendant = "import pathlib,time; p=pathlib.Path('descendant-heartbeat'); "
    descendant += "\nfor _ in range(500):\n p.write_text(str(time.monotonic())); time.sleep(.02)\n"
    subprocess.Popen([sys.executable, '-B', '-c', descendant],
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    deadline = time.monotonic() + 3
    while not (root / 'descendant-heartbeat').exists():
        if time.monotonic() >= deadline:
            sys.exit(8)
        time.sleep(.01)
emit({'type': 'thread.started', 'thread_id': 'synthetic-process-thread'})
emit({'type': 'turn.started'})
if mode == 'cancel':
    time.sleep(30)
    sys.exit(7)
if mode == 'success':
    deadline = time.monotonic() + 5
    while not (root / 'coordination-released').exists():
        if time.monotonic() >= deadline:
            sys.exit(6)
        time.sleep(.01)
if mode == 'malformed':
    print('not a json event', flush=True)
    time.sleep(1)
    sys.exit(5)
output = json.dumps({
    'text_context_received': 'SYNTHETIC_ROLE_KNOWLEDGE' in prompt,
    'business_file_absent': not (root / 'business-private.txt').exists(),
    'project_config_absent': not (root / '.codex').exists(),
    'agents_absent': not (root / 'AGENTS.md').exists(),
})
emit({'type': 'item.completed', 'item': {'type': 'agent_message', 'id': 'message', 'text': output}})
emit({'type': 'turn.completed', 'usage': {'input_tokens': 11, 'cached_input_tokens': 0, 'output_tokens': 5}})
sys.exit(9 if mode == 'nonzero' else 0)
'''


class RuntimeProcessTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="masteragent-v3-runtime-process-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.workspace = self.root / "business-workspace"
        self.workspace.mkdir()
        (self.workspace / ".codex").mkdir()
        (self.workspace / ".codex" / "config.toml").write_text('synthetic_bait = "DO_NOT_LOAD"', encoding="utf-8")
        (self.workspace / "AGENTS.md").write_text("SYNTHETIC_BUSINESS_FILE_INSTRUCTION_BAIT", encoding="utf-8")
        (self.workspace / "business-private.txt").write_text("SYNTHETIC_PRIVATE_BUSINESS_FILE", encoding="utf-8")
        self.package = self.root / "package.json"
        self.package.write_text(json.dumps({
            "schema_version": 1, "platform_api": 1, "package_id": "process_fixture", "version": "1.0.0",
            "roles": [{"role_id": "reviewer", "display_name": "Reviewer", "instructions": "SYNTHETIC_ROLE_KNOWLEDGE", "tools": ["respond"]}],
            "knowledge": {}, "tools": {"respond": {"adapter": "agent-text", "description": "Answer supplied text"}},
            "workflows": {},
        }), encoding="utf-8")
        self.platform = self.handle()
        self.platform.install("a", self.package, self.workspace)
        self.platform.activate("a")
        self.actor = self.platform.bind("a", "reviewer")
        self.home = self.root / "dedicated-runtime-home"
        self.home.mkdir()
        config_path = self.root / "runtime.json"
        config_path.write_text(json.dumps({
            "schema_version": 1, "runtime": "codex-exec", "executable": sys.executable,
            "expected_version": "Python test", "model": "synthetic-model", "adapter_id": "agent-text",
            "installation_id": "a", "role_id": "reviewer", "generation": 1,
            "codex_home": str(self.home), "timeout_seconds": 8, "lease_seconds": 15,
        }), encoding="utf-8")
        self.config = load_runtime_config(config_path)
        self.execution_directories = []
        self.native_outcomes = []

    def handle(self):
        return Platform(self.root / "state", external_adapters={"agent-text"})

    def run_child(self, mode, callback=None):
        task = self.platform.submit("a", "reviewer", "Synthetic process integration " + mode)
        def supervisor(argv, prompt, cwd, env, timeout_seconds, heartbeat, on_event,
                       cancelled=None, heartbeat_seconds=5):
            self.execution_directories.append(Path(cwd))
            def record(kind, detail):
                on_event(kind, detail)
                if callback is not None:
                    callback(kind, detail, Path(cwd))
            outcome = execute([sys.executable, "-B", "-c", CHILD, mode], prompt, cwd, env,
                              timeout_seconds, heartbeat, record, cancelled=cancelled,
                              heartbeat_seconds=min(.1, heartbeat_seconds))
            self.native_outcomes.append(outcome)
            return outcome
        runner = CodexRunner(self.platform, self.config, supervisor=supervisor,
                             probe=lambda _: {"compatible": True, "version": "Python test"})
        return runner.run(self.actor, task["task_id"], "respond")

    def test_real_child_success_and_concurrent_coordination_with_private_cwd(self):
        submissions = []
        def coordinate(kind, detail, cwd):
            if kind == "runtime_thread":
                submissions.append(self.handle().submit("a", "reviewer", "Concurrent independent write"))
                (cwd / "coordination-released").write_text("Synthetic coordination committed", encoding="utf-8")
        returned = self.run_child("success", coordinate)
        self.assertEqual("completed", returned["task_state"], returned)
        self.assertTrue(returned["termination_confirmed"])
        self.assertEqual("queued", submissions[0]["state"])
        self.assertEqual({"text_context_received": True, "business_file_absent": True,
                          "project_config_absent": True, "agents_absent": True},
                         json.loads(returned["result"]["output"]))
        cwd = self.execution_directories[0]
        self.assertIn(self.home, cwd.parents)
        self.assertNotEqual(self.workspace, cwd)
        self.assertEqual("synthetic-process-thread", returned["thread_id"])
        history = self.platform.history("a")
        self.assertEqual(1, len(history["results"]))
        self.assertEqual(1, len(history["outbox"]))
        stopped = next(event for event in history["attempt_events"] if event["kind"] == "runtime_stopped")
        self.assertEqual({"input_tokens": 11, "cached_input_tokens": 0, "output_tokens": 5}, stopped["detail"]["usage"])
        self.assertEqual("SYNTHETIC_PRIVATE_BUSINESS_FILE", (self.workspace / "business-private.txt").read_text(encoding="utf-8"))

    def test_durable_stop_terminates_actual_child_and_descendant_without_fake_result(self):
        requests = []
        def stop(kind, detail, cwd):
            if kind == "runtime_thread":
                independent = self.handle()
                attempt = independent.attempts("a")[0]
                requests.append(independent.request_runtime_stop("a", attempt["attempt_id"], "Synthetic operator cancellation"))
        returned = self.run_child("cancel", stop)
        self.assertTrue(requests[0]["stop_requested"])
        self.assertFalse(requests[0]["termination_confirmed"])
        self.assertEqual("cancelled", returned["outcome"], returned)
        self.assertEqual("needs_reconcile", returned["task_state"])
        self.assertTrue(returned["termination_confirmed"])
        self.assertIsNotNone(self.native_outcomes[0]["returncode"])
        heartbeat = self.execution_directories[0] / "descendant-heartbeat"
        first = heartbeat.read_text(encoding="utf-8")
        time.sleep(.15)
        self.assertEqual(first, heartbeat.read_text(encoding="utf-8"), "Descendant still writes after confirmed termination")
        history = self.platform.history("a")
        self.assertEqual([], history["results"])
        self.assertEqual([], history["outbox"])
        self.assertEqual("expired", history["attempts"][0]["state"])
        self.assertIn("runtime_cancel_requested", [event["kind"] for event in history["attempt_events"]])

    def test_malformed_stream_and_nonzero_after_success_are_uncertain(self):
        for mode, expected in (("malformed", "protocol_error"), ("nonzero", "interrupted")):
            with self.subTest(mode=mode):
                returned = self.run_child(mode)
                self.assertEqual(expected, returned["outcome"], returned)
                self.assertEqual("needs_reconcile", returned["task_state"])
                self.assertTrue(returned["termination_confirmed"])
                self.assertIsNone(returned["result"])
                self.assertIsNone(self.native_outcomes[-1]["output"])
        self.assertEqual([], self.platform.results("a"))
        self.assertEqual([], self.platform.outbox("a"))
        self.assertEqual(2, len(self.platform.attempts("a")))


if __name__ == "__main__":
    unittest.main()
