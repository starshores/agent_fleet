"""Ticket/context races against private registry, IO and subprocess substitutes.

Only the three reviewed source modules are loaded. Their bus imports are fake
modules and every data path is a synthetic TemporaryDirectory. No real hooks,
bus commands, app-server processes, memory, sessions or credentials are used.
"""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager, redirect_stdout
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import threading
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch


SOURCE = Path(__file__).resolve().parents[1]


class RoleBindingTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="codex-compat-role-binding-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.bus = self.root / "agent_bus"
        self.bus.mkdir()
        self.cwd = str(self.root / "shared-project")
        Path(self.cwd).mkdir()
        self.role_cwd = str(self.root / "declared-role-project")
        Path(self.role_cwd).mkdir()
        self.now = 1000.0
        self.assignment_lock = threading.RLock()
        self.agents = {
            "synthetic-role": {"caps": ["self-wake"]},
            "other-role": {"caps": ["self-wake"]},
        }
        self.write_json(self.bus / "agents.json", self.agents)
        self.memory = self.root / "synthetic-role.MEMORY.md"
        self.memory.write_text("SYNTHETIC_ROLE_ONLY_MEMORY", encoding="utf-8")
        self.profile = {"role": "synthetic-role", "cwd": self.role_cwd,
                        "one_liner": "SYNTHETIC_ROLE_ONLY_PROFILE", "boundary": "Fixture only"}
        self.projects_read = []
        self.memory_resolved = []
        self.hello_calls = []
        self.bridge_calls = []
        bus_module = ModuleType("bus")
        bus_module.__path__ = []
        config = ModuleType("bus.config")
        config.BUS = self.bus
        config._now = lambda: "synthetic-time"
        storage = ModuleType("bus.io")
        storage._write_json = self.write_json
        storage.json_lock = self.lock_json
        roster = ModuleType("bus.roster")
        roster._load_agents = lambda: json.loads((self.bus / "agents.json").read_text(encoding="utf-8"))
        roster.load_role_profiles = lambda: {"roles": [self.profile]}
        proc = ModuleType("bus.proc")
        proc._host_ancestor = lambda: (None, None, None, [])
        proc._host_session_id = lambda kind: None
        substitutes = {"bus": bus_module, "bus.config": config, "bus.io": storage,
                       "bus.roster": roster, "bus.proc": proc}
        original_path = list(sys.path)
        try:
            with patch.dict(sys.modules, substitutes):
                self.assignment = self.load("agent_bus/bus/codex_startup.py", "isolated_codex_assignment")
                sys.modules["bus.codex_startup"] = self.assignment
                self.context = self.load(".codex/hooks/agentbus_codex_session_context.py", "isolated_codex_context")
                self.startup = self.load(".codex/hooks/agentbus_codex_session_start.py", "isolated_codex_startup")
        finally:
            sys.path[:] = original_path
        self.assignment.time = SimpleNamespace(time=lambda: self.now)
        self.context.ROOT = self.root
        self.context.BUS = self.bus
        self.context._project_claude_context = self.project_context
        self.context._memory_index = self.memory_index
        self.context._role_memory_files = lambda *_: []
        self.startup.ROOT = self.root
        self.startup.AGENTS = self.bus / "agents.json"
        self.startup.BUS = self.bus / "never-executed-agentbus.py"
        self.startup.subprocess = SimpleNamespace(run=self.fake_hello)
        self.startup._start_messenger = self.fake_bridge
        self.arm()

    @staticmethod
    def load(relative, name):
        spec = importlib.util.spec_from_file_location(name, SOURCE / relative)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def write_json(self, path, value):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + ".fixture-write")
        temporary.write_text(json.dumps(value), encoding="utf-8")
        temporary.replace(path)

    @contextmanager
    def lock_json(self, path, *args, **kwargs):
        self.assertEqual(self.bus / "codex_role_next.json", Path(path))
        with self.assignment_lock:
            yield

    def arm(self, **changes):
        value = {"status": "pending", "role": "synthetic-role",
                 "cwd": self.assignment._norm_cwd(self.cwd), "created": "synthetic-time", "expires_epoch": 2000.0}
        value.update(changes)
        self.write_json(self.assignment.ASSIGNMENT, value)

    def ticket(self):
        return json.loads(self.assignment.ASSIGNMENT.read_text(encoding="utf-8"))

    def presence(self, role, session_id):
        self.write_json(self.bus / role / "presence.json",
                        {"role": role, "host_kind": "codex", "host_session_id": session_id})

    def project_context(self, cwd):
        self.projects_read.append(cwd)
        return "SYNTHETIC_PROJECT:" + str(cwd)

    def memory_index(self, role, profile):
        self.memory_resolved.append(role)
        return self.memory

    def fake_hello(self, command, **kwargs):
        self.hello_calls.append((command, kwargs))
        role = command[command.index("--role") + 1]
        self.presence(role, kwargs["env"]["CODEX_SESSION_ID"])
        return SimpleNamespace(returncode=0, stderr="")

    def fake_bridge(self, role, session_id, cwd):
        self.bridge_calls.append((role, session_id, cwd))
        return 0

    def start(self, sid="session-one", cwd=None):
        event = {"hook_event_name": "SessionStart", "source": "startup", "session_id": sid,
                 "cwd": self.cwd if cwd is None else cwd}
        with patch.object(sys, "stdin", io.StringIO(json.dumps(event))):
            return self.startup.main()

    def assert_no_role_context(self, context):
        self.assertNotIn("## Explicit agent-bus role:", context)
        self.assertNotIn("SYNTHETIC_ROLE_ONLY_PROFILE", context)
        self.assertNotIn("SYNTHETIC_ROLE_ONLY_MEMORY", context)

    def test_context_first_reserves_then_startup_reenters_and_registers(self):
        result = self.context._build_context("session-one", self.cwd)
        self.assertIn("SYNTHETIC_ROLE_ONLY_MEMORY", result)
        self.assertEqual("claimed", self.ticket()["status"])
        self.assertEqual("session-one", self.ticket()["session_id"])
        self.assertEqual([], self.hello_calls)
        self.assertFalse((self.bus / "synthetic-role" / "presence.json").exists())
        before = self.assignment.ASSIGNMENT.read_bytes()
        self.assertEqual(0, self.start())
        self.assertEqual(before, self.assignment.ASSIGNMENT.read_bytes())
        self.assertEqual(1, len(self.hello_calls))
        self.assertEqual(1, len(self.bridge_calls))

    def test_startup_first_registers_then_context_recovers_without_new_ticket(self):
        self.assertEqual(0, self.start())
        self.now = 3000
        result = self.context._build_context("session-one", self.cwd)
        self.assertIn("SYNTHETIC_ROLE_ONLY_MEMORY", result)
        self.assertEqual(1, len(self.hello_calls))
        self.assertEqual(0, self.start())
        self.assertEqual(1, len(self.hello_calls), "Exact registered recovery must not require hello or a new ticket")

    def test_same_session_context_and_startup_can_race(self):
        barrier = threading.Barrier(2)
        def context():
            barrier.wait()
            return self.context._build_context("session-one", self.cwd)
        def startup():
            barrier.wait()
            return self.start()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = [pool.submit(context), pool.submit(startup)]
            self.assertIn("SYNTHETIC_ROLE_ONLY_MEMORY", results[0].result(timeout=3))
            self.assertEqual(0, results[1].result(timeout=3))
        self.assertEqual("session-one", self.ticket()["session_id"])
        self.assertEqual(1, len(self.hello_calls))

    def test_distinct_sessions_race_only_one_gets_role_context(self):
        barrier = threading.Barrier(2)
        def context(sid):
            barrier.wait()
            return sid, self.context._build_context(sid, self.cwd)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(context, ("session-one", "session-two")))
        winners = [(sid, result) for sid, result in results if "SYNTHETIC_ROLE_ONLY_MEMORY" in result]
        self.assertEqual(1, len(winners))
        self.assertEqual(winners[0][0], self.ticket()["session_id"])
        self.assertEqual(["synthetic-role"], self.memory_resolved)
        for sid, result in results:
            if sid != winners[0][0]:
                self.assert_no_role_context(result)

    def test_two_sessions_competing_across_context_and_startup(self):
        barrier = threading.Barrier(2)
        def context():
            barrier.wait()
            return self.context._build_context("context-session", self.cwd)
        def startup():
            barrier.wait()
            return self.start("startup-session")
        with ThreadPoolExecutor(max_workers=2) as pool:
            first, second = pool.submit(context), pool.submit(startup)
            result = first.result(timeout=3)
            self.assertEqual(0, second.result(timeout=3))
        winner = self.ticket()["session_id"]
        if winner == "context-session":
            self.assertIn("SYNTHETIC_ROLE_ONLY_MEMORY", result)
            self.assertEqual([], self.hello_calls)
            self.assertEqual([], self.bridge_calls)
        else:
            self.assertEqual("startup-session", winner)
            self.assert_no_role_context(result)
            self.assertEqual(1, len(self.hello_calls))

    def test_claim_reentry_requires_exact_session_and_cwd_and_does_not_rewrite(self):
        claim = self.assignment.claim_role_assignment
        self.assertEqual("synthetic-role", claim("session-one", self.cwd))
        before = self.assignment.ASSIGNMENT.read_bytes()
        self.assertEqual("synthetic-role", claim("session-one", self.cwd))
        self.assertIsNone(claim("session-two", self.cwd))
        self.assertIsNone(claim("session-one", str(self.root / "different-project")))
        self.assertEqual(before, self.assignment.ASSIGNMENT.read_bytes())

    def test_expired_pending_or_claimed_ticket_never_supplies_context(self):
        for status in ("pending", "claimed"):
            with self.subTest(status=status):
                self.arm(status=status, session_id="session-one", expires_epoch=self.now)
                self.assertIsNone(self.assignment.claim_role_assignment("session-one", self.cwd))
                self.assert_no_role_context(self.context._build_context("session-one", self.cwd))
                self.assertEqual(0, self.start())
        self.assertEqual([], self.memory_resolved)
        self.assertEqual([], self.hello_calls)
        self.assertEqual([], self.bridge_calls)

    def test_reentry_rechecks_registered_role_and_selfwake_permission(self):
        self.assertEqual("synthetic-role", self.assignment.claim_role_assignment("session-one", self.cwd))
        for registration in ({"caps": []}, {"caps": "self-wake"}, None):
            with self.subTest(registration=registration):
                agents = {"other-role": {"caps": ["self-wake"]}}
                if registration is not None:
                    agents["synthetic-role"] = registration
                self.write_json(self.bus / "agents.json", agents)
                self.assertIsNone(self.assignment.claim_role_assignment("session-one", self.cwd))
                self.assert_no_role_context(self.context._build_context("session-one", self.cwd))
                self.assertEqual(0, self.start())
        self.assertEqual([], self.memory_resolved)
        self.assertEqual([], self.hello_calls)

    def test_empty_or_invalid_session_is_rejected_before_context_reads(self):
        for sid in (None, "", "  ", 7, [], " session-one"):
            with self.subTest(session_id=sid):
                self.assertIsNone(self.assignment.claim_role_assignment(sid, self.cwd))
                self.assert_no_role_context(self.context._build_context(sid, self.cwd))
                self.assertEqual(0, self.start(sid))
        self.assertEqual("pending", self.ticket()["status"])
        self.assertEqual([], self.projects_read)
        self.assertEqual([], self.memory_resolved)
        self.assertEqual([], self.hello_calls)

    def test_duplicate_presence_identity_fails_before_ticket_or_context_reads(self):
        self.presence("synthetic-role", "session-one")
        self.presence("other-role", "session-one")
        before = self.assignment.ASSIGNMENT.read_bytes()
        result = self.context._build_context("session-one", self.cwd)
        self.assertIn("multiple roles", result)
        self.assert_no_role_context(result)
        with self.assertRaisesRegex(RuntimeError, "multiple"):
            self.start()
        self.assertEqual(before, self.assignment.ASSIGNMENT.read_bytes())
        self.assertEqual([], self.projects_read)
        self.assertEqual([], self.memory_resolved)
        self.assertEqual([], self.hello_calls)

    def test_bound_session_recovery_does_not_consume_another_pending_ticket(self):
        self.presence("synthetic-role", "session-one")
        self.arm(role="other-role")
        before = self.assignment.ASSIGNMENT.read_bytes()
        self.assertIn("SYNTHETIC_ROLE_ONLY_MEMORY", self.context._build_context("session-one", self.cwd))
        self.assertEqual(0, self.start())
        self.assertEqual(before, self.assignment.ASSIGNMENT.read_bytes())
        self.assertEqual([], self.hello_calls)
        self.assertEqual(1, len(self.bridge_calls))

    def test_failed_hello_can_retry_its_exact_reserved_ticket(self):
        self.context._build_context("session-one", self.cwd)
        self.startup.subprocess.run = lambda *args, **kwargs: SimpleNamespace(returncode=1, stderr="fixture hello failure")
        with self.assertRaisesRegex(RuntimeError, "hello failed"):
            self.start()
        self.assertEqual("session-one", self.ticket()["session_id"])
        self.assertEqual([], self.bridge_calls)
        self.startup.subprocess.run = self.fake_hello
        self.assertEqual(0, self.start())
        self.assertEqual(1, len(self.hello_calls))

    def test_new_assignment_cannot_replace_context_reservation_before_hello(self):
        self.assignment.claim_role_assignment("session-one", self.cwd)
        before = self.assignment.ASSIGNMENT.read_bytes()
        args = SimpleNamespace(role="other-role", cwd=self.cwd)
        with redirect_stdout(io.StringIO()), self.assertRaises(SystemExit) as error:
            self.assignment.cmd_codex_role_next(args)
        self.assertEqual(2, error.exception.code)
        self.assertEqual(before, self.assignment.ASSIGNMENT.read_bytes())
        self.start()
        with redirect_stdout(io.StringIO()):
            self.assignment.cmd_codex_role_next(args)
        self.assertEqual("other-role", self.ticket()["role"])
        self.assertEqual("pending", self.ticket()["status"])

    def test_invalid_ticket_expiry_and_shape_fail_closed(self):
        for deadline in (None, True, "2000", float("nan"), float("inf")):
            with self.subTest(deadline=deadline):
                self.arm(expires_epoch=deadline)
                self.assertIsNone(self.assignment.claim_role_assignment("session-one", self.cwd))
        self.write_json(self.assignment.ASSIGNMENT, [])
        self.assertIsNone(self.assignment.claim_role_assignment("session-one", self.cwd))

    def test_failed_ticket_persistence_cannot_expose_role_context(self):
        def fail(*args, **kwargs):
            raise OSError("Synthetic ticket persistence failure")
        self.assignment._write_json = fail
        with self.assertRaises(OSError):
            self.context._build_context("session-one", self.cwd)
        self.assertEqual("pending", self.ticket()["status"])
        self.assertEqual([], self.projects_read)
        self.assertEqual([], self.memory_resolved)
        self.assertEqual([], self.hello_calls)


if __name__ == "__main__":
    unittest.main()
