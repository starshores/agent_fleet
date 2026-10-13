"""Decision and child-work races using disposable synthetic installations only."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import tempfile
import unittest

from masteragent.service import Platform, PlatformError


class CoordinationTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="masteragent-v3-coordination-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.now = 1000.0
        self.platform = Platform(self.root / "state", clock=lambda: self.now)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.package = self.root / "package.json"
        self.package.write_text(json.dumps({
            "schema_version": 1, "platform_api": 1, "package_id": "example", "version": "1.0.0",
            "roles": [{"role_id": role, "display_name": role, "instructions": role,
                       "tools": ["echo"]} for role in ("parent", "child")],
            "knowledge": {}, "tools": {"echo": {"adapter": "echo", "description": "echo"}},
            "workflows": {},
        }), encoding="utf-8")
        self.platform.install("a", self.package, self.workspace)
        self.platform.activate("a")
        self.parent = self.platform.bind("a", "parent")
        self.child = self.platform.bind("a", "child")
        self.task = self.platform.submit("a", "parent", "parent work")

    def assert_code(self, code, callback, *args, **kwargs):
        with self.assertRaises(PlatformError) as error:
            callback(*args, **kwargs)
        self.assertEqual(code, error.exception.code)

    def ask(self, request="question", deadline=None):
        return self.platform.ask_decision(self.parent, self.task["task_id"], "Proceed?", request, deadline)

    def answer(self, decision, answer="answer", choice="approve"):
        return self.platform.answer_decision("a", decision["decision_id"], decision["revision"], choice, answer)

    def request(self, request="child-1", text="child work"):
        return self.platform.request_child(self.parent, self.task["task_id"], "child", text, request)

    def state(self):
        return self.platform.task_for(self.parent, self.task["task_id"])["state"]

    def reactivate(self):
        self.platform.deactivate("a")
        self.platform.activate("a")
        self.parent = self.platform.bind("a", "parent")
        self.child = self.platform.bind("a", "child")

    def test_pending_question_is_idempotent_and_cannot_be_bypassed(self):
        decision = self.ask()
        self.assertEqual(decision, self.ask())
        self.assertEqual("awaiting_decision", self.state())
        self.assert_code("request_conflict", self.platform.ask_decision, self.parent,
                         self.task["task_id"], "Changed?", "question")
        self.assert_code("decision_pending", self.ask, "second-question")
        self.assert_code("parent_not_ready", self.request)
        self.assert_code("task_not_queued", self.platform.run_as, self.parent, self.task["task_id"], "echo")
        self.assertEqual(1, len(self.platform.decisions("a")))

    def test_answer_retries_are_exact_and_revision_conflicts_fail(self):
        decision = self.ask()
        self.assert_code("stale_revision", self.platform.answer_decision,
                         "a", decision["decision_id"], 7, "approve", "answer")
        answered = self.answer(decision)
        self.assertEqual("approved", answered["state"])
        self.assertEqual(answered, self.answer(decision))
        self.assert_code("answer_conflict", self.answer, decision, "answer", "reject")
        self.assert_code("answer_conflict", self.platform.answer_decision,
                         "a", decision["decision_id"], answered["revision"], "approve", "answer")
        self.assert_code("stale_revision", self.answer, decision, "different-answer")
        self.assertEqual("queued", self.state())
        second = self.ask("second-question")
        self.assert_code("answer_conflict", self.answer, second)
        self.assertEqual("awaiting_decision", self.state())

    def test_parallel_answers_commit_one_winner(self):
        decision = self.ask()
        def answer(index):
            try:
                return self.answer(decision, "answer-" + str(index), "approve" if index % 2 else "reject")["state"]
            except PlatformError as error:
                return error.code
        with ThreadPoolExecutor(max_workers=4) as executor:
            values = list(executor.map(answer, range(8)))
        self.assertEqual(1, sum(value in ("approved", "rejected") for value in values))
        self.assertEqual(7, values.count("stale_revision"))
        events = [e for e in self.platform.outbox("a") if e["kind"] == "decision_answered"]
        self.assertEqual(1, len(events))

    def test_deadline_never_autoapproves_and_reads_do_not_expire(self):
        decision = self.ask(deadline=1010)
        self.now = 1011
        before = self.platform.db_path.read_bytes()
        self.assertEqual("pending", self.platform.decisions("a")[0]["state"])
        self.assertEqual("awaiting_decision", self.state())
        self.assertEqual(before, self.platform.db_path.read_bytes())
        self.assert_code("decision_expired", self.answer, decision)
        self.assertEqual(before, self.platform.db_path.read_bytes())
        expired = self.platform.expire_decisions("a")
        self.assertEqual(["expired"], [d["state"] for d in expired])
        self.assertEqual([], self.platform.expire_decisions("a"))
        self.assertEqual("awaiting_decision", self.state())
        replacement = self.ask("replacement", 1020)
        self.assertEqual(["superseded", "pending"], [d["state"] for d in self.platform.decisions("a")])
        self.answer(replacement)
        self.assertEqual("queued", self.state())

    def test_decision_generation_requires_new_question_and_new_answer(self):
        old = self.ask()
        self.reactivate()
        self.assertEqual([], self.platform.decisions("a"))
        self.assert_code("stale_decision", self.answer, old)
        continued = self.platform.resume_task("a", self.task["task_id"], "Continue this task")
        self.assertEqual("awaiting_decision", continued["state"])
        current = self.platform.decisions("a")[0]
        self.assertNotEqual(old["decision_id"], current["decision_id"])
        self.assertEqual(2, current["generation"])
        self.assertEqual("superseded", self.platform.decisions("a", include_history=True)[0]["state"])
        self.assert_code("stale_decision", self.answer, old)
        self.answer(current)
        self.assertEqual("queued", self.state())

    def test_previous_approval_does_not_transfer_to_restored_generation(self):
        old = self.ask()
        self.answer(old, "old-answer")
        self.reactivate()
        continued = self.platform.resume_task("a", self.task["task_id"], "Continue explicitly")
        self.assertEqual("awaiting_decision", continued["state"])
        history = self.platform.decisions("a", include_history=True)
        self.assertEqual(["approved", "pending"], [d["state"] for d in history])
        self.assert_code("stale_decision", self.answer, old, "old-answer")
        self.assert_code("task_not_queued", self.platform.run_as, self.parent, self.task["task_id"], "echo")
        self.assert_code("answer_conflict", self.answer, history[-1], "old-answer")
        self.answer(history[-1], "fresh-answer")
        self.assertEqual("queued", self.state())

    def test_expired_resumed_question_preserves_deadline(self):
        self.ask(deadline=1010)
        self.reactivate()
        self.now = 1020
        resumed = self.platform.resume_task("a", self.task["task_id"], "Restore task")
        self.assertEqual("awaiting_decision", resumed["state"])
        decision = self.platform.decisions("a")[0]
        self.assertEqual("expired", decision["state"])
        self.assertEqual(1010, decision["expires_at"])
        self.assert_code("decision_expired", self.answer, decision)
        self.answer(self.ask("fresh-question", 1030))
        self.assertEqual("queued", self.state())

    def test_child_requests_are_correlated_idempotent_and_wait_for_all(self):
        first = self.request()
        self.assertEqual(first, self.request())
        self.assert_code("request_conflict", self.request, "child-1", "changed text")
        second = self.request("child-2", "second child")
        self.assertEqual("waiting", self.state())
        self.assert_code("task_not_queued", self.platform.run_as, self.parent, self.task["task_id"], "echo")
        result = self.platform.run_as(self.child, second["child_task_id"], "echo")
        replies = self.platform.child_replies(self.parent, self.task["task_id"])
        self.assertEqual([second["request_id"]], [r["request_id"] for r in replies])
        self.assertEqual(result, replies[0]["result"])
        self.assertEqual("waiting", self.state())
        self.platform.run_as(self.child, first["child_task_id"], "echo")
        self.assertEqual("queued", self.state())
        self.assertEqual(2, len(self.platform.child_replies(self.parent, self.task["task_id"])))
        self.assert_code("task_scope", self.platform.child_replies, self.child, self.task["task_id"])

    def test_child_completion_cannot_bypass_decision(self):
        request = self.request()
        decision = self.ask()
        self.platform.run_as(self.child, request["child_task_id"], "echo")
        self.assertEqual("awaiting_decision", self.state())
        self.answer(decision)
        self.assertEqual("queued", self.state())

    def test_parallel_child_request_retries_create_one_child(self):
        with ThreadPoolExecutor(max_workers=4) as executor:
            requests = list(executor.map(lambda _: self.request(), range(8)))
        self.assertEqual(1, len({request["child_task_id"] for request in requests}))
        self.assertEqual(2, len(self.platform.history("a")["tasks"]))
        self.assertEqual(1, len([event for event in self.platform.outbox("a") if event["kind"] == "child_requested"]))

    def test_decision_approval_cannot_bypass_pending_child(self):
        request = self.request()
        self.answer(self.ask())
        self.assertEqual("waiting", self.state())
        self.platform.run_as(self.child, request["child_task_id"], "echo")
        self.assertEqual("queued", self.state())

    def test_rejected_parent_is_not_resurrected_by_child_completion(self):
        request = self.request()
        self.answer(self.ask(), choice="reject")
        self.assertEqual("cancelled", self.state())
        self.platform.run_as(self.child, request["child_task_id"], "echo")
        self.assertEqual("cancelled", self.state())
        self.assertEqual("completed", self.platform.child_replies(self.parent, self.task["task_id"])[0]["child_state"])

    def test_rejected_child_settles_parent_without_fabricated_result(self):
        request = self.request()
        decision = self.platform.ask_decision(self.child, request["child_task_id"], "Proceed?", "child-question")
        self.answer(decision, choice="reject")
        self.assertEqual("queued", self.state())
        reply = self.platform.child_replies(self.parent, self.task["task_id"])[0]
        self.assertEqual("cancelled", reply["child_state"])
        self.assertIsNone(reply["result"])

    def test_resumed_parent_requires_explicit_child_resume(self):
        request = self.request()
        self.reactivate()
        self.platform.resume_task("a", self.task["task_id"], "Restore parent")
        self.assertEqual("waiting", self.state())
        self.assertEqual(request, self.request())
        self.assertEqual(2, len(self.platform.history("a")["tasks"]))
        self.assert_code("request_conflict", self.request, "child-1", "changed after restoration")
        self.assert_code("stale_task", self.platform.run_as, self.child, request["child_task_id"], "echo")
        self.platform.resume_task("a", request["child_task_id"], "Restore child")
        self.platform.run_as(self.child, request["child_task_id"], "echo")
        self.assertEqual("queued", self.state())
        self.assertEqual(1, len(self.platform.child_replies(self.parent, self.task["task_id"])))

    def test_resumed_parent_republishes_completed_child_reference(self):
        request = self.request()
        self.platform.run_as(self.child, request["child_task_id"], "echo")
        self.reactivate()
        self.platform.resume_task("a", self.task["task_id"], "Review existing reply")
        events = [e for e in self.platform.outbox("a") if e["kind"] == "child_replied"]
        self.assertEqual([request["child_task_id"]], [e["payload"]["child_task_id"] for e in events])
        self.assertEqual(1, self.platform.child_replies(self.parent, self.task["task_id"])[0]["result"]["generation"])

    def test_ack_and_readonly_queries_preserve_result_and_child_reply(self):
        request = self.request()
        self.platform.run_as(self.child, request["child_task_id"], "echo")
        events = self.platform.outbox("a")
        for event in events:
            acknowledged = self.platform.ack_outbox("a", event["event_id"])
            self.assertEqual(acknowledged, self.platform.ack_outbox("a", event["event_id"]))
        self.assertEqual([], self.platform.outbox("a"))
        self.assertEqual(len(events), len(self.platform.outbox("a", include_delivered=True)))
        before = self.platform.db_path.read_bytes()
        readonly = Platform(self.root / "state", readonly=True, clock=lambda: self.now)
        self.assertEqual(1, len(readonly.results("a")))
        self.assertEqual(1, len(readonly.child_replies(self.parent, self.task["task_id"])))
        self.assertEqual([], readonly.decisions("a"))
        self.assert_code("read_only", readonly.ack_outbox, "a", events[0]["event_id"])
        self.assertEqual(before, self.platform.db_path.read_bytes())

    def test_outbox_and_decisions_cannot_cross_installations_or_generations(self):
        old = self.ask()
        event = self.platform.outbox("a")[0]
        workspace_b = self.root / "workspace-b"
        workspace_b.mkdir()
        self.platform.install("b", self.package, workspace_b)
        self.platform.deactivate("a")
        self.platform.activate("b")
        self.assertEqual([], self.platform.outbox("b"))
        self.assertEqual([], self.platform.decisions("b"))
        self.assert_code("event_scope", self.platform.ack_outbox, "b", event["event_id"])
        self.assert_code("decision_scope", self.platform.answer_decision,
                         "b", old["decision_id"], old["revision"], "approve", "answer")
        self.platform.deactivate("b")
        self.platform.activate("a")
        self.assertEqual([], self.platform.outbox("a"))
        self.assert_code("stale_event", self.platform.ack_outbox, "a", event["event_id"])


if __name__ == "__main__":
    unittest.main()
