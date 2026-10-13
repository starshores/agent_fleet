"""One owned App Server stdio process per text attempt.

No desktop proxy, private thread resume, tools, or approval grants are used.
Wire request/notification shapes follow the locally pinned App Server schema.
Native steer receipts mean accepted input, not proof of its effect on prose.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
import queue
import re
import subprocess
import threading
import time

from .codex_exec import spawn_owned_process, stop_owned_process


MAX_LINE_BYTES = 1024 * 1024
MAX_TOTAL_BYTES = 8 * 1024 * 1024
MAX_STDERR_BYTES = 256 * 1024
MAX_EVENTS = 8192
MAX_CANDIDATES = 128
_ID = re.compile(r"[A-Za-z0-9_.:-]{1,200}\Z")
_DENIALS = {
    "item/commandExecution/requestApproval": {"decision": "decline"},
    "item/fileChange/requestApproval": {"decision": "decline"},
    "item/permissions/requestApproval": {"permissions": {}, "scope": "turn"},
    "mcpServer/elicitation/request": {"action": "decline"},
    "item/tool/requestUserInput": {"answers": {}},
    "item/tool/call": {"contentItems": [], "success": False},
}


class ProtocolError(Exception):
    """A generic wire failure; never include native payloads in the message."""


class _ExecutionStopped(Exception):
    def __init__(self, outcome):
        self.outcome = outcome


def _identifier(value):
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise ProtocolError("invalid identifier")
    return value


def _rpc_key(value):
    if type(value) is int and -(2**63) <= value < 2**63:
        return int, value
    if isinstance(value, str) and 0 < len(value) <= 200:
        return str, value
    raise ProtocolError("invalid RPC identifier")


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ProtocolError("duplicate JSON key")
        result[key] = value
    return result


def _nonfinite(_):
    raise ProtocolError("nonfinite JSON")


def _decode(line):
    try:
        value = json.loads(line.decode("utf-8"), object_pairs_hook=_unique, parse_constant=_nonfinite)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise ProtocolError("invalid JSON") from exc
    if not isinstance(value, dict):
        raise ProtocolError("invalid message")
    return value


class AppServerProtocol:
    """Pure ordered protocol state; callbacks never receive raw tool payloads."""
    def __init__(self, send, event, cwd, prompt, ack_control=None):
        self.send, self.event = send, event
        self.cwd, self.prompt = str(cwd), prompt
        self.ack_control = ack_control
        self.thread_id = self.turn_id = None
        self.terminal = self.output = self.usage = None
        self.pending = {}
        self.unsettled_controls = set()
        self.sent_controls = set()
        self.server_requests = set()
        self.candidates = []
        self.sequence = 0
        self.event_count = 0
        self.error_seen = False
        self.interrupt_sent = False

    def progress(self, phase, **details):
        self.event("runtime_progress", {"phase": phase, **details})

    def request(self, method, params, control_id=None):
        self.sequence += 1
        identifier = "ma-" + str(self.sequence)
        self.pending[(str, identifier)] = (method, control_id)
        self.send({"id": identifier, "method": method, "params": params})

    def start(self):
        self.request("initialize", {"clientInfo": {"name": "masteragent", "version": "3.0"}})

    def steer(self, control):
        if not isinstance(control, dict):
            raise ProtocolError("invalid control")
        identifier = _identifier(control.get("input_id"))
        if identifier in self.sent_controls:
            raise ProtocolError("duplicate control")
        self.sent_controls.add(identifier)
        self.unsettled_controls.add(identifier)
        text = control.get("text")
        if (self.turn_id is None or self.terminal is not None or not isinstance(text, str)
                or not text.strip() or len(text.encode("utf-8")) > 256000
                or self.ack_control is None):
            raise ProtocolError("invalid control")
        self.request("turn/steer", {"threadId": self.thread_id, "expectedTurnId": self.turn_id,
                                    "input": [{"type": "text", "text": text}]}, identifier)
        self.progress("steer_sent", input_id=identifier, turn_id=self.turn_id)

    def interrupt(self):
        if self.turn_id is not None and self.terminal is None and not self.interrupt_sent:
            self.interrupt_sent = True
            self.request("turn/interrupt", {"threadId": self.thread_id, "turnId": self.turn_id})

    def reconcile_controls(self):
        for identifier in list(self.unsettled_controls):
            if self.ack_control is not None:
                self.ack_control(identifier, "needs_reconcile")
            self.unsettled_controls.remove(identifier)

    def accept(self, value):
        self.event_count += 1
        if self.event_count > MAX_EVENTS or not isinstance(value, dict):
            raise ProtocolError("event limit")
        if "method" in value:
            if not isinstance(value["method"], str) or len(value["method"]) > 160:
                raise ProtocolError("invalid method")
            if "result" in value or "error" in value:
                raise ProtocolError("ambiguous message")
            params = value.get("params", {})
            if not isinstance(params, dict):
                raise ProtocolError("invalid parameters")
            if "id" in value:
                self._server_request(value["id"], value["method"], params)
            else:
                self._notification(value["method"], params)
            return
        if "id" not in value or (("result" in value) == ("error" in value)):
            raise ProtocolError("invalid response")
        key = _rpc_key(value["id"])
        if key not in self.pending:
            raise ProtocolError("unexpected response")
        method, control = self.pending.pop(key)
        if "error" in value or not isinstance(value.get("result"), dict):
            raise ProtocolError("RPC failure")
        result = value["result"]
        if method == "initialize":
            self.send({"method": "initialized", "params": {}})
            self.request("thread/start", {"cwd": self.cwd, "approvalPolicy": "never", "sandbox": "read-only"})
        elif method == "thread/start":
            thread = result.get("thread")
            if not isinstance(thread, dict) or self.thread_id is not None:
                raise ProtocolError("invalid thread")
            self.thread_id = _identifier(thread.get("id"))
            self.event("runtime_thread", {"thread_id": self.thread_id})
            self.request("turn/start", {"threadId": self.thread_id, "cwd": self.cwd,
                                        "input": [{"type": "text", "text": self.prompt}]})
        elif method == "turn/start":
            turn = result.get("turn")
            if not isinstance(turn, dict) or self.turn_id is not None:
                raise ProtocolError("invalid turn")
            self.turn_id = _identifier(turn.get("id"))
            if (turn.get("threadId", self.thread_id) != self.thread_id
                    or result.get("threadId", self.thread_id) != self.thread_id):
                raise ProtocolError("turn scope mismatch")
            self.progress("turn_started", turn_id=self.turn_id)
            candidates, self.candidates = self.candidates, []
            for name, params in candidates:
                self._notification(name, params)
            if turn.get("status") not in (None, "inProgress") or turn.get("error") is not None:
                self._complete_turn(turn)
        elif method == "turn/steer":
            if (self.terminal is not None or result.get("turnId") != self.turn_id
                    or control not in self.unsettled_controls):
                raise ProtocolError("stale steer receipt")
            self.ack_control(control, "accepted")
            self.unsettled_controls.remove(control)
            self.progress("steer_accepted", input_id=control, turn_id=self.turn_id)
        elif method != "turn/interrupt":
            raise ProtocolError("unexpected RPC method")

    def _server_request(self, identifier, method, params):
        key = _rpc_key(identifier)
        if key in self.server_requests:
            raise ProtocolError("duplicate native request")
        self.server_requests.add(key)
        denial = _DENIALS.get(method)
        if denial is None:
            self.send({"id": identifier, "error": {"code": -32601, "message": "Unsupported request"}})
            self.progress("unsupported_request_rejected")
            return
        if (self.thread_id is None or params.get("threadId") != self.thread_id
                or (self.turn_id is not None and params.get("turnId") != self.turn_id)
                or self.terminal is not None):
            raise ProtocolError("native request scope mismatch")
        # The response always refuses authority. Before turn/start replies,
        # rejecting a request does not adopt its unconfirmed turn as our own.
        self.send({"id": identifier, "result": denial})
        self.progress("native_request_denied", method=method)

    def _notification(self, method, params):
        if params.get("threadId") != self.thread_id or self.thread_id is None:
            return
        turn = params.get("turn")
        turn_id = turn.get("id") if isinstance(turn, dict) else params.get("turnId")
        if (isinstance(turn, dict) and "turnId" in params
                and params["turnId"] != turn_id):
            raise ProtocolError("notification turn mismatch")
        if turn_id is None:
            return  # Thread-level progress is never a turn receipt.
        if self.turn_id is None:
            if len(self.candidates) >= MAX_CANDIDATES:
                raise ProtocolError("candidate limit")
            self.candidates.append((method, params))
            return
        if turn_id != self.turn_id:
            return
        if isinstance(turn, dict) and turn.get("threadId", self.thread_id) != self.thread_id:
            raise ProtocolError("notification scope mismatch")
        if method == "turn/completed":
            self._complete_turn(turn)
        elif self.terminal is not None:
            if method == "error" or method in ("item/started", "item/completed", "turn/started"):
                raise ProtocolError("event after terminal")
        elif method in ("item/started", "item/completed"):
            item = params.get("item")
            if not isinstance(item, dict) or item.get("type") not in {"agentMessage", "userMessage", "reasoning", "plan"}:
                raise ProtocolError("tool item forbidden")
            if method == "item/completed" and item["type"] == "agentMessage":
                phase, text = item.get("phase"), item.get("text")
                if phase not in (None, "commentary", "partial_answer", "final_answer") or not isinstance(text, str):
                    raise ProtocolError("invalid agent message")
                if len(text.encode("utf-8")) > 512000:
                    raise ProtocolError("output limit")
                # alpha.17.2 may complete an intermediate answer item before
                # the turn's final answer. Progress must never become a result
                # or overwrite final text already received for this turn.
                if phase in (None, "final_answer"):
                    self.output = text
                self.progress("message_completed", turn_id=self.turn_id)
        elif method == "error":
            if params.get("willRetry") is not True:
                self.error_seen = True
            self.progress("runtime_error", turn_id=self.turn_id)

    def _complete_turn(self, turn):
        if not isinstance(turn, dict) or turn.get("id") != self.turn_id:
            raise ProtocolError("invalid terminal")
        status = turn.get("status")
        outcome = {"completed": "succeeded", "failed": "failed", "interrupted": "interrupted"}.get(status)
        if outcome is None or (outcome == "succeeded" and (turn.get("error") is not None or self.error_seen)):
            raise ProtocolError("invalid terminal status")
        if self.terminal is not None:
            if self.terminal != outcome:
                raise ProtocolError("conflicting terminal")
            return
        if self.unsettled_controls:
            raise ProtocolError("unresolved steer at terminal")
        if outcome == "succeeded" and (not isinstance(self.output, str) or not self.output.strip()):
            raise ProtocolError("missing final response")
        self.terminal = outcome
        self.progress("turn_completed", turn_id=self.turn_id, status=status)


class _Transport:
    def __init__(self, process):
        self.process = process
        self.incoming = queue.Queue(maxsize=64)
        self.outgoing = queue.Queue(maxsize=16)
        self.stop = threading.Event()
        self.failure = threading.Event()
        self.input_closed = False
        self.threads = []
        for target in (self._stdout, self._stderr, self._writer):
            thread = threading.Thread(target=target, daemon=True)
            self.threads.append(thread)
            thread.start()

    def _put(self, kind, payload=None):
        while not self.stop.is_set():
            try:
                self.incoming.put((kind, payload), timeout=0.05)
                return
            except queue.Full:
                pass

    def _stdout(self):
        total = 0
        try:
            while not self.stop.is_set():
                line = self.process.stdout.readline(MAX_LINE_BYTES + 1)
                if not line:
                    self._put("eof")
                    return
                total += len(line)
                if len(line) > MAX_LINE_BYTES or total > MAX_TOTAL_BYTES or not line.endswith(b"\n"):
                    raise ProtocolError("stdout limit")
                self._put("message", _decode(line))
        except (OSError, ValueError, ProtocolError):
            self.failure.set()
            self._put("failed")

    def _stderr(self):
        total = 0
        try:
            while not self.stop.is_set():
                chunk = self.process.stderr.read1(8192)
                if not chunk:
                    return
                total += len(chunk)
                if total > MAX_STDERR_BYTES:
                    raise ProtocolError("stderr limit")
        except (OSError, ValueError, ProtocolError):
            self.failure.set()
            self._put("failed")

    def _writer(self):
        try:
            while not self.stop.is_set():
                try:
                    data = self.outgoing.get(timeout=0.1)
                except queue.Empty:
                    continue
                if data is None:
                    self.process.stdin.close()
                    return
                self.process.stdin.write(data)
                self.process.stdin.flush()
        except (OSError, ValueError):
            self.failure.set()
            self._put("failed")

    def send(self, value):
        if self.input_closed:
            raise ProtocolError("input closed")
        try:
            data = (json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")) + "\n").encode("utf-8")
            if len(data) > MAX_LINE_BYTES:
                raise ProtocolError("input limit")
            self.outgoing.put_nowait(data)
        except (ValueError, UnicodeError, queue.Full) as exc:
            raise ProtocolError("input unavailable") from exc

    def close_input(self):
        if not self.input_closed:
            self.input_closed = True
            try:
                self.outgoing.put_nowait(None)
            except queue.Full as exc:
                raise ProtocolError("input unavailable") from exc

    def close(self):
        self.stop.set()
        for thread in self.threads:
            thread.join(timeout=3)
        if any(thread.is_alive() for thread in self.threads):
            return False
        for pipe in (self.process.stdin, self.process.stdout, self.process.stderr):
            try:
                pipe.close()
            except (OSError, ValueError):
                pass
        return True


def supervise_app_server(argv, prompt, cwd, env, timeout_seconds, heartbeat, on_event,
                         cancelled=None, heartbeat_seconds=5, controls=None, ack_control=None):
    """Run one scoped turn with optional durable steering and deny-only RPCs.

    Real process exit status is retained. controlled_shutdown means this
    controller requested shutdown after receiving the exact turn terminal;
    it never converts an already-dead server into a successful execution.
    """
    if (not isinstance(argv, list) or not argv or any(not isinstance(v, str) or not v or "\0" in v for v in argv)
            or not isinstance(prompt, str) or not isinstance(env, dict)
            or any(not isinstance(k, str) or not isinstance(v, str) or "\0" in k + v for k, v in env.items())
            or not callable(heartbeat) or not callable(on_event)
            or any(value is not None and not callable(value) for value in (cancelled, controls, ack_control))
            or (controls is not None and ack_control is None)):
        raise ValueError("invalid App Server execution arguments")
    for duration in (timeout_seconds, heartbeat_seconds):
        if isinstance(duration, bool) or not isinstance(duration, (int, float)) or not math.isfinite(duration) or duration <= 0:
            raise ValueError("invalid App Server execution timing")
    directory = Path(cwd)
    if not directory.is_absolute() or len(prompt.encode("utf-8")) > 512000 or Path(argv[0]).suffix.lower() in {".cmd", ".bat", ".ps1", ".sh"}:
        raise ValueError("invalid App Server execution arguments")
    process = job = transport = protocol = None
    outcome, termination_confirmed, controlled_shutdown = "launch_failed", True, False
    deadline = time.monotonic() + timeout_seconds
    shutdown_deadline = stop_deadline = None
    stop_outcome = None
    try:
        outcome = "lease_lost"
        heartbeat()
        if cancelled is not None and cancelled():
            outcome = "cancelled"
        else:
            outcome = "launch_failed"
            process, job = spawn_owned_process(argv, directory, env)
        if process is not None:
            termination_confirmed = False
            transport = _Transport(process)
            def guarded_send(value):
                # A callback may observe cancellation inside protocol.accept.
                # Gate the actual enqueue as well as the controller loop.
                if value.get("method") in {"initialize", "thread/start", "turn/start", "turn/steer"}:
                    if cancelled is not None and cancelled():
                        raise _ExecutionStopped("cancelled")
                    if time.monotonic() >= deadline:
                        raise _ExecutionStopped("timed_out")
                transport.send(value)
            protocol = AppServerProtocol(guarded_send, on_event, directory, prompt, ack_control)
            on_event("runtime_started", {"pid": process.pid})
            protocol.start()
            next_heartbeat = time.monotonic() + heartbeat_seconds
            next_control = time.monotonic()
            outcome = "protocol_error"
            while True:
                now = time.monotonic()
                if transport.failure.is_set():
                    raise ProtocolError("transport failed")
                if now >= next_heartbeat:
                    heartbeat()
                    next_heartbeat = now + heartbeat_seconds
                if protocol.terminal is None and stop_outcome is None:
                    if cancelled is not None and cancelled():
                        stop_outcome = "cancelled"
                    elif now >= deadline:
                        stop_outcome = "timed_out"
                    if stop_outcome is not None:
                        if protocol.turn_id is None:
                            outcome = stop_outcome
                            break
                        protocol.interrupt()
                        stop_deadline = now + 1
                if stop_outcome is not None and (protocol.terminal is not None or now >= stop_deadline or process.poll() is not None):
                    outcome = stop_outcome
                    break
                if protocol.terminal is not None and shutdown_deadline is None:
                    if process.poll() is not None:
                        outcome = "interrupted"
                        break
                    controlled_shutdown = True
                    transport.close_input()
                    shutdown_deadline = now + 2
                if shutdown_deadline is not None and now >= shutdown_deadline:
                    outcome = protocol.terminal if process.poll() in (None, 0) else "interrupted"
                    break
                if (controls is not None and protocol.turn_id is not None and protocol.terminal is None
                        and not protocol.unsettled_controls and stop_outcome is None and now >= next_control):
                    next_control = now + min(1, heartbeat_seconds)
                    control = controls()
                    if control is not None:
                        protocol.steer(control)
                try:
                    kind, payload = transport.incoming.get(timeout=0.05)
                except queue.Empty:
                    continue
                if kind == "message":
                    protocol.accept(payload)
                elif kind == "eof":
                    if stop_outcome is not None:
                        outcome = stop_outcome
                    elif controlled_shutdown and protocol.terminal is not None:
                        try:
                            process.wait(timeout=0.2)
                        except subprocess.TimeoutExpired:
                            pass
                        outcome = protocol.terminal if process.poll() in (None, 0) else "interrupted"
                    else:
                        outcome = "interrupted"
                    break
                else:
                    raise ProtocolError("transport failed")
    except _ExecutionStopped as stopped:
        outcome = stopped.outcome
    except KeyboardInterrupt:
        outcome = "interrupted"
    except ProtocolError:
        outcome = "protocol_error"
    except (OSError, ValueError):
        if process is not None:
            outcome = "lease_lost"
    except Exception:
        outcome = "lease_lost"
    finally:
        if protocol is not None:
            try:
                protocol.reconcile_controls()
            except Exception:
                outcome = "lease_lost"
        if process is not None:
            stopped = stop_owned_process(process, job)
            termination_confirmed = isinstance(stopped, dict) and stopped.get("termination_confirmed") is True
            if outcome == "succeeded" and process.returncode != 0:
                if (not isinstance(stopped, dict) or stopped.get("termination_applied") is not True
                        or "pre_stop_returncode" not in stopped or stopped["pre_stop_returncode"] is not None):
                    outcome = "interrupted"
            if transport is not None:
                termination_confirmed = transport.close() and termination_confirmed
                if transport.failure.is_set() and outcome == "succeeded":
                    outcome = "protocol_error"
    if outcome == "succeeded" and (not controlled_shutdown or not termination_confirmed):
        outcome = "protocol_error"
    return {"outcome": outcome, "output": protocol.output if protocol is not None and outcome == "succeeded" else None,
            "thread_id": protocol.thread_id if protocol is not None else None,
            "turn_id": protocol.turn_id if protocol is not None else None,
            "usage": protocol.usage if protocol is not None and outcome == "succeeded" else None,
            "returncode": process.returncode if process is not None else None,
            "termination_confirmed": termination_confirmed, "controlled_shutdown": controlled_shutdown}
