"""User-facing dev4 CLI workflows against temporary state and fake supervisors."""
from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from masteragent import cli
from masteragent.runtime import CodexRunner
from masteragent.service import Actor, Platform


class Dev4CliTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="ma3-dev4-cli-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.state = self.root / "state"
        self.default = self.directory("default")
        self.editor_workspace = self.directory("editor")
        self.reviewer_workspace = self.directory("reviewer")
        self.home = self.directory("synthetic-runtime")
        self.profile_data = {
            "schema_version": 1, "runtime": "codex-app-server",
            "executable": str(Path(sys.executable).resolve()),
            "expected_version": "codex-cli synthetic", "model": "synthetic-model",
            "adapter_id": "agent-text", "installation_id": "a", "role_id": "editor",
            "generation": 1, "codex_home": str(self.home),
            "timeout_seconds": 90, "lease_seconds": 30,
        }
        self.profile = self.document("runtime", self.profile_data)
        self.package = self.document("package", {
            "schema_version": 1, "platform_api": 1, "package_id": "synthetic-dev4", "version": "1.0.0",
            "roles": [{"role_id": role, "display_name": role, "instructions": "Synthetic text only", "tools": ["draft"]}
                      for role in ("editor", "reviewer")],
            "knowledge": {}, "tools": {"draft": {"adapter": "agent-text", "description": "Synthetic text"}},
            "workflows": {},
        })
        self.mapping = {
            "editor": {"workspace": str(self.editor_workspace), "access": "read-write"},
            "reviewer": {"workspace": str(self.reviewer_workspace), "access": "read-only"},
        }
        self.mapping_file = self.document("role-deployments", self.mapping, bom=True)
        guard = patch("subprocess.Popen", side_effect=AssertionError("Dev4 CLI tests must not launch real processes"))
        self.process_guard = guard.start()
        self.addCleanup(guard.stop)
        self.addCleanup(self.process_guard.assert_not_called)

    def directory(self, name):
        path = self.root / name
        path.mkdir()
        return path

    def document(self, name, value, bom=False):
        path = self.root / (name + ".json")
        path.write_text(json.dumps(value), encoding="utf-8-sig" if bom else "utf-8")
        return path

    def invoke(self, *arguments, error=None, profile=None, state=None):
        output, errors = io.StringIO(), io.StringIO()
        with redirect_stdout(output), redirect_stderr(errors):
            status = cli.main(["--state-root", str(state or self.state), "--runtime-config", str(profile or self.profile),
                               *map(str, arguments)])
        self.assertEqual(2 if error else 0, status, errors.getvalue())
        self.assertEqual("", output.getvalue() if error else errors.getvalue())
        result = json.loads(errors.getvalue() if error else output.getvalue())
        if error:
            self.assertEqual(error, result["error"]["code"])
        return result

    def install(self):
        self.invoke("install", "a", "--package", self.package, "--workspace", self.default,
                    "--role-deployments", self.mapping_file)
        self.invoke("activate", "a")
        registration = self.invoke("register", "a", "editor")
        actor_file = self.document("actor", registration, bom=True)
        actor = Actor("a", "editor", registration["generation"], registration["token"])
        return actor, actor_file

    def running(self, supports_inputs=True):
        actor, actor_file = self.install()
        task = self.invoke("submit", "a", "editor", "--text", "Synthetic initial task")
        attempt = self.invoke("claim", task["task_id"], "--actor-file", actor_file, "--tool", "draft")
        platform = Platform(self.state, external_adapters={"agent-text"})
        platform.record_runtime_event(actor, attempt["attempt_id"], attempt["lease_token"], "runtime_start_requested",
                                      {"runtime": "synthetic", "supports_runtime_inputs": supports_inputs})
        return platform, actor, task, attempt

    def test_install_role_mapping_is_persisted_and_each_context_has_only_its_binding(self):
        self.install()
        # New CLI calls create new handles: the returned mapping comes from disk.
        self.assertEqual(self.mapping, self.invoke("view", "a")["role_deployments"])
        for role, binding in self.mapping.items():
            context = self.invoke("context", "a", role)
            self.assertEqual(binding["workspace"], context["workspace"])
            self.assertEqual(binding["access"], context["workspace_access"])
            other = self.reviewer_workspace if role == "editor" else self.editor_workspace
            self.assertNotIn(str(other), json.dumps(context))
        self.assertNotIn("role_deployments", self.invoke("view", "a")["package"])

    def test_unknown_roles_and_invalid_deployment_paths_leave_no_installation(self):
        unknown = self.document("unknown-map", {"missing": self.mapping["editor"]})
        self.invoke("install", "a", "--package", self.package, "--workspace", self.default,
                    "--role-deployments", unknown, error="invalid_role_deployments")
        self.assertEqual([], self.invoke("status")["installations"])
        nested = self.editor_workspace / "nested"
        nested.mkdir()
        invalid = self.document("overlap-map", {**self.mapping, "reviewer": {"workspace": str(nested), "access": "read-only"}})
        self.invoke("install", "a", "--package", self.package, "--workspace", self.default,
                    "--role-deployments", invalid, error="workspace_overlap")
        self.assertEqual([], self.invoke("status")["installations"])
        self.install()
        self.invoke("context", "a", "missing", error="role_not_found")

    def test_steer_cli_persists_deduplicates_and_readonly_query_never_sends(self):
        platform, actor, task, attempt = self.running()
        command = ("steer-attempt", "a", attempt["attempt_id"], "--text", "Synthetic correction", "--request-id", "correction-1")
        row = self.invoke(*command)
        self.assertEqual("pending", row["state"])
        self.assertEqual(row, self.invoke(*command))
        self.invoke("steer-attempt", "a", attempt["attempt_id"], "--text", "Conflicting correction", "--request-id", "correction-1",
                    error="request_conflict")
        before = platform.db_path.read_bytes()
        names = sorted(path.name for path in self.state.iterdir())
        self.assertEqual([row], self.invoke("runtime-inputs", "a"))
        self.assertEqual([row], self.invoke("runtime-inputs", "a", "--attempt-id", attempt["attempt_id"]))
        self.assertEqual(before, platform.db_path.read_bytes())
        self.assertEqual(names, sorted(path.name for path in self.state.iterdir()))
        self.assertEqual("Synthetic initial task", platform.task_for(actor, task["task_id"])["text"])
        self.assertEqual([row], Platform(self.state, readonly=True).runtime_inputs("a", attempt["attempt_id"]))

    def test_scope_errors_do_not_expose_or_mutate_other_attempt_inputs(self):
        platform, actor, task, attempt = self.running()
        self.invoke("steer-attempt", "other", attempt["attempt_id"], "--text", "Synthetic", "--request-id", "x", error="runtime_scope")
        other_workspace = self.directory("other-installation")
        other_profile = self.document("other-runtime", {**self.profile_data, "installation_id": "b"})
        self.invoke("install", "b", "--package", self.package, "--workspace", other_workspace, profile=other_profile)
        self.invoke("runtime-inputs", "b", "--attempt-id", attempt["attempt_id"], profile=other_profile, error="attempt_scope")
        self.invoke("steer-attempt", "a", "unknown-attempt", "--text", "Synthetic", "--request-id", "x", error="attempt_scope")
        self.assertEqual([], platform.runtime_inputs("a"))
        self.assertEqual([], self.invoke("runtime-inputs", "b", profile=other_profile))

    def test_plain_text_runtime_cannot_accept_steering_without_capability(self):
        platform, actor, task, attempt = self.running(supports_inputs=False)
        before = platform.db_path.read_bytes()
        self.invoke("steer-attempt", "a", attempt["attempt_id"], "--text", "Synthetic", "--request-id", "x",
                    error="runtime_input_unsupported")
        self.assertEqual([], self.invoke("runtime-inputs", "a"))
        self.assertEqual([], self.invoke("outbox", "a"))
        self.assertEqual(before, platform.db_path.read_bytes())

    def test_app_server_execute_uses_selected_runner_and_delivers_cli_input(self):
        actor, actor_file = self.install()
        task = self.invoke("submit", "a", "editor", "--text", "Synthetic app-server work")
        observed = {}

        def supervisor(argv, prompt, cwd, environment, timeout, heartbeat, event, **options):
            self.assertIn("app-server", argv)
            self.assertEqual(str(self.home), environment["CODEX_HOME"])
            self.assertIn("Synthetic app-server work", prompt)
            self.assertNotEqual(self.editor_workspace, cwd)
            heartbeat()
            attempt = self.invoke("attempts", "a")[0]
            self.assertTrue(next(item["detail"] for item in attempt["events"] if item["kind"] == "runtime_start_requested")["supports_runtime_inputs"])
            row = self.invoke("steer-attempt", "a", attempt["attempt_id"], "--text", "Synthetic live correction", "--request-id", "live-1")
            claimed = options["controls"]()
            self.assertEqual(row["input_id"], claimed["input_id"])
            self.assertEqual("sending", self.invoke("runtime-inputs", "a")[0]["state"])
            options["ack_control"](claimed["input_id"], "accepted")
            self.assertIsNone(options["controls"]())
            observed["attempt_id"] = attempt["attempt_id"]
            return {"outcome": "succeeded", "output": "Synthetic final result", "thread_id": "synthetic-thread",
                    "turn_id": "synthetic-turn", "usage": {}, "returncode": 0,
                    "termination_confirmed": True, "controlled_shutdown": True}

        def runner(platform, config):
            self.assertEqual("codex-app-server", config.runtime)
            return CodexRunner(platform, config, probe=lambda _: {"compatible": True})

        with patch("masteragent.cli.CodexRunner", side_effect=runner), \
             patch("masteragent.codex_app_server.supervise_app_server", side_effect=supervisor) as app_server, \
             patch("masteragent.runtime.execute", side_effect=AssertionError("Wrong runtime selected")) as exec_runtime:
            result = self.invoke("execute-task", task["task_id"], "--actor-file", actor_file, "--tool", "draft")
        self.assertEqual("completed", result["task_state"])
        self.assertEqual(observed["attempt_id"], result["attempt_id"])
        self.assertEqual("Synthetic final result", self.invoke("results", "a")[0]["output"])
        self.assertEqual("accepted", self.invoke("runtime-inputs", "a")[0]["state"])
        app_server.assert_called_once()
        exec_runtime.assert_not_called()

    def test_execute_rejects_wrong_role_before_probe_or_supervisor(self):
        self.install()
        registration = self.invoke("register", "a", "reviewer")
        actor_file = self.document("reviewer-actor", registration)
        task = self.invoke("submit", "a", "reviewer", "--text", "Wrong configured role")
        with patch("masteragent.codex_app_server.supervise_app_server") as supervisor:
            self.invoke("execute-task", task["task_id"], "--actor-file", actor_file, "--tool", "draft", error="runtime_scope_mismatch")
            supervisor.assert_not_called()
        self.assertEqual([], self.invoke("attempts", "a"))
        self.assertEqual([], list(self.home.iterdir()))

    def test_readonly_input_query_does_not_create_missing_state(self):
        missing = self.root / "missing-state"
        self.invoke("runtime-inputs", "a", state=missing, error="not_initialized")
        self.assertFalse(missing.exists())


if __name__ == "__main__":
    unittest.main()
