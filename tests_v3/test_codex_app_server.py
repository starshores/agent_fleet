"""Pure JSON-RPC state tests with no subprocess, model, home or credentials."""
import unittest

from masteragent.codex_app_server import AppServerProtocol, ProtocolError, _decode


def event(name, turn="turn-a", thread="thread-a", **extra):
    return {"method": name, "params": {"threadId": thread, "turnId": turn, **extra}}


def completed(status="completed", turn="turn-a", thread="thread-a", **extra):
    return {"method": "turn/completed", "params": {"threadId": thread,
            "turn": {"id": turn, "status": status, **extra}}}


def message(text="synthetic final", turn="turn-a", thread="thread-a", phase="final_answer"):
    return event("item/completed", turn=turn, thread=thread,
                 item={"id": "item-a", "type": "agentMessage", "phase": phase, "text": text})


class AppServerProtocolTests(unittest.TestCase):
    def setUp(self):
        self.sent, self.events, self.acks = [], [], []
        self.protocol = AppServerProtocol(self.sent.append,
            lambda kind, details: self.events.append((kind, details)), "synthetic-cwd", "synthetic prompt",
            lambda identifier, state: self.acks.append((identifier, state)))

    def reply(self, result, identifier=None):
        self.protocol.accept({"id": identifier or self.sent[-1]["id"], "result": result})

    def start(self, bind=True):
        self.protocol.start()
        self.assertEqual("initialize", self.sent[-1]["method"])
        self.reply({})
        self.assertEqual("initialized", self.sent[-2]["method"])
        self.assertEqual("thread/start", self.sent[-1]["method"])
        self.reply({"thread": {"id": "thread-a"}})
        self.assertEqual("turn/start", self.sent[-1]["method"])
        if bind:
            self.reply({"turn": {"id": "turn-a", "status": "inProgress"}})

    def test_exact_start_and_final_are_distinct_from_process_lifecycle(self):
        self.start()
        self.protocol.accept(message())
        self.protocol.accept(completed())
        self.assertEqual("succeeded", self.protocol.terminal)
        self.assertEqual("synthetic final", self.protocol.output)
        self.assertEqual("thread-a", self.protocol.thread_id)
        self.assertEqual("turn-a", self.protocol.turn_id)
        self.assertNotIn("synthetic final", str(self.events))
        self.assertNotIn("synthetic prompt", str(self.events))

    def test_completion_before_rpc_response_is_buffered_until_owned_id(self):
        self.start(bind=False)
        turn_request = self.sent[-1]["id"]
        self.protocol.accept(message(turn="unrelated"))
        self.protocol.accept(completed(turn="unrelated"))
        self.protocol.accept(message())
        self.protocol.accept(completed())
        self.assertIsNone(self.protocol.terminal)
        self.reply({"turn": {"id": "turn-a", "status": "inProgress"}}, turn_request)
        self.assertEqual("succeeded", self.protocol.terminal)

    def test_partial_answer_then_final_answer_completes_without_auditing_text(self):
        self.start()
        self.protocol.accept(message("synthetic partial", phase="partial_answer"))
        self.assertIsNone(self.protocol.output)
        self.assertIsNone(self.protocol.terminal)
        self.protocol.accept(message("synthetic final"))
        self.protocol.accept(completed())
        self.assertEqual("synthetic final", self.protocol.output)
        self.assertEqual("succeeded", self.protocol.terminal)
        self.assertNotIn("synthetic partial", str(self.events))

    def test_partial_answer_without_final_cannot_publish_success(self):
        self.start()
        self.protocol.accept(message("synthetic partial", phase="partial_answer"))
        with self.assertRaisesRegex(ProtocolError, "missing final response"):
            self.protocol.accept(completed())
        self.assertIsNone(self.protocol.output)
        self.assertIsNone(self.protocol.terminal)

    def test_partial_and_commentary_after_final_do_not_replace_result(self):
        self.start()
        self.protocol.accept(message("synthetic final"))
        for phase in ("partial_answer", "commentary"):
            self.protocol.accept(message("synthetic nonfinal " + phase, phase=phase))
            self.assertEqual("synthetic final", self.protocol.output)
        self.protocol.accept(completed())
        self.assertEqual("synthetic final", self.protocol.output)
        self.assertEqual("succeeded", self.protocol.terminal)

    def test_unrelated_turn_thread_and_thread_progress_do_not_complete(self):
        self.start()
        for notification in (message(thread="other"), completed(thread="other"),
                             message(turn="other"), completed(turn="other"),
                             {"method": "thread/status/changed", "params": {"threadId": "thread-a", "status": "idle"}}):
            self.protocol.accept(notification)
        self.assertIsNone(self.protocol.terminal)
        self.assertIsNone(self.protocol.output)

    def test_failed_interrupted_unknown_error_and_missing_output(self):
        for status, outcome in (("failed", "failed"), ("interrupted", "interrupted")):
            with self.subTest(status=status):
                self.setUp()
                self.start()
                self.protocol.accept(completed(status))
                self.assertEqual(outcome, self.protocol.terminal)
        for notification in (completed(), completed("futureStatus"), completed(error={"message": "private failure"})):
            with self.subTest(notification=notification):
                self.setUp()
                self.start()
                with self.assertRaises(ProtocolError):
                    self.protocol.accept(notification)

    def test_conflicting_terminal_and_late_message_are_rejected(self):
        self.start()
        self.protocol.accept(message())
        self.protocol.accept(completed())
        self.protocol.accept(completed())  # Identical duplicate is harmless.
        with self.assertRaises(ProtocolError):
            self.protocol.accept(completed("failed"))
        with self.assertRaises(ProtocolError):
            self.protocol.accept(message("late text"))

    def test_contradictory_scope_fields_cannot_be_a_turn_receipt(self):
        self.start()
        notification = completed()
        notification["params"]["turnId"] = "other-turn"
        with self.assertRaises(ProtocolError):
            self.protocol.accept(notification)
        self.assertIsNone(self.protocol.terminal)
        self.setUp()
        self.start(bind=False)
        with self.assertRaises(ProtocolError):
            self.reply({"threadId": "other-thread", "turn": {"id": "turn-a", "status": "inProgress"}})

    def test_steer_requires_same_turn_receipt_and_never_restarts_turn(self):
        self.start()
        self.protocol.steer({"input_id": "input-a", "text": "synthetic correction"})
        request = self.sent[-1]
        self.assertEqual("turn/steer", request["method"])
        self.assertEqual("turn-a", request["params"]["expectedTurnId"])
        self.assertEqual("thread-a", request["params"]["threadId"])
        self.assertEqual([], self.acks)
        self.reply({"turnId": "turn-a"})
        self.assertEqual([("input-a", "accepted")], self.acks)
        self.assertEqual(1, sum(item.get("method") == "turn/start" for item in self.sent))
        self.assertNotIn("synthetic correction", str(self.events))

    def test_accepted_control_identifier_cannot_be_sent_twice(self):
        self.start()
        control = {"input_id": "input-a", "text": "synthetic correction"}
        self.protocol.steer(control)
        self.reply({"turnId": "turn-a"})
        with self.assertRaises(ProtocolError):
            self.protocol.steer(control)
        self.assertEqual(1, sum(item.get("method") == "turn/steer" for item in self.sent))
        self.protocol.reconcile_controls()
        self.assertEqual([("input-a", "accepted")], self.acks)

    def test_stale_steer_response_and_terminal_race_hold_input(self):
        for race in (False, True):
            with self.subTest(race=race):
                self.setUp()
                self.start()
                self.protocol.steer({"input_id": "input-a", "text": "correction"})
                with self.assertRaises(ProtocolError):
                    if race:
                        self.protocol.accept(message())
                        self.protocol.accept(completed())
                    else:
                        self.reply({"turnId": "other"})
                self.protocol.reconcile_controls()
                self.assertEqual([("input-a", "needs_reconcile")], self.acks)

    def test_native_requests_are_typed_denials_without_payload_audit(self):
        self.start()
        cases = {"item/commandExecution/requestApproval": {"decision": "decline"},
                 "item/fileChange/requestApproval": {"decision": "decline"},
                 "item/permissions/requestApproval": {"permissions": {}, "scope": "turn"},
                 "mcpServer/elicitation/request": {"action": "decline"},
                 "item/tool/requestUserInput": {"answers": {}},
                 "item/tool/call": {"success": False, "contentItems": []}}
        for identifier, (method, expected) in enumerate(cases.items()):
            request = event(method, command="private command", arguments="private arguments")
            request["id"] = identifier
            self.protocol.accept(request)
            self.assertEqual({"id": identifier, "result": expected}, self.sent[-1])
        self.assertNotIn("private command", str(self.events))
        self.assertNotIn("private arguments", str(self.events))

    def test_server_request_id_does_not_alias_client_response_id(self):
        self.start()
        self.protocol.steer({"input_id": "input-a", "text": "correction"})
        identifier = self.sent[-1]["id"]
        request = event("item/fileChange/requestApproval")
        request["id"] = identifier
        self.protocol.accept(request)
        self.reply({"turnId": "turn-a"}, identifier)
        self.assertEqual([("input-a", "accepted")], self.acks)

    def test_unknown_native_request_gets_explicit_unsupported_response(self):
        self.start()
        self.protocol.accept({"id": "unsupported", "method": "account/private", "params": {"secret": "hidden"}})
        self.assertEqual(-32601, self.sent[-1]["error"]["code"])
        self.assertNotIn("hidden", str(self.events))

    def test_native_request_wrong_scope_duplicate_and_tool_events_are_rejected(self):
        self.start()
        request = event("item/fileChange/requestApproval", thread="other")
        request["id"] = 9
        with self.assertRaises(ProtocolError):
            self.protocol.accept(request)
        request = event("item/fileChange/requestApproval")
        request["id"] = 10
        self.protocol.accept(request)
        with self.assertRaises(ProtocolError):
            self.protocol.accept(request)
        for kind in ("commandExecution", "fileChange", "mcpToolCall", "webSearch"):
            with self.subTest(kind=kind), self.assertRaises(ProtocolError):
                self.protocol.accept(event("item/started", item={"type": kind}))

    def test_interrupt_targets_only_owned_thread_and_turn(self):
        self.start()
        self.protocol.interrupt()
        request = self.sent[-1]
        self.protocol.interrupt()
        self.assertIs(request, self.sent[-1])
        self.assertEqual("turn/interrupt", request["method"])
        self.assertEqual({"threadId": "thread-a", "turnId": "turn-a"}, request["params"])

    def test_bad_rpc_and_json_do_not_create_fake_success(self):
        self.start()
        for response in ({"id": "unknown", "result": {}}, {"id": True, "result": {}},
                         {"id": "ma-1", "result": {}, "error": {}}, {"result": {}}):
            with self.subTest(response=response), self.assertRaises(ProtocolError):
                self.protocol.accept(response)
        for line in (b'[]', b'{"id":1,"id":2}', b'{"number":NaN}', b'\xff'):
            with self.subTest(line=line), self.assertRaises(ProtocolError):
                _decode(line)


if __name__ == "__main__":
    unittest.main()
