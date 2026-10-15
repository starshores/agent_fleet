"""Read Codex rollout token telemetry without retaining or returning conversation text.

The source is local Codex session JSONL. Only token usage/context counters and
model metadata are extracted; message text, tool input, and tool output are
never returned to the WebUI.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

CODEX_HOME = Path(os.environ.get("CODEX_HOME") or (Path.home() / ".codex"))
_ACTIVE_MIN = 90
_TAIL_BYTES = 512 * 1024
_INDEX_TTL = 60.0
_USAGE_TTL = 300.0
_lock = threading.Lock()
_index_cache = {"ts": 0.0, "scope": None, "paths": {}}
_usage_cache = {"ts": 0.0, "scope": None, "data": None, "files": {}}
_model_cache = {}  # exact session id -> (checked_at, model); long turns can exceed tail window
_SESSION_ID = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", re.I)


def _session_ids(values) -> frozenset[str]:
    if isinstance(values, str):
        values = [values]
    return frozenset(value.lower() for value in (values or ())
                     if isinstance(value, str) and _SESSION_ID.fullmatch(value))


def _registered_presences(bus_root: Path | None) -> list[dict]:
    """Fail closed: only an in-roster role's exact Codex binding grants access."""
    if bus_root is None:
        return []
    root = Path(bus_root)
    try:
        roster = json.loads((root / "agents.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(roster, dict):
        return []
    rows = []
    for role in roster:
        if not isinstance(role, str) or not role or role.startswith((".", "_")) or any(c in role for c in "/\\:"):
            continue
        path = root / role / "presence.json"
        try:
            if not path.resolve().is_relative_to(root.resolve()):
                continue
            presence = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(presence, dict) or presence.get("host_kind") != "codex" or presence.get("role") != role:
            continue
        ids = _session_ids([presence.get("host_session_id")])
        if ids:
            rows.append({**presence, "host_session_id": next(iter(ids))})
    return rows


def _allowed_sessions(bus_root, allowed_session_ids) -> frozenset[str]:
    explicit = _session_ids(allowed_session_ids)
    if bus_root is None:
        return explicit
    registered = frozenset(row["host_session_id"] for row in _registered_presences(bus_root))
    return registered if allowed_session_ids is None else registered & explicit


def _scope_key(ids):
    return str(CODEX_HOME.resolve()), tuple(sorted(ids))


def _local_date(value: str) -> str | None:
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return (dt.astimezone(timezone(timedelta(hours=8)))).date().isoformat()
    except Exception:
        return None


def _rollout_index(allowed_session_ids=(), force: bool = False) -> dict[str, Path]:
    ids = _session_ids(allowed_session_ids)
    scope = _scope_key(ids)
    now = time.time()
    with _lock:
        if _index_cache["scope"] != scope:
            _index_cache.update(ts=0.0, scope=scope, paths={})
        if not force and now - _index_cache["ts"] < _INDEX_TTL:
            return dict(_index_cache["paths"])
    found: dict[str, Path] = {}
    for root_name in (("sessions", "archived_sessions") if ids else ()):
        root = CODEX_HOME / root_name
        if not root.exists():
            continue
        try:
            # Enumerate matching filenames only; never open unrelated rollouts.
            for sid in ids:
                for path in root.rglob(f"rollout-*-{sid}.jsonl"):
                    if not path.is_file() or not path.resolve().is_relative_to(root.resolve()):
                        continue
                    prev = found.get(sid)
                    if prev is None or path.stat().st_mtime_ns > prev.stat().st_mtime_ns:
                        found[sid] = path
        except Exception:
            continue
    with _lock:
        _index_cache.update(ts=now, scope=scope, paths=found)
    return found


def _usage_fields(usage: dict) -> dict:
    inp = int(usage.get("input_tokens") or 0)
    cached = int(usage.get("cached_input_tokens") or 0)
    out = int(usage.get("output_tokens") or 0)
    reasoning = int(usage.get("reasoning_output_tokens") or 0)
    # Codex input_tokens already includes cached_input_tokens; cache is a subset,
    # so adding it again would double count. Its total_tokens is input + output.
    return {"input": inp, "cached_input": cached, "output": out,
            "reasoning": reasoning, "total": int(usage.get("total_tokens") or (inp + out))}


def _read_rollout_usage(path: Path) -> tuple[dict, dict, str | None]:
    """Return daily/model counters and latest model; never retain transcript items."""
    daily: dict[str, dict] = {}
    by_model: dict[str, dict] = {}
    model = None
    records = []
    snapshots = []
    try:
        with path.open("rb") as stream:
            for raw in stream:
                try:
                    event = json.loads(raw)
                except Exception:
                    continue
                typ = event.get("type")
                payload = event.get("payload") or {}
                if typ == "turn_context":
                    model = payload.get("model") or payload.get("model_slug") or model
                if typ == "token_usage_record":
                    usage = payload.get("usage")
                    date = _local_date(event.get("timestamp") or "")
                    if isinstance(usage, dict) and date:
                        records.append((date, model or "Codex (model unknown)", _usage_fields(usage)))
                elif typ == "event_msg" and payload.get("type") == "token_count":
                    info = payload.get("info") or {}
                    total = info.get("total_token_usage")
                    date = _local_date(event.get("timestamp") or "")
                    if isinstance(total, dict) and date:
                        snapshots.append((date, model or "Codex (model unknown)", _usage_fields(total)))
    except Exception:
        return daily, by_model, model

    if records:
        for date, mdl, fields in records:
            _add(daily, date, fields)
            _add(by_model, mdl, fields)
    else:
        # Older Codex rollouts may have cumulative token_count snapshots only.
        # Convert each monotonic delta once; a reset starts a new segment.
        previous = {"input": 0, "cached_input": 0, "output": 0, "reasoning": 0, "total": 0}
        for date, mdl, current in snapshots:
            delta = {}
            for key, value in current.items():
                delta[key] = value - previous[key] if value >= previous[key] else value
            previous = current
            if delta["total"] <= 0:
                continue
            _add(daily, date, delta)
            _add(by_model, mdl, delta)
    return daily, by_model, model


def _add(target: dict, key: str, fields: dict, calls: int = 1) -> None:
    row = target.setdefault(key, {"input": 0, "cached_input": 0, "output": 0,
                                  "reasoning": 0, "tokens_total": 0, "tokens_io": 0,
                                  "calls": 0})
    row["input"] += fields["input"]
    row["cached_input"] += fields["cached_input"]
    row["output"] += fields["output"]
    row["reasoning"] += fields["reasoning"]
    row["tokens_total"] += fields["total"]
    row["tokens_io"] += fields["input"] + fields["output"]
    row["calls"] += calls


def historical_usage(force: bool = False, *, bus_root: Path | None = None,
                     allowed_session_ids=None) -> dict:
    """Aggregate exact authorized sessions; no scope means no history access.

    bus_root uses the current roster and presence bindings on every call. Explicit
    IDs alone are supported; when both are given they narrow the roster scope.
    """
    explicit = None if allowed_session_ids is None else _session_ids(allowed_session_ids)
    ids = _allowed_sessions(bus_root, explicit)
    scope = _scope_key(ids)
    now = time.time()
    with _lock:
        if _usage_cache["scope"] != scope:
            _usage_cache.update(ts=0.0, scope=scope, data=None, files={})
        cached = _usage_cache["data"]
        if cached is not None and not force and now - _usage_cache["ts"] < _USAGE_TTL:
            return cached
        old_files = dict(_usage_cache["files"])
    files: dict[str, tuple[int, int, dict, dict]] = {}
    all_daily: dict[str, dict] = {}
    all_models: dict[str, dict] = {}
    paths = _rollout_index(ids, force=force)
    all_sessions: dict[str, dict] = {}
    session_rows = {}
    for sid, path in paths.items():
        try:
            st = path.stat()
        except OSError:
            continue
        key = str(path)
        cached_file = old_files.get(key)
        if not force and cached_file and cached_file[0] == st.st_mtime_ns and cached_file[1] == st.st_size:
            dday, dmodel = cached_file[2], cached_file[3]
        else:
            dday, dmodel, _ = _read_rollout_usage(path)
        files[key] = (st.st_mtime_ns, st.st_size, dday, dmodel)
        session_rows[sid] = (dday, dmodel)
    # A binding can be revoked while a file is being parsed. Drop it before
    # publishing or caching the result; newly authorized IDs wait for next call.
    final_ids = ids & _allowed_sessions(bus_root, explicit)
    files = {str(paths[sid]): files[str(paths[sid])] for sid in final_ids
             if sid in session_rows}
    for sid in final_ids:
        if sid not in session_rows:
            continue
        dday, dmodel = session_rows[sid]
        all_sessions[sid] = _sum_rows(dday.values())
        for date, row in dday.items():
            _add(all_daily, date, {"input": row["input"], "cached_input": row["cached_input"],
                                   "output": row["output"], "reasoning": row["reasoning"],
                                   "total": row["tokens_total"]}, calls=row["calls"])
        for mdl, row in dmodel.items():
            _add(all_models, mdl, {"input": row["input"], "cached_input": row["cached_input"],
                                   "output": row["output"], "reasoning": row["reasoning"],
                                   "total": row["tokens_total"]}, calls=row["calls"])
    daily = [{"date": date, "src": "codex", **row} for date, row in sorted(all_daily.items())]
    data = {"ok": True, "source": "local Codex rollout token_usage_record / token_count",
            "as_of": time.strftime("%Y-%m-%dT%H:%M:%S"), "sessions": len(all_sessions),
            "daily": daily, "by_model": [{"model": mdl, **row} for mdl, row in sorted(all_models.items())],
            "totals": _sum_rows(all_daily.values()), "by_session": all_sessions}
    with _lock:
        _usage_cache.update(ts=now, scope=_scope_key(final_ids), data=data, files=files)
        if final_ids != ids:
            _index_cache.update(ts=0.0, scope=None, paths={})
    return data


def _sum_rows(rows) -> dict:
    out = {"input": 0, "cached_input": 0, "output": 0, "reasoning": 0,
           "tokens_total": 0, "tokens_io": 0, "calls": 0}
    for row in rows:
        for key in out:
            out[key] += row.get(key, 0) or 0
    return out


def _latest_model_backscan(path: Path, sid: str, now: float) -> str | None:
    """Find model metadata in a bounded exact-session tail; never retain content."""
    with _lock:
        cached = _model_cache.get(sid)
    if cached and now - cached[0] < 300:
        return cached[1]
    model = None
    try:
        with path.open("rb") as stream:
            size = stream.seek(0, 2)
            start = max(0, size - 16 * 1024 * 1024)
            stream.seek(start)
            lines = stream.read().splitlines()
        if start:
            lines = lines[1:]  # discard a possibly truncated first JSONL record
        for raw in reversed(lines):
            if b"turn_context" not in raw:
                continue
            try:
                event = json.loads(raw)
            except ValueError:
                continue
            if event.get("type") == "turn_context":
                payload = event.get("payload") or {}
                model = payload.get("model") or payload.get("model_slug")
                if model:
                    break
    except OSError:
        pass
    with _lock:
        _model_cache[sid] = (now, model)
    return model


def active_context(bus_root: Path, active_min: int = _ACTIVE_MIN, *,
                   allowed_session_ids=None, force: bool = False) -> list[dict]:
    """Read only rollouts for exact Codex session IDs registered in bus presence."""
    presences = _registered_presences(bus_root)
    ids = frozenset(row["host_session_id"] for row in presences)
    if allowed_session_ids is not None:
        ids &= _session_ids(allowed_session_ids)
    index = _rollout_index(ids, force=force)
    now = time.time()
    rows = []
    for presence in presences:
        sid = presence["host_session_id"]
        path = index.get(str(sid or "").lower())
        if not sid or path is None:
            continue
        try:
            stat = path.stat()
            if now - stat.st_mtime > active_min * 60:
                continue
            with path.open("rb") as stream:
                stream.seek(max(0, stat.st_size - _TAIL_BYTES))
                lines = stream.read().decode("utf-8", "replace").splitlines()
        except Exception:
            continue
        model = "Codex (model unknown)"
        usage = None
        stamp = ""
        window = 0
        for line in reversed(lines):
            try:
                event = json.loads(line)
            except Exception:
                continue
            if event.get("type") == "event_msg":
                payload = event.get("payload") or {}
                if usage is None and payload.get("type") == "token_count":
                    info = payload.get("info") or {}
                    candidate = info.get("last_token_usage")
                    if not isinstance(candidate, dict):
                        continue
                    usage = candidate
                    window = info.get("model_context_window") or 0
                    stamp = event.get("timestamp") or stamp
            elif event.get("type") == "turn_context" and usage is not None:
                payload = event.get("payload") or {}
                model = payload.get("model") or payload.get("model_slug") or model
                break
        if model == "Codex (model unknown)":
            model = _latest_model_backscan(path, sid, now) or model
        else:
            with _lock:
                _model_cache[sid] = (now, model)
        if not isinstance(usage, dict):
            continue
        try:
            ctx = _usage_fields(usage)["total"]
            window = int(window or 0)
        except (TypeError, ValueError):
            continue
        if window <= 0:
            continue
        pct = round(100.0 * ctx / window, 1)
        rows.append({"source": "codex", "role": presence.get("role"), "role_live": None,
                     "role_src": "host_session_id", "pct": pct, "level": "info",
                     "ctx": ctx, "win": window, "win_known": True,
                     "compact_at": None, "margin": None, "model": model,
                     "sid": str(sid)[:8], "sid_full": sid, "ts": stamp,
                     "proj": presence.get("cwd") or "", "is_agent": True,
                     "compact_threshold_known": False})
    current = {(p["role"], p["host_session_id"]) for p in _registered_presences(bus_root)}
    return [row for row in rows if (row["role"], row["sid_full"]) in current]
