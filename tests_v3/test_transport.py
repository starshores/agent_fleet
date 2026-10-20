"""Local controlled-source integration; no native runtime, credentials or old bus."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from masteragent.errors import PlatformError
from masteragent.receipt_store import ReceiptStore
from masteragent.service import Platform
from masteragent.source_store import SourceStore
from masteragent.state_waiter import StateWaiter
from masteragent.transport import TransportWorker, _LeaseKeeper
from masteragent.routing import _digest, _json


class TransportTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="ma-dev4-transport-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.platform = Platform(self.root / "state")
        workspace = self.root / "workspace"
        workspace.mkdir()
        package = self.root / "synthetic.json"
        package.write_text(json.dumps({
            "schema_version": 1, "platform_api": 1, "package_id": "synthetic", "version": "1.0.0",
            "roles": [{"role_id": "worker", "display_name": "Worker", "instructions": "Synthetic text only",
                       "tools": ["echo"]}],
            "knowledge": {}, "tools": {"echo": {"adapter": "echo", "description": "Echo"}}, "workflows": {},
        }), encoding="utf-8")
        self.platform.install("a", package, workspace)
        self.platform.activate("a")
        self.actor = self.platform.bind("a", "worker")
        self.platform.configure_route("route", "synthetic.source", "v3", "a", "worker", 1,
                                      "synthetic.destination", "inbox")
        self.source = SourceStore(self.root / "source", "synthetic.source", initialize=True)
        self.receiver = ReceiptStore(self.root / "receiver", "synthetic.destination", "inbox", initialize=True)
        self.platform.bind_route_endpoints("route", 1, self.source, self.receiver)

    def worker(self, **kwargs):
        worker = TransportWorker(self.platform, "route", "connector", self.source, self.receiver, **kwargs)
        self.addCleanup(worker.stop)
        return worker

    def revise(self, **kwargs):
        route = self.platform.routes()[0]
        args = {key: route[key] for key in ("route_id", "source_namespace", "owner_lane", "installation_id",
                                           "role_id", "generation", "destination_namespace", "destination_id",
                                           "admission_open", "claim_open", "delivery_open")}
        args.update(expected_epoch=route["route_epoch"])
        args.update(kwargs)
        return self.platform.configure_route(**args)

    def finish(self, admission, text="完整结果 🙂\n尾段 SENTINEL"):
        attempt = self.platform.claim(self.actor, admission["task_id"], "echo", 60)
        self.platform.complete(self.actor, attempt["attempt_id"], attempt["lease_token"], text)
        return self.platform.deliveries("a")[-1]

    def assert_code(self, code, call, *args):
        with self.assertRaises(PlatformError) as caught:
            call(*args)
        self.assertEqual(code, caught.exception.code)

    def test_complete_round_trip_has_one_execution_and_preserves_full_text(self):
        worker = self.worker()
        text = "中文、emoji🙂\r\n" * 200 + "END"
        self.source.publish(1, text)
        admitted = worker.run_once()
        self.assertEqual([], admitted["errors"])
        self.assertEqual(1, len(admitted["sources"]))
        self.assertEqual([], self.platform.attempts("a"))
        delivery = self.finish(admitted["sources"][0], text)
        sent = worker.run_once()
        self.assertEqual("received", sent["deliveries"][0]["state"])
        self.assertEqual(text, self.receiver.read_text(self.platform.delivery_envelope(delivery["delivery_id"])))
        self.assertEqual([], worker.run_once()["deliveries"])
        self.assertEqual(1, len(self.platform.attempts("a")))
        self.assertIsNone(sent["deliveries"][0]["agent_processed"])
        self.assertIsNone(sent["deliveries"][0]["user_read"])

    def test_two_connectors_keep_typed_sources_unique(self):
        self.source.publish(1, "integer")
        self.source.publish("1", "string")
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(lambda _: self.worker().run_once(), range(2)))
        self.worker().run_once()
        rows = self.platform.admissions()
        self.assertEqual(2, len(rows))
        self.assertEqual({int, str}, {type(row["source_id"]) for row in rows})
        self.assertEqual([], self.source.pending())
        self.assertEqual([], self.platform.attempts("a"))

    def test_source_stop_after_admit_leaves_recoverable_same_task(self):
        worker = self.worker()
        self.source.publish("m", "test")
        admit = self.platform.admit_source
        def stop_after_commit(*args, **kwargs):
            value = admit(*args, **kwargs)
            worker.stop()
            return value
        with patch.object(self.platform, "admit_source", side_effect=stop_after_commit):
            self.assert_code("transport_stopped", worker.transfer_source, "m")
        first = self.platform.admissions()[0]
        self.assertFalse(self.source.get("m")["source_confirmed"])
        second = self.worker().transfer_source("m")
        self.assertEqual(first["task_id"], second["task_id"])
        self.assertTrue(second["source_confirmed"])

    def test_stop_after_receive_holds_and_reconciliation_keeps_execution_count(self):
        worker = self.worker()
        self.source.publish("m", "test")
        delivery = self.finish(worker.transfer_source("m"))
        receive = self.receiver.receive
        def stop_after_store(envelope):
            receipt = receive(envelope)
            worker.stop()
            return receipt
        with patch.object(self.receiver, "receive", side_effect=stop_after_store):
            held = worker.send_result(delivery["delivery_id"])
        self.assertEqual("held", held["state"])
        self.assertEqual("sender_stopped", held["reason"])
        self.assertEqual([], self.worker().run_once()["deliveries"])
        reconciled = self.platform.reconcile_delivery(delivery["delivery_id"], self.receiver)
        self.assertEqual("received", reconciled["state"])
        self.assertEqual(1, reconciled["send_count"])
        self.assertEqual(1, len(self.platform.attempts("a")))

    def test_frozen_admission_claim_and_delivery_are_independent(self):
        self.source.publish("m", "test")
        self.revise(admission_open=False)
        self.assert_code("route_admission_closed", self.worker().transfer_source, "m")
        self.assertEqual([], self.platform.admissions())
        self.revise(admission_open=True, claim_open=False)
        admitted = self.worker().transfer_source("m")
        with self.assertRaises(PlatformError):
            self.platform.claim(self.actor, admitted["task_id"], "echo", 60)
        self.assertEqual([], self.platform.attempts("a"))
        self.revise(claim_open=True, delivery_open=False)
        delivery = self.finish(admitted)
        self.assert_code("delivery_frozen", self.worker().send_result, delivery["delivery_id"])
        self.assertEqual("pending", self.platform.delivery_status(delivery["delivery_id"])["state"])
        self.revise(delivery_open=True)
        self.assertEqual("received", self.worker().send_result(delivery["delivery_id"])["state"])

    def test_lane_change_during_claim_does_not_silently_confirm_legacy(self):
        self.source.publish("m", "test")
        worker = self.worker()
        claim = self.platform.claim_source
        def change_then_claim(*args):
            self.revise(owner_lane="legacy")
            return claim(*args)
        with patch.object(self.platform, "claim_source", side_effect=change_then_claim):
            self.assert_code("stale_route", worker.transfer_source, "m")
        self.assertEqual([], self.platform.admissions())
        self.assertFalse(self.source.get("m")["source_confirmed"])

    def test_wrong_receiver_rejected_before_any_task(self):
        replacement = ReceiptStore(self.root / "other", "synthetic.destination", "inbox", initialize=True)
        self.source.publish("m", "test")
        self.assert_code("route_endpoint_mismatch", TransportWorker,
                         self.platform, "route", "connector", self.source, replacement)
        self.assertEqual([], self.platform.admissions())
        self.assertEqual([], self.platform.attempts("a"))

    def test_completed_source_is_checked_against_original_admission(self):
        worker = self.worker()
        self.source.publish("m", "test")
        original = worker.transfer_source("m")
        self.revise(admission_open=False)
        replay = worker.transfer_source("m")
        self.assertEqual(original, replay)
        self.assertEqual(1, len(self.platform.admissions()))

    def test_completed_source_with_rehashed_changed_text_cannot_hide_conflict(self):
        worker = self.worker()
        self.source.publish("m", "original")
        worker.transfer_source("m")
        record = self.source.get("m")
        envelope = record["envelope"]
        envelope["text"] = "changed"
        envelope["text_bytes"] = 7
        envelope["text_sha256"] = _digest("changed")
        envelope["envelope_sha256"] = _digest(_json({k: v for k, v in envelope.items() if k != "envelope_sha256"}))
        with patch.object(self.source, "get", return_value=record):
            self.assert_code("source_conflict", worker.transfer_source, "m")
        self.assertEqual(1, len(self.platform.admissions()))

    def test_process_kill_after_native_confirmation_resumes_original_task(self):
        self.source.publish("crash", "synthetic crash recovery")
        script = """
import sys, threading
from pathlib import Path
from masteragent.service import Platform
from masteragent.source_store import SourceStore
from masteragent.receipt_store import ReceiptStore
from masteragent.transport import TransportWorker
root = Path(sys.argv[1])
platform = Platform(root / 'state', initialize=False, clock=lambda: 1000.)
source = SourceStore(root / 'source', 'synthetic.source')
receiver = ReceiptStore(root / 'receiver', 'synthetic.destination', 'inbox')
confirm = source.confirm
def crash_window(*args):
    record = confirm(*args)
    print('native-confirmed', flush=True)
    threading.Event().wait(60)
    return record
source.confirm = crash_window
TransportWorker(platform, 'route', 'child', source, receiver).transfer_source('crash')
"""
        process = subprocess.Popen(
            [sys.executable, "-B", "-c", script, str(self.root)],
            cwd=Path(__file__).resolve().parents[1], stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        marker, lines = threading.Event(), []
        def read_marker():
            lines.append(process.stdout.readline())
            marker.set()
        reader = threading.Thread(target=read_marker, daemon=True)
        reader.start()
        try:
            self.assertTrue(marker.wait(10), "synthetic child did not reach confirmation window")
            self.assertEqual([b"native-confirmed\r\n" if os.name == "nt" else b"native-confirmed\n"], lines)
        finally:
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=5)
            reader.join(2)
        before = self.platform.admissions()[0]
        self.assertFalse(before["source_confirmed"])
        self.assertTrue(self.source.get("crash")["source_confirmed"])
        # The parent's actual wall clock is past the child's deterministic
        # lease deadline. This models recovery after TTL, not forced takeover.
        after = self.worker().transfer_source("crash")
        self.assertEqual(before["admission_id"], after["admission_id"])
        self.assertEqual(before["task_id"], after["task_id"])
        self.assertTrue(after["source_confirmed"])
        self.assertEqual([], self.platform.attempts("a"))

    def test_two_executor_processes_start_only_one_synthetic_child_for_same_source(self):
        self.source.publish("compete", "one synthetic execution")
        admission = self.worker().transfer_source("compete")
        marker = self.root / "synthetic-launches.txt"
        script = """
import json, os, subprocess, sys
from pathlib import Path
from masteragent.service import Platform, Actor
from masteragent.errors import PlatformError
incoming = json.loads(sys.stdin.readline())
platform = Platform(incoming['state'], initialize=False)
actor = Actor(**incoming['actor'])
print('ready', flush=True)
sys.stdin.readline()
try:
    claim = platform.claim(actor, incoming['task'], 'echo', 60)
except PlatformError as exc:
    print(json.dumps({'launched': False, 'code': exc.code}), flush=True)
else:
    child = "import pathlib,sys; p=pathlib.Path(sys.argv[1]); f=p.open('a',encoding='utf-8'); f.write('started\\\\n'); f.close()"
    subprocess.run([sys.executable, '-B', '-c', child, incoming['marker']], check=True,
                   stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                   creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0, timeout=10)
    platform.complete(actor, claim['attempt_id'], claim['lease_token'], 'one synthetic execution')
    print(json.dumps({'launched': True, 'attempt_id': claim['attempt_id']}), flush=True)
"""
        processes, readers, ready, lines = [], [], [], []
        payload = json.dumps({"state": str(self.platform.state_root), "actor": asdict(self.actor),
                              "task": admission["task_id"], "marker": str(marker)}) + "\n"
        try:
            for _ in range(2):
                process = subprocess.Popen(
                    [sys.executable, "-B", "-c", script],
                    cwd=Path(__file__).resolve().parents[1],
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
                processes.append(process)
                event, received = threading.Event(), []
                ready.append(event)
                lines.append(received)
                def read_marker(proc=process, signal=event, values=received):
                    values.append(proc.stdout.readline().strip())
                    signal.set()
                reader = threading.Thread(target=read_marker, daemon=True)
                readers.append(reader)
                reader.start()
                process.stdin.write(payload.encode("utf-8"))
                process.stdin.flush()
            for event, received in zip(ready, lines):
                self.assertTrue(event.wait(10), "synthetic executor did not become ready")
                self.assertEqual([b"ready"], received)
            for process in processes:
                process.stdin.write(b"\n")
                process.stdin.flush()
            results = []
            for process in processes:
                output, error = process.communicate(timeout=15)
                self.assertEqual(0, process.returncode, error.decode("utf-8", errors="replace"))
                results.append(json.loads(output))
            self.assertEqual(1, sum(row["launched"] for row in results))
            self.assertEqual(["task_not_queued"], [row["code"] for row in results if not row["launched"]])
            self.assertEqual(["started"], marker.read_text(encoding="utf-8").splitlines())
            self.assertEqual(1, len(self.platform.attempts("a")))
            self.assertEqual(1, len(self.platform.results("a")))
            self.assertEqual(1, len(self.platform.deliveries("a")))
        finally:
            for process in processes:
                if process.poll() is None:
                    process.kill()
                process.communicate(timeout=5)
            for reader in readers:
                reader.join(2)

    def test_first_failed_page_does_not_starve_later_source(self):
        worker = self.worker()
        for number in range(101):
            self.source.publish(number, "test")
        original = worker.transfer_source
        def selective(identity):
            if identity < 100:
                raise PlatformError("synthetic_unavailable", "Untrusted secret must not be logged")
            return original(identity)
        with patch.object(worker, "transfer_source", side_effect=selective):
            first = worker.run_once()
            second = worker.run_once()
        self.assertEqual(100, len(first["errors"]))
        self.assertNotIn("secret", json.dumps(first))
        self.assertEqual(100, second["sources"][0]["source_id"])
        self.assertEqual(1, len(self.platform.admissions()))

    def test_lease_renewal_failure_stops_after_io_without_platform_confirmation(self):
        worker = self.worker()
        self.source.publish("m", "test")
        confirm = self.source.confirm
        renewed = threading.Event()
        entered = threading.Event()
        keepers = []
        def fail():
            if not entered.wait(3):
                raise RuntimeError("synthetic confirmation did not start")
            renewed.set()
            raise PlatformError("synthetic_expiry", "Synthetic")
        def keeper_factory(renew, seconds):
            keeper = _LeaseKeeper(fail, .03)
            keepers.append(keeper)
            return keeper
        def slow_confirm(*args):
            value = confirm(*args)
            entered.set()
            self.assertTrue(renewed.wait(3))
            keepers[0].thread.join(3)
            return value
        with patch("masteragent.transport._LeaseKeeper", side_effect=keeper_factory), \
                patch.object(self.source, "confirm", side_effect=slow_confirm):
            self.assert_code("transport_lease_lost", worker.transfer_source, "m")
        self.assertTrue(self.source.get("m")["source_confirmed"])
        self.assertFalse(self.platform.admissions()[0]["source_confirmed"])
        self.assertTrue(self.worker().transfer_source("m")["source_confirmed"])

    def test_stopped_worker_never_claims_new_source_or_delivery(self):
        worker = self.worker()
        self.source.publish("m", "test")
        worker.stop()
        self.assertEqual({"sources": [], "deliveries": [], "errors": []}, worker.run_once())
        self.assert_code("transport_stopped", worker.transfer_source, "m")
        self.assertEqual([], self.platform.admissions())

    def test_failed_stop_notification_still_joins_both_lanes_and_reports_failure(self):
        waiters = []
        class FailedWake:
            def __init__(self, *_args, **_kwargs):
                self.closed = False
                self.wait_timeouts = []
                waiters.append(self)
            def __enter__(self):
                return self
            def arm(self):
                pass
            def wait(self, timeout):
                self.wait_timeouts.append(timeout)
                threading.Event().wait(.02)
                return False
            def wake(self):
                raise PlatformError("state_waiter_failed", "Synthetic SetEvent failure")
            def __exit__(self, *_):
                self.closed = True
        worker = self.worker(waiter_factory=FailedWake)
        self.assert_code("transport_stop_failed", worker.run, .3)
        self.assertFalse(worker._running)
        self.assertEqual(2, len(waiters))
        self.assertTrue(all(waiter.closed for waiter in waiters))
        self.assertTrue(all(max(waiter.wait_timeouts) <= 30 for waiter in waiters))
        self.assertEqual([], [t for t in threading.enumerate() if t.name.startswith("ma-transport-")])

    @unittest.skipUnless(os.name == "nt", "Windows directory notifications")
    def test_two_lane_service_startup_backlog_and_arrival_after_empty_scan(self):
        self.source.publish("backlog", "before service")
        received = threading.Event()
        injected = threading.Event()
        reports = []
        worker = None
        def state(event):
            if event["lane"] == "source" and event["phase"] == "waiting" and not injected.is_set():
                injected.set()
                self.source.publish("arrival", "between query and wait")
            if event["lane"] == "source" and event["phase"] == "scanned":
                for admission in event["sources"]:
                    self.finish(admission)
            if event["lane"] == "delivery" and event["phase"] == "scanned":
                reports.extend(event["deliveries"])
                if len(reports) == 2:
                    received.set()
                    worker.stop()
        worker = self.worker(on_state=state)
        result = worker.run(max_seconds=10)
        self.assertTrue(received.is_set(), result)
        self.assertEqual(2, result["sources"])
        self.assertEqual(2, result["deliveries"])
        self.assertTrue(result["stopped"])
        self.assertEqual([], result["errors"])

    @unittest.skipUnless(os.name == "nt", "Windows directory notifications")
    def test_blocked_source_io_does_not_block_result_lane(self):
        worker = self.worker()
        self.source.publish("ready", "ready")
        delivery = self.finish(worker.transfer_source("ready"))
        self.source.publish("slow", "slow")
        entered, release, delivered = threading.Event(), threading.Event(), threading.Event()
        confirm = self.source.confirm
        def blocked_confirm(*args):
            entered.set()
            if not release.wait(5):
                raise RuntimeError("synthetic timeout")
            return confirm(*args)
        def state(event):
            if event["lane"] == "delivery" and event["phase"] == "scanned" and event["deliveries"]:
                delivered.set()
                release.set()
                worker.stop()
        worker.on_state = state
        with patch.object(self.source, "confirm", side_effect=blocked_confirm):
            result = worker.run(max_seconds=8)
        self.assertTrue(delivered.is_set(), result)
        self.assertEqual("received", self.platform.delivery_status(delivery["delivery_id"])["state"])

    @unittest.skipUnless(os.name == "nt", "Windows directory notifications")
    def test_multiroot_waiter_observes_each_directory_and_closes_handles(self):
        with StateWaiter(self.platform.state_root, additional_roots=(self.source.root,)) as waiter:
            waiter.arm()
            self.source.publish("m", "new")
            self.assertTrue(waiter.wait(2))
            waiter.arm()
            self.platform.submit("a", "worker", "direct synthetic")
            self.assertTrue(waiter.wait(2))
        self.assertEqual([], waiter._additional_handles)
        self.assertIsNone(waiter._change_handle)


if __name__ == "__main__":
    unittest.main()
