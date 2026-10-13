"""Upgrade real v1 schema fixtures without assuming today's CREATE statements."""
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from masteragent.contracts import load_package
from masteragent.errors import PlatformError
from masteragent.service import Platform
from masteragent.storage import migrate_state, verify_schema

ROOT = Path(__file__).resolve().parents[1]


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="ma3-upgrade-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.state = self.root / "state"
        self.state.mkdir()
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.database = self.state / "state.sqlite3"
        package = load_package(ROOT / "examples" / "v3" / "business-a.json")
        snapshot = json.dumps({"manifest": package.manifest, "roles": list(package.roles), "knowledge": package.knowledge, "tools": package.tools, "workflows": package.workflows}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        with closing(sqlite3.connect(self.database)) as db:
            db.executescript((ROOT / "tests_v3" / "fixtures" / "schema_v1.sql").read_text(encoding="utf-8"))
            db.execute("INSERT INTO installations VALUES (?,?,?,?,?,?,?,?,?,?)", ("a", package.package_id, package.version, package.digest, snapshot, hashlib.sha256(snapshot.encode()).hexdigest(), str(self.workspace), "active", 1, "2026-10-08T00:00:00Z"))
            db.execute("INSERT INTO registrations VALUES ('a','reviewer',1,'now')")
            db.execute("INSERT INTO actor_tokens VALUES ('old-token','a','reviewer',1)")
            for task, status in (("queued", "queued"), ("completed", "completed"), ("running", "running")):
                db.execute("INSERT INTO tasks VALUES (?,?,?,?,?,?,?,?,?)", (task, "a", "reviewer", 1, task + " payload", task + "-request", status, "now", "now" if status == "completed" else None))
            db.execute("INSERT INTO results VALUES ('completed','a','reviewer',1,'catalogue-echo','old output','ok',NULL,'now')")
            db.execute("INSERT INTO notifications VALUES ('old-notice','completed','a','reviewer',1,'task_completed','now')")
            db.commit()

    def snapshot(self):
        return {str(p.relative_to(self.state)): p.read_bytes() for p in self.state.rglob("*") if p.is_file()}

    def test_readers_and_regular_open_require_explicit_upgrade_without_writes(self):
        before = self.snapshot()
        for read_only in (True, False):
            with self.subTest(readonly=read_only), self.assertRaises(PlatformError) as caught:
                Platform(self.state, readonly=read_only).status()
            self.assertEqual(caught.exception.code, "migration_required")
        self.assertEqual(before, self.snapshot())

    def test_upgrade_preserves_history_and_backup_without_replaying_unknown_work(self):
        result = migrate_state(self.state)
        self.assertTrue(result["migrated"])
        backup = Path(result["backup_path"])
        self.assertTrue(backup.is_file())
        with closing(sqlite3.connect(backup)) as db:
            verify_schema(db, version=1)
            self.assertEqual(db.execute("SELECT state FROM tasks WHERE task_id='running'").fetchone()[0], "running")
            self.assertEqual(db.execute("SELECT count(*) FROM actor_tokens").fetchone()[0], 1)
        platform = Platform(self.state)
        history = platform.history("a")
        self.assertEqual(len(history["tasks"]), 3)
        states = {task["task_id"]: task["state"] for task in history["tasks"]}
        self.assertEqual(states, {"queued": "queued", "completed": "completed", "running": "needs_reconcile"})
        self.assertEqual(history["results"][0]["output"], "old output")
        self.assertIsNone(history["results"][0]["attempt_id"])
        self.assertEqual(platform.outbox("a"), [])
        held = platform.outbox("a", include_delivered=True)[0]
        with self.assertRaises(PlatformError) as caught:
            platform.ack_outbox("a", held["event_id"])
        self.assertEqual(caught.exception.code, "event_held")
        self.assertEqual(platform.outbox("a", include_delivered=True)[0]["state"], "held")
        with closing(sqlite3.connect(self.database)) as db:
            verify_schema(db)
            self.assertEqual(db.execute("SELECT count(*) FROM actor_tokens").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT state FROM outbox").fetchone()[0], "held")
            self.assertEqual(db.execute("PRAGMA foreign_key_check").fetchall(), [])
        with self.assertRaises(PlatformError):
            platform.deactivate("a")
        platform.reconcile_task("a", "running", "retry", "Verified old synthetic executor stopped", safe_to_retry=True)
        task = next(row for row in platform.history("a")["tasks"] if row["task_id"] == "running")
        self.assertEqual(task["state"], "queued")
        result = platform.run("a", "running", "catalogue-echo")
        self.assertEqual(result["output"], "running payload")
        self.assertIsNotNone(result["attempt_id"])

    def test_second_migration_is_read_only_idempotent(self):
        migrate_state(self.state)
        before = self.snapshot()
        self.assertFalse(migrate_state(self.state)["migrated"])
        self.assertEqual(before, self.snapshot())

    def test_corrupt_source_schema_is_not_repaired_or_backed_up_as_valid(self):
        with closing(sqlite3.connect(self.database)) as db:
            db.execute("DROP TABLE registrations")
            db.commit()
        before = self.snapshot()
        with self.assertRaises(PlatformError) as caught:
            migrate_state(self.state)
        self.assertEqual(caught.exception.code, "schema_corrupt")
        self.assertEqual(before, self.snapshot())

    def test_invalid_legacy_references_roll_back_entire_schema_change(self):
        with closing(sqlite3.connect(self.database)) as db:
            db.execute("UPDATE results SET task_id='missing-task'")
            db.commit()
        before = self.database.read_bytes()
        with self.assertRaises(PlatformError) as caught:
            migrate_state(self.state)
        self.assertEqual(caught.exception.code, "migration_integrity")
        self.assertEqual(before, self.database.read_bytes())
        with closing(sqlite3.connect(self.database)) as db:
            verify_schema(db, version=1)
        self.assertEqual(len(list((self.state / "backups").glob("*.sqlite3"))), 1)


if __name__ == "__main__":
    unittest.main()
