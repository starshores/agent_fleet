"""Persistent member integration with real local state and fake model execution."""

import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import tomllib
import unittest
from unittest.mock import patch

from masteragent.errors import PlatformError
from masteragent.runtime import CodexRunner
from masteragent.runtime_config import RuntimeConfig
from masteragent.service import Platform
from masteragent.state_waiter import StateWaiter
from masteragent.worker import MemberWorker


class WorkerFixture(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="ma3-member-worker-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.platform = self.handle()
        self.install("a")
        self.platform.activate("a")
        self.actor = self.platform.bind("a", "parent")
        self.child = self.platform.bind("a", "child")
        self.config = self.config_for("a", 1)
        # Even an accidental probe fallback must not execute a native binary.
        self.no_native = patch("masteragent.runtime.inspect_command", side_effect=AssertionError("native probe forbidden"))
        self.no_native.start()
        self.addCleanup(self.no_native.stop)

    def handle(self):
        return Platform(self.root / "state", external_adapters={"agent-text"})

    def install(self, installation):
        workspace = self.root / (installation + "-workspace")
        workspace.mkdir()
        package = self.root / (installation + ".json")
        package.write_text(json.dumps({
            "schema_version": 1, "platform_api": 1, "package_id": installation + "_business", "version": "1.0.0",
            "roles": [
                {"role_id": "parent", "display_name": "parent", "instructions": installation + "_ONLY_CONTEXT", "tools": ["respond"]},
                {"role_id": "child", "display_name": "child", "instructions": installation + "_CHILD_CONTEXT", "tools": ["echo"]},
            ],
            "knowledge": {"fact": installation + "_ONLY_KNOWLEDGE"},
            "tools": {"respond": {"adapter": "agent-text", "description": "Respond using text"},
                      "echo": {"adapter": "echo", "description": "Synthetic local echo"}},
            "workflows": {},
        }), encoding="utf-8")
        self.platform.install(installation, package, workspace)

    def config_for(self, installation, generation):
        home = self.root / f"{installation}-runtime-{generation}"
        home.mkdir(exist_ok=True)
        return RuntimeConfig(1, "codex-app-server", Path(sys.executable).resolve(),
                             "synthetic-version", "synthetic-model", "agent-text", installation,
                             "parent", generation, home, 20, 15)

    @staticmethod
    def outcome(**overrides):
        return {"outcome": "succeeded", "output": "synthetic member response", "thread_id": "synthetic-thread",
                "turn_id": "synthetic-turn", "usage": None, "returncode": 1,
                "termination_confirmed": True, "controlled_shutdown": True, **overrides}

    def launch(self, supervisor=None, *, max_tasks=1, max_seconds=12, waiter_factory=None, observer=None):
        observed_wait = threading.Event()
        views, results, errors = [], [], []
        class ObservedWaiter(StateWaiter):
            def wait(self, timeout_seconds):
                # The real constructor and real arm have run before this hook.
                observed_wait.set()
                return super().wait(timeout_seconds)
        def on_state(view):
            views.append(view)
            if observer is not None:
                observer(view)
        runner = CodexRunner(self.platform, self.config,
                             supervisor=supervisor or (lambda *args, **kwargs: self.outcome()),
                             probe=lambda config: {"compatible": True, "version": "synthetic-version"})
        worker = MemberWorker(self.platform, self.config, self.actor, "respond", runner=runner,
                              waiter_factory=waiter_factory or ObservedWaiter, on_state=on_state)
        def serve():
            try:
                results.append(worker.run(max_tasks=max_tasks, max_seconds=max_seconds))
            except BaseException as exc:
                errors.append(exc)
        thread = threading.Thread(target=serve, daemon=True)
        thread.start()
        def cleanup():
            if thread.is_alive():
                try:
                    worker.stop("Synthetic test cleanup")
                except PlatformError:
                    pass
                thread.join(5)
            self.assertFalse(thread.is_alive(), "Synthetic member thread did not stop")
        self.addCleanup(cleanup)
        return worker, thread, observed_wait, results, errors, views

    def finish(self, launched):
        worker, thread, observed_wait, results, errors, views = launched
        thread.join(10)
        self.assertFalse(thread.is_alive(), "Member did not complete within synthetic test deadline")
        if errors:
            raise errors[0]
        self.assertEqual(1, len(results))
        return results[0]

    def task(self, task_id):
        return next(row for row in self.platform.history("a")["tasks"] if row["task_id"] == task_id)


@unittest.skipUnless(os.name == "nt", "Windows member notification integration")
class WorkerTests(WorkerFixture):
    def test_two_tasks_reuse_home_without_native_bundled_skill_contamination(self):
        launched_homes = []

        def supervisor(argv, prompt, cwd, env, *args, **kwargs):
            home = Path(env["CODEX_HOME"])
            launched_homes.append(home)
            overrides = [argv[index + 1] for index, value in enumerate(argv) if value == "-c"]
            settings = tomllib.loads("\n".join(overrides))
            # Reproduce native alpha.17.2's observed first-launch side effect:
            # without bundled.enabled=false, the next task's preflight fails.
            if settings.get("skills", {}).get("bundled", {}).get("enabled", True):
                generated = home / "skills" / ".system"
                generated.mkdir(parents=True)
                (generated / ".codex-system-skills.marker").write_text("synthetic", encoding="utf-8")
            return self.outcome()

        first = self.platform.submit("a", "parent", "FIRST_SKILL_ISOLATION_TASK")
        second = self.platform.submit("a", "parent", "SECOND_SKILL_ISOLATION_TASK")
        result = self.finish(self.launch(supervisor, max_tasks=2))
        self.assertEqual(("stopped", 2, 2),
                         (result["state"], result["attempted_tasks"], result["completed_tasks"]))
        self.assertEqual([self.config.codex_home, self.config.codex_home], launched_homes)
        self.assertFalse((self.config.codex_home / "skills").exists())
        attempts = self.platform.attempts("a")
        self.assertEqual([first["task_id"], second["task_id"]], [row["task_id"] for row in attempts])
        self.assertTrue(all(row["state"] == "completed" for row in attempts))

    def test_generated_looking_skill_directory_is_still_rejected_before_next_spawn(self):
        calls = []

        def supervisor(*args, **kwargs):
            calls.append(True)
            injected = self.config.codex_home / "skills" / ".system"
            injected.mkdir(parents=True)
            (injected / ".codex-system-skills.marker").write_text("untrusted marker", encoding="utf-8")
            (injected / "SKILL.md").write_text("synthetic untrusted skill", encoding="utf-8")
            return self.outcome()

        self.platform.submit("a", "parent", "FIRST_CONTAMINATION_TASK")
        second = self.platform.submit("a", "parent", "SECOND_CONTAMINATION_TASK")
        result = self.finish(self.launch(supervisor, max_tasks=2))
        self.assertEqual(("lost", 2, 1),
                         (result["state"], result["attempted_tasks"], result["completed_tasks"]))
        self.assertEqual(1, len(calls))
        self.assertEqual("needs_reconcile", self.task(second["task_id"])["state"])
        self.assertEqual("synthetic untrusted skill",
                         (self.config.codex_home / "skills" / ".system" / "SKILL.md").read_text(encoding="utf-8"))

    def test_idle_real_watcher_receives_two_tasks_with_one_attempt_each(self):
        prompts = []
        def supervisor(argv, prompt, *args, **kwargs):
            prompts.append(prompt)
            return self.outcome()
        launched = self.launch(supervisor, max_tasks=2)
        self.assertTrue(launched[2].wait(5))
        first = self.handle().submit("a", "parent", "FIRST_SYNTHETIC_WORK")
        second = self.handle().submit("a", "parent", "SECOND_SYNTHETIC_WORK")
        result = self.finish(launched)
        self.assertEqual(("stopped", 2, 2, "task_limit"),
                         (result["state"], result["attempted_tasks"], result["completed_tasks"], result["reason"]))
        self.assertEqual(2, len(prompts))
        self.assertIn("FIRST_SYNTHETIC_WORK", prompts[0])
        self.assertIn("SECOND_SYNTHETIC_WORK", prompts[1])
        attempts = self.platform.attempts("a")
        self.assertEqual([first["task_id"], second["task_id"]], [row["task_id"] for row in attempts])
        self.assertEqual(2, len({row["attempt_id"] for row in attempts}))
        self.assertTrue(all(row["state"] == "completed" for row in attempts))
        self.assertFalse(self.platform.members("a")[0]["online"])

    def test_busy_member_accepts_persistent_steering_without_second_attempt(self):
        entered, release = threading.Event(), threading.Event()
        consumed = []
        def supervisor(*args, **callbacks):
            entered.set()
            if not release.wait(5):
                raise RuntimeError("synthetic steering release missing")
            selected = callbacks["controls"]()
            consumed.append(selected)
            callbacks["ack_control"](selected["input_id"], "accepted")
            return self.outcome()
        self.addCleanup(release.set)
        task = self.platform.submit("a", "parent", "STEERING_SYNTHETIC_WORK")
        launched = self.launch(supervisor)
        self.assertTrue(entered.wait(5))
        attempt = self.platform.attempts("a")[0]
        steering = self.handle().submit_runtime_input("a", attempt["attempt_id"], "SYNTHETIC_CORRECTION", "correction-1")
        self.assertEqual("pending", self.handle().runtime_inputs("a")[0]["state"])
        release.set()
        result = self.finish(launched)
        self.assertEqual(1, result["completed_tasks"])
        self.assertEqual([steering["input_id"]], [row["input_id"] for row in consumed])
        self.assertEqual("accepted", self.handle().runtime_inputs("a")[0]["state"])
        self.assertEqual("completed", self.task(task["task_id"])["state"])
        self.assertEqual(1, len(self.platform.attempts("a")))

    def test_stop_fences_before_late_success_and_never_publishes_it(self):
        entered, release = threading.Event(), threading.Event()
        cancelled = []
        def supervisor(*args, **callbacks):
            entered.set()
            if not release.wait(5):
                raise RuntimeError("synthetic stop release missing")
            cancelled.append(callbacks["cancelled"]())
            return self.outcome(output="LATE_SYNTHETIC_RESULT")
        self.addCleanup(release.set)
        task = self.platform.submit("a", "parent", "STOP_SYNTHETIC_WORK")
        launched = self.launch(supervisor)
        self.assertTrue(entered.wait(5))
        launched[0].stop("Synthetic explicit stop")
        self.assertEqual("needs_reconcile", self.task(task["task_id"])["state"])
        stopping = self.platform.members("a")[0]
        self.assertEqual("stopping", stopping["state"])
        self.assertFalse(stopping["owned_attempt_active"])
        self.assertNotIn("runtime_busy", stopping)
        self.assertTrue(launched[1].is_alive())  # Supervisor is still awaiting release.
        self.assertNotEqual("stopped", stopping["state"])
        release.set()
        result = self.finish(launched)
        self.assertEqual([True], cancelled)
        self.assertEqual(0, result["completed_tasks"])
        self.assertEqual([], self.platform.results("a"))
        self.assertEqual(1, len(self.platform.attempts("a")))
        self.assertFalse(self.platform.members("a")[0]["online"])

    def test_decision_answer_wakes_waiting_member(self):
        task = self.platform.submit("a", "parent", "DECISION_SYNTHETIC_WORK")
        decision = self.platform.ask_decision(self.actor, task["task_id"], "Proceed with synthetic work?", "decision-1")
        launched = self.launch()
        self.assertTrue(launched[2].wait(5))
        self.assertEqual([], self.platform.attempts("a"))
        self.handle().answer_decision("a", decision["decision_id"], decision["revision"], "approve", "Synthetic approval")
        result = self.finish(launched)
        self.assertEqual(1, result["completed_tasks"])
        self.assertEqual("completed", self.task(task["task_id"])["state"])

    def test_child_completion_wakes_parent_and_supplies_only_its_reply(self):
        task = self.platform.submit("a", "parent", "PARENT_SYNTHETIC_WORK")
        request = self.platform.request_child(self.actor, task["task_id"], "child", "CHILD_SYNTHETIC_REPLY", "child-1")
        prompts = []
        def supervisor(argv, prompt, *args, **kwargs):
            prompts.append(json.loads(prompt.split("\n", 1)[1]))
            return self.outcome()
        launched = self.launch(supervisor)
        self.assertTrue(launched[2].wait(5))
        self.assertEqual([], self.platform.attempts("a"))
        self.handle().run_as(self.child, request["child_task_id"], "echo")
        result = self.finish(launched)
        self.assertEqual(1, result["completed_tasks"])
        self.assertEqual(1, len(prompts[0]["completed_requests"]))
        reply = prompts[0]["completed_requests"][0]
        self.assertEqual(request["request_id"], reply["request_id"])
        self.assertEqual("completed", reply["child_state"])
        self.assertIn("CHILD_SYNTHETIC_REPLY", json.dumps(reply["result"]))
        self.assertEqual("completed", self.task(task["task_id"])["state"])

    def test_supervisor_exception_marks_member_lost_and_does_not_retry(self):
        calls = []
        def supervisor(*args, **kwargs):
            calls.append("started")
            raise RuntimeError("synthetic process ownership unknown")
        task = self.platform.submit("a", "parent", "UNCERTAIN_SYNTHETIC_WORK")
        result = self.finish(self.launch(supervisor))
        self.assertEqual("lost", result["state"])
        self.assertEqual("runtime_termination_unconfirmed", result["reason"])
        self.assertEqual("needs_reconcile", self.task(task["task_id"])["state"])
        self.assertEqual(["started"], calls)
        self.assertEqual([], self.platform.results("a"))
        replacement = self.launch(supervisor, max_seconds=.15)
        replacement_result = self.finish(replacement)
        self.assertEqual(0, replacement_result["attempted_tasks"])
        self.assertEqual(["started"], calls)
        self.assertEqual(1, len(self.platform.attempts("a")))

    def test_success_without_termination_evidence_is_lost(self):
        self.platform.submit("a", "parent", "UNCONFIRMED_SYNTHETIC_WORK")
        result = self.finish(self.launch(lambda *args, **kwargs: self.outcome(termination_confirmed=False)))
        self.assertEqual("lost", result["state"])
        self.assertEqual(0, result["completed_tasks"])
        self.assertEqual([], self.platform.results("a"))

    def test_watcher_arm_failure_never_registers_online_member(self):
        class BrokenArm(StateWaiter):
            def arm(self):
                raise PlatformError("synthetic_watcher_failed", "Synthetic arm failure")
        launched = self.launch(waiter_factory=BrokenArm)
        with self.assertRaises(PlatformError) as caught:
            self.finish(launched)
        self.assertEqual("synthetic_watcher_failed", caught.exception.code)
        self.assertEqual([], self.platform.members("a"))
        self.assertEqual([], self.platform.attempts("a"))

    def test_failed_waiter_cannot_leave_member_online(self):
        class BrokenWait(StateWaiter):
            def wait(self, timeout_seconds):
                raise PlatformError("synthetic_watcher_failed", "Synthetic wait failure")
        result = self.finish(self.launch(waiter_factory=BrokenWait))
        self.assertEqual("synthetic_watcher_failed", result["reason"])
        self.assertFalse(self.platform.members("a")[0]["online"])
        self.assertEqual([], self.platform.attempts("a"))

    def test_business_switch_revokes_old_member_and_cannot_consume_other_queue(self):
        calls = []
        launched = self.launch(lambda *args, **kwargs: calls.append("unexpected") or self.outcome())
        self.assertTrue(launched[2].wait(5))
        self.install("b")
        self.platform.deactivate("a")
        self.platform.activate("b")
        foreign = self.platform.submit("b", "parent", "B_ONLY_SYNTHETIC_WORK")
        self.finish(launched)
        self.assertEqual([], calls)
        self.assertFalse(self.platform.members("a")[0]["online"])
        self.assertEqual("queued", self.platform.history("b")["tasks"][0]["state"])
        worker = launched[0]
        with self.assertRaises(PlatformError):
            self.platform.claim_member_task(self.actor, worker._member, worker._token, 15)
        self.platform.deactivate("b")
        self.platform.activate("a")
        with self.assertRaises(PlatformError):
            self.platform.renew_member(self.actor, worker._member, worker._token, 15)
        self.assertEqual(foreign["task_id"], self.platform.history("b")["tasks"][0]["task_id"])

    def test_stop_before_run_never_becomes_online(self):
        runner = CodexRunner(self.platform, self.config, supervisor=lambda *args, **kwargs: self.outcome(),
                             probe=lambda config: {})
        worker = MemberWorker(self.platform, self.config, self.actor, "respond", runner=runner)
        worker.stop("Synthetic stop before start")
        result = worker.run(max_tasks=1)
        self.assertEqual("not_started", result["state"])
        self.assertEqual([], self.platform.members("a"))


if __name__ == "__main__":
    unittest.main()
