"""Shared workbench writes against disposable business packages and SQLite state."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing, contextmanager, redirect_stderr, redirect_stdout
from io import StringIO
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch

from masteragent.cli import main
from masteragent.errors import PlatformError
from masteragent.service import Platform
from masteragent.workbench_commands import WorkbenchCommands, decode_command_payload, MAX_COMMAND_BYTES


class WorkbenchCommandTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="ma31-commands-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.now = 1000.0
        self.platform = Platform(self.root / "state", clock=lambda: self.now, external_adapters={"model_text"})
        for name in ("a", "b"):
            workspace = self.root / (name + "-workspace")
            workspace.mkdir()
            package = self.root / (name + ".json")
            package.write_text(json.dumps({
                "schema_version": 1, "platform_api": 1, "package_id": "business-" + name, "version": "1.0.0",
                "roles": [{"role_id": "worker", "display_name": "Worker " + name,
                           "instructions": "Synthetic " + name, "tools": ["local", "think"]}],
                "knowledge": {}, "tools": {"local": {"adapter": "echo", "description": "Synthetic local"},
                                           "think": {"adapter": "model_text", "description": "Synthetic external"}},
                "workflows": {},
            }), encoding="utf-8")
            self.platform.install(name, package, workspace)
        self.platform.activate("a")
        self.actor = self.platform.bind("a", "worker")
        self.commands = self.handle()

    def handle(self):
        result = WorkbenchCommands(self.root / "state")
        result._platform.clock = lambda: self.now
        return result

    def assert_code(self, code, callback, *args, **kwargs):
        with self.assertRaises(PlatformError) as caught:
            callback(*args, **kwargs)
        self.assertEqual(code, caught.exception.code)

    def execute(self, action, payload, installation="a"):
        return self.commands.execute(installation, action, payload)["data"]["result"]

    def submit(self, request="submit-1", text="Synthetic task"):
        return {"generation": 1, "role_id": "worker", "text": text, "request_id": request}

    def running(self, request="running-1", started=True, supports=True):
        task = self.platform.submit("a", "worker", "Synthetic running", request)
        attempt = self.platform.claim(self.actor, task["task_id"], "think", 60)
        if started:
            self.platform.record_runtime_event(self.actor, attempt["attempt_id"], attempt["lease_token"],
                "runtime_start_requested", {"runtime": "synthetic", "supports_runtime_inputs": supports})
        return task, attempt

    def input_payload(self, task, attempt, request="input-1"):
        return {"generation": 1, "task_id": task["task_id"], "attempt_id": attempt["attempt_id"],
                "text": "Synthetic correction", "request_id": request}

    def stop_payload(self, task, attempt):
        return {"generation": 1, "task_id": task["task_id"], "attempt_id": attempt["attempt_id"], "reason": "Synthetic operator request"}

    def decision(self, expires=None):
        task = self.platform.submit("a", "worker", "Synthetic decision")
        decision = self.platform.ask_decision(self.actor, task["task_id"], "Continue synthetic task?", task["task_id"], expires)
        payload = {"generation": 1, "task_id": task["task_id"], "decision_id": decision["decision_id"],
                   "expected_revision": decision["revision"], "choice": "approve", "answer_id": "answer-" + decision["decision_id"]}
        return task, decision, payload

    def snapshot(self):
        with closing(sqlite3.connect(self.platform.db_path)) as db:
            return "\n".join(db.iterdump())

    def test_missing_root_is_never_initialized_even_by_valid_command(self):
        missing = self.root / "never-created"
        commands = WorkbenchCommands(missing)
        self.assert_code("not_initialized", commands.execute, "a", "submit", self.submit())
        self.assertFalse(missing.exists())

    def test_submit_is_idempotent_conflicts_and_returns_only_public_receipt(self):
        payload = self.submit()
        envelope = self.commands.execute("a", "submit", payload)
        self.assertEqual("3.1-dev1", envelope["api_version"])
        self.assertEqual({"kind": "masteragent", "state_root": str(self.root / "state"),
                          "installation_id": "a", "generation": 1}, envelope["source"])
        first = envelope["data"]["result"]
        self.assertEqual("queued", first["state"])
        self.assertEqual(first, self.execute("submit", payload))
        self.assert_code("request_conflict", self.execute, "submit", {**payload, "text": "Changed"})
        self.assertNotIn("text", first)
        self.assertNotIn("client_request_id", first)
        self.assertNotIn(self.actor.token, json.dumps(envelope))

    def test_parallel_duplicate_submission_creates_one_task(self):
        handles = [self.handle() for _ in range(6)]
        with ThreadPoolExecutor(max_workers=6) as pool:
            receipts = list(pool.map(lambda handle: handle.execute("a", "submit", self.submit()), handles))
        self.assertEqual(1, len({receipt["data"]["result"]["task_id"] for receipt in receipts}))
        with closing(sqlite3.connect(self.platform.db_path)) as db:
            self.assertEqual(1, db.execute("SELECT COUNT(*) FROM tasks").fetchone()[0])

    def test_exact_payload_and_utf8_limits_reject_without_writing(self):
        before = self.snapshot()
        payload = self.submit()
        for invalid in (None, [], {}, {**payload, "extra": 1}, {key: value for key, value in payload.items() if key != "request_id"}):
            self.assert_code("invalid_payload", self.execute, "submit", invalid)
        for generation in (None, True, False, 0, -1, 1.0, "1", 2**63):
            self.assert_code("invalid_generation", self.execute, "submit", {**payload, "generation": generation})
        for text in (None, True, [], "", "  ", "\ud800", "中" * 21334, "a" * 64001):
            self.assert_code("invalid_payload", self.execute, "submit", {**payload, "text": text})
        for request in (None, True, "", "r" * 201, "\ud800"):
            self.assert_code("invalid_payload", self.execute, "submit", {**payload, "request_id": request})
        for action in (None, [], "unknown", "Submit"):
            self.assert_code("invalid_action", self.execute, action, payload)
        for identifier in (None, True, "../a", "a\n", "a" * 65):
            self.assert_code("invalid_id", self.commands.execute, identifier, "submit", payload)
        self.assertEqual(before, self.snapshot())
        self.assertEqual("queued", self.execute("submit", {**payload, "text": "中" * 21333 + "a"})["state"])

    def test_json_decoder_rejects_ambiguous_or_oversized_payload(self):
        for raw in (b'[]', b'null', b'{"generation":1,"generation":2}', b'{"generation":NaN}',
                    b'{"generation":Infinity}', b'\xff', b'{} trailing', b' ' * (MAX_COMMAND_BYTES + 1)):
            self.assert_code("invalid_payload", decode_command_payload, raw)
        self.assertEqual(self.submit(), decode_command_payload(json.dumps(self.submit()).encode("utf-8")))

    def test_generation_and_active_installation_are_fenced(self):
        old = self.execute("submit", self.submit())
        self.platform.deactivate("a")
        self.platform.activate("b")
        self.assert_code("installation_inactive", self.execute, "submit", self.submit("inactive"))
        self.platform.deactivate("b")
        self.platform.activate("a")
        before = self.snapshot()
        self.assert_code("stale_generation", self.execute, "submit", self.submit("stale"))
        self.assert_code("stale_task", self.execute, "submit", {**self.submit(), "generation": 2})
        self.assertEqual(before, self.snapshot())
        self.assertEqual(1, old["dispatch_generation"])
        fresh = self.execute("submit", {**self.submit("fresh"), "generation": 2})
        self.assertEqual(2, fresh["dispatch_generation"])

    def test_activation_change_before_write_transaction_cannot_cross_scope(self):
        entered, proceed = threading.Event(), threading.Event()
        original = self.commands._platform._db

        @contextmanager
        def paused_db(write=False):
            if write:
                entered.set()
                if not proceed.wait(5):
                    raise AssertionError("test did not release command")
            with original(write=write) as db:
                yield db

        with patch.object(self.commands._platform, "_db", paused_db), ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(self.execute, "submit", self.submit())
            self.assertTrue(entered.wait(5))
            self.platform.deactivate("a")
            self.platform.activate("a")
            proceed.set()
            self.assert_code("stale_generation", future.result)
        with closing(sqlite3.connect(self.platform.db_path)) as db:
            self.assertEqual(0, db.execute("SELECT COUNT(*) FROM tasks").fetchone()[0])

    def test_input_is_durable_idempotent_and_task_scoped(self):
        task, attempt = self.running()
        other = self.platform.submit("a", "worker", "Other synthetic task")
        payload = self.input_payload(task, attempt)
        first = self.execute("input", payload)
        self.assertEqual("pending", first["state"])
        self.assertEqual(first, self.execute("input", payload))
        self.assert_code("request_conflict", self.execute, "input", {**payload, "text": "Changed"})
        self.assert_code("task_scope", self.execute, "input", {**payload, "task_id": other["task_id"]})
        self.assert_code("stale_generation", self.execute, "input", {**payload, "generation": 2})
        self.assertEqual(1, len(self.platform.runtime_inputs("a")))
        self.assertNotIn(attempt["lease_token"], json.dumps(first))
        self.assertNotIn("owner_hash", first)

    def test_input_requires_runtime_support_and_live_lease(self):
        task, attempt = self.running(supports=False)
        self.assert_code("runtime_input_unsupported", self.execute, "input", self.input_payload(task, attempt))
        self.now += 61
        self.assert_code("lease_expired", self.execute, "input", self.input_payload(task, attempt))
        self.assertEqual([], self.platform.runtime_inputs("a"))

    def test_stop_is_a_durable_request_and_blocks_new_input(self):
        task, attempt = self.running()
        pending = self.input_payload(task, attempt)
        first_input = self.execute("input", pending)
        payload = self.stop_payload(task, attempt)
        result = self.execute("stop", payload)
        self.assertEqual({"attempt_id": attempt["attempt_id"], "stop_requested": True, "termination_confirmed": False}, result)
        self.assertEqual(result, self.execute("stop", payload))
        self.assert_code("cancel_conflict", self.execute, "stop", {**payload, "reason": "Other reason"})
        self.assert_code("runtime_stopping", self.execute, "input", {**pending, "request_id": "input-2"})
        self.assertEqual(first_input, self.execute("input", pending))
        self.assertEqual("running", self.platform.task_for(self.actor, task["task_id"])["state"])
        events = self.platform.attempts("a")[0]["events"]
        self.assertEqual(1, sum(event["kind"] == "runtime_cancel_requested" for event in events))

    def test_stop_checks_task_runtime_and_expiration(self):
        task, attempt = self.running(started=False)
        payload = self.stop_payload(task, attempt)
        self.assert_code("runtime_not_started", self.execute, "stop", payload)
        self.assert_code("task_scope", self.execute, "stop", {**payload, "task_id": "other-task"})
        self.platform.record_runtime_event(self.actor, attempt["attempt_id"], attempt["lease_token"],
            "runtime_start_requested", {"runtime": "synthetic", "supports_runtime_inputs": True})
        self.now += 61
        self.assert_code("lease_expired", self.execute, "stop", payload)
        self.platform.expire_attempts("a")
        self.assert_code("stale_attempt", self.execute, "stop", payload)

    def test_runtime_exit_rejects_new_stop_and_input_without_claiming_termination(self):
        task, attempt = self.running()
        self.platform.record_runtime_event(self.actor, attempt["attempt_id"], attempt["lease_token"],
            "runtime_stopped", {"outcome": "synthetic", "termination_confirmed": True})
        self.assert_code("runtime_stopped", self.execute, "stop", self.stop_payload(task, attempt))
        self.assert_code("runtime_stopping", self.execute, "input", self.input_payload(task, attempt))

    def test_stop_retry_after_exit_and_completion_preserves_original_receipt(self):
        task, attempt = self.running()
        payload = self.stop_payload(task, attempt)
        receipt = self.execute("stop", payload)
        self.platform.record_runtime_event(self.actor, attempt["attempt_id"], attempt["lease_token"],
            "runtime_stopped", {"outcome": "synthetic", "termination_confirmed": True})
        after_exit = self.snapshot()
        self.assertEqual(receipt, self.execute("stop", payload))
        self.assert_code("cancel_conflict", self.execute, "stop", {**payload, "reason": "Another reason"})
        self.assertEqual(after_exit, self.snapshot())
        self.platform.complete(self.actor, attempt["attempt_id"], attempt["lease_token"], "Synthetic result")
        after_completion = self.snapshot()
        self.assertEqual(receipt, self.execute("stop", payload))
        self.assertFalse(receipt["termination_confirmed"])
        self.assert_code("cancel_conflict", self.execute, "stop", {**payload, "reason": "Another reason"})
        self.assertEqual(after_completion, self.snapshot())
        # Keep the pre-existing API's closed-attempt behavior unchanged.
        self.assert_code("stale_attempt", self.platform.request_runtime_stop, "a", attempt["attempt_id"], payload["reason"])

    def test_stop_retry_after_expiry_is_idempotent_but_cannot_cross_generation(self):
        task, attempt = self.running()
        payload = self.stop_payload(task, attempt)
        receipt = self.execute("stop", payload)
        self.now += 61
        before_maintenance = self.snapshot()
        self.assertEqual(receipt, self.execute("stop", payload))
        self.assert_code("cancel_conflict", self.execute, "stop", {**payload, "reason": "Another reason"})
        self.assertEqual(before_maintenance, self.snapshot())
        self.platform.expire_attempts("a")
        after_expiry = self.snapshot()
        self.assertEqual(receipt, self.execute("stop", payload))
        self.assertEqual(after_expiry, self.snapshot())
        self.platform.reconcile_task("a", task["task_id"], "retry", "Synthetic continuation", safe_to_retry=True)
        self.platform.deactivate("a")
        self.platform.activate("a")
        self.assert_code("stale_generation", self.execute, "stop", payload)
        self.assert_code("stale_task", self.execute, "stop", {**payload, "generation": 2})
        self.platform.resume_task("a", task["task_id"], "Synthetic continuation")
        after_resume = self.snapshot()
        self.assert_code("stale_attempt", self.execute, "stop", {**payload, "generation": 2})
        self.assertEqual(after_resume, self.snapshot())

    def test_answers_are_revision_guarded_idempotent_and_task_scoped(self):
        task, decision, payload = self.decision()
        self.assert_code("task_scope", self.execute, "answer", {**payload, "task_id": "other-task"})
        self.assert_code("stale_generation", self.execute, "answer", {**payload, "generation": 2})
        self.assert_code("stale_revision", self.execute, "answer", {**payload, "expected_revision": 9})
        for revision in (True, None, "0", -1, 2**63):
            self.assert_code("invalid_revision", self.execute, "answer", {**payload, "expected_revision": revision})
        for choice in (None, [], True, "yes"):
            self.assert_code("invalid_answer", self.execute, "answer", {**payload, "choice": choice})
        result = self.execute("answer", payload)
        self.assertEqual("approved", result["state"])
        self.assertEqual(result, self.execute("answer", payload))
        self.assert_code("answer_conflict", self.execute, "answer", {**payload, "choice": "reject"})
        self.assertEqual("queued", self.platform.task_for(self.actor, task["task_id"])["state"])

    def test_expired_and_old_generation_decisions_cannot_be_answered(self):
        task, decision, payload = self.decision(expires=1001)
        self.now += 2
        self.assert_code("decision_expired", self.execute, "answer", payload)
        self.platform.deactivate("a")
        self.platform.activate("a")
        self.assert_code("stale_generation", self.execute, "answer", payload)
        self.assert_code("stale_task", self.execute, "answer", {**payload, "generation": 2})

    def test_foreign_installation_attempt_and_decision_never_cross_scope(self):
        task, decision, answer = self.decision()
        other_task, attempt = self.running()
        self.now += 61
        self.platform.expire_attempts("a")
        self.platform.reconcile_task("a", other_task["task_id"], "cancel", "Synthetic switch")
        self.platform.deactivate("a")
        self.platform.activate("b")
        self.assert_code("decision_scope", self.execute, "answer", answer, "b")
        self.assert_code("attempt_scope", self.execute, "input", self.input_payload(other_task, attempt), "b")
        self.assert_code("attempt_scope", self.execute, "stop", self.stop_payload(other_task, attempt), "b")

    def test_optional_domain_guards_keep_old_callers_compatible_and_reject_bool(self):
        task = self.platform.submit("a", "worker", "Old API", "old-api")
        self.assertEqual(task, self.platform.submit("a", "worker", "Old API", "old-api"))
        self.assert_code("invalid_generation", self.platform.submit, "a", "worker", "Guarded", expected_generation=True)
        running, attempt = self.running()
        self.assert_code("invalid_generation", self.platform.submit_runtime_input, "a", attempt["attempt_id"], "x", "x", expected_generation=True)
        self.assert_code("invalid_generation", self.platform.request_runtime_stop, "a", attempt["attempt_id"], "x", expected_generation=True)

    def test_cli_uses_same_payload_and_refuses_runtime_configuration(self):
        path = self.root / "command.json"
        path.write_text(json.dumps(self.submit()), encoding="utf-8")
        args = ["--state-root", str(self.root / "state"), "workbench-command", "--installation-id", "a",
                "--action", "submit", "--payload-file", str(path)]
        output, errors = StringIO(), StringIO()
        with redirect_stdout(output), redirect_stderr(errors):
            self.assertEqual(0, main(args))
        result = json.loads(output.getvalue())
        self.assertEqual(self.execute("submit", self.submit()), result["data"]["result"])
        with redirect_stderr(errors):
            self.assertEqual(2, main(["--runtime-config", "not-read.json", *args]))
        self.assertEqual("invalid_command", json.loads(errors.getvalue())["error"]["code"])


if __name__ == "__main__":
    unittest.main()
