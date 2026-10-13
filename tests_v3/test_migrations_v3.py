"""Schema 2 -> current migrations from frozen dev.3 SQL and synthetic data only."""
from contextlib import ExitStack, closing
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from masteragent.errors import PlatformError
from masteragent.service import Actor, Platform
from masteragent import storage


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests_v3" / "fixtures" / "schema_v2.sql"


class SchemaThreeMigrationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="ma3-schema2-upgrade-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.state = self.root / "state"
        self.state.mkdir()
        self.database = self.state / "state.sqlite3"
        self.workspaces = {name: self.root / (name + "-business") for name in ("a", "b", "c")}
        for workspace in self.workspaces.values():
            workspace.mkdir()
        roles = [{"role_id": name, "display_name": name, "instructions": "synthetic instructions",
                  "tools": ["text"]} for name in ("reviewer", "editor")]
        self.snapshot = {"manifest": {"package_id": "synthetic", "version": "1.0.0"},
                         "roles": roles, "knowledge": {"reference": "synthetic knowledge"},
                         "tools": {"text": {"adapter": "echo", "description": "synthetic capability"}},
                         "workflows": {}}
        raw = json.dumps(self.snapshot, sort_keys=True, separators=(",", ":"))
        with closing(sqlite3.connect(self.database)) as db:
            db.executescript(FIXTURE.read_text(encoding="utf-8"))
            for name, state in (("a", "active"), ("b", "inactive"), ("c", "uninstalled")):
                db.execute("INSERT INTO installations VALUES (?,?,?,?,?,?,?,?,?,?)", (
                    name, "synthetic", "1.0.0", "synthetic-package-digest", raw,
                    hashlib.sha256(raw.encode()).hexdigest(), str(self.workspaces[name]), state, 1, "before"))
            db.execute("INSERT INTO registrations VALUES ('a','reviewer',1,'before')")
            db.execute("INSERT INTO actor_tokens VALUES (?,'a','reviewer',1)",
                       (hashlib.sha256(b"synthetic-old-token").hexdigest(),))
            tasks = (("queued", "queued", "reviewer", None),
                     ("completed", "completed", "reviewer", None),
                     ("uncertain", "needs_reconcile", "reviewer", None),
                     ("parent", "waiting", "reviewer", None),
                     ("child", "completed", "editor", "parent"))
            for task_id, state, role, parent in tasks:
                db.execute("INSERT INTO tasks VALUES (?,?,?,?,?,?,?,?,?,?,?)", (
                    task_id, "a", role, 1, "synthetic " + task_id, task_id + "-request",
                    state, "before", "finished" if state == "completed" else None, 1, parent))
            for task_id, role, state in (("completed", "reviewer", "completed"),
                                        ("uncertain", "reviewer", "expired"),
                                        ("child", "editor", "completed")):
                db.execute("INSERT INTO attempts VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", (
                    task_id + "-attempt", task_id, "a", role, 1, 1, "text", "owner-hash",
                    "lease-hash", 123456.0, state, "before", "finished"))
            for task_id, role in (("completed", "reviewer"), ("child", "editor")):
                db.execute("INSERT INTO results VALUES (?,?,?,?,?,?,?,?,?,?)", (
                    task_id, "a", role, 1, "text", "synthetic result", "ok", None, "finished", task_id + "-attempt"))
            db.execute("INSERT INTO notifications VALUES ('notice','completed','a','reviewer',1,'task_completed','before')")
            db.execute("INSERT INTO attempt_events VALUES ('event','uncertain-attempt','a','runtime_uncertain','{}','before')")
            db.execute("INSERT INTO continuations VALUES ('continuation','queued','a',0,1,'synthetic continuation','before')")
            db.execute("INSERT INTO decisions VALUES ('decision','a','parent',1,'decision-request','synthetic question','pending',1,NULL,NULL,NULL,'before',NULL)")
            for index, state in enumerate(("pending", "delivered", "held")):
                db.execute("INSERT INTO outbox VALUES (?,?,?,?,?,?,?,?,?,?,?)", (
                    "outbox-" + state, "a", 1, "completed", "reviewer", "task_completed",
                    '{"synthetic":true}', "dedupe-" + str(index), state, "before",
                    "delivered" if state == "delivered" else None))
            db.execute("INSERT INTO requests VALUES ('request','a',1,'parent','child','reviewer','editor','child-request','synthetic request','replied','before','finished')")
            db.execute("INSERT INTO migration_log VALUES (2,1,'before','synthetic-old-backup')")
            db.commit()
            storage.verify_schema(db, version=2)
            self.assertEqual(db.execute("PRAGMA foreign_key_check").fetchall(), [])

    def filesystem_snapshot(self):
        return {str(path.relative_to(self.state)): path.read_bytes()
                for path in self.state.rglob("*") if path.is_file()}

    def rows(self, database=None):
        with closing(sqlite3.connect(database or self.database)) as db:
            tables = [row[0] for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
            return {name: db.execute('SELECT * FROM "' + name + '" ORDER BY rowid').fetchall() for name in tables}

    def assert_error(self, code, callback, *args):
        with self.assertRaises(PlatformError) as caught:
            callback(*args)
        self.assertEqual(caught.exception.code, code)

    def assert_source_unchanged(self, before):
        self.assertEqual(self.database.read_bytes(), before)
        with closing(sqlite3.connect(self.database)) as db:
            storage.verify_schema(db, version=2)
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 2)
            self.assertEqual(db.execute("SELECT count(*) FROM actor_tokens").fetchone()[0], 1)

    def test_frozen_fixture_is_genuine_schema_two(self):
        with closing(sqlite3.connect(self.database)) as db:
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 2)
            names = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertNotIn("role_deployments", names)
            self.assertNotIn("attempt_inputs", names)
            self.assertIn("attempts", names)
            self.assertIn("outbox", names)

    def test_regular_open_requires_explicit_migration_without_writes(self):
        before = self.filesystem_snapshot()
        for readonly in (True, False):
            with self.subTest(readonly=readonly):
                self.assert_error("migration_required", lambda: Platform(self.state, readonly=readonly).status())
                self.assertEqual(self.filesystem_snapshot(), before)
                self.assertFalse((self.state / "backups").exists())

    def test_upgrade_backs_up_defaults_every_role_and_preserves_history(self):
        old_rows = self.rows()
        result = storage.migrate_state(self.state)
        self.assertTrue(result["migrated"])
        self.assertEqual(result["schema_version"], 4)
        self.assertTrue(result["tokens_revoked"])
        self.assertEqual(result["legacy_notifications"], "preserved")
        backup = Path(result["backup_path"])
        self.assertEqual(backup.parent, self.state / "backups")
        with closing(sqlite3.connect(backup)) as db:
            storage.verify_schema(db, version=2)
        self.assertEqual(self.rows(backup), old_rows)
        upgraded = self.rows()
        for table, values in old_rows.items():
            if table not in {"actor_tokens", "migration_log"}:
                self.assertEqual(upgraded[table], values, table)
        self.assertEqual(upgraded["actor_tokens"], [])
        self.assertEqual(upgraded["migration_log"][0], old_rows["migration_log"][0])
        self.assertEqual(upgraded["migration_log"][-1][:2], (4, 2))
        self.assertEqual(upgraded["attempt_inputs"], [])
        expected = {(name, role, str(workspace), "none")
                    for name, workspace in self.workspaces.items() for role in ("reviewer", "editor")}
        self.assertEqual({row[:4] for row in upgraded["role_deployments"]}, expected)
        platform = Platform(self.state)
        self.assert_error("invalid_actor", platform.context_for,
                          Actor("a", "reviewer", 1, "synthetic-old-token"))
        self.assertEqual(platform.context("a", "reviewer")["workspace_access"], "none")
        self.assertEqual(len(platform.history("a")["tasks"]), 5)
        with closing(sqlite3.connect(self.database)) as db:
            storage.verify_schema(db)
            self.assertEqual(db.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_running_task_without_running_attempt_refuses_before_backup(self):
        with closing(sqlite3.connect(self.database)) as db:
            db.execute("UPDATE tasks SET state='running' WHERE task_id='queued'")
            db.commit()
        before = self.filesystem_snapshot()
        self.assert_error("migration_busy", storage.migrate_state, self.state)
        self.assertEqual(self.filesystem_snapshot(), before)
        self.assertFalse((self.state / "backups").exists())

    def test_running_attempt_without_running_task_refuses_before_backup(self):
        with closing(sqlite3.connect(self.database)) as db:
            db.execute("UPDATE attempts SET state='running',finished_at=NULL WHERE attempt_id='completed-attempt'")
            db.commit()
        before = self.filesystem_snapshot()
        self.assert_error("migration_busy", storage.migrate_state, self.state)
        self.assertEqual(self.filesystem_snapshot(), before)
        self.assertFalse((self.state / "backups").exists())

    def test_snapshot_digest_failure_rolls_back_and_keeps_old_backup(self):
        with closing(sqlite3.connect(self.database)) as db:
            db.execute("UPDATE installations SET snapshot='{}' WHERE installation_id='b'")
            db.commit()
        before, before_rows = self.database.read_bytes(), self.rows()
        self.assert_error("invalid_snapshot", storage.migrate_state, self.state)
        self.assert_source_unchanged(before)
        backups = list((self.state / "backups").glob("*.sqlite3"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(self.rows(backups[0]), before_rows)

    def test_bad_snapshot_roles_roll_back_even_when_digest_matches(self):
        value = {**self.snapshot, "roles": [{"role_id": "reviewer"}, {"role_id": "reviewer"}]}
        raw = json.dumps(value)
        with closing(sqlite3.connect(self.database)) as db:
            db.execute("UPDATE installations SET snapshot=?,snapshot_digest=? WHERE installation_id='b'",
                       (raw, hashlib.sha256(raw.encode()).hexdigest()))
            db.commit()
        before = self.database.read_bytes()
        self.assert_error("invalid_snapshot", storage.migrate_state, self.state)
        self.assert_source_unchanged(before)

    def test_late_failure_rolls_back_schema_tokens_version_and_data(self):
        before, old_rows = self.database.read_bytes(), self.rows()
        verify = storage.verify_schema
        def fail_final_check(db, version=4):
            if version == 4:
                self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 4)
                self.assertEqual(db.execute("SELECT count(*) FROM actor_tokens").fetchone()[0], 0)
                self.assertEqual(db.execute("SELECT count(*) FROM role_deployments").fetchone()[0], 6)
                raise RuntimeError("synthetic late migration failure")
            return verify(db, version=version)
        with patch.object(storage, "verify_schema", side_effect=fail_final_check):
            with self.assertRaisesRegex(RuntimeError, "synthetic late migration failure"):
                storage.migrate_state(self.state)
        self.assert_source_unchanged(before)
        self.assertEqual(self.rows(), old_rows)
        self.assertEqual(len(list((self.state / "backups").glob("*.sqlite3"))), 1)

    def test_invalid_foreign_key_rolls_back_the_complete_upgrade(self):
        with closing(sqlite3.connect(self.database)) as db:
            db.execute("UPDATE results SET attempt_id='missing-attempt' WHERE task_id='completed'")
            db.commit()
        before = self.database.read_bytes()
        self.assert_error("migration_integrity", storage.migrate_state, self.state)
        self.assert_source_unchanged(before)

    def test_migration_never_reads_or_enumerates_business_directory_contents(self):
        roots = set(self.workspaces.values())
        for workspace in roots:
            (workspace / "synthetic-private.txt").write_text("unread synthetic bait", encoding="utf-8")
        with ExitStack() as stack:
            for name in ("open", "read_text", "read_bytes", "iterdir", "glob", "rglob"):
                original = getattr(Path, name)
                def guarded(path, *args, _original=original, **kwargs):
                    if any(path == root or root in path.parents for root in roots):
                        raise AssertionError("Migration touched business directory contents")
                    return _original(path, *args, **kwargs)
                stack.enter_context(patch.object(Path, name, guarded))
            result = storage.migrate_state(self.state)
        self.assertTrue(result["migrated"])
        for workspace in roots:
            self.assertEqual((workspace / "synthetic-private.txt").read_text(encoding="utf-8"), "unread synthetic bait")

    def test_missing_old_workspace_does_not_prevent_metadata_migration(self):
        self.workspaces["c"].rmdir()
        self.assertTrue(storage.migrate_state(self.state)["migrated"])
        self.assertFalse(self.workspaces["c"].exists())
        bindings = Platform(self.state).view("c")["role_deployments"]
        self.assertEqual({row["workspace"] for row in bindings.values()}, {str(self.workspaces["c"])})

    def test_second_migration_is_idempotent_without_another_backup(self):
        storage.migrate_state(self.state)
        before = self.filesystem_snapshot()
        self.assertEqual(storage.migrate_state(self.state),
                         {"migrated": False, "schema_version": 4, "backup_path": None})
        self.assertEqual(self.filesystem_snapshot(), before)


if __name__ == "__main__":
    unittest.main()
