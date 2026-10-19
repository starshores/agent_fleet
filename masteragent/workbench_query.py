"""Bounded read-only projections for the local 3.1 workbench.

Each initialized response is read in one SQLite transaction. A selected
generation filters current durable records; it is not a historical snapshot.
No query starts a runtime, repairs state, scans business files, or loads a host
configuration. Administrative access to this local query surface is trusted.
"""
from __future__ import annotations

import base64
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import time

from . import __version__
from .errors import PlatformError
from .runtime import CAPABILITIES, APP_SERVER_CAPABILITIES
from .service import Platform


API_VERSION = "3.1-dev1"
_STATES = frozenset({"queued", "running", "waiting", "awaiting_decision",
                     "needs_reconcile", "completed", "failed", "cancelled"})
_SUMMARY = ("task_id", "role_id", "generation", "dispatch_generation", "state",
            "created_at", "completed_at", "parent_task_id")
_INSTALLATION = ("installation_id", "package_id", "version", "state", "generation", "installed_at")
_ATTEMPT = ("attempt_id", "task_id", "role_id", "generation", "ordinal", "tool_id",
            "expires_at", "state", "created_at", "finished_at")
_INPUT = ("input_id", "role_id", "generation", "attempt_id", "sequence", "text",
          "state", "created_at", "updated_at")
_DECISION = ("decision_id", "task_id", "generation", "title", "state", "revision",
             "expires_at", "answer", "created_at", "answered_at")
_MEMBER = ("member_id", "role_id", "generation", "tool_id", "state", "expires_at",
           "started_at", "updated_at", "stop_reason")
_RESULT = ("task_id", "role_id", "generation", "tool_id", "output", "status", "created_at", "attempt_id")
_NOTIFICATION = ("event_id", "task_id", "generation", "recipient_role", "kind", "state", "created_at", "delivered_at")
_DELIVERY = ("delivery_id", "task_id", "attempt_id", "generation", "destination_namespace",
             "destination_id", "state", "reason", "send_count", "created_at", "updated_at", "received_at")
_RECEIPT = ("receipt_id", "text_bytes", "text_sha256", "storage_revision", "received_at")
_CONTINUATION = ("continuation_id", "task_id", "from_generation", "to_generation", "reason", "created_at")
_HISTORY_SECTIONS = frozenset({"generations", "continuations", "attempts", "decisions", "results",
                               "inputs", "events", "notifications"})
_HISTORY_NOTE = ("This read-only task chain spans recorded generations. The task summary is its current durable "
                 "state, not a historical snapshot. Record pages are limited to record_generation; continuation "
                 "edges include both incoming and outgoing links. No historical record authorizes a write.")
_RUNTIME_KINDS = ("runtime_start_requested", "runtime_started", "runtime_thread", "runtime_progress",
                  "runtime_stopped", "runtime_cancel_requested", "runtime_observation",
                  "runtime_native_request_denied")
_USAGE_FIELDS = ("input_tokens", "cached_input_tokens", "output_tokens", "reasoning_output_tokens",
                 "total_tokens", "cache_write_input_tokens")
_MODEL_SOURCES = frozenset({"thread/start", "model/rerouted"})
_NATIVE_REQUEST_METHODS = frozenset({"item/commandExecution/requestApproval", "item/fileChange/requestApproval",
    "item/permissions/requestApproval", "mcpServer/elicitation/request", "item/tool/requestUserInput", "item/tool/call"})
_EVENT_FIELDS = {
    "claimed": ("ordinal",), "renewed": ("expires_at",),
    "completed": ("status",), "failed": ("status",),
    "completion_rejected": ("code",), "expired": ("effects",),
    "reconciled": ("resolution", "safe_to_retry"),
    "member_fenced": ("member_id", "effects"),
    "runtime_uncertain": ("effects",), "runtime_cancel_requested": (),
    "runtime_start_requested": ("runtime", "version", "model_requested", "model_observed", "supports_runtime_inputs"),
    "runtime_started": ("pid",), "runtime_thread": ("thread_id",),
    "runtime_progress": ("phase", "state", "code", "turn_id"),
    "runtime_observation": ("thread_id", "turn_id"),
    "runtime_native_request_denied": ("thread_id", "turn_id"),
    "runtime_stopped": ("outcome", "returncode", "termination_confirmed", "thread_id", "turn_id",
                        "model_requested", "model_observed", "controlled_shutdown"),
}
_SCOPE_NOTE = ("Tasks are selected by dispatch_generation. Related records are limited to the selected "
               "generation; this is not a historical point-in-time snapshot or a complete cross-generation chain.")


def _pick(row, fields):
    return {key: row[key] for key in fields}


def _delivery_view(row):
    try:
        receipt = json.loads(row["receipt"]) if row["receipt"] else None
    except (ValueError, TypeError):
        receipt = None
    return {**_pick(row, _DELIVERY),
            "receipt": {key: receipt[key] for key in _RECEIPT if key in receipt} if isinstance(receipt, dict) else None,
            "agent_processed": None, "user_read": None}


def _identifier(value, name="installation_id"):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", value):
        raise PlatformError("invalid_id", f"{name} must be a nonempty portable identifier of at most 64 characters.")
    return value


def _json_object(value):
    try:
        parsed = json.loads(value)
    except (ValueError, TypeError, RecursionError) as exc:
        raise PlatformError("state_corrupt", "Stored public metadata is invalid.") from exc
    if not isinstance(parsed, dict):
        raise PlatformError("state_corrupt", "Stored public metadata must be an object.")
    return parsed


def _usage(value):
    if not isinstance(value, dict):
        return None
    public = {key: value[key] for key in _USAGE_FIELDS
              if type(value.get(key)) is int and 0 <= value[key] <= 2**63 - 1}
    return public or None


class WorkbenchQueries:
    def __init__(self, state_root: str | Path):
        self._platform = Platform(state_root, readonly=True)
        self.state_root = self._platform.state_root
        self._root_key = hashlib.sha256(str(self.state_root).encode("utf-8")).hexdigest()

    def _envelope(self, data, installation_id=None, generation=None, observed=None):
        return {"api_version": API_VERSION,
                "source": {"kind": "masteragent", "state_root": str(self.state_root),
                           "installation_id": installation_id, "generation": generation},
                "observed_at": datetime.fromtimestamp(observed if observed is not None else time.time(), timezone.utc).isoformat(),
                "revision": None, "data": data}

    def _scope(self, db, installation_id, generation):
        _identifier(installation_id)
        if generation is not None and (type(generation) is not int or generation < 0):
            raise PlatformError("invalid_generation", "generation must be a nonnegative integer.")
        row = self._platform._installation(db, installation_id)
        selected = row["generation"] if generation is None else generation
        if selected > row["generation"]:
            raise PlatformError("invalid_generation", "The selected generation has not been activated.")
        return row, selected

    @staticmethod
    def _decision(row, observed):
        result = _pick(row, _DECISION)
        result["stored_state"] = row["state"]
        if row["state"] == "pending" and row["expires_at"] is not None and row["expires_at"] <= observed:
            result["state"] = "expired"
        return result

    @staticmethod
    def _result(row):
        error = _json_object(row["error"]) if row["error"] is not None else None
        return {**_pick(row, _RESULT),
                "error": {key: error[key] for key in ("code", "message")
                          if isinstance(error.get(key), str)} if error else None}

    @staticmethod
    def _limited(db, sql, args, limit=100):
        rows = list(db.execute(sql + " LIMIT ?", (*args, limit + 1)))
        return rows[:limit], len(rows) > limit

    def deployment(self):
        if not self._platform.db_path.exists():
            return self._envelope({"initialized": False, "platform_version": __version__,
                                   "schema_version": None, "active_installation_id": None, "installations": []})
        with self._platform._db() as db:
            observed = time.time()
            rows = list(db.execute("SELECT " + ",".join(_INSTALLATION) + " FROM installations ORDER BY installation_id"))
            data = {"initialized": True, "platform_version": __version__,
                    "schema_version": db.execute("PRAGMA user_version").fetchone()[0],
                    "active_installation_id": next((row["installation_id"] for row in rows if row["state"] == "active"), None),
                    "installations": [_pick(row, _INSTALLATION) for row in rows]}
            return self._envelope(data, observed=observed)

    def overview(self, installation_id, generation=None):
        with self._platform._db() as db:
            observed = time.time()
            installation, selected = self._scope(db, installation_id, generation)
            counts = {state: 0 for state in sorted(_STATES)}
            for row in db.execute("""SELECT state,COUNT(*) AS total FROM tasks
                    WHERE installation_id=? AND dispatch_generation=? GROUP BY state""", (installation_id, selected)):
                if row["state"] not in _STATES:
                    raise PlatformError("state_corrupt", "The stored task state is unsupported.")
                counts[row["state"]] = row["total"]
            counts["tasks_total"] = sum(counts.values())
            pending = "installation_id=? AND generation=? AND state='pending' AND (expires_at IS NULL OR expires_at>?)"
            args = (installation_id, selected, observed)
            counts["pending_decisions"] = db.execute("SELECT COUNT(*) FROM decisions WHERE " + pending, args).fetchone()[0]
            recent = db.execute("SELECT " + ",".join(_SUMMARY) + " FROM tasks WHERE installation_id=? AND dispatch_generation=? ORDER BY rowid DESC LIMIT 5", (installation_id, selected))
            decisions = db.execute("SELECT " + ",".join(_DECISION) + " FROM decisions WHERE " + pending + " ORDER BY rowid DESC LIMIT 10", args)
            data = {"installation": _pick(installation, _INSTALLATION), "counts": counts,
                    "recent_tasks": [_pick(row, _SUMMARY) for row in recent],
                    "pending_decisions": [self._decision(row, observed) for row in decisions], "scope_note": _SCOPE_NOTE}
            return self._envelope(data, installation_id, selected, observed)

    def _cursor(self, cursor, scope):
        try:
            if not isinstance(cursor, str) or not 1 <= len(cursor) <= 2048 or not re.fullmatch(r"[A-Za-z0-9_-]+", cursor):
                raise ValueError("encoding")
            raw = base64.b64decode(cursor + "=" * (-len(cursor) % 4), altchars=b"-_", validate=True)
            data = json.loads(raw.decode("utf-8"))
            if (not isinstance(data, dict) or set(data) != {"scope", "ceiling", "before"}
                    or json.dumps(data["scope"], sort_keys=True) != json.dumps(scope, sort_keys=True)):
                raise ValueError("scope")
            if any(type(data[k]) is not int or not 0 < data[k] <= 2**63 - 1 for k in ("before", "ceiling")) or data["before"] > data["ceiling"]:
                raise ValueError("bounds")
            return data["ceiling"], data["before"]
        except (ValueError, TypeError, UnicodeError, RecursionError) as exc:
            raise PlatformError("invalid_cursor", "The cursor is invalid or belongs to another deployment, generation or filter.") from exc

    @staticmethod
    def _encode_cursor(scope, ceiling, before):
        payload = json.dumps({"scope": scope, "ceiling": ceiling, "before": before}, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")

    def tasks(self, installation_id, generation=None, limit=20, cursor=None, state=None, role_id=None):
        if type(limit) is not int or not 1 <= limit <= 100:
            raise PlatformError("invalid_limit", "limit must be an integer between 1 and 100.")
        if state is not None and (not isinstance(state, str) or state not in _STATES):
            raise PlatformError("invalid_state", "The task state filter is unsupported.")
        if role_id is not None:
            _identifier(role_id, "role_id")
        with self._platform._db() as db:
            observed = time.time()
            installation, selected = self._scope(db, installation_id, generation)
            if role_id is not None:
                self._platform._role(installation, role_id)
            scope = {"v": 1, "root": self._root_key, "installation": installation_id,
                     "generation": selected, "state": state, "role": role_id}
            where, args = "installation_id=? AND dispatch_generation=?", [installation_id, selected]
            if state is not None:
                where += " AND state=?"
                args.append(state)
            if role_id is not None:
                where += " AND role_id=?"
                args.append(role_id)
            if cursor is not None:
                ceiling, before = self._cursor(cursor, scope)
            else:
                ceiling = db.execute("SELECT COALESCE(MAX(rowid),0) FROM tasks WHERE " + where, args).fetchone()[0]
                before = None
            bounds = " AND rowid<=?" + (" AND rowid<?" if before is not None else "")
            bound_args = (ceiling, before) if before is not None else (ceiling,)
            rows = list(db.execute("SELECT rowid AS _rowid," + ",".join(_SUMMARY) + " FROM tasks WHERE " + where +
                                   bounds + " ORDER BY rowid DESC LIMIT ?", (*args, *bound_args, limit + 1)))
            items = rows[:limit]
            next_cursor = self._encode_cursor(scope, ceiling, items[-1]["_rowid"]) if len(rows) > limit else None
            return self._envelope({"items": [_pick(row, _SUMMARY) for row in items], "next_cursor": next_cursor,
                                   "scope_note": _SCOPE_NOTE}, installation_id, selected, observed)

    @staticmethod
    def _event(row):
        detail = _json_object(row["detail"])
        fields = _EVENT_FIELDS[row["kind"]]
        # Never expose arbitrary nested runtime payloads, raw output or private reasoning.
        public = {key: detail[key] for key in fields if key in detail and
                  (detail[key] is None or type(detail[key]) in (str, int, bool) or
                   type(detail[key]) is float and math.isfinite(detail[key]))}
        if row["kind"] in {"runtime_stopped", "runtime_observation"}:
            public["usage"] = _usage(detail.get("usage"))
            source = detail.get("model_reported_source")
            model = detail.get("model_reported")
            valid_model = (isinstance(source, str) and source in _MODEL_SOURCES
                           and isinstance(model, str) and 0 < len(model) <= 200
                           and not any(ord(char) < 32 for char in model))
            public["model_reported"] = model if valid_model else None
            public["model_reported_source"] = source if valid_model else None
            valid_usage = (public["usage"] is not None and detail.get("usage_source") == "thread/tokenUsage/updated"
                           and detail.get("usage_scope") == "fresh_thread_total")
            public["usage_source"] = "thread/tokenUsage/updated" if valid_usage else None
            public["usage_scope"] = "fresh_thread_total" if valid_usage else None
        if row["kind"] == "runtime_native_request_denied":
            method = detail.get("method")
            public["method"] = method if isinstance(method, str) and method in _NATIVE_REQUEST_METHODS else None
            public["reason"] = "runtime_text_only" if detail.get("reason") == "runtime_text_only" else None
        return {**_pick(row, ("event_id", "attempt_id", "kind", "created_at")), "detail": public}

    def _runtime(self, db, attempt):
        # A bounded latest record per kind preserves launch evidence even when
        # the visible event timeline has been truncated by later progress.
        records, times = {}, {}
        latest = None
        for kind in _RUNTIME_KINDS:
            row = db.execute("""SELECT rowid AS _rowid,event_id,attempt_id,kind,detail,created_at
                FROM attempt_events WHERE installation_id=? AND attempt_id=? AND kind=?
                ORDER BY rowid DESC LIMIT 1""", (attempt["installation_id"], attempt["attempt_id"], kind)).fetchone()
            if row is not None:
                records[kind] = self._event(row)["detail"]
                times[kind] = row["created_at"]
                if latest is None or row["_rowid"] > latest["_rowid"]:
                    latest = row
        requested = records.get("runtime_start_requested", {})
        stopped = records.get("runtime_stopped", {})
        observation = records.get("runtime_observation", {})
        usage_record = next((record for record in (stopped, observation) if record.get("usage") is not None), {})
        model_record = next((record for record in (stopped, observation) if record.get("model_reported") is not None), {})
        denied = records.get("runtime_native_request_denied", {})
        denial_count = db.execute("""SELECT COUNT(*) FROM attempt_events
            WHERE installation_id=? AND attempt_id=? AND kind='runtime_native_request_denied'""",
            (attempt["installation_id"], attempt["attempt_id"])).fetchone()[0]

        def string(key, *sources):
            return next((source[key] for source in sources
                         if isinstance(source.get(key), str) and 0 < len(source[key]) <= 200), None)

        facts = {"evidence_source": "persisted_attempt_events", "runtime": string("runtime", requested),
                 "version": string("version", requested),
                 "model_requested": string("model_requested", stopped, requested),
                 "model_observed": string("model_observed", stopped, requested),
                 "model_reported": model_record.get("model_reported"),
                 "model_reported_source": model_record.get("model_reported_source"),
                 "usage": usage_record.get("usage"), "usage_source": usage_record.get("usage_source"),
                 "usage_scope": usage_record.get("usage_scope"),
                 "last_observation_at": times.get("runtime_observation"),
                 "native_requests_denied": {"count": denial_count, "last_method": denied.get("method"),
                                             "last_at": times.get("runtime_native_request_denied")},
                 "thread_id": string("thread_id", stopped, observation, records.get("runtime_thread", {})),
                 "turn_id": string("turn_id", stopped, observation, records.get("runtime_progress", {})),
                 "started": "runtime_started" in records,
                 "stop_requested": "runtime_cancel_requested" in records,
                 "termination_confirmed": stopped.get("termination_confirmed") is True,
                 "last_event_at": latest["created_at"] if latest is not None else None}
        return facts, records

    @staticmethod
    def _operations(db, installation, task, attempt, observed, records):
        reason = None
        if installation["state"] != "active":
            reason = "inactive_installation"
        elif installation["generation"] != attempt["generation"]:
            reason = "historical_generation"
        elif attempt["state"] != "running":
            reason = "attempt_not_running"
        elif task["state"] != "running" or task["dispatch_generation"] != attempt["generation"]:
            reason = "task_not_running"
        elif attempt["expires_at"] <= observed:
            reason = "lease_expired"
        elif "runtime_start_requested" not in records:
            reason = "runtime_not_started"
        elif "runtime_stopped" in records:
            reason = "runtime_finished"
        elif "runtime_cancel_requested" in records:
            reason = "stop_requested"
        input_reason = reason
        if input_reason is None:
            owner = db.execute("""SELECT 1 FROM actor_tokens WHERE token_hash=? AND installation_id=?
                AND role_id=? AND generation=?""", (attempt["owner_hash"], attempt["installation_id"],
                    attempt["role_id"], attempt["generation"])).fetchone()
            member = db.execute("""SELECT s.* FROM member_sessions s JOIN attempt_members m
                ON s.member_id=m.member_id WHERE m.attempt_id=?""", (attempt["attempt_id"],)).fetchone()
            if owner is None:
                input_reason = "owner_unavailable"
            elif member is not None and (member["state"] not in ("ready", "waiting", "busy")
                    or member["expires_at"] <= observed
                    or any(member[key] != attempt[key] for key in ("installation_id", "role_id", "generation", "owner_hash"))):
                input_reason = "member_unavailable"
            elif records["runtime_start_requested"].get("supports_runtime_inputs") is not True:
                input_reason = "runtime_input_unsupported"
        return {"can_input": input_reason is None, "input_reason": input_reason,
                "can_stop": reason is None, "stop_reason": reason}

    def task(self, installation_id, task_id, generation=None):
        _identifier(task_id, "task_id")
        with self._platform._db() as db:
            observed = time.time()
            installation, selected = self._scope(db, installation_id, generation)
            task = db.execute("SELECT " + ",".join((*_SUMMARY, "text")) + " FROM tasks WHERE installation_id=? AND task_id=? AND dispatch_generation=?",
                              (installation_id, task_id, selected)).fetchone()
            if task is None:
                raise PlatformError("task_not_found", "No task belongs to this installation and selected generation.")
            args = (installation_id, task_id, selected)
            attempts, attempts_cut = self._limited(db, "SELECT * FROM attempts WHERE installation_id=? AND task_id=? AND generation=? ORDER BY rowid DESC", args)
            attempt_items = []
            for attempt in attempts:
                runtime, records = self._runtime(db, attempt)
                attempt_items.append({**_pick(attempt, _ATTEMPT), "runtime": runtime,
                                      "operations": self._operations(db, installation, task, attempt, observed, records)})
            inputs, inputs_cut = self._limited(db, """SELECT i.* FROM attempt_inputs i JOIN attempts a ON i.attempt_id=a.attempt_id
                WHERE i.installation_id=? AND a.task_id=? AND i.generation=?
                AND a.installation_id=i.installation_id AND a.generation=i.generation ORDER BY i.rowid DESC""", args)
            decisions, decisions_cut = self._limited(db, "SELECT " + ",".join(_DECISION) + " FROM decisions WHERE installation_id=? AND task_id=? AND generation=? ORDER BY rowid DESC", args)
            notifications, notifications_cut = self._limited(db, "SELECT " + ",".join(_NOTIFICATION) +
                " FROM outbox WHERE installation_id=? AND task_id=? AND generation=? ORDER BY rowid DESC", args)
            deliveries, deliveries_cut = self._limited(db, "SELECT " + ",".join((*_DELIVERY, "receipt")) +
                " FROM result_deliveries WHERE installation_id=? AND task_id=? AND generation=? ORDER BY rowid DESC", args)
            kinds = ",".join("?" for _ in _EVENT_FIELDS)
            events, events_cut = self._limited(db, """SELECT e.event_id,e.attempt_id,e.kind,e.detail,e.created_at
                FROM attempt_events e JOIN attempts a ON e.attempt_id=a.attempt_id
                WHERE e.installation_id=? AND a.task_id=? AND a.generation=?
                AND a.installation_id=e.installation_id AND e.kind IN (""" + kinds + ") ORDER BY e.rowid DESC", (*args, *_EVENT_FIELDS))
            row = db.execute("SELECT " + ",".join((*_RESULT, "error")) + " FROM results WHERE installation_id=? AND task_id=? AND generation=?", args).fetchone()
            result = None
            if row is not None:
                result = self._result(row)
            history_available = bool(task["generation"] != selected or db.execute(
                "SELECT 1 FROM continuations WHERE installation_id=? AND task_id=? LIMIT 1", args[:2]).fetchone()
                or any(db.execute("SELECT 1 FROM " + table + " WHERE installation_id=? AND task_id=? AND generation<>? LIMIT 1", args).fetchone()
                       for table in ("attempts", "decisions", "results", "outbox")))
            data = {"task": dict(task), "attempts": attempt_items,
                    "events": [self._event(row) for row in events], "inputs": [_pick(row, _INPUT) for row in inputs],
                    "decisions": [self._decision(row, observed) for row in decisions], "result": result,
                    "notifications": [{**dict(row), "user_read": None} for row in notifications],
                    "deliveries": {"items": [_delivery_view(row) for row in deliveries], "truncated": deliveries_cut},
                    "notification_note": "Outbox states describe transport records; delivered does not prove user read or model receipt. Reading this view does not acknowledge delivery.",
                    "truncated": {"attempts": attempts_cut, "events": events_cut, "inputs": inputs_cut,
                                  "decisions": decisions_cut, "notifications": notifications_cut},
                    "history_available": history_available, "scope_note": _SCOPE_NOTE}
            return self._envelope(data, installation_id, selected, observed)

    def task_history(self, installation_id, task_id, generation=None, record_generation=None,
                     section="generations", limit=20, cursor=None):
        """Read a stable task identity across generations without relaxing task writes."""
        _identifier(task_id, "task_id")
        if type(limit) is not int or not 1 <= limit <= 100:
            raise PlatformError("invalid_limit", "limit must be an integer between 1 and 100.")
        if not isinstance(section, str) or section not in _HISTORY_SECTIONS:
            raise PlatformError("invalid_section", "The task history section is unsupported.")
        if section == "generations":
            if record_generation is not None:
                raise PlatformError("invalid_query", "The generation directory does not accept record_generation.")
        elif type(record_generation) is not int or record_generation < 0:
            raise PlatformError("invalid_generation", "Record pages require an explicit nonnegative record_generation.")
        with self._platform._db() as db:
            observed = time.time()
            installation, selected = self._scope(db, installation_id, generation)
            task = db.execute("SELECT " + ",".join((*_SUMMARY, "text")) +
                              " FROM tasks WHERE installation_id=? AND task_id=?",
                              (installation_id, task_id)).fetchone()
            if task is None:
                raise PlatformError("task_not_found", "No task belongs to this installation.")
            scope = {"v": 1, "kind": "task_history", "root": self._root_key,
                     "installation": installation_id, "task": task_id, "generation": selected,
                     "record_generation": record_generation, "section": section}
            # Include continuation-only generations, not just generations that
            # obtained an attempt. This keeps repeatedly resumed queued tasks reachable.
            generation_sources = (("tasks", "generation"), ("tasks", "dispatch_generation"),
                ("continuations", "from_generation"), ("continuations", "to_generation"),
                ("attempts", "generation"), ("decisions", "generation"),
                ("results", "generation"), ("outbox", "generation"))
            generations_sql = " UNION ".join("SELECT " + column + " AS generation FROM " + table +
                " WHERE installation_id=? AND task_id=?" for table, column in generation_sources)
            generation_args = (installation_id, task_id) * len(generation_sources)
            if cursor is not None:
                ceiling, before = self._cursor(cursor, scope)
            else:
                ceiling, before = None, None
            if section == "generations":
                base = "SELECT generation+1 AS _rowid,generation FROM (" + generations_sql + ")"
                args = generation_args
            else:
                if record_generation > installation["generation"] or db.execute(
                        "SELECT 1 FROM (" + generations_sql + ") WHERE generation=? LIMIT 1",
                        (*generation_args, record_generation)).fetchone() is None:
                    raise PlatformError("history_generation_not_found", "This generation has no records for the task.")
                args = (installation_id, task_id, record_generation)
                if section == "continuations":
                    base = "SELECT rowid AS _rowid," + ",".join(_CONTINUATION) + " FROM continuations " + \
                           "WHERE installation_id=? AND task_id=? AND (from_generation=? OR to_generation=?)"
                    args = (*args, record_generation)
                elif section == "inputs":
                    base = """SELECT i.rowid AS _rowid,i.* FROM attempt_inputs i JOIN attempts a
                        ON i.attempt_id=a.attempt_id AND i.installation_id=a.installation_id AND i.generation=a.generation
                        WHERE i.installation_id=? AND a.task_id=? AND i.generation=?"""
                elif section == "events":
                    kinds = ",".join("?" for _ in _EVENT_FIELDS)
                    base = """SELECT e.rowid AS _rowid,e.event_id,e.attempt_id,e.kind,e.detail,e.created_at
                        FROM attempt_events e JOIN attempts a ON e.attempt_id=a.attempt_id AND e.installation_id=a.installation_id
                        WHERE e.installation_id=? AND a.task_id=? AND a.generation=? AND e.kind IN (""" + kinds + ")"
                    args = (*args, *_EVENT_FIELDS)
                else:
                    table = "outbox" if section == "notifications" else section
                    base = "SELECT rowid AS _rowid,* FROM " + table + " WHERE installation_id=? AND task_id=? AND generation=?"
            # Every section uses keyset pagination and a first-page ceiling.
            # No raw row or unbounded list is returned to the client.
            if ceiling is None:
                ceiling = db.execute("SELECT COALESCE(MAX(_rowid),0) FROM (" + base + ")", args).fetchone()[0]
            bounds = " WHERE _rowid<=?" + (" AND _rowid<?" if before is not None else "")
            bound_args = (ceiling, before) if before is not None else (ceiling,)
            rows = list(db.execute("SELECT * FROM (" + base + ")" + bounds + " ORDER BY _rowid DESC LIMIT ?",
                                   (*args, *bound_args, limit + 1)))
            items = []
            for row in rows[:limit]:
                if section == "generations":
                    item = {"generation": row["generation"], "is_original": row["generation"] == task["generation"],
                            "is_current_dispatch": row["generation"] == task["dispatch_generation"],
                            "is_installation_current": row["generation"] == installation["generation"]}
                elif section == "attempts":
                    runtime, _ = self._runtime(db, row)
                    item = {**_pick(row, _ATTEMPT), "runtime": runtime}
                elif section == "decisions":
                    item = self._decision(row, observed)
                elif section == "results":
                    item = self._result(row)
                    delivery = db.execute("SELECT " + ",".join((*_DELIVERY, "receipt")) +
                        " FROM result_deliveries WHERE installation_id=? AND task_id=? AND generation=? AND attempt_id=?",
                        (installation_id, task_id, record_generation, row["attempt_id"])).fetchone()
                    item["delivery"] = _delivery_view(delivery) if delivery is not None else None
                elif section == "events":
                    item = self._event(row)
                elif section == "notifications":
                    item = {**_pick(row, _NOTIFICATION), "user_read": None}
                else:
                    item = _pick(row, _INPUT if section == "inputs" else _CONTINUATION)
                items.append(item)
            next_cursor = self._encode_cursor(scope, ceiling, rows[limit - 1]["_rowid"]) if len(rows) > limit else None
            data = {"scope": {"kind": "task_history", "task_id": task_id}, "task": dict(task),
                    "section": section, "record_generation": record_generation, "readonly": True,
                    "items": items, "next_cursor": next_cursor, "scope_note": _HISTORY_NOTE}
            return self._envelope(data, installation_id, selected, observed)

    def team(self, installation_id, generation=None):
        with self._platform._db() as db:
            observed = time.time()
            installation, selected = self._scope(db, installation_id, generation)
            package = self._platform._snapshot(installation)
            bindings = self._platform._deployments(db, installation)
            roles = [{**_pick(role, ("role_id", "display_name", "instructions", "tools")), **bindings[role["role_id"]]}
                     for role in package["roles"]]
            rows, truncated = self._limited(db, """SELECT s.*,EXISTS(SELECT 1 FROM actor_tokens t WHERE t.token_hash=s.owner_hash
                AND t.installation_id=s.installation_id AND t.role_id=s.role_id AND t.generation=s.generation) AS authorized
                FROM member_sessions s WHERE s.installation_id=? AND s.generation=? ORDER BY s.rowid DESC""", (installation_id, selected))
            members = []
            for row in rows:
                active_state = row["state"] in ("ready", "waiting", "busy", "stopping")
                live = bool(active_state and row["expires_at"] > observed and row["authorized"] and
                            installation["state"] == "active" and installation["generation"] == selected)
                active = db.execute("""SELECT a.attempt_id,a.expires_at FROM attempts a JOIN attempt_members m ON a.attempt_id=m.attempt_id
                    WHERE m.member_id=? AND a.installation_id=? AND a.generation=? AND a.state='running' ORDER BY a.rowid LIMIT 1""",
                    (row["member_id"], installation_id, selected)).fetchone()
                members.append({**_pick(row, _MEMBER), "stored_state": row["state"],
                                "state": "lost" if active_state and not live else row["state"],
                                "lease_valid": live, "online": live,
                                "accepting_tasks": live and row["state"] in ("ready", "waiting") and active is None,
                                "reception_waiting": live and row["state"] == "waiting",
                                "owned_attempt_active": bool(live and row["state"] != "stopping" and active is not None and active["expires_at"] > observed),
                                "active_attempt_id": active["attempt_id"] if active is not None else None,
                                "model_connectivity": "not_verified"})
            return self._envelope({"roles": roles, "members": members, "truncated": {"members": truncated},
                                   "scope_note": _SCOPE_NOTE}, installation_id, selected, observed)

    def resources(self, installation_id, generation=None):
        with self._platform._db() as db:
            observed = time.time()
            installation, selected = self._scope(db, installation_id, generation)
            package = self._platform._snapshot(installation)
            data = {"package_id": installation["package_id"], "version": installation["version"],
                    **_pick(package, ("knowledge", "workflows", "tools")), "artifact_catalog_available": False}
            return self._envelope(data, installation_id, selected, observed)

    def connections(self, installation_id, generation=None):
        with self._platform._db() as db:
            observed = time.time()
            _, selected = self._scope(db, installation_id, generation)
            unknown = {"authentication": "not_verified", "model_requested": None, "model_observed": None,
                       "model_reported": None, "model_reported_source": None,
                       "usage": None, "usage_source": None, "usage_scope": None}
            starts, truncated = self._limited(db, """SELECT a.*,e.detail FROM attempt_events e JOIN attempts a
                ON e.attempt_id=a.attempt_id AND e.installation_id=a.installation_id
                WHERE a.installation_id=? AND a.generation=? AND e.kind='runtime_start_requested'
                ORDER BY e.rowid DESC""", (installation_id, selected))
            latest_attempt = None
            for attempt in starts:
                if _json_object(attempt["detail"]).get("runtime") not in ("codex-exec", "codex-app-server"):
                    continue
                runtime, _ = self._runtime(db, attempt)
                if runtime["runtime"] not in ("codex-exec", "codex-app-server"):
                    continue
                latest_attempt = {**_pick(attempt, ("task_id", "attempt_id", "role_id", "generation", "state")), **runtime}
                break
            items = [{**unknown, "runtime": "codex", "label": "Codex", "supported": True,
                      "status": "not_verified", "connectivity": "not_verified",
                      "reason": "Adapters exist; this read-only view does not inspect deployment credentials or probe model connectivity.",
                      "capabilities": {"codex_exec": dict(CAPABILITIES), "codex_app_server": dict(APP_SERVER_CAPABILITIES)}},
                     {**unknown, "runtime": "claude", "label": "Claude Code", "supported": False,
                      "status": "unavailable", "connectivity": "unavailable",
                      "reason": "The platform Claude adapter is not implemented; native integration has not been verified.", "capabilities": {}}]
            for item in items:
                item["latest_attempt"] = latest_attempt if item["runtime"] == "codex" else None
            if latest_attempt is not None:
                items[0].update({key: latest_attempt[key] for key in ("model_requested", "model_observed", "usage",
                    "model_reported", "model_reported_source", "usage_source", "usage_scope")})
                items[0]["reason"] = "Model and usage facts come from the latest recorded attempt in this generation; they do not verify current authentication or connectivity."
            return self._envelope({"items": items, "truncated": {"runtime_observations": truncated},
                                   "scope_note": _SCOPE_NOTE}, installation_id, selected, observed)
