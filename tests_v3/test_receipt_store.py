"""Independent local receipt authority, using synthetic text and temporary roots."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import hashlib
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from masteragent.errors import PlatformError
from masteragent.paths import PathSafetyError
from masteragent.receipt_store import ReceiptStore, canonical_json, digest, validate_envelope


def envelope(text="合成 result 🙂 e\u0301\r\n末尾 SENTINEL", delivery_id="delivery-1", namespace="synthetic.destination"):
    return {"purpose": "masteragent.result-delivery", "payload_version": "text.v1",
            "delivery_id": delivery_id, "admission_id": "admission-1", "route_id": "route-1",
            "route_epoch": 1, "installation_id": "synthetic", "role_id": "worker", "generation": 1,
            "task_id": "task-1", "attempt_id": "attempt-1", "result_id": "task-1",
            "destination_namespace": namespace, "destination_id": "synthetic.inbox", "result_status": "ok",
            "text": text, "text_bytes": len(text.encode("utf-8")), "text_sha256": digest(text)}


class ReceiptStoreTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="ma31-receipt-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.store = self.handle(initialize=True)

    def handle(self, **kwargs):
        return ReceiptStore(self.root / "destination", "synthetic.destination", "synthetic.inbox", **kwargs)

    def assert_code(self, code, operation, *args, **kwargs):
        with self.assertRaises(PlatformError) as caught:
            operation(*args, **kwargs)
        self.assertEqual(code, caught.exception.code)

    def test_complete_text_receipt_reopens_and_duplicate_keeps_original_receipt(self):
        payload = envelope()
        receipt = self.store.receive(payload)
        self.assertEqual(payload["text_bytes"], receipt["text_bytes"])
        self.assertEqual(payload["text_sha256"], receipt["text_sha256"])
        reopened = self.handle()
        self.assertEqual(receipt, reopened.receive(payload))
        self.assertEqual(receipt, reopened.query(payload)["receipt"])
        self.assertEqual(payload["text"], reopened.read_text(payload))
        self.assertEqual(1, receipt["storage_revision"])
        self.assertNotIn("agent_processed", receipt)
        self.assertNotIn("user_read", receipt)

    def test_concurrent_repeats_are_one_atomic_persisted_receipt(self):
        payload = envelope()
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: self.handle().receive(payload), range(16)))
        self.assertEqual(1, len({value["receipt_id"] for value in results}))
        with closing(sqlite3.connect(str(self.store.db_path))) as db:
            self.assertEqual(1, db.execute("SELECT count(*) FROM receipts").fetchone()[0])
            self.assertEqual(1, db.execute("SELECT revision FROM destination").fetchone()[0])

    def test_same_id_different_text_or_route_conflicts_without_overwrite(self):
        original = envelope()
        receipt = self.store.receive(original)
        for other in (envelope("different"), {**original, "route_epoch": 2}, {**original, "task_id": "other"}):
            self.assert_code("receipt_conflict", self.store.receive, other)
            self.assertEqual("conflict", self.store.query(other)["status"])
        self.assertEqual(receipt, self.store.query(original)["receipt"])
        self.assertEqual(original["text"], self.store.read_text(original))

    def test_absent_is_authoritative_only_for_initialized_matching_store(self):
        payload = envelope()
        before = hashlib.sha256(self.store.db_path.read_bytes()).hexdigest()
        absent = self.store.query(payload)
        self.assertEqual("absent", absent["status"])
        self.assertEqual(digest(canonical_json(payload)), absent["envelope_sha256"])
        self.assertEqual(before, hashlib.sha256(self.store.db_path.read_bytes()).hexdigest())
        missing = ReceiptStore(self.root / "missing", "synthetic.destination", "synthetic.inbox")
        self.assertEqual("unavailable", missing.query(payload)["status"])
        self.assertFalse(missing.root.exists())
        wrong = ReceiptStore(self.store.root, "different.destination", "synthetic.inbox")
        self.assertEqual("unavailable", wrong.query(envelope(namespace="different.destination"))["status"])
        self.assertEqual("conflict", self.store.query(envelope(namespace="different.destination"))["status"])

    def test_utf8_exact_limit_and_one_byte_over(self):
        body = "中" * 170666 + "ab"
        self.assertEqual(512000, len(body.encode("utf-8")))
        at_limit = envelope(body)
        self.store.receive(at_limit)
        self.assertEqual(body, self.store.read_text(at_limit))
        self.assert_code("delivery_text_limit", self.store.receive, envelope(body + "x", delivery_id="overflow"))
        self.assertEqual("absent", self.store.query(envelope("small", delivery_id="overflow"))["status"])

    def test_actual_serialized_envelope_limit_not_character_count(self):
        escaped = envelope("\x00" * 180000)
        self.assertLess(escaped["text_bytes"], 512000)
        self.assert_code("delivery_envelope_limit", self.store.receive, escaped)
        ordinary = envelope("abc")
        size = len(canonical_json(ordinary).encode("utf-8"))
        self.assertEqual(canonical_json(ordinary), validate_envelope(ordinary, envelope_limit=size))
        self.assert_code("delivery_envelope_limit", validate_envelope, ordinary, 512000, size - 1)

    def test_declared_smaller_destination_limit_enforced_and_immutable(self):
        small = ReceiptStore(self.root / "small", "synthetic.destination", "synthetic.inbox",
                             initialize=True, max_text_bytes=10)
        self.assert_code("delivery_text_limit", small.receive, envelope("x" * 11))
        small.receive(envelope("x" * 10))
        mismatch = ReceiptStore(small.root, "synthetic.destination", "synthetic.inbox")
        self.assertEqual("unavailable", mismatch.query(envelope("x" * 10))["status"])

    def test_invalid_unicode_digest_types_and_control_identity_rejected(self):
        bad = envelope()
        bad["text"] = "\ud800"
        self.assert_code("invalid_delivery", self.store.receive, bad)
        for key, value in (("text_bytes", True), ("generation", True), ("route_epoch", 0),
                           ("text_sha256", "0" * 64), ("delivery_id", "with\nnewline"),
                           ("destination_id", "中" * 67)):
            payload = {**envelope(), key: value}
            with self.assertRaises(PlatformError):
                self.store.receive(payload)
        self.assert_code("invalid_delivery", self.store.receive, {**envelope(), "unknown": True})
        self.assertEqual("absent", self.store.query(envelope())["status"])

    def test_late_failure_rolls_back_complete_body_and_receipt_revision(self):
        with patch.object(self.store, "_receipt", side_effect=RuntimeError("synthetic before commit")):
            with self.assertRaises(RuntimeError):
                self.store.receive(envelope())
        self.assertEqual("absent", self.store.query(envelope())["status"])
        receipt = self.store.receive(envelope())
        self.assertEqual(1, receipt["storage_revision"])

    def test_storage_corruption_is_unavailable_never_absent_or_success(self):
        self.store.receive(envelope())
        with closing(sqlite3.connect(str(self.store.db_path))) as db:
            db.execute("UPDATE receipts SET text='corrupt'")
            db.commit()
        self.assertEqual("unavailable", self.store.query(envelope())["status"])
        self.assert_code("destination_integrity", self.store.read_text, envelope())

    def test_bad_schema_wrong_path_or_unmanaged_directory_not_adopted(self):
        with self.assertRaises(PathSafetyError):
            ReceiptStore(Path("relative"), "synthetic.destination", "synthetic.inbox")
        unmanaged = self.root / "unmanaged"
        unmanaged.mkdir()
        (unmanaged / "unrelated.txt").write_text("synthetic", encoding="utf-8")
        self.assert_code("unmanaged_destination", ReceiptStore, unmanaged, "synthetic.destination",
                         "synthetic.inbox", initialize=True)
        with closing(sqlite3.connect(str(self.store.db_path))) as db:
            db.execute("PRAGMA user_version=99")
        self.assertEqual("unavailable", self.store.query(envelope())["status"])
        self.assert_code("destination_schema", self.handle, initialize=True)

    def test_new_id_same_content_is_separate_receipt_without_execution(self):
        one = self.store.receive(envelope(delivery_id="one"))
        two = self.store.receive(envelope(delivery_id="two"))
        self.assertNotEqual(one["receipt_id"], two["receipt_id"])
        self.assertEqual(2, two["storage_revision"])

    def test_instance_descriptor_is_persistent_and_separate_for_a_new_store(self):
        descriptor = self.store.descriptor()
        self.assertEqual(descriptor, self.handle().descriptor())
        self.assertEqual(descriptor, self.handle(initialize=True).descriptor())
        other = ReceiptStore(self.root / "other", "synthetic.destination", "synthetic.inbox", initialize=True)
        self.assertNotEqual(descriptor["destination_instance"], other.descriptor()["destination_instance"])
        receipt = self.store.receive(envelope())
        self.assertEqual(descriptor["destination_instance"], receipt["destination_instance"])
        self.assertEqual(descriptor["destination_instance"], self.store.query(envelope(delivery_id="absent"))["destination_instance"])

    def test_trigger_or_view_cannot_erase_old_receipts_then_claim_absence(self):
        self.store.receive(envelope())
        with closing(sqlite3.connect(str(self.store.db_path))) as db:
            db.execute("""CREATE TRIGGER erase_receipts AFTER UPDATE ON destination
                BEGIN DELETE FROM receipts; END""")
        self.assert_code("destination_schema", self.store.receive, envelope(delivery_id="second"))
        self.assertEqual("unavailable", self.store.query(envelope())["status"])
        with closing(sqlite3.connect(str(self.store.db_path))) as db:
            self.assertEqual(1, db.execute("SELECT count(*) FROM receipts").fetchone()[0])
            db.execute("DROP TRIGGER erase_receipts")
            db.execute("CREATE VIEW unsupported AS SELECT delivery_id FROM receipts")
        self.assertEqual("unavailable", self.store.query(envelope())["status"])

    def test_selected_destination_detects_instance_change_before_receive(self):
        import uuid
        self.store.descriptor()
        with closing(sqlite3.connect(str(self.store.db_path))) as db:
            db.execute("UPDATE destination SET store_instance_id=?", (str(uuid.uuid4()),))
            db.commit()
        self.assert_code("destination_instance_changed", self.store.receive, envelope())
        self.assertEqual("unavailable", self.store.query(envelope())["status"])

    def test_wal_header_is_rejected_before_connect_without_creating_sidecars(self):
        with closing(sqlite3.connect(self.store.db_path)) as db:
            self.assertEqual("wal", db.execute("PRAGMA journal_mode=WAL").fetchone()[0])
        before = {path.name: path.read_bytes() for path in self.store.root.iterdir()}
        self.assertEqual({"receipts.sqlite3"}, set(before))
        self.assertEqual(b"\x02\x02", before["receipts.sqlite3"][18:20])
        # Every opening path, including initialize and query, must reject the
        # unsupported database before SQLite can create -wal or -shm files.
        with patch("masteragent.receipt_store.sqlite3.connect", side_effect=AssertionError("WAL database was opened")):
            for action, args, kwargs in (
                (self.store.descriptor, (), {}),
                (self.store.read_text, (envelope(),), {}),
                (self.store.receive, (envelope(),), {}),
                (self.handle, (), {"initialize": True}),
            ):
                with self.subTest(action=action.__name__):
                    self.assert_code("destination_schema", action, *args, **kwargs)
            self.assertEqual("unavailable", self.store.query(envelope())["status"])
        self.assertEqual(before, {path.name: path.read_bytes() for path in self.store.root.iterdir()})

    def test_existing_live_wal_sidecars_are_untouched_by_descriptor_or_query(self):
        with closing(sqlite3.connect(self.store.db_path)) as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("UPDATE destination SET revision=1")
            db.commit()
            before = {path.name: path.read_bytes() for path in self.store.root.iterdir()}
            self.assertIn("receipts.sqlite3-wal", before)
            self.assert_code("destination_schema", self.store.descriptor)
            self.assertEqual("unavailable", self.store.query(envelope())["status"])
            self.assertEqual(before, {path.name: path.read_bytes() for path in self.store.root.iterdir()})


if __name__ == "__main__":
    unittest.main()
