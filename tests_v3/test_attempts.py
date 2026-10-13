"""Fenced attempt ownership and explicit recovery on synthetic isolated state."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from masteragent.service import Platform, PlatformError


class AttemptTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="masteragent-v3-attempts-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.package = self.root / "package.json"
        self.package.write_text(json.dumps({
            "schema_version": 1, "platform_api": 1, "package_id": "synthetic", "version": "1.0.0",
            "roles": [
                {"role_id": "worker", "display_name": "Worker", "instructions": "Synthetic work", "tools": ["echo"]},
                {"role_id": "other", "display_name": "Other", "instructions": "Synthetic work", "tools": []},
            ],
            "knowledge": {}, "tools": {"echo": {"adapter": "echo", "description": "Echo text"}},
            "workflows": {},
        }), encoding="utf-8")
        self.now = 1000.0
        self.platform = Platform(self.root / "state", clock=lambda: self.now)
        self.platform.install("a", self.package, self.workspace)
        self.platform.activate("a")
        self.actor = self.platform.bind("a", "worker")
        self.task = self.platform.submit("a", "worker", "synthetic work")

    def assert_code(self, code, callback, *args, **kwargs):
        with self.assertRaises(PlatformError) as error:
            callback(*args, **kwargs)
        self.assertEqual(code, error.exception.code)

    def claim(self, ttl=60):
        return self.platform.claim(self.actor, self.task["task_id"], "echo", ttl)

    def finish(self, attempt, output="done", **kwargs):
        return self.platform.complete(self.actor, attempt["attempt_id"], attempt["lease_token"], output, **kwargs)

    def test_separate_handles_race_and_only_one_claim_survives(self):
        handles = [Platform(self.root / "state", clock=lambda: self.now) for _ in range(8)]
        def claim(handle):
            try:
                return handle.claim(self.actor, self.task["task_id"], "echo")
            except PlatformError as exc:
                return exc.code
        with ThreadPoolExecutor(max_workers=8) as pool:
            outcomes = list(pool.map(claim, handles))
        self.assertEqual(1, sum(isinstance(outcome, dict) for outcome in outcomes))
        self.assertEqual(7, outcomes.count("task_not_queued"))
        self.assertEqual(1, len(self.platform.attempts("a")))
        self.assertEqual("running", self.platform.task_for(self.actor, self.task["task_id"])["state"])

    def test_secrets_only_issued_once_never_persisted_or_exposed_by_reads(self):
        issued = self.claim()
        public = json.dumps(self.platform.attempts("a")) + json.dumps(self.platform.history("a"))
        for secret in (self.actor.token, issued["lease_token"], hashlib.sha256(issued["lease_token"].encode()).hexdigest()):
            self.assertNotIn(secret, public)
        for key in ("owner_hash", "lease_hash", "lease_token"):
            self.assertNotIn(key, public)
        raw = self.platform.db_path.read_bytes()
        self.assertNotIn(issued["lease_token"].encode(), raw)
        self.assertNotIn(self.actor.token.encode(), raw)
        renewed = self.platform.renew(self.actor, issued["attempt_id"], issued["lease_token"])
        self.assertNotIn("lease_token", renewed)

    def test_renewal_and_completion_at_exact_deadline_are_denied(self):
        attempt = self.claim(10)
        self.now += 5
        renewed = self.platform.renew(self.actor, attempt["attempt_id"], attempt["lease_token"], 20)
        self.assertEqual(1025, renewed["expires_at"])
        self.now = 1025
        self.assert_code("lease_expired", self.platform.renew, self.actor, attempt["attempt_id"], attempt["lease_token"])
        self.assert_code("lease_expired", self.finish, attempt)
        self.assertEqual([], self.platform.results("a"))
        self.assertEqual("running", self.platform.history("a")["tasks"][0]["state"])
        self.assertEqual("completion_rejected", self.platform.attempts("a")[0]["events"][-1]["kind"])

    def test_invalid_lease_bounds_do_not_claim_or_renew(self):
        for ttl in (True, False, 0, -1, float("nan"), float("inf"), 86401, "60"):
            with self.subTest(ttl=ttl):
                self.assert_code("invalid_lease", self.claim, ttl)
        self.assertEqual([], self.platform.attempts("a"))
        attempt = self.claim(86400)
        self.assert_code("invalid_lease", self.platform.renew, self.actor, attempt["attempt_id"], attempt["lease_token"], float("nan"))
        self.assertEqual(attempt["expires_at"], self.platform.attempts("a")[0]["expires_at"])

    def test_worker_and_lease_scope_cannot_be_forged(self):
        attempt = self.claim()
        other = self.platform.bind("a", "other")
        self.assert_code("attempt_scope", self.platform.complete, other, attempt["attempt_id"], attempt["lease_token"], "wrong")
        self.assert_code("invalid_actor", self.platform.complete, replace(self.actor, generation=2), attempt["attempt_id"], attempt["lease_token"], "wrong")
        self.assert_code("lease_denied", self.platform.complete, self.actor, attempt["attempt_id"], "fake", "wrong")
        self.assertEqual([], self.platform.results("a"))
        self.assertEqual("done", self.finish(attempt)["output"])

    def test_reregister_revokes_old_actor_but_does_not_transfer_attempt_ownership(self):
        attempt = self.claim()
        replacement = self.platform.bind("a", "worker")
        self.assert_code("invalid_actor", self.finish, attempt)
        self.assert_code("attempt_owner", self.platform.complete, replacement, attempt["attempt_id"], attempt["lease_token"], "takeover")
        self.assertEqual([], self.platform.results("a"))
        self.now += 61
        self.platform.expire_attempts("a")
        self.platform.reconcile_task("a", self.task["task_id"], "retry", "Verified no effects", safe_to_retry=True)
        fresh = self.platform.claim(replacement, self.task["task_id"], "echo")
        result = self.platform.complete(replacement, fresh["attempt_id"], fresh["lease_token"], "new")
        self.assertEqual("new", result["output"])

    def test_expiry_is_observable_idempotent_and_never_automatically_requeues(self):
        self.claim(1)
        self.now += 1
        expired = self.platform.expire_attempts("a")
        self.assertEqual(["needs_reconcile"], [task["state"] for task in expired])
        self.assertEqual([], self.platform.expire_attempts("a"))
        self.assert_code("task_not_queued", self.claim)
        self.assert_code("unfinished_work", self.platform.deactivate, "a")
        self.assert_code("unsafe_retry", self.platform.reconcile_task, "a", self.task["task_id"], "retry", "Try again")
        self.assert_code("unsafe_retry", self.platform.reconcile_task, "a", self.task["task_id"], "retry", "Try again", safe_to_retry=1)
        self.assert_code("invalid_reason", self.platform.reconcile_task, "a", self.task["task_id"], "cancel", " ")
        self.assertEqual([], self.platform.results("a"))

    def test_late_old_completion_cannot_overwrite_new_attempt_or_result(self):
        old = self.claim(1)
        self.now += 2
        self.platform.expire_attempts("a")
        self.platform.reconcile_task("a", self.task["task_id"], "retry", "Confirmed no external effects", safe_to_retry=True)
        new = self.claim()
        self.assertEqual(2, new["ordinal"])
        self.assert_code("stale_attempt", self.finish, old, "late")
        self.assertEqual("running", self.platform.task_for(self.actor, self.task["task_id"])["state"])
        completed = self.finish(new, "authoritative")
        before = {key: self.platform.history("a")[key] for key in ("tasks", "results", "notifications", "outbox")}
        self.assert_code("stale_attempt", self.finish, old, "later")
        after = {key: self.platform.history("a")[key] for key in before}
        self.assertEqual(before, after)
        self.assertEqual(new["attempt_id"], completed["attempt_id"])
        events = self.platform.attempts("a", self.task["task_id"])[0]["events"]
        self.assertEqual(2, sum(event["kind"] == "completion_rejected" for event in events))

    def test_success_retry_is_idempotent_and_conflicting_payload_is_audited(self):
        attempt = self.claim()
        first = self.finish(attempt)
        self.now += 100
        self.assertEqual(first, self.finish(attempt))
        self.assert_code("completion_conflict", self.finish, attempt, "different")
        history = self.platform.history("a")
        self.assertEqual(1, len(history["results"]))
        self.assertEqual(1, len(history["notifications"]))
        self.assertEqual(1, len(history["outbox"]))
        self.assertEqual("completion_rejected", history["attempt_events"][-1]["kind"])

    def test_error_completion_is_terminal_and_does_not_retry(self):
        attempt = self.claim()
        error = {"code": "synthetic_failure", "message": "The synthetic worker failed."}
        first = self.finish(attempt, None, error=error)
        self.assertEqual("error", first["status"])
        self.assertEqual(error, first["error"])
        self.assertEqual(first, self.finish(attempt, None, error=error))
        self.assert_code("completion_conflict", self.finish, attempt)
        self.assert_code("task_not_queued", self.claim)
        self.assertEqual("failed", self.platform.history("a")["tasks"][0]["state"])

    def test_result_notification_and_outbox_roll_back_together_on_failure(self):
        attempt = self.claim()
        original = self.platform._enqueue
        def fail(*args, **kwargs):
            raise PlatformError("synthetic_outbox_failure", "Synthetic transaction fault")
        self.platform._enqueue = fail
        self.assert_code("synthetic_outbox_failure", self.finish, attempt)
        history = self.platform.history("a")
        self.assertEqual([], history["results"])
        self.assertEqual([], history["notifications"])
        self.assertEqual([], history["outbox"])
        self.assertEqual("running", history["tasks"][0]["state"])
        self.assertEqual("running", history["attempts"][0]["state"])
        self.platform._enqueue = original
        self.assertEqual("done", self.finish(attempt)["output"])

    def test_cancel_reconciles_uncertainty_preserves_audit_and_permits_switch(self):
        attempt = self.claim(1)
        self.now += 2
        self.platform.expire_attempts("a")
        cancelled = self.platform.reconcile_task("a", self.task["task_id"], "cancel", "Operator abandoned uncertain work")
        self.assertEqual("cancelled", cancelled["state"])
        self.assertIsNotNone(cancelled["completed_at"])
        self.assert_code("stale_attempt", self.finish, attempt)
        self.platform.deactivate("a")
        self.platform.uninstall("a")
        self.assertEqual("expired", self.platform.attempts("a")[0]["state"])
        self.assertEqual("cancelled", self.platform.history("a")["tasks"][0]["state"])

    def test_explicit_continuation_preserves_origin_generation_and_records_reason(self):
        self.platform.deactivate("a")
        self.platform.activate("a")
        current = self.platform.bind("a", "worker")
        self.assert_code("stale_task", self.platform.claim, current, self.task["task_id"], "echo")
        resumed = self.platform.resume_task("a", self.task["task_id"], "Continue retained work")
        self.assertEqual(1, resumed["generation"])
        self.assertEqual(2, resumed["dispatch_generation"])
        self.assertEqual(resumed, self.platform.resume_task("a", self.task["task_id"], "Continue retained work"))
        self.assert_code("continuation_conflict", self.platform.resume_task, "a", self.task["task_id"], "Different continuation")
        issued = self.platform.claim(current, self.task["task_id"], "echo")
        result = self.platform.complete(current, issued["attempt_id"], issued["lease_token"], "continued")
        self.assertEqual(2, result["generation"])
        self.assertEqual("continued", self.platform.results("a")[0]["output"])
        history = self.platform.history("a")
        self.assertEqual(1, len(history["continuations"]))
        self.assertEqual((1, 2), (history["continuations"][0]["from_generation"], history["continuations"][0]["to_generation"]))
        self.assertEqual(1, history["tasks"][0]["generation"])

    def test_terminal_work_cannot_be_resumed_after_restore(self):
        self.finish(self.claim())
        self.platform.deactivate("a")
        self.platform.activate("a")
        self.assert_code("task_not_resumable", self.platform.resume_task, "a", self.task["task_id"], "Do not replay")
        self.assertEqual([], self.platform.results("a"))
        self.assertEqual(1, len(self.platform.history("a")["results"]))

    def test_unknown_old_owner_requires_exact_migration_evidence_before_reconciliation(self):
        # Model the explicit schema-1 import evidence without manufacturing an
        # execution attempt or a worker identity for historical running work.
        with self.platform._db(write=True) as db:
            db.execute("UPDATE tasks SET state='needs_reconcile' WHERE task_id=?", (self.task["task_id"],))
        self.assert_code("reconciliation_required", self.platform.reconcile_task,
                         "a", self.task["task_id"], "retry", "Verified old effect", safe_to_retry=True)
        with self.platform._db(write=True) as db:
            db.execute("INSERT INTO attempt_events VALUES (?,NULL,?,?,?,?)",
                       ("synthetic-migration", "a", "migration_uncertain", json.dumps({
                           "task_id": self.task["task_id"], "source": "schema1_unknown_owner"}), "synthetic-time"))
        retried = self.platform.reconcile_task("a", self.task["task_id"], "retry",
                                              "Verified imported work has no effects", safe_to_retry=True)
        self.assertEqual("queued", retried["state"])
        self.assertEqual([], self.platform.attempts("a"))
        events = self.platform.history("a")["attempt_events"]
        self.assertEqual("reconciled", events[-1]["kind"])
        self.assertIsNone(events[-1]["attempt_id"])
        self.assertEqual("schema1_unknown_owner", events[-1]["detail"]["source"])
        self.assertEqual("done", self.finish(self.claim())["output"])

    def test_readonly_attempt_history_has_no_database_mutations(self):
        self.finish(self.claim())
        before = self.platform.db_path.read_bytes()
        readonly = Platform(self.root / "state", readonly=True)
        self.assertEqual("completed", readonly.attempts("a")[0]["state"])
        readonly.history("a")
        self.assert_code("read_only", readonly.expire_attempts, "a")
        self.assertEqual(before, self.platform.db_path.read_bytes())


if __name__ == "__main__":
    unittest.main()
