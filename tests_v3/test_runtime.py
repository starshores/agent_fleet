"""Runner integration with fake supervisors; no Codex or model is executed."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from masteragent.errors import PlatformError
from masteragent.runtime import CodexRunner
from masteragent.runtime_config import load_runtime_config
from masteragent.service import Platform


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="masteragent-v3-runner-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.now = 1000.0
        self.platform = self.handle()
        self.install("a", "ALPHA_ONLY_CONTEXT")
        self.platform.activate("a")
        self.actor = self.platform.bind("a", "reviewer")
        self.task = self.platform.submit("a", "reviewer", "ALPHA_ONLY_TASK")
        self.config = self.config_for("a", 1)
        self.calls = []
        self.probes = []

    def handle(self):
        return Platform(self.root / "state", external_adapters={"agent-text"}, clock=lambda: self.now)

    def install(self, installation, content):
        workspace = self.root / (installation + "-workspace")
        workspace.mkdir()
        package = self.root / (installation + "-package.json")
        package.write_text(json.dumps({
            "schema_version": 1, "platform_api": 1, "package_id": installation + "_business", "version": "1.0.0",
            "roles": [{"role_id": "reviewer", "display_name": "Same reviewer", "instructions": content, "tools": ["respond"]}],
            "knowledge": {"facts": content + "_KNOWLEDGE"},
            "tools": {"respond": {"adapter": "agent-text", "description": "Return a response from supplied text"}},
            "workflows": {},
        }), encoding="utf-8")
        self.platform.install(installation, package, workspace)
        return workspace

    def config_for(self, installation, generation, home=None):
        home = home or self.root / f"{installation}-runtime-{generation}"
        home.mkdir(exist_ok=True)
        path = self.root / f"{installation}-config-{generation}.json"
        path.write_text(json.dumps({
            "schema_version": 1, "runtime": "codex-exec", "executable": sys.executable,
            "expected_version": "Python test", "model": "synthetic-model", "adapter_id": "agent-text",
            "installation_id": installation, "role_id": "reviewer", "generation": generation,
            "codex_home": str(home), "timeout_seconds": 60, "lease_seconds": 15,
        }), encoding="utf-8")
        return load_runtime_config(path)

    def probe(self, config):
        self.probes.append(config)
        return {"compatible": True, "version": "Python test", "model_observed": None}

    def outcome(self, outcome="succeeded", **kwargs):
        return {"outcome": outcome, "output": "SYNTHETIC_FINAL", "thread_id": "thread-fixture",
                "usage": {"input_tokens": 23, "cached_input_tokens": 2, "output_tokens": 9},
                "returncode": 0, "termination_confirmed": True, **kwargs}

    def supervisor(self, argv, prompt, cwd, env, timeout_seconds, heartbeat, on_event,
                   cancelled=None, heartbeat_seconds=5):
        self.calls.append({"argv": argv, "prompt": prompt, "cwd": str(cwd), "env": env,
                           "timeout": timeout_seconds, "heartbeat_seconds": heartbeat_seconds})
        thread_id = f"thread-fixture-{len(self.calls)}"
        heartbeat()
        on_event("runtime_started", {"pid": 12345})
        on_event("runtime_thread", {"thread_id": thread_id})
        on_event("runtime_progress", {"phase": "turn_started"})
        return self.outcome(thread_id=thread_id)

    def runner(self, supervisor=None, config=None, probe=None):
        return CodexRunner(self.platform, config or self.config,
                           supervisor=supervisor or self.supervisor, probe=probe or self.probe)

    def run_task(self, supervisor=None):
        return self.runner(supervisor).run(self.actor, self.task["task_id"], "respond")

    def assert_code(self, expected, callback, *args, **kwargs):
        with self.assertRaises(PlatformError) as raised:
            callback(*args, **kwargs)
        self.assertEqual(expected, raised.exception.code)

    def test_success_persists_scoped_native_metadata_usage_and_result(self):
        returned = self.run_task()
        self.assertEqual("completed", returned["task_state"])
        self.assertEqual("SYNTHETIC_FINAL", returned["result"]["output"])
        history = self.platform.history("a")
        self.assertEqual(1, len(history["attempts"]))
        self.assertEqual(1, len(history["results"]))
        self.assertEqual(1, len(history["outbox"]))
        self.assertEqual(returned["attempt_id"], history["results"][0]["attempt_id"])
        events = history["attempt_events"]
        native = next(event for event in events if event["kind"] == "runtime_thread")
        self.assertEqual("thread-fixture-1", native["detail"]["thread_id"])
        stopped = next(event for event in events if event["kind"] == "runtime_stopped")
        self.assertEqual(self.outcome()["usage"], stopped["detail"]["usage"])
        self.assertIsNone(stopped["detail"]["model_observed"])
        self.assertEqual("synthetic-model", stopped["detail"]["model_requested"])
        self.assertTrue((self.config.codex_home / ".masteragent-runtime.json").is_file())
        self.assertEqual(1, len(self.probes))

    def test_live_supervisor_does_not_lock_submit_or_decision_answer(self):
        pending_task = self.platform.submit("a", "reviewer", "Separately waiting for a decision")
        decision = self.platform.ask_decision(self.actor, pending_task["task_id"], "Proceed?", "gate")
        started, release = threading.Event(), threading.Event()
        def waiting(*args, **kwargs):
            started.set()
            if not release.wait(5):
                raise AssertionError("Parallel coordination was blocked by external execution")
            return self.supervisor(*args, **kwargs)
        with ThreadPoolExecutor(max_workers=2) as pool:
            executing = pool.submit(self.run_task, waiting)
            try:
                self.assertTrue(started.wait(2))
                handle = self.handle()
                submitted = pool.submit(handle.submit, "a", "reviewer", "Independent new work").result(timeout=2)
                self.assertEqual("queued", submitted["state"])
                answered = pool.submit(handle.answer_decision, "a", decision["decision_id"],
                                       decision["revision"], "approve", "answer-one").result(timeout=2)
                self.assertEqual("approved", answered["state"])
            finally:
                release.set()
            self.assertEqual("completed", executing.result(timeout=2)["task_state"])

    def test_secrets_and_platform_state_do_not_enter_child_inputs_or_metadata(self):
        issued = []
        original_claim = self.platform.claim
        def capture_claim(*args, **kwargs):
            value = original_claim(*args, **kwargs)
            issued.append(value)
            return value
        sensitive = {
            "OPENAI_API_KEY": "FAKE_API_SECRET", "CODEX_THREAD_ID": "FAKE_PARENT_THREAD",
            "MASTERAGENT_ACTOR_TOKEN": self.actor.token, "MASTERAGENT_STATE_ROOT": str(self.platform.state_root),
            "CLAUDE_CONFIG_DIR": str(self.root / "unrelated-host-runtime"),
        }
        with patch.dict(os.environ, sensitive), patch.object(self.platform, "claim", side_effect=capture_claim):
            self.run_task()
        child = self.calls[0]
        emitted = json.dumps(child) + json.dumps(self.platform.history("a")["attempt_events"])
        for value in (self.actor.token, issued[0]["lease_token"], str(self.platform.state_root),
                      "FAKE_API_SECRET", "FAKE_PARENT_THREAD"):
            self.assertNotIn(value, emitted)
        self.assertNotIn(self.task["text"], json.dumps(child["argv"]))
        self.assertIn(self.task["text"], child["prompt"])
        self.assertEqual(str(self.config.codex_home), child["env"]["CODEX_HOME"])
        self.assertEqual(self.config.codex_home / "execution", Path(child["cwd"]).parent)
        self.assertNotEqual(str(self.root / "a-workspace"), child["cwd"])

    def test_uncertain_supervisor_outcomes_never_create_fake_results(self):
        for category in ("interrupted", "timeout", "cancelled", "protocol_error", "output_limit"):
            with self.subTest(category=category):
                task = self.platform.submit("a", "reviewer", "Unknown execution " + category)
                def uncertain(*args, **kwargs):
                    return self.outcome(category, returncode=9, output="UNTRUSTED_PARTIAL")
                returned = self.runner(uncertain).run(self.actor, task["task_id"], "respond")
                self.assertEqual("needs_reconcile", returned["task_state"])
                self.assertIsNone(returned["result"])
        history = self.platform.history("a")
        self.assertEqual([], history["results"])
        self.assertEqual([], history["notifications"])
        self.assertEqual([], history["outbox"])
        self.assertNotIn("UNTRUSTED_PARTIAL", json.dumps(history))
        self.assertTrue(all(attempt["state"] == "expired" for attempt in history["attempts"]))

    def test_terminal_success_without_confirmed_termination_is_uncertain(self):
        returned = self.run_task(lambda *args, **kwargs: self.outcome(termination_confirmed=False))
        self.assertEqual("needs_reconcile", returned["task_state"])
        self.assertEqual([], self.platform.results("a"))

    def test_malformed_supervisor_results_are_fenced(self):
        for malformed in (None, {}, self.outcome(returncode=True), self.outcome(termination_confirmed="yes")):
            with self.subTest(malformed=malformed):
                task = self.platform.submit("a", "reviewer", "Malformed supervisor result")
                returned = self.runner(lambda *args, **kwargs: malformed).run(self.actor, task["task_id"], "respond")
                self.assertEqual("needs_reconcile", returned["task_state"])
                self.assertIsNone(returned["result"])
        self.assertEqual([], self.platform.results("a"))

    def test_contradictory_success_with_nonzero_exit_is_uncertain(self):
        returned = self.run_task(lambda *args, **kwargs: self.outcome(returncode=9))
        self.assertEqual("needs_reconcile", returned["task_state"])
        self.assertEqual([], self.platform.results("a"))

    def test_launch_failure_is_known_terminal_failure(self):
        returned = self.run_task(lambda *args, **kwargs: self.outcome(
            "launch_failed", output=None, thread_id=None, usage=None, returncode=None))
        self.assertEqual("failed", returned["task_state"])
        self.assertEqual("error", returned["result"]["status"])
        self.assertEqual("runtime_launch_failed", returned["result"]["error"]["code"])
        self.assertEqual(1, len(self.platform.outbox("a")))

    def test_authoritative_runtime_failure_is_terminal_without_output(self):
        returned = self.run_task(lambda *args, **kwargs: self.outcome("failed", output=None, returncode=1))
        self.assertEqual("failed", returned["task_state"])
        self.assertIsNone(returned["result"]["output"])
        self.assertEqual("runtime_failed", returned["result"]["error"]["code"])

    def test_expired_heartbeat_fences_original_execution(self):
        def lose_lease(argv, prompt, cwd, env, timeout_seconds, heartbeat, on_event, **kwargs):
            self.now += 16
            heartbeat()
            raise AssertionError("Expired ownership must reject heartbeat")
        returned = self.run_task(lose_lease)
        self.assertEqual("needs_reconcile", returned["task_state"])
        self.assertEqual([], self.platform.results("a"))
        self.assertEqual("expired", self.platform.attempts("a")[0]["state"])

    def test_actor_reregistration_does_not_transfer_running_execution(self):
        def lose_owner(argv, prompt, cwd, env, timeout_seconds, heartbeat, on_event, **kwargs):
            self.platform.bind("a", "reviewer")
            on_event("runtime_progress", {"phase": "turn_started"})
            raise AssertionError("Revoked owner must reject events")
        returned = self.run_task(lose_owner)
        self.assertEqual("needs_reconcile", returned["task_state"])
        self.assertEqual([], self.platform.results("a"))
        self.assertEqual("expired", self.platform.attempts("a")[0]["state"])

    def test_unexpected_supervisor_exception_is_fenced_without_leaking_exception(self):
        def explode(*args, **kwargs):
            raise RuntimeError("FAKE_PRIVATE_ERROR_CONTENT")
        returned = self.run_task(explode)
        self.assertEqual("needs_reconcile", returned["task_state"])
        self.assertNotIn("FAKE_PRIVATE_ERROR_CONTENT", json.dumps(returned) + json.dumps(self.platform.history("a")))

    def test_explicit_cancellation_callback_reaches_supervisor(self):
        observed = []
        def cancel(argv, prompt, cwd, env, timeout_seconds, heartbeat, on_event, cancelled=None, **kwargs):
            observed.append(cancelled())
            return self.outcome("cancelled", output=None, returncode=1)
        returned = self.runner(cancel).run(self.actor, self.task["task_id"], "respond", cancelled=lambda: True)
        self.assertEqual([True], observed)
        self.assertEqual("needs_reconcile", returned["task_state"])
        self.assertEqual([], self.platform.results("a"))

    def test_durable_stop_request_reaches_owner_and_preserves_uncertain_history(self):
        def cancel(argv, prompt, cwd, env, timeout_seconds, heartbeat, on_event, cancelled=None, **kwargs):
            self.assertFalse(cancelled())
            attempt_id = self.platform.attempts("a")[0]["attempt_id"]
            independent = self.handle()
            first = independent.request_runtime_stop("a", attempt_id, "Operator requested stop")
            repeated = independent.request_runtime_stop("a", attempt_id, "Operator requested stop")
            self.assertEqual(first, repeated)
            self.assertTrue(first["stop_requested"])
            self.assertFalse(first["termination_confirmed"])
            self.assert_code("cancel_conflict", independent.request_runtime_stop,
                             "a", attempt_id, "Different stop reason")
            self.assertEqual("running", independent.attempts("a")[0]["state"])
            requests = [event for event in independent.attempts("a")[0]["events"]
                        if event["kind"] == "runtime_cancel_requested"]
            self.assertEqual(1, len(requests))
            self.assertTrue(cancelled())
            return self.outcome("cancelled", output=None, returncode=1)
        returned = self.run_task(cancel)
        self.assertEqual("needs_reconcile", returned["task_state"])
        history = self.platform.history("a")
        kinds = [event["kind"] for event in history["attempt_events"]]
        self.assertIn("runtime_cancel_requested", kinds)
        self.assertIn("runtime_uncertain", kinds)
        self.assertEqual([], history["results"])
        self.assertEqual([], history["outbox"])
        before = self.platform.history("a")
        self.assert_code("stale_attempt", self.platform.request_runtime_stop,
                         "a", returned["attempt_id"], "Operator requested stop")
        self.assertEqual(before, self.platform.history("a"))

    def test_stop_poll_requires_exact_live_owner_and_lease(self):
        attempt = self.platform.claim(self.actor, self.task["task_id"], "respond", 15)
        self.platform.record_runtime_event(self.actor, attempt["attempt_id"], attempt["lease_token"],
                                           "runtime_start_requested", {"runtime": "fixture"})
        self.assertFalse(self.platform.runtime_stop_requested(self.actor, attempt["attempt_id"], attempt["lease_token"]))
        self.platform.request_runtime_stop("a", attempt["attempt_id"], "Stop this attempt")
        self.assert_code("lease_denied", self.platform.runtime_stop_requested,
                         self.actor, attempt["attempt_id"], "forged")
        self.assert_code("invalid_actor", self.platform.runtime_stop_requested,
                         replace(self.actor, token="forged"), attempt["attempt_id"], attempt["lease_token"])
        replacement = self.platform.bind("a", "reviewer")
        self.assert_code("invalid_actor", self.platform.runtime_stop_requested,
                         self.actor, attempt["attempt_id"], attempt["lease_token"])
        self.assert_code("attempt_owner", self.platform.runtime_stop_requested,
                         replacement, attempt["attempt_id"], attempt["lease_token"])
        self.platform.fence_uncertain(self.actor, attempt["attempt_id"], attempt["lease_token"], "owner_revoked")

    def test_stop_requests_reject_unstarted_expired_and_completed_attempts(self):
        attempt = self.platform.claim(self.actor, self.task["task_id"], "respond", 15)
        self.assert_code("runtime_not_started", self.platform.request_runtime_stop,
                         "a", attempt["attempt_id"], "Stop")
        self.platform.record_runtime_event(self.actor, attempt["attempt_id"], attempt["lease_token"],
                                           "runtime_start_requested", {"runtime": "fixture"})
        self.now += 15
        self.assert_code("lease_expired", self.platform.runtime_stop_requested,
                         self.actor, attempt["attempt_id"], attempt["lease_token"])
        self.platform.expire_attempts("a")
        self.assert_code("stale_attempt", self.platform.request_runtime_stop,
                         "a", attempt["attempt_id"], "Stop")
        self.platform.reconcile_task("a", self.task["task_id"], "retry", "Verified safe", safe_to_retry=True)
        returned = self.run_task()
        self.assert_code("stale_attempt", self.platform.request_runtime_stop,
                         "a", returned["attempt_id"], "Stop")

    def test_stop_requests_are_installation_scoped_and_never_address_a_pid(self):
        self.install("b", "BETA_ONLY_CONTEXT")
        returned = self.run_task()
        self.assert_code("attempt_scope", self.platform.request_runtime_stop, "a", "12345", "Stop")
        self.platform.deactivate("a")
        self.platform.activate("b")
        self.assert_code("attempt_scope", self.platform.request_runtime_stop,
                         "b", returned["attempt_id"], "Stop another installation")
        self.assertEqual("completed", self.platform.history("a")["attempts"][0]["state"])

    def test_new_attempt_does_not_inherit_prior_stop_request(self):
        def cancel(argv, prompt, cwd, env, timeout_seconds, heartbeat, on_event, cancelled=None, **kwargs):
            old = self.platform.attempts("a")[0]
            self.platform.request_runtime_stop("a", old["attempt_id"], "Stop old execution")
            self.assertTrue(cancelled())
            return self.outcome("cancelled", output=None, returncode=1)
        self.run_task(cancel)
        self.platform.reconcile_task("a", self.task["task_id"], "retry", "Verified no effects", safe_to_retry=True)
        def second(*args, **kwargs):
            self.assertFalse(kwargs["cancelled"]())
            return self.supervisor(*args, **kwargs)
        returned = self.run_task(second)
        self.assertEqual("completed", returned["task_state"])
        attempts = self.platform.attempts("a")
        self.assertEqual(2, len(attempts))
        self.assertNotIn("runtime_cancel_requested", [event["kind"] for event in attempts[1]["events"]])

    def test_completed_task_retry_never_starts_another_process(self):
        self.run_task()
        self.assert_code("task_not_queued", self.run_task)
        self.assertEqual(1, len(self.calls))
        self.assertEqual(1, len(self.platform.attempts("a")))
        self.assertEqual(1, len(self.platform.results("a")))

    def test_probe_failure_does_not_claim_or_bind_home(self):
        def unavailable(config):
            raise PlatformError("runtime_version_mismatch", "Synthetic version mismatch")
        self.assert_code("runtime_version_mismatch", self.runner(probe=unavailable).run,
                         self.actor, self.task["task_id"], "respond")
        self.assertEqual([], self.platform.attempts("a"))
        self.assertEqual([], self.calls)
        self.assertEqual([], list(self.config.codex_home.iterdir()))

    def test_scope_binding_and_unmanaged_home_fail_before_attempt(self):
        wrong = replace(self.config, generation=2)
        self.assert_code("runtime_scope_mismatch", self.runner(config=wrong).run,
                         self.actor, self.task["task_id"], "respond")
        (self.config.codex_home / "foreign-config.txt").write_text("Synthetic unrelated home", encoding="utf-8")
        self.assert_code("unmanaged_runtime_home", self.run_task)
        self.assertEqual([], self.platform.attempts("a"))
        self.assertEqual([], self.calls)

    def test_business_switch_rebuilds_context_and_requires_distinct_home_binding(self):
        self.install("b", "BETA_ONLY_CONTEXT")
        self.run_task()
        alpha_prompt = self.calls[-1]["prompt"]
        self.platform.deactivate("a")
        self.platform.activate("b")
        beta_actor = self.platform.bind("b", "reviewer")
        beta_task = self.platform.submit("b", "reviewer", "BETA_ONLY_TASK")
        reused = self.config_for("b", 1, home=self.config.codex_home)
        self.assert_code("runtime_home_bound", self.runner(config=reused).run,
                         beta_actor, beta_task["task_id"], "respond")
        self.assertEqual([], self.platform.attempts("b"))
        beta_config = self.config_for("b", 1)
        self.runner(config=beta_config).run(beta_actor, beta_task["task_id"], "respond")
        beta_prompt = self.calls[-1]["prompt"]
        self.assertIn("ALPHA_ONLY_CONTEXT", alpha_prompt)
        self.assertNotIn("BETA_ONLY_CONTEXT", alpha_prompt)
        self.assertIn("BETA_ONLY_CONTEXT", beta_prompt)
        self.assertNotIn("ALPHA_ONLY_CONTEXT", beta_prompt)
        self.assertNotEqual(self.calls[0]["env"]["CODEX_HOME"], self.calls[1]["env"]["CODEX_HOME"])
        self.platform.deactivate("b")
        self.platform.activate("a")
        restored_actor = self.platform.bind("a", "reviewer")
        restored_task = self.platform.submit("a", "reviewer", "RESTORED_ALPHA_TASK")
        self.assert_code("runtime_scope_mismatch", self.runner().run,
                         restored_actor, restored_task["task_id"], "respond")
        reused_alpha = self.config_for("a", 2, home=self.config.codex_home)
        self.assert_code("runtime_home_bound", self.runner(config=reused_alpha).run,
                         restored_actor, restored_task["task_id"], "respond")
        restored_config = self.config_for("a", 2)
        self.runner(config=restored_config).run(restored_actor, restored_task["task_id"], "respond")
        self.assertEqual(3, len(self.calls))
        self.assertNotIn("BETA_ONLY_CONTEXT", self.calls[-1]["prompt"])
        self.assertNotEqual(self.calls[0]["env"]["CODEX_HOME"], self.calls[-1]["env"]["CODEX_HOME"])
        self.assertEqual(2, len(self.platform.history("a")["results"]))
        alpha_ids = [event["detail"]["thread_id"] for event in self.platform.history("a")["attempt_events"]
                     if event["kind"] == "runtime_thread"]
        beta_ids = [event["detail"]["thread_id"] for event in self.platform.history("b")["attempt_events"]
                    if event["kind"] == "runtime_thread"]
        self.assertEqual(["thread-fixture-1", "thread-fixture-3"], alpha_ids)
        self.assertEqual(["thread-fixture-2"], beta_ids)


if __name__ == "__main__":
    unittest.main()
