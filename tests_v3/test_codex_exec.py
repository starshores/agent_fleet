"""Real subprocess checks with synthetic Python children, never a model CLI."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from masteragent.codex_exec import execute, inspect_command


THREAD = {"type": "thread.started", "thread_id": "synthetic-thread"}
START = {"type": "turn.started"}
MESSAGE = {"type": "item.completed", "item": {
    "id": "synthetic-message", "type": "agent_message", "text": "synthetic answer"}}
DONE = {"type": "turn.completed", "usage": {
    "input_tokens": 7, "cached_input_tokens": 1, "output_tokens": 3,
    "unexpected_secret": "must not be returned"}}
SUCCESS = [THREAD, START, MESSAGE, DONE]

CHILD = r'''
import json, os, subprocess, sys, time
config = json.loads(sys.stdin.read())
if config.get('descendant'):
    descendant = "import pathlib,time; p=pathlib.Path(" + repr(config['descendant']) + "); "
    descendant += "\ntime.sleep(" + repr(config.get('descendant_start_delay', 0)) + ")\n"
    descendant += "p.write_text(str(time.monotonic()), encoding='utf-8')\n"
    if config.get('descendant_ready'):
        descendant += "print(" + repr(json.dumps(config['descendant_ready'])) + ", flush=True)\n"
    descendant += "while True:\n p.write_text(str(time.monotonic()), encoding='utf-8'); time.sleep(.02)\n"
    subprocess.Popen([sys.executable, '-B', '-c', descendant],
        stdout=sys.stdout, stderr=sys.stderr,
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
if config.get('stderr'):
    sys.stderr.write(config['stderr'])
    sys.stderr.flush()
if config.get('raw') is not None:
    sys.stdout.write(config['raw'])
    sys.stdout.flush()
for event in config.get('events', []):
    print(json.dumps(event), flush=True)
    time.sleep(config.get('delay', 0))
time.sleep(config.get('sleep', 0))
sys.exit(config.get('exit', 0))
'''


class CodexExecTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="masteragent-v3-exec-")
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name).resolve()
        self.events = []
        self.heartbeats = []

    def run_child(self, config, **overrides):
        arguments = {
            "argv": [sys.executable, "-B", "-u", "-c", CHILD],
            "prompt": json.dumps(config), "cwd": self.directory,
            "env": dict(os.environ), "timeout_seconds": 5,
            "heartbeat": lambda: self.heartbeats.append(time.monotonic()),
            "on_event": lambda kind, details: self.events.append((kind, details)),
            "heartbeat_seconds": 0.05,
        }
        arguments.update(overrides)
        return execute(**arguments)

    def assert_stopped(self, outcome, result):
        self.assertEqual(outcome, result["outcome"])
        self.assertTrue(result["termination_confirmed"], result)
        self.assertIsNotNone(result["returncode"])
        if outcome != "succeeded":
            self.assertIsNone(result["output"])
            self.assertIsNone(result["usage"])

    def test_success_stdin_output_and_sanitized_metadata(self):
        result = self.run_child({"events": SUCCESS, "stderr": "synthetic credential\n"})
        self.assert_stopped("succeeded", result)
        self.assertEqual("synthetic answer", result["output"])
        self.assertEqual("synthetic-thread", result["thread_id"])
        self.assertEqual({"input_tokens": 7, "cached_input_tokens": 1, "output_tokens": 3}, result["usage"])
        self.assertEqual("runtime_started", self.events[0][0])
        self.assertIsInstance(self.events[0][1]["pid"], int)
        self.assertIn(("runtime_thread", {"thread_id": "synthetic-thread"}), self.events)
        serialized = json.dumps([result, self.events])
        self.assertNotIn("synthetic credential", serialized)
        self.assertNotIn("must not be returned", serialized)
        self.assertNotIn("synthetic answer", json.dumps(self.events))

    def test_progress_reasoning_and_last_agent_message(self):
        events = [THREAD, {"type": "new_progress", "secret": "discard me"}, START,
                  {"type": "item.completed", "item": {"type": "reasoning", "text": "private thought"}},
                  MESSAGE, {"type": "item.completed", "item": {"type": "agent_message", "text": "final"}}, DONE]
        result = self.run_child({"events": events})
        self.assert_stopped("succeeded", result)
        self.assertEqual("final", result["output"])
        self.assertNotIn("private thought", json.dumps(self.events))
        self.assertNotIn("discard me", json.dumps(self.events))

    def test_explicit_failure_has_no_raw_diagnostic(self):
        for events in ([THREAD, START, {"type": "turn.failed", "error": {"message": "private failure"}}],
                       [{"type": "error", "message": "private failure"}]):
            with self.subTest(events=events):
                result = self.run_child({"events": events, "exit": 1})
                self.assert_stopped("failed", result)
                self.assertNotIn("private failure", json.dumps([result, self.events]))

    def test_missing_order_duplicate_terminal_and_no_output_are_uncertain(self):
        cases = [[], [START, MESSAGE, DONE], [THREAD, MESSAGE, DONE],
                 [THREAD, START, DONE], [THREAD, START, MESSAGE],
                 SUCCESS + [DONE], SUCCESS + [{"type": "error"}],
                 [THREAD, THREAD, START, MESSAGE, DONE],
                 [THREAD, START, START, MESSAGE, DONE],
                 [THREAD, START, {"type": "turn.cancelled"}, MESSAGE, DONE]]
        for events in cases:
            with self.subTest(events=events):
                self.assert_stopped("protocol_error", self.run_child({"events": events}))

    def test_nonzero_after_completed_is_interrupted(self):
        self.assert_stopped("interrupted", self.run_child({"events": SUCCESS, "exit": 9}))

    def test_tools_fail_closed_without_emitting_payloads(self):
        for kind in ("command_execution", "mcp_tool_call", "file_change", "web_search", "unknown_tool"):
            with self.subTest(kind=kind):
                events = [THREAD, START, {"type": "item.started", "item": {
                    "type": kind, "command": "private command", "result": "private result"}}, MESSAGE, DONE]
                result = self.run_child({"events": events})
                self.assert_stopped("protocol_error", result)
                self.assertNotIn("private command", json.dumps([result, self.events]))
                self.assertNotIn("private result", json.dumps([result, self.events]))

    def test_invalid_json_duplicate_keys_and_usage(self):
        cases = ["invalid\n", "[]\n", '{"type":"thread.started","type":"error"}\n',
                 '{"type":"x","value":NaN}\n', "\n", '{"type":"thread.started","thread_id":"bad/path"}\n']
        for raw in cases:
            with self.subTest(raw=raw):
                self.assert_stopped("protocol_error", self.run_child({"raw": raw}))
        for value in (True, -1, 1.1, "private"):
            with self.subTest(usage=value):
                done = {"type": "turn.completed", "usage": {"input_tokens": value}}
                self.assert_stopped("protocol_error", self.run_child({"events": SUCCESS[:-1] + [done]}))

    def test_final_line_without_newline_is_accepted(self):
        result = self.run_child({"raw": "\n".join(json.dumps(event) for event in SUCCESS)})
        self.assert_stopped("succeeded", result)

    def test_heartbeats_continue_during_silent_process_and_timeout(self):
        result = self.run_child({"events": [THREAD, START], "sleep": 30}, timeout_seconds=0.4)
        self.assert_stopped("timed_out", result)
        self.assertGreaterEqual(len(self.heartbeats), 3)

    def test_cancel_and_lease_loss_stop_process(self):
        started = time.monotonic()
        result = self.run_child({"sleep": 30}, cancelled=lambda: time.monotonic() - started > 0.2)
        self.assert_stopped("cancelled", result)
        beats = []
        def lost():
            beats.append(1)
            if len(beats) > 1:
                raise RuntimeError("private lease detail")
        result = self.run_child({"sleep": 30}, heartbeat=lost)
        self.assert_stopped("lease_lost", result)
        self.assertNotIn("private lease detail", json.dumps(result))

    def test_event_callback_failure_stops_process(self):
        def callback(kind, details):
            if kind == "runtime_thread":
                raise RuntimeError("private callback")
        result = self.run_child({"events": [THREAD], "sleep": 30}, on_event=callback)
        self.assert_stopped("lease_lost", result)
        self.assertNotIn("private callback", json.dumps(result))

    def test_initial_lease_loss_and_cancellation_do_not_launch(self):
        def lost():
            raise RuntimeError("private")
        for arguments, outcome in (({"heartbeat": lost}, "lease_lost"),
                                   ({"cancelled": lambda: True}, "cancelled")):
            result = self.run_child({"events": SUCCESS}, **arguments)
            self.assertEqual(outcome, result["outcome"])
            self.assertTrue(result["termination_confirmed"])
            self.assertIsNone(result["returncode"])
            self.assertEqual([], self.events)

    def test_keyboard_interrupt_is_reported_after_stop(self):
        def callback(kind, details):
            if kind == "runtime_thread":
                raise KeyboardInterrupt()
        self.assert_stopped("interrupted", self.run_child({"events": [THREAD], "sleep": 30}, on_event=callback))

    def test_output_limits_fail_closed_without_deadlock(self):
        for limit, config in (("MAX_STDERR_BYTES", {"stderr": "x" * 16384, "events": SUCCESS}),
                              ("MAX_STDOUT_BYTES", {"raw": "x" * 16384}),
                              ("MAX_LINE_BYTES", {"raw": "x" * 16384})):
            with self.subTest(limit=limit), patch("masteragent.codex_exec." + limit, 1024):
                self.assert_stopped("protocol_error", self.run_child(config))
        with patch("masteragent.codex_exec.MAX_EVENTS", 4):
            self.assert_stopped("protocol_error", self.run_child({
                "events": [THREAD, START, {"type": "progress"}, MESSAGE, DONE]}))

    def test_blocked_stdin_writer_is_terminated(self):
        result = self.run_child({}, argv=[sys.executable, "-B", "-c", "import time; time.sleep(30)"],
                                prompt="x" * (1024 * 1024), timeout_seconds=0.3)
        self.assert_stopped("timed_out", result)

    def test_descendant_stops_with_owned_process_tree(self):
        marker = self.directory / "owned-descendant-marker.txt"
        ready = threading.Event()
        def on_event(kind, details):
            self.events.append((kind, details))
            if kind == "runtime_thread" and details["thread_id"] == "synthetic-descendant-ready":
                ready.set()
        # The descendant acknowledges its first write through the real stdout
        # pipe. Startup may exceed the old 0.7-second deadline under load.
        result = self.run_child({
            "descendant": str(marker), "descendant_start_delay": 0.8,
            "descendant_ready": {"type": "thread.started", "thread_id": "synthetic-descendant-ready"},
            "sleep": 30,
        }, timeout_seconds=10, on_event=on_event, cancelled=ready.is_set)
        self.assert_stopped("cancelled", result)
        self.assertTrue(ready.is_set(), "synthetic descendant did not acknowledge startup")
        self.assertTrue(marker.exists(), "synthetic descendant never started")
        content = marker.read_text(encoding="utf-8")
        time.sleep(0.15)
        self.assertEqual(content, marker.read_text(encoding="utf-8"), "owned descendant continued after cancellation")

    @unittest.skipUnless(os.name == "nt", "Kill-on-close proof requires Windows Job Objects")
    def test_abrupt_supervisor_death_closes_job_and_stops_descendant(self):
        marker = self.directory / "crash-descendant-marker.txt"
        supervisor_code = r'''
import json, os, pathlib, sys
from masteragent.codex_exec import execute
configuration = json.loads(sys.stdin.read())
execute([sys.executable, '-B', '-u', '-c', configuration['child']],
        json.dumps({'descendant': configuration['marker'], 'sleep': 30}),
        pathlib.Path(configuration['directory']), dict(os.environ), 25,
        lambda: None, lambda *args: None)
'''
        supervisor = subprocess.Popen(
            [sys.executable, "-B", "-u", "-c", supervisor_code],
            cwd=Path(__file__).resolve().parents[1], stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        def cleanup():
            if supervisor.poll() is None:
                supervisor.kill()
            supervisor.wait(timeout=5)
            supervisor.stdin.close()
            supervisor.stderr.close()
        self.addCleanup(cleanup)
        supervisor.stdin.write(json.dumps({"child": CHILD, "marker": str(marker),
                                           "directory": str(self.directory)}).encode("utf-8"))
        supervisor.stdin.close()
        deadline = time.monotonic() + 5
        while not marker.exists() and supervisor.poll() is None and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertTrue(marker.exists(), "synthetic supervisor's descendant never started")
        supervisor.kill()  # Only the exact Popen handle created by this test.
        supervisor.wait(timeout=5)
        time.sleep(0.15)
        content = marker.read_text()
        time.sleep(0.2)
        self.assertEqual(content, marker.read_text(), "job descendants survived abrupt supervisor death")

    def test_launch_error_and_argument_validation_do_not_leak_paths(self):
        result = self.run_child({}, argv=[str(self.directory / "private-nonexistent.exe")])
        self.assertEqual("launch_failed", result["outcome"])
        self.assertTrue(result["termination_confirmed"])
        self.assertIsNone(result["returncode"])
        self.assertNotIn("private-nonexistent", json.dumps(result))
        for overrides in ({"timeout_seconds": float("nan")}, {"heartbeat_seconds": True},
                          {"timeout_seconds": -1}, {"cwd": Path("relative")},
                          {"prompt": "x" * (2 * 1024 * 1024 + 1)}):
            with self.subTest(overrides=list(overrides)), self.assertRaises(ValueError):
                self.run_child({}, **overrides)

    def inspect_child(self, code, **options):
        return inspect_command([sys.executable, "-B", "-u", "-c", code],
                               self.directory, dict(os.environ), **options)

    def test_inspection_stdout_utf8_and_stderr_privacy(self):
        result = self.inspect_child("import sys; print('synthetic help'); sys.stderr.write('private diagnostic'); sys.exit(7)")
        self.assertEqual("completed", result["outcome"])
        self.assertEqual("synthetic help\n", result["stdout"].replace("\r\n", "\n"))
        self.assertEqual(7, result["returncode"])
        self.assertTrue(result["termination_confirmed"])
        self.assertNotIn("private diagnostic", json.dumps(result))
        result = self.inspect_child("import sys; sys.stdout.buffer.write(b'\\xff')")
        self.assertEqual("interrupted", result["outcome"])
        self.assertEqual("", result["stdout"])
        self.assertTrue(result["termination_confirmed"])

    def test_inspection_stdout_and_stderr_flood_are_bounded(self):
        for stream in ("stdout", "stderr"):
            with self.subTest(stream=stream):
                result = self.inspect_child("import sys,time; sys." + stream + ".write('x'*20000); time.sleep(30)",
                                            max_output_bytes=1000)
                self.assertEqual("output_limit", result["outcome"])
                self.assertEqual("", result["stdout"])
                self.assertTrue(result["termination_confirmed"])

    def test_inspection_descendant_holding_pipes_does_not_escape_timeout(self):
        marker = self.directory / "inspection-descendant-marker.txt"
        child = ("import pathlib,time; p=pathlib.Path(" + repr(str(marker)) + "); "
                 "\nwhile True:\n p.write_text(str(time.monotonic())); time.sleep(.02)\n")
        code = ("import subprocess,sys; subprocess.Popen([sys.executable,'-B','-c'," + repr(child)
                + "], stdout=sys.stdout, stderr=sys.stderr, creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0)); print('parent completed')")
        started = time.monotonic()
        result = self.inspect_child(code, timeout_seconds=0.7)
        self.assertEqual("timed_out", result["outcome"])
        self.assertTrue(result["termination_confirmed"])
        self.assertEqual("", result["stdout"])
        self.assertLess(time.monotonic() - started, 5)
        self.assertTrue(marker.exists(), "synthetic inspection descendant never started")
        content = marker.read_text()
        time.sleep(0.15)
        self.assertEqual(content, marker.read_text(), "inspection descendant survived cleanup")


if __name__ == "__main__":
    unittest.main()
