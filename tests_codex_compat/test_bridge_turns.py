"""Synthetic App Server events only; no bus config, histories or processes."""
import contextlib
import importlib.util
import io
from pathlib import Path
import queue
import sys
import threading
import types
import unittest
from unittest.mock import Mock, patch


def load_bridge():
    bus = types.ModuleType("bus")
    bus.__path__ = []
    config = types.ModuleType("bus.config")
    config.BUS = Path("synthetic-bus-never-opened")
    mailbox = types.ModuleType("bus.mailbox")
    mailbox._claim = Mock(side_effect=AssertionError("real claim forbidden"))
    mailbox._oldest_claimable = Mock(side_effect=AssertionError("real inbox forbidden"))
    bus_io = types.ModuleType("bus.io")
    bus_io.json_lock = Mock(side_effect=AssertionError("real state write forbidden"))
    bus_io._write_json = Mock(side_effect=AssertionError("real state write forbidden"))
    recovery = types.ModuleType("bus.codex_recovery")
    recovery.fence_tasks = Mock(return_value=[])
    recovery.reconcile_unfinished = Mock(return_value=[])
    recovery.validate_delivery = Mock(return_value=None)
    modules = {"bus": bus, "bus.config": config, "bus.mailbox": mailbox,
               "bus.io": bus_io, "bus.codex_recovery": recovery}
    source = Path(__file__).resolve().parents[1] / ".codex" / "codex_wait_bridge.py"
    spec = importlib.util.spec_from_file_location("synthetic_codex_bridge", source)
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, modules), patch.object(sys, "path", list(sys.path)):
        spec.loader.exec_module(module)
    return module


bridge = load_bridge()
REAL_VERIFY_IDENTITY = bridge.verify_identity
bridge.verify_identity = Mock()
SID = "synthetic-thread"
ROLE = "synthetic-role"
ITEMS = [{"id": "synthetic-task", "task": "synthetic task text"}]


def notification(turn_id="owned", status="completed", thread=SID, **extra):
    return {"method": "turn/completed", "params": {
        "threadId": thread, "turn": {"id": turn_id, "status": status, **extra}}}


class BridgeTurnTests(unittest.TestCase):
    def setUp(self):
        bridge.fence_tasks.reset_mock(return_value=True, side_effect=True)
        bridge.fence_tasks.return_value = []
        bridge.reconcile_unfinished.reset_mock(return_value=True, side_effect=True)
        bridge.reconcile_unfinished.return_value = []
        bridge.validate_delivery.reset_mock(return_value=True, side_effect=True)
        bridge.validate_delivery.return_value = None
        bridge.verify_identity.reset_mock(return_value=True, side_effect=True)
        bridge.verify_identity.return_value = None
        self.app = bridge.AppServer.__new__(bridge.AppServer)
        self.app.thread_id, self.app.role = SID, ROLE
        self.app._init_turn_tracking()
        self.states = []
        self.app._write_turn_state = lambda *args: self.states.append(args)

    def begin(self, result=None):
        self.app.begin_owned_turn(SID)
        self.app.associate_started_turn(result or {"turn": {"id": "owned", "status": "inProgress"}})

    def dispatch(self, events=None, result=None):
        def rpc(method, params, timeout):
            self.assertEqual("turn/start", method)
            self.assertEqual(SID, params["threadId"])
            for event in events if events is not None else [notification()]:
                self.app._handle_notification(event)
            return result if result is not None else {"turn": {"id": "owned", "status": "inProgress"}}
        self.app.rpc = rpc
        with contextlib.redirect_stdout(io.StringIO()):
            bridge._dispatch_batch(self.app, ROLE, SID, "synthetic-cwd", ITEMS)

    def test_other_turns_do_not_acquire_ownership_or_release_wait(self):
        self.app._handle_notification({"method": "turn/started", "params": {
            "threadId": SID, "turn": {"id": "user-turn"}}})
        self.assertIsNone(self.app.active_turn_id)
        self.begin()
        for message in (
            notification("user-turn"), notification(thread="another-thread"),
            {"method": "turn/started", "params": {"threadId": SID, "turn": {"id": "user-turn"}}},
            {"method": "item/started", "params": {"threadId": SID, "turnId": "user-turn", "item": {"type": "commandExecution"}}},
            {"method": "item/completed", "params": {"threadId": SID}},
        ):
            self.app._handle_notification(message)
        self.assertEqual("owned", self.app.active_turn_id)
        self.assertFalse(self.app.turn_finished.is_set())
        self.assertEqual([("thinking", "turn/start", "owned")], self.states)
        self.app._handle_notification(notification())
        self.assertTrue(self.app.turn_finished.is_set())
        self.assertEqual("completed", self.app.turn_outcome)

    def test_completion_before_rpc_id_is_correlated_and_receipts_checked(self):
        self.dispatch(events=[notification("user-turn", "failed"), notification()])
        self.assertFalse(self.app._dispatch_inflight)
        self.assertIsNone(self.app.active_turn_id)
        bridge.reconcile_unfinished.assert_called_once_with(ROLE, ITEMS, "missing_task_receipt")
        bridge.fence_tasks.assert_not_called()
        bridge.validate_delivery.assert_called_once_with(ROLE, SID, ITEMS)

    def test_unrelated_early_completion_is_not_reused_for_new_batch(self):
        self.app.begin_owned_turn(SID)
        self.app._handle_notification(notification("user-turn"))
        self.assertFalse(self.app.turn_finished.is_set())
        self.app.associate_started_turn({"turn": {"id": "owned"}})
        self.assertFalse(self.app.turn_finished.is_set())
        self.app._handle_notification(notification())
        self.app.release_completed_turn()
        self.app.begin_owned_turn(SID)
        self.app._handle_notification(notification())
        self.app.associate_started_turn({"turn": {"id": "second"}})
        self.assertFalse(self.app.turn_finished.is_set())
        self.assertEqual("second", self.app.active_turn_id)

    def test_matching_failure_interruption_unknown_and_error_are_fenced(self):
        for event, reason in (
            (notification(status="failed"), "turn_failed"),
            (notification(status="interrupted"), "turn_interrupted"),
            (notification(status="unknownFutureStatus"), "unknown_turn_status"),
            (notification(status=None), "unknown_turn_status"),
            (notification(error={"message": "private diagnostic"}), "turn_failed"),
            ({"method": "error", "params": {"threadId": SID, "turnId": "owned",
                                               "willRetry": False, "error": {"message": "private diagnostic"}}}, "turn_failed"),
        ):
            with self.subTest(reason=reason, event=event):
                self.setUp()
                with self.assertRaises(bridge.TurnFailure) as caught:
                    self.dispatch(events=[event])
                self.assertEqual(reason, caught.exception.reason)
                self.assertTrue(self.app._dispatch_blocked)
                self.assertEqual({"turn_failed": "failed", "turn_interrupted": "interrupted"}.get(reason, "unknown"),
                                 self.app.turn_outcome)
                bridge.fence_tasks.assert_called_once_with(ROLE, ITEMS, reason)
                bridge.reconcile_unfinished.assert_not_called()
                self.assertNotIn("private diagnostic", str(caught.exception))
                with self.assertRaises(bridge.TurnFailure):
                    self.app.begin_owned_turn(SID)

    def test_retrying_or_unrelated_errors_do_not_end_our_turn(self):
        self.begin()
        for params in ({"threadId": SID, "turnId": "owned", "willRetry": True},
                       {"threadId": SID, "turnId": "other", "willRetry": False},
                       {"threadId": SID, "willRetry": False}):
            self.app._handle_notification({"method": "error", "params": params})
        self.assertFalse(self.app.turn_finished.is_set())

    def test_conflicting_terminal_candidates_fail_closed(self):
        with self.assertRaises(bridge.TurnFailure) as caught:
            self.dispatch(events=[notification(), notification(status="interrupted")])
        self.assertEqual("conflicting_turn_terminal", caught.exception.reason)
        bridge.fence_tasks.assert_called_once()

    def test_matching_terminal_response_is_authoritative_and_conflict_is_rejected(self):
        self.dispatch(events=[], result={"turn": {"id": "owned", "status": "completed"}})
        self.setUp()
        with self.assertRaises(bridge.TurnFailure) as caught:
            self.dispatch(events=[notification()], result={"turn": {"id": "owned", "status": "failed"}})
        self.assertEqual("conflicting_turn_terminal", caught.exception.reason)

    def test_missing_or_wrong_thread_rpc_id_fences_batch(self):
        for response, reason in (({}, "missing_turn_id"),
                                 ({"turn": {"id": ""}}, "missing_turn_id"),
                                 ({"turn": {"id": "owned", "threadId": "other"}}, "turn_thread_mismatch")):
            with self.subTest(response=response):
                self.setUp()
                with self.assertRaises(bridge.TurnFailure) as caught:
                    self.dispatch(events=[notification()], result=response)
                self.assertEqual(reason, caught.exception.reason)
                bridge.fence_tasks.assert_called_once_with(ROLE, ITEMS, reason)

    def test_conflicting_notification_thread_fields_and_missing_turn_id_ignored(self):
        self.begin()
        self.app._handle_notification(notification(thread=SID, threadId="other"))
        self.app._handle_notification({"method": "turn/completed", "params": {
            "threadId": SID, "turn": {"status": "completed"}}})
        self.app._handle_notification({"method": "turn/completed", "params": {
            "threadId": SID, "turnId": "other", "turn": {"id": "owned", "status": "completed"}}})
        self.assertFalse(self.app.turn_finished.is_set())

    def test_timeout_rpc_error_and_interrupt_fence_without_next_dispatch(self):
        for error, reason in ((TimeoutError("fake timeout"), "turn_start_timeout"),
                              (RuntimeError("busy user turn"), "bridge_dispatch_error"),
                              (KeyboardInterrupt(), "bridge_interrupted")):
            with self.subTest(reason=reason):
                self.setUp()
                self.app.rpc = Mock(side_effect=error)
                with self.assertRaises(type(error)):
                    bridge._dispatch_batch(self.app, ROLE, SID, "synthetic-cwd", ITEMS)
                bridge.fence_tasks.assert_called_once_with(ROLE, ITEMS, reason)
                self.assertTrue(self.app._dispatch_blocked)

    def test_terminal_wait_timeout_fences_uncertain_batch(self):
        self.app.turn_finished.wait = Mock(return_value=False)
        with self.assertRaises(bridge.TurnFailure) as caught:
            self.dispatch(events=[])
        self.assertEqual("turn_timeout", caught.exception.reason)
        bridge.fence_tasks.assert_called_once_with(ROLE, ITEMS, "turn_timeout")

    def test_disconnect_while_rpc_id_pending_cannot_be_cleared_by_response(self):
        self.app.closed = threading.Event()
        self.app.ready = threading.Event()
        self.app.ready.set()
        self.app.disconnected = threading.Event()
        self.app.connection_lock = threading.Lock()
        self.app.lock = threading.Lock()
        self.app.pending = {}
        self.app.proc = object()
        self.app._recv_frame = Mock(side_effect=OSError("synthetic disconnect"))
        self.app.begin_owned_turn(SID)
        self.app._handle_notification(notification())
        with contextlib.redirect_stderr(io.StringIO()):
            self.app._read_loop(self.app.proc)
        self.assertTrue(self.app.turn_finished.is_set())
        self.assertTrue(self.app._dispatch_blocked)
        self.assertEqual("app_server_disconnected", self.app.turn_error)
        with self.assertRaises(bridge.TurnFailure):
            self.app.associate_started_turn({"turn": {"id": "owned"}})

    def test_candidate_buffer_is_bounded_and_overflow_is_not_success(self):
        self.app.begin_owned_turn(SID)
        for index in range(129):
            self.app._handle_notification(notification(str(index)))
        self.assertEqual(128, len(self.app._terminal_candidates))
        self.assertEqual("turn_candidate_overflow", self.app.turn_error)
        with self.assertRaises(bridge.TurnFailure):
            self.app.associate_started_turn({"turn": {"id": "owned"}})

    def test_model_completed_without_real_task_receipt_stops_bridge(self):
        bridge.reconcile_unfinished.return_value = ["synthetic-task"]
        with self.assertRaises(bridge.TurnFailure) as caught:
            self.dispatch()
        self.assertEqual("missing_task_receipt", caught.exception.reason)
        self.assertTrue(self.app._dispatch_blocked)
        bridge.fence_tasks.assert_called_once_with(ROLE, ITEMS, "missing_task_receipt")

    def test_persistence_failure_remains_visible(self):
        bridge.fence_tasks.side_effect = OSError("synthetic storage failure")
        with self.assertRaisesRegex(OSError, "synthetic storage failure"):
            self.dispatch(events=[notification(status="failed")])
        self.assertTrue(self.app._dispatch_blocked)

    def test_startup_failure_fences_claimed_initial_batch_and_releases_lock(self):
        lock = object()
        with patch.object(sys, "argv", ["bridge", "--role", ROLE, "--session-id", SID]), \
                patch.object(bridge, "verify_identity"), \
                patch.object(bridge, "_acquire_session_lock", return_value=lock), \
                patch.object(bridge, "_release_session_lock") as release, \
                patch.object(bridge, "drain_inbox", return_value=ITEMS), \
                patch.object(bridge, "AppServer", side_effect=RuntimeError("synthetic startup")):
            with self.assertRaisesRegex(RuntimeError, "synthetic startup"):
                bridge.main()
        bridge.fence_tasks.assert_called_once_with(ROLE, ITEMS, "bridge_stopped_before_dispatch")
        release.assert_called_once_with(lock)

    def test_pump_start_failure_fences_queued_claims_and_initial_batch(self):
        pump = Mock()
        pump.items = queue.Queue()
        pump.items.put({"id": "queued-task"})
        pump.start.side_effect = RuntimeError("synthetic pump failure")
        with patch.object(sys, "argv", ["bridge", "--role", ROLE, "--session-id", SID]), \
                patch.object(bridge, "verify_identity"), \
                patch.object(bridge, "_acquire_session_lock", return_value=object()), \
                patch.object(bridge, "_release_session_lock"), \
                patch.object(bridge, "drain_inbox", return_value=ITEMS), \
                patch.object(bridge, "AppServer", return_value=Mock()), \
                patch.object(bridge, "WaitPump", return_value=pump):
            with self.assertRaisesRegex(RuntimeError, "synthetic pump failure"):
                bridge.main()
        bridge.fence_tasks.assert_called_once_with(ROLE, ITEMS + [{"id": "queued-task"}], "bridge_stopped_before_dispatch")
        pump.stop.assert_called_once()

    def test_batch_assembly_failure_fences_already_collected_tasks(self):
        pump = Mock()
        pump.items = queue.Queue()
        pump.items.put({"id": "queued-task"})
        pump.items.put(RuntimeError("synthetic pump failure"))
        with patch.object(bridge, "drain_inbox", return_value=[{"id": "drained-task"}]):
            with self.assertRaisesRegex(RuntimeError, "synthetic pump failure"):
                bridge._take_batch(pump, ITEMS[0], ROLE, SID)
        self.assertEqual(["synthetic-task", "drained-task", "queued-task"],
                         [item["id"] for item in bridge.fence_tasks.call_args.args[1]])

    def test_partial_inbox_drain_failure_fences_claims_not_yet_returned(self):
        with patch.object(bridge, "_oldest_claimable", return_value=object()), \
                patch.object(bridge, "_claim", side_effect=[ITEMS[0], OSError("synthetic claim failure")]):
            with self.assertRaisesRegex(OSError, "synthetic claim failure"):
                bridge.drain_inbox(ROLE, SID)
        bridge.fence_tasks.assert_called_once_with(ROLE, ITEMS, "bridge_inbox_drain_failed")

    def test_claim_checks_identity_each_time_and_passes_explicit_session(self):
        paths = [object(), object()]
        bridge.verify_identity.side_effect = [None, RuntimeError("synthetic rebind")]
        with patch.object(bridge, "_oldest_claimable", side_effect=paths) as oldest, \
                patch.object(bridge, "_claim", return_value=ITEMS[0]) as claim:
            with self.assertRaisesRegex(RuntimeError, "synthetic rebind"):
                bridge.drain_inbox(ROLE, SID)
        self.assertEqual(1, oldest.call_count)
        claim.assert_called_once_with(ROLE, paths[0], codex_session_id=SID)
        bridge.fence_tasks.assert_called_once_with(ROLE, ITEMS, "bridge_inbox_drain_failed")

    def test_rebound_dispatch_is_refused_before_model_request(self):
        self.app.rpc = Mock()
        bridge.verify_identity.side_effect = RuntimeError("synthetic rebind")
        with self.assertRaisesRegex(RuntimeError, "synthetic rebind"):
            bridge._dispatch_batch(self.app, ROLE, SID, "synthetic-cwd", ITEMS)
        self.app.rpc.assert_not_called()
        bridge.validate_delivery.assert_not_called()
        bridge.fence_tasks.assert_called_once_with(ROLE, ITEMS, "bridge_identity_mismatch")

    def test_missing_or_completed_delivery_fence_refuses_model_request(self):
        for error in ("synthetic missing marker", "synthetic already dispatched", "synthetic completed task"):
            with self.subTest(error=error):
                self.setUp()
                self.app.rpc = Mock()
                bridge.validate_delivery.side_effect = RuntimeError(error)
                with self.assertRaisesRegex(RuntimeError, error):
                    bridge._dispatch_batch(self.app, ROLE, SID, "synthetic-cwd", ITEMS)
                self.app.rpc.assert_not_called()
                bridge.fence_tasks.assert_called_once_with(ROLE, ITEMS, "invalid_delivery_fence")

    def test_rebind_after_delivery_fence_still_refuses_model_request(self):
        self.app.rpc = Mock()
        bridge.verify_identity.side_effect = [None, RuntimeError("synthetic late rebind")]
        with self.assertRaisesRegex(RuntimeError, "synthetic late rebind"):
            bridge._dispatch_batch(self.app, ROLE, SID, "synthetic-cwd", ITEMS)
        bridge.validate_delivery.assert_called_once_with(ROLE, SID, ITEMS)
        self.app.rpc.assert_not_called()
        bridge.fence_tasks.assert_called_once_with(ROLE, ITEMS, "bridge_identity_mismatch")

    def test_wait_launch_checks_binding_before_any_subprocess(self):
        pump = bridge.WaitPump(ROLE, SID)
        bridge.verify_identity.side_effect = RuntimeError("synthetic rebind")
        with patch.object(bridge.subprocess, "Popen") as launch:
            with self.assertRaisesRegex(RuntimeError, "synthetic rebind"):
                pump._launch()
        launch.assert_not_called()

    def test_wait_rearm_rebind_fences_buffered_claim_and_does_not_relaunch(self):
        pump = bridge.WaitPump(ROLE, SID)
        proc = Mock()
        proc.poll.return_value = 0
        bridge.verify_identity.side_effect = [None, RuntimeError("synthetic rebind")]
        with patch.object(bridge.subprocess, "Popen", return_value=proc) as launch, \
                patch.object(bridge, "_parse_wait", return_value=ITEMS[0]):
            with self.assertRaisesRegex(RuntimeError, "synthetic rebind"):
                pump._launch()
        self.assertEqual(1, launch.call_count)
        self.assertEqual(SID, launch.call_args.kwargs["env"]["CODEX_SESSION_ID"])
        bridge.fence_tasks.assert_called_once_with(ROLE, ITEMS, "bridge_wait_rearm_failed")

    def test_presence_requires_primary_exact_session_without_legacy_fallback(self):
        import json
        for presence in ({"host_kind": "codex", "codex_session_id": SID},
                         {"host_kind": "codex", "host_session_id": "other", "codex_session_id": SID},
                         {"host_kind": "claude", "host_session_id": SID}):
            with self.subTest(presence=presence), patch.object(Path, "read_text", return_value=json.dumps(presence)):
                with self.assertRaises(RuntimeError):
                    REAL_VERIFY_IDENTITY(ROLE, SID)
        with patch.object(Path, "read_text", return_value=json.dumps({"host_kind": "codex", "host_session_id": SID})):
            REAL_VERIFY_IDENTITY(ROLE, SID)


if __name__ == "__main__":
    unittest.main()
