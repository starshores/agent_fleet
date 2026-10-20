"""Synthetic source protocol checks; no native runtime, personal home or legacy bus."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
import uuid

from masteragent.errors import PlatformError
from masteragent.paths import PathSafetyError, SOURCE_ROOT
from masteragent.routing import _json
from masteragent.source_store import SourceStore, validate_envelope


class SourceStoreTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="ma31-source-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.store = self.handle(initialize=True)

    def handle(self, **kwargs):
        return SourceStore(self.root / "source", "synthetic.source", **kwargs)

    def assert_code(self, code, operation, *args, **kwargs):
        with self.assertRaises(PlatformError) as caught:
            operation(*args, **kwargs)
        self.assertEqual(code, caught.exception.code)

    def test_descriptor_is_persistent_explicit_and_distinct_for_new_store(self):
        descriptor = self.store.descriptor()
        self.assertEqual(str(self.store.root), descriptor["root"])
        self.assertEqual("ma-source-v1", descriptor["protocol_version"])
        self.assertEqual(64000, descriptor["max_text_bytes"])
        self.assertEqual(1048576, descriptor["max_envelope_bytes"])
        self.assertEqual(descriptor, self.handle().descriptor())
        self.assertEqual(descriptor, self.handle(initialize=True).descriptor())
        other = SourceStore(self.root / "other", "synthetic.source", initialize=True)
        self.assertNotEqual(descriptor["source_instance"], other.descriptor()["source_instance"])
        missing = SourceStore(self.root / "missing", "synthetic.source")
        for operation in (missing.descriptor, missing.pending, lambda: missing.get("x"),
                          lambda: missing.publish("x", "synthetic")):
            self.assert_code("source_unavailable", operation)
            self.assertFalse(missing.root.exists())

    def test_unicode_roundtrip_and_envelope_digest_excludes_only_itself(self):
        text = "合成任务 🙂 e\u0301\r\n\x00末尾 SENTINEL"
        record = self.store.publish("one", text)
        envelope = record["envelope"]
        self.assertEqual(text, self.handle().get("one")["envelope"]["text"])
        self.assertEqual(len(text.encode("utf-8")), envelope["text_bytes"])
        self.assertEqual(hashlib.sha256(text.encode("utf-8")).hexdigest(), envelope["text_sha256"])
        payload = {key: value for key, value in envelope.items() if key != "envelope_sha256"}
        self.assertEqual(hashlib.sha256(_json(payload).encode("utf-8")).hexdigest(), envelope["envelope_sha256"])
        self.assertEqual(_json(envelope), validate_envelope(envelope))
        self.assertEqual("masteragent.source-task", envelope["purpose"])
        self.assertEqual(self.store.descriptor()["source_instance"], envelope["source_instance"])
        self.assertEqual({"envelope", "admission_id", "source_confirmed", "transport_completed"}, set(record))
        self.assertIsNone(record["admission_id"])
        self.assertFalse(record["source_confirmed"])
        self.assertFalse(record["transport_completed"])

    def test_typed_source_ids_stay_distinct_and_boolean_is_never_integer(self):
        integer = self.store.publish(7, "integer")
        string = self.store.publish("7", "string")
        self.assertEqual(integer, self.handle().get(7))
        self.assertEqual(string, self.handle().get("7"))
        self.assertIs(type(integer["envelope"]["source_id"]), int)
        self.assertIs(type(string["envelope"]["source_id"]), str)
        self.assert_code("invalid_source_id", self.store.publish, True, "no")
        self.assert_code("invalid_source_id", self.store.get, 7.0)
        negative = self.store.publish(-7, "negative integer")
        self.assertEqual(negative, self.store.get(-7))

    def test_repeated_publish_preserves_original_and_changed_text_never_overwrites(self):
        original = self.store.publish("one", "合成 payload")
        self.assertEqual(original, self.handle().publish("one", "合成 payload"))
        self.assert_code("source_conflict", self.store.publish, "one", "changed")
        self.assertEqual(original, self.store.get("one"))
        self.assertEqual(1, len(self.store.pending()))

    def test_concurrent_publish_and_confirm_are_atomic_and_idempotent(self):
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: self.handle().publish("one", "synthetic"), range(16)))
        self.assertTrue(all(value == results[0] for value in results))
        digest = results[0]["envelope"]["envelope_sha256"]
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: self.handle().confirm("one", "admission-1", digest), range(16)))
        self.assertTrue(all(value == results[0] for value in results))
        with closing(sqlite3.connect(str(self.store.db_path))) as db:
            self.assertEqual(1, db.execute("SELECT count(*) FROM messages").fetchone()[0])
            self.assertEqual(1, db.execute("SELECT revision FROM source").fetchone()[0])

    def test_exact_text_byte_limit_and_one_byte_over(self):
        text = "中" * 21333 + "x"
        self.assertEqual(64000, len(text.encode("utf-8")))
        self.assertEqual(text, self.store.publish("limit", text)["envelope"]["text"])
        self.assert_code("invalid_route_text", self.store.publish, "over", text + "x")
        self.assert_code("source_not_found", self.store.get, "over")

    def test_pending_is_ordered_bounded_and_read_only(self):
        for source_id in ("z", 1, "a"):
            self.store.publish(source_id, "synthetic")
        before = self.store.db_path.read_bytes()
        self.assertEqual(["z", 1], [r["envelope"]["source_id"] for r in self.store.pending(limit=2)])
        self.assertFalse(self.store.get("z")["source_confirmed"])
        self.assertEqual(before, self.store.db_path.read_bytes())
        for limit in (True, 0, -1, 1001, "1", 1.0):
            self.assert_code("invalid_source_limit", self.store.pending, limit)

    def test_pending_cursor_passes_unfinished_front_batch_and_can_restart_scan(self):
        for source_id in ("blocked-1", "blocked-2", "later-1", "later-2"):
            self.store.publish(source_id, "synthetic")
        first = self.store.pending(limit=2)
        self.assertEqual(["blocked-1", "blocked-2"], [row["envelope"]["source_id"] for row in first])
        second = self.handle().pending(limit=2, after_id=first[-1]["envelope"]["source_id"])
        self.assertEqual(["later-1", "later-2"], [row["envelope"]["source_id"] for row in second])
        self.assertEqual([], self.store.pending(after_id="later-2"))
        self.assertEqual(first, self.store.pending(limit=2, after_id=None))
        self.assertTrue(all(not row["source_confirmed"] for row in self.store.pending()))

    def test_pending_cursor_preserves_id_type_and_accepts_completed_record(self):
        integer = self.store.publish(7, "integer")
        self.store.publish("7", "string")
        self.store.publish("tail", "tail")
        self.assertEqual(["7", "tail"], [
            row["envelope"]["source_id"] for row in self.store.pending(after_id=7)])
        self.assertEqual(["tail"], [
            row["envelope"]["source_id"] for row in self.store.pending(after_id="7")])
        self.store.confirm(7, "admission-7", integer["envelope"]["envelope_sha256"])
        self.store.complete(7, "admission-7")
        self.assertEqual(["7", "tail"], [
            row["envelope"]["source_id"] for row in self.handle().pending(after_id=7)])
        self.assert_code("source_not_found", self.store.pending, after_id="missing")
        self.assert_code("invalid_source_id", self.store.pending, after_id=True)

    def test_confirmation_crash_windows_remain_pending_until_platform_ack_is_durable(self):
        original = self.store.publish("one", "synthetic")
        digest = original["envelope"]["envelope_sha256"]
        # Platform admission committed, process crashed before native confirmation.
        self.assertEqual([original], self.handle().pending())
        confirmed = self.handle().confirm("one", "admission-1", digest)
        self.assertTrue(confirmed["source_confirmed"])
        self.assertFalse(confirmed["transport_completed"])
        # Native confirmation committed, process crashed before platform confirmation.
        self.assertEqual([confirmed], self.handle().pending())
        self.assertEqual(confirmed, self.handle().confirm("one", "admission-1", digest))
        # Platform confirmation committed, process crashed before local completion.
        self.assertEqual([confirmed], self.handle().pending())
        completed = self.handle().complete("one", "admission-1")
        self.assertTrue(completed["transport_completed"])
        self.assertEqual([], self.handle().pending())
        self.assertEqual(completed, self.handle().complete("one", "admission-1"))
        self.assertEqual(completed, self.handle().confirm("one", "admission-1", digest))
        self.assertEqual(completed, self.handle().publish("one", "synthetic"))
        self.assertEqual(completed, self.handle().get("one"))

    def test_confirmation_and_completion_reject_wrong_digest_or_admission(self):
        original = self.store.publish("one", "synthetic")
        self.assert_code("source_confirmation_conflict", self.store.complete, "one", "admission-1")
        self.assert_code("source_confirmation_conflict", self.store.confirm, "one", "admission-1", "0" * 64)
        self.assertEqual(original, self.store.get("one"))
        digest = original["envelope"]["envelope_sha256"]
        confirmed = self.store.confirm("one", "admission-1", digest)
        self.assert_code("source_confirmation_conflict", self.store.confirm, "one", "admission-2", digest)
        self.assert_code("source_confirmation_conflict", self.store.complete, "one", "admission-2")
        self.assertEqual(confirmed, self.store.get("one"))

    def test_late_failure_rolls_back_publish_and_each_confirmation_step(self):
        with patch.object(self.store, "_record", side_effect=RuntimeError("synthetic before commit")):
            with self.assertRaises(RuntimeError):
                self.store.publish("one", "synthetic")
        self.assertEqual([], self.store.pending())
        original = self.store.publish("one", "synthetic")
        digest = original["envelope"]["envelope_sha256"]
        real_record = self.store._record
        with patch.object(self.store, "_record", side_effect=[original, RuntimeError("synthetic before commit")]):
            with self.assertRaises(RuntimeError):
                self.store.confirm("one", "admission-1", digest)
        self.assertEqual(original, self.store.get("one"))
        confirmed = self.store.confirm("one", "admission-1", digest)
        with patch.object(self.store, "_record", side_effect=[confirmed, RuntimeError("synthetic before commit")]):
            with self.assertRaises(RuntimeError):
                self.store.complete("one", "admission-1")
        self.assertEqual(confirmed, self.store.get("one"))
        self.assertEqual(real_record.__func__, self.store._record.__func__)
        with closing(sqlite3.connect(str(self.store.db_path))) as db:
            self.assertEqual(1, db.execute("SELECT revision FROM source").fetchone()[0])

    def test_invalid_input_never_creates_message_or_execution_authority(self):
        for source_id in ("", "x\n", None, [], 1.5, "中" * 100):
            with self.assertRaises(PlatformError):
                self.store.publish(source_id, "synthetic")
        for text in ("", " \r\n ", "\ud800", None, 123):
            with self.assertRaises(PlatformError):
                self.store.publish("one", text)
        for kind in ("notice", "supplement", "decision", "execute", True, None):
            self.assert_code("unsupported_source_kind", self.store.publish, "one", "synthetic", kind=kind)
        self.assertFalse(hasattr(self.store, "claim"))
        self.assertFalse(hasattr(self.store, "claim_legacy"))
        self.assertFalse(hasattr(self.store, "mark_confirmed"))
        self.assertEqual([], self.store.pending())

    def test_envelope_validator_rejects_missing_extra_fields_types_and_tampering(self):
        envelope = self.store.publish("one", "synthetic")["envelope"]
        invalid = [{key: value for key, value in envelope.items() if key != "text"},
                   {**envelope, "execute": True}]
        invalid.extend({**envelope, key: value} for key, value in (
            ("purpose", "execute"), ("protocol_version", "unknown"), ("text_bytes", True),
            ("text_sha256", "0" * 64), ("envelope_sha256", "0" * 64),
            ("source_instance", "not-uuid"), ("source_id", True), ("text", "\ud800"),
            ("source_namespace", "other"), ("kind", "notice")))
        for value in invalid:
            with self.assertRaises(PlatformError):
                validate_envelope(value)
        escaped = self.store.publish("escaped", "\x00" * 64000)["envelope"]
        self.assertGreater(len(validate_envelope(escaped).encode("utf-8")), escaped["text_bytes"])

    def test_namespace_and_limits_cannot_be_adopted_or_changed(self):
        wrong = SourceStore(self.store.root, "other")
        self.assert_code("source_scope", wrong.descriptor)
        self.assert_code("source_scope", SourceStore, self.store.root, "other", initialize=True)
        with closing(sqlite3.connect(str(self.store.db_path))) as db:
            db.execute("UPDATE source SET text_limit=100")
            db.commit()
        self.assert_code("source_scope", self.store.publish, "one", "synthetic")

    def test_selected_instance_change_and_corrupt_uuid_are_rejected(self):
        self.store.descriptor()
        with closing(sqlite3.connect(str(self.store.db_path))) as db:
            db.execute("UPDATE source SET source_instance=?", (str(uuid.uuid4()),))
            db.commit()
        self.assert_code("source_instance_changed", self.store.pending)
        with closing(sqlite3.connect(str(self.store.db_path))) as db:
            db.execute("UPDATE source SET source_instance='corrupt'")
            db.commit()
        self.assert_code("source_integrity", self.handle().descriptor)

    def test_corrupt_stored_content_is_not_returned_as_valid_pending(self):
        self.store.publish("one", "synthetic")
        with closing(sqlite3.connect(str(self.store.db_path))) as db:
            stored = json.loads(db.execute("SELECT envelope FROM messages").fetchone()[0])
            stored["text"] = "corrupt"
            db.execute("UPDATE messages SET envelope=?", (_json(stored),))
            db.commit()
        self.assert_code("source_integrity", self.store.get, "one")
        self.assert_code("source_integrity", self.store.pending)
        self.assert_code("source_integrity", self.store.publish, "one", "synthetic")

    def test_strict_schema_rejects_extra_objects_wrong_ddl_and_protocol_versions(self):
        mutations = (
            "CREATE TABLE unexpected (value TEXT)",
            "CREATE VIEW unexpected AS SELECT * FROM messages",
            "CREATE INDEX unexpected ON messages(admission_id)",
            "CREATE TRIGGER unexpected AFTER UPDATE ON source BEGIN DELETE FROM messages; END",
            "ALTER TABLE messages ADD COLUMN unexpected TEXT",
            "PRAGMA user_version=2",
            "PRAGMA application_id=0",
            "PRAGMA journal_mode=WAL",
        )
        for index, sql in enumerate(mutations):
            store = SourceStore(self.root / f"schema-{index}", "synthetic.source", initialize=True)
            store.publish("one", "synthetic")
            with closing(sqlite3.connect(str(store.db_path))) as db:
                db.execute(sql)
                db.commit()
            self.assert_code("source_schema", store.pending)
            self.assert_code("source_schema", SourceStore, store.root, "synthetic.source", initialize=True)

    def test_rejecting_wal_never_creates_sidecars_or_changes_stored_bytes(self):
        with closing(sqlite3.connect(self.store.db_path)) as db:
            db.execute("PRAGMA journal_mode=WAL")
        before = {path.name: path.read_bytes() for path in self.store.root.iterdir()}
        self.assertEqual({"sources.sqlite3"}, set(before))
        for action in (self.store.descriptor, self.store.pending,
                       lambda: self.store.publish("one", "synthetic"),
                       lambda: self.handle(initialize=True)):
            self.assert_code("source_schema", action)
            self.assertEqual(before, {path.name: path.read_bytes() for path in self.store.root.iterdir()})

    def test_unrelated_database_and_nonempty_directory_are_not_adopted(self):
        unmanaged = self.root / "unmanaged"
        unmanaged.mkdir()
        (unmanaged / "unrelated.txt").write_text("synthetic", encoding="utf-8")
        self.assert_code("unmanaged_source", SourceStore, unmanaged, "synthetic.source", initialize=True)
        unrelated = self.root / "unrelated"
        unrelated.mkdir()
        with closing(sqlite3.connect(str(unrelated / "sources.sqlite3"))) as db:
            db.execute("CREATE TABLE unrelated (value TEXT)")
        self.assert_code("source_schema", SourceStore, unrelated, "synthetic.source", initialize=True)
        with closing(sqlite3.connect(str(unrelated / "sources.sqlite3"))) as db:
            self.assertEqual(["unrelated"], [row[0] for row in db.execute("SELECT name FROM sqlite_master")])

    def test_paths_reject_project_home_relative_drive_and_legacy_roots(self):
        with self.assertRaises(PathSafetyError):
            SourceStore("relative", "synthetic.source")
        with self.assertRaises(PathSafetyError):
            SourceStore(SOURCE_ROOT / "releases" / "never-created", "synthetic.source")
        with self.assertRaises(PathSafetyError):
            SourceStore(Path(self.root.anchor), "synthetic.source")
        fake_home = self.root / "synthetic-home"
        with patch("masteragent.paths.Path.home", return_value=fake_home):
            for candidate in (fake_home, fake_home / ".codex" / "source", fake_home / ".claude" / "source"):
                with self.assertRaises(PathSafetyError):
                    SourceStore(candidate, "synthetic.source")
        legacy = self.root / "synthetic-legacy"
        (legacy / "agent_bus").mkdir(parents=True)
        (legacy / "agent_bus" / "agentbus.py").write_text("# synthetic marker\n", encoding="utf-8")
        with self.assertRaises(PathSafetyError):
            SourceStore(legacy / "source", "synthetic.source")

    def test_linked_database_and_nonregular_sidecar_rejected(self):
        hardlink = self.root / "source-alias.sqlite3"
        try:
            os.link(self.store.db_path, hardlink)
        except OSError as exc:
            self.skipTest(f"Hard-link capability unavailable: {exc}")
        self.assert_code("unsafe_source", self.store.descriptor)
        hardlink.unlink()
        (self.store.root / "sources.sqlite3-wal").mkdir()
        self.assert_code("unsafe_source", self.store.publish, "one", "synthetic")

    def test_symlink_root_or_database_is_not_followed(self):
        alias = self.root / "alias"
        try:
            alias.symlink_to(self.store.root, target_is_directory=True)
        except OSError as exc:
            self.skipTest(f"Symlink capability unavailable: {exc}")
        with self.assertRaises(PathSafetyError):
            SourceStore(alias, "synthetic.source")
        linked = self.root / "linked"
        linked.mkdir()
        (linked / "sources.sqlite3").symlink_to(self.store.db_path)
        self.assert_code("unsafe_source", SourceStore(linked, "synthetic.source").descriptor)

    def test_initialization_argument_must_be_boolean(self):
        for value in (1, "yes", None):
            self.assert_code("invalid_initialization", SourceStore, self.root / "missing", "synthetic.source",
                             initialize=value)
        self.assertFalse((self.root / "missing").exists())


if __name__ == "__main__":
    unittest.main()
