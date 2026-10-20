"""Durable decisions and parent/child coordination inside one installation.

These APIs persist intent and observations. They do not deliver messages, call
models, or infer approval from a timeout. Administrative decision APIs trust the
local caller; role-facing APIs require a current Actor issued by Platform.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import math
import uuid

from .errors import PlatformError


_TERMINAL = ("completed", "failed", "cancelled")


def _text(value, code, description, limit=200):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise PlatformError(code, description)
    return value


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


class CoordinationMixin:
    def _coordination_time(self):
        return datetime.fromtimestamp(self.clock(), timezone.utc).isoformat()

    @staticmethod
    def _event(row):
        value = dict(row)
        value["payload"] = json.loads(value["payload"])
        return value

    def _enqueue(self, db, installation_id, generation, task_id, recipient_role,
                 kind, payload, dedupe_key):
        """Insert an event alongside the state change, in the caller's transaction."""
        encoded = _json(payload)
        existing = db.execute("""SELECT * FROM outbox WHERE installation_id=?
            AND generation=? AND dedupe_key=?""", (installation_id, generation, dedupe_key)).fetchone()
        if existing is not None:
            if (existing["task_id"], existing["recipient_role"], existing["kind"], existing["payload"]) != (
                    task_id, recipient_role, kind, encoded):
                raise PlatformError("event_conflict", "This event key already refers to different content.")
            return self._event(existing)
        event_id = str(uuid.uuid4())
        db.execute("""INSERT INTO outbox
            (event_id,installation_id,generation,task_id,recipient_role,kind,payload,dedupe_key,state,created_at,delivered_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,NULL)""", (event_id, installation_id, generation,
                task_id, recipient_role, kind, encoded, dedupe_key, "pending", self._coordination_time()))
        return self._event(db.execute("SELECT * FROM outbox WHERE event_id=?", (event_id,)).fetchone())

    def outbox(self, installation_id, include_delivered=False):
        with self._db() as db:
            installation = self._installation(db, installation_id, active=True)
            state_filter = "" if include_delivered else " AND state='pending'"
            return [self._event(row) for row in db.execute(
                "SELECT * FROM outbox WHERE installation_id=? AND generation=?" + state_filter + " ORDER BY rowid",
                (installation_id, installation["generation"]))]

    def ack_outbox(self, installation_id, event_id):
        with self._db(write=True) as db:
            installation = self._installation(db, installation_id, active=True)
            row = db.execute("SELECT * FROM outbox WHERE installation_id=? AND event_id=?",
                             (installation_id, event_id)).fetchone()
            if row is None:
                raise PlatformError("event_scope", "This event does not belong to this installation.")
            if row["generation"] != installation["generation"]:
                raise PlatformError("stale_event", "This event belongs to an earlier activation.")
            if row["state"] == "held":
                raise PlatformError("event_held", "Historical delivery is uncertain; this event requires reconciliation.")
            if row["state"] == "pending":
                db.execute("UPDATE outbox SET state='delivered',delivered_at=? WHERE event_id=?",
                           (self._coordination_time(), event_id))
            return self._event(db.execute("SELECT * FROM outbox WHERE event_id=?", (event_id,)).fetchone())

    def decisions(self, installation_id, include_history=False):
        with self._db() as db:
            installation = self._installation(db, installation_id, active=not include_history)
            generation_filter = "" if include_history else " AND generation=?"
            args = (installation_id,) if include_history else (installation_id, installation["generation"])
            return [dict(row) for row in db.execute(
                "SELECT * FROM decisions WHERE installation_id=?" + generation_filter + " ORDER BY rowid", args)]

    def _decision_event(self, db, decision, role_id):
        self._enqueue(db, decision["installation_id"], decision["generation"], decision["task_id"],
                      role_id, "decision_requested", {"decision_id": decision["decision_id"],
                      "title": decision["title"], "revision": decision["revision"],
                      "expires_at": decision["expires_at"], "state": decision["state"]},
                      "decision_requested:" + decision["decision_id"])

    def ask_decision(self, actor, task_id, title, request_id, expires_at=None):
        _text(title, "invalid_decision", "A decision must have a nonempty title up to 2000 characters.", 2000)
        _text(request_id, "invalid_request_id", "Request identifiers must be nonempty strings up to 200 characters.")
        if expires_at is not None and (isinstance(expires_at, bool) or not isinstance(expires_at, (int, float))
                                       or not math.isfinite(expires_at)):
            raise PlatformError("invalid_deadline", "A decision deadline must be a finite Unix timestamp.")
        with self._db(write=True) as db:
            self._actor(db, actor)
            task = self._task(db, actor.installation_id, actor.role_id, actor.generation, task_id)
            previous = db.execute("""SELECT * FROM decisions WHERE installation_id=?
                AND request_id=? AND generation=?""", (actor.installation_id, request_id, actor.generation)).fetchone()
            if previous is not None:
                if (previous["task_id"], previous["title"], previous["expires_at"]) != (task_id, title, expires_at):
                    raise PlatformError("request_conflict", "This request identifier already refers to another decision.")
                return dict(previous)
            if task["state"] not in ("queued", "waiting", "awaiting_decision"):
                raise PlatformError("decision_task_state", "Only queued or waiting tasks can request a decision.")
            if expires_at is not None and expires_at <= self.clock():
                raise PlatformError("invalid_deadline", "A newly issued decision must have a future deadline.")
            if db.execute("""SELECT 1 FROM decisions WHERE installation_id=? AND task_id=?
                    AND generation=? AND state='pending'""", (actor.installation_id, task_id, actor.generation)).fetchone():
                raise PlatformError("decision_pending", "This task already has a pending decision.")
            # A new request is an explicit replacement for an expired question.
            db.execute("""UPDATE decisions SET state='superseded',revision=revision+1 WHERE
                installation_id=? AND task_id=? AND generation=? AND state='expired'""",
                       (actor.installation_id, task_id, actor.generation))
            decision_id = str(uuid.uuid4())
            db.execute("""INSERT INTO decisions
                (decision_id,installation_id,task_id,generation,request_id,title,state,revision,expires_at,answer_id,answer,created_at,answered_at)
                VALUES (?,?,?,?,?,?,'pending',0,?,NULL,NULL,?,NULL)""",
                (decision_id, actor.installation_id, task_id, actor.generation, request_id, title,
                 expires_at, self._coordination_time()))
            db.execute("UPDATE tasks SET state='awaiting_decision' WHERE task_id=?", (task_id,))
            decision = db.execute("SELECT * FROM decisions WHERE decision_id=?", (decision_id,)).fetchone()
            self._decision_event(db, decision, actor.role_id)
            return dict(decision)

    @staticmethod
    def _coordination_state(db, task_id, generation):
        if db.execute("""SELECT 1 FROM decisions WHERE task_id=? AND generation=?
                AND state IN ('pending','expired')""", (task_id, generation)).fetchone():
            return "awaiting_decision"
        if db.execute("SELECT 1 FROM requests WHERE parent_task_id=? AND state='waiting'", (task_id,)).fetchone():
            return "waiting"
        return "queued"

    def answer_decision(self, installation_id, decision_id, expected_revision, choice, answer_id, *,
                        expected_generation=None, expected_task_id=None):
        if isinstance(expected_revision, bool) or not isinstance(expected_revision, int) or expected_revision < 0:
            raise PlatformError("invalid_revision", "A decision revision must be a nonnegative integer.")
        if choice not in ("approve", "reject"):
            raise PlatformError("invalid_answer", "A decision answer must be approve or reject.")
        _text(answer_id, "invalid_answer_id", "Answer identifiers must be nonempty strings up to 200 characters.")
        with self._db(write=True) as db:
            installation = self._installation(db, installation_id, active=True)
            self._guard_command_generation(installation, expected_generation)
            decision = db.execute("SELECT * FROM decisions WHERE installation_id=? AND decision_id=?",
                                  (installation_id, decision_id)).fetchone()
            if decision is None:
                raise PlatformError("decision_scope", "This decision does not belong to this installation.")
            self._guard_command_task(db, installation, decision["task_id"], expected_task_id)
            if decision["generation"] != installation["generation"]:
                raise PlatformError("stale_decision", "This decision belongs to an earlier activation.")
            task = db.execute("SELECT * FROM tasks WHERE task_id=?", (decision["task_id"],)).fetchone()
            if task["dispatch_generation"] != installation["generation"]:
                raise PlatformError("stale_decision", "The decision's task requires explicit continuation.")
            previous = db.execute("SELECT * FROM decisions WHERE installation_id=? AND answer_id=?",
                                  (installation_id, answer_id)).fetchone()
            if previous is not None:
                if (previous["decision_id"], previous["answer"], previous["revision"] - 1) == (
                        decision_id, choice, expected_revision):
                    return dict(previous)
                raise PlatformError("answer_conflict", "This answer identifier has already been used.")
            if decision["revision"] != expected_revision:
                raise PlatformError("stale_revision", "The decision changed; reload it before answering.")
            if decision["state"] == "expired" or (decision["state"] == "pending" and
                    decision["expires_at"] is not None and decision["expires_at"] <= self.clock()):
                raise PlatformError("decision_expired", "This decision expired and must be explicitly reissued.")
            if decision["state"] != "pending" or task["state"] != "awaiting_decision":
                raise PlatformError("decision_closed", "This decision is no longer pending.")
            timestamp = self._coordination_time()
            state = "approved" if choice == "approve" else "rejected"
            db.execute("""UPDATE decisions SET state=?,revision=revision+1,answer_id=?,answer=?,answered_at=?
                WHERE decision_id=?""", (state, answer_id, choice, timestamp, decision_id))
            desired = self._coordination_state(db, task["task_id"], installation["generation"]) if choice == "approve" else "cancelled"
            db.execute("UPDATE tasks SET state=?,completed_at=? WHERE task_id=?",
                       (desired, timestamp if desired == "cancelled" else None, task["task_id"]))
            self._enqueue(db, installation_id, installation["generation"], task["task_id"], task["role_id"],
                          "decision_answered", {"decision_id": decision_id, "choice": choice,
                          "revision": expected_revision + 1}, "decision_answered:" + decision_id)
            if desired == "cancelled":
                self._child_terminal(db, db.execute("SELECT * FROM tasks WHERE task_id=?", (task["task_id"],)).fetchone())
            return dict(db.execute("SELECT * FROM decisions WHERE decision_id=?", (decision_id,)).fetchone())

    def expire_decisions(self, installation_id):
        with self._db(write=True) as db:
            installation = self._installation(db, installation_id, active=True)
            expired = list(db.execute("""SELECT * FROM decisions WHERE installation_id=? AND generation=?
                AND state='pending' AND expires_at IS NOT NULL AND expires_at<=? ORDER BY rowid""",
                (installation_id, installation["generation"], self.clock())))
            values = []
            for decision in expired:
                db.execute("UPDATE decisions SET state='expired',revision=revision+1 WHERE decision_id=?", (decision["decision_id"],))
                task = db.execute("SELECT * FROM tasks WHERE task_id=?", (decision["task_id"],)).fetchone()
                self._enqueue(db, installation_id, installation["generation"], task["task_id"], task["role_id"],
                              "decision_expired", {"decision_id": decision["decision_id"]},
                              "decision_expired:" + decision["decision_id"])
                values.append(dict(db.execute("SELECT * FROM decisions WHERE decision_id=?", (decision["decision_id"],)).fetchone()))
            return values

    def request_child(self, actor, parent_task_id, to_role, text, request_id):
        _text(text, "invalid_task", "A task must contain nonempty text up to 100000 characters.", 100000)
        _text(request_id, "invalid_request_id", "Request identifiers must be nonempty strings up to 200 characters.")
        with self._db(write=True) as db:
            installation = self._actor(db, actor)
            parent = self._task(db, actor.installation_id, actor.role_id, actor.generation, parent_task_id)
            self._role(installation, to_role)
            previous = db.execute("""SELECT * FROM requests WHERE installation_id=? AND parent_task_id=?
                AND client_request_id=?""", (actor.installation_id, parent_task_id, request_id)).fetchone()
            if previous is not None:
                if (previous["to_role"], previous["text"]) != (to_role, text):
                    raise PlatformError("request_conflict", "This request identifier refers to different child work.")
                return dict(previous)
            if parent["state"] not in ("queued", "waiting") or self._coordination_state(db, parent_task_id, actor.generation) == "awaiting_decision":
                raise PlatformError("parent_not_ready", "Only queued or child-waiting tasks may request new child work.")
            child_task_id, internal_id, timestamp = str(uuid.uuid4()), str(uuid.uuid4()), self._coordination_time()
            db.execute("""INSERT INTO tasks
                (task_id,installation_id,role_id,generation,text,client_request_id,state,created_at,completed_at,dispatch_generation,parent_task_id)
                VALUES (?,?,?,?,?,NULL,'queued',?,NULL,?,?)""",
                (child_task_id, actor.installation_id, to_role, actor.generation, text, timestamp, actor.generation, parent_task_id))
            db.execute("""INSERT INTO requests
                (request_id,installation_id,generation,parent_task_id,child_task_id,from_role,to_role,client_request_id,text,state,created_at,replied_at)
                VALUES (?,?,?,?,?,?,?,?,?,'waiting',?,NULL)""", (internal_id, actor.installation_id,
                actor.generation, parent_task_id, child_task_id, actor.role_id, to_role, request_id, text, timestamp))
            db.execute("UPDATE tasks SET state='waiting' WHERE task_id=?", (parent_task_id,))
            self._enqueue(db, actor.installation_id, actor.generation, child_task_id, to_role, "child_requested",
                          {"request_id": internal_id, "parent_task_id": parent_task_id}, "child_requested:" + internal_id)
            return dict(db.execute("SELECT * FROM requests WHERE request_id=?", (internal_id,)).fetchone())

    def _child_reply_event(self, db, request, child, generation):
        self._enqueue(db, request["installation_id"], generation, request["parent_task_id"],
                      request["from_role"], "child_replied", {"request_id": request["request_id"],
                      "parent_task_id": request["parent_task_id"], "child_task_id": child["task_id"],
                      "child_state": child["state"]}, "child_replied:" + request["request_id"])

    def _child_terminal(self, db, task_row):
        if task_row["state"] not in _TERMINAL:
            return
        request = db.execute("SELECT * FROM requests WHERE child_task_id=?", (task_row["task_id"],)).fetchone()
        if request is None:
            return
        if request["state"] == "waiting":
            db.execute("UPDATE requests SET state='replied',replied_at=? WHERE request_id=?",
                       (self._coordination_time(), request["request_id"]))
        parent = db.execute("SELECT * FROM tasks WHERE task_id=?", (request["parent_task_id"],)).fetchone()
        self._child_reply_event(db, request, task_row, parent["dispatch_generation"])
        if parent["state"] in ("waiting", "awaiting_decision"):
            desired = self._coordination_state(db, parent["task_id"], parent["dispatch_generation"])
            db.execute("UPDATE tasks SET state=? WHERE task_id=?", (desired, parent["task_id"]))

    def child_replies(self, actor, parent_task_id):
        with self._db() as db:
            self._actor(db, actor)
            self._task(db, actor.installation_id, actor.role_id, actor.generation, parent_task_id)
            values = []
            for request in db.execute("""SELECT * FROM requests WHERE installation_id=?
                    AND parent_task_id=? AND state='replied' ORDER BY rowid""", (actor.installation_id, parent_task_id)):
                child = db.execute("SELECT * FROM tasks WHERE task_id=?", (request["child_task_id"],)).fetchone()
                result = db.execute("SELECT * FROM results WHERE task_id=?", (request["child_task_id"],)).fetchone()
                value = dict(request)
                value.update(child_state=child["state"], result=self._result(result) if result else None)
                values.append(value)
            return values

    def _resume_coordination(self, db, task_row, new_generation):
        """Reissue current approval gates; preserve child identities and results."""
        installation_id, task_id = task_row["installation_id"], task_row["task_id"]
        for decision in list(db.execute("""SELECT d.* FROM decisions d WHERE d.task_id=?
                AND d.state IN ('pending','expired','approved') AND NOT EXISTS
                (SELECT 1 FROM decisions newer WHERE newer.task_id=d.task_id
                    AND newer.request_id=d.request_id AND newer.generation>d.generation)
                ORDER BY d.rowid""", (task_id,))):
            # An old approval is evidence, not permission for the new activation.
            # Preserve that evidence; only unresolved predecessor rows supersede.
            if decision["state"] != "approved":
                db.execute("UPDATE decisions SET state='superseded',revision=revision+1 WHERE decision_id=?", (decision["decision_id"],))
            decision_id = str(uuid.uuid4())
            state = "expired" if decision["expires_at"] is not None and decision["expires_at"] <= self.clock() else "pending"
            db.execute("""INSERT INTO decisions
                (decision_id,installation_id,task_id,generation,request_id,title,state,revision,expires_at,answer_id,answer,created_at,answered_at)
                VALUES (?,?,?,?,?,?,?,0,?,NULL,NULL,?,NULL)""", (decision_id, installation_id, task_id,
                new_generation, decision["request_id"], decision["title"], state, decision["expires_at"], self._coordination_time()))
            self._decision_event(db, db.execute("SELECT * FROM decisions WHERE decision_id=?", (decision_id,)).fetchone(), task_row["role_id"])
        for request in db.execute("SELECT * FROM requests WHERE parent_task_id=? AND state='replied'", (task_id,)):
            child = db.execute("SELECT * FROM tasks WHERE task_id=?", (request["child_task_id"],)).fetchone()
            self._child_reply_event(db, request, child, new_generation)
        return self._coordination_state(db, task_id, new_generation)
