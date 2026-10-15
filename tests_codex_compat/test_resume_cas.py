"""Exact-session resume races using real JSON locks and synthetic local state.

Only reviewed modules are imported; registry, process ancestry and every path
are replaced. No real bus, messenger, App Server or model is started.
"""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
from types import ModuleType
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]


class ResumeCASTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="ma-resume-cas-")
        self.addCleanup(temporary.cleanup)
        self.bus = Path(temporary.name)
        self.role = "synthetic-role"
        self.sid = "old-session"
        self.current_sid = self.sid
        self.host_kind = "codex"
        self.host_pid = 202
        self.path = self.bus / self.role / "presence.json"
        self.path.parent.mkdir()
        package = ModuleType("bus")
        package.__path__ = []
        config = ModuleType("bus.config")
        config.BUS, config.METRICS = self.bus, self.bus / "metrics-unused.jsonl"
        config._now = lambda: "2026-10-13T00:00:00"
        proc = ModuleType("bus.proc")
        proc._host_ancestor = lambda: (self.host_kind, self.host_pid, str(self.bus), [])
        proc._host_session_id = lambda kind: self.current_sid if kind == "codex" else None
        proc._host_pid_alive = lambda *_: True
        proc._age_seconds = lambda _: 0
        roster = ModuleType("bus.roster")
        roster._load_agents = lambda: {self.role: {"caps": ["self-wake"]}, "other-role": {}}
        modules = patch.dict(sys.modules, {m.__name__: m for m in (package, config, proc, roster)})
        modules.start()
        self.addCleanup(modules.stop)
        environment = patch.dict(os.environ)
        environment.start()
        self.addCleanup(environment.stop)
        processes = patch.object(subprocess, "Popen", side_effect=AssertionError("No subprocess authorized"))
        processes.start()
        self.addCleanup(processes.stop)
        self.io = self.load("agent_bus/bus/io.py", "bus.io")
        self.startup = self.load("agent_bus/bus/codex_startup.py", "bus.codex_startup")
        self.presence = self.load("agent_bus/bus/presence.py", "bus.presence")
        self.write_presence()
        self.startup.record_codex_session_role(self.role, self.sid)

    def load(self, relative, name):
        spec = importlib.util.spec_from_file_location(name, ROOT / relative)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        return module

    def write_presence(self, **changes):
        value = {"role": self.role, "host_kind": "codex", "host_pid": 101,
                 "host_session_id": self.sid, "mode": "listening", "current_task": "synthetic-task"}
        value.update(changes)
        self.io._write_json(self.path, value)

    def read_presence(self):
        return json.loads(self.path.read_text(encoding="utf-8"))

    def test_same_session_resume_updates_host_and_keeps_task(self):
        self.assertEqual(self.role, self.startup.restore_registered_codex_session(self.sid))
        value = self.read_presence()
        self.assertEqual(202, value["host_pid"])
        self.assertEqual(self.sid, value["host_session_id"])
        self.assertEqual("synthetic-task", value["current_task"])
        self.assertFalse(self.path.with_name("presence.json.lock").exists())

    def test_resume_cannot_overwrite_new_binding_while_waiting_for_same_lock(self):
        waiting = threading.Event()
        real_lock = self.io.json_lock

        @contextmanager
        def observed_lock(path, **kwargs):
            self.assertEqual(self.path, path)
            waiting.set()
            with real_lock(path, **kwargs):
                yield

        self.presence.json_lock = observed_lock
        before_ledger = self.startup.SESSION_ROLES.read_bytes()
        with ThreadPoolExecutor(max_workers=1) as pool:
            with real_lock(self.path):
                future = pool.submit(self.startup.restore_registered_codex_session, self.sid)
                self.assertTrue(waiting.wait(3), "Resume must reach the existing presence lock")
                self.write_presence(host_session_id="new-session", host_pid=303)
                new_binding = self.path.read_bytes()
            with self.assertRaisesRegex(RuntimeError, "binding changed"):
                future.result(timeout=3)
        self.assertEqual(new_binding, self.path.read_bytes())
        self.assertEqual(before_ledger, self.startup.SESSION_ROLES.read_bytes())

    def test_park_between_precheck_and_write_is_preserved(self):
        real_lock = self.io.json_lock

        @contextmanager
        def park_before_lock(path, **kwargs):
            self.io._write_json(path.parent / "parked.json", {"since": "synthetic"})
            with real_lock(path, **kwargs):
                yield

        self.presence.json_lock = park_before_lock
        before = self.path.read_bytes()
        with self.assertRaisesRegex(RuntimeError, "binding changed"):
            self.startup.restore_registered_codex_session(self.sid)
        self.assertEqual(before, self.path.read_bytes())

    def test_cleared_host_requires_prior_exact_messenger(self):
        self.write_presence(host_kind=None, host_pid=None)
        before = self.path.read_bytes()
        self.assertIsNone(self.startup.restore_registered_codex_session(self.sid))
        self.assertEqual(before, self.path.read_bytes())
        self.io._write_json(self.path.parent / "messenger.state.json", {"session_id": self.sid})
        self.assertEqual(self.role, self.startup.restore_registered_codex_session(self.sid))
        self.assertEqual(202, self.read_presence()["host_pid"])

    def test_cas_rejects_non_codex_or_missing_or_corrupt_binding(self):
        for value in ({"host_session_id": self.sid, "host_kind": "claude"},
                      {"host_kind": "codex"}, [], None):
            with self.subTest(value=value):
                self.io._write_json(self.path, value)
                before = self.path.read_bytes()
                with self.assertRaisesRegex(RuntimeError, "binding changed"):
                    self.presence._touch_presence(self.role, "idle", expected_host_session_id=self.sid)
                self.assertEqual(before, self.path.read_bytes())

    def test_cas_requires_calling_host_to_match_expected_session(self):
        before = self.path.read_bytes()
        for expected in ("another-session", "", 42):
            with self.subTest(expected=expected):
                with self.assertRaisesRegex(RuntimeError, "host does not match"):
                    self.presence._touch_presence(self.role, "idle", expected_host_session_id=expected)
                self.assertEqual(before, self.path.read_bytes())

    def test_default_presence_call_still_allows_explicit_registration(self):
        self.current_sid = "new-session"
        self.presence._touch_presence(self.role, "idle")
        self.assertEqual("new-session", self.read_presence()["host_session_id"])
        self.assertEqual(self.role, self.startup.registered_codex_role("new-session"))

    def test_lost_lock_does_not_report_resume_success(self):
        @contextmanager
        def unavailable_lock(*args, **kwargs):
            raise TimeoutError("synthetic contention")
            yield

        self.presence.json_lock = unavailable_lock
        before = self.path.read_bytes()
        with self.assertRaisesRegex(RuntimeError, "did not complete"):
            self.startup.restore_registered_codex_session(self.sid)
        self.assertEqual(before, self.path.read_bytes())

    def test_new_session_or_reassigned_role_has_no_resume_authority(self):
        self.assertIsNone(self.startup.restore_registered_codex_session("unregistered-session"))
        self.write_presence(host_session_id="new-session")
        before = self.path.read_bytes()
        self.assertIsNone(self.startup.restore_registered_codex_session(self.sid))
        self.assertEqual(before, self.path.read_bytes())


if __name__ == "__main__":
    unittest.main()
