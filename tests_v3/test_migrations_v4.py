"""Upgrade an independent frozen schema 3 fixture, preserving deployment/history."""
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from masteragent import storage
from masteragent.errors import PlatformError
from masteragent.service import Platform


FIXTURE = Path(__file__).resolve().parent / "fixtures" / "schema_v3.sql"


class SchemaFourMigrationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="ma3-schema3-upgrade-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.state = self.root / "state"
        self.state.mkdir()
        self.database = self.state / "state.sqlite3"
        workspace = self.root / "workspace"
        workspace.mkdir()
        snapshot = {"manifest": {"package_id": "synthetic", "version": "1.0.0"},
                    "roles": [{"role_id": "worker", "display_name": "Worker", "instructions": "synthetic",
                               "tools": ["text"]}], "knowledge": {},
                    "tools": {"text": {"adapter": "echo", "description": "synthetic"}}, "workflows": {}}
        raw = json.dumps(snapshot)
        with closing(sqlite3.connect(self.database)) as db:
            db.executescript(FIXTURE.read_text(encoding="utf-8"))
            db.execute("INSERT INTO installations VALUES (?,?,?,?,?,?,?,?,?,?)", (
                "a", "synthetic", "1.0.0", "package-digest", raw, hashlib.sha256(raw.encode()).hexdigest(),
                str(workspace), "active", 1, "before"))
            db.execute("INSERT INTO registrations VALUES ('a','worker',1,'before')")
            db.execute("INSERT INTO actor_tokens VALUES ('old-token-hash','a','worker',1)")
            db.execute("INSERT INTO role_deployments VALUES ('a','worker',?,'read-only','before')", (str(workspace),))
            for task, status, attempt_state in (("done", "completed", "completed"),
                                               ("held", "needs_reconcile", "expired")):
                db.execute("INSERT INTO tasks VALUES (?,?,?,1,?,?,?,'before','finished',1,NULL)",
                           (task, "a", "worker", "synthetic " + task, task + "-request", status))
                db.execute("INSERT INTO attempts VALUES (?,?,?,'worker',1,1,'text','owner','lease',1,?,'before','finished')",
                           (task + "-attempt", task, "a", attempt_state))
                db.execute("INSERT INTO attempt_inputs VALUES (?,?,'worker',1,?,1,?,?,?,'before','finished')", (
                    task + "-input", "a", task + "-attempt", task + "-input-request", "synthetic input " + task,
                    "accepted" if status == "completed" else "held"))
            db.execute("INSERT INTO results VALUES ('done','a','worker',1,'text','synthetic output','ok',NULL,'finished','done-attempt')")
            db.execute("INSERT INTO outbox VALUES ('event','a',1,'held','worker','runtime_input_held','{}','held-input','pending','before',NULL)")
            db.execute("INSERT INTO migration_log VALUES (3,2,'before','synthetic-previous-backup')")
            db.commit()
            storage.verify_schema(db, version=3)

    def rows(self, path=None):
        with closing(sqlite3.connect(path or self.database)) as db:
            names = [row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
            return {name: db.execute('SELECT * FROM "' + name + '" ORDER BY rowid').fetchall() for name in names}

    def snapshot(self):
        return {str(path.relative_to(self.state)): path.read_bytes() for path in self.state.rglob("*") if path.is_file()}

    def assert_code(self, code, callback, *args):
        with self.assertRaises(PlatformError) as caught:
            callback(*args)
        self.assertEqual(caught.exception.code, code)

    def test_old_fixture_is_frozen_without_member_tables(self):
        with closing(sqlite3.connect(self.database)) as db:
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 3)
            tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertEqual(tables, set(storage.V3_COLUMNS))
            self.assertNotIn("member_sessions", tables)
            self.assertNotIn("attempt_members", tables)

    def test_schema_three_readers_require_explicit_migration_and_never_mutate(self):
        before = self.snapshot()
        for readonly in (False, True):
            self.assert_code("migration_required", lambda: Platform(self.state, readonly=readonly).status())
            self.assertEqual(before, self.snapshot())

    def test_upgrade_preserves_deployments_inputs_and_history_and_revokes_tokens(self):
        before = self.rows()
        result = storage.migrate_state(self.state)
        self.assertEqual(result["schema_version"], 4)
        self.assertTrue(result["tokens_revoked"])
        backup = Path(result["backup_path"])
        self.assertEqual(self.rows(backup), before)
        with closing(sqlite3.connect(backup)) as db:
            storage.verify_schema(db, version=3)
        after = self.rows()
        for table, values in before.items():
            if table not in ("actor_tokens", "migration_log"):
                self.assertEqual(after[table], values, table)
        self.assertEqual(after["actor_tokens"], [])
        self.assertEqual(after["migration_log"][:-1], before["migration_log"])
        self.assertEqual(after["migration_log"][-1][:2], (4, 3))
        self.assertEqual(after["member_sessions"], [])
        self.assertEqual(after["attempt_members"], [])
        with closing(sqlite3.connect(self.database)) as db:
            storage.verify_schema(db)
            self.assertEqual(db.execute("PRAGMA foreign_key_check").fetchall(), [])
        self.assertEqual(Platform(self.state).context("a", "worker")["workspace_access"], "read-only")

    def test_running_task_refuses_before_backup(self):
        with closing(sqlite3.connect(self.database)) as db:
            db.execute("UPDATE tasks SET state='running' WHERE task_id='held'")
            db.commit()
        before = self.snapshot()
        self.assert_code("migration_busy", storage.migrate_state, self.state)
        self.assertEqual(before, self.snapshot())

    def test_running_attempt_refuses_even_if_task_is_terminal(self):
        with closing(sqlite3.connect(self.database)) as db:
            db.execute("UPDATE attempts SET state='running' WHERE attempt_id='done-attempt'")
            db.commit()
        before = self.snapshot()
        self.assert_code("migration_busy", storage.migrate_state, self.state)
        self.assertEqual(before, self.snapshot())

    def test_invalid_input_reference_rolls_back_and_keeps_schema_three_backup(self):
        with closing(sqlite3.connect(self.database)) as db:
            db.execute("UPDATE attempt_inputs SET attempt_id='missing' WHERE input_id='done-input'")
            db.commit()
        before, old_rows = self.database.read_bytes(), self.rows()
        self.assert_code("migration_integrity", storage.migrate_state, self.state)
        self.assertEqual(before, self.database.read_bytes())
        backups = list((self.state / "backups").glob("*.sqlite3"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(self.rows(backups[0]), old_rows)

    def test_late_failure_rolls_back_tables_index_tokens_and_version(self):
        before = self.database.read_bytes()
        original = storage.verify_schema
        def fail(db, version=4):
            if version == 4:
                self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 4)
                self.assertEqual(db.execute("SELECT count(*) FROM actor_tokens").fetchone()[0], 0)
                raise RuntimeError("synthetic late failure")
            return original(db, version)
        with patch.object(storage, "verify_schema", side_effect=fail):
            with self.assertRaisesRegex(RuntimeError, "late failure"):
                storage.migrate_state(self.state)
        self.assertEqual(before, self.database.read_bytes())
        with closing(sqlite3.connect(self.database)) as db:
            storage.verify_schema(db, version=3)

    def test_current_member_uniqueness_index_is_verified(self):
        storage.migrate_state(self.state)
        with closing(sqlite3.connect(self.database)) as db:
            db.execute("DROP INDEX one_live_member")
            db.commit()
        self.assert_code("schema_corrupt", Platform, self.state)

    def test_second_upgrade_is_read_only_and_has_no_additional_backup(self):
        storage.migrate_state(self.state)
        before = self.snapshot()
        self.assertEqual(storage.migrate_state(self.state),
                         {"migrated": False, "schema_version": 4, "backup_path": None})
        self.assertEqual(before, self.snapshot())


if __name__ == "__main__":
    unittest.main()
