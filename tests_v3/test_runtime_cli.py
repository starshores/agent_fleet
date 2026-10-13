"""Runtime CLI routing with synthetic deployments and no model processes."""
from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from masteragent import cli
from masteragent.service import Actor, Platform


class RuntimeCliTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="ma3-runtime-cli-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.state = self.root / "state"
        self.workspace = self.root / "business"
        self.workspace.mkdir()
        self.home = self.root / "runtime"
        self.home.mkdir()
        self.profile = self.document("runtime", {
            "schema_version": 1, "runtime": "codex-exec",
            "executable": str(Path(sys.executable).resolve()),
            "expected_version": "codex-cli synthetic", "model": "synthetic-model",
            "adapter_id": "agent-text", "installation_id": "a", "role_id": "editor",
            "generation": 1, "codex_home": str(self.home),
            "timeout_seconds": 90, "lease_seconds": 30,
        })
        self.package = self.document("package", {
            "schema_version": 1, "platform_api": 1, "package_id": "synthetic-business", "version": "1.0.0",
            "roles": [{"role_id": "editor", "display_name": "Synthetic editor",
                       "instructions": "Use only the supplied synthetic text.", "tools": ["draft"]}],
            "knowledge": {}, "tools": {"draft": {"adapter": "agent-text", "description": "Draft text"}},
            "workflows": {},
        })
        # An accidental process call makes any test fail before a binary starts.
        process_guard = patch("subprocess.Popen", side_effect=AssertionError("CLI test must not launch processes"))
        self.process_guard = process_guard.start()
        self.addCleanup(process_guard.stop)

    def document(self, name, value):
        path = self.root / (name + ".json")
        path.write_text(json.dumps(value), encoding="utf-8")
        return path

    def invoke(self, *arguments, runtime=True, state=True, error=None):
        options = []
        if state:
            options.extend(["--state-root", str(self.state)])
        if runtime:
            options.extend(["--runtime-config", str(self.profile)])
        output, errors = io.StringIO(), io.StringIO()
        with redirect_stdout(output), redirect_stderr(errors):
            status = cli.main([*options, *map(str, arguments)])
        self.assertEqual(status, 2 if error else 0, errors.getvalue())
        self.assertEqual(output.getvalue() if error else errors.getvalue(), "")
        value = json.loads(errors.getvalue() if error else output.getvalue())
        if error:
            self.assertEqual(value["error"]["code"], error)
        return value

    def install(self):
        self.invoke("install", "a", "--package", self.package, "--workspace", self.workspace)
        self.invoke("activate", "a")
        registration = self.invoke("register", "a", "editor")
        return registration, self.document("actor", registration)

    def test_external_capability_install_registration_and_profile_scope(self):
        with patch("masteragent.cli.CodexRunner") as runner, patch("masteragent.cli.probe_runtime") as probe:
            self.invoke("install", "other", "--package", self.package, "--workspace", self.workspace,
                        error="runtime_scope")
            self.assertFalse(self.state.exists())
            registration, _ = self.install()
            self.assertIsNone(registration["runtime"])
            self.assertFalse(registration["online"])
            task = self.invoke("submit", "a", "editor", "--text", "Synthetic drafting")
            self.invoke("run", "a", task["task_id"], "--tool", "draft", error="external_adapter_required")
            self.assertEqual(self.invoke("attempts", "a"), [])
            self.assertEqual(list(self.home.iterdir()), [])
            runner.assert_not_called()
            probe.assert_not_called()

    def test_runtime_check_is_read_only_and_requires_explicit_configuration(self):
        for command in ("runtime-check", "runtime-prepare"):
            self.invoke(command, runtime=False, error="missing_runtime_config")
        self.invoke("execute-task", "synthetic-task", "--actor-file", self.root / "absent.json",
                    "--tool", "draft", runtime=False, error="missing_runtime_config")
        self.assertFalse(self.state.exists())
        result = {"compatible": True, "authentication": "not_checked", "version": "codex-cli synthetic"}
        with patch("masteragent.cli.probe_runtime", return_value=result) as probe:
            self.assertEqual(self.invoke("runtime-check", state=False), result)
            self.assertEqual(probe.call_args.args[0].installation_id, "a")
        self.assertFalse(self.state.exists())
        self.assertEqual(list(self.home.iterdir()), [])

    def test_prepare_creates_only_scoped_binding_and_cannot_reuse_old_activation(self):
        self.install()
        result = self.invoke("runtime-prepare")
        self.assertEqual(result, {"prepared": True, "codex_home": str(self.home), "authentication": "not_checked"})
        marker = self.home / ".masteragent-runtime.json"
        before = marker.read_bytes()
        binding = json.loads(before)
        self.assertEqual(binding, {"schema_version": 1, "state_root": str(self.state),
                                  "installation_id": "a", "role_id": "editor", "generation": 1})
        self.assertEqual([path.name for path in self.home.iterdir()], [marker.name])
        self.assertEqual(self.invoke("runtime-prepare"), result)
        self.invoke("deactivate", "a")
        current = self.invoke("activate", "a")
        self.invoke("runtime-prepare", error="runtime_scope_mismatch")
        profile = json.loads(self.profile.read_text(encoding="utf-8"))
        profile["generation"] = current["generation"]
        self.profile.write_text(json.dumps(profile), encoding="utf-8")
        self.invoke("runtime-prepare", error="runtime_home_bound")
        self.assertEqual(marker.read_bytes(), before)

    def test_execute_dispatches_exact_actor_and_tool_without_running_model(self):
        registration, actor_file = self.install()
        task = self.invoke("submit", "a", "editor", "--text", "Synthetic task")
        result = {"outcome": "synthetic_route_only", "attempt_id": None}
        with patch("masteragent.cli.CodexRunner") as runner:
            runner.return_value.run.return_value = result
            self.assertEqual(self.invoke("execute-task", task["task_id"], "--actor-file", actor_file,
                                         "--tool", "draft"), result)
            platform, config = runner.call_args.args
            self.assertEqual(platform.state_root, self.state)
            self.assertIn("agent-text", platform.external_adapters)
            self.assertEqual((config.installation_id, config.role_id, config.generation), ("a", "editor", 1))
            actor, task_id, tool = runner.return_value.run.call_args.args
            self.assertEqual(actor, Actor("a", "editor", 1, registration["token"]))
            self.assertEqual((task_id, tool), (task["task_id"], "draft"))
        self.assertEqual(self.invoke("attempts", "a"), [])
        self.assertEqual(list(self.home.iterdir()), [])

    def test_cancel_route_records_request_and_never_claims_process_termination(self):
        registration, _ = self.install()
        task = self.invoke("submit", "a", "editor", "--text", "Synthetic running task")
        platform = Platform(self.state, external_adapters={"agent-text"})
        actor = Actor("a", "editor", 1, registration["token"])
        attempt = platform.claim(actor, task["task_id"], "draft", 60)
        self.invoke("cancel-attempt", "a", attempt["attempt_id"], "--reason", "Synthetic stop",
                    error="runtime_not_started")
        platform.record_runtime_event(actor, attempt["attempt_id"], attempt["lease_token"],
                                      "runtime_start_requested", {"runtime": "synthetic-test"})
        result = self.invoke("cancel-attempt", "a", attempt["attempt_id"], "--reason", "Synthetic stop")
        self.assertEqual(result, {"attempt_id": attempt["attempt_id"], "stop_requested": True,
                                  "termination_confirmed": False})
        self.assertEqual(self.invoke("cancel-attempt", "a", attempt["attempt_id"],
                                     "--reason", "Synthetic stop"), result)
        self.assertTrue(platform.runtime_stop_requested(actor, attempt["attempt_id"], attempt["lease_token"]))
        self.assertEqual(platform.task_for(actor, task["task_id"])["state"], "running")
        self.assertEqual(self.invoke("results", "a"), [])
        self.process_guard.assert_not_called()


if __name__ == "__main__":
    unittest.main()
