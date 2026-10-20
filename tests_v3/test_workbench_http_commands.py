"""Real loopback command requests against disposable synthetic deployments."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from masteragent.service import Platform
from masteragent.workbench import MAX_COMMAND_BYTES, legacy_console_url
from tests_v3.test_workbench_http import request, running_server, file_snapshot


ROOT = Path(__file__).resolve().parents[1]


class WorkbenchCommandHttpTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="ma31-command-http-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.state = self.root / "state"
        self.platform = Platform(self.state)
        workspace = self.root / "workspace"
        workspace.mkdir()
        self.platform.install("alpha", ROOT / "examples/v3/business-a.json", workspace)
        self.platform.activate("alpha")
        self.actor = self.platform.bind("alpha", "reviewer")

    def command(self, server, action, payload, *, token=None, headers=None):
        origin = "http://%s:%s" % server.server_address
        if token is None:
            config = json.loads(request(server, "/api/v1/workbench")[2])
            token = config["data"]["commands"]["csrf_token"]
        if headers is None:
            headers = (("Origin", origin), ("X-Workbench-CSRF", token),
                       ("Content-Type", "application/json"))
        body = payload if isinstance(payload, bytes) else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        return request(server, "/api/v1/installations/alpha/commands/" + action,
                       method="POST", headers=headers, body=body)

    def running(self):
        task = self.platform.submit("alpha", "reviewer", "合成执行，不调用模型。")
        lease = self.platform.claim(self.actor, task["task_id"], "catalogue-echo", ttl_seconds=3600)
        self.platform.record_runtime_event(self.actor, lease["attempt_id"], lease["lease_token"],
            "runtime_start_requested", {"runtime": "synthetic", "supports_runtime_inputs": True})
        return task, lease

    def test_config_is_read_only_and_destination_is_never_contacted(self):
        missing = self.root / "missing"
        before = file_snapshot(self.root)
        with patch("subprocess.Popen", side_effect=AssertionError("Process started")), \
                patch("urllib.request.urlopen", side_effect=AssertionError("Legacy source contacted")):
            with running_server(missing, enable_commands=True, legacy_url="http://localhost:8799") as server:
                status, headers, body = request(server, "/api/v1/workbench")
                self.assertEqual(200, status)
                data = json.loads(body)["data"]
                self.assertTrue(data["commands"]["enabled"])
                self.assertEqual(43, len(data["commands"]["csrf_token"]))
                self.assertEqual("http://localhost:8799/", data["legacy_console"]["url"])
                self.assertEqual("not_checked", data["legacy_console"]["connectivity"])
                self.assertIn("no-store", headers["Cache-Control"])
                response = self.command(server, "submit", {"generation": 1, "role_id": "reviewer", "text": "x", "request_id": "new"})
                self.assertEqual(409, response[0], response[2])
        self.assertEqual(before, file_snapshot(self.root))
        self.assertFalse(missing.exists())

    def test_default_read_only_and_tokens_are_process_specific(self):
        before = file_snapshot(self.state)
        with running_server(self.state) as server:
            config = json.loads(request(server, "/api/v1/workbench")[2])["data"]
            self.assertEqual({"enabled": False, "csrf_token": None}, config["commands"])
            self.assertFalse(config["legacy_console"]["configured"])
            self.assertEqual(405, self.command(server, "submit", {}, token="fake")[0])
        with running_server(self.state, enable_commands=True) as first, \
                running_server(self.state, enable_commands=True) as second:
            token = json.loads(request(first, "/api/v1/workbench")[2])["data"]["commands"]["csrf_token"]
            self.assertEqual(403, self.command(second, "submit", {}, token=token)[0])
        self.assertEqual(before, file_snapshot(self.state))

    def test_command_requires_exact_origin_token_and_json(self):
        before = file_snapshot(self.state)
        with running_server(self.state, enable_commands=True) as server:
            origin = "http://%s:%s" % server.server_address
            token = server.csrf_token
            cases = [
                ((("Content-Type", "application/json"), ("X-Workbench-CSRF", token)), 403),
                ((("Origin", origin), ("Content-Type", "application/json")), 403),
                ((("Origin", origin), ("X-Workbench-CSRF", "é"), ("Content-Type", "application/json")), 403),
                ((("Origin", origin), ("X-Workbench-CSRF", token), ("X-Workbench-CSRF", token), ("Content-Type", "application/json")), 403),
                ((("Origin", "http://attacker.invalid"), ("X-Workbench-CSRF", token), ("Content-Type", "application/json")), 403),
                ((("Origin", origin), ("X-Workbench-CSRF", token), ("Sec-Fetch-Site", "same-site"), ("Content-Type", "application/json")), 403),
                ((("Origin", origin), ("X-Workbench-CSRF", token), ("Content-Type", "text/plain")), 415),
                ((("Origin", origin), ("X-Workbench-CSRF", token), ("Content-Type", "application/json"), ("Transfer-Encoding", "chunked")), 400),
                ((("Origin", origin), ("X-Workbench-CSRF", token), ("Content-Type", "application/json"), ("Content-Length", "2")), 400),
            ]
            for headers, expected in cases:
                with self.subTest(headers=headers):
                    response = self.command(server, "submit", b"{}", headers=headers)
                    self.assertEqual(expected, response[0], response[2])
                    self.assertNotIn(token.encode(), response[2])
        self.assertEqual(before, file_snapshot(self.state))

    def test_malformed_extra_fields_and_invalid_routes_cannot_write(self):
        before = file_snapshot(self.state)
        with running_server(self.state, enable_commands=True) as server:
            for payload in (b'{"generation":1,"generation":2}', b'{"x":NaN}', b'[]', b'\xff',
                            b'{"generation":true}', b'{"generation":1,"actor_token":"fake"}'):
                with self.subTest(payload=payload):
                    self.assertEqual(400, self.command(server, "submit", payload)[0])
            # The length guard must reject before reading any body. Uploading
            # the rejected body races HTTP/1.0 close on Windows, where unread
            # queued bytes can reset the connection before the client reads 413.
            oversized = request(
                server, "/api/v1/installations/alpha/commands/submit", method="POST",
                headers=(("Origin", "http://%s:%s" % server.server_address),
                         ("X-Workbench-CSRF", server.csrf_token),
                         ("Content-Type", "application/json"),
                         ("Content-Length", str(MAX_COMMAND_BYTES + 1))))
            self.assertEqual(413, oversized[0], oversized[2])
            self.assertEqual("body_too_large", json.loads(oversized[2])["error"]["code"])
            for action in ("submit?generation=1", "submit/", "../submit", "activate", "%73ubmit"):
                self.assertEqual(404, self.command(server, action, {})[0])
        self.assertEqual(before, file_snapshot(self.state))

    def test_all_four_commands_repeat_safely_and_never_start_a_process(self):
        task, lease = self.running()
        decision_task = self.platform.submit("alpha", "reviewer", "等待决定。")
        decision = self.platform.ask_decision(self.actor, decision_task["task_id"], "批准合成范围？", "question")
        payloads = [
            ("submit", {"generation": 1, "role_id": "reviewer", "text": "中文新任务", "request_id": "submit-once"}),
            ("input", {"generation": 1, "task_id": task["task_id"], "attempt_id": lease["attempt_id"], "text": "补充说明", "request_id": "input-once"}),
            ("answer", {"generation": 1, "task_id": decision_task["task_id"], "decision_id": decision["decision_id"],
                        "expected_revision": decision["revision"], "choice": "approve", "answer_id": "answer-once"}),
            ("stop", {"generation": 1, "task_id": task["task_id"], "attempt_id": lease["attempt_id"], "reason": "样例停止"}),
        ]
        with patch("subprocess.Popen", side_effect=AssertionError("A command started a process")):
            with running_server(self.state, enable_commands=True) as server:
                for action, payload in payloads:
                    with self.subTest(action=action):
                        first = self.command(server, action, payload)
                        self.assertEqual(200, first[0], first[2])
                        second = self.command(server, action, payload)
                        self.assertEqual(200, second[0], second[2])
                        self.assertEqual(json.loads(first[2])["data"], json.loads(second[2])["data"])
                        self.assertNotIn(self.actor.token.encode(), first[2])
                        self.assertNotIn(lease["lease_token"].encode(), first[2])
                        if action == "stop":
                            self.assertFalse(json.loads(first[2])["data"]["result"]["termination_confirmed"])
                # Retry after later commands still acknowledges the exact input,
                # without creating or delivering another record.
                self.assertEqual(200, self.command(server, "input", payloads[1][1])[0])
        self.assertEqual(1, len(self.platform.runtime_inputs("alpha", lease["attempt_id"])))

    def test_old_scope_conflict_and_task_mismatch_are_rejected(self):
        task, lease = self.running()
        with running_server(self.state, enable_commands=True) as server:
            response = self.command(server, "submit", {"generation": 2, "role_id": "reviewer", "text": "wrong scope", "request_id": "wrong"})
            self.assertEqual(409, response[0], response[2])
            response = self.command(server, "input", {"generation": 1, "task_id": "another-task", "attempt_id": lease["attempt_id"], "text": "wrong task", "request_id": "wrong"})
            self.assertIn(response[0], (400, 404, 409), response[2])
            payload = {"generation": 1, "role_id": "reviewer", "text": "first", "request_id": "same-id"}
            self.assertEqual(200, self.command(server, "submit", payload)[0])
            payload["text"] = "different"
            self.assertEqual(409, self.command(server, "submit", payload)[0])
        self.assertEqual([], self.platform.runtime_inputs("alpha", lease["attempt_id"]))

    def test_legacy_destinations_reject_remote_credentials_paths_and_scripts(self):
        for url in ("https://127.0.0.1:8799", "http://example.com:8799", "http://127.0.0.1", "http://127.0.0.1:0",
                    "http://127.0.0.1:8799/api/state", "http://user@127.0.0.1:8799", "http://127.0.0.1:8799?x=1",
                    "http://127.0.0.1:8799#tasks", "javascript:alert(1)", "http://127.0.0.1:8799\n", "http://[::1]:8799"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                legacy_console_url(url)
        self.assertEqual("http://127.0.0.1:8799/", legacy_console_url("http://127.0.0.1:8799/"))
        self.assertIsNone(legacy_console_url(None))

    def test_command_internal_errors_do_not_expose_exception_contents(self):
        import sqlite3
        marker = "PRIVATE_SENTINEL_do_not_return"
        with running_server(self.state, enable_commands=True) as server:
            for error in (sqlite3.OperationalError(marker), OSError(marker), RuntimeError(marker), ValueError(marker)):
                with self.subTest(error=type(error).__name__), \
                        patch("masteragent.workbench_commands.WorkbenchCommands.execute", side_effect=error):
                    response = self.command(server, "submit", {})
                    self.assertIn(response[0], (400, 500, 503))
                    self.assertNotIn(marker.encode(), response[2])
                    self.assertNotIn(b"Traceback", response[2])

    def test_cli_forwards_explicit_controls_without_loading_runtime(self):
        from masteragent.cli import main
        with patch("masteragent.workbench.main", return_value=0) as entry:
            self.assertEqual(0, main(["--state-root", str(self.state), "workbench", "--port", "0", "--enable-commands", "--legacy-console-url", "http://localhost:8799"]))
        self.assertEqual(["--state-root", str(self.state), "--port", "0", "--enable-commands", "--legacy-console-url", "http://localhost:8799"], entry.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
