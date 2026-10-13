"""Bounded, text-only Codex JSONL supervision, independent of platform state.

This owns a child process tree; it is not a filesystem or network sandbox. Only
the final agent message and a narrow metadata allowlist cross this boundary.
Callers must persist ownership before calling and reconcile uncertain outcomes.
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import queue
import re
import signal
import subprocess
import threading
import time
from typing import Callable


MAX_PROMPT_BYTES = 2 * 1024 * 1024
MAX_LINE_BYTES = 256 * 1024
MAX_STDOUT_BYTES = 2 * 1024 * 1024
MAX_STDERR_BYTES = 256 * 1024
MAX_EVENTS = 2048
_IDENTIFIER = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
_USAGE_FIELDS = frozenset({
    "input_tokens", "cached_input_tokens", "output_tokens", "reasoning_output_tokens",
})


class _ProtocolError(Exception):
    pass


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise _ProtocolError()
        result[key] = value
    return result


def _constant(_):
    raise _ProtocolError()


class _Protocol:
    def __init__(self, callback):
        self.callback = callback
        self.thread_id = None
        self.started = False
        self.terminal = None
        self.output = None
        self.usage = None
        self.count = 0

    def accept(self, line):
        if not line or len(line) > MAX_LINE_BYTES:
            raise _ProtocolError()
        try:
            event = json.loads(line.decode("utf-8"), object_pairs_hook=_object,
                               parse_constant=_constant)
        except (ValueError, UnicodeError, RecursionError) as exc:
            raise _ProtocolError() from exc
        if not isinstance(event, dict) or not isinstance(event.get("type"), str):
            raise _ProtocolError()
        kind = event["type"]
        self.count += 1
        if self.count > MAX_EVENTS or len(kind) > 80 or self.terminal is not None:
            raise _ProtocolError()
        phase = "other"
        if kind == "thread.started":
            identifier = event.get("thread_id")
            if self.thread_id is not None or self.started or not isinstance(identifier, str):
                raise _ProtocolError()
            if not _IDENTIFIER.fullmatch(identifier):
                raise _ProtocolError()
            self.thread_id = identifier
            self.callback("runtime_thread", {"thread_id": identifier})
            return
        if kind == "turn.started":
            if self.thread_id is None or self.started:
                raise _ProtocolError()
            self.started = True
            phase = "turn_started"
        elif kind in {"item.started", "item.updated", "item.completed"}:
            item = event.get("item")
            if not self.started or not isinstance(item, dict):
                raise _ProtocolError()
            # Reasoning text is discarded. Every tool, command, file-change,
            # MCP, web-search, or unknown item fails this text-only contract.
            if item.get("type") not in {"agent_message", "reasoning"}:
                raise _ProtocolError()
            if kind == "item.completed" and item["type"] == "agent_message":
                text = item.get("text")
                if not isinstance(text, str):
                    raise _ProtocolError()
                try:
                    text.encode("utf-8")
                except UnicodeError as exc:
                    raise _ProtocolError() from exc
                self.output = text
                phase = "message_completed"
            else:
                phase = "item_progress"
        elif kind == "turn.completed":
            if not self.started or self.output is None:
                raise _ProtocolError()
            usage = event.get("usage")
            if usage is not None:
                if not isinstance(usage, dict):
                    raise _ProtocolError()
                self.usage = {}
                for key in _USAGE_FIELDS:
                    if key in usage:
                        value = usage[key]
                        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 2**63 - 1:
                            raise _ProtocolError()
                        self.usage[key] = value
            self.terminal = "succeeded"
            phase = "turn_completed"
        elif kind in {"turn.failed", "error"}:
            if kind == "turn.failed" and not self.started:
                raise _ProtocolError()
            self.terminal = "failed"
            phase = "turn_failed" if kind == "turn.failed" else "error"
        elif kind.startswith(("thread.", "turn.", "item.")):
            # An unknown lifecycle event cannot be mistaken for progress.
            raise _ProtocolError()
        self.callback("runtime_progress", {"phase": phase, "sequence": self.count})


class _WindowsJob:
    """Create a kill-on-close job before launching a suspended child.

    CPython closes CreateProcess's primary thread handle, so NtResumeProcess
    resumes only the process we just created after AssignProcessToJobObject.
    If any ownership operation fails, the suspended process is never resumed.
    """
    def __init__(self):
        import ctypes
        from ctypes import wintypes

        self.ctypes = ctypes
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.nt = ctypes.WinDLL("ntdll", use_last_error=True)
        class BasicLimits(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_int64),
                ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]
        class Counters(ctypes.Structure):
            _fields_ = [(name, ctypes.c_uint64) for name in (
                "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]
        class ExtendedLimits(ctypes.Structure):
            _fields_ = [("BasicLimitInformation", BasicLimits), ("IoInfo", Counters),
                        ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                        ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]
        class Accounting(ctypes.Structure):
            _fields_ = [("TotalUserTime", ctypes.c_int64), ("TotalKernelTime", ctypes.c_int64),
                        ("ThisPeriodTotalUserTime", ctypes.c_int64), ("ThisPeriodTotalKernelTime", ctypes.c_int64),
                        ("TotalPageFaultCount", wintypes.DWORD), ("TotalProcesses", wintypes.DWORD),
                        ("ActiveProcesses", wintypes.DWORD), ("TotalTerminatedProcesses", wintypes.DWORD)]
        self.accounting_type = Accounting
        signatures = {
            "CreateJobObjectW": ([ctypes.c_void_p, wintypes.LPCWSTR], wintypes.HANDLE),
            "SetInformationJobObject": ([wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD], wintypes.BOOL),
            "AssignProcessToJobObject": ([wintypes.HANDLE, wintypes.HANDLE], wintypes.BOOL),
            "TerminateJobObject": ([wintypes.HANDLE, wintypes.UINT], wintypes.BOOL),
            "QueryInformationJobObject": ([wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p], wintypes.BOOL),
            "CloseHandle": ([wintypes.HANDLE], wintypes.BOOL),
        }
        for name, (arguments, result) in signatures.items():
            method = getattr(self.kernel, name)
            method.argtypes, method.restype = arguments, result
        self.nt.NtResumeProcess.argtypes = [wintypes.HANDLE]
        self.nt.NtResumeProcess.restype = ctypes.c_long
        self.handle = self.kernel.CreateJobObjectW(None, None)
        if not self.handle:
            raise OSError("process ownership unavailable")
        info = ExtendedLimits()
        info.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not self.kernel.SetInformationJobObject(self.handle, 9, ctypes.byref(info), ctypes.sizeof(info)):
            self.close()
            raise OSError("process ownership unavailable")

    def attach_and_resume(self, process):
        if not self.kernel.AssignProcessToJobObject(self.handle, int(process._handle)):
            raise OSError("process ownership unavailable")
        if self.nt.NtResumeProcess(int(process._handle)) != 0:
            raise OSError("process start unavailable")

    def terminate(self, exit_code=1):
        return bool(self.kernel.TerminateJobObject(self.handle, exit_code))

    def empty(self):
        info = self.accounting_type()
        if not self.kernel.QueryInformationJobObject(
                self.handle, 1, self.ctypes.byref(info), self.ctypes.sizeof(info), None):
            return False
        return info.ActiveProcesses == 0

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None


def _stop_tree(process, job):
    """Stop only this owned tree; never discover or signal unrelated PIDs."""
    if job is not None:
        job.terminate()
    elif os.name != "nt":
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        except OSError:
            return False
    try:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=3)
    except (OSError, subprocess.TimeoutExpired):
        return False
    if job is not None:
        deadline = time.monotonic() + 3
        while not job.empty() and time.monotonic() < deadline:
            time.sleep(0.01)
        return job.empty()
    if os.name != "nt":
        deadline = time.monotonic() + 1
        while time.monotonic() < deadline:
            try:
                os.killpg(process.pid, 0)
            except ProcessLookupError:
                return True
            except OSError:
                return False
            time.sleep(0.01)
        return False
    # A failed Windows job assignment leaves only the suspended direct child.
    return process.poll() is not None


def spawn_owned_process(argv, cwd, env, stdin=subprocess.PIPE):
    """Start an owned native process for a bounded protocol controller.

    The caller owns pipe supervision and must call stop_owned_process in a
    finally block. Windows execution remains suspended until job assignment.
    """
    process = job = None
    try:
        if os.name == "nt":
            job = _WindowsJob()
        options = {"creationflags": subprocess.CREATE_NO_WINDOW | 0x4} if os.name == "nt" else {"start_new_session": True}
        process = subprocess.Popen(argv, stdin=stdin, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, cwd=str(cwd), env=env,
                                   shell=False, **options)
        if job is not None:
            job.attach_and_resume(process)
        return process, job
    except BaseException:
        try:
            if process is not None:
                _stop_tree(process, job)
                for pipe in (process.stdin, process.stdout, process.stderr):
                    if pipe is not None:
                        pipe.close()
        finally:
            if job is not None:
                job.close()
        raise


def stop_owned_process(process, job):
    """Stop an owned tree and retain evidence about the parent's exit.

    A pre-stop poll alone cannot prove that a later nonzero exit was ours.
    The new App Server controller uses a distinct Windows termination code,
    or SIGKILL on POSIX, and requires the actual parent exit code to match.
    Existing one-shot execution keeps its original _stop_tree behavior.
    """
    prior = process.poll()
    expected = 0x4D413354 if os.name == "nt" else -signal.SIGKILL
    applied = False
    try:
        try:
            if job is not None:
                sent = job.terminate(expected)
            elif os.name != "nt":
                os.killpg(process.pid, signal.SIGKILL)
                sent = True
            else:
                sent = False
            process.wait(timeout=3)
            applied = prior is None and sent and process.returncode == expected
        except (OSError, subprocess.TimeoutExpired):
            pass
        confirmed = _stop_tree(process, job)
        return {"termination_confirmed": confirmed, "termination_applied": applied,
                "pre_stop_returncode": prior}
    finally:
        if job is not None:
            job.close()


def execute(argv: list[str], prompt: str, cwd: Path, env: dict[str, str],
            timeout_seconds: float, heartbeat: Callable[[], None],
            on_event: Callable[[str, dict], None],
            cancelled: Callable[[], bool] | None = None,
            heartbeat_seconds: float = 5) -> dict:
    """Run one text-only turn, returning an outcome with no raw diagnostics.

    Success needs an ordered thread/turn/message/completion sequence, clean
    EOF, exit status zero and confirmed process-tree termination. Timeout,
    cancellation, lease loss and protocol ambiguity never return model output.
    Callback failures return lease_lost after stopping the owned process tree.
    KeyboardInterrupt is converted to interrupted so callers can record it.
    Invalid API arguments raise ValueError before launching any process.
    """
    if (not isinstance(argv, list) or not argv or
            any(not isinstance(arg, str) or not arg or "\0" in arg for arg in argv) or
            not isinstance(prompt, str) or not isinstance(env, dict) or
            any(not isinstance(k, str) or not isinstance(v, str) or "\0" in k + v for k, v in env.items()) or
            not callable(heartbeat) or not callable(on_event) or
            (cancelled is not None and not callable(cancelled))):
        raise ValueError("invalid execution arguments")
    for value in (timeout_seconds, heartbeat_seconds):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
            raise ValueError("invalid execution timing")
    try:
        encoded_prompt = prompt.encode("utf-8")
        directory = Path(cwd)
    except (TypeError, ValueError, UnicodeError) as exc:
        raise ValueError("invalid execution arguments") from exc
    if len(encoded_prompt) > MAX_PROMPT_BYTES or not directory.is_absolute():
        raise ValueError("invalid execution arguments")
    if os.name == "nt" and Path(argv[0]).suffix.lower() in {".bat", ".cmd"}:
        raise ValueError("shell launchers are unsupported")

    process = None
    job = None
    readers = []
    writer = None
    stop_readers = threading.Event()
    messages = queue.Queue(maxsize=32)
    reader_failure = threading.Event()
    outcome = "launch_failed"
    termination_confirmed = True
    protocol = _Protocol(on_event)
    end = time.monotonic() + timeout_seconds

    def put(value):
        while not stop_readers.is_set():
            try:
                messages.put(value, timeout=0.05)
                return
            except queue.Full:
                pass

    def read_pipe(name, pipe):
        try:
            while not stop_readers.is_set():
                chunk = pipe.read1(8192)
                if not chunk:
                    break
                put((name, chunk))
        except (OSError, ValueError):
            reader_failure.set()
        finally:
            put((name, None))

    def write_prompt():
        try:
            process.stdin.write(encoded_prompt)
            process.stdin.flush()
        except (BrokenPipeError, OSError, ValueError):
            # Early closed stdin is not evidence of a completed turn.
            reader_failure.set()
        finally:
            try:
                process.stdin.close()
            except (OSError, ValueError):
                pass

    try:
        try:
            heartbeat()
            if cancelled is not None and cancelled():
                outcome = "cancelled"
                return _result(outcome, protocol, None, True)
        except Exception:
            outcome = "lease_lost"
            return _result(outcome, protocol, None, True)
        if os.name == "nt":
            job = _WindowsJob()
        options = {"creationflags": subprocess.CREATE_NO_WINDOW | 0x4} if os.name == "nt" else {"start_new_session": True}
        process = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, cwd=str(directory), env=env,
                                   shell=False, **options)
        termination_confirmed = False
        if job is not None:
            job.attach_and_resume(process)
        try:
            on_event("runtime_started", {"pid": process.pid})
        except Exception:
            outcome = "lease_lost"
            raise _ProtocolError() from None
        for name, pipe in (("stdout", process.stdout), ("stderr", process.stderr)):
            thread = threading.Thread(target=read_pipe, args=(name, pipe), daemon=True)
            readers.append(thread)
            thread.start()
        writer = threading.Thread(target=write_prompt, daemon=True)
        writer.start()
        pending = bytearray()
        totals = {"stdout": 0, "stderr": 0}
        closed = set()
        next_heartbeat = time.monotonic() + heartbeat_seconds
        outcome = "protocol_error"
        while True:
            now = time.monotonic()
            if now >= end:
                outcome = "timed_out"
                break
            try:
                if cancelled is not None and cancelled():
                    outcome = "cancelled"
                    break
                if now >= next_heartbeat:
                    heartbeat()
                    next_heartbeat = time.monotonic() + heartbeat_seconds
            except Exception:
                outcome = "lease_lost"
                break
            if reader_failure.is_set():
                break
            if len(closed) == 2 and process.poll() is not None:
                if pending:
                    try:
                        protocol.accept(bytes(pending))
                    except _ProtocolError:
                        break
                    except Exception:
                        outcome = "lease_lost"
                        break
                if protocol.terminal == "succeeded" and process.returncode == 0:
                    outcome = "succeeded"
                elif protocol.terminal == "succeeded":
                    outcome = "interrupted"
                elif protocol.terminal == "failed":
                    outcome = "failed"
                break
            try:
                name, chunk = messages.get(timeout=min(0.05, max(0.001, end - now)))
            except queue.Empty:
                continue
            if chunk is None:
                closed.add(name)
                continue
            totals[name] += len(chunk)
            limit = MAX_STDOUT_BYTES if name == "stdout" else MAX_STDERR_BYTES
            if totals[name] > limit:
                break
            if name == "stderr":
                continue  # Deliberately discard diagnostics, credentials and paths.
            pending.extend(chunk)
            while b"\n" in pending:
                line, _, rest = pending.partition(b"\n")
                pending = bytearray(rest)
                if line.endswith(b"\r"):
                    line = line[:-1]
                try:
                    protocol.accept(bytes(line))
                except _ProtocolError:
                    raise
                except Exception:
                    outcome = "lease_lost"
                    raise _ProtocolError() from None
            if len(pending) > MAX_LINE_BYTES:
                break
    except KeyboardInterrupt:
        outcome = "interrupted"
    except _ProtocolError:
        pass
    except (OSError, ValueError, subprocess.SubprocessError):
        # No exception text is propagated: process errors may contain secrets.
        if process is not None and outcome != "launch_failed":
            outcome = "protocol_error"
    finally:
        if process is not None:
            termination_confirmed = _stop_tree(process, job)
        stop_readers.set()
        for thread in readers + ([writer] if writer is not None else []):
            thread.join(timeout=3)
        if any(thread.is_alive() for thread in readers + ([writer] if writer is not None else [])):
            termination_confirmed = False
        else:
            if process is not None:
                for pipe in (process.stdin, process.stdout, process.stderr):
                    try:
                        pipe.close()
                    except (OSError, ValueError):
                        pass
        if job is not None:
            job.close()
    if outcome == "succeeded" and (not termination_confirmed or reader_failure.is_set()):
        outcome = "protocol_error"
    return _result(outcome, protocol, process.returncode if process is not None else None, termination_confirmed)


def _result(outcome, protocol, returncode, termination_confirmed):
    return {"outcome": outcome, "output": protocol.output if outcome == "succeeded" else None,
            "thread_id": protocol.thread_id, "usage": protocol.usage if outcome == "succeeded" else None,
            "returncode": returncode, "termination_confirmed": termination_confirmed}


def inspect_command(argv: list[str], cwd: Path, env: dict[str, str],
                    timeout_seconds: float = 10, max_output_bytes: int = 200000) -> dict:
    """Inspect a native executable with bounded UTF-8 output and owned cleanup.

    Intended for explicit version/help checks, not model execution. Stdin is
    closed, stderr is discarded with the same byte limit as stdout, and only a
    fully captured response from a stopped process tree is returned. A nonzero
    exit is still `completed`; the caller decides whether that code is valid.
    """
    if (not isinstance(argv, list) or not argv or
            any(not isinstance(arg, str) or not arg or "\0" in arg for arg in argv) or
            not isinstance(env, dict) or
            any(not isinstance(k, str) or not isinstance(v, str) or "\0" in k + v for k, v in env.items()) or
            type(max_output_bytes) is not int or not 0 < max_output_bytes <= MAX_STDOUT_BYTES or
            isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)) or
            not math.isfinite(timeout_seconds) or timeout_seconds <= 0):
        raise ValueError("invalid inspection arguments")
    try:
        directory = Path(cwd)
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid inspection arguments") from exc
    if not directory.is_absolute() or (os.name == "nt" and Path(argv[0]).suffix.lower() in {".bat", ".cmd"}):
        raise ValueError("invalid inspection arguments")

    process = None
    job = None
    readers = []
    stop_readers = threading.Event()
    failed_reader = threading.Event()
    messages = queue.Queue(maxsize=32)
    captured = bytearray()
    outcome = "launch_failed"
    termination_confirmed = True
    deadline = time.monotonic() + timeout_seconds

    def put(value):
        while not stop_readers.is_set():
            try:
                messages.put(value, timeout=0.05)
                return
            except queue.Full:
                pass

    def read_pipe(name, pipe):
        try:
            while not stop_readers.is_set():
                chunk = pipe.read1(8192)
                if not chunk:
                    break
                put((name, chunk))
        except (OSError, ValueError):
            failed_reader.set()
        finally:
            put((name, None))

    try:
        if os.name == "nt":
            job = _WindowsJob()
        options = {"creationflags": subprocess.CREATE_NO_WINDOW | 0x4} if os.name == "nt" else {"start_new_session": True}
        process = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, cwd=str(directory), env=env,
                                   shell=False, **options)
        termination_confirmed = False
        if job is not None:
            job.attach_and_resume(process)
        outcome = "interrupted"
        for name, pipe in (("stdout", process.stdout), ("stderr", process.stderr)):
            thread = threading.Thread(target=read_pipe, args=(name, pipe), daemon=True)
            readers.append(thread)
            thread.start()
        totals = {"stdout": 0, "stderr": 0}
        closed = set()
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                outcome = "timed_out"
                break
            if failed_reader.is_set():
                break
            if len(closed) == 2 and process.poll() is not None:
                outcome = "completed"
                break
            try:
                name, chunk = messages.get(timeout=min(0.05, remaining))
            except queue.Empty:
                continue
            if chunk is None:
                closed.add(name)
                continue
            totals[name] += len(chunk)
            if totals[name] > max_output_bytes:
                outcome = "output_limit"
                break
            if name == "stdout":
                captured.extend(chunk)
    except KeyboardInterrupt:
        outcome = "interrupted"
    except (OSError, ValueError, subprocess.SubprocessError):
        pass  # No exception or command text is exposed.
    finally:
        if process is not None:
            termination_confirmed = _stop_tree(process, job)
        stop_readers.set()
        for thread in readers:
            thread.join(timeout=3)
        if any(thread.is_alive() for thread in readers):
            termination_confirmed = False
        elif process is not None:
            for pipe in (process.stdout, process.stderr):
                try:
                    pipe.close()
                except (OSError, ValueError):
                    pass
        if job is not None:
            job.close()
    stdout = ""
    if outcome == "completed":
        if not termination_confirmed or failed_reader.is_set():
            outcome = "interrupted"
        else:
            try:
                stdout = captured.decode("utf-8")
            except UnicodeError:
                outcome = "interrupted"
    return {"stdout": stdout, "returncode": process.returncode if process is not None else None,
            "termination_confirmed": termination_confirmed, "outcome": outcome}
