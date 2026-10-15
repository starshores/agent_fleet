"""Real compatibility functions, isolated modules, synthetic filesystem only."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]


class RecoveryFenceTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ)
        self.env.start()
        self.addCleanup(self.env.stop)
        os.environ.pop("CODEX_SESSION_ID", None)
        os.environ.pop("CODEX_THREAD_ID", None)
        self.tmp = tempfile.TemporaryDirectory(prefix="ma-codex-fences-")
        self.addCleanup(self.tmp.cleanup)
        self.bus = Path(self.tmp.name)
        self.role = "synthetic-reviewer"
        for name in (self.role, "synthetic-sender"):
            for sub in ("inbox", "processing", "archive", "done", "failed"):
                (self.bus / name / sub).mkdir(parents=True)
        self.sid = "synthetic-session"
        self.write(self.bus / self.role / "presence.json", {
            "host_kind": "codex", "host_session_id": self.sid, "mode": "listening"})
        package = types.ModuleType("bus")
        package.__path__ = [str(ROOT / "agent_bus" / "bus")]
        config = types.ModuleType("bus.config")
        config.BUS, config.METRICS = self.bus, self.bus / "metrics.jsonl"
        config._now = lambda: "2026-10-12T12:00:00"
        config.cfg = lambda name, default=None: default
        proc = types.ModuleType("bus.proc")
        proc._host_ancestor = lambda: ("codex", 123, str(self.bus), [])
        proc._host_session_id = lambda kind: "synthetic-session"
        proc._proc_scan = lambda: ({}, {})
        proc._host_pid_alive = lambda pid, kind: False
        proc._age_seconds = lambda value: 999999
        presence = types.ModuleType("bus.presence")
        presence._set_current_task = lambda *args: None
        presence._owner_live = lambda role: False
        roster = types.ModuleType("bus.roster")
        roster._list_roles = lambda: [self.role]
        roster._retired_roles = lambda: set()
        roster._is_test_role = lambda role: True
        roster._policy = lambda *args, **kwargs: {}
        lock = types.ModuleType("bus.lock")
        lock.holders = lambda: []
        replacements = {m.__name__: m for m in (package, config, proc, presence, roster, lock)}
        self.modules = patch.dict(sys.modules, replacements)
        self.modules.start()
        self.addCleanup(self.modules.stop)
        self.load("io")
        self.recovery = self.load("codex_recovery")
        self.mailbox = self.load("mailbox")
        self.selfwake = self.load("selfwake")
        self.health = self.load("health")

    def load(self, name):
        spec = importlib.util.spec_from_file_location("bus." + name, ROOT / "agent_bus/bus" / (name + ".py"))
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return module

    @staticmethod
    def write(path, value):
        path.write_text(json.dumps(value), encoding="utf-8")

    def task(self, task_id="synthetic-1", folder="inbox"):
        value = {"id": task_id, "from": "synthetic-sender", "status": "pending",
                 "task": "synthetic text", "claimed": "2000-01-01T00:00:00"}
        path = self.bus / self.role / folder / (task_id + ".json")
        self.write(path, value)
        return path, value

    def marker(self, task_id="synthetic-1"):
        return self.bus / self.role / "_codex_recovery" / (task_id + ".json")

    def reap(self, dry=False):
        args = types.SimpleNamespace(role=self.role, grace=0, ttl=1, max_requeue=2, dry_run=dry)
        with contextlib.redirect_stdout(io.StringIO()) as output:
            self.health.cmd_reap(args)
        return output.getvalue()

    def test_claim_fence_precedes_queue_movement_and_delivery(self):
        path, _ = self.task()
        original = self.mailbox.os.replace
        checked = []
        def replace(src, dst):
            if Path(dst).parent.name == "processing":
                self.assertTrue(self.marker().is_file())
                checked.append(True)
            return original(src, dst)
        with patch.object(self.mailbox.os, "replace", replace):
            claimed = self.mailbox._claim(self.role, path)
        self.assertTrue(checked)
        self.assertEqual(claimed["codex_recovery"]["replay_policy"], "manual")
        self.assertTrue(self.recovery.requeue_blocked(self.role, claimed))

    def test_marker_write_failure_never_delivers_or_moves(self):
        path, _ = self.task()
        with patch.object(self.recovery, "_write_json", side_effect=OSError("synthetic disk failure")):
            with self.assertRaises(OSError):
                self.mailbox._claim(self.role, path)
        self.assertTrue(path.is_file())
        self.assertFalse((path.parent.parent / "processing" / path.name).exists())
        self.assertFalse(path.with_name(path.name + ".lock").exists())

    def test_claim_metadata_failure_still_has_durable_fence(self):
        path, value = self.task()
        with patch.object(self.mailbox, "_write_json", side_effect=OSError("synthetic disk failure")):
            with self.assertRaisesRegex(RuntimeError, "could not be persisted"):
                self.mailbox._claim(self.role, path)
        self.assertTrue(self.marker().is_file())
        self.write(self.bus / self.role / "presence.json", {"host_kind": "claude"})
        self.assertTrue(self.recovery.requeue_blocked(self.role, value))

    def test_reap_retains_unfinished_codex_task_after_role_rebound(self):
        path, _ = self.task()
        item = self.mailbox._claim(self.role, path)
        self.write(self.bus / self.role / "presence.json", {"host_kind": "claude"})
        output = self.reap()
        self.assertIn("Codex", output)
        self.assertFalse(path.exists())
        self.assertTrue((path.parent.parent / "processing" / path.name).is_file())
        marker = json.loads(self.marker().read_text(encoding="utf-8"))
        self.assertEqual(marker["state"], "needs_reconcile")
        self.assertEqual(marker["claim_id"], item["codex_recovery"]["claim_id"])

    def test_reap_pre_upgrade_codex_claim_is_fenced_not_retried(self):
        path, _ = self.task(folder="processing")
        self.reap()
        self.assertTrue(path.is_file())
        self.assertEqual(json.loads(self.marker().read_text(encoding="utf-8"))["state"], "needs_reconcile")

    def test_reap_dry_run_does_not_create_marker(self):
        path, _ = self.task(folder="processing")
        before = path.read_bytes()
        self.reap(dry=True)
        self.assertFalse(self.marker().exists())
        self.assertEqual(path.read_bytes(), before)

    def test_orphan_requeue_rechecks_fence_even_with_stale_candidate_list(self):
        path, value = self.task(folder="processing")
        with patch.object(self.selfwake, "_orphan_claims", return_value=[path]):
            self.assertEqual(self.selfwake._requeue_orphans(self.role), 0)
        self.assertTrue(path.is_file())
        self.assertFalse(self.selfwake._is_orphan_claim(self.role, value, min_s=0))

    def test_requeued_copy_cannot_bypass_marker_or_explicit_approval(self):
        path, value = self.task()
        self.mailbox._claim(self.role, path)
        self.write(path, value)
        self.assertIsNone(self.mailbox._claim(self.role, path))
        self.assertIsNone(self.mailbox._oldest_claimable(path.parent, include_approval=True))
        other, _ = self.task("synthetic-2")
        self.assertEqual(self.mailbox._oldest_claimable(path.parent), other)

    def test_existing_receipt_can_archive_but_never_reexecute(self):
        path, value = self.task(folder="processing")
        receipt = self.bus / "synthetic-sender/done/synthetic-1.json"
        self.write(receipt, {"id": value["id"], "result": "synthetic success"})
        before = receipt.read_bytes()
        self.reap()
        self.assertFalse(path.exists())
        self.assertTrue((self.bus / self.role / "archive" / path.name).is_file())
        self.assertEqual(receipt.read_bytes(), before)

    def test_duplicate_inbox_never_overwrites_pre_upgrade_processing(self):
        old, _ = self.task(folder="processing")
        before = old.read_bytes()
        new, item = self.task()
        item["task"] = "different synthetic payload"
        self.write(new, item)
        self.assertIsNone(self.mailbox._claim(self.role, new))
        self.assertEqual(old.read_bytes(), before)
        self.assertTrue(new.is_file())
        self.assertEqual(json.loads(self.marker().read_text(encoding="utf-8"))["state"], "needs_reconcile")

    def test_fence_only_unfinished_and_deduplicates(self):
        _, one = self.task(folder="processing")
        done, two = self.task("synthetic-2", "archive")
        before = done.read_bytes()
        result = self.recovery.fence_tasks(self.role, [one, one, two], "turn_failed")
        self.assertEqual(result, [one["id"]])
        self.assertEqual(done.read_bytes(), before)
        self.assertFalse(self.marker(two["id"]).exists())

    def test_claim_mismatch_does_not_overwrite_marker(self):
        path, _ = self.task()
        item = self.mailbox._claim(self.role, path)
        before = self.marker().read_bytes()
        item["codex_recovery"]["claim_id"] = "not-the-owner"
        with self.assertRaisesRegex(RuntimeError, "claim mismatch"):
            self.recovery.fence_tasks(self.role, [item], "turn_failed")
        self.assertEqual(self.marker().read_bytes(), before)

    def test_corrupt_marker_remains_blocking_without_replacement(self):
        _, item = self.task(folder="processing")
        self.marker().parent.mkdir()
        self.marker().write_text("broken synthetic marker", encoding="utf-8")
        self.assertTrue(self.recovery.claim_blocked(self.role, item["id"]))
        with self.assertRaises(ValueError):
            self.recovery.fence_tasks(self.role, [item], "turn_failed")
        self.assertEqual(self.marker().read_text(encoding="utf-8"), "broken synthetic marker")

    def test_non_codex_claim_and_legacy_reap_keep_existing_behavior(self):
        self.write(self.bus / self.role / "presence.json", {"host_kind": "claude"})
        path, _ = self.task()
        item = self.mailbox._claim(self.role, path)
        self.assertNotIn("codex_recovery", item)
        self.assertFalse(self.marker().exists())
        self.reap()
        self.assertTrue(path.is_file())

    def test_bad_scope_and_reason_cannot_escape_synthetic_root(self):
        with self.assertRaises(ValueError):
            self.recovery.claim_blocked("..", "synthetic")
        with self.assertRaises(ValueError):
            self.recovery.fence_tasks(self.role, [{"id": "../elsewhere"}], "turn_failed")
        with self.assertRaises(ValueError):
            self.recovery.fence_tasks(self.role, [], "raw error with content")

    def test_rebound_role_rejects_explicit_and_wait_environment_claims(self):
        path, _ = self.task()
        for presence in ({"host_kind": "claude"},
                         {"host_kind": "codex", "host_session_id": "new-session"},
                         {"host_kind": "codex", "codex_session_id": self.sid}):
            self.write(self.bus / self.role / "presence.json", presence)
            with self.subTest(presence=presence):
                with self.assertRaises(RuntimeError):
                    self.mailbox._claim(self.role, path, codex_session_id=self.sid)
                with patch.dict(os.environ, {"CODEX_SESSION_ID": self.sid}):
                    with self.assertRaises(RuntimeError):
                        self.mailbox._claim(self.role, path)
                self.assertTrue(path.is_file())
                self.assertFalse(self.marker().exists())

    def test_presence_corruption_never_downgrades_wait_claim(self):
        path, _ = self.task()
        (self.bus / self.role / "presence.json").write_text("broken", encoding="utf-8")
        with patch.dict(os.environ, {"CODEX_SESSION_ID": self.sid}):
            with self.assertRaises(ValueError):
                self.mailbox._claim(self.role, path)
        self.assertTrue(path.is_file())

    def test_rebind_after_marker_write_cannot_move_or_deliver(self):
        path, _ = self.task()
        original = self.mailbox.protect_claim
        def change_binding(*args, **kwargs):
            result = original(*args, **kwargs)
            self.write(self.bus / self.role / "presence.json", {"host_kind": "claude"})
            return result
        with patch.object(self.mailbox, "protect_claim", side_effect=change_binding):
            with self.assertRaises(RuntimeError):
                self.mailbox._claim(self.role, path, codex_session_id=self.sid)
        self.assertTrue(path.is_file())
        self.assertTrue(self.marker().exists())
        self.assertTrue(self.recovery.claim_blocked(self.role, path.stem))

    def test_delivery_is_durable_and_cannot_repeat(self):
        path, _ = self.task()
        item = self.mailbox._claim(self.role, path)
        self.recovery.validate_delivery(self.role, self.sid, [item])
        saved = json.loads(self.marker().read_text(encoding="utf-8"))
        self.assertEqual(saved["state"], "dispatching")
        self.assertEqual(saved["host_session_id"], self.sid)
        with self.assertRaises(RuntimeError):
            self.recovery.validate_delivery(self.role, self.sid, [item])

    def test_rebound_session_cannot_dispatch_queued_task(self):
        path, _ = self.task()
        item = self.mailbox._claim(self.role, path)
        self.write(self.bus / self.role / "presence.json", {
            "host_kind": "codex", "host_session_id": "new-session"})
        with self.assertRaises(RuntimeError):
            self.recovery.validate_delivery(self.role, self.sid, [item])
        with self.assertRaises(RuntimeError):
            self.recovery.validate_delivery(self.role, "new-session", [item])

    def test_unfenced_processing_cannot_be_dispatched(self):
        path, item = self.task(folder="processing")
        with self.assertRaises((OSError, RuntimeError)):
            self.recovery.validate_delivery(self.role, self.sid, [item])
        self.assertTrue(path.is_file())
        self.assertFalse(self.marker().exists())

    def test_finished_processing_cannot_be_dispatched_again(self):
        path, _ = self.task()
        item = self.mailbox._claim(self.role, path)
        processing = path.parent.parent / "processing" / path.name
        processing.replace(path.parent.parent / "archive" / path.name)
        with self.assertRaises(RuntimeError):
            self.recovery.validate_delivery(self.role, self.sid, [item])

    def test_delivery_write_failure_prevents_execution_and_keeps_claim(self):
        path, _ = self.task()
        item = self.mailbox._claim(self.role, path)
        before = self.marker().read_bytes()
        with patch.object(self.recovery, "_write_json", side_effect=OSError("synthetic failure")):
            with self.assertRaises(OSError):
                self.recovery.validate_delivery(self.role, self.sid, [item])
        self.assertEqual(self.marker().read_bytes(), before)

    def test_partial_batch_delivery_cannot_replay_reserved_tasks(self):
        path, _ = self.task()
        first = self.mailbox._claim(self.role, path)
        _, second = self.task("synthetic-2", "processing")
        with self.assertRaises((OSError, RuntimeError)):
            self.recovery.validate_delivery(self.role, self.sid, [first, second])
        self.assertEqual(json.loads(self.marker().read_text(encoding="utf-8"))["state"], "dispatching")
        self.recovery.fence_tasks(self.role, [first, second], "batch_delivery_failed")
        self.assertTrue(self.marker(second["id"]).exists())
        with self.assertRaises(RuntimeError):
            self.recovery.validate_delivery(self.role, self.sid, [first])

    def bridge_with_fake_transport(self, item, finish_task=False):
        spec = importlib.util.spec_from_file_location("synthetic_bridge_real_fences", ROOT / ".codex/codex_wait_bridge.py")
        bridge = importlib.util.module_from_spec(spec)
        with patch.object(sys, "path", list(sys.path)):
            spec.loader.exec_module(bridge)
        app = bridge.AppServer.__new__(bridge.AppServer)
        app.thread_id, app.role = self.sid, self.role
        app._init_turn_tracking()
        app._write_turn_state = lambda *args: None
        def rpc(method, params, timeout):
            self.assertEqual(json.loads(self.marker().read_text(encoding="utf-8"))["state"], "dispatching")
            if finish_task:
                path = self.bus / self.role / "processing" / (item["id"] + ".json")
                saved = json.loads(path.read_text(encoding="utf-8"))
                saved.update(status="ok", result="synthetic receipt")
                self.write(path, saved)
                path.replace(self.bus / self.role / "archive" / path.name)
            app._handle_notification({"method": "turn/completed", "params": {
                "threadId": self.sid, "turn": {"id": "synthetic-turn", "status": "completed"}}})
            return {"turn": {"id": "synthetic-turn", "status": "inProgress"}}
        app.rpc = rpc
        return bridge, app

    def test_real_bridge_success_without_receipt_fences_and_stops(self):
        path, _ = self.task()
        item = self.mailbox._claim(self.role, path)
        bridge, app = self.bridge_with_fake_transport(item)
        with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(bridge.TurnFailure):
            bridge._dispatch_batch(app, self.role, self.sid, "synthetic-cwd", [item])
        self.assertEqual(json.loads(self.marker().read_text(encoding="utf-8"))["state"], "needs_reconcile")

    def test_real_bridge_matching_archive_completes_without_replaying(self):
        path, _ = self.task()
        item = self.mailbox._claim(self.role, path)
        bridge, app = self.bridge_with_fake_transport(item, finish_task=True)
        with contextlib.redirect_stdout(io.StringIO()):
            bridge._dispatch_batch(app, self.role, self.sid, "synthetic-cwd", [item])
        self.assertFalse(app._dispatch_inflight)
        self.assertTrue(self.recovery.claim_blocked(self.role, item["id"]))

    def test_disappeared_processing_is_not_evidence_of_completion(self):
        path, _ = self.task()
        item = self.mailbox._claim(self.role, path)
        processing = path.parent.parent / "processing" / path.name
        processing.replace(path.parent.parent / "archive" / path.name)
        self.assertEqual(self.recovery.reconcile_unfinished(self.role, [item]), [item["id"]])
        self.assertEqual(json.loads(self.marker().read_text(encoding="utf-8"))["state"], "needs_reconcile")


if __name__ == "__main__":
    unittest.main()
