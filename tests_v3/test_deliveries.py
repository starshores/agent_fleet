"""Synthetic result transport faults; no bus, credentials, process or real model."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from masteragent.errors import PlatformError
from masteragent.receipt_store import ReceiptStore
from masteragent.service import Platform


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="ma31-delivery-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.now = 1000.0
        workspace = self.root / "workspace"
        workspace.mkdir()
        package = self.root / "package.json"
        package.write_text(json.dumps({
            "schema_version": 1, "platform_api": 1, "package_id": "synthetic", "version": "1.0.0",
            "roles": [{"role_id": "worker", "display_name": "Worker", "instructions": "Synthetic only",
                       "tools": ["echo"]}], "knowledge": {},
            "tools": {"echo": {"adapter": "echo", "description": "Synthetic deterministic echo"}}, "workflows": {}
        }), encoding="utf-8")
        self.platform = self.handle()
        self.platform.install("a", package, workspace)
        self.platform.activate("a")
        self.actor = self.platform.bind("a", "worker")
        self.platform.configure_route("route", "synthetic.source", "v3", "a", "worker", 1,
                                      "synthetic.destination", "synthetic.inbox")
        self.receiver = ReceiptStore(self.root / "destination", "synthetic.destination", "synthetic.inbox",
                                     initialize=True, clock=lambda: self.now)
        self.sequence = 0

    def handle(self, readonly=False):
        return Platform(self.root / "state", readonly=readonly, clock=lambda: self.now)

    def assert_code(self, code, operation, *args, **kwargs):
        with self.assertRaises(PlatformError) as caught:
            operation(*args, **kwargs)
        self.assertEqual(code, caught.exception.code)

    def task(self):
        self.sequence += 1
        claim = self.platform.claim_source("route", self.sequence, "source-transporter")
        admission = self.platform.admit_source("route", claim["route_epoch"], self.sequence,
                                               "Synthetic task " + str(self.sequence), "source-transporter",
                                               claim["claim_epoch"], claim["claim_token"])
        attempt = self.platform.claim(self.actor, admission["task_id"], "echo", 300)
        return admission, attempt

    def completed(self, text="合成 🙂 e\u0301\r\nFinal SENTINEL", error=None):
        admission, attempt = self.task()
        self.platform.complete(self.actor, attempt["attempt_id"], attempt["lease_token"], text, error)
        delivery = next(row for row in self.platform.deliveries("a") if row["task_id"] == admission["task_id"])
        return delivery

    def sending(self, delivery):
        claim = self.platform.claim_delivery(delivery["delivery_id"], "sender", self.receiver)
        args = (delivery["delivery_id"], "sender", claim["claim_epoch"], claim["claim_token"])
        return args, self.platform.begin_delivery(*args)

    def test_result_intent_same_transaction_rolls_back_even_after_delivery_insert(self):
        admission, attempt = self.task()
        original = self.platform._delivery_event
        def late_failure(db, row, kind, detail):
            original(db, row, kind, detail)
            raise RuntimeError("synthetic late completion failure")
        with patch.object(self.platform, "_delivery_event", side_effect=late_failure):
            with self.assertRaises(RuntimeError):
                self.platform.complete(self.actor, attempt["attempt_id"], attempt["lease_token"], "full result")
        self.assertEqual([], self.platform.results("a"))
        self.assertEqual([], self.platform.deliveries("a"))
        with self.platform._db() as db:
            self.assertEqual("running", db.execute("SELECT state FROM attempts").fetchone()[0])
            self.assertEqual(0, db.execute("SELECT count(*) FROM delivery_evidence").fetchone()[0])
        self.platform.complete(self.actor, attempt["attempt_id"], attempt["lease_token"], "full result")
        self.platform.complete(self.actor, attempt["attempt_id"], attempt["lease_token"], "full result")
        self.assertEqual(1, len(self.platform.deliveries("a")))

    def test_successful_send_full_body_and_receipt_do_not_claim_processing(self):
        delivery = self.completed()
        self.assertEqual("pending", delivery["state"])
        received = self.platform.send_delivery_once(delivery["delivery_id"], "sender", self.receiver)
        self.assertEqual("received", received["state"])
        self.assertEqual(1, received["send_count"])
        self.assertIsNone(received["agent_processed"])
        self.assertIsNone(received["user_read"])
        envelope = self.platform.delivery_envelope(delivery["delivery_id"])
        self.assertEqual(self.platform.results("a")[0]["output"], self.receiver.read_text(envelope))
        self.assertEqual(envelope["text_sha256"], received["receipt"]["text_sha256"])
        for key in ("claim_hash", "claim_token", "text", "envelope", "policy"):
            self.assertNotIn(key, received)

    def test_unrouted_task_has_no_delivery_and_old_outbox_does_not_prove_receipt(self):
        task = self.platform.submit("a", "worker", "ordinary synthetic")
        self.platform.run_as(self.actor, task["task_id"], "echo")
        self.assertEqual([], self.platform.deliveries())
        delivery = self.completed()
        with self.platform._db() as db:
            self.assertTrue(db.execute("SELECT 1 FROM outbox WHERE task_id=?", (delivery["task_id"],)).fetchone())
        self.assertEqual("pending", self.platform.delivery_status(delivery["delivery_id"])["state"])

    def test_unsent_claim_can_expire_reclaim_and_old_sender_is_fenced(self):
        delivery = self.completed()
        first = self.platform.claim_delivery(delivery["delivery_id"], "first", self.receiver)
        args = (delivery["delivery_id"], "first", first["claim_epoch"], first["claim_token"])
        self.assert_code("delivery_busy", self.platform.claim_delivery, delivery["delivery_id"], "second", self.receiver)
        self.now += 61
        second = self.platform.claim_delivery(delivery["delivery_id"], "second", self.receiver)
        self.assertGreater(second["claim_epoch"], first["claim_epoch"])
        self.assert_code("delivery_owner", self.platform.begin_delivery, *args)
        self.assert_code("delivery_owner", self.platform.renew_delivery, *args)
        self.assertEqual(0, second["send_count"])

    def test_concurrent_sender_claims_have_one_winner(self):
        delivery = self.completed()
        def claim(index):
            try:
                return self.handle().claim_delivery(delivery["delivery_id"], "sender-" + str(index), self.receiver)
            except PlatformError as exc:
                return exc.code
        with ThreadPoolExecutor(max_workers=8) as pool:
            outcomes = list(pool.map(claim, range(8)))
        self.assertEqual(1, sum(isinstance(value, dict) for value in outcomes))
        self.assertEqual(7, outcomes.count("delivery_busy"))

    def test_sending_expiry_is_held_not_blindly_reclaimed(self):
        delivery = self.completed()
        args, envelope = self.sending(delivery)
        self.now += 61
        self.assert_code("delivery_held", self.platform.claim_delivery, delivery["delivery_id"], "new-sender", self.receiver)
        held = self.platform.delivery_status(delivery["delivery_id"])
        self.assertEqual("held", held["state"])
        self.assertEqual("sending_lease_expired", held["reason"])
        self.assert_code("delivery_not_pending", self.platform.send_delivery_once,
                         delivery["delivery_id"], "new-sender", self.receiver)
        self.assertEqual("absent", self.receiver.query(envelope)["status"])

    def test_endpoint_commit_lost_response_recovers_without_second_receive_or_model(self):
        delivery = self.completed()
        calls = []
        destination = self.receiver
        class LostResponse:
            descriptor = destination.descriptor
            def receive(self, payload):
                calls.append(payload["delivery_id"])
                destination.receive(payload)
                raise OSError("synthetic response loss")
        held = self.platform.send_delivery_once(delivery["delivery_id"], "sender", LostResponse())
        self.assertEqual("held", held["state"])
        recovered = self.handle().reconcile_delivery(delivery["delivery_id"], self.receiver)
        self.assertEqual("received", recovered["state"])
        self.assertEqual([delivery["delivery_id"]], calls)
        with self.platform._db() as db:
            self.assertEqual(1, db.execute("SELECT count(*) FROM attempts").fetchone()[0])
            self.assertEqual(1, db.execute("SELECT count(*) FROM results").fetchone()[0])

    def test_receipt_saved_before_platform_commit_failure_is_recoverable(self):
        delivery = self.completed()
        original = self.platform._delivery_event
        def fail_received(db, row, kind, detail):
            original(db, row, kind, detail)
            if kind == "delivery_received":
                raise RuntimeError("synthetic core commit failure")
        with patch.object(self.platform, "_delivery_event", side_effect=fail_received):
            held = self.platform.send_delivery_once(delivery["delivery_id"], "sender", self.receiver)
        self.assertEqual("held", held["state"])
        self.assertEqual("received", self.platform.reconcile_delivery(delivery["delivery_id"], self.receiver)["state"])

    def test_expired_late_sender_cannot_commit_but_receipt_is_queryable(self):
        delivery = self.completed()
        args, envelope = self.sending(delivery)
        receipt = self.receiver.receive(envelope)
        self.now += 61
        self.assert_code("delivery_fenced", self.platform.complete_delivery, *args, receipt)
        result = self.platform.reconcile_delivery(delivery["delivery_id"], self.receiver)
        self.assertEqual("received", result["state"])
        self.assertEqual(receipt, result["receipt"])
        self.assert_code("delivery_owner", self.platform.complete_delivery, *args, receipt)

    def test_unavailable_and_non_authoritative_absent_remain_held(self):
        delivery = self.completed()
        args, envelope = self.sending(delivery)
        self.platform.hold_delivery(*args)
        destination = self.receiver
        class Unknown:
            descriptor = destination.descriptor
            def query(self, payload):
                raise OSError("synthetic offline")
        self.assertEqual("held", self.platform.reconcile_delivery(delivery["delivery_id"], Unknown(), retry_absent=True)["state"])
        class FakeAbsent:
            descriptor = destination.descriptor
            def query(self, payload):
                return {"status": "absent"}
        result = self.platform.reconcile_delivery(delivery["delivery_id"], FakeAbsent(), retry_absent=True)
        self.assertEqual("held", result["state"])
        self.assertEqual("receipt_unavailable", result["reason"])

    def test_authoritative_absent_retry_same_id_backoff_and_three_call_limit(self):
        delivery = self.completed()
        calls = []
        destination = self.receiver
        class BeforeReceiveFailure:
            descriptor = destination.descriptor
            def receive(self, payload):
                calls.append(payload["delivery_id"])
                raise OSError("synthetic not reached")
        for index in range(3):
            held = self.platform.send_delivery_once(delivery["delivery_id"], "sender", BeforeReceiveFailure())
            self.assertEqual("held", held["state"])
            self.assertEqual(index + 1, held["send_count"])
            pending = self.platform.reconcile_delivery(delivery["delivery_id"], self.receiver, retry_absent=True)
            if index < 2:
                self.assertEqual("pending", pending["state"])
                self.assert_code("delivery_backoff", self.platform.claim_delivery, delivery["delivery_id"], "sender", self.receiver)
                self.now += 5
            else:
                self.assertEqual("held", pending["state"])
                self.assertEqual("receipt_send_limit", pending["reason"])
        self.assertEqual([delivery["delivery_id"]] * 3, calls)
        self.assertEqual(1, len(self.platform.results("a")))

    def test_mismatched_late_receipt_does_not_cross_delivery_scope(self):
        first, second = self.completed("first"), self.completed("second")
        first_args, first_payload = self.sending(first)
        second_args, second_payload = self.sending(second)
        receipt = self.receiver.receive(first_payload)
        self.assert_code("receipt_mismatch", self.platform.complete_delivery, *second_args, receipt)
        self.platform.hold_delivery(*second_args)
        destination = self.receiver
        class WrongReceipt:
            descriptor = destination.descriptor
            def query(self, payload):
                return {"status": "received", "receipt": receipt}
        result = self.platform.reconcile_delivery(second["delivery_id"], WrongReceipt())
        self.assertEqual("held", result["state"])
        self.assertEqual("receipt_conflict", result["reason"])
        self.assertIsNone(result["receipt"])
        self.platform.complete_delivery(*first_args, receipt)

    def test_route_freeze_blocks_unsent_but_does_not_block_receipt_reconciliation(self):
        unsent, sent = self.completed("unsent"), self.completed("sent")
        args, payload = self.sending(sent)
        self.receiver.receive(payload)
        self.platform.hold_delivery(*args)
        self.platform.set_route_controls("route", 1, delivery_open=False)
        self.assert_code("delivery_frozen", self.platform.claim_delivery, unsent["delivery_id"], "sender", self.receiver)
        self.assertEqual("received", self.platform.reconcile_delivery(sent["delivery_id"], self.receiver)["state"])

    def test_route_reconfigure_does_not_change_existing_destination_or_policy(self):
        delivery = self.completed()
        self.platform.configure_route("route", "synthetic.source", "v3", "a", "worker", 1,
                                      "new.destination", "new.inbox", expected_epoch=1, destination_limit=8)
        payload = self.platform.delivery_envelope(delivery["delivery_id"])
        self.assertEqual(1, payload["route_epoch"])
        self.assertEqual("synthetic.destination", payload["destination_namespace"])
        self.assertEqual("received", self.platform.send_delivery_once(delivery["delivery_id"], "sender", self.receiver)["state"])

    def test_destination_io_occurs_outside_core_transaction(self):
        delivery = self.completed()
        platform, destination = self.platform, self.receiver
        class IndependentDestination:
            descriptor = destination.descriptor
            def receive(self, payload):
                platform.submit("a", "worker", "Synthetic concurrent core writer")
                return destination.receive(payload)
        result = self.platform.send_delivery_once(delivery["delivery_id"], "sender", IndependentDestination())
        self.assertEqual("received", result["state"])
        another = self.completed()
        args, _ = self.sending(another)
        self.platform.hold_delivery(*args)
        class IndependentLookup:
            descriptor = destination.descriptor
            def query(self, payload):
                platform.submit("a", "worker", "Synthetic concurrent lookup writer")
                return destination.query(payload)
        self.assertEqual("held", self.platform.reconcile_delivery(another["delivery_id"], IndependentLookup())["state"])

    def test_long_result_and_escaped_envelope_are_retained_and_rejected(self):
        for text, reason in (("中" * 170666 + "abc", "delivery_text_limit"),
                             ("\x00" * 180000, "delivery_envelope_limit")):
            delivery = self.completed(text)
            self.assertEqual("rejected", delivery["state"])
            self.assertEqual(reason, delivery["reason"])
            result = next(value for value in self.platform.results("a") if value["task_id"] == delivery["task_id"])
            self.assertEqual(text, result["output"])
            self.assertEqual(len(text.encode("utf-8")), delivery["text_bytes"])
            self.assert_code("delivery_not_pending", self.platform.claim_delivery, delivery["delivery_id"], "sender", self.receiver)

    def test_failed_result_is_preserved_as_explicit_error_payload(self):
        error = {"code": "synthetic_failure", "message": "完整错误 SENTINEL"}
        delivery = self.completed(None, error)
        result = self.platform.send_delivery_once(delivery["delivery_id"], "sender", self.receiver)
        payload = self.platform.delivery_envelope(delivery["delivery_id"])
        self.assertEqual("received", result["state"])
        self.assertEqual("error", payload["result_status"])
        self.assertEqual(error, json.loads(self.receiver.read_text(payload)))

    def test_readonly_queries_never_claim_initialize_or_acknowledge(self):
        delivery = self.completed()
        readonly = self.handle(readonly=True)
        before = hashlib.sha256(self.platform.db_path.read_bytes()).hexdigest()
        self.assertEqual("pending", readonly.delivery_status(delivery["delivery_id"])["state"])
        readonly.deliveries("a")
        readonly.delivery_envelope(delivery["delivery_id"])
        self.assertEqual(before, hashlib.sha256(self.platform.db_path.read_bytes()).hexdigest())
        self.assert_code("read_only", readonly.claim_delivery, delivery["delivery_id"], "sender", self.receiver)

    def test_empty_other_root_with_same_namespace_cannot_requeue_a_lost_receipt(self):
        delivery = self.completed()
        args, payload = self.sending(delivery)
        self.receiver.receive(payload)
        held = self.platform.hold_delivery(*args)
        other = ReceiptStore(self.root / "empty-other", "synthetic.destination", "synthetic.inbox", initialize=True)
        self.assertEqual("absent", other.query(payload)["status"])
        self.assert_code("delivery_destination_mismatch", self.platform.reconcile_delivery,
                         delivery["delivery_id"], other, retry_absent=True)
        self.assert_code("delivery_destination_mismatch", self.platform.claim_delivery,
                         delivery["delivery_id"], "sender", other)
        self.assertEqual(held, self.platform.delivery_status(delivery["delivery_id"]))
        recovered = self.platform.reconcile_delivery(delivery["delivery_id"], self.receiver)
        self.assertEqual("received", recovered["state"])
        self.assertEqual(1, recovered["send_count"])

    def test_same_root_replaced_instance_cannot_claim_absence_or_accept_receipt(self):
        import uuid
        delivery = self.completed()
        args, payload = self.sending(delivery)
        self.platform.hold_delivery(*args)
        with closing(sqlite3.connect(str(self.receiver.db_path))) as db:
            db.execute("UPDATE destination SET store_instance_id=?", (str(uuid.uuid4()),))
            db.commit()
        replaced = ReceiptStore(self.receiver.root, "synthetic.destination", "synthetic.inbox")
        self.assert_code("delivery_destination_mismatch", self.platform.reconcile_delivery,
                         delivery["delivery_id"], replaced, retry_absent=True)
        self.assertEqual("held", self.platform.delivery_status(delivery["delivery_id"])["state"])

    def test_receipt_and_absent_must_match_bound_instance_even_if_descriptor_matches(self):
        delivery = self.completed()
        args, payload = self.sending(delivery)
        other = ReceiptStore(self.root / "other", "synthetic.destination", "synthetic.inbox", initialize=True)
        wrong_receipt = other.receive(payload)
        self.assert_code("receipt_mismatch", self.platform.complete_delivery, *args, wrong_receipt)
        self.platform.hold_delivery(*args)
        destination = self.receiver
        class WrongInstance:
            descriptor = destination.descriptor
            def query(self, envelope):
                response = destination.query(envelope)
                response["destination_instance"] = other.descriptor()["destination_instance"]
                return response
        held = self.platform.reconcile_delivery(delivery["delivery_id"], WrongInstance(), retry_absent=True)
        self.assertEqual("held", held["state"])
        self.assertEqual("receipt_unavailable", held["reason"])


if __name__ == "__main__":
    unittest.main()
