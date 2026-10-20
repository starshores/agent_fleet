"""Independent restart/fault checks against real synthetic SQLite storage domains.

These checks inject lost responses *after* successful durable operations rather
than only returning fake errors before the operation. No native model is started.
"""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch

from masteragent.errors import PlatformError
from masteragent.receipt_store import ReceiptStore
from masteragent.service import Platform
from masteragent.source_store import SourceStore


class TransportAdversarialTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="ma-dev4-adversarial-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.now = 1000.0
        workspace = self.root / "workspace"
        workspace.mkdir()
        package = self.root / "package.json"
        package.write_text(json.dumps({
            "schema_version": 1, "platform_api": 1,
            "package_id": "synthetic", "version": "1.0.0",
            "roles": [{"role_id": "worker", "display_name": "Worker",
                       "instructions": "Synthetic only", "tools": ["echo"]}],
            "knowledge": {}, "tools": {"echo": {"adapter": "echo", "description": "Synthetic"}},
            "workflows": {},
        }), encoding="utf-8")
        self.platform = self.handle()
        self.platform.install("synthetic", package, workspace)
        self.platform.activate("synthetic")
        self.actor = self.platform.bind("synthetic", "worker")
        self.source = SourceStore(self.root / "source", "synthetic.source", initialize=True)
        self.receiver = ReceiptStore(self.root / "receiver", "synthetic.destination", "inbox", initialize=True)
        self.platform.configure_route("route", "synthetic.source", "v3", "synthetic", "worker", 1,
                                      "synthetic.destination", "inbox")
        self.platform.bind_route_endpoints("route", 1, self.source, self.receiver)
        self.text = "Synthetic complete text 🙂 e\u0301\r\n尾段 SENTINEL"

    def handle(self):
        return Platform(self.root / "state", clock=lambda: self.now)

    def worker(self, *, platform=None, source=None, receiver=None, owner="transporter"):
        from masteragent.transport import TransportWorker
        return TransportWorker(platform or self.platform, "route", owner, source or self.source,
                               receiver or self.receiver)

    def reopened_worker(self):
        return self.worker(
            platform=self.handle(),
            source=SourceStore(self.source.root, "synthetic.source"),
            receiver=ReceiptStore(self.receiver.root, "synthetic.destination", "inbox"),
            owner="restarted-transporter",
        )

    def counts(self):
        with closing(sqlite3.connect(self.platform.db_path)) as db:
            return {name: db.execute("SELECT count(*) FROM " + name).fetchone()[0]
                    for name in ("tasks", "source_admissions", "attempts", "results", "result_deliveries")}

    def assert_recovered_once(self, source_id="one"):
        # A restarted process does not possess the old in-memory bearer lease.
        self.now += 61
        self.reopened_worker().transfer_source(source_id)
        record = self.source.get(source_id)
        admission = self.platform.source_admission("route", source_id)
        self.assertTrue(record["source_confirmed"])
        self.assertTrue(record["transport_completed"])
        self.assertTrue(admission["source_confirmed"])
        self.assertEqual(record["admission_id"], admission["admission_id"])
        self.assertEqual(self.text, admission["text"])
        self.assertEqual({"tasks": 1, "source_admissions": 1, "attempts": 0, "results": 0,
                          "result_deliveries": 0}, self.counts())
        return admission

    def inject_lost_response(self, endpoint, name):
        original = getattr(endpoint, name)
        def commit_then_lose_response(*args, **kwargs):
            original(*args, **kwargs)
            raise OSError("Synthetic response loss after durable commit")
        return patch.object(endpoint, name, side_effect=commit_then_lose_response)

    def test_admission_commit_response_loss_restarts_without_second_task(self):
        self.source.publish("one", self.text)
        with self.inject_lost_response(self.platform, "admit_source"):
            with self.assertRaises(OSError):
                self.worker().transfer_source("one")
        admitted = self.platform.source_admission("route", "one")
        self.assertIsNotNone(admitted)
        self.assertFalse(self.source.get("one")["source_confirmed"])
        self.assertEqual(admitted["task_id"], self.assert_recovered_once()["task_id"])

    def test_native_confirmation_commit_response_loss_remains_discoverable(self):
        self.source.publish("one", self.text)
        with self.inject_lost_response(self.source, "confirm"):
            with self.assertRaises(OSError):
                self.worker().transfer_source("one")
        record = self.source.get("one")
        self.assertTrue(record["source_confirmed"])
        self.assertFalse(record["transport_completed"])
        self.assertEqual([record], self.source.pending())
        self.assert_recovered_once()

    def test_platform_confirmation_commit_response_loss_restarts_same_admission(self):
        self.source.publish("one", self.text)
        with self.inject_lost_response(self.platform, "confirm_source"):
            with self.assertRaises(OSError):
                self.worker().transfer_source("one")
        original = self.platform.source_admission("route", "one")
        self.assertTrue(original["source_confirmed"])
        self.assertFalse(self.source.get("one")["transport_completed"])
        self.assertEqual(original["admission_id"], self.assert_recovered_once()["admission_id"])

    def test_transport_completion_commit_response_loss_is_harmless_on_explicit_replay(self):
        self.source.publish("one", self.text)
        with self.inject_lost_response(self.source, "complete"):
            with self.assertRaises(OSError):
                self.worker().transfer_source("one")
        self.assertTrue(self.source.get("one")["transport_completed"])
        self.assertEqual([], self.source.pending())
        self.assert_recovered_once()

    def test_old_generation_confirmation_recovery_does_not_create_new_task(self):
        self.source.publish("one", self.text)
        with self.inject_lost_response(self.source, "confirm"):
            with self.assertRaises(OSError):
                self.worker().transfer_source("one")
        original = self.platform.source_admission("route", "one")
        self.platform.deactivate("synthetic")
        current = self.platform.activate("synthetic")
        self.assertEqual(2, current["generation"])
        self.platform.configure_route("route", "synthetic.source", "v3", "synthetic", "worker", 2,
                                      "synthetic.destination", "inbox", expected_epoch=1)
        recovered = self.assert_recovered_once()
        self.assertEqual(original["task_id"], recovered["task_id"])
        self.assertEqual(1, recovered["generation"])
        new_actor = self.platform.bind("synthetic", "worker")
        with self.assertRaises(PlatformError):
            self.platform.claim(new_actor, recovered["task_id"], "echo")
        self.assertEqual(0, self.counts()["attempts"])

    def test_recovery_of_prior_admission_survives_admission_freeze(self):
        self.source.publish("one", self.text)
        with self.inject_lost_response(self.platform, "admit_source"):
            with self.assertRaises(OSError):
                self.worker().transfer_source("one")
        self.platform.set_route_controls("route", 1, admission_open=False)
        self.assert_recovered_once()
        self.source.publish("new", "New work remains frozen")
        with self.assertRaises(PlatformError):
            self.reopened_worker().transfer_source("new")
        self.assertFalse(self.source.get("new")["source_confirmed"])
        self.assertEqual(1, self.counts()["tasks"])

    def test_two_transporters_never_duplicate_typed_source_task(self):
        self.source.publish(7, self.text)
        def transfer(owner):
            try:
                return self.worker(platform=self.handle(), owner=owner).transfer_source(7)
            except PlatformError as exc:
                return exc.code
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(transfer, ("first", "second")))
        self.assertEqual(1, self.counts()["tasks"])
        self.assertEqual(1, self.counts()["source_admissions"])
        self.assertEqual(0, self.counts()["attempts"])
        self.assert_recovered_once(source_id=7)
        self.assertIsNone(self.platform.source_admission("route", "7"))

    def completed_result(self):
        self.source.publish("one", self.text)
        self.worker().transfer_source("one")
        admission = self.platform.source_admission("route", "one")
        attempt = self.platform.claim(self.actor, admission["task_id"], "echo")
        self.platform.complete(self.actor, attempt["attempt_id"], attempt["lease_token"], self.text)
        return self.platform.deliveries()[0]

    def test_result_commit_response_loss_recovers_without_second_receive_or_execution(self):
        delivery = self.completed_result()
        original = self.receiver.receive
        calls = []
        def commit_then_lose_response(envelope):
            calls.append(envelope["delivery_id"])
            original(envelope)
            raise OSError("Synthetic destination response loss")
        with patch.object(self.receiver, "receive", side_effect=commit_then_lose_response):
            self.worker().send_result(delivery["delivery_id"])
        self.assertEqual("held", self.platform.delivery_status(delivery["delivery_id"])["state"])
        with patch.object(self.receiver, "receive", side_effect=AssertionError("Must reconcile, not resend")):
            self.platform.reconcile_delivery(delivery["delivery_id"], self.receiver)
        recovered = self.platform.delivery_status(delivery["delivery_id"])
        self.assertEqual("received", recovered["state"])
        self.assertEqual(1, recovered["send_count"])
        self.assertEqual([delivery["delivery_id"]], calls)
        self.assertEqual(1, self.counts()["attempts"])
        self.assertEqual(self.text, self.receiver.read_text(self.platform.delivery_envelope(delivery["delivery_id"])))

    def test_held_authoritative_absent_does_not_become_automatic_resend(self):
        delivery = self.completed_result()
        with patch.object(self.receiver, "receive", side_effect=OSError("Synthetic failure before commit")):
            self.worker().send_result(delivery["delivery_id"])
        held = self.platform.delivery_status(delivery["delivery_id"])
        self.assertEqual("held", held["state"])
        self.now += 61
        with patch.object(self.receiver, "receive", side_effect=AssertionError("Absent needs explicit retry authority")):
            self.worker(owner="restarted").run_once()
        unchanged = self.platform.delivery_status(delivery["delivery_id"])
        self.assertEqual("held", unchanged["state"])
        self.assertEqual(1, unchanged["send_count"])
        self.assertEqual(1, self.counts()["attempts"])

    def test_receiver_instance_replaced_before_transfer_prevents_admission(self):
        self.source.publish("one", self.text)
        # Disposable fixture only: a fresh database in the exact old root must
        # never inherit the old receiver's authority just because the name matches.
        self.receiver.db_path.unlink()
        recreated = ReceiptStore(self.receiver.root, "synthetic.destination", "inbox", initialize=True)
        with self.assertRaises(PlatformError):
            self.worker(receiver=recreated).transfer_source("one")
        self.assertFalse(self.source.get("one")["source_confirmed"])
        self.assertEqual(0, self.counts()["tasks"])

    def test_expired_source_retry_completed_by_other_worker_restores_idle_wait(self):
        self.source.publish("one", self.text)
        worker = self.worker()
        with patch("masteragent.transport.time.monotonic", return_value=1000.0), \
                patch.object(worker, "transfer_source", side_effect=OSError("Synthetic temporary fault")):
            self.assertEqual(1, len(worker.run_once()["errors"]))
        self.reopened_worker().transfer_source("one")
        with patch("masteragent.transport.time.monotonic", return_value=1010.0):
            self.assertEqual({"sources": [], "deliveries": [], "errors": []}, worker.run_once())
            self.assertEqual(86400, worker._deadline("source"))

    def test_expired_delivery_retry_completed_by_other_worker_restores_idle_wait(self):
        delivery = self.completed_result()
        worker = self.worker()
        with patch("masteragent.transport.time.monotonic", return_value=1000.0), \
                patch.object(worker, "send_result", side_effect=OSError("Synthetic temporary fault")):
            self.assertEqual(1, len(worker.run_once()["errors"]))
        self.reopened_worker().send_result(delivery["delivery_id"])
        with patch("masteragent.transport.time.monotonic", return_value=1010.0):
            self.assertEqual({"sources": [], "deliveries": [], "errors": []}, worker.run_once())
            self.assertEqual(86400, worker._deadline("delivery"))

    def test_source_lease_really_renews_during_blocked_native_confirmation(self):
        from masteragent.transport import _LeaseKeeper
        worker = self.worker()
        self.source.publish("one", self.text)
        renewed = threading.Event()
        renew, confirm = self.platform.renew_source, self.source.confirm
        def renew_durably(*args):
            if not renewed.is_set():
                self.now = 1050.0
            result = renew(*args)
            renewed.set()
            return result
        def blocked_confirm(*args):
            self.assertTrue(renewed.wait(3), "Renewal must continue during source IO")
            self.now = 1080.0  # Beyond the original 1060 lease, within renewed 1110.
            return confirm(*args)
        with patch("masteragent.transport._LeaseKeeper", side_effect=lambda renew, seconds: _LeaseKeeper(renew, .03)), \
                patch.object(self.platform, "renew_source", side_effect=renew_durably), \
                patch.object(self.source, "confirm", side_effect=blocked_confirm):
            worker.transfer_source("one")
        self.assertTrue(self.platform.source_admission("route", "one")["source_confirmed"])
        self.assertTrue(self.source.get("one")["transport_completed"])
        self.assertEqual(0, self.counts()["attempts"])

    def test_delivery_lease_really_renews_during_blocked_receiver_io(self):
        from masteragent.transport import _LeaseKeeper
        delivery = self.completed_result()
        worker = self.worker()
        renewed = threading.Event()
        renew, receive = self.platform.renew_delivery, self.receiver.receive
        def renew_durably(*args):
            if not renewed.is_set():
                self.now = 1050.0
            result = renew(*args)
            renewed.set()
            return result
        def blocked_receive(*args):
            self.assertTrue(renewed.wait(3), "Renewal must continue during destination IO")
            self.now = 1080.0
            return receive(*args)
        with patch("masteragent.transport._LeaseKeeper", side_effect=lambda renew, seconds: _LeaseKeeper(renew, .03)), \
                patch.object(self.platform, "renew_delivery", side_effect=renew_durably), \
                patch.object(self.receiver, "receive", side_effect=blocked_receive):
            result = worker.send_result(delivery["delivery_id"])
        self.assertEqual("received", result["state"])
        self.assertEqual(1, result["send_count"])
        self.assertEqual(1, self.counts()["attempts"])

    @unittest.skipUnless(os.name == "nt", "Windows directory notification transport")
    def test_stop_request_does_not_report_exit_while_native_io_is_blocked(self):
        self.source.publish("one", self.text)
        worker = self.worker()
        entered, release, exited = threading.Event(), threading.Event(), threading.Event()
        confirm = self.source.confirm
        results, errors = [], []
        def blocked_confirm(*args):
            entered.set()
            if not release.wait(5):
                raise RuntimeError("Synthetic test release timeout")
            return confirm(*args)
        def run():
            try:
                results.append(worker.run(max_seconds=6))
            except Exception as exc:
                errors.append(exc)
            finally:
                exited.set()
        thread = threading.Thread(target=run)
        with patch.object(self.source, "confirm", side_effect=blocked_confirm):
            try:
                thread.start()
                self.assertTrue(entered.wait(3), errors)
                worker.stop()
                self.assertFalse(exited.is_set())
                self.assertTrue(thread.is_alive())
            finally:
                worker.stop()
                release.set()
                thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertEqual([], errors)
        self.assertTrue(results[0]["stopped"])
        self.assertTrue(self.source.get("one")["source_confirmed"])
        self.assertFalse(self.platform.source_admission("route", "one")["source_confirmed"])
        self.assert_recovered_once()


if __name__ == "__main__":
    unittest.main()
