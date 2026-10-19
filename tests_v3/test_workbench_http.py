"""HTTP boundary acceptance against disposable synthetic state only.

No native runtime, credentials, old bus, or existing service is used. The
listener is owned by each test, binds an ephemeral loopback port, and is closed.
"""
from contextlib import contextmanager
import hashlib
import http.client
import json
import os
from pathlib import Path
import socketserver
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
import zipfile

from masteragent.service import Platform


ROOT = Path(__file__).resolve().parents[1]
API = "/api/v1"
ASSETS = ("app.mjs", "state.mjs", "api.mjs", "ui.mjs", "views.mjs", "styles.css", "navigation.mjs", "commands.mjs", "history.mjs")


@contextmanager
def running_server(state_root, **options):
    from masteragent.workbench import create_server

    server = create_server(state_root, host="127.0.0.1", port=0, **options)
    thread = threading.Thread(target=server.serve_forever,
                              kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()
        if thread.is_alive():
            raise AssertionError("The test-owned HTTP server did not stop.")


def request(server, target, *, method="GET", headers=(), body=None, automatic_host=True):
    connection = http.client.HTTPConnection(*server.server_address, timeout=5)
    try:
        connection.putrequest(method, target, skip_host=True,
                              skip_accept_encoding=True)
        headers = list(headers)
        if automatic_host and not any(key.lower() == "host" for key, _ in headers):
            connection.putheader("Host", "%s:%s" % server.server_address)
        for key, value in headers:
            connection.putheader(key, value)
        if body is not None:
            connection.putheader("Content-Length", str(len(body)))
        connection.endheaders(body)
        response = connection.getresponse()
        return response.status, dict(response.getheaders()), response.read()
    finally:
        connection.close()


def file_snapshot(root):
    snapshot = {".": ("directory", root.stat().st_mtime_ns)}
    for path in root.rglob("*"):
        key = str(path.relative_to(root))
        if path.is_dir():
            snapshot[key] = ("directory", path.stat().st_mtime_ns)
        elif path.is_file():
            snapshot[key] = ("file", path.stat().st_mtime_ns,
                             hashlib.sha256(path.read_bytes()).hexdigest())
    return snapshot


class WorkbenchHttpTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="ma31-http-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.state = self.root / "state"
        self.platform = Platform(self.state)
        self.tasks = {}
        self.actors = {}
        for installation in ("alpha", "beta"):
            workspace = self.root / (installation + "-workspace")
            workspace.mkdir()
            package = self.root / (installation + ".json")
            package.write_text(json.dumps({
                "schema_version": 1, "platform_api": 1,
                "package_id": installation, "version": "1.0.0",
                "roles": [{"role_id": "reviewer", "display_name": "审查员",
                           "instructions": "Synthetic role " + installation,
                           "tools": ["echo"]}],
                "knowledge": {"fixture": installation + "-private-knowledge"},
                "tools": {"echo": {"adapter": "echo", "description": "文本回声"}},
                "workflows": {},
            }, ensure_ascii=False), encoding="utf-8")
            self.platform.install(installation, package, workspace)
            self.platform.activate(installation)
            self.actors[installation] = self.platform.bind(installation, "reviewer")
            self.tasks[installation] = self.platform.submit(
                installation, "reviewer", installation + " 合成任务：你好，世界 🌏")
            if installation == "alpha":
                self.platform.deactivate(installation)

    def assert_error(self, response, expected_status=None):
        status, headers, body = response
        if expected_status is not None:
            self.assertEqual(expected_status, status, body)
        self.assertIn("application/json", headers.get("Content-Type", ""))
        self.assertEqual("nosniff", headers.get("X-Content-Type-Options"))
        self.assertIn("no-store", headers.get("Cache-Control", ""))
        self.assertNotIn("Access-Control-Allow-Origin", headers)
        value = json.loads(body.decode("utf-8"))
        self.assertEqual("3.1-dev1", value["api_version"])
        self.assertIsInstance(value["error"]["code"], str)
        self.assertIsInstance(value["error"]["message"], str)
        self.assertNotIn(str(self.root), body.decode("utf-8"))
        self.assertNotIn("Traceback", body.decode("utf-8"))
        return value

    def test_loopback_binding_is_explicit_and_other_hosts_rejected(self):
        from masteragent.workbench import create_server

        for host in ("0.0.0.0", "::", "::1", "localhost", "192.0.2.1", ""):
            with self.subTest(host=host), self.assertRaises(ValueError):
                # Server creation must reject these before any bind is attempted.
                server = create_server(self.state, host=host, port=0)
                server.server_close()
                self.fail("Unsupported bind host was accepted")
        with running_server(self.state) as server:
            self.assertEqual("127.0.0.1", server.server_address[0])
            self.assertGreater(server.server_address[1], 0)

    def test_host_origin_and_fetch_metadata_reject_foreign_requesters(self):
        with running_server(self.state) as server:
            port = server.server_address[1]
            bad_headers = (
                (("Host", "attacker.invalid:%d" % port),),
                (("Host", "127.0.0.1:%d" % (port + 1)),),
                (("Host", "127.0.0.1:%d" % port), ("Host", "localhost:%d" % port)),
                (("Origin", "https://attacker.invalid"),),
                (("Origin", "null"),),
                (("Origin", "http://127.0.0.1:%d" % (port + 1)),),
                (("Origin", "http://localhost:%d" % port),),
                (("Origin", "http://127.0.0.1:%d" % port), ("Origin", "http://127.0.0.1:%d" % port)),
                (("Sec-Fetch-Site", "cross-site"),),
                (("Sec-Fetch-Site", "same-site"),),
                (("Sec-Fetch-Site", "same-origin"), ("Sec-Fetch-Site", "same-origin")),
            )
            for headers in bad_headers:
                for target in (API + "/deployment", "/assets/app.mjs"):
                    with self.subTest(headers=headers, target=target):
                        self.assert_error(request(server, target, headers=headers), 403)
            self.assert_error(request(server, API + "/deployment", automatic_host=False), 403)

    def test_exact_same_origin_and_localhost_alias_are_usable(self):
        with running_server(self.state) as server:
            port = server.server_address[1]
            for host in ("127.0.0.1", "localhost"):
                origin = "http://%s:%d" % (host, port)
                response = request(server, API + "/deployment", headers=(
                    ("Host", "%s:%d" % (host, port)), ("Origin", origin),
                    ("Sec-Fetch-Site", "same-origin")))
                self.assertEqual(200, response[0], response[2])
                self.assertNotIn("Access-Control-Allow-Origin", response[1])

    def test_mutating_and_preflight_methods_are_rejected_without_writes(self):
        before = file_snapshot(self.state)
        with running_server(self.state) as server:
            for method in ("POST", "PUT", "PATCH", "DELETE", "OPTIONS", "TRACE", "CONNECT"):
                with self.subTest(method=method):
                    response = request(server, API + "/deployment", method=method,
                                       body=b'{"operation":"activate","installation":"alpha"}')
                    self.assert_error(response, 405)
                    self.assertEqual({"GET", "HEAD"},
                                     set(response[1].get("Allow", "").replace(" ", "").split(",")))
        self.assertEqual(before, file_snapshot(self.state))

    def test_arbitrary_paths_and_url_encoding_cannot_read_files(self):
        marker = "synthetic-outside-file-not-public"
        (self.root / "private.txt").write_text(marker, encoding="utf-8")
        targets = (
            "/state.sqlite3", "/assets/state.sqlite3", "/assets/../state.sqlite3",
            "/../private.txt", "/assets\\..\\private.txt", "/assets/%2e%2e/private.txt",
            "/assets/%252e%252e/private.txt", "/assets/%2fprivate.txt", "/assets/%00",
            "/assets/%FF", "/assets/%", "/C:/Windows/win.ini",
            "/api/v1/installations/alpha/tasks/../../beta/tasks",
            "/api/v1/installations/alpha%2fbeta/overview",
            "/api/v1/deployment?state_root=" + str(self.root).replace("\\", "/"),
            "http://attacker.invalid/api/v1/deployment",
        )
        with running_server(self.state) as server:
            for target in targets:
                with self.subTest(target=target):
                    response = request(server, target)
                    self.assertIn(response[0], (400, 404))
                    self.assert_error(response)
                    self.assertNotIn(marker.encode(), response[2])
                    self.assertNotIn(b"SQLite format", response[2])

    def test_duplicate_unknown_and_invalid_query_parameters_are_rejected(self):
        targets = (
            API + "/deployment?file=auth.json",
            API + "/installations/alpha/tasks?limit=1&limit=2",
            API + "/installations/alpha/tasks?limit=-1",
            API + "/installations/alpha/tasks?limit=not-a-number",
            API + "/installations/alpha/overview?generation=1&generation=2",
            API + "/installations/alpha/tasks?unknown=anything",
        )
        with running_server(self.state) as server:
            for target in targets:
                with self.subTest(target=target):
                    self.assert_error(request(server, target), 400)
            self.assertIn(request(server, "/" + "a" * 70000)[0], (400, 414))

    def test_scoped_endpoints_do_not_mix_installations_or_task_details(self):
        with running_server(self.state) as server:
            for installation, other in (("alpha", "beta"), ("beta", "alpha")):
                prefix = API + "/installations/" + installation
                for endpoint in ("overview", "tasks", "team", "resources", "connections"):
                    with self.subTest(installation=installation, endpoint=endpoint):
                        status, _, body = request(server, prefix + "/" + endpoint)
                        self.assertEqual(200, status, body)
                        value = json.loads(body)
                        self.assertEqual(installation, value["source"]["installation_id"])
                        self.assertNotIn(self.tasks[other]["task_id"], body.decode("utf-8"))
                        self.assertNotIn(other + "-private-knowledge", body.decode("utf-8"))
                task_id = self.tasks[installation]["task_id"]
                response = request(server, prefix + "/tasks/" + task_id)
                self.assertEqual(200, response[0], response[2])
                self.assertIn("合成任务", response[2].decode("utf-8"))
                self.assert_error(request(server, prefix + "/tasks/" + self.tasks[other]["task_id"]), 404)

    def test_legal_dotted_installation_identifier_is_not_treated_as_traversal(self):
        workspace = self.root / "dotted-workspace"
        workspace.mkdir()
        self.platform.install("alpha..copy", self.root / "alpha.json", workspace)
        with running_server(self.state) as server:
            response = request(server, API + "/installations/alpha..copy/overview")
            self.assertEqual(200, response[0], response[2])
            self.assertEqual("alpha..copy", json.loads(response[2])["source"]["installation_id"])

    def test_history_route_pages_cross_generation_without_changing_legacy_task_scope(self):
        task_id = self.tasks["alpha"]["task_id"]
        self.platform.deactivate("beta")
        self.platform.activate("alpha")
        self.platform.resume_task("alpha", task_id, "synthetic continuation")
        before = file_snapshot(self.state)
        prefix = API + "/installations/alpha/"
        target = prefix + "task-history/" + task_id
        with running_server(self.state, enable_commands=True) as server:
            configuration = json.loads(request(server, API + "/workbench")[2])["data"]
            self.assertTrue(configuration["capabilities"]["task_history"])
            self.assertEqual(configuration["contract_revision"], "2026-10-19-routing-receipts-1")
            self.assertTrue(configuration["capabilities"]["result_delivery"])
            response = request(server, target + "?generation=1&limit=1")
            self.assertEqual(200, response[0], response[2])
            first = json.loads(response[2])
            self.assertEqual(first["source"]["generation"], 1)
            self.assertTrue(first["data"]["readonly"])
            self.assertEqual(first["data"]["items"][0]["generation"], 2)
            tail = request(server, target + "?generation=1&cursor=" + first["data"]["next_cursor"])
            self.assertEqual(json.loads(tail[2])["data"]["items"][0]["generation"], 1)
            edge = request(server, target + "?generation=1&section=continuations&record_generation=1")
            self.assertEqual(200, edge[0], edge[2])
            self.assertEqual(json.loads(edge[2])["data"]["items"][0]["to_generation"], 2)
            self.assert_error(request(server, prefix + "tasks/" + task_id + "?generation=1"), 404)
            self.assert_error(request(server, target + "?section=attempts&record_generation=0"), 404)
            self.assert_error(request(server, target + "?generation=2&cursor=" + first["data"]["next_cursor"]), 400)
            self.assert_error(request(server, API + "/installations/beta/task-history/" + task_id), 404)
            for suffix in ("?record_generation=1", "?section=attempts", "?section=unknown",
                           "?section=attempts&record_generation=-1", "?section=attempts&record_generation=true",
                           "?limit=1&limit=2", "?state=queued"):
                self.assert_error(request(server, target + suffix), 400)
            self.assert_error(request(server, prefix + "task-history"), 400)
        self.assertEqual(before, file_snapshot(self.state))

    def test_api_keeps_unknown_runtime_measurements_and_never_exposes_actor_tokens(self):
        with running_server(self.state) as server:
            response = request(server, API + "/installations/beta/connections")
            self.assertEqual(200, response[0], response[2])
            items = json.loads(response[2])["data"]["items"]
            self.assertEqual({"codex", "claude"}, {item["runtime"] for item in items})
            for item in items:
                self.assertIsNone(item["model_observed"])
                self.assertIsNone(item["usage"])
                if item["runtime"] == "claude":
                    self.assertEqual("unavailable", item["status"])
            for endpoint in ("deployment", "installations/beta/team", "installations/beta/tasks",
                             "installations/beta/tasks/" + self.tasks["beta"]["task_id"]):
                body = request(server, API + "/" + endpoint)[2].decode("utf-8")
                for actor in self.actors.values():
                    self.assertNotIn(actor.token, body)
                    self.assertNotIn(hashlib.sha256(actor.token.encode()).hexdigest(), body)
                self.assertNotRegex(body, r'"(?:token_hash|owner_hash|lease_hash)"\s*:')

    def test_all_gets_and_head_are_utf8_no_store_and_do_not_write_or_start_processes(self):
        targets = ["/", "/index.html", API + "/deployment", API + "/workbench"]
        targets += ["/assets/" + name for name in ASSETS]
        targets += [API + "/installations/" + installation + "/" + endpoint
                    for installation in self.tasks
                    for endpoint in ("overview", "tasks", "team", "resources", "connections")]
        targets += [API + "/installations/" + installation + "/tasks/" + task["task_id"]
                    for installation, task in self.tasks.items()]
        targets += [API + "/installations/" + installation + "/task-history/" + task["task_id"] + suffix
                    for installation, task in self.tasks.items()
                    for suffix in ("", "?section=attempts&record_generation=1", "?section=events&record_generation=1")]
        before = file_snapshot(self.root)
        with patch("subprocess.Popen", side_effect=AssertionError("A read started a subprocess")):
            with running_server(self.state) as server:
                for target in targets:
                    with self.subTest(target=target):
                        status, headers, body = request(server, target)
                        self.assertEqual(200, status, body)
                        body.decode("utf-8")
                        self.assertIn("no-store", headers.get("Cache-Control", ""))
                        self.assertEqual("nosniff", headers.get("X-Content-Type-Options"))
                        self.assertNotIn("Access-Control-Allow-Origin", headers)
                        head_status, head_headers, head_body = request(server, target, method="HEAD")
                        self.assertEqual(status, head_status)
                        self.assertEqual(b"", head_body)
                        # JSON observation timestamps can differ; static resource sizes cannot.
                        if not target.startswith(API):
                            self.assertEqual(len(body), int(head_headers["Content-Length"]))
        self.assertEqual(before, file_snapshot(self.root))

    def test_queries_do_not_read_or_scan_native_homes_and_business_files(self):
        native = self.root / "synthetic-native-home"
        native.mkdir()
        (native / "auth.json").write_text('{"fixture":"never-open-this"}', encoding="utf-8")
        protected = (native, self.root / "alpha-workspace", self.root / "beta-workspace",
                     Path.home() / ".codex", Path.home() / ".claude")
        original_open, original_scandir = Path.open, os.scandir

        def guard(path):
            if isinstance(path, (str, Path)):
                candidate = Path(path).absolute()
                if any(candidate == boundary or boundary in candidate.parents for boundary in protected):
                    raise AssertionError("A query accessed an unrelated native or business file")

        def guarded_open(path, *args, **kwargs):
            guard(path)
            return original_open(path, *args, **kwargs)

        def guarded_scandir(path):
            guard(path)
            return original_scandir(path)

        with patch.dict(os.environ, {"CODEX_HOME": str(native), "CLAUDE_CONFIG_DIR": str(native)}), \
                patch.object(Path, "open", guarded_open), patch("os.scandir", guarded_scandir):
            with running_server(self.state) as server:
                for endpoint in ("deployment", "installations/beta/overview", "installations/beta/tasks",
                                 "installations/beta/team", "installations/beta/resources",
                                 "installations/beta/connections",
                                 "installations/beta/tasks/" + self.tasks["beta"]["task_id"]):
                    with self.subTest(endpoint=endpoint):
                        response = request(server, API + "/" + endpoint)
                        self.assertEqual(200, response[0], response[2])

    def test_missing_and_empty_roots_are_observed_without_initialization(self):
        for name, create in (("missing", False), ("empty", True)):
            state = self.root / name
            if create:
                state.mkdir()
            before = file_snapshot(self.root)
            with running_server(state) as server:
                self.assertEqual(200, request(server, "/")[0])
                response = request(server, API + "/deployment")
                self.assertEqual(200, response[0], response[2])
                self.assertFalse(json.loads(response[2])["data"]["initialized"])
                self.assert_error(request(server, API + "/installations/alpha/task-history/task-one"), 409)
            self.assertEqual(create, state.exists())
            self.assertEqual(before, file_snapshot(self.root))

    def test_corrupt_database_error_is_structured_and_does_not_reveal_internal_exception(self):
        state = self.root / "corrupt"
        state.mkdir()
        (state / "state.sqlite3").write_bytes(b"synthetic-damaged-database")
        before = file_snapshot(state)
        with running_server(state) as server:
            response = request(server, API + "/deployment")
            self.assertIn(response[0], (400, 409, 500, 503))
            self.assert_error(response)
            self.assertNotIn(b"sqlite3.", response[2])
            self.assertNotIn(b"synthetic-damaged-database", response[2])
        self.assertEqual(before, file_snapshot(state))

    def test_cancelled_fetch_closes_without_second_response_and_server_remains_usable(self):
        original_write = socketserver._SocketWriter.write
        # The first write flushes headers; the second sends the JSON body.
        # Both are possible cancellation points when the browser switches scope.
        for fail_at in (1, 2):
            with self.subTest(write=fail_at), running_server(self.state) as server:
                writes = 0

                def disconnect_once(writer, payload):
                    nonlocal writes
                    writes += 1
                    if writes == fail_at:
                        raise ConnectionAbortedError(10053, "Synthetic browser cancellation")
                    return original_write(writer, payload)

                with patch.object(server, "handle_error") as handle_error, \
                        patch.object(socketserver._SocketWriter, "write", disconnect_once):
                    with self.assertRaises((http.client.RemoteDisconnected,
                                            http.client.IncompleteRead, ConnectionResetError)):
                        request(server, API + "/deployment")
                    self.assertEqual(fail_at, writes, "A closed response was retried with another status")
                    response = request(server, API + "/deployment")
                    self.assertEqual(200, response[0], response[2])
                    self.assertTrue(json.loads(response[2])["data"]["initialized"])
                    handle_error.assert_not_called()

    def test_unknown_routes_and_missing_entities_have_safe_json_errors(self):
        with running_server(self.state) as server:
            for target in (API + "/unknown", API + "/installations/missing/tasks",
                           API + "/installations/alpha/tasks/missing-task", "/assets/private.json"):
                with self.subTest(target=target):
                    self.assert_error(request(server, target), 404)

    def test_browser_assets_are_local_allowlisted_and_have_safe_mime(self):
        with running_server(self.state) as server:
            status, headers, body = request(server, "/")
            self.assertEqual(200, status)
            self.assertIn("text/html", headers["Content-Type"])
            policy = headers.get("Content-Security-Policy", "")
            self.assertIn("default-src 'none'", policy)
            self.assertIn("script-src 'self'", policy)
            self.assertIn("frame-ancestors 'none'", policy)
            self.assertNotIn("'unsafe-eval'", policy)
            html = body.decode("utf-8")
            self.assertIn("/assets/app.mjs", html)
            self.assertIn("/assets/styles.css", html)
            self.assertNotRegex(html, r'(?:src|href)\s*=\s*[\"\']\s*(?:https?:)?//')
            for asset in ASSETS:
                response = request(server, "/assets/" + asset)
                self.assertEqual(200, response[0], response[2])
                self.assertIn("text/css" if asset.endswith(".css") else "javascript",
                              response[1]["Content-Type"])
                self.assertNotRegex(response[2].decode("utf-8"),
                                    r'(?:import\s*\(|from\s*)[\"\']https?://')


class WorkbenchBundleTests(unittest.TestCase):
    def test_local_bundle_contains_all_frontend_resources(self):
        with tempfile.TemporaryDirectory(prefix="ma31-http-bundle-") as directory:
            root = Path(directory).resolve()
            archive_path = root / "workbench.pyz"
            result = subprocess.run(
                [sys.executable, "-B", str(ROOT / "scripts" / "build_v3.py"), "--output", str(archive_path)],
                cwd=root, capture_output=True, text=True, encoding="utf-8", timeout=20, check=False)
            self.assertEqual(0, result.returncode, result.stderr)
            with zipfile.ZipFile(archive_path) as archive:
                names = set(archive.namelist())
                for name in ("index.html", *ASSETS):
                    matches = [candidate for candidate in names
                               if candidate.startswith("masteragent/") and candidate.endswith("/" + name)]
                    self.assertEqual(1, len(matches), (name, matches))
                    archive.read(matches[0]).decode("utf-8")
            # Import from the archive in an unrelated cwd, then actually serve
            # its resources. Zip entries alone do not prove importlib.resources
            # works in the standalone distribution.
            script = """
import http.client, json, sys, threading
sys.path.insert(0, sys.argv[1])
import masteragent.workbench as workbench
assert sys.argv[1] in workbench.__file__, workbench.__file__
assert not any(name == 'agent_bus' or name.startswith('agent_bus.') for name in sys.modules)
server = workbench.create_server(sys.argv[2], port=0)
thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': .01}, daemon=True)
thread.start()
try:
    for path in ('/', '/assets/app.mjs', '/assets/styles.css', '/api/v1/deployment'):
        connection = http.client.HTTPConnection(*server.server_address, timeout=5)
        try:
            connection.request('GET', path)
            response = connection.getresponse()
            body = response.read().decode('utf-8')
            assert response.status == 200, (path, response.status, body)
            if path.endswith('/deployment'):
                assert json.loads(body)['data']['initialized'] is False
        finally:
            connection.close()
finally:
    server.shutdown()
    thread.join(5)
    server.server_close()
assert not thread.is_alive()
"""
            state = root / "uninitialized"
            served = subprocess.run([sys.executable, "-B", "-c", script, str(archive_path), str(state)],
                                    cwd=root, capture_output=True, text=True, encoding="utf-8",
                                    timeout=20, check=False)
            self.assertEqual(0, served.returncode, served.stderr)
            self.assertFalse(state.exists())


if __name__ == "__main__":
    unittest.main()
