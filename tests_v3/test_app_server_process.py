"""Owned stdio protocol tests using local synthetic Python children only."""
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from masteragent.codex_app_server import supervise_app_server
from masteragent import codex_app_server


CHILD = r'''
import json, os, pathlib, subprocess, sys, time
mode = sys.argv[1]
def send(value):
    print(json.dumps(value), flush=True)
def reply(message, result):
    send({'id':message['id'], 'result':result})
def final(status='completed'):
    if status == 'completed':
        send({'method':'item/completed','params':{'threadId':'thread-a','turnId':'turn-a',
              'item':{'id':'item-a','type':'agentMessage','phase':'final_answer','text':'synthetic answer'}}})
    send({'method':'turn/completed','params':{'threadId':'thread-a','turn':{'id':'turn-a','status':status}}})
sys.stderr.write('private diagnostic must not escape\n')
sys.stderr.flush()
if mode == 'flood':
    sys.stdout.write('x'*8192)
    sys.stdout.flush()
    time.sleep(30)
if mode == 'bad_json':
    print('{broken', flush=True)
    time.sleep(30)
if mode == 'silent':
    time.sleep(30)
if mode == 'descendant':
    marker = sys.argv[2]
    child = 'import pathlib,time; p=pathlib.Path('+repr(marker)+');\nwhile True:\n p.write_text(str(time.monotonic())); time.sleep(.02)\n'
    subprocess.Popen([sys.executable,'-B','-c',child], stdin=subprocess.DEVNULL, stdout=sys.stdout, stderr=sys.stderr,
                     creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
for line in sys.stdin:
    message = json.loads(line)
    method = message.get('method')
    if method == 'initialize':
        reply(message, {})
    elif method == 'thread/start':
        result = {'thread':{'id':'thread-a'}}
        if mode.startswith('observed_'):
            result['model'] = 'synthetic-reported-model'
        reply(message, result)
    elif method == 'turn/start':
        assert message['params']['input'][0]['text'] == 'synthetic prompt'
        if mode == 'early':
            final()
        reply(message, {'turn':{'id':'turn-a','status':'inProgress'}})
        if mode.startswith('observed_'):
            send({'method':'thread/tokenUsage/updated','params':{'threadId':'thread-a','turnId':'turn-a',
                  'tokenUsage':{'total':{'inputTokens':20,'cachedInputTokens':4,'outputTokens':10,
                    'reasoningOutputTokens':3,'totalTokens':30}}}})
            if mode == 'observed_success':
                final()
            elif mode == 'observed_exit':
                os._exit(7)
        if mode in ('normal','bad_exit','stubborn','eof_exit'):
            final()
        elif mode == 'failed':
            final('failed')
        elif mode == 'exit':
            os._exit(7)
        elif mode == 'approval':
            send({'id':101,'method':'item/commandExecution/requestApproval',
                  'params':{'threadId':'thread-a','turnId':'turn-a','itemId':'tool-a','command':'private command'}})
    elif method == 'turn/steer':
        assert message['params']['expectedTurnId'] == 'turn-a'
        assert message['params']['threadId'] == 'thread-a'
        if mode == 'steer_race':
            final()
        elif mode == 'steer_exit':
            os._exit(8)
        else:
            reply(message, {'turnId':'turn-a'})
            final()
    elif method == 'turn/interrupt':
        if mode != 'ignore_interrupt':
            reply(message, {})
            final('interrupted')
    elif message.get('id') == 101:
        assert message.get('result') == {'decision':'decline'}
        final()
if mode == 'stubborn':
    time.sleep(30)
if mode == 'bad_exit':
    sys.exit(7)
if mode == 'eof_exit':
    os.close(1)
    time.sleep(.4)
    os._exit(7)
'''


class AppServerProcessTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="ma3-appserver-process-")
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name).resolve()
        self.events, self.beats, self.acks = [], [], []

    def run_server(self, mode="normal", **changes):
        arguments = {
            "argv": [sys.executable, "-B", "-u", "-c", CHILD, mode],
            "prompt": "synthetic prompt", "cwd": self.directory, "env": dict(os.environ),
            "timeout_seconds": 5, "heartbeat_seconds": 0.05,
            "heartbeat": lambda: self.beats.append(time.monotonic()),
            "on_event": lambda name, detail: self.events.append((name, detail)),
        }
        arguments.update(changes)
        return supervise_app_server(**arguments)

    def assert_outcome(self, outcome, value):
        self.assertEqual(outcome, value["outcome"], value)
        self.assertTrue(value["termination_confirmed"], value)
        if outcome != "succeeded":
            self.assertIsNone(value["output"])

    def controls(self):
        if getattr(self, "issued_control", False):
            return None
        self.issued_control = True
        return {"input_id": "input-a", "text": "synthetic correction"}

    def ack(self, identifier, state):
        self.acks.append((identifier, state))

    def test_clean_turn_then_owned_shutdown_preserves_result_and_privacy(self):
        for mode in ("normal", "early"):
            with self.subTest(mode=mode):
                result = self.run_server(mode)
                self.assert_outcome("succeeded", result)
                self.assertTrue(result["controlled_shutdown"])
                self.assertEqual(0, result["returncode"])
                self.assertEqual("synthetic answer", result["output"])
                self.assertEqual("thread-a", result["thread_id"])
                self.assertEqual("turn-a", result["turn_id"])
                self.assertNotIn("private diagnostic", json.dumps([result, self.events]))
                self.assertNotIn("synthetic answer", json.dumps(self.events))

    def test_stubborn_server_is_owned_and_killed_after_exact_turn_terminal(self):
        result = self.run_server("stubborn")
        self.assert_outcome("succeeded", result)
        self.assertTrue(result["controlled_shutdown"])
        self.assertNotEqual(0, result["returncode"])

    def test_native_approval_request_is_declined_and_never_logged_raw(self):
        result = self.run_server("approval")
        self.assert_outcome("succeeded", result)
        self.assertTrue(any(details.get("phase") == "native_request_denied" for _, details in self.events))
        self.assertNotIn("private command", json.dumps([result, self.events]))

    def test_observed_usage_survives_success_timeout_and_unexpected_exit(self):
        for mode, outcome in (("observed_success", "succeeded"), ("observed_wait", "timed_out"),
                              ("observed_exit", "interrupted")):
            with self.subTest(mode=mode):
                result = self.run_server(mode, timeout_seconds=1)
                self.assert_outcome(outcome, result)
                self.assertEqual("synthetic-reported-model", result["model_reported"])
                self.assertEqual("thread/start", result["model_reported_source"])
                self.assertEqual(30, result["usage"]["total_tokens"])
                self.assertEqual("thread/tokenUsage/updated", result["usage_source"])
                self.assertEqual("fresh_thread_total", result["usage_scope"])

    def test_steer_is_sent_while_turn_is_running_and_acknowledged(self):
        result = self.run_server("steer", controls=self.controls, ack_control=self.ack)
        self.assert_outcome("succeeded", result)
        self.assertEqual([("input-a", "accepted")], self.acks)
        self.assertNotIn("synthetic correction", json.dumps(self.events))

    def test_lost_steer_receipt_and_terminal_race_hold_input_without_replay(self):
        for mode, outcome in (("steer_race", "protocol_error"), ("steer_exit", "interrupted")):
            with self.subTest(mode=mode):
                self.issued_control = False
                self.acks.clear()
                result = self.run_server(mode, controls=self.controls, ack_control=self.ack)
                self.assert_outcome(outcome, result)
                self.assertEqual([("input-a", "needs_reconcile")], self.acks)

    def test_failure_and_unexpected_exit_are_not_success(self):
        self.assert_outcome("failed", self.run_server("failed"))
        self.assert_outcome("interrupted", self.run_server("exit"))
        self.assert_outcome("interrupted", self.run_server("bad_exit"))

    def test_natural_failure_between_eof_and_cleanup_cannot_be_controlled_success(self):
        original = codex_app_server.stop_owned_process
        def delayed_cleanup(process, job):
            process.wait(timeout=2)  # A scheduling gap before owned cleanup.
            return original(process, job)
        with patch.object(codex_app_server, "stop_owned_process", delayed_cleanup):
            result = self.run_server("eof_exit")
        self.assert_outcome("interrupted", result)
        self.assertEqual(7, result["returncode"])
        self.assertTrue(result["controlled_shutdown"])

    def test_missing_or_failed_owned_stop_evidence_cannot_succeed(self):
        original = codex_app_server.stop_owned_process
        for replacement in ({}, {"termination_confirmed": False},
                            {"termination_confirmed": True, "termination_applied": True}):
            with self.subTest(evidence=replacement):
                def incomplete_cleanup(process, job):
                    original(process, job)
                    return replacement
                with patch.object(codex_app_server, "stop_owned_process", incomplete_cleanup):
                    result = self.run_server("stubborn")
                self.assertNotEqual("succeeded", result["outcome"])
                self.assertIsNone(result["output"])

    def test_cancel_sends_native_interrupt_and_stops_owned_server(self):
        started = time.monotonic()
        result = self.run_server("waiting", cancelled=lambda: time.monotonic() - started > 0.25)
        self.assert_outcome("cancelled", result)
        self.assertEqual("turn-a", result["turn_id"])
        self.assertGreater(len(self.beats), 1)

    def test_timeout_cleans_silent_or_unresponsive_server_without_blocking_stdin(self):
        for mode in ("silent", "ignore_interrupt"):
            with self.subTest(mode=mode):
                result = self.run_server(mode, timeout_seconds=0.3)
                self.assert_outcome("timed_out", result)

    def test_lease_callback_and_event_callback_failure_stop_process(self):
        def heartbeat():
            self.beats.append(1)
            if len(self.beats) > 1:
                raise RuntimeError("private lease failure")
        result = self.run_server("waiting", heartbeat=heartbeat)
        self.assert_outcome("lease_lost", result)
        self.assertNotIn("private lease failure", json.dumps(result))
        def callback(name, detail):
            if name == "runtime_thread":
                raise RuntimeError("private callback failure")
        self.assert_outcome("lease_lost", self.run_server("normal", on_event=callback))

    def test_bad_json_and_stdout_flood_fail_closed(self):
        self.assert_outcome("protocol_error", self.run_server("bad_json"))
        with patch("masteragent.codex_app_server.MAX_LINE_BYTES", 1024):
            self.assert_outcome("protocol_error", self.run_server("flood"))

    def test_cancelled_server_descendant_does_not_survive(self):
        marker = self.directory / "descendant-marker.txt"
        result = self.run_server("descendant", argv=[sys.executable, "-B", "-u", "-c", CHILD,
                                                       "descendant", str(marker)],
                                 cancelled=marker.exists)
        self.assert_outcome("cancelled", result)
        self.assertTrue(marker.exists(), "synthetic descendant never started")
        content = marker.read_text(encoding="utf-8")
        time.sleep(0.1)
        self.assertEqual(content, marker.read_text(encoding="utf-8"))

    def test_initial_lease_failure_or_cancel_does_not_launch(self):
        def failed():
            raise ValueError("private initial lease failure")
        for arguments, outcome in (({"heartbeat": failed}, "lease_lost"),
                                   ({"cancelled": lambda: True}, "cancelled")):
            with self.subTest(outcome=outcome):
                self.assert_outcome(outcome, self.run_server(**arguments))
                self.assertEqual([], self.events)

    def test_cancellation_during_handshake_does_not_start_thread_or_turn(self):
        original = codex_app_server._Transport.send
        for stage in ("runtime_started", "runtime_thread"):
            with self.subTest(stage=stage):
                sent, stopped = [], []
                def send(transport, message):
                    sent.append(message)
                    return original(transport, message)
                def event(name, details):
                    if name == stage:
                        stopped.append(True)
                with patch.object(codex_app_server._Transport, "send", send):
                    result = self.run_server(on_event=event, cancelled=lambda: bool(stopped))
                self.assert_outcome("cancelled", result)
                self.assertIsNone(result["turn_id"])
                if stage == "runtime_started":
                    self.assertIsNone(result["thread_id"])
                    self.assertNotIn("thread/start", [item.get("method") for item in sent])
                self.assertNotIn("turn/start", [item.get("method") for item in sent])

    def test_handshake_timeout_does_not_start_thread_or_turn(self):
        sent = []
        original = codex_app_server._Transport.send
        def send(transport, message):
            sent.append(message)
            return original(transport, message)
        def event(name, details):
            if name == "runtime_started":
                time.sleep(0.02)
        with patch.object(codex_app_server._Transport, "send", send):
            result = self.run_server(on_event=event, timeout_seconds=0.01)
        self.assert_outcome("timed_out", result)
        self.assertIsNone(result["thread_id"])
        self.assertIsNone(result["turn_id"])
        self.assertNotIn("thread/start", [item.get("method") for item in sent])
        self.assertNotIn("turn/start", [item.get("method") for item in sent])


if __name__ == "__main__":
    unittest.main()
