"""Synthetic persistent-member ownership, publication and queue tests."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch

from masteragent.errors import PlatformError
from masteragent.service import Platform


class MemberTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="ma3-members-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.now = 1000.0
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.package = self.root / "package.json"
        self.package.write_text(json.dumps({
            "schema_version": 1, "platform_api": 1, "package_id": "synthetic", "version": "1.0.0",
            "roles": [{"role_id": role, "display_name": role, "instructions": "synthetic",
                       "tools": ["think", "local"]} for role in ("worker", "other")],
            "knowledge": {}, "tools": {
                "think": {"adapter": "model_text", "description": "synthetic external capability"},
                "local": {"adapter": "echo", "description": "synthetic local capability"}}, "workflows": {}
        }), encoding="utf-8")
        self.platform = self.handle()
        self.platform.install("a", self.package, self.workspace)
        self.platform.activate("a")
        self.actor = self.platform.bind("a", "worker")

    def handle(self, readonly=False):
        return Platform(self.root / "state", readonly=readonly, clock=lambda: self.now,
                        external_adapters={"model_text"})

    def assert_code(self, code, method, *args, **kwargs):
        with self.assertRaises(PlatformError) as caught:
            method(*args, **kwargs)
        self.assertEqual(code, caught.exception.code)

    def start(self, platform=None, actor=None, ttl=60):
        return (platform or self.platform).start_member(actor or self.actor, "a" * 64, "think", ttl)

    def claim(self, member, platform=None, actor=None):
        return (platform or self.platform).claim_member_task(actor or self.actor, member["member_id"], member["member_token"], 60)

    def status(self, member):
        return self.platform.member_status(self.actor, member["member_id"], member["member_token"])

    def complete(self, attempt, platform=None):
        return (platform or self.platform).complete(self.actor, attempt["attempt_id"], attempt["lease_token"], "synthetic result")

    def running(self):
        member = self.start()
        task = self.platform.submit("a", "worker", "synthetic task")
        attempt = self.claim(member)
        self.platform.record_runtime_event(self.actor, attempt["attempt_id"], attempt["lease_token"],
                                           "runtime_start_requested", {"supports_runtime_inputs": True})
        return member, task, attempt

    def test_explicit_actor_and_digest_are_required_and_secrets_not_exposed(self):
        self.assert_code("invalid_deployment_digest", self.platform.start_member, self.actor, "configuration", "think")
        self.assert_code("invalid_actor", self.start, actor=replace(self.actor, token="forged"))
        self.assert_code("tool_denied", self.platform.start_member, self.actor, "a" * 64, "missing")
        self.assert_code("member_adapter", self.platform.start_member, self.actor, "a" * 64, "local")
        member = self.start()
        view = self.status(member)
        self.assertEqual(view["state"], "ready")
        encoded = json.dumps([view, self.platform.members("a"), self.platform.outbox("a")])
        for secret in (self.actor.token, member["member_token"], "owner_hash", "lease_hash"):
            self.assertNotIn(secret, encoded)
        self.assertEqual(len(member["member_token"]) > 20, True)

    def test_concurrent_start_has_exactly_one_instance(self):
        handles = [self.handle() for _ in range(5)]
        def start(handle):
            try:
                return self.start(handle)["member_id"]
            except PlatformError as exc:
                return exc.code
        with ThreadPoolExecutor(max_workers=5) as pool:
            values = list(pool.map(start, handles))
        self.assertEqual(values.count("member_conflict"), 4)
        self.assertEqual(len(self.platform.members("a")), 1)

    def test_tasks_are_claimed_in_order_and_membership_commits_atomically(self):
        member = self.start()
        tasks = [self.platform.submit("a", "worker", "task " + str(i)) for i in range(3)]
        first = self.claim(member)
        self.assertEqual(first["task_id"], tasks[0]["task_id"])
        self.assertEqual(first["member_id"], member["member_id"])
        self.assertEqual(self.status(member)["state"], "busy")
        self.assert_code("member_busy", self.claim, member)
        self.complete(first)
        second = self.claim(member)
        self.assertEqual(second["task_id"], tasks[1]["task_id"])
        self.complete(second)
        third = self.claim(member)
        self.complete(third)
        self.assertIsNone(self.claim(member))
        self.assertEqual(self.status(member)["state"], "ready")
        with self.platform._db() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM attempt_members").fetchone()[0], 3)

    def test_concurrent_claim_is_serial_even_for_different_queued_tasks(self):
        member = self.start()
        for i in range(4):
            self.platform.submit("a", "worker", "task " + str(i))
        handles = [self.handle() for _ in range(4)]
        def claim(handle):
            try:
                return self.claim(member, handle)["attempt_id"]
            except PlatformError as exc:
                return exc.code
        with ThreadPoolExecutor(max_workers=4) as pool:
            values = list(pool.map(claim, handles))
        self.assertEqual(values.count("member_busy"), 3)
        self.assertEqual(len(self.platform.attempts("a")), 1)

    def test_ordinary_claim_and_sync_execution_cannot_bypass_member(self):
        self.start()
        task = self.platform.submit("a", "worker", "synthetic")
        self.assert_code("member_exclusive", self.platform.claim, self.actor, task["task_id"], "think")
        self.assert_code("member_exclusive", self.platform.run_as, self.actor, task["task_id"], "local")
        self.assert_code("member_exclusive", self.platform.run, "a", task["task_id"], "local")
        self.assertEqual(self.platform.attempts("a"), [])

    def test_existing_standalone_attempt_prevents_member_takeover(self):
        task = self.platform.submit("a", "worker", "synthetic")
        attempt = self.platform.claim(self.actor, task["task_id"], "think")
        self.assert_code("member_busy", self.start)
        self.complete(attempt)
        self.assertEqual(self.start()["state"], "ready")

    def test_waiting_requires_owner_and_idle_attempt_state(self):
        member = self.start()
        self.assert_code("member_owner", self.platform.member_waiting, self.actor, member["member_id"], "forged", True)
        self.assert_code("invalid_member_state", self.platform.member_waiting, self.actor, member["member_id"], member["member_token"], 1)
        self.assertEqual(self.platform.member_waiting(self.actor, member["member_id"], member["member_token"], True)["state"], "waiting")
        before = self.platform.db_path.read_bytes()
        self.platform.member_waiting(self.actor, member["member_id"], member["member_token"], True)
        self.assertIsNone(self.claim(member))
        self.assertEqual(before, self.platform.db_path.read_bytes())
        self.platform.submit("a", "worker", "synthetic")
        self.claim(member)
        self.assert_code("member_busy", self.platform.member_waiting, self.actor, member["member_id"], member["member_token"], True)

    def test_heartbeats_do_not_emit_delivery_noise(self):
        member = self.start()
        before = self.platform.outbox("a")
        self.now += 10
        updated = self.platform.renew_member(self.actor, member["member_id"], member["member_token"], 90)
        self.assertEqual(updated["expires_at"], 1100)
        self.assertEqual(before, self.platform.outbox("a"))

    def test_stop_fences_publication_inputs_and_future_work_in_one_transaction(self):
        member, task, attempt = self.running()
        input_row = self.platform.submit_runtime_input("a", attempt["attempt_id"], "synthetic input", "one")
        self.platform.claim_runtime_input(self.actor, attempt["attempt_id"], attempt["lease_token"])
        stopped = self.platform.request_member_stop("a", member["member_id"], "synthetic stop")
        self.assertEqual(stopped["state"], "stopping")
        self.assertEqual(self.platform.task_for(self.actor, task["task_id"])["state"], "needs_reconcile")
        self.assertEqual(self.platform.runtime_inputs("a")[0]["state"], "held")
        self.assert_code("stale_attempt", self.complete, attempt)
        self.assert_code("member_inactive", self.claim, member)
        self.assert_code("member_inactive", self.platform.submit_runtime_input,
                         "a", attempt["attempt_id"], "new input", "two")
        self.assertEqual(self.platform.results("a"), [])
        self.assertEqual(self.platform.request_member_stop("a", member["member_id"], "synthetic stop"), stopped)
        self.assert_code("member_stop_conflict", self.platform.request_member_stop,
                         "a", member["member_id"], "different")
        renewed = self.platform.renew_member(self.actor, member["member_id"], member["member_token"], 100)
        self.assertEqual(renewed["expires_at"], member["expires_at"])
        finished = self.platform.finish_member(self.actor, member["member_id"], member["member_token"], True)
        self.assertEqual(finished["state"], "stopped")
        self.assertFalse(finished["online"])

    def test_stop_and_complete_race_never_publish_after_stop_wins(self):
        for index in range(5):
            member, task, attempt = self.running()
            barrier = threading.Barrier(2)
            def complete():
                barrier.wait()
                try:
                    return self.complete(attempt, self.handle())["status"]
                except PlatformError as exc:
                    return exc.code
            def stop():
                barrier.wait()
                return self.handle().request_member_stop("a", member["member_id"], "stop")
            with ThreadPoolExecutor(max_workers=2) as pool:
                a, b = pool.submit(complete), pool.submit(stop)
                outcome, stop_view = a.result(), b.result()
            state = self.platform.task_for(self.actor, task["task_id"])["state"]
            self.assertIn((outcome, state), (("ok", "completed"), ("stale_attempt", "needs_reconcile")))
            self.platform.finish_member(self.actor, member["member_id"], member["member_token"], True)

    def test_member_expiry_blocks_all_publication_before_expiration_sweep(self):
        member, task, attempt = self.running()
        self.platform.submit_runtime_input("a", attempt["attempt_id"], "input", "one")
        self.now = member["expires_at"]
        self.assert_code("member_inactive", self.complete, attempt)
        self.assert_code("member_inactive", self.platform.renew, self.actor, attempt["attempt_id"], attempt["lease_token"], 60)
        self.assert_code("member_inactive", self.platform.claim_runtime_input, self.actor, attempt["attempt_id"], attempt["lease_token"])
        self.assert_code("member_inactive", self.platform.record_runtime_event, self.actor, attempt["attempt_id"], attempt["lease_token"], "runtime_progress", {})
        before = self.platform.db_path.read_bytes()
        views = self.handle(readonly=True).members("a")
        self.assertEqual(views[0]["state"], "lost")
        self.assertFalse(views[0]["online"])
        self.assertEqual(before, self.platform.db_path.read_bytes())
        self.assertEqual(self.platform.expire_members("a")[0]["state"], "lost")
        self.assertEqual(self.platform.runtime_inputs("a")[0]["state"], "held")
        self.assertEqual(self.platform.task_for(self.actor, task["task_id"])["state"], "needs_reconcile")
        self.assertEqual(self.platform.expire_members("a"), [])

    def test_new_instance_fences_expired_predecessor_without_replaying_its_task(self):
        old, task, attempt = self.running()
        next_task = self.platform.submit("a", "worker", "next synthetic task")
        self.now = old["expires_at"]
        current = self.start()
        self.assertNotEqual(current["member_id"], old["member_id"])
        self.assertEqual(self.status(old)["state"], "lost")
        issued = self.claim(current)
        self.assertEqual(issued["task_id"], next_task["task_id"])
        self.assertEqual(self.platform.task_for(self.actor, task["task_id"])["state"], "needs_reconcile")
        self.assertEqual(len(self.platform.attempts("a", task["task_id"])), 1)
        self.assert_code("stale_attempt", self.complete, attempt)

    def test_re_registration_revokes_member_and_replacement_fences_old_attempt(self):
        member, task, attempt = self.running()
        new_actor = self.platform.bind("a", "worker")
        self.assertEqual(self.status(member)["state"], "lost")
        new_member = self.start(actor=new_actor)
        self.assertEqual(new_member["state"], "ready")
        self.assert_code("invalid_actor", self.complete, attempt)
        self.assertEqual(self.platform.task_for(new_actor, task["task_id"])["state"], "needs_reconcile")

    def test_role_and_member_lease_scope_cannot_be_forged(self):
        member = self.start()
        other = self.platform.bind("a", "other")
        for method in (self.platform.member_status, self.platform.renew_member,
                       self.platform.claim_member_task):
            self.assert_code("member_owner", method, other, member["member_id"], member["member_token"])
            self.assert_code("member_owner", method, self.actor, member["member_id"], "forged")
        self.assertEqual(self.start(actor=other)["role_id"], "other")

    def test_finish_needs_confirmed_shutdown_and_no_running_attempt(self):
        member, task, attempt = self.running()
        value = self.platform.finish_member(self.actor, member["member_id"], member["member_token"], True)
        self.assertEqual(value["state"], "lost")
        self.assertEqual(self.platform.task_for(self.actor, task["task_id"])["state"], "needs_reconcile")
        another = self.start()
        value = self.platform.finish_member(self.actor, another["member_id"], another["member_token"], False)
        self.assertEqual(value["state"], "lost")

    def test_completed_result_keeps_idempotent_receipt_after_member_stops(self):
        member, task, attempt = self.running()
        result = self.complete(attempt)
        self.platform.finish_member(self.actor, member["member_id"], member["member_token"], True)
        self.assertEqual(self.complete(attempt), result)

    def test_failed_stop_transaction_rolls_back_all_fencing(self):
        member, task, attempt = self.running()
        self.platform.submit_runtime_input("a", attempt["attempt_id"], "input", "one")
        original = self.platform._attempt_event
        def fail(db, attempt, kind, detail):
            if kind == "member_fenced":
                raise RuntimeError("synthetic transaction failure")
            return original(db, attempt, kind, detail)
        with patch.object(self.platform, "_attempt_event", side_effect=fail):
            with self.assertRaisesRegex(RuntimeError, "transaction failure"):
                self.platform.request_member_stop("a", member["member_id"], "stop")
        self.assertEqual(self.status(member)["state"], "busy")
        self.assertEqual(self.platform.task_for(self.actor, task["task_id"])["state"], "running")
        self.assertEqual(self.platform.runtime_inputs("a")[0]["state"], "pending")

    def test_claim_transaction_rollback_cannot_leave_unattached_running_attempt(self):
        member = self.start()
        task = self.platform.submit("a", "worker", "synthetic")
        with self.platform._db(write=True) as db:
            db.execute("""CREATE TRIGGER synthetic_claim_failure BEFORE INSERT ON attempt_members
                BEGIN SELECT RAISE(ABORT, 'synthetic membership insert failure'); END""")
        with self.assertRaisesRegex(sqlite3.IntegrityError, "membership insert failure"):
            self.claim(member)
        self.assertEqual(self.status(member)["state"], "ready")
        self.assertEqual(self.platform.task_for(self.actor, task["task_id"])["state"], "queued")
        self.assertEqual(self.platform.attempts("a"), [])

    def test_attempt_expiry_does_not_replay_work_while_member_lease_remains_live(self):
        member = self.start(ttl=300)
        first = self.platform.submit("a", "worker", "first synthetic task")
        next_task = self.platform.submit("a", "worker", "next synthetic task")
        attempt = self.claim(member)
        self.now += 61
        self.assertTrue(self.status(member)["online"])
        self.assert_code("lease_expired", self.complete, attempt)
        self.assert_code("member_busy", self.claim, member)
        self.platform.expire_attempts("a")
        issued = self.claim(member)
        self.assertEqual(issued["task_id"], next_task["task_id"])
        self.assertEqual(self.platform.task_for(self.actor, first["task_id"])["state"], "needs_reconcile")
        self.assertEqual(len(self.platform.attempts("a", first["task_id"])), 1)

    def test_inactive_generation_is_lost_and_new_activation_never_reuses_member(self):
        member = self.start()
        old_task = self.platform.submit("a", "worker", "old synthetic task")
        self.platform.deactivate("a")
        self.assertEqual(self.status(member)["state"], "lost")
        self.platform.activate("a")
        new_actor = self.platform.bind("a", "worker")
        new_member = self.start(actor=new_actor)
        self.assertEqual(new_member["generation"], 2)
        self.assertIsNone(self.claim(new_member, actor=new_actor))
        self.assertEqual(self.platform.history("a")["tasks"][0]["task_id"], old_task["task_id"])


if __name__ == "__main__":
    unittest.main()
