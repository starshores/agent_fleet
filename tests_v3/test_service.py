"""Behavioral checks for business replacement and trusted worker scope.

All mutations are inside TemporaryDirectory; no legacy modules or data load.
"""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from masteragent.contracts import ContractError
from masteragent import __version__
from masteragent.paths import PathSafetyError
from masteragent.service import Actor, Platform, PlatformError


class ServiceTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="masteragent-v3-service-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.state = self.root / "state"
        self.workspace_a = self.root / "workspace-a"
        self.workspace_b = self.root / "workspace-b"
        self.workspace_a.mkdir()
        self.workspace_b.mkdir()
        self.package_a = self.make_package("alpha", "echo")
        self.package_b = self.make_package("beta", "uppercase")
        self.platform = Platform(self.state)

    def make_package(self, name, adapter):
        manifest = {
            "schema_version": 1, "platform_api": 1, "package_id": name, "version": "1.0.0",
            "roles": [
                {"role_id": "reviewer", "display_name": "Reviewer", "instructions": name + " instructions", "tools": [name]},
                {"role_id": "observer", "display_name": "Observer", "instructions": name + " observe", "tools": []},
            ],
            "knowledge": {name: name + " knowledge"},
            "tools": {name: {"adapter": adapter, "description": name + " tool"}},
            "workflows": {name: {"roles": ["reviewer"], "description": name + " workflow"}},
        }
        path = self.root / (name + ".json")
        path.write_text(json.dumps(manifest), encoding="utf-8")
        return path

    def install_a(self):
        self.platform.install("a", self.package_a, self.workspace_a)
        self.platform.activate("a")

    def both(self):
        self.install_a()
        self.platform.install("b", self.package_b, self.workspace_b)

    def assert_code(self, code, callback, *args):
        with self.assertRaises(PlatformError) as error:
            callback(*args)
        self.assertEqual(code, error.exception.code)

    def test_empty_a_b_restore_a_same_core_and_retained_history(self):
        self.assertEqual([], self.platform.status()["installations"])
        self.assertIsNone(self.platform.status()["active"])
        self.both()
        actor_a = self.platform.bind("a", "reviewer")
        task_a = self.platform.submit("a", "reviewer", "Alpha output")
        self.assertEqual("Alpha output", self.platform.run_as(actor_a, task_a["task_id"], "alpha")["output"])
        self.assert_code("active_conflict", self.platform.activate, "b")
        self.platform.deactivate("a")
        self.platform.activate("b")
        actor_b = self.platform.bind("b", "reviewer")
        context_b = self.platform.context_for(actor_b)
        self.assertEqual({"beta": "beta knowledge"}, context_b["knowledge"])
        self.assertEqual(["beta"], list(context_b["tools"]))
        self.assertEqual(str(self.workspace_b), context_b["workspace"])
        self.assertNotIn("alpha", json.dumps(context_b))
        task_b = self.platform.submit("b", "reviewer", "Beta output")
        self.assertEqual("BETA OUTPUT", self.platform.run_as(actor_b, task_b["task_id"], "beta")["output"])
        self.assertEqual([task_b["task_id"]], [r["task_id"] for r in self.platform.results("b")])
        self.assert_code("installation_inactive", self.platform.context, "a", "reviewer")
        self.platform.deactivate("b")
        self.platform.activate("a")
        restored = self.platform.bind("a", "reviewer")
        self.assertEqual(2, restored.generation)
        self.assertEqual({"alpha": "alpha knowledge"}, self.platform.context_for(restored)["knowledge"])
        self.assertEqual([], self.platform.notifications("a"))
        self.assertEqual([], self.platform.results("a"))
        self.assertEqual([task_a["task_id"]], [r["task_id"] for r in self.platform.history("a")["results"]])
        self.assert_code("invalid_actor", self.platform.context_for, actor_a)
        self.assert_code("invalid_actor", self.platform.context_for, actor_b)

    def test_restart_restores_state_and_package_snapshot_without_source(self):
        self.install_a()
        task = self.platform.submit("a", "reviewer", "persisted")
        self.platform.run("a", task["task_id"], "alpha")
        self.package_a.unlink()
        restarted = Platform(self.state)
        self.assertEqual("a", restarted.status()["active"])
        self.assertEqual("persisted", restarted.results("a")[0]["output"])
        self.assertEqual("alpha knowledge", restarted.context("a", "reviewer")["knowledge"]["alpha"])
        restarted.deactivate("a")
        restarted.activate("a")
        self.assertEqual(1, len(restarted.history("a")["results"]))

    def test_source_change_cannot_replace_installed_snapshot(self):
        source_before = self.package_a.read_bytes()
        self.install_a()
        self.platform.register("a", "reviewer")
        task = self.platform.submit("a", "reviewer", "test")
        self.platform.run("a", task["task_id"], "alpha")
        self.assertEqual(source_before, self.package_a.read_bytes())
        self.platform.deactivate("a")
        self.package_a.write_text("malformed replacement", encoding="utf-8")
        self.platform.activate("a")
        self.assertEqual("alpha knowledge", self.platform.context("a", "reviewer")["knowledge"]["alpha"])

    def test_forged_actor_cannot_change_installation_role_generation_or_token(self):
        self.both()
        actor = self.platform.bind("a", "reviewer")
        for forged in (replace(actor, installation_id="b"), replace(actor, role_id="observer"),
                       replace(actor, generation=999), replace(actor, token="fake")):
            with self.subTest(forged=repr(forged)):
                self.assert_code("invalid_actor", self.platform.context_for, forged)
        self.assertNotIn(actor.token, repr(actor))

    def test_cross_installation_task_read_and_run_rejected_with_valid_actor(self):
        self.both()
        task_a = self.platform.submit("a", "reviewer", "private alpha")
        self.platform.deactivate("a")
        self.platform.activate("b")
        actor_b = self.platform.bind("b", "reviewer")
        self.assert_code("task_scope", self.platform.task_for, actor_b, task_a["task_id"])
        self.assert_code("task_scope", self.platform.run_as, actor_b, task_a["task_id"], "beta")
        self.assert_code("task_scope", self.platform.run, "b", task_a["task_id"], "beta")
        self.assertEqual([], self.platform.results("b"))
        self.assertEqual("queued", self.platform.history("a")["tasks"][0]["state"])

    def test_role_permissions_and_same_installation_task_scope(self):
        self.install_a()
        reviewer = self.platform.bind("a", "reviewer")
        observer = self.platform.bind("a", "observer")
        review_task = self.platform.submit("a", "reviewer", "review")
        observer_task = self.platform.submit("a", "observer", "observe")
        self.assertEqual({}, self.platform.context_for(observer)["tools"])
        self.assert_code("task_scope", self.platform.task_for, observer, review_task["task_id"])
        self.assert_code("task_scope", self.platform.run_as, reviewer, observer_task["task_id"], "alpha")
        self.assert_code("tool_denied", self.platform.run_as, observer, observer_task["task_id"], "alpha")
        self.assert_code("tool_denied", self.platform.run_as, reviewer, review_task["task_id"], "beta")
        self.assertEqual([], self.platform.results("a"))

    def test_new_binding_revokes_old_token(self):
        self.install_a()
        old = self.platform.bind("a", "reviewer")
        new = self.platform.bind("a", "reviewer")
        self.assertNotEqual(old.token, new.token)
        self.assert_code("invalid_actor", self.platform.context_for, old)
        self.assertEqual("a", self.platform.context_for(new)["installation_id"])

    def test_queued_old_generation_is_preserved_without_replay(self):
        self.install_a()
        task = self.platform.submit("a", "reviewer", "must not replay")
        old = self.platform.bind("a", "reviewer")
        self.platform.deactivate("a")
        self.platform.activate("a")
        new = self.platform.bind("a", "reviewer")
        self.assert_code("invalid_actor", self.platform.run_as, old, task["task_id"], "alpha")
        self.assert_code("stale_task", self.platform.run_as, new, task["task_id"], "alpha")
        self.assertEqual("queued", self.platform.history("a")["tasks"][0]["state"])
        self.assertEqual([], self.platform.notifications("a"))

    def test_unfinished_work_blocks_switch_and_uninstall(self):
        self.install_a()
        task = self.platform.submit("a", "reviewer", "unfinished")
        # Model the persistent state a future external adapter may leave after
        # a crash. This slice deliberately does not guess how to recover it.
        with closing(sqlite3.connect(self.platform.db_path)) as db:
            with db:
                db.execute("UPDATE tasks SET state='running' WHERE task_id=?", (task["task_id"],))
        self.assert_code("unfinished_work", self.platform.deactivate, "a")
        self.assert_code("installation_active", self.platform.uninstall, "a")
        self.assertEqual("a", self.platform.status()["active"])

    def test_uninstall_disables_and_preserves_history(self):
        self.install_a()
        task = self.platform.submit("a", "reviewer", "history survives")
        self.platform.run("a", task["task_id"], "alpha")
        self.platform.deactivate("a")
        self.platform.uninstall("a")
        self.assertEqual("uninstalled", self.platform.view("a")["state"])
        self.assertEqual("history survives", self.platform.history("a")["results"][0]["output"])
        self.assert_code("installation_uninstalled", self.platform.activate, "a")
        self.assert_code("installation_exists", self.platform.install, "a", self.package_a, self.workspace_a)

    def test_invalid_package_leaves_no_installation(self):
        self.package_a.write_text('{"roles":', encoding="utf-8")
        with self.assertRaises(ContractError):
            self.platform.install("a", self.package_a, self.workspace_a)
        self.assertEqual([], self.platform.status()["installations"])

    def test_request_retry_is_idempotent_and_content_conflict_is_rejected(self):
        self.both()
        task = self.platform.submit("a", "reviewer", "once", "request-1")
        self.assertEqual(task, self.platform.submit("a", "reviewer", "once", "request-1"))
        self.assert_code("request_conflict", self.platform.submit, "a", "reviewer", "different", "request-1")
        self.assert_code("request_conflict", self.platform.submit, "a", "observer", "once", "request-1")
        self.platform.deactivate("a")
        self.platform.activate("b")
        other = self.platform.submit("b", "reviewer", "once", "request-1")
        self.assertNotEqual(task["task_id"], other["task_id"])
        self.platform.deactivate("b")
        self.platform.activate("a")
        self.assertEqual(task, self.platform.submit("a", "reviewer", "once", "request-1"))
        self.assertEqual(1, len(self.platform.history("a")["tasks"]))

    def test_concurrent_retries_produce_one_task_result_and_notification(self):
        self.install_a()
        with ThreadPoolExecutor(max_workers=4) as executor:
            tasks = list(executor.map(lambda _: self.platform.submit("a", "reviewer", "once", "same-request"), range(8)))
        self.assertEqual(1, len({task["task_id"] for task in tasks}))
        actor = self.platform.bind("a", "reviewer")
        with ThreadPoolExecutor(max_workers=4) as executor:
            results = list(executor.map(lambda _: self.platform.run_as(actor, tasks[0]["task_id"], "alpha"), range(8)))
        self.assertTrue(all(result == results[0] for result in results))
        self.assertEqual(1, len(self.platform.results("a")))
        self.assertEqual(1, len(self.platform.notifications("a")))

    def test_admin_run_preserves_existing_worker_registration_and_token(self):
        self.install_a()
        actor = self.platform.bind("a", "reviewer")
        task = self.platform.submit("a", "reviewer", "admin execution")
        def identities():
            with closing(sqlite3.connect(self.platform.db_path)) as db:
                return (db.execute("SELECT * FROM registrations ORDER BY rowid").fetchall(),
                        db.execute("SELECT * FROM actor_tokens ORDER BY rowid").fetchall())
        before = identities()
        result = self.platform.run("a", task["task_id"], "alpha")
        self.assertEqual("a", self.platform.context_for(actor)["installation_id"])
        self.assertEqual(before, identities())
        self.assertEqual(result, self.platform.run("a", task["task_id"], "alpha"))
        self.assert_code("tool_denied", self.platform.run, "a", task["task_id"], "ungranted")
        self.assertEqual(before, identities())
        self.assertEqual(result, self.platform.run_as(actor, task["task_id"], "alpha"))

    def test_concurrent_admin_retries_do_not_register_workers_or_duplicate_results(self):
        self.install_a()
        task = self.platform.submit("a", "reviewer", "one administrative execution")
        with ThreadPoolExecutor(max_workers=4) as executor:
            results = list(executor.map(lambda _: self.platform.run("a", task["task_id"], "alpha"), range(8)))
        self.assertTrue(all(result == results[0] for result in results))
        self.assertEqual(1, len(self.platform.results("a")))
        self.assertEqual(1, len(self.platform.notifications("a")))
        self.assertEqual([], self.platform.history("a")["registrations"])
        with closing(sqlite3.connect(self.platform.db_path)) as db:
            self.assertEqual(0, db.execute("SELECT COUNT(*) FROM actor_tokens").fetchone()[0])

    def test_readonly_missing_state_does_not_create_it(self):
        missing = self.root / "missing" / "state"
        readonly = Platform(missing, readonly=True)
        self.assertEqual({"version": __version__, "initialized": False, "active": None, "installations": []}, readonly.status())
        self.assertFalse(missing.parent.exists())
        self.assert_code("not_initialized", readonly.history, "a")
        self.assert_code("read_only", readonly.activate, "a")
        self.assertFalse(missing.parent.exists())

    def test_readonly_observation_preserves_database_and_has_no_tokens(self):
        self.install_a()
        task = self.platform.submit("a", "reviewer", "persisted")
        self.platform.run("a", task["task_id"], "alpha")
        def fingerprint():
            return {path.name: (hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_mtime_ns)
                    for path in self.state.iterdir() if path.is_file()}
        before = fingerprint()
        readonly = Platform(self.state, readonly=True)
        readonly.status()
        readonly.view("a")
        readonly.context("a", "reviewer")
        readonly.results("a")
        readonly.notifications("a")
        history = readonly.history("a")
        self.assertNotIn("token", json.dumps(history))
        self.assert_code("read_only", readonly.register, "a", "reviewer")
        self.assert_code("read_only", readonly.submit, "a", "reviewer", "denied")
        self.assertEqual(before, fingerprint())

    def test_snapshot_corruption_fails_closed(self):
        self.install_a()
        with closing(sqlite3.connect(self.platform.db_path)) as db:
            with db:
                db.execute("UPDATE installations SET snapshot='{}' WHERE installation_id='a'")
        self.assert_code("snapshot_corrupt", self.platform.context, "a", "reviewer")

    def test_custom_business_adapter_without_core_changes(self):
        package_c = self.make_package("gamma", "reverse")
        workspace_c = self.root / "workspace-c"
        workspace_c.mkdir()
        custom = Platform(self.state, adapters={"reverse": lambda text: text[::-1]})
        custom.install("c", package_c, workspace_c)
        custom.activate("c")
        task = custom.submit("c", "reviewer", "hello")
        result = custom.run("c", task["task_id"], "gamma")
        self.assertEqual("olleh", result["output"])
        self.assertEqual("ok", result["status"])
        custom.deactivate("c")
        without_plugin = Platform(self.state)
        self.assertEqual("olleh", without_plugin.history("c")["results"][0]["output"])
        self.assert_code("adapter_unavailable", without_plugin.activate, "c")
        custom.activate("c")
        self.assertEqual(2, custom.context("c", "reviewer")["generation"])

    def test_missing_adapter_cannot_create_installation(self):
        custom_package = self.make_package("gamma", "unavailable")
        self.assert_code("adapter_unavailable", self.platform.install, "c", custom_package, self.workspace_a)
        self.assertEqual([], self.platform.status()["installations"])

    def test_adapter_failure_is_terminal_and_error_detail_is_not_exposed(self):
        calls = []
        def failing(text):
            calls.append(text)
            raise RuntimeError("SYNTHETIC_PRIVATE_DETAIL")
        custom = Platform(self.state, adapters={"echo": failing})
        custom.install("a", self.package_a, self.workspace_a)
        custom.activate("a")
        task = custom.submit("a", "reviewer", "fails once")
        result = custom.run("a", task["task_id"], "alpha")
        self.assertEqual("error", result["status"])
        self.assertIsNone(result["output"])
        self.assertEqual("adapter_failed", result["error"]["code"])
        self.assertEqual(result, custom.run("a", task["task_id"], "alpha"))
        self.assertEqual(["fails once"], calls)
        self.assertEqual("failed", custom.history("a")["tasks"][0]["state"])
        self.assertEqual("task_failed", custom.notifications("a")[0]["kind"])
        self.assertNotIn("SYNTHETIC_PRIVATE_DETAIL", json.dumps(custom.history("a")))

    def test_non_text_adapter_result_is_a_terminal_failure(self):
        custom = Platform(self.state, adapters={"echo": lambda text: {"text": text}})
        custom.install("a", self.package_a, self.workspace_a)
        custom.activate("a")
        task = custom.submit("a", "reviewer", "invalid return")
        result = custom.run("a", task["task_id"], "alpha")
        self.assertEqual("error", result["status"])
        self.assertEqual("invalid_adapter_result", result["error"]["code"])
        self.assertEqual("failed", custom.history("a")["tasks"][0]["state"])

    def test_deleted_workspace_is_rejected_before_context_or_run(self):
        self.install_a()
        actor = self.platform.bind("a", "reviewer")
        task = self.platform.submit("a", "reviewer", "cannot run")
        self.workspace_a.rmdir()
        for operation in (lambda: self.platform.context_for(actor),
                          lambda: self.platform.context("a", "reviewer"),
                          lambda: self.platform.run_as(actor, task["task_id"], "alpha")):
            with self.assertRaises(PathSafetyError) as error:
                operation()
            self.assertEqual("missing_workspace", error.exception.code)
        self.assertEqual([], self.platform.results("a"))

    def test_unrelated_database_with_same_version_is_never_adopted(self):
        foreign_state = self.root / "foreign-state"
        foreign_state.mkdir()
        foreign_db = foreign_state / "state.sqlite3"
        with closing(sqlite3.connect(foreign_db)) as db:
            with db:
                db.execute("CREATE TABLE unrelated (value TEXT)")
                db.execute("PRAGMA user_version = 1")
        before = foreign_db.read_bytes()
        self.assert_code("unsupported_schema", Platform, foreign_state)
        self.assert_code("unsupported_schema", Platform(foreign_state, readonly=True).status)
        self.assertEqual(before, foreign_db.read_bytes())

    def test_missing_own_schema_table_is_not_silently_repaired(self):
        with closing(sqlite3.connect(self.platform.db_path)) as db:
            with db:
                db.execute("DROP TABLE notifications")
        before = self.platform.db_path.read_bytes()
        self.assert_code("schema_corrupt", Platform, self.state)
        self.assertEqual(before, self.platform.db_path.read_bytes())

    def test_wal_database_is_rejected_without_creating_readonly_sidecars(self):
        with closing(sqlite3.connect(self.platform.db_path)) as db:
            self.assertEqual("wal", db.execute("PRAGMA journal_mode=WAL").fetchone()[0])
        before = {path.name: path.read_bytes() for path in self.state.iterdir()}
        self.assertEqual({"state.sqlite3"}, set(before))
        readonly = Platform(self.state, readonly=True)
        self.assert_code("unsupported_journal_mode", readonly.status)
        self.assert_code("unsupported_journal_mode", Platform, self.state)
        after = {path.name: path.read_bytes() for path in self.state.iterdir()}
        self.assertEqual(before, after)

    def test_activation_is_idempotent_without_revoking_worker(self):
        self.install_a()
        actor = self.platform.bind("a", "reviewer")
        self.assertEqual(1, self.platform.activate("a")["generation"])
        self.assertEqual(1, self.platform.context_for(actor)["generation"])


if __name__ == "__main__":
    unittest.main()
