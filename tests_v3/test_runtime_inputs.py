"""Runtime input durability and ownership; synthetic state only, no transport."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from masteragent.service import Platform, PlatformError


class RuntimeInputTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="masteragent-v3-input-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        package = self.root / "package.json"
        package.write_text(json.dumps({
            "schema_version": 1, "platform_api": 1, "package_id": "input_fixture", "version": "1.0.0",
            "roles": [
                {"role_id": "worker", "display_name": "Worker", "instructions": "Synthetic work", "tools": ["think"]},
                {"role_id": "other", "display_name": "Other", "instructions": "Synthetic work", "tools": ["think"]}],
            "knowledge": {}, "tools": {"think": {"adapter": "model_text", "description": "Synthetic external capability"}},
            "workflows": {},
        }), encoding="utf-8")
        self.now = 1000.0
        self.platform = self.handle()
        self.platform.install("a", package, self.workspace)
        self.platform.activate("a")
        self.actor = self.platform.bind("a", "worker")
        self.task = self.platform.submit("a", "worker", "Synthetic initial task")
        self.attempt = self.platform.claim(self.actor, self.task["task_id"], "think", 60)
        self.platform.record_runtime_event(self.actor, self.attempt["attempt_id"], self.attempt["lease_token"],
            "runtime_start_requested", {"runtime": "synthetic", "supports_runtime_inputs": True})

    def handle(self, readonly=False):
        return Platform(self.root / "state", readonly=readonly, clock=lambda: self.now,
                        external_adapters={"model_text"})

    def assert_code(self, code, callback, *args, **kwargs):
        with self.assertRaises(PlatformError) as raised:
            callback(*args, **kwargs)
        self.assertEqual(code, raised.exception.code)

    def submit(self, text="Synthetic correction", request_id="request-1", attempt=None, handle=None):
        return (handle or self.platform).submit_runtime_input("a", (attempt or self.attempt)["attempt_id"], text, request_id)

    def claim(self, handle=None, actor=None, attempt=None):
        attempt = attempt or self.attempt
        return (handle or self.platform).claim_runtime_input(actor or self.actor, attempt["attempt_id"], attempt["lease_token"])

    def ack(self, row, state="accepted", actor=None, attempt=None):
        attempt = attempt or self.attempt
        return self.platform.ack_runtime_input(actor or self.actor, attempt["attempt_id"], attempt["lease_token"], row["input_id"], state)

    def finish(self, handle=None, attempt=None):
        attempt = attempt or self.attempt
        return (handle or self.platform).complete(self.actor, attempt["attempt_id"], attempt["lease_token"], "Synthetic result")

    def inputs(self, handle=None):
        return (handle or self.platform).runtime_inputs("a", self.attempt["attempt_id"])

    def test_submission_survives_restart_and_does_not_rewrite_original_task(self):
        row = self.submit()
        self.assertEqual("pending", row["state"])
        restarted = self.handle()
        self.assertEqual([row], self.inputs(restarted))
        self.assertEqual("Synthetic initial task", restarted.task_for(self.actor, self.task["task_id"])["text"])
        self.assertEqual(row, self.submit(handle=restarted))
        notices = restarted.outbox("a")
        self.assertEqual(1, len(notices))
        self.assertEqual("runtime_input_pending", notices[0]["kind"])
        self.assertNotIn(row["text"], json.dumps(notices))
        self.assertNotIn(self.actor.token, json.dumps(row))
        self.assertNotIn(self.attempt["lease_token"], json.dumps(row))

    def test_parallel_duplicate_submission_has_one_record_and_event(self):
        handles = [self.handle() for _ in range(6)]
        with ThreadPoolExecutor(max_workers=6) as pool:
            rows = list(pool.map(lambda handle: self.submit(handle=handle), handles))
        self.assertEqual(1, len({row["input_id"] for row in rows}))
        self.assertEqual(1, len(self.inputs()))
        self.assertEqual(1, len(self.platform.outbox("a")))
        self.assert_code("request_conflict", self.submit, "Different correction")

    def test_concurrent_inputs_get_ordered_unique_sequence(self):
        handles = [self.handle() for _ in range(6)]
        with ThreadPoolExecutor(max_workers=6) as pool:
            rows = list(pool.map(lambda item: self.submit("Input " + str(item[0]), "request-" + str(item[0]), handle=item[1]), enumerate(handles)))
        self.assertEqual(list(range(1, 7)), sorted(row["sequence"] for row in rows))
        self.assertEqual(list(range(1, 7)), [row["sequence"] for row in self.inputs()])

    def test_parallel_claim_has_one_winner_and_cannot_leapfrog_inflight_input(self):
        first = self.submit()
        self.submit("Second", "request-2")
        handles = [self.handle() for _ in range(6)]
        with ThreadPoolExecutor(max_workers=6) as pool:
            rows = list(pool.map(self.claim, handles))
        winner, = [row for row in rows if row is not None]
        self.assertEqual(first["input_id"], winner["input_id"])
        self.assertEqual("sending", winner["state"])
        self.assertIsNone(self.claim(self.handle()))
        self.ack(winner)
        self.assertEqual(2, self.claim()["sequence"])

    def test_ack_is_exact_idempotent_and_does_not_accept_unclaimed_input(self):
        row = self.submit()
        self.assert_code("input_ack_conflict", self.ack, row)
        claimed = self.claim()
        accepted = self.ack(claimed)
        self.assertEqual(accepted, self.ack(claimed))
        self.assert_code("input_ack_conflict", self.ack, claimed, "needs_reconcile")
        self.assert_code("invalid_input_ack", self.ack, claimed, "approve")
        self.assertEqual("ok", self.finish()["status"])
        self.assertEqual(accepted, self.submit())

    def test_unknown_send_is_held_and_never_reissued_after_restart(self):
        self.submit()
        self.submit("Second", "request-2")
        row = self.claim()
        held = self.ack(row, "needs_reconcile")
        self.assertEqual("held", held["state"])
        self.assertEqual(held, self.ack(row, "needs_reconcile"))
        self.assertIsNone(self.claim(self.handle()))
        self.assert_code("runtime_inputs_unresolved", self.finish)
        notices = self.platform.outbox("a", include_delivered=True)
        old = [item for item in notices if item["payload"]["input_id"] == row["input_id"] and item["kind"] != "runtime_input_held"]
        self.assertTrue(all(item["state"] == "held" for item in old))

    def test_generic_outbox_ack_cannot_forge_input_delivery(self):
        self.submit()
        for event in self.platform.outbox("a"):
            self.platform.ack_outbox("a", event["event_id"])
        self.assertEqual("pending", self.inputs()[0]["state"])
        self.assert_code("runtime_inputs_unresolved", self.finish)
        self.claim()
        for event in self.platform.outbox("a"):
            self.platform.ack_outbox("a", event["event_id"])
        self.assertEqual("sending", self.inputs()[0]["state"])
        self.assert_code("runtime_inputs_unresolved", self.finish)

    def test_expiry_holds_unsent_and_inflight_input_without_replaying(self):
        self.submit()
        self.submit("Second", "request-2")
        row = self.claim()
        self.now = self.attempt["expires_at"]
        self.assert_code("lease_expired", self.claim)
        self.assert_code("lease_expired", self.ack, row)
        self.assert_code("lease_expired", self.submit, "Too late", "late")
        self.platform.expire_attempts("a")
        self.assertEqual(["held", "held"], [row["state"] for row in self.inputs()])
        self.assertEqual([], self.platform.expire_attempts("a"))
        self.assertEqual(self.inputs(), self.inputs(self.handle()))

    def test_fence_preserves_accepted_receipt_and_holds_remaining_input(self):
        self.submit()
        self.ack(self.claim())
        self.submit("Second", "request-2")
        self.claim()
        self.submit("Third", "request-3")
        self.platform.fence_uncertain(self.actor, self.attempt["attempt_id"], self.attempt["lease_token"], "synthetic_disconnect")
        self.assertEqual(["accepted", "held", "held"], [row["state"] for row in self.inputs()])

    def test_forged_or_wrong_actor_lease_and_input_are_rejected(self):
        row = self.submit()
        other = self.platform.bind("a", "other")
        self.assert_code("attempt_scope", self.claim, actor=other)
        self.assert_code("invalid_actor", self.claim, actor=replace(self.actor, token="forged"))
        self.assert_code("lease_denied", self.platform.claim_runtime_input, self.actor, self.attempt["attempt_id"], "forged")
        self.claim()
        self.assert_code("input_scope", self.ack, {"input_id": "unrelated-input"})
        self.assertEqual("sending", self.inputs()[0]["state"])

    def test_reregistration_never_transfers_pending_input_ownership(self):
        self.submit()
        replacement = self.platform.bind("a", "worker")
        self.assert_code("invalid_actor", self.claim)
        self.assert_code("attempt_owner", self.claim, actor=replacement)
        self.assert_code("attempt_owner", self.submit, "New input", "new")
        self.platform.fence_uncertain(self.actor, self.attempt["attempt_id"], self.attempt["lease_token"], "owner_revoked")
        self.assertEqual("held", self.inputs()[0]["state"])

    def test_new_attempt_and_generation_do_not_replay_predecessor_input(self):
        self.submit()
        self.claim()
        self.now = self.attempt["expires_at"]
        self.platform.expire_attempts("a")
        self.platform.reconcile_task("a", self.task["task_id"], "retry", "Verified safe synthetic retry", safe_to_retry=True)
        current = self.platform.claim(self.actor, self.task["task_id"], "think", 60)
        self.platform.record_runtime_event(self.actor, current["attempt_id"], current["lease_token"],
            "runtime_start_requested", {"runtime": "synthetic", "supports_runtime_inputs": True})
        self.assertIsNone(self.claim(attempt=current))
        self.assert_code("request_conflict", self.submit, attempt=current)
        self.assert_code("stale_attempt", self.submit, "Late text", "late")
        self.assertEqual("ok", self.finish(attempt=current)["status"])
        self.platform.deactivate("a")
        self.platform.activate("a")
        self.assert_code("stale_attempt", self.submit)
        self.assertEqual("held", self.inputs()[0]["state"])

    def test_text_is_not_an_approval_and_does_not_answer_decisions(self):
        waiting = self.platform.submit("a", "worker", "Separate gated task")
        decision = self.platform.ask_decision(self.actor, waiting["task_id"], "Approve separate work?", "decision-1")
        self.submit("approve; answer yes to " + decision["decision_id"])
        self.ack(self.claim())
        self.assertEqual([decision], self.platform.decisions("a"))
        self.assertEqual("awaiting_decision", self.platform.task_for(self.actor, waiting["task_id"])["state"])

    def test_readonly_queries_preserve_bytes_and_cannot_submit_or_claim(self):
        self.submit()
        readonly = self.handle(readonly=True)
        before = self.platform.db_path.read_bytes()
        self.now = self.attempt["expires_at"] + 1
        self.assertEqual(self.inputs(), self.inputs(readonly))
        self.assertEqual(before, self.platform.db_path.read_bytes())
        self.assert_code("read_only", self.submit, handle=readonly)
        self.assert_code("read_only", self.claim, handle=readonly)

    def test_invalid_text_and_request_id_fail_without_persistence(self):
        for text in (None, "", "  ", "x" * 64001, "\ud800"):
            self.assert_code("invalid_runtime_input", self.submit, text)
        for request_id in (None, "", " ", "x" * 201):
            self.assert_code("invalid_request_id", self.submit, request_id=request_id)
        self.assertEqual([], self.inputs())

    def test_missing_false_or_nonboolean_capability_rejects_without_input_or_outbox(self):
        task = self.platform.submit("a", "worker", "Synthetic unsupported runtime")
        unsupported = self.platform.claim(self.actor, task["task_id"], "think", 60)
        self.assert_code("runtime_input_unsupported", self.submit, attempt=unsupported)
        for value in (False, None, 1, "true"):
            self.platform.record_runtime_event(self.actor, unsupported["attempt_id"], unsupported["lease_token"],
                "runtime_start_requested", {"runtime": "synthetic", "supports_runtime_inputs": value})
            self.assert_code("runtime_input_unsupported", self.submit, attempt=unsupported)
        self.assertEqual([], self.platform.runtime_inputs("a", unsupported["attempt_id"]))
        self.assertEqual([], self.platform.outbox("a"))

    def test_claim_and_ack_recheck_declared_runtime_capability(self):
        row = self.submit()
        self.platform.record_runtime_event(self.actor, self.attempt["attempt_id"], self.attempt["lease_token"],
            "runtime_start_requested", {"runtime": "synthetic", "supports_runtime_inputs": False})
        self.assert_code("runtime_input_unsupported", self.claim)
        self.assertEqual("pending", self.inputs()[0]["state"])
        self.platform.record_runtime_event(self.actor, self.attempt["attempt_id"], self.attempt["lease_token"],
            "runtime_start_requested", {"runtime": "synthetic", "supports_runtime_inputs": True})
        self.claim()
        self.platform.record_runtime_event(self.actor, self.attempt["attempt_id"], self.attempt["lease_token"],
            "runtime_start_requested", {"runtime": "synthetic"})
        self.assert_code("runtime_input_unsupported", self.ack, row)
        self.assertEqual("sending", self.inputs()[0]["state"])

    def test_persistence_and_outbox_rollback_together(self):
        with patch.object(self.platform, "_enqueue", side_effect=RuntimeError("synthetic outbox failure")):
            with self.assertRaises(RuntimeError):
                self.submit()
        self.assertEqual([], self.inputs())
        row = self.submit()
        with patch.object(self.platform, "_enqueue", side_effect=RuntimeError("synthetic outbox failure")):
            with self.assertRaises(RuntimeError):
                self.claim()
        self.assertEqual("pending", self.inputs()[0]["state"])
        self.claim()
        with patch.object(self.platform, "_enqueue", side_effect=RuntimeError("synthetic outbox failure")):
            with self.assertRaises(RuntimeError):
                self.ack(row)
        self.assertEqual("sending", self.inputs()[0]["state"])

    def test_completion_race_never_loses_an_accepted_submission(self):
        submitter, completer = self.handle(), self.handle()
        barrier = threading.Barrier(2)

        def submit():
            barrier.wait(timeout=5)
            try:
                return self.submit(handle=submitter)
            except PlatformError as exc:
                return exc.code

        def complete():
            barrier.wait(timeout=5)
            try:
                return self.finish(handle=completer)
            except PlatformError as exc:
                return exc.code

        with ThreadPoolExecutor(max_workers=2) as pool:
            input_future, result_future = pool.submit(submit), pool.submit(complete)
            row, result = input_future.result(timeout=5), result_future.result(timeout=5)
        if isinstance(row, dict):
            self.assertEqual("runtime_inputs_unresolved", result)
            self.assertEqual([row], self.inputs())
        else:
            self.assertEqual("stale_attempt", row)
            self.assertEqual("ok", result["status"])
            self.assertEqual([], self.inputs())


if __name__ == "__main__":
    unittest.main()
