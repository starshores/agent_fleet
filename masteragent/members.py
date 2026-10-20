"""Persistent local member leases, separate from individual execution leases.

Members schedule existing tasks; they are not native model sessions. State
changes and execution ownership are transactional. No process is started here.
"""
from __future__ import annotations

import hashlib
import re
import secrets
import uuid

from .errors import PlatformError


_ACTIVE = ("ready", "waiting", "busy", "stopping")


def _digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class MemberMixin:
    @staticmethod
    def _member(db, member_id):
        row = db.execute("SELECT * FROM member_sessions WHERE member_id=?", (member_id,)).fetchone()
        if row is None:
            raise PlatformError("member_scope", "No accessible member exists with this identifier.")
        return row

    def _member_live(self, db, row):
        if row["state"] not in _ACTIVE or row["expires_at"] <= self._clock_now():
            return False
        installation = db.execute("SELECT state,generation FROM installations WHERE installation_id=?",
                                  (row["installation_id"],)).fetchone()
        return bool(installation and installation["state"] == "active"
                    and installation["generation"] == row["generation"]
                    and db.execute("""SELECT 1 FROM actor_tokens WHERE token_hash=?
                        AND installation_id=? AND role_id=? AND generation=?""",
                        (row["owner_hash"], row["installation_id"], row["role_id"], row["generation"])).fetchone())

    def _member_view(self, db, row):
        value = {key: row[key] for key in row.keys() if key not in ("owner_hash", "lease_hash")}
        live = self._member_live(db, row)
        if row["state"] in _ACTIVE and not live:
            value["state"] = "lost"
        value["online"] = live
        active = db.execute("""SELECT a.attempt_id FROM attempts a JOIN attempt_members m
            ON a.attempt_id=m.attempt_id WHERE m.member_id=? AND a.state='running'
            ORDER BY a.rowid LIMIT 1""", (row["member_id"],)).fetchone()
        value["active_attempt_id"] = active[0] if active else None
        value["accepting_tasks"] = live and row["state"] in ("ready", "waiting")
        value["reception_waiting"] = live and row["state"] == "waiting"
        # Execution ownership may be fenced before its process has exited.
        # This field is about the lease, never an OS process observation.
        value["owned_attempt_active"] = live and row["state"] != "stopping" and active is not None
        value["model_connectivity"] = "not_verified"
        return value

    def _member_credentials(self, db, actor, member_id, member_token):
        row = self._member(db, member_id)
        # Stop acknowledgement must remain possible after actor revocation.
        if (not isinstance(getattr(actor, "token", None), str)
                or not isinstance(member_token, str)
                or type(getattr(actor, "generation", None)) is not int
                or (getattr(actor, "installation_id", None), getattr(actor, "role_id", None), actor.generation) !=
                   (row["installation_id"], row["role_id"], row["generation"])
                or not secrets.compare_digest(row["owner_hash"], _digest(actor.token))
                or not secrets.compare_digest(row["lease_hash"], _digest(member_token))):
            raise PlatformError("member_owner", "The original actor and member lease are required.")
        return row

    def _member_event(self, db, row, kind):
        self._enqueue(db, row["installation_id"], row["generation"], None, row["role_id"], kind,
                      {"member_id": row["member_id"], "state": row["state"]},
                      kind + ":" + row["member_id"])

    def _fence_member(self, db, row, state, reason):
        """Close publication before requesting process shutdown; never retry work."""
        stamp = self._coordination_time()
        attempts = list(db.execute("""SELECT a.* FROM attempts a JOIN attempt_members m
            ON a.attempt_id=m.attempt_id WHERE m.member_id=? AND a.state='running'""", (row["member_id"],)))
        for attempt in attempts:
            self._hold_runtime_inputs(db, attempt)
            db.execute("UPDATE attempts SET state='expired',finished_at=? WHERE attempt_id=?",
                       (stamp, attempt["attempt_id"]))
            db.execute("""UPDATE tasks SET state='needs_reconcile' WHERE task_id=?
                AND state='running' AND dispatch_generation=?""", (attempt["task_id"], attempt["generation"]))
            self._attempt_event(db, attempt, "member_fenced", {"member_id": row["member_id"], "reason": reason,
                                                               "effects": "unknown"})
        db.execute("UPDATE member_sessions SET state=?,updated_at=?,stop_reason=? WHERE member_id=?",
                   (state, stamp, reason, row["member_id"]))
        updated = self._member(db, row["member_id"])
        if state != row["state"]:
            self._member_event(db, updated, "member_" + state)
        return updated

    def _require_live_member(self, db, row):
        if not self._member_live(db, row) or row["state"] == "stopping":
            raise PlatformError("member_inactive", "This member no longer has permission to execute or publish.")

    def _guard_attempt_member(self, db, attempt):
        row = db.execute("""SELECT s.* FROM member_sessions s JOIN attempt_members m
            ON s.member_id=m.member_id WHERE m.attempt_id=?""", (attempt["attempt_id"],)).fetchone()
        if row is not None:
            self._require_live_member(db, row)
            if (row["installation_id"], row["role_id"], row["generation"], row["owner_hash"]) != (
                    attempt["installation_id"], attempt["role_id"], attempt["generation"], attempt["owner_hash"]):
                raise PlatformError("member_scope", "Attempt membership does not match its execution owner.")

    def _guard_member_claim(self, db, installation_id, role_id, member_id=None):
        row = db.execute("""SELECT * FROM member_sessions WHERE installation_id=? AND role_id=?
            AND state IN ('ready','waiting','busy','stopping')""", (installation_id, role_id)).fetchone()
        if member_id is None:
            if row is not None:
                raise PlatformError("member_exclusive", "A persistent member owns this role; use its queue.")
            return
        if row is None or row["member_id"] != member_id:
            raise PlatformError("member_inactive", "This member is no longer the role's current owner.")
        self._require_live_member(db, row)
        if db.execute("""SELECT 1 FROM attempts a JOIN attempt_members m ON a.attempt_id=m.attempt_id
            WHERE m.member_id=? AND a.state='running'""", (member_id,)).fetchone():
            raise PlatformError("member_busy", "A member may execute only one attempt at a time.")

    def start_member(self, actor, deployment_digest, tool_id, ttl_seconds=60):
        ttl = self._ttl(ttl_seconds)
        if not isinstance(deployment_digest, str) or not re.fullmatch(r"[0-9a-f]{64}", deployment_digest):
            raise PlatformError("invalid_deployment_digest", "An explicit SHA256 deployment digest is required.")
        with self._db(write=True) as db:
            installation = self._actor(db, actor)
            self._check_workspace(db, installation)
            if tool_id not in self._role(installation, actor.role_id)["tools"]:
                raise PlatformError("tool_denied", "This role is not granted the member's capability.")
            adapter = self._snapshot(installation)["tools"][tool_id]["adapter"]
            if adapter not in self.external_adapters:
                raise PlatformError("member_adapter", "Persistent members require an explicitly deployed external adapter.")
            existing = db.execute("""SELECT * FROM member_sessions WHERE installation_id=? AND role_id=?
                AND state IN ('ready','waiting','busy','stopping')""", (actor.installation_id, actor.role_id)).fetchone()
            if existing is not None:
                if self._member_live(db, existing):
                    raise PlatformError("member_conflict", "This role already has a live member instance.")
                self._fence_member(db, existing, "lost", "ownership_lost")
            # An independently started attempt must be reconciled before a
            # persistent scheduler can take over the same role.
            if db.execute("""SELECT 1 FROM attempts WHERE installation_id=? AND role_id=?
                AND state='running'""", (actor.installation_id, actor.role_id)).fetchone():
                raise PlatformError("member_busy", "An existing attempt still owns this role.")
            member_id, token, stamp = str(uuid.uuid4()), secrets.token_urlsafe(32), self._coordination_time()
            db.execute("""INSERT INTO member_sessions VALUES (?,?,?,?,?,?,?,?,'ready',?,?,?,NULL)""",
                       (member_id, actor.installation_id, actor.role_id, actor.generation, _digest(actor.token),
                        _digest(token), deployment_digest, tool_id, self._clock_now() + ttl, stamp, stamp))
            row = self._member(db, member_id)
            self._member_event(db, row, "member_started")
            return {**self._member_view(db, row), "member_token": token}

    def member_status(self, actor, member_id, member_token):
        with self._db() as db:
            return self._member_view(db, self._member_credentials(db, actor, member_id, member_token))

    def members(self, installation_id):
        """Read status without expiring leases or mutating delivery state."""
        with self._db() as db:
            self._installation(db, installation_id)
            return [self._member_view(db, row) for row in db.execute(
                "SELECT * FROM member_sessions WHERE installation_id=? ORDER BY rowid", (installation_id,))]

    def renew_member(self, actor, member_id, member_token, ttl_seconds=60):
        ttl = self._ttl(ttl_seconds)
        with self._db(write=True) as db:
            row = self._member_credentials(db, actor, member_id, member_token)
            if row["state"] == "stopping":
                return self._member_view(db, row)
            self._require_live_member(db, row)
            db.execute("UPDATE member_sessions SET expires_at=?,updated_at=? WHERE member_id=?",
                       (self._clock_now() + ttl, self._coordination_time(), member_id))
            return self._member_view(db, self._member(db, member_id))

    def claim_member_task(self, actor, member_id, member_token, ttl_seconds=60):
        self._ttl(ttl_seconds)
        with self._db(write=True) as db:
            installation = self._actor(db, actor)
            row = self._member_credentials(db, actor, member_id, member_token)
            self._guard_member_claim(db, actor.installation_id, actor.role_id, member_id)
            task = db.execute("""SELECT t.* FROM tasks t
                LEFT JOIN source_admissions a ON a.task_id=t.task_id
                LEFT JOIN routes r ON r.route_id=a.route_id
                WHERE t.installation_id=? AND t.role_id=?
                AND t.dispatch_generation=? AND t.state='queued'
                AND (a.admission_id IS NULL OR (a.owner_lane='v3' AND r.claim_open=1
                    AND a.installation_id=t.installation_id AND a.role_id=t.role_id))
                ORDER BY t.rowid LIMIT 1""",
                (actor.installation_id, actor.role_id, actor.generation)).fetchone()
            if task is None:
                if row["state"] == "busy":
                    db.execute("UPDATE member_sessions SET state='ready',updated_at=? WHERE member_id=?",
                               (self._coordination_time(), member_id))
                return None
            issued = self._claim(db, installation, actor.role_id, task["task_id"], row["tool_id"],
                                 _digest(actor.token), ttl_seconds, member_id=member_id)
            db.execute("INSERT INTO attempt_members VALUES (?,?)", (issued["attempt_id"], member_id))
            db.execute("UPDATE member_sessions SET state='busy',updated_at=? WHERE member_id=?",
                       (self._coordination_time(), member_id))
            return {**issued, "member_id": member_id}

    def member_waiting(self, actor, member_id, member_token, waiting):
        if type(waiting) is not bool:
            raise PlatformError("invalid_member_state", "Waiting state must be a boolean observation.")
        with self._db(write=True) as db:
            row = self._member_credentials(db, actor, member_id, member_token)
            self._require_live_member(db, row)
            self._guard_member_claim(db, actor.installation_id, actor.role_id, member_id)
            desired = "waiting" if waiting else "ready"
            if row["state"] == desired:
                return self._member_view(db, row)
            db.execute("UPDATE member_sessions SET state=?,updated_at=? WHERE member_id=?",
                       (desired, self._coordination_time(), member_id))
            return self._member_view(db, self._member(db, member_id))

    def request_member_stop(self, installation_id, member_id, reason):
        self._reason(reason)
        with self._db(write=True) as db:
            self._installation(db, installation_id)
            row = self._member(db, member_id)
            if row["installation_id"] != installation_id:
                raise PlatformError("member_scope", "This member belongs to another installation.")
            if row["state"] in ("stopped", "lost"):
                return self._member_view(db, row)
            if row["state"] == "stopping":
                if row["stop_reason"] != reason:
                    raise PlatformError("member_stop_conflict", "A different member stop request is already recorded.")
                return self._member_view(db, row)
            updated = self._fence_member(db, row, "stopping", reason)
            return self._member_view(db, updated)

    def finish_member(self, actor, member_id, member_token, termination_confirmed):
        if type(termination_confirmed) is not bool:
            raise PlatformError("invalid_termination", "Process termination evidence must be a boolean.")
        with self._db(write=True) as db:
            row = self._member_credentials(db, actor, member_id, member_token)
            if row["state"] in ("stopped", "lost"):
                return self._member_view(db, row)
            running = db.execute("""SELECT 1 FROM attempts a JOIN attempt_members m
                ON a.attempt_id=m.attempt_id WHERE m.member_id=? AND a.state='running'""", (member_id,)).fetchone()
            state = "stopped" if termination_confirmed and not running else "lost"
            updated = self._fence_member(db, row, state, row["stop_reason"] or "worker_finished")
            return self._member_view(db, updated)

    def expire_members(self, installation_id):
        with self._db(write=True) as db:
            self._installation(db, installation_id)
            rows = list(db.execute("""SELECT * FROM member_sessions WHERE installation_id=?
                AND state IN ('ready','waiting','busy','stopping') ORDER BY rowid""", (installation_id,)))
            values = []
            for row in rows:
                if not self._member_live(db, row):
                    updated = self._fence_member(db, row, "lost", "ownership_lost")
                    values.append(self._member_view(db, updated))
            return values
