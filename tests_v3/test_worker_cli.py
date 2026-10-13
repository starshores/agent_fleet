"""Public member commands against temporary state; no real native runtime."""
import contextlib
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from masteragent.cli import main
from masteragent.runtime import CodexRunner
from masteragent.runtime_config import RuntimeConfig
from masteragent.service import Platform


class WorkerCliTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="ma3-worker-cli-")
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        workspace = self.root / "workspace"
        workspace.mkdir()
        package = self.root / "business.json"
        package.write_text(json.dumps({"schema_version": 1, "platform_api": 1,
            "package_id": "synthetic", "version": "1.0.0",
            "roles": [{"role_id": "reviewer", "display_name": "Reviewer", "instructions": "Use synthetic text",
                       "tools": ["respond"]}], "knowledge": {},
            "tools": {"respond": {"adapter": "agent-text", "description": "Respond"}},
            "workflows": {}}), encoding="utf-8")
        self.platform = Platform(self.root / "state", external_adapters={"agent-text"})
        self.platform.install("a", package, workspace)
        self.platform.activate("a")
        registered = self.platform.register("a", "reviewer")
        self.actor_path = self.root / "actor.json"
        self.actor_path.write_text(json.dumps(registered), encoding="utf-8")
        from masteragent.service import Actor
        self.actor = Actor("a", "reviewer", registered["generation"], registered["token"])
        home = self.root / "runtime"
        home.mkdir()
        self.config = RuntimeConfig(1, "codex-app-server", Path(sys.executable).resolve(),
            "synthetic-version", "synthetic-model", "agent-text", "a", "reviewer", 1, home, 10, 15)
        self.config_path = self.root / "runtime.json"
        self.config_path.write_text(json.dumps(self.config.as_public_dict()), encoding="utf-8")

    def invoke(self, *args, config=False):
        argv = ["--state-root", str(self.platform.state_root)]
        if config:
            argv += ["--runtime-config", str(self.config_path)]
        output, errors = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            code = main([*argv, *args])
        return code, json.loads(output.getvalue() or errors.getvalue())

    def start(self):
        return self.platform.start_member(self.actor, "a" * 64, "respond", 60)

    def test_serve_requires_explicit_runtime_and_actor(self):
        code, value = self.invoke("serve", "--actor-file", str(self.actor_path), "--tool", "respond")
        self.assertEqual(code, 2)
        self.assertEqual(value["error"]["code"], "missing_runtime_config")
        code, value = self.invoke("serve", "--tool", "respond", config=True)
        self.assertEqual(code, 2)
        self.assertEqual(value["error"]["code"], "invalid_command")
        self.assertEqual(self.platform.members("a"), [])

    def test_members_is_read_only_and_does_not_leak_bearer_tokens(self):
        member = self.start()
        before = hashlib.sha256(self.platform.db_path.read_bytes()).hexdigest()
        code, value = self.invoke("members", "a")
        self.assertEqual(code, 0)
        self.assertEqual(value[0]["member_id"], member["member_id"])
        self.assertNotIn(member["member_token"], json.dumps(value))
        self.assertNotIn(self.actor.token, json.dumps(value))
        self.assertEqual(hashlib.sha256(self.platform.db_path.read_bytes()).hexdigest(), before)

    def test_stop_member_persists_fence_without_process_lookup(self):
        member = self.start()
        self.platform.submit("a", "reviewer", "synthetic task")
        attempt = self.platform.claim_member_task(self.actor, member["member_id"], member["member_token"])
        with patch("subprocess.Popen", side_effect=AssertionError("no process")):
            code, value = self.invoke("stop-member", "a", member["member_id"], "--reason", "local stop")
        self.assertEqual(code, 0)
        self.assertEqual(value["state"], "stopping")
        self.assertEqual(self.platform.task_for(self.actor, attempt["task_id"])["state"], "needs_reconcile")
        self.assertEqual(self.platform.results("a"), [])

    def test_expire_members_does_not_requeue(self):
        member = self.start()
        self.platform.submit("a", "reviewer", "synthetic task")
        attempt = self.platform.claim_member_task(self.actor, member["member_id"], member["member_token"])
        with self.platform._db(write=True) as db:
            db.execute("UPDATE member_sessions SET expires_at=0")
        code, value = self.invoke("expire-members", "a")
        self.assertEqual(code, 0)
        self.assertEqual(value[0]["state"], "lost")
        self.assertEqual(self.platform.task_for(self.actor, attempt["task_id"])["state"], "needs_reconcile")

    def test_serve_executes_one_task_with_public_cli_and_no_new_registration(self):
        self.platform.submit("a", "reviewer", "synthetic task")
        calls = []
        def factory(platform, config):
            def supervisor(*args, **kwargs):
                calls.append(1)
                return {"outcome": "succeeded", "output": "synthetic response", "thread_id": "thread-one",
                        "turn_id": "turn-one", "usage": None, "returncode": 0,
                        "termination_confirmed": True, "controlled_shutdown": True}
            return CodexRunner(platform, config, supervisor=supervisor, probe=lambda config: {})
        with patch("masteragent.worker.CodexRunner", side_effect=factory), \
             patch("subprocess.Popen", side_effect=AssertionError("no real runtime")), \
             patch.object(Platform, "register", side_effect=AssertionError("no automatic registration")):
            code, value = self.invoke("serve", "--actor-file", str(self.actor_path), "--tool", "respond",
                                      "--max-tasks", "1", config=True)
        self.assertEqual(code, 0, value)
        self.assertEqual(value["state"], "stopped")
        self.assertEqual(value["completed_tasks"], 1)
        self.assertEqual(calls, [1])
        self.assertEqual(len(self.platform.attempts("a")), 1)

    def test_bad_limits_never_create_member(self):
        for limit in ("0", "-1"):
            code, value = self.invoke("serve", "--actor-file", str(self.actor_path), "--tool", "respond",
                                      "--max-tasks", limit, config=True)
            self.assertEqual(code, 2)
            self.assertEqual(value["error"]["code"], "invalid_member_limit")
        self.assertEqual(self.platform.members("a"), [])

    def test_claimed_runner_uses_database_identity_not_supplied_task_fields(self):
        task = self.platform.submit("a", "reviewer", "actual claimed task")
        other = self.platform.submit("a", "reviewer", "unclaimed task")
        member = self.start()
        issued = self.platform.claim_member_task(self.actor, member["member_id"], member["member_token"])
        prompts = []
        def supervisor(argv, prompt, *args, **kwargs):
            prompts.append(prompt)
            return {"outcome": "succeeded", "output": "done", "thread_id": "thread-one",
                    "turn_id": "turn-one", "usage": None, "returncode": 0,
                    "termination_confirmed": True, "controlled_shutdown": True}
        runner = CodexRunner(self.platform, self.config, supervisor=supervisor, probe=lambda config: {})
        tampered = {**issued, "task_id": other["task_id"], "tool_id": "ungranted", "role_id": "other"}
        result = runner.run_claimed(self.actor, tampered)
        self.assertEqual(result["task_state"], "completed")
        self.assertIn("actual claimed task", prompts[0])
        self.assertNotIn("unclaimed task", prompts[0])
        self.assertEqual(self.platform.task_for(self.actor, task["task_id"])["state"], "completed")
        self.assertEqual(self.platform.task_for(self.actor, other["task_id"])["state"], "queued")

    def test_claimed_runner_rejects_invalid_lease_before_probe_or_launch(self):
        from masteragent.errors import PlatformError
        member = self.start()
        self.platform.submit("a", "reviewer", "task")
        issued = self.platform.claim_member_task(self.actor, member["member_id"], member["member_token"])
        def forbidden(*args, **kwargs):
            self.fail("Invalid lease reached the runtime")
        runner = CodexRunner(self.platform, self.config, supervisor=forbidden, probe=forbidden)
        with self.assertRaises(PlatformError):
            runner.run_claimed(self.actor, {**issued, "lease_token": "forged"})
        self.assertEqual(self.platform.results("a"), [])


if __name__ == "__main__":
    unittest.main()
