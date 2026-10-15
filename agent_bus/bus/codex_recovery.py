"""Conservative replay fences for the legacy Codex bridge.

These markers are a compatibility safety barrier, not V3 attempts or leases.
A claimed task remains protected across bridge/host death and role rebindings.
There is deliberately no automatic retry or marker deletion in this module.
"""
import json
import os
import re
import uuid

from bus.config import BUS, _now
from bus.io import _write_json, json_lock


def _segment(value):
    if (not isinstance(value, str) or not value or value in {".", ".."}
            or value[-1:] in {" ", "."} or any(c in value for c in '/\\:\x00')
            or any(ord(c) < 32 for c in value)):
        raise ValueError("Invalid role or task path component")
    return value


def _marker(role, task_id):
    return BUS / _segment(role) / "_codex_recovery" / (_segment(task_id) + ".json")


def is_codex_role(role):
    try:
        value = json.loads((BUS / _segment(role) / "presence.json").read_text(encoding="utf-8"))
        return value.get("host_kind") == "codex"
    except (OSError, ValueError, AttributeError):
        return False


def claim_blocked(role, task_id, task=None):
    """Even a copied/requeued inbox item must not bypass a durable fence."""
    return (_marker(role, task_id).exists()
            or isinstance(task, dict) and (task.get("status") == "needs_reconcile"
                                          or bool(task.get("codex_recovery"))))


def requeue_blocked(role, task):
    """Protect new fenced claims and pre-upgrade Codex processing records."""
    task_id = task.get("id")
    if not isinstance(task_id, str) or not task_id:
        return is_codex_role(role) or task.get("status") == "needs_reconcile"
    return claim_blocked(role, task_id, task) or is_codex_role(role)


def verify_claim_identity(role, session_id):
    """Only the exact presence host-session binding authorizes Codex work."""
    if not isinstance(session_id, str) or not session_id or session_id != session_id.strip():
        raise RuntimeError("Codex claim requires an exact session identity")
    path = BUS / _segment(role) / "presence.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    if value.get("host_kind") != "codex" or value.get("host_session_id") != session_id:
        raise RuntimeError("Codex claim session does not match role presence")


def protect_claim(role, task_id, *, required=False, session_id=None):
    """Durably protect a Codex claim before inbox movement or model delivery.

    Caller holds the existing inbox claim lock. A write error must prevent
    delivery, rather than fall back to an unprotected execution.
    """
    if not required and not is_codex_role(role):
        return None
    verify_claim_identity(role, session_id)
    path = _marker(role, task_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with json_lock(path):
        if path.exists():
            raise RuntimeError("Codex task already has a replay fence")
        value = {"schema_version": 1, "task_id": task_id, "role": role,
                 "claim_id": str(uuid.uuid4()), "state": "claimed",
                 "replay_policy": "manual", "host_session_id": session_id,
                 "created": _now()}
        _write_json(path, value)
    return {"claim_id": value["claim_id"], "replay_policy": "manual"}


def validate_delivery(role, session_id, items):
    """Reserve one model delivery from durable, session-bound claims.

    Partial reservation is conservative: the bridge fences the whole batch on
    any failure. Never manufacture a missing claim or reset dispatching state.
    """
    verify_claim_identity(role, session_id)
    if not items:
        raise RuntimeError("Codex delivery requires a nonempty task batch")
    seen = set()
    for item in items:
        task_id = _segment(item.get("id"))
        if task_id in seen:
            raise RuntimeError("Duplicate Codex task in delivery batch")
        seen.add(task_id)
        path = _marker(role, task_id)
        processing = BUS / _segment(role) / "processing" / (task_id + ".json")
        if not path.is_file() or not processing.is_file():
            raise RuntimeError("Codex delivery requires a persisted claim and processing task")
        with json_lock(path):
            verify_claim_identity(role, session_id)
            value = json.loads(path.read_text(encoding="utf-8"))
            saved = json.loads(processing.read_text(encoding="utf-8"))
            expected = (item.get("codex_recovery") or {}).get("claim_id")
            if (not expected or value.get("claim_id") != expected
                    or (saved.get("codex_recovery") or {}).get("claim_id") != expected
                    or value.get("role") != role or value.get("task_id") != task_id
                    or saved.get("id") != task_id or saved.get("status") != "processing"
                    or value.get("host_session_id") != session_id
                    or value.get("state") != "claimed"):
                raise RuntimeError("Codex delivery claim is missing, stale or already dispatched")
            value.update(state="dispatching", updated=_now())
            _write_json(path, value)
    verify_claim_identity(role, session_id)


def already_completed_claim(role, item):
    """Recognize a queued duplicate only with the same archived claim receipt."""
    task_id = _segment(item.get("id"))
    role_dir = BUS / _segment(role)
    if (role_dir / "processing" / (task_id + ".json")).exists():
        return False
    claim_id = (item.get("codex_recovery") or {}).get("claim_id")
    if not claim_id:
        return False
    try:
        archived = json.loads((role_dir / "archive" / (task_id + ".json")).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return (archived.get("id") == task_id
            and (archived.get("codex_recovery") or {}).get("claim_id") == claim_id
            and archived.get("status") in {"ok", "error"}
            and "result" in archived)


def _preserve_completed_processing(role, item):
    """Preserve a duplicate processing file only after its exact done is proven.

    ``done`` writes the sender receipt and archive before removing processing.
    On Windows that final unlink can fail under a transient file lock. The
    completed claim must not be replayed or mistaken for missing work.
    """
    try:
        role = _segment(role)
        task_id = _segment(item.get("id"))
        sender = _segment(item.get("from"))
        claim_id = (item.get("codex_recovery") or {}).get("claim_id")
        if not isinstance(claim_id, str) or not claim_id:
            return False
        role_dir = BUS / role
        processing = role_dir / "processing" / (task_id + ".json")
        archived = json.loads((role_dir / "archive" / (task_id + ".json")).read_text(encoding="utf-8"))
        receipt = json.loads((BUS / sender / "done" / (task_id + ".json")).read_text(encoding="utf-8"))
        if (archived.get("id") != task_id
                or (archived.get("codex_recovery") or {}).get("claim_id") != claim_id
                or archived.get("status") not in {"ok", "error"}
                or "result" not in archived
                or receipt.get("id") != task_id
                or receipt.get("from") != role or receipt.get("to") != sender
                or receipt.get("status") != archived.get("status")
                or receipt.get("result") != archived.get("result")):
            return False
        if not processing.exists():
            return True
        saved = json.loads(processing.read_text(encoding="utf-8"))
        if (saved.get("id") != task_id
                or (saved.get("codex_recovery") or {}).get("claim_id") != claim_id
                or saved.get("status") != "processing"):
            return False
        preserved_dir = role_dir / "_codex_recovery" / "completed_processing"
        preserved_dir.mkdir(parents=True, exist_ok=True)
        preserved = preserved_dir / (task_id + "." + uuid.uuid4().hex + ".json")
        os.replace(processing, preserved)
        return True
    except (OSError, ValueError, TypeError, AttributeError):
        return False


def fence_tasks(role, items, reason, *, include_missing=False):
    """Fence only unfinished processing records; leave done/archive untouched.

    A completion racing this diagnostic may leave an extra conservative marker,
    but this never recreates a processing file or overwrites a result.
    """
    if not isinstance(reason, str) or not re.fullmatch(r"[a-z][a-z0-9_-]{0,99}", reason):
        raise ValueError("Recovery reason must be a stable slug")
    fenced = []
    seen = set()
    for item in items:
        task_id = _segment(item.get("id"))
        if task_id in seen:
            continue
        seen.add(task_id)
        processing = BUS / _segment(role) / "processing" / (task_id + ".json")
        path = _marker(role, task_id)
        if not processing.is_file() and not (include_missing and path.is_file()):
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        with json_lock(path):
            if not processing.is_file() and not (include_missing and path.is_file()):
                continue
            if path.exists():
                # Corruption remains a blocking marker; do not destroy evidence.
                value = json.loads(path.read_text(encoding="utf-8"))
                if value.get("role") != role or value.get("task_id") != task_id:
                    raise RuntimeError("Codex recovery marker scope mismatch")
                expected = (item.get("codex_recovery") or {}).get("claim_id")
                if expected and value.get("claim_id") != expected:
                    raise RuntimeError("Codex recovery marker claim mismatch")
            else:
                value = {"schema_version": 1, "task_id": task_id, "role": role,
                         "claim_id": str(uuid.uuid4()), "created": _now()}
            value.update(state="needs_reconcile", replay_policy="manual",
                         reason=reason, updated=_now())
            _write_json(path, value)
            fenced.append(task_id)
    return fenced


def reconcile_unfinished(role, items, reason="missing_task_receipt"):
    unresolved = []
    for item in items:
        task_id = _segment(item.get("id"))
        role_dir = BUS / _segment(role)
        if _preserve_completed_processing(role, item):
            continue
        if not (role_dir / "processing" / (task_id + ".json")).exists():
            try:
                archived = json.loads((role_dir / "archive" / (task_id + ".json")).read_text(encoding="utf-8"))
                claim_id = (item.get("codex_recovery") or {}).get("claim_id")
                if (claim_id and archived.get("id") == task_id
                        and (archived.get("codex_recovery") or {}).get("claim_id") == claim_id
                        and archived.get("status") in {"ok", "error"}
                        and "result" in archived):
                    continue
            except (OSError, ValueError, AttributeError):
                pass
            try:
                sender = _segment(item.get("from"))
                receipt = json.loads((BUS / sender / "done" / (task_id + ".json")).read_text(encoding="utf-8"))
                if (receipt.get("id") == task_id and receipt.get("from") == role
                        and receipt.get("to") == sender and receipt.get("status") in {"ok", "error"}
                        and "result" in receipt):
                    continue
            except (OSError, ValueError, AttributeError):
                pass
        unresolved.append(item)
    fence_tasks(role, unresolved, reason, include_missing=True)
    return [item["id"] for item in unresolved]
