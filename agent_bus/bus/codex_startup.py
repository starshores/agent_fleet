"""Explicit one-shot role assignment for new Codex threads.

Codex SessionStart includes a session id and cwd but no agent-bus role. A pending
assignment is scoped to an exact project cwd and atomically consumed by one new
session; it is never inferred from a shared host PID.
"""
import json
import math
import os
import time
from pathlib import Path

from bus.config import BUS, _now
from bus.io import _write_json, json_lock
from bus.proc import _host_ancestor, _host_session_id
from bus.roster import _load_agents


ASSIGNMENT = BUS / "codex_role_next.json"
SESSION_ROLES = BUS / "codex_session_roles.json"
TTL_SECONDS = 1800


def _norm_cwd(value):
    return os.path.normcase(os.path.abspath(os.path.expanduser(str(value))))


def _valid_identity_text(value):
    return isinstance(value, str) and bool(value.strip()) and value == value.strip()


def prepare_session_start_identity(session_id):
    """Use Codex's SessionStart id when the hook has no inherited ID variable."""
    if not _valid_identity_text(session_id):
        raise ValueError("SessionStart has no exact Codex session id")
    inherited = _host_session_id("codex")
    if inherited and inherited != session_id:
        raise RuntimeError("SessionStart id conflicts with the inherited Codex session")
    os.environ["CODEX_SESSION_ID"] = session_id


def _unexpired(ticket):
    deadline = ticket.get("expires_epoch") if isinstance(ticket, dict) else None
    return (type(deadline) in (int, float) and math.isfinite(deadline)
            and deadline > time.time())


def _eligible_role(agents, role):
    if not isinstance(agents, dict) or not _valid_identity_text(role):
        return False
    record = agents.get(role)
    return (isinstance(record, dict) and isinstance(record.get("caps"), list)
            and "self-wake" in record["caps"])


def _read_json(path):
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def registered_codex_role(session_id):
    """Read a durable, exact Codex session binding; never infer from cwd or PID."""
    if not _valid_identity_text(session_id):
        return None
    sessions = _read_json(SESSION_ROLES).get("sessions")
    if not isinstance(sessions, dict):
        return None
    row = sessions.get(session_id)
    return row.get("role") if isinstance(row, dict) else None


def record_codex_session_role(role, session_id, source="hello"):
    """Persist an exact registration separately from volatile presence heartbeats."""
    if role not in _load_agents() or not _valid_identity_text(session_id):
        raise ValueError("invalid Codex session-role registration")
    with json_lock(SESSION_ROLES):
        ledger = _read_json(SESSION_ROLES)
        sessions = ledger.get("sessions")
        if not isinstance(sessions, dict):
            sessions = {}
        old = sessions.get(session_id)
        if isinstance(old, dict) and old.get("role") not in (None, role):
            raise ValueError("Codex session is already registered to another role")
        if not isinstance(old, dict) or not old.get("role"):
            sessions[session_id] = {"role": role, "registered": _now(), "source": source}
            _write_json(SESSION_ROLES, {"schema": 1, "sessions": sessions})


def resume_role_for_session(session_id):
    """Use durable registration only while the role's last exact session agrees."""
    role = registered_codex_role(session_id)
    if not role or role not in _load_agents():
        return None
    role_dir = BUS / role
    if (role_dir / "parked.json").exists():
        return None
    presence = _read_json(role_dir / "presence.json")
    if (presence.get("host_session_id") != session_id
            or presence.get("host_kind") not in (None, "codex")):
        return None
    # A heartbeat that lost its host kind is accepted only with independent
    # evidence that this exact Codex thread previously owned the messenger.
    if presence.get("host_kind") is None:
        state = _read_json(role_dir / "messenger.state.json")
        if state.get("session_id") != session_id:
            return None
    for other in _load_agents():
        if other != role and _read_json(BUS / other / "presence.json").get("host_session_id") == session_id:
            return None
    return role


def restore_registered_codex_session(session_id):
    """Rebind only inside this exact resumed Codex hook process."""
    role = resume_role_for_session(session_id)
    if not role:
        return None
    kind, pid, _, _ = _host_ancestor()
    env_sid = _host_session_id("codex")
    if kind != "codex" or not pid or env_sid != session_id:
        return None
    current = _read_json(BUS / role / "presence.json")
    if (current.get("host_kind") == "codex"
            and current.get("host_session_id") == session_id
            and current.get("host_pid") == pid):
        return role
    # SessionStart supplies the opaque session id. A child of the actual Codex
    # host may use it for this one exact rebind, never for another role.
    os.environ["CODEX_SESSION_ID"] = session_id
    from bus.presence import _touch_presence
    _touch_presence(role, "idle", expected_host_session_id=session_id)
    presence = _read_json(BUS / role / "presence.json")
    if (presence.get("host_kind") != "codex" or presence.get("host_pid") != pid
            or presence.get("host_session_id") != session_id):
        raise RuntimeError("Codex resume presence rebind did not complete")
    return role


def _assignment_registered(ticket):
    """A reserved ticket can be replaced only after hello has recorded it."""
    if not _valid_identity_text(ticket.get("role")) or not _valid_identity_text(ticket.get("session_id")):
        return False
    try:
        presence = json.loads((BUS / ticket["role"] / "presence.json").read_text(encoding="utf-8"))
    except Exception:
        return False
    return (isinstance(presence, dict) and presence.get("host_kind") == "codex"
            and presence.get("host_session_id") == ticket["session_id"])


def cmd_codex_role_next(args):
    role = args.role
    agents = _load_agents()
    if role not in agents:
        print(json.dumps({"error": f"unknown role: {role}"}, ensure_ascii=False))
        raise SystemExit(2)
    if "self-wake" not in (agents.get(role) or {}).get("caps", []):
        print(json.dumps({"error": f"role {role} does not hold self-wake capability"}, ensure_ascii=False))
        raise SystemExit(2)

    cwd = _norm_cwd(args.cwd)
    with json_lock(ASSIGNMENT):
        current = {}
        try:
            current = json.loads(ASSIGNMENT.read_text(encoding="utf-8"))
        except Exception:
            pass
        if (_unexpired(current) and (current.get("status") == "pending"
                or (current.get("status") == "claimed" and not _assignment_registered(current)))):
            print(json.dumps({"error": "a Codex role assignment is pending or awaiting registration",
                              "role": current.get("role"), "cwd": current.get("cwd")}, ensure_ascii=False))
            raise SystemExit(2)
        obj = {"status": "pending", "role": role, "cwd": cwd,
               "created": _now(), "expires_epoch": time.time() + TTL_SECONDS}
        _write_json(ASSIGNMENT, obj)
    print(json.dumps({"armed": True, "role": role, "cwd": cwd,
                      "expires_in_seconds": TTL_SECONDS}, ensure_ascii=False))


def claim_role_assignment(session_id, cwd):
    """Atomically reserve a ticket before exposing its role context.

    The context and startup hooks may arrive in either order. Reentry is valid
    only for the same session and cwd while the ticket and role grant are valid.
    A claimed ticket never authorizes another session or a different workspace.
    """
    if not _valid_identity_text(session_id) or not _valid_identity_text(cwd):
        return None
    with json_lock(ASSIGNMENT):
        try:
            obj = json.loads(ASSIGNMENT.read_text(encoding="utf-8"))
        except Exception:
            return None
        if not isinstance(obj, dict) or obj.get("status") not in ("pending", "claimed"):
            return None
        if not _unexpired(obj) or obj.get("cwd") != _norm_cwd(cwd):
            return None
        role = obj.get("role")
        if not _eligible_role(_load_agents(), role):
            return None
        if obj["status"] == "claimed":
            return role if obj.get("session_id") == session_id else None
        obj.update({"status": "claimed", "session_id": session_id, "claimed": _now()})
        _write_json(ASSIGNMENT, obj)
        return role
