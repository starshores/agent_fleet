"""Repair a recently lost, idle Codex messenger without replaying bus work.

The local WebUI runs this one-shot guard. It reads role presence and task state,
never Codex transcripts, and never derives a role from a shared process ID.
"""
from __future__ import annotations

import argparse
import json
import stat
import sys
from datetime import datetime
from pathlib import Path

import psutil


ROOT = Path(__file__).resolve().parents[1]
BUS = ROOT / "agent_bus"
sys.path.insert(0, str(BUS))
sys.path.insert(0, str(ROOT / ".codex" / "hooks"))

from bus.roster import load_role_profiles  # noqa: E402
from bus.io import json_lock, _write_json, _gen_id  # noqa: E402
from bus.selfwake import _codex_messenger_evidence, _codex_messenger_state  # noqa: E402
from bus.codex_startup import resume_role_for_session  # noqa: E402
from bus.core import _deliver  # noqa: E402
from agentbus_codex_session_start import _role_for_session, _start_messenger  # noqa: E402


def _read(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _age(value) -> float | None:
    try:
        return (datetime.now() - datetime.fromisoformat(str(value)[:19])).total_seconds()
    except (TypeError, ValueError):
        return None


def _live_messenger(pid, role: str, sid: str) -> bool:
    try:
        args = psutil.Process(int(pid)).cmdline()
        return ("--role" in args and role in args and "--session-id" in args
                and sid in args and any(Path(x).name.lower() == "codex_messenger.py" for x in args))
    except (TypeError, ValueError, psutil.Error):
        return False


def _live_host(pid) -> bool:
    """Liveness gate only; the exact session binding is checked separately."""
    try:
        proc = psutil.Process(int(pid))
        return (proc.is_running() and proc.name().lower() == "codex.exe"
                and "app-server" in proc.cmdline())
    except (TypeError, ValueError, psutil.Error):
        return False


def _recent_idle_candidate(role: str, cwd: str) -> tuple[str, str] | None:
    role_dir = BUS / role
    if (role_dir / "parked.json").exists() or not cwd or not Path(cwd).is_dir():
        return None
    presence = _read(role_dir / "presence.json")
    state = _read(role_dir / "messenger.state.json")
    sid = presence.get("host_session_id")
    if (presence.get("host_kind") != "codex" or not isinstance(sid, str) or not sid
            or presence.get("mode") != "listening" or state.get("session_id") != sid):
        return None
    if _role_for_session(sid) != role or resume_role_for_session(sid) != role:
        return None
    # Bounded recency prevents reviving a closed tab merely because another
    # thread shares the still-running App Server process.
    last_seen = _age(presence.get("last_seen"))
    beat_age = _age(state.get("heartbeat"))
    if (not _live_host(presence.get("host_pid")) or last_seen is None
            or last_seen < 0 or last_seen > 300 or beat_age is None
            or beat_age < 0 or beat_age > 300):
        return None
    if state.get("status") == "armed" and beat_age <= 35:
        return None
    if _live_messenger(state.get("pid"), role, sid):
        return None  # A degraded but live messenger must not gain a rival.
    if any((role_dir / "processing").glob("*.json")):
        return None  # An unfinished or uncertain turn is never replayed.
    turn = _read(role_dir / "turn_state.json")
    if turn.get("session_id") == sid and turn.get("state") in {"thinking", "tool"}:
        return None
    return sid, cwd


def _record(role: str, sid: str, outcome: str, detail: str = "") -> None:
    row = {"at": datetime.now().isoformat(timespec="seconds"), "role": role,
           "session_id": sid, "outcome": outcome, "detail": detail[:300]}
    with (BUS / "codex_messenger_guard.jsonl").open("a", encoding="utf-8") as out:
        out.write(json.dumps(row, ensure_ascii=False) + "\n")


def _note_evidence_gap(role: str, sid: str, reason: str | None) -> None:
    """Persist local diagnosis only; never fabricate heartbeat or claim evidence.

    Append one guard event per reason/session transition, while refreshing the
    most recent observation in the role's dedicated diagnostic record.
    """
    role_dir = BUS / role
    if (not isinstance(role, str) or not role or role in {".", ".."}
            or any(c in role for c in '/\\:\x00') or not role_dir.is_dir()
            or role_dir.resolve().parent != BUS.resolve()):
        raise ValueError("Invalid local diagnostic directory")
    path = role_dir / "messenger.diagnostic.json"
    if path.exists() or path.is_symlink():
        metadata = path.lstat()
        if (path.is_symlink() or metadata.st_nlink > 1
                or getattr(metadata, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT):
            raise ValueError("Refusing redirected diagnostic record")
    with json_lock(path):
        previous = _read(path)
        stamp = datetime.now().isoformat(timespec="seconds")
        if reason is None:
            if previous.get("status") != "blocked":
                return
            same_session = previous.get("session_id") == sid
            previous.update(status="resolved" if same_session else "superseded",
                            last_checked=stamp, resolved_at=stamp)
            _write_json(path, previous)
            _record(role, sid, "evidence_restored" if same_session else "evidence_binding_changed",
                    "state_evidence_available" if same_session else "previous_diagnostic_superseded")
            return
        same = (previous.get("status") == "blocked" and previous.get("session_id") == sid
                and previous.get("reason") == reason)
        value = {"role": role, "session_id": sid, "status": "blocked", "reason": reason,
                 "first_seen": previous.get("first_seen", stamp) if same else stamp,
                 "last_checked": stamp, "automatic_recovery": False}
        _write_json(path, value)
        if not same:
            _record(role, sid, "skipped_evidence", reason)


def _report_recovery_failure(role: str, sid: str, reason: str, *, immediate: bool = False) -> None:
    """Stage one exact-session incident for a fleet-wide, deduplicated report."""
    path = BUS / role / "messenger.guard.report.json"
    with json_lock(path):
        previous = _read(path)
        stamp = datetime.now().isoformat(timespec="seconds")
        if previous.get("session_id") != sid or previous.get("status") == "resolved":
            previous = {"session_id": sid, "first_seen": stamp, "status": "observing"}
        if previous.get("status") == "reported":
            return
        new_immediate = bool(immediate or previous.get("immediate"))
        if (previous.get("reason") != reason[:500]
                or bool(previous.get("immediate")) != new_immediate
                or "last_checked" not in previous):
            previous.update(last_checked=stamp, reason=reason[:500], immediate=new_immediate)
            _write_json(path, previous)


def _flush_failure_reports(roles) -> None:
    """Send one MA task for the ready incidents in this scan, not one per role."""
    batch_path = BUS / "codex_messenger_guard.alerts.json"
    with json_lock(batch_path):
        ready = []
        for role in roles:
            row = _read(BUS / role / "messenger.guard.report.json")
            if row.get("status") != "observing":
                continue
            age = _age(row.get("first_seen"))
            # Let overlapping 45s/180s scans join the same incident batch.
            threshold = 30 if row.get("immediate") else 90
            if age is not None and age >= threshold:
                ready.append((role, row))
        if not ready:
            return
        tid = _gen_id("MasterAgent")
        stamp = datetime.now().isoformat(timespec="seconds")
        lines = [f"- {role} | session={row.get('session_id') or '缺失'} | {str(row.get('reason') or '原因未知')[:180]}"
                 for role, row in ready[:40]]
        if len(ready) > 40:
            lines.append(f"- 其余 {len(ready) - 40} 个角色见各自 messenger.guard.report.json")
        task = {
            "id": tid, "from": "bus守卫", "to": "MasterAgent", "kind": "urgent",
            "no_receipt": True, "title": f"Codex 信使恢复异常汇总（{len(ready)} 角色）",
            "task": ("自醒守卫已尝试安全恢复或核查恢复条件，以下投递链仍未确认。"
                     "请按精确会话核查；守卫没有重放在飞任务或手动挂 wait。\n"
                     + "\n".join(lines)),
            "created": stamp, "status": "pending",
        }
        _deliver("MasterAgent", "bus守卫", task, audit_event="codex_guard_report")
        for role, observed in ready:
            path = BUS / role / "messenger.guard.report.json"
            with json_lock(path):
                current = _read(path)
                if (current.get("status") == "observing"
                        and current.get("session_id") == observed.get("session_id")
                        and current.get("first_seen") == observed.get("first_seen")):
                    current.update(status="reported", reported_at=stamp, report_id=tid)
                    _write_json(path, current)
        _record("*", "", "reported_batch", f"{len(ready)} roles; report_id={tid}")


def _resolve_report(role: str, sid: str) -> None:
    path = BUS / role / "messenger.guard.report.json"
    if not path.exists():
        return
    with json_lock(path):
        previous = _read(path)
        if previous.get("session_id") == sid and previous.get("status") != "resolved":
            previous.update(status="resolved", resolved_at=datetime.now().isoformat(timespec="seconds"))
            _write_json(path, previous)


def _recovery_blocker(role: str, sid: str, cwd: str) -> str:
    role_dir = BUS / role
    presence = _read(role_dir / "presence.json")
    state = _read(role_dir / "messenger.state.json")
    if not cwd or not Path(cwd).is_dir():
        return "role workspace unavailable"
    if not sid or resume_role_for_session(sid) != role:
        return "durable exact-session binding unavailable"
    if state.get("session_id") != sid:
        return "messenger state does not match the exact session"
    if any((role_dir / "processing").glob("*.json")):
        return "unfinished processing exists; automatic replay is forbidden"
    turn = _read(role_dir / "turn_state.json")
    if turn.get("session_id") == sid and turn.get("state") in {"thinking", "tool"}:
        return "model turn is active"
    if _live_messenger(state.get("pid"), role, sid):
        return "messenger process is live but delivery is degraded"
    if not _live_host(presence.get("host_pid")):
        return "Codex App Server host is unavailable"
    if (_age(presence.get("last_seen")) is None or _age(presence.get("last_seen")) > 300
            or _age(state.get("heartbeat")) is None or _age(state.get("heartbeat")) > 300):
        return "session evidence is too old for safe automatic recovery"
    return "safe recovery preconditions are not met"


def _guard_role(role: str, cwd: str, *, allow_recovery: bool = True) -> str:
    role_dir = BUS / role
    presence = _read(role_dir / "presence.json")
    sid = presence.get("host_session_id")
    sid = sid if isinstance(sid, str) else ""
    if (role_dir / "parked.json").exists():
        return "skipped"
    if presence.get("host_kind") != "codex":
        # A resumed session may have lost its volatile host kind. The durable
        # exact-session ledger plus its previous messenger are needed to label
        # it Codex; without a current Codex host binding, do not launch.
        if not sid or resume_role_for_session(sid) != role:
            return "skipped"
        _report_recovery_failure(role, sid, "Codex presence host_kind missing; exact resume/rebind required")
        return "blocked"
    _, evidence_reason = _codex_messenger_evidence(role, sid)
    _note_evidence_gap(role, sid, evidence_reason)
    health, detail = _codex_messenger_state(role, sid)
    if health == "armed":
        _resolve_report(role, sid)
        return "healthy"
    candidate = _recent_idle_candidate(role, cwd)
    if candidate is None:
        reason = "; ".join(filter(None, (evidence_reason or detail,
                                         _recovery_blocker(role, sid, cwd))))
        _report_recovery_failure(role, sid, reason)
        return "blocked"
    if not allow_recovery:
        return "deferred"  # Inspect all roles, but launch at most one messenger per scan.
    sid, cwd = candidate
    attempt_path = role_dir / "messenger.guard.json"
    with json_lock(attempt_path):
        prior = _read(attempt_path)
        prior_age = _age(prior.get("at"))
        if prior.get("session_id") == sid and prior_age is not None and 0 <= prior_age < 180:
            return "cooldown"
        _write_json(attempt_path, {"session_id": sid,
                                   "at": datetime.now().isoformat(timespec="seconds")})
    if _recent_idle_candidate(role, cwd) != candidate:
        return "changed"
    try:
        _start_messenger(role, sid, cwd)
        new_state = _read(role_dir / "messenger.state.json")
        if (new_state.get("session_id") != sid or new_state.get("status") != "armed"
                or not _live_messenger(new_state.get("pid"), role, sid)
                or _codex_messenger_state(role, sid)[0] != "armed"):
            raise RuntimeError("replacement messenger did not establish the delivery chain")
        _record(role, sid, "rearmed")
        _resolve_report(role, sid)
        return "rearmed"
    except Exception as exc:
        reason = f"messenger launch failed: {exc}"
        _record(role, sid, "failed", reason)
        _report_recovery_failure(role, sid, reason, immediate=True)
        return "failed"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--role", help="inspect one registered role")
    args = parser.parse_args()
    if _read(BUS / "maintenance.json").get("active"):
        return 0  # Maintenance mode intentionally permits stopped agents.
    profiles = {p.get("role"): p for p in load_role_profiles().get("roles", []) if p.get("role")}
    agents = _read(BUS / "agents.json")
    selected_roles = [role for role in agents if not args.role or role == args.role]
    attempted = 0
    for role in selected_roles:
        profile = profiles.get(role) or {}
        cwd = profile.get("cwd")
        if not isinstance(cwd, str) or not cwd:
            cwd = ""
        try:
            outcome = _guard_role(role, cwd, allow_recovery=attempted < 1)
            if outcome in {"rearmed", "failed"}:
                attempted += 1
        except Exception as exc:
            sid = _read(BUS / role / "presence.json").get("host_session_id") or ""
            _record(role, sid, "guard_error", str(exc))
            try:
                _report_recovery_failure(role, sid, f"guard error: {exc}", immediate=True)
            except Exception as report_exc:
                print(f"Codex guard could not report {role}: {report_exc}", file=sys.stderr)
    _flush_failure_reports(selected_roles)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
