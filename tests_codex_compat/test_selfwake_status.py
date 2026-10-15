"""Self-wake status checks using synthetic registrations and fake processes."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]


class SelfWakeStatusTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="ma-selfwake-status-")
        self.addCleanup(temporary.cleanup)
        self.bus = Path(temporary.name)
        self.role = "synthetic-reviewer"
        self.role_dir = self.bus / self.role
        self.role_dir.mkdir()
        self.host_alive = Mock(return_value=False)
        self.scan = Mock(return_value=({}, {self.role}))
        self.process = Mock()
        self.process.cmdline.return_value = ["python", "agentbus.py", "wait", "--role", self.role]
        self.messenger_process = Mock()
        self.messenger_process.cmdline.return_value = ["python", "codex_messenger.py",
                                                       "--session-id", "synthetic-session"]
        psutil = types.ModuleType("psutil")
        psutil.Process = Mock(side_effect=lambda pid: self.messenger_process if pid == 301 else self.process)
        psutil.pid_exists = Mock(return_value=False)
        package = types.ModuleType("bus")
        package.__path__ = []
        config = types.ModuleType("bus.config")
        config.BUS = self.bus
        config.cfg = lambda name, default=None: default
        proc = types.ModuleType("bus.proc")
        proc._proc_scan = self.scan
        proc._age_seconds = lambda value: 1 if value else None
        proc._host_pid_alive = self.host_alive
        recovery = types.ModuleType("bus.codex_recovery")
        recovery.requeue_blocked = lambda *args: False
        recovery.fence_tasks = Mock(side_effect=AssertionError("No recovery operation expected"))
        replacements = {m.__name__: m for m in (package, config, proc, recovery, psutil)}
        modules = patch.dict(sys.modules, replacements)
        modules.start()
        self.addCleanup(modules.stop)
        spec = importlib.util.spec_from_file_location(
            "bus.selfwake", ROOT / "agent_bus" / "bus" / "selfwake.py")
        self.module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = self.module
        spec.loader.exec_module(self.module)
        self.write("presence.json", {"host_kind": "codex", "host_pid": 201,
                                      "host_session_id": "synthetic-session",
                                      "mode": "listening", "last_seen": "synthetic-fresh"})
        self.messenger()
        self.registration()

    def write(self, name, value):
        (self.role_dir / name).write_text(json.dumps(value), encoding="utf-8")

    def registration(self, **changes):
        value = {"wait_pid": 101, "host_kind": "codex", "host_pid": 201}
        value.update(changes)
        self.write("wait.reg.json", value)

    def messenger(self, **changes):
        value = {"session_id": "synthetic-session", "pid": 301, "heartbeat": "synthetic-fresh",
                 "status": "armed", "wait_alive": True, "app_ready": True, "pump_alive": True}
        value.update(changes)
        self.write("messenger.state.json", value)

    def legacy_presence(self):
        self.write("presence.json", {"host_kind": "claude", "host_pid": 201,
                                      "mode": "listening", "last_seen": "synthetic-fresh"})

    def test_registered_dead_host_is_explicitly_unhealthy(self):
        armed, detail = self.module._reg_wait_state(self.role)
        self.assertIs(armed, False)
        self.assertIn("孤儿", detail)
        self.host_alive.assert_called_once_with(201, "codex")

    def test_orphan_is_stale_even_when_wait_roles_contains_it(self):
        state, detail = self.module._self_wake_state(self.role, wait_roles={self.role})
        self.assertEqual(state, "stale")
        self.assertIn("宿主 codex(201)已退", detail)
        self.scan.assert_not_called()

    def test_orphan_does_not_scan_for_a_fallback(self):
        self.scan.side_effect = AssertionError("Known orphan must not fall back")
        state, _ = self.module._self_wake_state(self.role)
        self.assertEqual(state, "stale")
        self.scan.assert_not_called()

    def test_legacy_claude_orphan_is_also_stale(self):
        self.write("wait.reg.json", {"wait_pid": 101, "host_claude_pid": 202})
        state, detail = self.module._self_wake_state(self.role, wait_roles={self.role})
        self.assertEqual(state, "stale")
        self.assertIn("claude(202)已退", detail)
        self.host_alive.assert_called_once_with(202, "claude")

    def test_live_registered_host_remains_armed(self):
        self.host_alive.return_value = True
        state, detail = self.module._self_wake_state(self.role, wait_roles=set())
        self.assertEqual(state, "armed")
        self.assertIn("App Server 就绪", detail)
        self.scan.assert_not_called()

    def test_absent_registration_keeps_legacy_scan_fallback(self):
        self.legacy_presence()
        (self.role_dir / "wait.reg.json").unlink()
        self.assertEqual(self.module._reg_wait_state(self.role), (None, None))
        state, detail = self.module._self_wake_state(self.role)
        self.assertEqual(state, "armed")
        self.assertIn("无 reg", detail)
        self.scan.assert_called_once_with()

    def test_reused_wait_pid_is_unavailable_not_a_confirmed_orphan(self):
        self.legacy_presence()
        self.process.cmdline.return_value = ["python", "unrelated.py"]
        self.assertEqual(self.module._reg_wait_state(self.role), (None, None))
        state, _ = self.module._self_wake_state(self.role, wait_roles={self.role})
        self.assertEqual(state, "armed")
        self.host_alive.assert_not_called()

    def test_legacy_registration_without_host_remains_supported(self):
        self.legacy_presence()
        self.write("wait.reg.json", {"wait_pid": 101})
        state, _ = self.module._self_wake_state(self.role, wait_roles=set())
        self.assertEqual(state, "armed")
        self.host_alive.assert_not_called()

    def test_missing_messenger_is_unknown_even_with_live_registered_wait(self):
        self.host_alive.return_value = True
        (self.role_dir / "messenger.state.json").unlink()
        state, detail = self.module._self_wake_state(self.role, wait_roles={self.role})
        self.assertEqual("recent", state)
        self.assertIn("尚未确认", detail)
        self.scan.assert_not_called()

    def test_corrupt_messenger_is_unknown_even_with_wait_scan_evidence(self):
        (self.role_dir / "wait.reg.json").unlink()
        (self.role_dir / "messenger.state.json").write_text("{invalid", encoding="utf-8")
        state, detail = self.module._self_wake_state(self.role, wait_roles={self.role})
        self.assertEqual("recent", state)
        self.assertIn("状态不可用", detail)
        self.scan.assert_not_called()

    def test_wrong_messenger_shape_and_missing_session_do_not_claim_ready(self):
        self.host_alive.return_value = True
        for value in ([], None, "invalid"):
            with self.subTest(value=value):
                self.write("messenger.state.json", value)
                state, _ = self.module._self_wake_state(self.role, wait_roles={self.role})
                self.assertEqual("recent", state)
        self.messenger()
        self.write("presence.json", {"host_kind": "codex", "host_pid": 201, "mode": "listening"})
        self.assertEqual("recent", self.module._self_wake_state(self.role, wait_roles={self.role})[0])

    def test_missing_messenger_does_not_hide_confirmed_dead_host(self):
        (self.role_dir / "messenger.state.json").unlink()
        state, detail = self.module._self_wake_state(self.role, wait_roles={self.role})
        self.assertEqual("stale", state)
        self.assertIn("孤儿", detail)

    def test_other_session_or_incomplete_chain_cannot_be_armed(self):
        self.host_alive.return_value = True
        for changes, expected in (({"session_id": "other-session"}, "stale"),
                                  ({"app_ready": False}, "recent"),
                                  ({"pump_alive": False}, "recent"),
                                  ({"wait_alive": False}, "recent")):
            with self.subTest(changes=changes):
                self.messenger(**changes)
                self.assertEqual(expected, self.module._self_wake_state(self.role, wait_roles={self.role})[0])

    def test_future_messenger_heartbeat_cannot_be_armed(self):
        self.host_alive.return_value = True
        self.module._age_seconds = lambda value: -1
        self.assertEqual("stale", self.module._self_wake_state(self.role, wait_roles={self.role})[0])

    def test_explicit_park_still_takes_precedence(self):
        self.write("parked.json", {"since": "synthetic-time"})
        state, _ = self.module._self_wake_state(self.role, wait_roles={self.role})
        self.assertEqual(state, "parked")
        self.host_alive.assert_not_called()

    def test_daemon_mode_keeps_its_existing_pid_rule(self):
        self.write("presence.json", {"mode": "daemon", "daemon_pid": 301})
        for alive, expected in ((True, "armed"), (False, "stale")):
            with self.subTest(alive=alive):
                self.module.psutil.pid_exists.return_value = alive
                state, _ = self.module._self_wake_state(self.role, wait_roles={self.role})
                self.assertEqual(state, expected)
        self.host_alive.assert_not_called()


if __name__ == "__main__":
    unittest.main()
