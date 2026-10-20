"""All data below are artificial fixtures; never use a real agent home."""
import contextlib
import importlib.util
import json
from pathlib import Path
import sqlite3
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock


SPEC = importlib.util.spec_from_file_location("session_cleanup", Path(__file__).parents[1] / "scripts" / "session_cleanup.py")
cleanup = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(cleanup)
SID = "11111111-2222-3333-4444-555555555555"


class CleanupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="session-cleanup-fixture-")
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name).resolve()
        self.project = self.base / "project"
        self.codex = self.base / "codex"
        self.claude = self.base / "claude"
        self.projects = self.project / "projects"
        for root in (self.project, self.codex, self.claude, self.projects):
            root.mkdir(exist_ok=True)

    def write(self, root, name, value="synthetic only"):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value, encoding="utf-8")
        return path

    def db(self, name, sql):
        path = self.codex / name
        with contextlib.closing(sqlite3.connect(path)) as db:
            db.executescript(sql)
        return path

    def plan(self):
        return cleanup.make_plan(self.project, [self.codex], [self.claude], [self.projects])

    def apply(self, plan=None, **kwargs):
        return cleanup.apply_plan(plan or self.plan(), guard=lambda: None, **kwargs)

    def test_preview_is_read_only_and_protected_content_is_not_in_plan(self):
        session = self.write(self.codex, "sessions/day/rollout.jsonl")
        for root, name in ((self.codex, "auth.json"), (self.codex, "config.toml"),
                           (self.codex, "memories/a.md"), (self.claude, ".credentials.json"),
                           (self.claude, "settings.json"), (self.projects, "business/memory/a.md")):
            self.write(root, name, "preserve synthetic")
        before = {str(p): p.read_bytes() for p in self.base.rglob("*") if p.is_file()}
        plan = self.plan()
        self.assertEqual([str(session)], [x["path"] for x in plan["files"]])
        self.assertEqual(before, {str(p): p.read_bytes() for p in self.base.rglob("*") if p.is_file()})
        self.assertFalse((self.project / "maintenance").exists())

    def test_move_hash_restore_and_purge(self):
        path = self.write(self.codex, "sessions/day/rollout.jsonl", "你好\n" + "x" * 30000)
        before = path.read_bytes()
        archive = self.apply()
        self.assertFalse(path.exists())
        manifest = json.loads((archive / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual("applied", manifest["status"])
        self.assertEqual(cleanup.digest(archive / "000000.bin"), manifest["operations"][0]["sha256"])
        cleanup.restore_archive(archive, guard=lambda: None)
        self.assertEqual(before, path.read_bytes())
        cleanup.purge_archive(archive, confirmation=archive.name, guard=lambda: None)
        self.assertFalse(archive.exists())
        self.assertEqual(before, path.read_bytes())

    def test_mixed_database_keeps_projects_and_configuration(self):
        path = self.db("state_5.sqlite", """
        CREATE TABLE projects(id TEXT PRIMARY KEY);
        CREATE TABLE threads(id TEXT PRIMARY KEY, project_id TEXT REFERENCES projects(id));
        CREATE TABLE thread_attachments(thread_id TEXT REFERENCES threads(id));
        CREATE TABLE remote_control_enrollments(id TEXT);
        INSERT INTO projects VALUES('project');
        INSERT INTO threads VALUES('thread','project');
        INSERT INTO thread_attachments VALUES('thread');
        INSERT INTO remote_control_enrollments VALUES('configuration');
        """)
        original = path.read_bytes()
        archive = self.apply()
        with contextlib.closing(sqlite3.connect(path)) as db:
            self.assertEqual(0, db.execute("SELECT count(*) FROM threads").fetchone()[0])
            self.assertEqual(0, db.execute("SELECT count(*) FROM thread_attachments").fetchone()[0])
            self.assertEqual(1, db.execute("SELECT count(*) FROM projects").fetchone()[0])
            self.assertEqual(1, db.execute("SELECT count(*) FROM remote_control_enrollments").fetchone()[0])
        cleanup.restore_archive(archive, guard=lambda: None)
        self.assertEqual(original, path.read_bytes())

    def test_related_databases_and_indexes_are_cleaned_together(self):
        self.db("thread_history_1.sqlite", "CREATE TABLE thread_items(thread_id TEXT); INSERT INTO thread_items VALUES('t');")
        self.db("goals_1.sqlite", "CREATE TABLE thread_goals(thread_id TEXT); INSERT INTO thread_goals VALUES('t');")
        logs = self.db("logs_2.sqlite", "CREATE TABLE logs(thread_id TEXT,body TEXT); INSERT INTO logs VALUES('t','chat'),(NULL,'system');")
        self.db("queue_1.sqlite", "")
        memory = self.db("memories_1.sqlite", "CREATE TABLE stage1_outputs(thread_id TEXT); INSERT INTO stage1_outputs VALUES('keep');")
        memory_bytes = memory.read_bytes()
        for name in ("history.jsonl", "session_index.jsonl", "archived_sessions/x.jsonl"):
            self.write(self.codex, name)
        global_file = self.write(self.codex, ".codex-global-state.json", json.dumps({
            "thread-project-assignments": {"t": "p"}, "local-projects": ["p"],
            "electron-persisted-atom-state": {"unknown": "preserve"}}))
        self.apply()
        with contextlib.closing(sqlite3.connect(logs)) as db:
            self.assertEqual([(None, "system")], db.execute("SELECT * FROM logs").fetchall())
        self.assertEqual(memory_bytes, memory.read_bytes())
        value = json.loads(global_file.read_text(encoding="utf-8"))
        self.assertNotIn("thread-project-assignments", value)
        self.assertEqual(["p"], value["local-projects"])
        self.assertEqual({"unknown": "preserve"}, value["electron-persisted-atom-state"])
        self.assertFalse((self.codex / "session_index.jsonl").exists())

    def test_claude_project_sessions_keep_memory_and_business_files(self):
        session = self.write(self.projects, f"business/{SID}.jsonl")
        subagent = self.write(self.projects, f"business/{SID}/subagents/agent-a.jsonl")
        result = self.write(self.projects, f"business/{SID}/tool-results/a.txt")
        index = self.write(self.projects, "business/sessions-index.json")
        memory = self.write(self.projects, "business/memory/MEMORY.md")
        config = self.write(self.projects, "business/settings.json")
        business = self.write(self.projects, "business/work.py")
        self.apply()
        for path in (session, subagent, result, index):
            self.assertFalse(path.exists())
        for path in (memory, config, business):
            self.assertTrue(path.exists())
        self.assertTrue((self.projects / "business").is_dir())

    def test_multiple_native_homes_preserve_credentials(self):
        native = self.base / "dedicated-native-home"
        native.mkdir()
        self.write(native, "sessions/x.jsonl")
        auth = self.write(native, "auth.json", "synthetic authentication")
        plan = cleanup.make_plan(self.project, [self.codex, native])
        self.apply(plan)
        self.assertTrue(auth.exists())
        self.assertFalse((native / "sessions/x.jsonl").exists())

    def test_stale_plan_rejected(self):
        path = self.write(self.codex, "history.jsonl")
        plan = self.plan()
        path.write_text("new synthetic record", encoding="utf-8")
        with self.assertRaisesRegex(cleanup.CleanupError, "stale"):
            self.apply(plan)
        self.assertEqual("new synthetic record", path.read_text(encoding="utf-8"))

    def test_new_session_after_plan_rejected(self):
        plan = self.plan()
        self.write(self.codex, "sessions/new.jsonl")
        with self.assertRaisesRegex(cleanup.CleanupError, "stale"):
            self.apply(plan)

    def test_root_and_user_home_rejected(self):
        for root in (Path(self.base.anchor), Path.home(), self.project):
            with self.assertRaises(cleanup.CleanupError):
                cleanup.make_plan(self.project, [root])
        with self.assertRaises(cleanup.CleanupError):
            cleanup.make_plan("relative", [self.codex])

    def test_network_device_and_alternate_stream_paths_rejected(self):
        for path in (r"\\server\share\codex", "//server/share/codex", r"\\?\C:\codex",
                     r"\\.\C:\codex", "C:/codex/file:stream"):
            with self.assertRaisesRegex(cleanup.CleanupError, "UNC"):
                cleanup.absolute(path, existing=False)

    def test_restore_rechecks_after_lock_and_preserves_new_file(self):
        path = self.write(self.codex, "history.jsonl")
        archive = self.apply()
        calls = 0
        def guard():
            nonlocal calls
            calls += 1
            if calls == 2:
                path.write_text("new content during lock", encoding="utf-8")
        with self.assertRaisesRegex(cleanup.CleanupError, "changed"):
            cleanup.restore_archive(archive, guard=guard)
        self.assertEqual("new content during lock", path.read_text(encoding="utf-8"))

    def test_restore_rechecks_new_database_sidecar_after_lock(self):
        path = self.db("state_5.sqlite", "CREATE TABLE threads(id TEXT); INSERT INTO threads VALUES('a');")
        archive = self.apply()
        current = path.read_bytes()
        calls = 0
        def guard():
            nonlocal calls
            calls += 1
            if calls == 2:
                Path(str(path) + "-wal").write_bytes(b"synthetic new sidecar")
        with self.assertRaisesRegex(cleanup.CleanupError, "sidecar"):
            cleanup.restore_archive(archive, guard=guard)
        self.assertEqual(current, path.read_bytes())

    def test_apply_rechecks_source_after_backup(self):
        path = self.write(self.codex, "history.jsonl")
        real_copy = cleanup.prepare_copy
        def replace_source(source, temporary, expected, created):
            real_copy(source, temporary, expected, created)
            if source == path:
                source.unlink()
                source.write_text("new content after backup", encoding="utf-8")
        with mock.patch.object(cleanup, "prepare_copy", replace_source):
            with self.assertRaisesRegex(cleanup.CleanupError, "interrupted"):
                self.apply()
        self.assertEqual("new content after backup", path.read_text(encoding="utf-8"))

    def test_partial_backup_does_not_block_restoring_completed_backups(self):
        first = self.write(self.codex, "history.jsonl", "first original")
        second = self.write(self.codex, "session_index.jsonl", "second original")
        original_copy = shutil.copyfileobj
        calls = 0
        def fail_second(source, destination, length):
            nonlocal calls
            calls += 1
            if calls == 2:
                destination.write(b"partial")
                raise OSError("synthetic interrupted write")
            return original_copy(source, destination, length)
        with mock.patch.object(cleanup.shutil, "copyfileobj", fail_second):
            with self.assertRaisesRegex(cleanup.CleanupError, "interrupted"):
                self.apply()
        self.assertFalse(first.exists())
        self.assertEqual("second original", second.read_text(encoding="utf-8"))
        archive = next(cleanup.quarantine_root(self.project).iterdir())
        self.assertFalse((archive / "000001.bin").exists())
        cleanup.restore_archive(archive, guard=lambda: None)
        self.assertEqual("first original", first.read_text(encoding="utf-8"))
        self.assertEqual("second original", second.read_text(encoding="utf-8"))
        cleanup.purge_archive(archive, confirmation=archive.name, guard=lambda: None)

    def test_partial_replacement_never_overwrites_original(self):
        path = self.write(self.codex, ".codex-global-state.json", '{"projectless-thread-ids":["synthetic"]}')
        original = path.read_bytes()
        original_copy = shutil.copyfileobj
        calls = 0
        def fail_second(source, destination, length):
            nonlocal calls
            calls += 1
            if calls == 2:
                destination.write(b"partial")
                raise OSError("synthetic interrupted replacement")
            return original_copy(source, destination, length)
        with mock.patch.object(cleanup.shutil, "copyfileobj", fail_second):
            with self.assertRaisesRegex(cleanup.CleanupError, "interrupted"):
                self.apply()
        self.assertEqual(original, path.read_bytes())
        archive = next(cleanup.quarantine_root(self.project).iterdir())
        cleanup.restore_archive(archive, guard=lambda: None)
        self.assertEqual(original, path.read_bytes())
        self.assertEqual([], list(self.codex.glob(".session-cleanup-*")))

    def test_missing_backup_and_original_cannot_be_reported_restored(self):
        self.write(self.codex, "history.jsonl", "original")
        archive = self.apply()
        path = archive / "manifest.json"
        manifest = json.loads(path.read_text(encoding="utf-8"))
        manifest["status"] = "interrupted"
        cleanup.json_write(path, manifest)
        (archive / manifest["operations"][0]["backup"]).unlink()
        with self.assertRaisesRegex(cleanup.CleanupError, "no verified original"):
            cleanup.restore_archive(archive, guard=lambda: None)
        self.assertEqual("interrupted", json.loads(path.read_text(encoding="utf-8"))["status"])
        with self.assertRaises(cleanup.CleanupError):
            cleanup.purge_archive(archive, confirmation=archive.name, guard=lambda: None)

    def test_manifest_hardlink_is_rejected_before_read(self):
        self.write(self.codex, "history.jsonl")
        archive = self.apply()
        cleanup.os.link(archive / "manifest.json", self.base / "manifest-hardlink")
        with self.assertRaisesRegex(cleanup.CleanupError, "manifest file"):
            cleanup.load_archive(archive)

    def test_outside_temporary_intent_is_rejected_before_hashing(self):
        self.write(self.codex, "history.jsonl")
        archive = self.apply()
        _, manifest = cleanup.load_archive(archive)
        outside = self.write(self.base, ".session-cleanup-" + archive.name + "-" + "a" * 32 + ".tmp")
        manifest["temporary_intents"] = [str(outside)]
        with mock.patch.object(cleanup, "digest", side_effect=AssertionError("must not read outside intent")):
            with self.assertRaisesRegex(cleanup.CleanupError, "temporary path"):
                cleanup.clean_temporary_residue(archive, manifest)
        self.assertTrue(outside.exists())

    def test_partial_restore_is_retryable_without_partial_destination(self):
        path = self.write(self.codex, "history.jsonl", "original")
        archive = self.apply()
        def fail_copy(source, destination, length):
            destination.write(b"partial")
            raise OSError("synthetic interrupted restore")
        with mock.patch.object(cleanup.shutil, "copyfileobj", fail_copy):
            with self.assertRaises(OSError):
                cleanup.restore_archive(archive, guard=lambda: None)
        self.assertFalse(path.exists())
        cleanup.restore_archive(archive, guard=lambda: None)
        self.assertEqual("original", path.read_text(encoding="utf-8"))
        self.assertEqual([], list(self.codex.glob(".session-cleanup-*")))

    def test_restore_publication_refuses_a_new_destination(self):
        path = self.write(self.codex, "history.jsonl")
        archive = self.apply()
        original_publish = cleanup.publish_new
        def conflict(temporary, destination, expected):
            if destination == path:
                path.write_text("new concurrent content", encoding="utf-8")
            return original_publish(temporary, destination, expected)
        with mock.patch.object(cleanup, "publish_new", conflict):
            with self.assertRaisesRegex(cleanup.CleanupError, "not be overwritten"):
                cleanup.restore_archive(archive, guard=lambda: None)
        self.assertEqual("new concurrent content", path.read_text(encoding="utf-8"))

    def test_duplicate_archive_operations_rejected(self):
        self.write(self.codex, "history.jsonl")
        self.write(self.codex, "session_index.jsonl")
        archive = self.apply()
        path = archive / "manifest.json"
        value = json.loads(path.read_text(encoding="utf-8"))
        value["operations"][1] = dict(value["operations"][0])
        path.write_text(json.dumps(value), encoding="utf-8")
        with self.assertRaisesRegex(cleanup.CleanupError, "operations"):
            cleanup.restore_archive(archive, guard=lambda: None)

    def test_purge_interruption_retains_journal_and_can_resume(self):
        self.write(self.codex, "history.jsonl")
        self.write(self.codex, "session_index.jsonl")
        archive = self.apply()
        original_write = cleanup.json_write
        def interrupt_after_delete(path, value, **kwargs):
            if value.get("status") == "purging" and len(value.get("purge_deleted", [])) == 1:
                raise OSError("synthetic journal write interruption")
            return original_write(path, value, **kwargs)
        with mock.patch.object(cleanup, "json_write", interrupt_after_delete):
            with self.assertRaises(OSError):
                cleanup.purge_archive(archive, confirmation=archive.name, guard=lambda: None)
        self.assertTrue((archive / "manifest.json").exists())
        self.assertFalse((archive / "000000.bin").exists())
        cleanup.purge_archive(archive, confirmation=archive.name, guard=lambda: None)
        self.assertFalse(archive.exists())

    def test_abandoned_manifest_temporary_does_not_block_restore(self):
        path = self.write(self.codex, "history.jsonl")
        archive = self.apply()
        manifest = json.loads((archive / "manifest.json").read_text(encoding="utf-8"))
        owned = archive / (".manifest.json." + manifest["manifest_id"] + "." + "a" * 32 + ".next")
        owned.write_text("interrupted partial state", encoding="utf-8")
        unknown = archive / ".manifest.json.next"
        unknown.write_text("unknown legacy temporary", encoding="utf-8")
        cleanup.restore_archive(archive, guard=lambda: None)
        self.assertTrue(path.exists())
        self.assertFalse(owned.exists())
        self.assertEqual("unknown legacy temporary", unknown.read_text(encoding="utf-8"))

    def test_symlink_escape_and_explicit_physical_root(self):
        target = self.base / "external-projects"
        target.mkdir()
        session = self.write(target, f"business/{SID}.jsonl")
        link = self.claude / "projects"
        try:
            link.symlink_to(target, target_is_directory=True)
        except OSError as exc:
            self.skipTest("symlink creation unavailable: " + type(exc).__name__)
        plan = self.plan()
        self.assertTrue(plan["uncovered"])
        self.assertNotIn(str(session), [x["path"] for x in plan["files"]])
        with self.assertRaises(cleanup.CleanupError):
            self.apply(plan)
        with self.assertRaises(cleanup.CleanupError):
            cleanup.make_plan(self.project, [self.codex], [self.claude], [link])
        plan = cleanup.make_plan(self.project, [self.codex], [self.claude], [target])
        self.assertEqual([], plan["uncovered"])
        self.apply(plan)
        self.assertFalse(session.exists())
        self.assertTrue(link.is_symlink())

    @unittest.skipUnless(cleanup.os.name == "nt", "Windows junction test")
    def test_junction_escape_requires_explicit_physical_root(self):
        target = self.base / "external-projects"
        target.mkdir()
        session = self.write(target, f"business/{SID}.jsonl")
        link = self.claude / "projects"
        # Only synthetic paths generated by TemporaryDirectory; no actual homes.
        command = "New-Item -ItemType Junction -Path '" + str(link).replace("'", "''") + "' -Target '" + str(target).replace("'", "''") + "' | Out-Null"
        subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command], check=True, capture_output=True)
        self.assertTrue(cleanup.reparse(link))
        plan = self.plan()
        self.assertTrue(plan["uncovered"])
        with self.assertRaises(cleanup.CleanupError):
            self.apply(plan)
        with self.assertRaises(cleanup.CleanupError):
            cleanup.make_plan(self.project, [self.codex], [self.claude], [link])
        plan = cleanup.make_plan(self.project, [self.codex], [self.claude], [target])
        self.assertEqual([], plan["uncovered"])
        self.apply(plan)
        self.assertFalse(session.exists())
        self.assertTrue(cleanup.reparse(link))

    def test_wal_schema_and_restore_family(self):
        writer = self.base / "writer.sqlite"
        with contextlib.closing(sqlite3.connect(writer)) as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("CREATE TABLE threads(id TEXT)")
            db.execute("INSERT INTO threads VALUES('synthetic')")
            db.commit()
            target = self.codex / "state_5.sqlite"
            for suffix in ("", "-wal", "-shm"):
                source = Path(str(writer) + suffix)
                shutil.copyfile(source, Path(str(target) + suffix))
        before = {str(p): p.read_bytes() for p in self.codex.iterdir()}
        plan = self.plan()
        self.assertEqual(before, {str(p): p.read_bytes() for p in self.codex.iterdir()})
        archive = self.apply(plan)
        with contextlib.closing(sqlite3.connect(target)) as db:
            self.assertEqual(0, db.execute("SELECT count(*) FROM threads").fetchone()[0])
        cleanup.restore_archive(archive, guard=lambda: None)
        self.assertEqual(before, {str(p): p.read_bytes() for p in self.codex.iterdir()})

    def test_interrupted_copy_keeps_recoverable_original(self):
        source = self.write(self.codex, "history.jsonl")
        original = source.read_bytes()
        real_unlink = Path.unlink
        def fail_once(path, *args, **kwargs):
            if path == source:
                raise PermissionError("synthetic unlink failure")
            return real_unlink(path, *args, **kwargs)
        with mock.patch.object(Path, "unlink", fail_once):
            with self.assertRaisesRegex(cleanup.CleanupError, "interrupted"):
                self.apply()
        archive = next((self.project / "maintenance" / "session-quarantine").iterdir())
        self.assertEqual(original, source.read_bytes())
        cleanup.restore_archive(archive, guard=lambda: None)
        self.assertEqual(original, source.read_bytes())

    def test_tampered_restore_target_rejected(self):
        self.write(self.codex, "history.jsonl")
        archive = self.apply()
        path = archive / "manifest.json"
        value = json.loads(path.read_text(encoding="utf-8"))
        value["operations"][0]["path"] = str(self.codex / "auth.json")
        path.write_text(json.dumps(value), encoding="utf-8")
        with self.assertRaises(cleanup.CleanupError):
            cleanup.restore_archive(archive, guard=lambda: None)

    def test_hard_link_candidate_is_not_modified(self):
        import os
        target = self.write(self.base, "outside.txt")
        linked = self.codex / "history.jsonl"
        os.link(target, linked)
        plan = self.plan()
        self.assertTrue(plan["uncovered"])
        self.assertFalse(plan["files"])
        self.assertTrue(target.exists())

    def test_unknown_schema_rejected_and_explicit_subset_is_marked(self):
        unknown = self.db("queue_1.sqlite", "CREATE TABLE new_queue_type(payload TEXT);")
        unknown_bytes = unknown.read_bytes()
        session = self.write(self.codex, "history.jsonl")
        plan = self.plan()
        with self.assertRaisesRegex(cleanup.CleanupError, "Uncovered"):
            self.apply(plan)
        archive = self.apply(plan, allow_supported_subset=True)
        self.assertEqual(unknown_bytes, unknown.read_bytes())
        self.assertFalse(session.exists())
        self.assertTrue(json.loads((archive / "manifest.json").read_text(encoding="utf-8"))["partial"])

    def test_unknown_database_trigger_refused(self):
        self.db("state_5.sqlite", "CREATE TABLE threads(id TEXT); CREATE TRIGGER custom AFTER DELETE ON threads BEGIN SELECT 1; END;")
        self.assertTrue(self.plan()["uncovered"])

    def test_nested_ui_session_keys_report_incomplete_coverage(self):
        path = self.write(self.codex, ".codex-global-state.json", json.dumps({
            "electron-persisted-atom-state": {"view": {"last-thread-id": "synthetic"}},
            "projectless-thread-ids": ["synthetic"], "unrecognized-session-index": []}))
        plan = self.plan()
        self.assertEqual(2, len(plan["uncovered"]))
        with self.assertRaisesRegex(cleanup.CleanupError, "Uncovered"):
            self.apply(plan)
        self.apply(plan, allow_supported_subset=True)
        remaining = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual("synthetic", remaining["electron-persisted-atom-state"]["view"]["last-thread-id"])
        self.assertNotIn("projectless-thread-ids", remaining)

    def test_process_guard_runs_before_mutation(self):
        path = self.write(self.codex, "history.jsonl")
        guard = mock.Mock(side_effect=cleanup.CleanupError("active process"))
        with self.assertRaisesRegex(cleanup.CleanupError, "active"):
            cleanup.apply_plan(self.plan(), guard=guard)
        self.assertTrue(path.exists())
        self.assertFalse((self.project / "maintenance").exists())

    def test_locked_file_refuses_apply(self):
        path = self.write(self.codex, "history.jsonl")
        plan = self.plan()
        if cleanup.os.name != "nt":
            self.skipTest("Windows deny-write handle test")
        with path.open("r+b"):
            with self.assertRaisesRegex(cleanup.CleanupError, "locked"):
                self.apply(plan)
        self.assertTrue(path.exists())

    def test_restore_wont_overwrite_new_session(self):
        path = self.write(self.codex, "history.jsonl")
        archive = self.apply()
        path.write_text("new session", encoding="utf-8")
        with self.assertRaisesRegex(cleanup.CleanupError, "changed"):
            cleanup.restore_archive(archive, guard=lambda: None)

    def test_archive_boundary_hash_and_purge_confirmation(self):
        self.write(self.codex, "history.jsonl")
        archive = self.apply()
        with self.assertRaises(cleanup.CleanupError):
            cleanup.purge_archive(archive, confirmation="wrong", guard=lambda: None)
        (archive / "000000.bin").write_text("tampered", encoding="utf-8")
        with self.assertRaisesRegex(cleanup.CleanupError, "hash"):
            cleanup.restore_archive(archive, guard=lambda: None)
        with self.assertRaisesRegex(cleanup.CleanupError, "hash"):
            cleanup.purge_archive(archive, confirmation=archive.name, guard=lambda: None)

    def test_purge_refuses_unknown_files(self):
        self.write(self.codex, "history.jsonl")
        archive = self.apply()
        self.write(archive, "unrelated.txt")
        with self.assertRaisesRegex(cleanup.CleanupError, "Unexpected"):
            cleanup.purge_archive(archive, confirmation=archive.name, guard=lambda: None)


if __name__ == "__main__":
    unittest.main()
