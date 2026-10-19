"""Local 3.1 workbench; independent of workers and the legacy UI.

Run with ``python -B -m masteragent.workbench --state-root ABSOLUTE_PATH``.
Creating a server binds a loopback socket but never initializes deployment data.
"""
from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
import json
from pathlib import Path
import re
import secrets
import sqlite3
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlsplit

from .errors import PlatformError
from .paths import PathSafetyError
from .workbench_query import WorkbenchQueries
from .workbench_commands import MAX_COMMAND_BYTES, decode_command_payload


WORKBENCH_VERSION = "3.1.0-dev.4"
API_VERSION = "3.1-dev1"
ASSETS = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    **{f"/assets/{name}.mjs": (f"{name}.mjs", "text/javascript; charset=utf-8")
       for name in ("app", "state", "api", "ui", "views", "navigation", "commands", "history")},
    "/assets/styles.css": ("styles.css", "text/css; charset=utf-8"),
}
_SCOPE = re.compile(r"/api/v1/installations/([A-Za-z0-9][A-Za-z0-9_.-]{0,63})/"
                    r"(overview|tasks|task-history|team|resources|connections)(?:/([A-Za-z0-9_-]{1,128}))?\Z")
_COMMAND = re.compile(r"/api/v1/installations/([A-Za-z0-9][A-Za-z0-9_.-]{0,63})/"
                      r"commands/(submit|input|answer|stop)\Z")


def legacy_console_url(value):
    """Validate an explicit local navigation destination; never probe it."""
    if value is None:
        return None
    if not isinstance(value, str) or not value or any(ord(char) <= 32 for char in value):
        raise ValueError("Legacy console URL must be an explicit local HTTP origin.")
    try:
        parsed = urlsplit(value)
        if (parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost"}
                or parsed.port is None or not 1 <= parsed.port <= 65535
                or parsed.username is not None or parsed.password is not None
                or parsed.path not in {"", "/"} or parsed.query or parsed.fragment
                or parsed.netloc not in {f"127.0.0.1:{parsed.port}", f"localhost:{parsed.port}"}):
            raise ValueError
    except ValueError as exc:
        raise ValueError("Legacy console URL must be http://127.0.0.1:PORT or http://localhost:PORT.") from exc
    return f"http://{parsed.netloc}/"


class WorkbenchServer(ThreadingHTTPServer):
    daemon_threads = True
    block_on_close = True
    allow_reuse_address = False

    def __init__(self, state_root: str | Path, host: str, port: int, *,
                 enable_commands=False, legacy_url=None):
        if host != "127.0.0.1":
            raise ValueError("The workbench only binds to 127.0.0.1.")
        if type(port) is not int or not 0 <= port <= 65535:
            raise ValueError("Port must be an integer between 0 and 65535.")
        if type(enable_commands) is not bool:
            raise ValueError("Command enablement must be an explicit boolean.")
        self.legacy_url = legacy_console_url(legacy_url)
        self.commands_enabled = enable_commands
        self.csrf_token = secrets.token_urlsafe(32) if enable_commands else None
        self.queries = WorkbenchQueries(state_root)
        super().__init__((host, port), WorkbenchHandler)

    def configuration(self):
        return {"api_version": API_VERSION,
                "source": {"kind": "masteragent", "state_root": str(self.queries.state_root),
                           "installation_id": None, "generation": None},
                "observed_at": datetime.now(timezone.utc).isoformat(), "revision": None,
                "data": {"version": WORKBENCH_VERSION,
                         "contract_revision": "2026-10-19-routing-receipts-1",
                         "capabilities": {"task_history": True, "result_delivery": True},
                         "commands": {"enabled": self.commands_enabled, "csrf_token": self.csrf_token},
                         "legacy_console": {"configured": self.legacy_url is not None,
                                            "url": self.legacy_url, "connectivity": "not_checked"}}}


class WorkbenchHandler(BaseHTTPRequestHandler):
    server_version = "MasterAgentWorkbench/3.1-dev2.2"
    sys_version = ""
    protocol_version = "HTTP/1.0"

    def setup(self):
        super().setup()
        self.connection.settimeout(10)

    def log_message(self, format, *args):
        # URLs may contain local identifiers. No unrequested request-log files.
        pass

    def _send(self, status, body, content_type="application/json; charset=utf-8", extra=None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header("Content-Security-Policy", "default-src 'none'; script-src 'self'; "
                         "style-src 'self'; connect-src 'self'; img-src 'self' data:; "
                         "font-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
        for name, value in (extra or {}).items():
            self.send_header(name, value)
        try:
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)
        except (ConnectionError, TimeoutError):
            # A scope switch cancels obsolete fetches. The response cannot be
            # retried on this socket; it is not a storage or query failure.
            self.close_connection = True

    def _json(self, status, value, extra=None):
        self._send(status, json.dumps(value, ensure_ascii=False, allow_nan=False,
                                     separators=(",", ":")).encode("utf-8"), extra=extra)

    def _error(self, status, code, message, extra=None):
        self._json(status, {"api_version": API_VERSION,
                            "error": {"code": code, "message": message}}, extra)

    def send_error(self, code, message=None, explain=None):
        # Also cover the HTTP parser's failures without reflecting request text.
        self._error(code, "invalid_http", "The HTTP request is not supported.")

    def _same_origin(self):
        hosts = self.headers.get_all("Host", [])
        port = self.server.server_address[1]
        allowed = {f"127.0.0.1:{port}", f"localhost:{port}"}
        if port == 80:
            allowed |= {"127.0.0.1", "localhost"}
        if len(hosts) != 1 or hosts[0] not in allowed:
            self._error(403, "invalid_host", "Use the local workbench address.")
            return False
        origins = self.headers.get_all("Origin", [])
        sites = self.headers.get_all("Sec-Fetch-Site", [])
        expected_origins = {f"http://{hosts[0]}"}
        if port == 80:
            hostname = hosts[0].split(":")[0]
            expected_origins |= {f"http://{hostname}", f"http://{hostname}:80"}
        if (len(origins) > 1 or (origins and origins[0] not in expected_origins)
                or len(sites) > 1 or (sites and sites[0] not in {"none", "same-origin"})):
            self._error(403, "cross_origin", "Only same-origin workbench requests are accepted.")
            return False
        return True

    def _read(self):
        if not self._same_origin():
            return
        try:
            if len(self.path) > 8192 or any(ord(char) < 32 for char in self.path):
                raise PlatformError("invalid_url", "The request URL is invalid.")
            parsed = urlsplit(self.path)
            path = parsed.path
            if (parsed.scheme or parsed.netloc or parsed.fragment or not path.startswith("/")
                    or "%" in path or "\\" in path or any(part in {".", ".."} for part in path.split("/"))):
                raise PlatformError("invalid_url", "The request path is invalid.")
            params = parse_qs(parsed.query, keep_blank_values=True, strict_parsing=True,
                              max_num_fields=10, encoding="utf-8", errors="strict")
            if any(len(values) != 1 for values in params.values()):
                raise PlatformError("invalid_query", "Query parameters must not be repeated.")
            params = {key: values[0] for key, values in params.items()}
            if path == "/api/v1/workbench":
                if params:
                    raise PlatformError("invalid_query", "Workbench configuration does not accept parameters.")
                self._json(200, self.server.configuration())
                return
            if path in ASSETS:
                if params:
                    raise PlatformError("invalid_query", "Static resources do not accept query parameters.")
                filename, content_type = ASSETS[path]
                payload = resources.files("masteragent").joinpath("workbench_assets", filename).read_bytes()
                self._send(200, payload, content_type)
                return
            if path == "/api/v1/deployment":
                if params:
                    raise PlatformError("invalid_query", "Deployment queries do not accept parameters.")
                self._json(200, self.server.queries.deployment())
                return
            match = _SCOPE.fullmatch(path)
            if not match:
                self._error(404, "not_found", "This workbench resource does not exist.")
                return
            installation_id, resource, task_id = match.groups()
            allowed = {"generation"}
            if resource == "tasks" and task_id is None:
                allowed |= {"limit", "cursor", "state", "role_id"}
            if resource == "task-history":
                allowed |= {"limit", "cursor", "section", "record_generation"}
                if task_id is None:
                    raise PlatformError("invalid_query", "Task history requires a task identifier.")
            if set(params) - allowed or (task_id is not None and resource not in {"tasks", "task-history"}):
                raise PlatformError("invalid_query", "This query contains unsupported parameters.")
            for field in ("generation", "record_generation", "limit"):
                if field in params:
                    if not re.fullmatch(r"0|[1-9][0-9]{0,8}", params[field]):
                        raise PlatformError("invalid_query", f"{field} must be a nonnegative integer.")
                    params[field] = int(params[field])
            if resource == "task-history":
                result = self.server.queries.task_history(installation_id, task_id, **params)
            elif task_id is not None:
                result = self.server.queries.task(installation_id, task_id, **params)
            else:
                result = getattr(self.server.queries, resource)(installation_id, **params)
            self._json(200, result)
        except (PlatformError, PathSafetyError) as exc:
            if exc.code.endswith("not_found") or exc.code in {"task_scope", "task_not_found"}:
                status = 404
            elif exc.code in {"not_initialized", "migration_required", "unsupported_schema",
                              "schema_corrupt", "snapshot_corrupt", "unsupported_journal_mode"}:
                status = 409
            else:
                status = 400
            self._error(status, exc.code, str(exc))
        except (ValueError, UnicodeError):
            self._error(400, "invalid_query", "The query is invalid.")
        except sqlite3.Error:
            self._error(503, "storage_unavailable", "The selected deployment cannot currently be read.")
        except OSError:
            self._error(503, "resource_unavailable", "A selected local resource is unavailable.")
        except Exception:
            self._error(500, "internal_error", "The workbench could not read this resource.")

    do_GET = _read
    do_HEAD = _read

    def do_POST(self):
        if not self._same_origin():
            return
        if not self.server.commands_enabled:
            self._error(405, "read_only", "Commands are disabled for this workbench.", {"Allow": "GET, HEAD"})
            return
        # Same-origin JS first reads this process's ephemeral token. Neither
        # a cross-site form nor a stale tab from another server can issue writes.
        tokens = self.headers.get_all("X-Workbench-CSRF", [])
        if (len(self.headers.get_all("Origin", [])) != 1 or len(tokens) != 1
                or re.fullmatch(r"[A-Za-z0-9_-]{43}", tokens[0]) is None
                or not secrets.compare_digest(tokens[0], self.server.csrf_token)):
            self._error(403, "command_origin", "Reload the local workbench before submitting commands.")
            return
        match = _COMMAND.fullmatch(self.path)
        if not match:
            self._error(404, "not_found", "This workbench command does not exist.")
            return
        try:
            if self.headers.get_all("Transfer-Encoding", []) or self.headers.get_all("Content-Encoding", []):
                raise PlatformError("invalid_body", "Encoded request bodies are not supported.")
            types = self.headers.get_all("Content-Type", [])
            if len(types) != 1 or not re.fullmatch(r"application/json(?:\s*;\s*charset=utf-8)?", types[0], re.IGNORECASE):
                self._error(415, "invalid_content_type", "Commands require UTF-8 application/json.")
                return
            lengths = self.headers.get_all("Content-Length", [])
            if len(lengths) != 1 or not re.fullmatch(r"[1-9][0-9]{0,8}", lengths[0]):
                raise PlatformError("invalid_body", "A single valid Content-Length is required.")
            length = int(lengths[0])
            if length > MAX_COMMAND_BYTES:
                self._error(413, "body_too_large", "The command body is too large.")
                return
            raw = self.rfile.read(length)
            if len(raw) != length:
                raise PlatformError("invalid_body", "The command body is incomplete.")

            payload = decode_command_payload(raw)
            from .workbench_commands import WorkbenchCommands
            installation_id, action = match.groups()
            self._json(200, WorkbenchCommands(self.server.queries.state_root).execute(installation_id, action, payload))
        except (PlatformError, PathSafetyError) as exc:
            if exc.code.endswith("not_found") or exc.code in {"task_scope", "attempt_scope", "decision_scope"}:
                status = 404
            elif (exc.code.startswith("stale_") or exc.code.endswith("_conflict")
                  or exc.code in {"generation_mismatch", "inactive_installation", "installation_inactive",
                                  "decision_closed", "decision_expired", "lease_expired", "not_initialized",
                                  "migration_required", "unsupported_schema", "schema_corrupt"}):
                status = 409
            else:
                status = 400
            self._error(status, exc.code, str(exc))
        except (ValueError, UnicodeError, RecursionError):
            self._error(400, "invalid_body", "The command must contain one valid UTF-8 JSON object.")
        except TimeoutError:
            self._error(408, "body_timeout", "The command body was not received in time.")
        except sqlite3.Error:
            self._error(503, "storage_unavailable", "Command outcome is uncertain; retry the same request identifier.")
        except OSError:
            self._error(503, "resource_unavailable", "Command outcome is uncertain; retry the same request identifier.")
        except Exception:
            self._error(500, "internal_error", "Command outcome is uncertain; retry the same request identifier.")

    def _method_not_allowed(self):
        if self._same_origin():
            self._error(405, "read_only", "This workbench only supports read-only queries.",
                        {"Allow": "GET, HEAD"})

    do_PUT = _method_not_allowed
    do_PATCH = _method_not_allowed
    do_DELETE = _method_not_allowed
    do_OPTIONS = _method_not_allowed
    do_TRACE = _method_not_allowed
    do_CONNECT = _method_not_allowed


def create_server(state_root: str | Path, host: str = "127.0.0.1", port: int = 0, *,
                  enable_commands=False, legacy_url=None):
    """Bind an independent server, read-only unless explicitly enabled."""
    return WorkbenchServer(state_root, host, port, enable_commands=enable_commands, legacy_url=legacy_url)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", action="version", version=WORKBENCH_VERSION)
    parser.add_argument("--state-root", required=True, help="Explicit isolated state directory; never created by this command.")
    parser.add_argument("--host", default="127.0.0.1", choices=("127.0.0.1",))
    parser.add_argument("--port", type=int, default=8801, help="Local port; use 0 for an unused port.")
    parser.add_argument("--enable-commands", action="store_true", help="Enable local task commands on the explicit existing deployment.")
    parser.add_argument("--legacy-console-url", help="Explicit local legacy console origin; navigation only, never probed.")
    args = parser.parse_args(argv)
    try:
        server = create_server(args.state_root, args.host, args.port,
                               enable_commands=args.enable_commands, legacy_url=args.legacy_console_url)
    except (ValueError, OSError) as exc:
        print(json.dumps({"error": {"code": getattr(exc, "code", "startup_failed"),
                                    "message": str(exc)}}, ensure_ascii=False), flush=True)
        return 2
    print(json.dumps({"workbench_version": WORKBENCH_VERSION, "read_only": not args.enable_commands,
                      "url": f"http://127.0.0.1:{server.server_address[1]}",
                      "state_root": str(server.queries.state_root)}, ensure_ascii=False), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
