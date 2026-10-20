"""Local, event-driven wakeups for one validated platform state directory.

A notification means only that a fresh database query may be useful. It does not
prove a transaction committed or that any particular role has runnable work.
Call ``arm()`` *before* querying, then ``wait()`` if that query found no work.
This keeps a change between the query and the wait observable without polling.
No business workspace or file content is inspected by this module.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import math
import os
from pathlib import Path
import threading

from .errors import PlatformError
from .paths import PathSafetyError, prepare_state_root, _explicit_path, _check_directory_boundary


_WAIT_OBJECT_0 = 0
_WAIT_TIMEOUT = 258
_WAIT_FAILED = 0xFFFFFFFF
_MAX_TIMEOUT_MS = 0xFFFFFFFE
_FILE_NOTIFY_CHANGE_FILE_NAME = 0x00000001
_FILE_NOTIFY_CHANGE_SIZE = 0x00000008
_FILE_NOTIFY_CHANGE_LAST_WRITE = 0x00000010


def _kernel32():
    if os.name != "nt":
        raise PlatformError(
            "state_waiter_unsupported", "Local state notifications require Windows."
        )
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.FindFirstChangeNotificationW.argtypes = (
        wintypes.LPCWSTR, wintypes.BOOL, wintypes.DWORD,
    )
    kernel.FindFirstChangeNotificationW.restype = wintypes.HANDLE
    kernel.FindNextChangeNotification.argtypes = (wintypes.HANDLE,)
    kernel.FindNextChangeNotification.restype = wintypes.BOOL
    kernel.FindCloseChangeNotification.argtypes = (wintypes.HANDLE,)
    kernel.FindCloseChangeNotification.restype = wintypes.BOOL
    kernel.CreateEventW.argtypes = (
        wintypes.LPVOID, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR,
    )
    kernel.CreateEventW.restype = wintypes.HANDLE
    kernel.SetEvent.argtypes = (wintypes.HANDLE,)
    kernel.SetEvent.restype = wintypes.BOOL
    kernel.WaitForMultipleObjects.argtypes = (
        wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE), wintypes.BOOL, wintypes.DWORD,
    )
    kernel.WaitForMultipleObjects.restype = wintypes.DWORD
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel.CloseHandle.restype = wintypes.BOOL
    return kernel


def _native_error(operation: str) -> PlatformError:
    # Retain the error number without disclosing any local path or file content.
    return PlatformError(
        "state_waiter_failed",
        f"Local state notification {operation} failed (Windows error {ctypes.get_last_error()}).",
    )


class StateWaiter:
    """Watch an existing state root; one waiting consumer, any number of wakers.

    Construction starts observation immediately. ``arm`` resets directory
    notifications only; a queued local ``wake`` remains observable. Local wakes
    coalesce and are consumed by one wait. Directory changes remain signalled
    until the next arm. Multiple instances can independently watch one root.

    ``close`` is idempotent, wakes a blocked wait, and waits for that native wait
    to return before releasing handles. A wait interrupted by close returns
    True. Starting another wait or arm after close is an explicit error.
    """

    def __init__(self, state_root: str | Path, *, additional_roots=()):
        self._kernel = _kernel32()
        try:
            self.state_root = prepare_state_root(state_root, create=False)
        except PathSafetyError as exc:
            raise PlatformError(exc.code, str(exc)) from exc
        if not self.state_root.is_dir():
            raise PlatformError("missing_state_root", "State notification directory must already exist.")
        self._condition = threading.Condition()
        self._closed = False
        self._waiting = False
        self._change_handle = None
        self._wake_handle = None
        change = self._kernel.FindFirstChangeNotificationW(
            str(self.state_root), False,
            _FILE_NOTIFY_CHANGE_FILE_NAME | _FILE_NOTIFY_CHANGE_SIZE | _FILE_NOTIFY_CHANGE_LAST_WRITE,
        )
        if change in (None, 0, ctypes.c_void_p(-1).value):
            raise _native_error("start")
        self._change_handle = change
        self._additional_handles = []
        try:
            for extra in additional_roots:
                # An additional store has its own schema and file names.
                # Apply the shared boundary guard, not platform DB detection.
                extra_root = _explicit_path(extra)
                _check_directory_boundary(extra_root)
                if not extra_root.is_dir():
                    raise PlatformError("missing_state_root", "Notification directories must already exist.")
                handle = self._kernel.FindFirstChangeNotificationW(
                    str(extra_root), False,
                    _FILE_NOTIFY_CHANGE_FILE_NAME | _FILE_NOTIFY_CHANGE_SIZE | _FILE_NOTIFY_CHANGE_LAST_WRITE,
                )
                if handle in (None, 0, ctypes.c_void_p(-1).value):
                    raise _native_error("start")
                self._additional_handles.append(handle)
                if len(self._additional_handles) > 60:
                    raise PlatformError("invalid_wait_roots", "Too many notification directories.")
        except Exception:
            for handle in self._additional_handles:
                self._kernel.FindCloseChangeNotification(handle)
            self._kernel.FindCloseChangeNotification(change)
            self._change_handle = None
            self._additional_handles = []
            raise
        wake = self._kernel.CreateEventW(None, False, False, None)
        if not wake:
            error = _native_error("wake event creation")
            self._kernel.FindCloseChangeNotification(change)
            for handle in self._additional_handles:
                self._kernel.FindCloseChangeNotification(handle)
            self._additional_handles = []
            self._change_handle = None
            raise error
        self._wake_handle = wake

    def _require_open(self) -> None:
        if self._closed:
            raise PlatformError("state_waiter_closed", "State notification waiter is closed.")

    def arm(self) -> None:
        """Reset the change signal before the caller queries durable state."""
        with self._condition:
            self._require_open()
            if self._waiting:
                raise PlatformError("state_waiter_busy", "Cannot rearm an active state wait.")
            if not self._kernel.FindNextChangeNotification(self._change_handle):
                raise _native_error("rearm")
            for handle in self._additional_handles:
                if not self._kernel.FindNextChangeNotification(handle):
                    raise _native_error("rearm")

    def wait(self, timeout_seconds: float) -> bool:
        """Return True for directory/local wake, False only for the timeout.

        A timeout is a housekeeping deadline, not evidence of incoming work.
        This method does not query SQLite or scan the filesystem.
        """
        if (isinstance(timeout_seconds, bool)
                or not isinstance(timeout_seconds, (int, float))
                or timeout_seconds < 0
                or timeout_seconds > _MAX_TIMEOUT_MS / 1000
                or not math.isfinite(timeout_seconds)):
            raise PlatformError("invalid_wait_timeout", "State wait timeout must be a finite nonnegative duration.")
        timeout_ms = math.ceil(timeout_seconds * 1000)
        with self._condition:
            self._require_open()
            if self._waiting:
                raise PlatformError("state_waiter_busy", "Only one consumer can wait on this instance.")
            values = [self._wake_handle, self._change_handle, *self._additional_handles]
            handles = (wintypes.HANDLE * len(values))(*values)
            self._waiting = True
        try:
            result = self._kernel.WaitForMultipleObjects(len(values), handles, False, timeout_ms)
            if _WAIT_OBJECT_0 <= result < _WAIT_OBJECT_0 + len(values):
                return True
            if result == _WAIT_TIMEOUT:
                return False
            if result == _WAIT_FAILED:
                raise _native_error("wait")
            raise PlatformError("state_waiter_failed", "Local state notification returned an unexpected wait result.")
        finally:
            with self._condition:
                self._waiting = False
                self._condition.notify_all()

    def wake(self) -> None:
        """Wake the consumer without touching disk; harmless after close."""
        with self._condition:
            if not self._closed and not self._kernel.SetEvent(self._wake_handle):
                raise _native_error("wake")

    def close(self) -> None:
        """Wake any active wait, then release both handles exactly once."""
        with self._condition:
            if self._change_handle is None and self._wake_handle is None:
                return
            self._closed = True
            if self._waiting and not self._kernel.SetEvent(self._wake_handle):
                self._closed = False
                raise _native_error("close wake")
            while self._waiting:
                self._condition.wait()
            if self._change_handle is None and self._wake_handle is None:
                return
            change, wake = self._change_handle, self._wake_handle
            additional = self._additional_handles
            self._additional_handles = []
            self._change_handle = self._wake_handle = None
            change_ok = self._kernel.FindCloseChangeNotification(change)
            change_error = None if change_ok else _native_error("close directory handle")
            wake_ok = self._kernel.CloseHandle(wake)
            for handle in additional:
                if not self._kernel.FindCloseChangeNotification(handle) and change_error is None:
                    change_error = _native_error("close directory handle")
            if change_error is not None:
                raise change_error
            if not wake_ok:
                raise _native_error("close wake handle")

    def __enter__(self) -> "StateWaiter":
        self._require_open()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()
