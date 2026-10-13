"""Real Windows notification checks against isolated synthetic state only."""

from contextlib import closing
import ctypes
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from masteragent.errors import PlatformError
from masteragent.state_waiter import StateWaiter


@unittest.skipUnless(os.name == "nt", "Windows directory notifications")
class StateWaiterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="masteragent-state-waiter-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.state = self.root / "state"
        self.state.mkdir()
        self.database = self.state / "state.sqlite3"
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute("CREATE TABLE synthetic_queue (value TEXT)")

    def waiter(self):
        waiter = StateWaiter(self.state)
        self.addCleanup(waiter.close)
        return waiter

    def write(self, value="synthetic"):
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute("INSERT INTO synthetic_queue VALUES (?)", (value,))

    def test_constructor_is_already_armed(self):
        waiter = self.waiter()
        self.write()
        self.assertTrue(waiter.wait(2))

    def test_commit_between_query_and_wait_is_not_lost(self):
        waiter = self.waiter()
        waiter.arm()
        with closing(sqlite3.connect(self.database)) as db:
            self.assertEqual(0, db.execute("SELECT count(*) FROM synthetic_queue").fetchone()[0])
        self.write("committed_after_query")
        self.assertTrue(waiter.wait(2))
        with closing(sqlite3.connect(self.database)) as db:
            self.assertEqual(1, db.execute("SELECT count(*) FROM synthetic_queue").fetchone()[0])

    def test_cross_thread_sqlite_commit_wakes_wait(self):
        waiter = self.waiter()
        ready = threading.Event()
        results = []
        def consume():
            waiter.arm()
            ready.set()
            results.append(waiter.wait(5))
        thread = threading.Thread(target=consume)
        thread.start()
        self.assertTrue(ready.wait(2))
        self.write()
        thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertEqual([True], results)

    def test_independent_instances_both_observe_commit(self):
        first, second = self.waiter(), self.waiter()
        first.arm()
        second.arm()
        self.write()
        self.assertTrue(first.wait(2))
        self.assertTrue(second.wait(2))

    def test_cross_process_sqlite_commit_wakes_wait(self):
        waiter = self.waiter()
        waiter.arm()
        script = (
            "import sqlite3,sys; db=sqlite3.connect(sys.argv[1]); "
            "db.execute('INSERT INTO synthetic_queue VALUES (?)', ('child_commit',)); "
            "db.commit(); db.close()"
        )
        proc = subprocess.Popen(
            [sys.executable, "-B", "-c", script, str(self.database)],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        try:
            self.assertTrue(waiter.wait(5))
            stdout, stderr = proc.communicate(timeout=5)
            self.assertEqual(0, proc.returncode, stderr.decode("utf-8", errors="replace"))
            self.assertEqual(b"", stdout)
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.communicate(timeout=5)

    def test_timeout_neither_scans_nor_opens_database(self):
        waiter = self.waiter()
        waiter.arm()
        with patch.object(Path, "iterdir", side_effect=AssertionError("directory scan")), \
                patch.object(Path, "read_bytes", side_effect=AssertionError("file read")), \
                patch.object(Path, "read_text", side_effect=AssertionError("file read")), \
                patch("sqlite3.connect", side_effect=AssertionError("database query")), \
                patch("masteragent.state_waiter.prepare_state_root", side_effect=AssertionError("repeat path scan")):
            self.assertFalse(waiter.wait(.02))

    def test_local_wake_survives_arm_and_is_consumed(self):
        waiter = self.waiter()
        waiter.wake()
        waiter.arm()
        self.assertTrue(waiter.wait(0))
        self.assertFalse(waiter.wait(0))

    def test_cross_thread_local_wake(self):
        waiter = self.waiter()
        ready = threading.Event()
        results = []
        def consume():
            ready.set()
            results.append(waiter.wait(5))
        thread = threading.Thread(target=consume)
        thread.start()
        self.assertTrue(ready.wait(2))
        waiter.wake()
        thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertEqual([True], results)

    def test_close_wakes_wait_and_releases_handles(self):
        waiter = self.waiter()
        entered = threading.Event()
        original = waiter._kernel.WaitForMultipleObjects
        results = []
        def native_wait(*args):
            entered.set()
            return original(*args)
        with patch.object(waiter._kernel, "WaitForMultipleObjects", side_effect=native_wait):
            thread = threading.Thread(target=lambda: results.append(waiter.wait(30)))
            thread.start()
            self.assertTrue(entered.wait(2))
            waiter.close()
            thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertEqual([True], results)
        self.assertIsNone(waiter._change_handle)
        self.assertIsNone(waiter._wake_handle)
        waiter.close()
        waiter.wake()
        for method in (waiter.arm, lambda: waiter.wait(0)):
            with self.assertRaises(PlatformError) as caught:
                method()
            self.assertEqual("state_waiter_closed", caught.exception.code)

    def test_concurrent_wait_and_arm_are_refused(self):
        waiter = self.waiter()
        entered = threading.Event()
        original = waiter._kernel.WaitForMultipleObjects
        def native_wait(*args):
            entered.set()
            return original(*args)
        with patch.object(waiter._kernel, "WaitForMultipleObjects", side_effect=native_wait):
            thread = threading.Thread(target=lambda: waiter.wait(30))
            thread.start()
            self.assertTrue(entered.wait(2))
            try:
                for method in (waiter.arm, lambda: waiter.wait(0)):
                    with self.assertRaises(PlatformError) as caught:
                        method()
                    self.assertEqual("state_waiter_busy", caught.exception.code)
            finally:
                waiter.wake()
                thread.join(2)
        self.assertFalse(thread.is_alive())

    def test_concurrent_closers_release_handles_once(self):
        waiter = self.waiter()
        entered = threading.Event()
        release = threading.Event()
        both_closing = threading.Event()
        native_wait = waiter._kernel.WaitForMultipleObjects
        native_wake = waiter._kernel.SetEvent
        wake_calls = []
        errors = []
        def blocked_wait(*args):
            entered.set()
            if not release.wait(5):
                raise AssertionError("synthetic wait was not released")
            return native_wait(*args)
        def wake(*args):
            wake_calls.append(args)
            if len(wake_calls) == 2:
                both_closing.set()
            return native_wake(*args)
        def close():
            try:
                waiter.close()
            except Exception as exc:
                errors.append(exc)
        with patch.object(waiter._kernel, "WaitForMultipleObjects", side_effect=blocked_wait), \
                patch.object(waiter._kernel, "SetEvent", side_effect=wake):
            consumer = threading.Thread(target=lambda: waiter.wait(5))
            consumer.start()
            self.assertTrue(entered.wait(2))
            closers = [threading.Thread(target=close) for _ in range(2)]
            for thread in closers:
                thread.start()
            try:
                self.assertTrue(both_closing.wait(2))
            finally:
                release.set()
                consumer.join(5)
                for thread in closers:
                    thread.join(5)
        self.assertFalse(consumer.is_alive())
        self.assertTrue(all(not thread.is_alive() for thread in closers))
        self.assertEqual([], errors)
        self.assertIsNone(waiter._change_handle)
        self.assertIsNone(waiter._wake_handle)

    def test_change_signal_is_reset_for_next_query(self):
        waiter = self.waiter()
        self.write()
        self.assertTrue(waiter.wait(2))
        original = waiter._kernel.FindNextChangeNotification
        with patch.object(waiter._kernel, "FindNextChangeNotification", wraps=original) as rearm:
            waiter.arm()
            rearm.assert_called_once_with(waiter._change_handle)
        self.write("next")
        self.assertTrue(waiter.wait(2))

    def test_nested_business_directory_is_not_watched(self):
        nested = self.state / "nested"
        nested.mkdir()
        waiter = self.waiter()
        waiter.arm()
        (nested / "not-state.txt").write_text("synthetic", encoding="utf-8")
        self.assertFalse(waiter.wait(.02))

    def test_context_manager_closes_on_exception(self):
        waiter = self.waiter()
        with self.assertRaisesRegex(RuntimeError, "synthetic"):
            with waiter:
                raise RuntimeError("synthetic")
        self.assertIsNone(waiter._change_handle)
        self.assertIsNone(waiter._wake_handle)

    def test_invalid_timeouts(self):
        waiter = self.waiter()
        for value in (-1, float("inf"), float("nan"), True, "1", None, 4294968, 10 ** 1000):
            with self.subTest(value=value), self.assertRaises(PlatformError) as caught:
                waiter.wait(value)
            self.assertEqual("invalid_wait_timeout", caught.exception.code)

    def test_missing_state_is_not_created(self):
        missing = self.root / "missing"
        with self.assertRaises(PlatformError) as caught:
            StateWaiter(missing)
        self.assertEqual("missing_state_root", caught.exception.code)
        self.assertFalse(missing.exists())

    def test_relative_and_unmanaged_paths_are_refused(self):
        with self.assertRaises(PlatformError) as caught:
            StateWaiter("relative-state")
        self.assertEqual("invalid_path", caught.exception.code)
        unmanaged = self.root / "unmanaged"
        unmanaged.mkdir()
        (unmanaged / "foreign.txt").write_text("synthetic", encoding="utf-8")
        with self.assertRaises(PlatformError) as caught:
            StateWaiter(unmanaged)
        self.assertEqual("unmanaged_state", caught.exception.code)

    def test_hardlinked_database_is_refused(self):
        os.link(self.database, self.root / "database-alias")
        with self.assertRaises(PlatformError) as caught:
            StateWaiter(self.state)
        self.assertEqual("linked_state", caught.exception.code)

    def test_notification_creation_failure_is_explicit(self):
        kernel = Mock()
        kernel.FindFirstChangeNotificationW.return_value = ctypes.c_void_p(-1).value
        with patch("masteragent.state_waiter._kernel32", return_value=kernel):
            with self.assertRaises(PlatformError) as caught:
                StateWaiter(self.state)
        self.assertEqual("state_waiter_failed", caught.exception.code)
        kernel.CreateEventW.assert_not_called()

    def test_event_creation_failure_releases_notification(self):
        kernel = Mock()
        kernel.FindFirstChangeNotificationW.return_value = 123
        kernel.CreateEventW.return_value = 0
        with patch("masteragent.state_waiter._kernel32", return_value=kernel):
            with self.assertRaises(PlatformError) as caught:
                StateWaiter(self.state)
        self.assertEqual("state_waiter_failed", caught.exception.code)
        kernel.FindCloseChangeNotification.assert_called_once_with(123)

    def test_native_wait_failure_releases_waiting_flag(self):
        waiter = self.waiter()
        with patch.object(waiter._kernel, "WaitForMultipleObjects", return_value=0xFFFFFFFF):
            with self.assertRaises(PlatformError) as caught:
                waiter.wait(.01)
        self.assertEqual("state_waiter_failed", caught.exception.code)
        self.assertFalse(waiter._waiting)
        waiter.close()
        self.assertIsNone(waiter._change_handle)

    def test_rearm_failure_is_explicit(self):
        waiter = self.waiter()
        with patch.object(waiter._kernel, "FindNextChangeNotification", return_value=False):
            with self.assertRaises(PlatformError) as caught:
                waiter.arm()
        self.assertEqual("state_waiter_failed", caught.exception.code)


class UnsupportedStateWaiterTests(unittest.TestCase):
    def test_unsupported_platform_does_not_fall_back_to_polling(self):
        with patch("masteragent.state_waiter.os.name", "posix"), \
                patch("masteragent.state_waiter.prepare_state_root", side_effect=AssertionError("no fallback")):
            with self.assertRaises(PlatformError) as caught:
                StateWaiter("unused")
        self.assertEqual("state_waiter_unsupported", caught.exception.code)


if __name__ == "__main__":
    unittest.main()
