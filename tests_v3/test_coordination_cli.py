"""End-to-end JSON CLI coordination using synthetic temporary deployments."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class CoordinationCliTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="masteragent-v3-coordination-cli-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.state = self.root / "state"
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONUTF8": "1"}
        package = self.document("package", {
            "schema_version": 1, "platform_api": 1, "package_id": "synthetic", "version": "1.0.0",
            "roles": [{"role_id": role, "display_name": role, "instructions": "Synthetic CLI task", "tools": ["echo"]}
                      for role in ("coordinator", "worker")],
            "knowledge": {}, "tools": {"echo": {"adapter": "echo", "description": "Synthetic echo"}},
            "workflows": {},
        })
        self.cli("install", "a", "--package", package, "--workspace", self.workspace)
        self.cli("activate", "a")
        self.actor = self.document("actor", self.cli("register", "a", "coordinator"), bom=True)
        self.worker = self.document("worker", self.cli("register", "a", "worker"))

    def document(self, name, payload, bom=False):
        path = self.root / (name + ".json")
        path.write_text(json.dumps(payload), encoding="utf-8-sig" if bom else "utf-8")
        return path

    def cli(self, *args, error=None):
        process = subprocess.run(
            [sys.executable, "-B", "-m", "masteragent", "--state-root", str(self.state), *map(str, args)],
            cwd=ROOT, env=self.env, capture_output=True, text=True, encoding="utf-8", timeout=30,
        )
        self.assertEqual(2 if error else 0, process.returncode,
                         f"CLI {args!r}: stdout={process.stdout!r}; stderr={process.stderr!r}")
        self.assertEqual("", process.stdout if error else process.stderr)
        data = json.loads(process.stderr if error else process.stdout)
        if error:
            self.assertEqual(error, data["error"]["code"])
        return data

    def submit(self, text="Synthetic parent"):
        return self.cli("submit", "a", "coordinator", "--text", text)["task_id"]

    def claim(self, task_id, name="lease", actor=None, ttl=60):
        issued = self.cli("claim", task_id, "--actor-file", actor or self.actor, "--tool", "echo", "--ttl", ttl)
        return issued, self.document(name, issued, bom=True)

    def complete(self, attempt, lease, output="Completed", actor=None, error=None):
        return self.cli("complete", attempt["attempt_id"], "--actor-file", actor or self.actor,
                        "--lease-file", lease, "--output", output, error=error)

    def test_bom_actor_decision_gate_lease_completion_and_outbox_ack(self):
        task_id = self.submit()
        decision = self.cli("ask-decision", task_id, "--actor-file", self.actor,
                            "--title", "Approve synthetic operation", "--request-id", "question-1")
        self.assertEqual("pending", decision["state"])
        self.cli("claim", task_id, "--actor-file", self.actor, "--tool", "echo", error="task_not_queued")
        answer = self.cli("answer-decision", "a", decision["decision_id"], "--revision", 0,
                          "--choice", "approve", "--answer-id", "answer-1")
        self.assertEqual("approved", answer["state"])
        self.assertEqual(answer, self.cli("answer-decision", "a", decision["decision_id"], "--revision", 0,
                                         "--choice", "approve", "--answer-id", "answer-1"))
        attempt, lease = self.claim(task_id)
        renewed = self.cli("renew", attempt["attempt_id"], "--actor-file", self.actor, "--lease-file", lease, "--ttl", 120)
        self.assertGreater(renewed["expires_at"], attempt["expires_at"])
        self.assertNotIn("lease_token", renewed)
        result = self.complete(attempt, lease)
        self.assertEqual(result, self.complete(attempt, lease))
        self.complete(attempt, lease, output="Conflict", error="completion_conflict")
        self.assertEqual([result], self.cli("results", "a"))
        notices = self.cli("notifications", "a")
        self.assertEqual(1, len(notices))
        events = self.cli("outbox", "a")
        terminal = [event for event in events if event["kind"] == "task_completed"]
        self.assertEqual(1, len(terminal))
        acknowledged = self.cli("ack-outbox", "a", terminal[0]["event_id"])
        self.assertEqual("delivered", acknowledged["state"])
        self.assertEqual(acknowledged, self.cli("ack-outbox", "a", terminal[0]["event_id"]))
        self.assertNotIn(terminal[0]["event_id"], [event["event_id"] for event in self.cli("outbox", "a")])
        self.assertEqual(len(events), len(self.cli("outbox", "a", "--include-delivered")))

    def test_invalid_actor_and_lease_documents_fail_without_finishing_task(self):
        task_id = self.submit()
        wrong_object = self.document("array-actor", [])
        self.cli("claim", task_id, "--actor-file", wrong_object, "--tool", "echo", error="invalid_document")
        bool_generation = json.loads(self.actor.read_text(encoding="utf-8-sig"))
        bool_generation["generation"] = True
        self.cli("claim", task_id, "--actor-file", self.document("bool-actor", bool_generation), "--tool", "echo", error="invalid_actor")
        duplicate = self.root / "duplicate-actor.json"
        duplicate.write_text('{"token":"x","token":"y"}', encoding="utf-8")
        self.cli("claim", task_id, "--actor-file", duplicate, "--tool", "echo", error="invalid_document")
        malformed = self.root / "malformed-actor.json"
        malformed.write_text('{"token":', encoding="utf-8")
        self.cli("claim", task_id, "--actor-file", malformed, "--tool", "echo", error="invalid_document")
        attempt, lease = self.claim(task_id)
        self.complete(attempt, self.document("array-lease", []), error="invalid_document")
        self.complete(attempt, self.document("missing-lease", {"attempt_id": attempt["attempt_id"]}), error="invalid_lease")
        self.complete(attempt, self.document("wrong-id", {**attempt, "attempt_id": "not-this-attempt"}), error="invalid_lease")
        self.complete(attempt, self.document("wrong-secret", {**attempt, "lease_token": "forged"}), error="lease_denied")
        self.complete(attempt, lease, actor=self.worker, error="attempt_scope")
        self.assertEqual([], self.cli("results", "a"))
        self.assertEqual("running", self.cli("attempts", "a", "--task-id", task_id)[0]["state"])
        self.assertEqual("ok", self.complete(attempt, lease)["status"])

    def test_parent_child_wait_and_error_reply_are_correlated_once(self):
        parent_id = self.submit()
        request = self.cli("request-child", parent_id, "--actor-file", self.actor, "--to-role", "worker",
                           "--text", "Synthetic child", "--request-id", "child-1")
        self.assertEqual(request, self.cli("request-child", parent_id, "--actor-file", self.actor, "--to-role", "worker",
                                           "--text", "Synthetic child", "--request-id", "child-1"))
        self.assertEqual([], self.cli("child-replies", parent_id, "--actor-file", self.actor))
        self.cli("claim", parent_id, "--actor-file", self.actor, "--tool", "echo", error="task_not_queued")
        child, lease = self.claim(request["child_task_id"], actor=self.worker)
        failed = self.cli("complete", child["attempt_id"], "--actor-file", self.worker, "--lease-file", lease,
                          "--error", "Synthetic child failure")
        self.assertEqual("error", failed["status"])
        self.assertEqual("worker_failed", failed["error"]["code"])
        replies = self.cli("child-replies", parent_id, "--actor-file", self.actor)
        self.assertEqual(1, len(replies))
        self.assertEqual(request["request_id"], replies[0]["request_id"])
        self.assertEqual(failed, replies[0]["result"])
        parent, parent_lease = self.claim(parent_id, name="parent-lease")
        self.complete(parent, parent_lease, output="Handled child failure")
        history = self.cli("history", "a")
        self.assertEqual(2, len(history["tasks"]))
        self.assertEqual(2, len(history["results"]))
        self.assertEqual(1, sum(event["kind"] == "child_replied" for event in self.cli("outbox", "a")))

    def test_expired_lease_requires_safe_retry_and_restored_task_explicit_continuation(self):
        task_id = self.submit()
        old, old_lease = self.claim(task_id, name="old-lease", ttl=0.000001)
        # A new interpreter roundtrip is longer than the microsecond lease;
        # no long sleep or production clock manipulation is needed.
        self.complete(old, old_lease, error="lease_expired")
        self.assertEqual("needs_reconcile", self.cli("expire-attempts", "a")[0]["state"])
        self.cli("deactivate", "a", error="unfinished_work")
        self.cli("reconcile", "a", task_id, "--resolution", "retry", "--reason", "Effects checked", error="unsafe_retry")
        retry = self.cli("reconcile", "a", task_id, "--resolution", "retry", "--reason", "Effects checked", "--safe-to-retry")
        self.assertEqual("queued", retry["state"])
        current, current_lease = self.claim(task_id, name="current-lease")
        self.complete(old, old_lease, error="stale_attempt")
        self.assertEqual(2, current["ordinal"])
        self.complete(current, current_lease)
        retained_id = self.submit("Retained across activation")
        self.cli("deactivate", "a")
        self.cli("activate", "a")
        self.actor = self.document("new-actor", self.cli("register", "a", "coordinator"))
        self.cli("claim", retained_id, "--actor-file", self.actor, "--tool", "echo", error="stale_task")
        resumed = self.cli("resume-task", "a", retained_id, "--reason", "Resume retained work explicitly")
        self.assertEqual((1, 2), (resumed["generation"], resumed["dispatch_generation"]))
        continued, continued_lease = self.claim(retained_id, name="continued-lease")
        self.assertEqual(2, self.complete(continued, continued_lease)["generation"])
        history = self.cli("history", "a")
        self.assertEqual(1, len(history["continuations"]))
        self.assertEqual(2, len(history["results"]))
        self.assertEqual(1, len(self.cli("results", "a")))


if __name__ == "__main__":
    unittest.main()
