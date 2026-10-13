"""Actual cross-process state notification, with synthetic model execution."""

from dataclasses import asdict
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import unittest

from masteragent.service import Platform
from test_worker import WorkerFixture


@unittest.skipUnless(os.name == "nt", "Windows member notification integration")
class WorkerProcessTests(WorkerFixture):
    def test_hidden_child_process_submission_wakes_real_waiting_member(self):
        prompts = []
        def supervisor(argv, prompt, *args, **kwargs):
            prompts.append(prompt)
            return self.outcome()
        launched = self.launch(supervisor)
        self.assertTrue(launched[2].wait(5))
        script = (
            "import json,sys; from masteragent.service import Platform; "
            "p=Platform(sys.argv[1], external_adapters={'agent-text'}); "
            "print(json.dumps(p.submit('a','parent','CROSS_PROCESS_SYNTHETIC_WORK')))"
        )
        child = subprocess.Popen(
            [sys.executable, "-B", "-c", script, str(self.platform.state_root)],
            cwd=Path(__file__).resolve().parents[1],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        try:
            stdout, stderr = child.communicate(timeout=10)
            self.assertEqual(0, child.returncode, stderr.decode("utf-8", errors="replace"))
            submitted = json.loads(stdout.decode("utf-8"))
            result = self.finish(launched)
        finally:
            if child.poll() is None:
                child.kill()
                child.communicate(timeout=5)
        self.assertEqual(("stopped", 1), (result["state"], result["completed_tasks"]))
        self.assertIn("CROSS_PROCESS_SYNTHETIC_WORK", prompts[0])
        self.assertEqual("completed", self.task(submitted["task_id"])["state"])
        self.assertEqual(1, len(self.platform.attempts("a")))

    def test_terminated_member_process_expires_without_automatic_task_retry(self):
        task = self.platform.submit("a", "parent", "CRASH_SYNTHETIC_WORK")
        script = r'''
import json, sys
from masteragent.service import Actor, Platform
from masteragent.runtime import CodexRunner
from masteragent.runtime_config import RuntimeConfig
from masteragent.worker import MemberWorker
incoming = json.loads(sys.stdin.readline())
platform = Platform(incoming['state'], external_adapters={'agent-text'})
actor = Actor(**incoming['actor'])
config = RuntimeConfig(**incoming['config'])
def supervisor(*args, **kwargs):
    print('SYNTHETIC_SUPERVISOR_ENTERED', flush=True)
    sys.stdin.readline()
    raise RuntimeError('Synthetic release is not successful execution')
runner = CodexRunner(platform, config, supervisor=supervisor, probe=lambda config: {})
MemberWorker(platform, config, actor, 'respond', runner=runner).run(max_tasks=1)
'''
        child = subprocess.Popen(
            [sys.executable, "-B", "-c", script],
            cwd=Path(__file__).resolve().parents[1],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        received, lines = threading.Event(), []
        def read_ready():
            lines.append(child.stdout.readline())
            received.set()
        reader = threading.Thread(target=read_ready, daemon=True)
        try:
            child.stdin.write((json.dumps({"state": str(self.platform.state_root), "actor": asdict(self.actor),
                                          "config": self.config.as_public_dict()}) + "\n").encode("utf-8"))
            child.stdin.flush()
            reader.start()
            self.assertTrue(received.wait(5), "Synthetic child did not enter its fake supervisor")
            self.assertEqual([b"SYNTHETIC_SUPERVISOR_ENTERED"], [line.strip() for line in lines])
            self.assertEqual("running", self.task(task["task_id"])["state"])
            child.terminate()
            child.wait(timeout=5)
        finally:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=5)
            if reader.ident is not None:
                reader.join(5)
            child.communicate(timeout=5)
        self.assertFalse(reader.is_alive())
        # Advance the injectable lease clock; no sleeping or private DB edits.
        expired_time = time.time() + 60
        self.platform = Platform(self.root / "state", external_adapters={"agent-text"}, clock=lambda: expired_time)
        expired = self.platform.expire_members("a")
        self.assertEqual(1, len(expired))
        self.assertEqual("lost", expired[0]["state"])
        self.assertFalse(expired[0]["online"])
        self.assertEqual("needs_reconcile", self.task(task["task_id"])["state"])
        calls = []
        replacement = self.launch(lambda *args, **kwargs: calls.append("unexpected") or self.outcome(), max_seconds=.15)
        result = self.finish(replacement)
        self.assertEqual(0, result["attempted_tasks"])
        self.assertEqual([], calls)
        self.assertEqual(1, len(self.platform.attempts("a")))
        self.assertEqual([], self.platform.results("a"))


if __name__ == "__main__":
    unittest.main()
