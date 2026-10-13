"""External-controller ownership uses isolated fake business and no model calls."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import threading
import unittest

from masteragent.service import Platform, PlatformError


class RuntimeOwnershipTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="masteragent-v3-runtime-owner-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.package = self.root / "package.json"
        self.package.write_text(json.dumps({
            "schema_version": 1, "platform_api": 1, "package_id": "runtime_fixture", "version": "1.0.0",
            "roles": [
                {"role_id": "worker", "display_name": "Worker", "instructions": "Synthetic work", "tools": ["think", "echo"]},
                {"role_id": "other", "display_name": "Other", "instructions": "Synthetic work", "tools": []},
            ],
            "knowledge": {}, "tools": {
                "think": {"adapter": "model_text", "description": "Deployment-provided external capability"},
                "echo": {"adapter": "echo", "description": "Pure text fixture"},
            }, "workflows": {},
        }), encoding="utf-8")
        self.now = 1000.0
        self.platform = self.handle()
        self.platform.install("a", self.package, self.workspace)
        self.platform.activate("a")
        self.actor = self.platform.bind("a", "worker")
        self.task = self.platform.submit("a", "worker", "Synthetic external text task")

    def handle(self):
        return Platform(self.root / "state", clock=lambda: self.now,
                        external_adapters={"model_text"})

    def assert_code(self, expected, callback, *args, **kwargs):
        with self.assertRaises(PlatformError) as raised:
            callback(*args, **kwargs)
        self.assertEqual(expected, raised.exception.code)

    def claim(self, ttl=60):
        return self.platform.claim(self.actor, self.task["task_id"], "think", ttl)

    def event(self, attempt, kind="runtime_started", detail=None, actor=None):
        return self.platform.record_runtime_event(
            actor or self.actor, attempt["attempt_id"], attempt["lease_token"],
            kind, {"runtime": "fixture"} if detail is None else detail)

    def fence(self, attempt, reason="timeout", actor=None):
        return self.platform.fence_uncertain(
            actor or self.actor, attempt["attempt_id"], attempt["lease_token"], reason)

    def test_external_registration_is_explicit_copied_and_distinct(self):
        for value in ("model_text", ["model_text"], {"invalid name"}, {3}, {"echo"}):
            self.assert_code("invalid_adapters", Platform, self.root / "unused", external_adapters=value)
        self.assertFalse((self.root / "unused").exists())
        names = {"model_text"}
        handle = Platform(self.root / "state", external_adapters=names)
        names.clear()
        self.assertEqual(frozenset({"model_text"}), handle.external_adapters)
        self.assertNotIn("model_text", handle.adapters)
        missing = Platform(self.root / "missing-state")
        self.assert_code("adapter_unavailable", missing.install, "a", self.package, self.workspace)

    def test_sync_paths_never_execute_external_adapters(self):
        self.assert_code("external_adapter_required", self.platform.run, "a", self.task["task_id"], "think")
        self.assert_code("external_adapter_required", self.platform.run_as, self.actor, self.task["task_id"], "think")
        self.assertEqual([], self.platform.attempts("a"))
        attempt = self.claim()
        self.platform.complete(self.actor, attempt["attempt_id"], attempt["lease_token"], "external output")
        self.assert_code("external_adapter_required", self.platform.run, "a", self.task["task_id"], "think")
        sync_task = self.platform.submit("a", "worker", "text stays synchronous")
        self.assertEqual("text stays synchronous", self.platform.run_as(self.actor, sync_task["task_id"], "echo")["output"])

    def test_caller_waiting_outside_claim_allows_other_coordinator_writes(self):
        claimed, release = threading.Event(), threading.Event()
        def external_controller():
            attempt = self.claim()
            claimed.set()
            if not release.wait(5):
                raise AssertionError("Coordinator failed to write while external work was waiting")
            return self.platform.complete(self.actor, attempt["attempt_id"], attempt["lease_token"], "external output")
        with ThreadPoolExecutor(max_workers=2) as pool:
            running = pool.submit(external_controller)
            try:
                self.assertTrue(claimed.wait(2))
                other = pool.submit(self.handle().submit, "a", "worker", "parallel coordination")
                self.assertEqual("queued", other.result(timeout=2)["state"])
            finally:
                release.set()
            self.assertEqual("external output", running.result(timeout=2)["output"])

    def test_current_owner_records_normalized_metadata_without_credentials(self):
        attempt = self.claim()
        detail = {"runtime": "fixture", "nested": {"flags": [True, None, 2, 3.5]}}
        for kind in ("runtime_start_requested", "runtime_started", "runtime_thread", "runtime_progress", "runtime_stopped"):
            view = self.event(attempt, kind, detail)
            self.assertEqual("running", view["state"])
            self.assertNotIn("lease_token", view)
        detail["nested"]["flags"].append("after recording")
        records = self.platform.attempts("a")[0]["events"]
        self.assertEqual(6, len(records))
        self.assertEqual([True, None, 2, 3.5], records[-1]["detail"]["nested"]["flags"])
        rendered = json.dumps(records)
        self.assertNotIn(self.actor.token, rendered)
        self.assertNotIn(attempt["lease_token"], rendered)
        self.assertEqual([], self.platform.results("a"))

    def test_runtime_metadata_rejects_bad_kind_shape_size_and_secrets(self):
        attempt = self.claim()
        nested = {}
        for _ in range(10):
            nested = {"nested": nested}
        for kind, detail in (
                ("completed", {}), ("runtime_started", []),
                ("runtime_started", {"set": {1}}), ("runtime_started", {1: "key"}),
                ("runtime_started", {"tuple": (1,)}), ("runtime_started", {"nan": float("nan")}),
                ("runtime_started", {"large": "a" * 8192}), ("runtime_started", nested),
                ("runtime_started", {"token": self.actor.token}),
                ("runtime_started", {"token": "prefix " + attempt["lease_token"]}),
                ("runtime_started", {"invalid_unicode": "\ud800"})):
            self.assert_code("invalid_runtime_event", self.event, attempt, kind, detail)
        self.assertEqual(1, len(self.platform.attempts("a")[0]["events"]))

    def test_metadata_requires_live_actor_and_unexpired_lease(self):
        attempt = self.claim(5)
        self.now += 5
        self.assert_code("lease_expired", self.event, attempt)
        self.now -= 5
        self.platform.bind("a", "worker")
        self.assert_code("invalid_actor", self.event, attempt)
        self.assertEqual(1, len(self.platform.attempts("a")[0]["events"]))

    def test_forged_owner_cannot_publish_or_fence(self):
        attempt = self.claim()
        other = self.platform.bind("a", "other")
        self.assert_code("attempt_scope", self.event, attempt, actor=other)
        for actor in (other, replace(self.actor, token="forged"),
                      replace(self.actor, generation=True), replace(self.actor, installation_id="b")):
            self.assert_code("attempt_owner", self.fence, attempt, actor=actor)
        fake_lease = {**attempt, "lease_token": "forged"}
        self.assert_code("lease_denied", self.event, fake_lease)
        self.assert_code("attempt_owner", self.fence, fake_lease)
        self.assertEqual("running", self.platform.attempts("a")[0]["state"])

    def test_fence_atomically_marks_uncertainty_without_result_or_notification(self):
        attempt = self.claim()
        view = self.fence(attempt)
        self.assertEqual("expired", view["state"])
        history = self.platform.history("a")
        self.assertEqual("needs_reconcile", history["tasks"][0]["state"])
        self.assertEqual({"reason": "timeout", "effects": "unknown"}, history["attempt_events"][-1]["detail"])
        for key in ("results", "notifications", "outbox"):
            self.assertEqual([], history[key])
        self.assert_code("unfinished_work", self.platform.deactivate, "a")
        self.assert_code("stale_attempt", self.event, attempt)
        self.assert_code("stale_attempt", self.fence, attempt)
        self.assertEqual(history, self.platform.history("a"))
        self.assertEqual("cancelled", self.platform.reconcile_task("a", self.task["task_id"], "cancel", "Manually checked unknown work")["state"])

    def test_original_owner_can_fence_after_revocation_and_deadline(self):
        attempt = self.claim(1)
        replacement = self.platform.bind("a", "worker")
        self.now += 2
        self.assert_code("attempt_owner", self.fence, attempt, actor=replacement)
        self.assertEqual("expired", self.fence(attempt, "ownership_lost")["state"])
        self.assertEqual("needs_reconcile", self.platform.history("a")["tasks"][0]["state"])

    def test_old_fence_cannot_alter_new_execution_or_final_result(self):
        old = self.claim(1)
        self.now += 2
        self.platform.expire_attempts("a")
        before = self.platform.history("a")
        self.assert_code("stale_attempt", self.fence, old)
        self.assertEqual(before, self.platform.history("a"))
        self.platform.reconcile_task("a", self.task["task_id"], "retry", "Verified safe to repeat", safe_to_retry=True)
        current = self.claim()
        before = self.platform.history("a")
        self.assert_code("stale_attempt", self.fence, old)
        self.assert_code("stale_attempt", self.event, old)
        self.assertEqual(before, self.platform.history("a"))
        self.platform.complete(self.actor, current["attempt_id"], current["lease_token"], "current result")
        before = self.platform.history("a")
        self.assert_code("stale_attempt", self.fence, old)
        self.assert_code("stale_attempt", self.fence, current)
        self.assert_code("stale_attempt", self.event, current)
        self.assertEqual(before, self.platform.history("a"))

    def test_fence_reason_is_a_bounded_category(self):
        attempt = self.claim()
        for reason in (None, "", "Timeout", "contains space", "secret/path", "a" * 101, True):
            self.assert_code("invalid_runtime_reason", self.fence, attempt, reason)
        self.assertEqual("expired", self.fence(attempt, "runtime.timeout_v1")["state"])

    def test_fence_storage_failure_rolls_back_all_state(self):
        attempt = self.claim()
        before = self.platform.history("a")
        original = self.platform._attempt_event
        def fail(*args, **kwargs):
            raise RuntimeError("Synthetic persistence failure")
        self.platform._attempt_event = fail
        try:
            with self.assertRaises(RuntimeError):
                self.fence(attempt)
        finally:
            self.platform._attempt_event = original
        self.assertEqual(before, self.platform.history("a"))

    def test_completion_and_fence_race_has_one_authoritative_outcome(self):
        attempt = self.claim()
        barrier = threading.Barrier(2)
        def finish():
            handle = self.handle()
            barrier.wait()
            try:
                return handle.complete(self.actor, attempt["attempt_id"], attempt["lease_token"], "done")
            except PlatformError as exc:
                return exc.code
        def fence():
            handle = self.handle()
            barrier.wait()
            try:
                return handle.fence_uncertain(self.actor, attempt["attempt_id"], attempt["lease_token"], "timeout")
            except PlatformError as exc:
                return exc.code
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(finish), pool.submit(fence)]
            outcomes = [future.result(timeout=5) for future in futures]
        self.assertEqual(1, sum(isinstance(outcome, dict) for outcome in outcomes))
        self.assertEqual(1, outcomes.count("stale_attempt"))
        history = self.platform.history("a")
        state = history["tasks"][0]["state"]
        self.assertIn(state, ("completed", "needs_reconcile"))
        self.assertEqual(1 if state == "completed" else 0, len(history["results"]))


if __name__ == "__main__":
    unittest.main()
