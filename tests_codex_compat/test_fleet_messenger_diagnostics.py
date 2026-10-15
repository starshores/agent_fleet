"""Fleet evidence diagnostics using temporary state and fake process providers.

No production module configuration, real bus command, process inventory,
messenger, network request, or private session is accessed.
"""
from datetime import datetime
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]


class FleetMessengerDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="ma-fleet-evidence-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.bus = self.root / "bus"
        self.role, self.sid = "synthetic-role", "synthetic-session"
        self.role_dir = self.bus / self.role
        self.role_dir.mkdir(parents=True)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        package = ModuleType("bus")
        package.__path__ = []
        config = ModuleType("bus.config")
        config.BUS, config.METRICS = self.bus, self.bus / "metrics-unused.jsonl"
        config._now = lambda: "2026-10-13T00:00:00"
        config.cfg = lambda name, default=None: default
        proc = ModuleType("bus.proc")
        proc._proc_scan = lambda: ({}, {self.role})
        proc._age_seconds = lambda value: 0 if value else None
        proc._host_pid_alive = lambda *args: True
        roster = ModuleType("bus.roster")
        roster._list_roles = lambda: [self.role]
        roster._retired_roles = lambda: set()
        roster._is_test_role = lambda role: False
        roster._policy = lambda *args: {}
        roster.load_role_profiles = lambda: {"roles": [{"role": self.role, "cwd": str(self.workspace)}]}
        lock = ModuleType("bus.lock")
        lock.holders = lambda: []
        presence = ModuleType("bus.presence")
        presence._owner_live = lambda role: True
        recovery = ModuleType("bus.codex_recovery")
        recovery.requeue_blocked = lambda *args: True
        recovery.fence_tasks = Mock(side_effect=AssertionError("Diagnosis must not fence or replay tasks"))
        psutil = ModuleType("psutil")
        psutil.Error = RuntimeError
        psutil.net_connections = lambda **kwargs: [SimpleNamespace(
            status="LISTEN", laddr=SimpleNamespace(port=8799), pid=901)]
        self.wait_proc = Mock()
        self.wait_proc.cmdline.return_value = ["python", "agentbus.py", "wait", "--role", self.role]
        self.messenger_proc = Mock()
        self.messenger_proc.cmdline.return_value = ["python", "codex_messenger.py", "--session-id", self.sid]
        psutil.Process = lambda pid: self.messenger_proc if pid == 301 else self.wait_proc
        startup = ModuleType("agentbus_codex_session_start")
        startup._role_for_session = lambda sid: self.role if sid == self.sid else None
        startup._start_messenger = Mock(side_effect=AssertionError("Evidence absence cannot authorize start"))
        modules = patch.dict(sys.modules, {m.__name__: m for m in (
            package, config, proc, roster, lock, presence, recovery, psutil, startup)})
        modules.start()
        self.addCleanup(modules.stop)
        processes = patch.object(subprocess, "Popen", side_effect=AssertionError("No process launch authorized"))
        processes.start()
        self.addCleanup(processes.stop)
        original_path = list(sys.path)
        self.addCleanup(lambda: sys.path.__setitem__(slice(None), original_path))
        self.io = self.load("agent_bus/bus/io.py", "bus.io")
        self.selfwake = self.load("agent_bus/bus/selfwake.py", "bus.selfwake")
        self.health = self.load("agent_bus/bus/health.py", "bus.health")
        self.guard = self.load(".codex/codex_messenger_guard.py", "isolated_messenger_guard")
        self.guard.ROOT, self.guard.BUS = self.root, self.bus
        self.guard._live_host = lambda pid: True
        self.guard._age = lambda value: 0 if value else None
        self.guard.datetime = SimpleNamespace(now=lambda: self.now)
        self.now = datetime(2026, 10, 13)
        self.start = startup._start_messenger
        self.health._health_anomalies = lambda *args: {"orphans": [], "stuck": []}
        self.health._orphan_daemon_pids = lambda *args: []
        self.health._stale_webui_pids = lambda *args: []
        self.health._stale_todos = lambda *args: []
        self.write(self.bus / "agents.json", {self.role: {"caps": ["self-wake"]}})
        self.write(self.role_dir / "presence.json", {"host_kind": "codex", "host_session_id": self.sid,
                                                   "host_pid": 201, "mode": "listening", "last_seen": "fresh"})
        self.write(self.role_dir / "wait.reg.json", {"wait_pid": 101, "host_kind": "codex", "host_pid": 201})

    def load(self, relative, name):
        spec = importlib.util.spec_from_file_location(name, ROOT / relative)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        return module

    def write(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.io._write_json(path, value)

    def messenger(self, **changes):
        value = {"session_id": self.sid, "pid": 301, "heartbeat": "fresh", "status": "armed",
                 "wait_alive": True, "app_ready": True, "pump_alive": True}
        value.update(changes)
        self.write(self.role_dir / "messenger.state.json", value)

    def snapshot(self):
        return {str(path.relative_to(self.bus)): path.read_bytes()
                for path in self.bus.rglob("*") if path.is_file()}

    def events(self):
        path = self.bus / "codex_messenger_guard.jsonl"
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []

    def test_missing_state_reports_stable_reason_without_marking_dead(self):
        value, reason = self.selfwake._codex_messenger_evidence(self.role, self.sid)
        self.assertIsNone(value)
        self.assertEqual("messenger_state_missing", reason)
        state, detail = self.selfwake._self_wake_state(self.role, wait_roles={self.role})
        self.assertEqual("recent", state)
        self.assertIn(reason, detail)
        self.assertIn("尚未确认", detail)

    def test_invalid_json_unicode_and_shape_have_stable_reason(self):
        path = self.role_dir / "messenger.state.json"
        for data in (b"{broken", b"\xff", b"[]", b"null"):
            with self.subTest(data=data):
                path.write_bytes(data)
                self.assertEqual("messenger_state_invalid",
                                 self.selfwake._codex_messenger_evidence(self.role, self.sid)[1])

    def test_unreadable_evidence_stays_unconfirmed(self):
        original = Path.read_text
        target = self.role_dir / "messenger.state.json"
        def read(path, *args, **kwargs):
            if path == target:
                raise PermissionError("synthetic denied read")
            return original(path, *args, **kwargs)
        with patch.object(Path, "read_text", read):
            self.assertEqual("messenger_state_unreadable",
                             self.selfwake._codex_messenger_evidence(self.role, self.sid)[1])

    def test_session_mismatch_and_invalid_session_remain_distinct(self):
        self.messenger(session_id="other-session")
        self.assertEqual("messenger_session_mismatch",
                         self.selfwake._codex_messenger_evidence(self.role, self.sid)[1])
        self.assertEqual("messenger_session_invalid",
                         self.selfwake._codex_messenger_evidence(self.role, None)[1])

    def test_health_missing_evidence_is_a_warning_and_query_is_readonly(self):
        before = self.snapshot()
        value = self.health._collect_health()
        self.assertFalse(value["healthy"])
        self.assertEqual(0, value["selfwake"]["stale"])
        self.assertEqual(1, value["selfwake"]["recent"])
        self.assertEqual([{"role": self.role, "reason": "messenger_state_missing"}],
                         value["selfwake"]["messenger_evidence_gaps"])
        self.assertIn("信使投递证据待核对", value["warns"][0])
        self.assertEqual(before, self.snapshot())

    def test_fresh_complete_evidence_clears_health_warning(self):
        self.assertFalse(self.health._collect_health()["healthy"])
        self.messenger()
        value = self.health._collect_health()
        self.assertTrue(value["healthy"])
        self.assertEqual([], value["selfwake"]["messenger_evidence_gaps"])
        self.assertEqual(1, value["selfwake"]["armed"])

    def test_claude_recent_state_does_not_gain_codex_warning(self):
        self.write(self.role_dir / "presence.json", {"host_kind": "claude"})
        self.health._self_wake_state = lambda *args, **kwargs: ("recent", "legacy transition")
        self.assertTrue(self.health._collect_health()["healthy"])

    def test_parked_and_retired_roles_do_not_gain_evidence_warnings(self):
        self.write(self.role_dir / "parked.json", {"since": "synthetic"})
        self.assertTrue(self.health._collect_health()["healthy"])
        self.health._retired_roles = lambda: {self.role}
        self.assertTrue(self.health._collect_health()["healthy"])

    def test_guard_missing_state_is_deduplicated_and_never_starts_messenger(self):
        self.assertEqual(0, self.guard.main())
        first = json.loads((self.role_dir / "messenger.diagnostic.json").read_text(encoding="utf-8"))
        self.now = datetime(2026, 10, 13, 0, 0, 45)
        self.assertEqual(0, self.guard.main())
        second = json.loads((self.role_dir / "messenger.diagnostic.json").read_text(encoding="utf-8"))
        self.assertEqual(first["first_seen"], second["first_seen"])
        self.assertNotEqual(first["last_checked"], second["last_checked"])
        self.assertEqual("blocked", second["status"])
        self.assertFalse(second["automatic_recovery"])
        self.assertEqual(["skipped_evidence"], [row["outcome"] for row in self.events()])
        self.start.assert_not_called()

    def test_guard_reason_changes_and_restoration_are_recorded_once(self):
        self.guard.main()
        (self.role_dir / "messenger.state.json").write_text("{bad", encoding="utf-8")
        self.guard.main()
        self.guard.main()
        self.messenger()
        self.guard.main()
        self.guard.main()
        events = self.events()
        self.assertEqual(["skipped_evidence", "skipped_evidence", "evidence_restored"],
                         [row["outcome"] for row in events])
        value = json.loads((self.role_dir / "messenger.diagnostic.json").read_text(encoding="utf-8"))
        self.assertEqual("resolved", value["status"])
        self.start.assert_not_called()

    def test_guard_never_changes_processing_or_recovery_evidence(self):
        self.write(self.role_dir / "processing" / "synthetic-task.json", {"id": "synthetic-task", "text": "PRIVATE_FIXTURE"})
        self.write(self.role_dir / "_codex_recovery" / "synthetic-task.json", {"state": "dispatching", "claim_id": "fixture-claim"})
        before = self.snapshot()
        self.guard.main()
        after = self.snapshot()
        for path, data in before.items():
            self.assertEqual(data, after[path])
        self.assertNotIn("PRIVATE_FIXTURE", json.dumps(self.events()))
        self.start.assert_not_called()

    def test_guard_maintenance_and_park_do_not_write_diagnostics(self):
        self.write(self.bus / "maintenance.json", {"active": True})
        before = self.snapshot()
        self.guard.main()
        self.assertEqual(before, self.snapshot())
        self.write(self.bus / "maintenance.json", {"active": False})
        self.write(self.role_dir / "parked.json", {"since": "synthetic"})
        before = self.snapshot()
        self.guard.main()
        self.assertEqual(before, self.snapshot())
        self.start.assert_not_called()

    def test_diagnostic_refuses_path_escape_and_hardlinked_record(self):
        with self.assertRaises(ValueError):
            self.guard._note_evidence_gap("../workspace", self.sid, "messenger_state_missing")
        original = self.root / "unrelated-fixture.json"
        original.write_text("{}", encoding="utf-8")
        os.link(original, self.role_dir / "messenger.diagnostic.json")
        with self.assertRaisesRegex(ValueError, "redirected"):
            self.guard._note_evidence_gap(self.role, self.sid, "messenger_state_missing")
        self.assertEqual("{}", original.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
