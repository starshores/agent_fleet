"""Schema 4 -> current routing migration from a frozen, synthetic source database."""
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


FIXTURE = Path(__file__).resolve().parent / "fixtures" / "schema_v4.sql"


class RoutingMigrationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="ma31-schema4-routing-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.state = self.root / "state"
        self.state.mkdir()
        self.database = self.state / "state.sqlite3"
        snapshot = {
            "manifest": {"package_id": "synthetic", "version": "1.0.0"},
            "roles": [{"role_id": "worker", "display_name": "Worker",
                       "instructions": "synthetic", "tools": ["text"]}],
            "knowledge": {}, "tools": {"text": {"adapter": "echo", "description": "synthetic"}},
            "workflows": {},
        }
        raw = json.dumps(snapshot, sort_keys=True)
        with closing(sqlite3.connect(self.database)) as db:
            db.executescript(FIXTURE.read_text(encoding="utf-8"))
            db.execute("INSERT INTO installations VALUES (?,?,?,?,?,?,?,?,?,?)", (
                "a", "synthetic", "1.0.0", "package-digest", raw,
                hashlib.sha256(raw.encode()).hexdigest(), str(self.root / "unused-workspace"),
                "active", 1, "before"))
            db.execute("INSERT INTO registrations VALUES ('a','worker',1,'before')")
            db.execute("INSERT INTO actor_tokens VALUES ('old-token','a','worker',1)")
            db.execute("INSERT INTO role_deployments VALUES ('a','worker',?,'none','before')",
                       (str(self.root / "unused-workspace"),))
            db.execute("INSERT INTO tasks VALUES ('done','a','worker',1,'synthetic text','old-request','completed','before','after',1,NULL)")
            db.execute("INSERT INTO attempts VALUES ('attempt','done','a','worker',1,1,'text','owner','lease',1,'completed','before','after')")
            db.execute("INSERT INTO results VALUES ('done','a','worker',1,'text','synthetic result','ok',NULL,'after','attempt')")
            db.execute("INSERT INTO attempt_inputs VALUES ('input','a','worker',1,'attempt',1,'input-request','synthetic input','accepted','before','after')")
            db.execute("INSERT INTO member_sessions VALUES ('member','a','worker',1,'owner','member-lease','deployment','text','stopped',1,'before','after','confirmed stopped')")
            db.execute("INSERT INTO attempt_members VALUES ('attempt','member')")
            for state in ("pending", "delivered", "held"):
                db.execute("INSERT INTO outbox VALUES (?,?,1,'done','worker','task_completed','{}',?,?,?,?)", (
                    "outbox-" + state, "a", "dedupe-" + state, state, "before",
                    "after" if state == "delivered" else None))
            db.execute("INSERT INTO migration_log VALUES (4,3,'before','synthetic-prior-backup')")
            db.commit()
            storage.verify_schema(db, version=4)

    def snapshot(self):
        return {str(path.relative_to(self.state)): path.read_bytes()
                for path in self.state.rglob("*") if path.is_file()}

    def rows(self, path=None):
        with closing(sqlite3.connect(path or self.database)) as db:
            names = [row[0] for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
            return {name: db.execute('SELECT * FROM "' + name + '" ORDER BY rowid').fetchall()
                    for name in names}

    def assert_code(self, code, callback, *args, **kwargs):
        with self.assertRaises(PlatformError) as caught:
            callback(*args, **kwargs)
        self.assertEqual(caught.exception.code, code)

    def test_schema_four_fixture_is_frozen_and_contains_no_routing_tables(self):
        with closing(sqlite3.connect(self.database)) as db:
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 4)
            tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertEqual(tables, set(storage.V4_COLUMNS))
            self.assertTrue(set(storage.COLUMNS) - tables)

    def test_reads_and_regular_open_do_not_migrate_or_make_backup(self):
        before = self.snapshot()
        for readonly in (True, False):
            with self.subTest(readonly=readonly):
                self.assert_code("migration_required", lambda: Platform(self.state, readonly=readonly).status())
                self.assertEqual(before, self.snapshot())
                self.assertFalse((self.state / "backups").exists())

    def test_upgrade_backs_up_all_history_without_fabricating_receipts(self):
        before = self.rows()
        result = storage.migrate_state(self.state)
        self.assertEqual(result["schema_version"], storage.SCHEMA_VERSION)
        self.assertTrue(result["tokens_revoked"])
        self.assertEqual(result["legacy_notifications"], "preserved")
        backup = Path(result["backup_path"])
        self.assertEqual(backup.parent, self.state / "backups")
        self.assertEqual(self.rows(backup), before)
        with closing(sqlite3.connect(backup)) as db:
            storage.verify_schema(db, version=4)
        after = self.rows()
        for table, rows in before.items():
            if table not in ("actor_tokens", "migration_log"):
                self.assertEqual(after[table], rows, table)
        self.assertEqual(after["actor_tokens"], [])
        self.assertEqual(after["migration_log"][:-1], before["migration_log"])
        self.assertEqual(after["migration_log"][-1][:2], (storage.SCHEMA_VERSION, 4))
        for table in set(storage.COLUMNS) - set(storage.V4_COLUMNS):
            self.assertEqual(after[table], [], table)
        with closing(sqlite3.connect(self.database)) as db:
            storage.verify_schema(db)
            self.assertEqual(db.execute("PRAGMA foreign_key_check").fetchall(), [])
        self.assertFalse((self.root / "unused-workspace").exists())

    def test_new_state_initializes_current_schema_only(self):
        root = self.root / "empty-state"
        Platform(root)
        with closing(sqlite3.connect(root / "state.sqlite3")) as db:
            storage.verify_schema(db)
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], storage.SCHEMA_VERSION)
            self.assertEqual(db.execute("SELECT count(*) FROM migration_log").fetchone()[0], 0)
        self.assertFalse((root / "backups").exists())

    def test_live_members_block_even_with_expired_lease(self):
        for state in ("ready", "waiting", "busy", "stopping"):
            with self.subTest(state=state):
                with closing(sqlite3.connect(self.database)) as db:
                    db.execute("UPDATE member_sessions SET state=?,expires_at=0", (state,))
                    db.commit()
                before = self.snapshot()
                self.assert_code("migration_busy", storage.migrate_state, self.state)
                self.assertEqual(before, self.snapshot())
                self.assertFalse((self.state / "backups").exists())

    def test_running_task_blocks_even_without_live_attempt(self):
        with closing(sqlite3.connect(self.database)) as db:
            db.execute("UPDATE tasks SET state='running'")
            db.commit()
        before = self.snapshot()
        self.assert_code("migration_busy", storage.migrate_state, self.state)
        self.assertEqual(before, self.snapshot())

    def test_running_attempt_blocks_even_with_expired_lease_and_terminal_task(self):
        with closing(sqlite3.connect(self.database)) as db:
            db.execute("UPDATE attempts SET state='running',expires_at=0")
            db.commit()
        before = self.snapshot()
        self.assert_code("migration_busy", storage.migrate_state, self.state)
        self.assertEqual(before, self.snapshot())

    def test_unresolved_inputs_block_without_changing_them(self):
        for state in ("pending", "sending", "held"):
            with self.subTest(state=state):
                with closing(sqlite3.connect(self.database)) as db:
                    db.execute("UPDATE attempt_inputs SET state=?", (state,))
                    db.commit()
                before = self.snapshot()
                self.assert_code("migration_busy", storage.migrate_state, self.state)
                self.assertEqual(before, self.snapshot())
                self.assertFalse((self.state / "backups").exists())

    def test_invalid_foreign_key_rolls_back_and_keeps_consistent_backup(self):
        with closing(sqlite3.connect(self.database)) as db:
            db.execute("UPDATE attempt_members SET member_id='missing'")
            db.commit()
        before, old_rows = self.database.read_bytes(), self.rows()
        self.assert_code("migration_integrity", storage.migrate_state, self.state)
        self.assertEqual(before, self.database.read_bytes())
        backups = list((self.state / "backups").glob("*.sqlite3"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(self.rows(backups[0]), old_rows)

    def test_late_failure_restores_tables_indexes_tokens_and_version(self):
        before, old_rows = self.database.read_bytes(), self.rows()
        original = storage.verify_schema
        def fail_final_check(db, version=storage.SCHEMA_VERSION):
            if version == storage.SCHEMA_VERSION:
                self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], storage.SCHEMA_VERSION)
                self.assertEqual(db.execute("SELECT count(*) FROM actor_tokens").fetchone()[0], 0)
                self.assertEqual(set(self.rows()) - set(storage.V4_COLUMNS), set())
                raise RuntimeError("synthetic routing migration failure")
            return original(db, version)
        with patch.object(storage, "verify_schema", side_effect=fail_final_check):
            with self.assertRaisesRegex(RuntimeError, "synthetic routing migration failure"):
                storage.migrate_state(self.state)
        self.assertEqual(before, self.database.read_bytes())
        self.assertEqual(old_rows, self.rows())
        with closing(sqlite3.connect(self.database)) as db:
            storage.verify_schema(db, version=4)
        backups = list((self.state / "backups").glob("*.sqlite3"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(self.rows(backups[0]), old_rows)

    def test_corrupt_constraint_is_rejected_before_backup(self):
        with closing(sqlite3.connect(self.database)) as db:
            db.execute("PRAGMA writable_schema=ON")
            row = db.execute("SELECT sql FROM sqlite_master WHERE name='member_sessions'").fetchone()[0]
            db.execute("UPDATE sqlite_master SET sql=? WHERE name='member_sessions'",
                       (row.replace("owner_hash TEXT NOT NULL", "owner_hash TEXT"),))
            db.commit()
        before = self.snapshot()
        self.assert_code("schema_corrupt", storage.migrate_state, self.state)
        self.assertEqual(before, self.snapshot())
        self.assertFalse((self.state / "backups").exists())

    def test_new_ledger_index_is_required(self):
        storage.migrate_state(self.state)
        indexes = set(storage.INDEXES) - set(storage.V4_INDEXES)
        self.assertTrue(indexes)
        name = sorted(indexes)[0]
        with closing(sqlite3.connect(self.database)) as db:
            db.execute('DROP INDEX "' + name + '"')
            db.commit()
        self.assert_code("schema_corrupt", Platform, self.state)

    def test_unknown_trigger_refuses_before_backup(self):
        with closing(sqlite3.connect(self.database)) as db:
            db.execute("CREATE TRIGGER unknown_mutator AFTER DELETE ON actor_tokens BEGIN DELETE FROM results; END")
            db.commit()
        before = self.snapshot()
        self.assert_code("schema_corrupt", storage.migrate_state, self.state)
        self.assertEqual(before, self.snapshot())

    def test_unknown_index_refuses_before_backup(self):
        with closing(sqlite3.connect(self.database)) as db:
            db.execute("CREATE UNIQUE INDEX unknown_restriction ON tasks(text)")
            db.commit()
        before = self.snapshot()
        self.assert_code("schema_corrupt", storage.migrate_state, self.state)
        self.assertEqual(before, self.snapshot())

    def test_backup_failure_rolls_back_without_revoking_credentials(self):
        before, old_rows = self.database.read_bytes(), self.rows()
        connect = sqlite3.connect
        class BrokenBackupConnection(sqlite3.Connection):
            def backup(self, target, *args, **kwargs):
                raise RuntimeError("synthetic backup failure")
        def connect_with_backup_failure(path, *args, **kwargs):
            if str(path).endswith("?mode=ro"):
                kwargs["factory"] = BrokenBackupConnection
            return connect(path, *args, **kwargs)
        with patch.object(storage.sqlite3, "connect", side_effect=connect_with_backup_failure):
            with self.assertRaisesRegex(RuntimeError, "synthetic backup failure"):
                storage.migrate_state(self.state)
        self.assertEqual(before, self.database.read_bytes())
        self.assertEqual(old_rows, self.rows())

    def test_write_reservation_prevents_other_writer_during_backup(self):
        connect, path = sqlite3.connect, self.database
        observer = self
        class ObservedBackupConnection(sqlite3.Connection):
            def backup(self, target, *args, **kwargs):
                with closing(connect(path, timeout=0)) as competing:
                    with observer.assertRaisesRegex(sqlite3.OperationalError, "locked"):
                        competing.execute("UPDATE tasks SET text='other writer'")
                return super().backup(target, *args, **kwargs)
        def connect_with_observed_backup(path, *args, **kwargs):
            if str(path).endswith("?mode=ro"):
                kwargs["factory"] = ObservedBackupConnection
            return connect(path, *args, **kwargs)
        with patch.object(storage.sqlite3, "connect", side_effect=connect_with_observed_backup):
            result = storage.migrate_state(self.state)
        with closing(connect(result["backup_path"])) as db:
            self.assertEqual(db.execute("SELECT text FROM tasks").fetchone()[0], "synthetic text")

    def test_current_database_rejects_schema_four_opener(self):
        storage.migrate_state(self.state)
        before = self.database.read_bytes()
        with closing(sqlite3.connect(self.database)) as db:
            self.assert_code("unsupported_schema", storage.verify_schema, db, version=4)
        self.assertEqual(before, self.database.read_bytes())

    def test_backup_restore_into_another_root_does_not_overwrite_new_history(self):
        result = storage.migrate_state(self.state)
        with closing(sqlite3.connect(self.database)) as db:
            db.execute("INSERT INTO tasks VALUES ('new','a','worker',1,'synthetic new text',NULL,'queued','after',NULL,1,NULL)")
            db.commit()
        before = self.database.read_bytes()
        restored = self.root / "restored"
        restored.mkdir()
        restored_db = restored / "state.sqlite3"
        restored_db.write_bytes(Path(result["backup_path"]).read_bytes())
        with closing(sqlite3.connect(restored_db)) as db:
            storage.verify_schema(db, version=4)
            self.assertIsNone(db.execute("SELECT 1 FROM tasks WHERE task_id='new'").fetchone())
        self.assertEqual(before, self.database.read_bytes())
        with closing(sqlite3.connect(self.database)) as db:
            storage.verify_schema(db)
            self.assertIsNotNone(db.execute("SELECT 1 FROM tasks WHERE task_id='new'").fetchone())

    def test_second_upgrade_creates_no_backup_and_does_not_mutate(self):
        storage.migrate_state(self.state)
        before = self.snapshot()
        self.assertEqual(storage.migrate_state(self.state),
                         {"migrated": False, "schema_version": storage.SCHEMA_VERSION, "backup_path": None})
        self.assertEqual(before, self.snapshot())


if __name__ == "__main__":
    unittest.main()
