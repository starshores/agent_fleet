"""Register an explicitly selected Codex role and keep its bus wait bridged.

The session id comes from Codex's SessionStart event, never from a process PID or
the working directory. A new thread consumes an explicitly armed, cwd-scoped
role ticket; an already registered thread recovers its role from an exact
presence session-id match.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import base64
import psutil
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PYTHON = Path(r"C:\path\to\python.exe")
BUS = ROOT / "agent_bus" / "agentbus.py"
MESSENGER = ROOT / ".codex" / "codex_messenger.py"
AGENTS = ROOT / "agent_bus" / "agents.json"
sys.path.insert(0, str(ROOT / "agent_bus"))
from bus.codex_startup import (claim_role_assignment, restore_registered_codex_session,
                               registered_codex_role, prepare_session_start_identity)


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _role_for_session(session_id: str) -> str | None:
    if not isinstance(session_id, str) or not session_id.strip() or session_id != session_id.strip():
        return None
    matches = []
    agents = _read_json(AGENTS)
    for role in agents:
        presence = _read_json(ROOT / "agent_bus" / role / "presence.json")
        if (presence.get("host_kind") == "codex"
                and presence.get("host_session_id") == session_id):
            matches.append(role)
    if len(matches) > 1:
        raise RuntimeError("session id is bound to multiple agent-bus roles")
    return matches[0] if matches else None


def _start_messenger(role: str, session_id: str, cwd: str) -> None:
    """Launch outside the hook job so the messenger has no hook lifetime limit."""
    pythonw = PYTHON.with_name("pythonw.exe")
    command = subprocess.list2cmdline([
        str(pythonw), "-B", str(MESSENGER), "--role", role,
        "--session-id", session_id, "--cwd", cwd,
    ])
    encoded = base64.b64encode(command.encode("utf-16-le")).decode("ascii")
    root_encoded = base64.b64encode(str(ROOT).encode("utf-16-le")).decode("ascii")
    script = (
        "$line=[Text.Encoding]::Unicode.GetString([Convert]::FromBase64String('" + encoded + "'));"
        "$dir=[Text.Encoding]::Unicode.GetString([Convert]::FromBase64String('" + root_encoded + "'));"
        "$created=Invoke-CimMethod -ClassName Win32_Process -MethodName Create "
        "-Arguments @{CommandLine=$line;CurrentDirectory=$dir};"
        "if($created.ReturnValue -ne 0){throw ('Win32_Process.Create failed: '+$created.ReturnValue)};"
        "[Console]::WriteLine($created.ProcessId)"
    )
    completed = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand",
         base64.b64encode(script.encode("utf-16-le")).decode("ascii")],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if completed.returncode:
        raise RuntimeError("messenger launch failed: " + completed.stderr[-1000:])
    try:
        pid = int(completed.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError) as exc:
        raise RuntimeError("messenger launch returned no PID") from exc

    state_path = ROOT / "agent_bus" / role / "messenger.state.json"
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        state = _read_json(state_path)
        if state.get("session_id") == session_id and state.get("status") == "armed":
            try:
                stamp = time.mktime(time.strptime(state["heartbeat"], "%Y-%m-%dT%H:%M:%S"))
                if time.time() - stamp < 30:
                    args = psutil.Process(int(state["pid"])).cmdline()
                    if ("--session-id" in args and session_id in args
                            and any(Path(arg).name.lower() == "codex_messenger.py" for arg in args)):
                        # Global and project hooks may race. The session lock
                        # allows only one messenger to report a healthy beat.
                        return
            except (KeyError, ValueError, OSError, psutil.Error):
                pass
        time.sleep(0.2)
    raise RuntimeError(f"messenger did not report armed for {role} (pid {pid})")


def main() -> int:
    try:
        event = json.load(sys.stdin)
    except Exception:
        return 0
    if (event.get("hook_event_name") != "SessionStart"
            or event.get("source") not in ("startup", "resume")):
        return 0

    session_id = event.get("session_id")
    cwd = event.get("cwd")
    if (not isinstance(session_id, str) or not session_id.strip() or session_id != session_id.strip()
            or not isinstance(cwd, str) or not cwd.strip() or cwd != cwd.strip()):
        return 0

    prepare_session_start_identity(session_id)
    known_role = registered_codex_role(session_id)
    bound_role = restore_registered_codex_session(session_id) if known_role else None
    if known_role and bound_role != known_role:
        raise RuntimeError("registered Codex resume could not rebind the exact live host")
    # The context hook may already have reserved this ticket. Its exact-session
    # reentry is intentional and still rechecks cwd, expiry and role capability.
    selected_role = None if bound_role else claim_role_assignment(session_id, cwd)
    role = bound_role or selected_role
    if not role:
        # A shared Codex process can host Master and business threads at once.
        # Unknown sessions stay unbound until an explicit role is supplied.
        return 0
    if role not in _read_json(AGENTS):
        raise RuntimeError(f"unknown agent-bus role: {role}")

    env = os.environ.copy()
    env["CODEX_SESSION_ID"] = session_id
    env["AGENTBUS_CODEX_HOOK_HELLO"] = "1"
    if not bound_role:
        # The one-shot ticket is explicitly armed for this exact project cwd.
        completed = subprocess.run(
            [str(PYTHON), "-B", str(BUS), "hello", "--role", role],
            cwd=ROOT, env=env, capture_output=True, text=True, timeout=30,
        )
        if completed.returncode:
            raise RuntimeError("agentbus hello failed: " + completed.stderr[-1000:])
        if restore_registered_codex_session(session_id) != role:
            raise RuntimeError("new Codex role did not complete exact host binding")

    _start_messenger(role, session_id, cwd)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"agent-bus Codex startup failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
