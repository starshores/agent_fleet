"""Durable, attempt-scoped runtime input; transport acknowledgements are not approval.

The trusted local submitter persists text before a controller can claim it.
Only the live attempt owner may claim or acknowledge delivery. A claim is never
reissued: an interrupted or ambiguous send remains held for reconciliation.
"""
from __future__ import annotations

import json
import uuid

from .errors import PlatformError


class RuntimeInteractionMixin:
    def _runtime_input_live(self, db, attempt):
        self._guard_attempt_member(db, attempt)
        installation = self._installation(db, attempt["installation_id"], active=True)
        if installation["generation"] != attempt["generation"]:
            raise PlatformError("stale_attempt", "Runtime input belongs to an earlier activation.")
        task = self._task(db, attempt["installation_id"], attempt["role_id"],
                          attempt["generation"], attempt["task_id"])
        if attempt["state"] != "running" or task["state"] != "running":
            raise PlatformError("stale_attempt", "Only a current running attempt accepts runtime input.")
        if attempt["expires_at"] <= self._clock_now():
            raise PlatformError("lease_expired", "Expired attempts cannot receive or deliver runtime input.")
        if not db.execute("""SELECT 1 FROM actor_tokens WHERE token_hash=?
                AND installation_id=? AND role_id=? AND generation=?""",
                (attempt["owner_hash"], attempt["installation_id"], attempt["role_id"],
                 attempt["generation"])).fetchone():
            raise PlatformError("attempt_owner", "The runtime input's original owner is no longer registered.")
        event = db.execute("""SELECT detail FROM attempt_events WHERE attempt_id=?
            AND kind='runtime_start_requested' ORDER BY rowid DESC LIMIT 1""",
            (attempt["attempt_id"],)).fetchone()
        if event is None or json.loads(event["detail"]).get("supports_runtime_inputs") is not True:
            raise PlatformError("runtime_input_unsupported", "This runtime has not declared support for in-flight input.")

    def _runtime_input_event(self, db, attempt, row):
        # Keep input text out of metadata logs and notification payloads.
        kind = "runtime_input_" + row["state"]
        self._enqueue(db, attempt["installation_id"], attempt["generation"],
                      attempt["task_id"], attempt["role_id"], kind,
                      {"input_id": row["input_id"], "attempt_id": attempt["attempt_id"],
                       "sequence": row["sequence"], "state": row["state"]},
                      kind + ":" + row["input_id"])

    def submit_runtime_input(self, installation_id, attempt_id, text, request_id, *,
                             expected_generation=None, expected_task_id=None):
        """Persist one immutable supplement for this attempt, never an approval."""
        if not isinstance(text, str) or not text.strip():
            raise PlatformError("invalid_runtime_input", "Runtime input must contain nonempty text.")
        try:
            oversized = len(text.encode("utf-8")) > 64000
        except UnicodeEncodeError as exc:
            raise PlatformError("invalid_runtime_input", "Runtime input must be valid UTF-8 text.") from exc
        if oversized:
            raise PlatformError("invalid_runtime_input", "Runtime input exceeds 64000 UTF-8 bytes.")
        if not isinstance(request_id, str) or not request_id.strip() or len(request_id) > 200:
            raise PlatformError("invalid_request_id", "Input request identifiers must be nonempty and at most 200 characters.")
        with self._db(write=True) as db:
            installation = self._installation(db, installation_id, active=True)
            self._guard_command_generation(installation, expected_generation)
            attempt = self._attempt(db, attempt_id)
            if attempt["installation_id"] != installation_id:
                raise PlatformError("attempt_scope", "This attempt belongs to another installation.")
            self._guard_command_task(db, installation, attempt["task_id"], expected_task_id)
            if attempt["generation"] != installation["generation"]:
                raise PlatformError("stale_attempt", "Runtime input belongs to an earlier activation.")
            previous = db.execute("""SELECT * FROM attempt_inputs WHERE installation_id=?
                AND generation=? AND request_id=?""",
                (installation_id, attempt["generation"], request_id)).fetchone()
            if previous is not None:
                if previous["attempt_id"] != attempt_id or previous["text"] != text:
                    raise PlatformError("request_conflict", "This input request identifier already refers to different content.")
                # Exact retries acknowledge the persisted record, even if the
                # attempt has since ended. They never requeue or send it again.
                return dict(previous)
            if expected_generation is not None and db.execute("""SELECT 1 FROM attempt_events
                    WHERE attempt_id=? AND kind IN ('runtime_cancel_requested','runtime_stopped')""",
                    (attempt_id,)).fetchone():
                raise PlatformError("runtime_stopping", "This runtime is stopping or stopped; it cannot accept new input.")
            self._runtime_input_live(db, attempt)
            sequence = db.execute("SELECT COALESCE(MAX(sequence),0)+1 FROM attempt_inputs WHERE attempt_id=?",
                                  (attempt_id,)).fetchone()[0]
            input_id, stamp = str(uuid.uuid4()), self._coordination_time()
            db.execute("""INSERT INTO attempt_inputs
                (input_id,installation_id,role_id,generation,attempt_id,sequence,request_id,text,state,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,?,'pending',?,?)""",
                (input_id, installation_id, attempt["role_id"], attempt["generation"],
                 attempt_id, sequence, request_id, text, stamp, stamp))
            row = db.execute("SELECT * FROM attempt_inputs WHERE input_id=?", (input_id,)).fetchone()
            self._runtime_input_event(db, attempt, row)
            return dict(row)

    def claim_runtime_input(self, actor, attempt_id, lease_token):
        """Claim the oldest unresolved input once; never leapfrog uncertain input."""
        with self._db(write=True) as db:
            attempt = self._attempt(db, attempt_id)
            self._owned_attempt(db, actor, attempt, lease_token)
            self._runtime_input_live(db, attempt)
            row = db.execute("""SELECT * FROM attempt_inputs WHERE attempt_id=?
                AND state!='accepted' ORDER BY sequence LIMIT 1""", (attempt_id,)).fetchone()
            if row is None or row["state"] != "pending":
                return None
            db.execute("UPDATE attempt_inputs SET state='sending',updated_at=? WHERE input_id=?",
                       (self._coordination_time(), row["input_id"]))
            row = db.execute("SELECT * FROM attempt_inputs WHERE input_id=?", (row["input_id"],)).fetchone()
            self._runtime_input_event(db, attempt, row)
            return dict(row)

    def ack_runtime_input(self, actor, attempt_id, lease_token, input_id, state):
        """Record transport receipt only; 'accepted' does not mean approval or adoption."""
        if state not in ("accepted", "needs_reconcile"):
            raise PlatformError("invalid_input_ack", "Input acknowledgement must be accepted or needs_reconcile.")
        desired = "accepted" if state == "accepted" else "held"
        with self._db(write=True) as db:
            attempt = self._attempt(db, attempt_id)
            self._owned_attempt(db, actor, attempt, lease_token)
            self._runtime_input_live(db, attempt)
            row = db.execute("SELECT * FROM attempt_inputs WHERE input_id=? AND attempt_id=?",
                             (input_id, attempt_id)).fetchone()
            if row is None:
                raise PlatformError("input_scope", "This input does not belong to the attempt.")
            if row["state"] == desired:
                return dict(row)
            if row["state"] != "sending":
                raise PlatformError("input_ack_conflict", "Only the claimed input can receive a new delivery outcome.")
            db.execute("UPDATE attempt_inputs SET state=?,updated_at=? WHERE input_id=?",
                       (desired, self._coordination_time(), input_id))
            row = db.execute("SELECT * FROM attempt_inputs WHERE input_id=?", (input_id,)).fetchone()
            if desired == "held":
                self._hold_input_notifications(db, row)
            self._runtime_input_event(db, attempt, row)
            return dict(row)

    def runtime_inputs(self, installation_id, attempt_id=None):
        """Trusted local history query. Reading never claims, expires, or replays input."""
        with self._db() as db:
            self._installation(db, installation_id)
            if attempt_id is not None:
                attempt = self._attempt(db, attempt_id)
                if attempt["installation_id"] != installation_id:
                    raise PlatformError("attempt_scope", "This attempt belongs to another installation.")
            query, args = "SELECT * FROM attempt_inputs WHERE installation_id=?", [installation_id]
            if attempt_id is not None:
                query += " AND attempt_id=?"
                args.append(attempt_id)
            return [dict(row) for row in db.execute(query + " ORDER BY rowid", args)]

    @staticmethod
    def _ensure_runtime_inputs_resolved(db, attempt):
        if db.execute("SELECT 1 FROM attempt_inputs WHERE attempt_id=? AND state!='accepted' LIMIT 1",
                      (attempt["attempt_id"],)).fetchone():
            raise PlatformError("runtime_inputs_unresolved", "Runtime input is not confirmed received; completion requires reconciliation.")

    @staticmethod
    def _hold_input_notifications(db, row):
        # A generic outbox consumer must not mistake old send-intent notices for
        # fresh transport work. The held-state notification remains observable.
        db.execute("""UPDATE outbox SET state='held' WHERE installation_id=? AND generation=?
            AND state='pending' AND dedupe_key IN (?,?)""",
            (row["installation_id"], row["generation"],
             "runtime_input_pending:" + row["input_id"], "runtime_input_sending:" + row["input_id"]))

    def _hold_runtime_inputs(self, db, attempt):
        """Caller holds the state-transition transaction; accepted evidence survives."""
        rows = list(db.execute("SELECT * FROM attempt_inputs WHERE attempt_id=? AND state IN ('pending','sending') ORDER BY sequence",
                               (attempt["attempt_id"],)))
        for row in rows:
            db.execute("UPDATE attempt_inputs SET state='held',updated_at=? WHERE input_id=?",
                       (self._coordination_time(), row["input_id"]))
            updated = db.execute("SELECT * FROM attempt_inputs WHERE input_id=?", (row["input_id"],)).fetchone()
            self._hold_input_notifications(db, updated)
            self._runtime_input_event(db, attempt, updated)
        return len(rows)
