"""Real coordinator with synthetic App Server supervisors, no model requests."""
from dataclasses import replace
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from masteragent.errors import PlatformError
from masteragent.runtime import CodexRunner, prepare_deployment, probe_runtime
from masteragent.runtime_config import RuntimeConfig, build_argv, prepare_runtime_home
from masteragent.service import Platform


class AppServerRunnerTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="ma3-app-server-runner-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.platform = Platform(self.root / "state", external_adapters={"agent-text"})
        self.install("a")
        self.platform.activate("a")
        self.actor = self.platform.bind("a", "reviewer")
        self.config = self.config_for("a", 1)
        self.task = self.platform.submit("a", "reviewer", "a private synthetic task")

    def install(self, installation):
        default = self.root / (installation + "-default")
        reviewer = self.root / (installation + "-reviewer")
        assistant = self.root / (installation + "-assistant")
        for path in (default, reviewer, assistant):
            path.mkdir()
        package = self.root / (installation + ".json")
        package.write_text(json.dumps({"schema_version": 1, "platform_api": 1,
            "package_id": installation + "_business", "version": "1.0.0",
            "roles": [{"role_id": role, "display_name": role, "instructions": installation + "_ONLY_CONTEXT",
                       "tools": ["respond"]} for role in ("reviewer", "assistant")],
            "knowledge": {"facts": installation + "_ONLY_KNOWLEDGE"},
            "tools": {"respond": {"adapter": "agent-text", "description": "Respond from text"}},
            "workflows": {}}), encoding="utf-8")
        self.platform.install(installation, package, default, role_deployments={
            "reviewer": {"workspace": str(reviewer), "access": "read-only"},
            "assistant": {"workspace": str(assistant), "access": "none"}})

    def config_for(self, installation, generation):
        home = self.root / f"{installation}-runtime-{generation}"
        home.mkdir()
        return RuntimeConfig(1, "codex-app-server", Path(sys.executable).resolve(),
                             "synthetic-version", "synthetic-model", "agent-text", installation,
                             "reviewer", generation, home, 60, 30)

    @staticmethod
    def outcome(**overrides):
        return {"outcome": "succeeded", "output": "synthetic answer", "thread_id": "synthetic-thread",
                "turn_id": "synthetic-turn", "usage": None, "returncode": 1,
                "termination_confirmed": True, "controlled_shutdown": True, **overrides}

    def execute(self, supervisor):
        return CodexRunner(self.platform, self.config, supervisor=supervisor,
                           probe=lambda config: {}).run(self.actor, self.task["task_id"], "respond")

    def test_inputs_persist_then_ack_without_business_paths_in_prompt(self):
        def supervisor(argv, prompt, cwd, env, timeout, heartbeat, event, **callbacks):
            self.assertEqual(argv[1:4], ["app-server", "--listen", "stdio://"])
            self.assertNotIn("--ignore-user-config", argv)
            self.assertNotIn(str(self.root / "a-reviewer"), prompt)
            self.assertNotEqual(cwd, self.root / "a-reviewer")
            self.assertIn("a_ONLY_CONTEXT", prompt)
            attempt = self.platform.attempts("a")[0]
            sent = self.platform.submit_runtime_input("a", attempt["attempt_id"], "Use the corrected synthetic label", "fix-1")
            selected = callbacks["controls"]()
            self.assertEqual(selected["input_id"], sent["input_id"])
            self.assertIsNone(callbacks["controls"]())
            callbacks["ack_control"](selected["input_id"], "accepted")
            heartbeat()
            return self.outcome()
        result = self.execute(supervisor)
        self.assertEqual(result["task_state"], "completed")
        self.assertEqual(result["returncode"], 1)  # Owned shutdown code is preserved, not model failure.
        self.assertEqual(self.platform.runtime_inputs("a")[0]["state"], "accepted")
        self.assertEqual(len(self.platform.results("a")), 1)

    def test_input_arriving_at_completion_cannot_silently_disappear(self):
        def supervisor(*args, **kwargs):
            attempt = self.platform.attempts("a")[0]
            self.platform.submit_runtime_input("a", attempt["attempt_id"], "late correction", "late-1")
            return self.outcome()
        result = self.execute(supervisor)
        self.assertEqual(result["task_state"], "needs_reconcile")
        self.assertEqual(self.platform.runtime_inputs("a")[0]["state"], "held")
        self.assertEqual(self.platform.results("a"), [])

    def test_uncertain_send_is_held_and_never_reclaimed(self):
        def supervisor(*args, **callbacks):
            attempt = self.platform.attempts("a")[0]
            self.platform.submit_runtime_input("a", attempt["attempt_id"], "correction", "send-1")
            selected = callbacks["controls"]()
            callbacks["ack_control"](selected["input_id"], "needs_reconcile")
            self.assertIsNone(callbacks["controls"]())
            return self.outcome(outcome="interrupted", output=None)
        result = self.execute(supervisor)
        self.assertEqual(result["task_state"], "needs_reconcile")
        self.assertEqual(self.platform.runtime_inputs("a")[0]["state"], "held")

    def test_success_without_controlled_shutdown_is_not_committed(self):
        result = self.execute(lambda *args, **kwargs: self.outcome(controlled_shutdown=False))
        self.assertEqual(result["task_state"], "needs_reconcile")
        self.assertEqual(self.platform.results("a"), [])

    def test_history_home_must_exclude_every_role_workspace(self):
        config = replace(self.config, codex_home=self.root / "a-assistant")
        with self.assertRaises(PlatformError) as caught:
            prepare_deployment(self.platform, config)
        self.assertEqual(caught.exception.code, "runtime_home_overlap")

    def test_app_server_home_rejects_custom_configuration_even_after_binding(self):
        prepare_runtime_home(self.config, self.platform.state_root, self.root / "a-reviewer", bind=True)
        injected = self.config.codex_home / "config.toml"
        injected.write_text("# synthetic unrelated configuration", encoding="utf-8")
        with self.assertRaises(PlatformError) as caught:
            prepare_deployment(self.platform, self.config)
        self.assertEqual(caught.exception.code, "runtime_config_contamination")

    def test_runtime_probe_only_checks_pinned_app_server_help(self):
        responses = [dict(outcome="completed", returncode=0, termination_confirmed=True, stdout=value)
                     for value in ("synthetic-version", "--listen stdio:// --config")]
        with patch("masteragent.runtime.inspect_command", side_effect=responses) as inspect:
            result = probe_runtime(self.config)
        self.assertEqual(inspect.call_args_list[1].args[0][1:], ["app-server", "--help"])
        self.assertTrue(result["capabilities"]["mid_turn_steering"])
        self.assertFalse(result["capabilities"]["live_team_member"])
        self.assertFalse(result["capabilities"]["business_file_tools"])

    def test_same_code_a_b_a_context_and_native_history_stay_separate(self):
        prompts = []
        def supervisor(argv, prompt, cwd, env, *args, **kwargs):
            prompts.append((prompt, cwd, env["CODEX_HOME"]))
            return self.outcome()
        self.execute(supervisor)
        original_actor, original_config = self.actor, self.config
        self.platform.deactivate("a")
        self.install("b")
        self.platform.activate("b")
        self.actor = self.platform.bind("b", "reviewer")
        self.task = self.platform.submit("b", "reviewer", "b private synthetic task")
        self.config = self.config_for("b", 1)
        self.execute(supervisor)
        self.assertNotIn("a_ONLY", prompts[1][0])
        self.platform.deactivate("b")
        generation = self.platform.activate("a")["generation"]
        self.actor = self.platform.bind("a", "reviewer")
        self.task = self.platform.submit("a", "reviewer", "a restored synthetic task")
        self.config = self.config_for("a", generation)
        with self.assertRaises(PlatformError):
            self.platform.context_for(original_actor)
        with self.assertRaises(PlatformError):
            prepare_deployment(self.platform, replace(original_config, generation=generation))
        self.execute(supervisor)
        self.assertNotIn("b_ONLY", prompts[2][0])
        self.assertEqual(len({row[2] for row in prompts}), 3)
        self.assertEqual(self.platform.context("a", "reviewer")["workspace"], str(self.root / "a-reviewer"))
