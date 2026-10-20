"""Fenced result delivery, independent of execution and of destination storage.

Only _create_result_delivery participates in task completion's transaction.
Every destination receive/query call happens after releasing the core database.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import secrets
import uuid

from .errors import PlatformError
from .paths import _explicit_path
from .receipt_store import (ENVELOPE_LIMIT, PAYLOAD_VERSION, RECEIPT_PROTOCOL, TEXT_LIMIT,
                            canonical_json, digest, identifier, utf8, validate_envelope)


class DeliveryMixin:
    def _delivery_stamp(self):
        return datetime.fromtimestamp(self._clock_now(), timezone.utc).isoformat()

    @staticmethod
    def _delivery(db, delivery_id):
        identifier(delivery_id)
        row = db.execute("SELECT * FROM result_deliveries WHERE delivery_id=?", (delivery_id,)).fetchone()
        if row is None:
            raise PlatformError("delivery_not_found", "There is no delivery with this identifier.")
        return row

    @staticmethod
    def _delivery_view(row):
        hidden = {"claim_hash", "text", "envelope", "policy"}
        result = {key: row[key] for key in row.keys() if key not in hidden}
        result["receipt"] = json.loads(result["receipt"]) if result["receipt"] else None
        result["destination_binding"] = json.loads(result["destination_binding"]) if result["destination_binding"] else None
        result["agent_processed"] = None
        result["user_read"] = None
        return result

    @staticmethod
    def _delivery_policy(row):
        try:
            policy = json.loads(row["policy"])
            if (type(policy["lease_seconds"]) not in (int, float) or not 15 <= policy["lease_seconds"] <= 300
                    or type(policy["retry_seconds"]) not in (int, float) or not 5 <= policy["retry_seconds"] <= 60
                    or type(policy["max_sends"]) is not int or policy["max_sends"] != 3
                    or type(policy["result_limit"]) is not int or not 1 <= policy["result_limit"] <= TEXT_LIMIT
                    or type(policy["envelope_limit"]) is not int or not 1 <= policy["envelope_limit"] <= ENVELOPE_LIMIT
                    or policy["receipt_protocol"] != RECEIPT_PROTOCOL):
                raise ValueError("policy")
            return policy
        except (TypeError, ValueError, KeyError):
            raise PlatformError("delivery_policy", "The immutable delivery policy is invalid.") from None

    def _delivery_event(self, db, row, kind, detail):
        db.execute("INSERT INTO delivery_evidence VALUES (?,?,?,?,?)", (
            str(uuid.uuid4()), row["delivery_id"], kind, canonical_json(detail), self._delivery_stamp()))
        self._routing_event(db, row["route_id"], row["admission_id"], kind,
                            {"delivery_id": row["delivery_id"], **detail})

    def _create_result_delivery(self, db, task, attempt, output, error_json):
        """Create intent alongside results; no external I/O, truncation, or execution."""
        admission = db.execute("SELECT * FROM source_admissions WHERE task_id=?", (task["task_id"],)).fetchone()
        if admission is None:
            return None
        if admission["owner_lane"] != "v3":
            raise PlatformError("delivery_scope", "Only V3 admissions can publish platform result deliveries.")
        policy = self._delivery_policy(admission)
        text = output if output is not None else (error_json or "")
        body = utf8(text, code="invalid_result")
        delivery_id = str(uuid.uuid4())
        envelope = {
            "purpose": "masteragent.result-delivery", "payload_version": PAYLOAD_VERSION,
            "delivery_id": delivery_id, "admission_id": admission["admission_id"],
            "route_id": admission["route_id"], "route_epoch": admission["route_epoch"],
            "installation_id": task["installation_id"], "role_id": attempt["role_id"],
            "generation": attempt["generation"], "task_id": task["task_id"],
            "attempt_id": attempt["attempt_id"], "result_id": task["task_id"],
            "destination_namespace": admission["destination_namespace"],
            "destination_id": admission["destination_id"], "result_status": "error" if error_json else "ok",
            "text": text, "text_bytes": len(body), "text_sha256": digest(text),
        }
        encoded = canonical_json(envelope)
        state, reason = "pending", None
        try:
            validate_envelope(envelope, policy["result_limit"], policy["envelope_limit"])
        except PlatformError as exc:
            # A completed model result remains intact even if this destination
            # cannot carry it. Fixing transport never authorizes another run.
            state, reason = "rejected", exc.code
        stamp = self._delivery_stamp()
        db.execute("""INSERT INTO result_deliveries (
            delivery_id,admission_id,route_id,route_epoch,installation_id,role_id,generation,
            task_id,attempt_id,result_task_id,destination_namespace,destination_id,payload_version,
            text,text_bytes,text_sha256,envelope,envelope_bytes,envelope_sha256,policy,state,revision,
            claim_epoch,sender_id,claim_hash,expires_at,send_count,last_sent_at,next_send_at,reason,
            receipt,created_at,updated_at,received_at,destination_binding)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,0,NULL,NULL,NULL,0,NULL,NULL,?,NULL,?,?,NULL,?)""", (
            delivery_id, admission["admission_id"], admission["route_id"], admission["route_epoch"],
            task["installation_id"], attempt["role_id"], attempt["generation"], task["task_id"],
            attempt["attempt_id"], task["task_id"], admission["destination_namespace"],
            admission["destination_id"], PAYLOAD_VERSION, text, len(body), digest(text), encoded,
            len(utf8(encoded)), digest(encoded), admission["policy"], state, reason, stamp, stamp,
            canonical_json(policy["destination_binding"]) if "destination_binding" in policy else None))
        row = self._delivery(db, delivery_id)
        self._delivery_event(db, row, "delivery_" + state,
                             {"text_bytes": len(body), "text_sha256": digest(text), "reason": reason})
        return self._delivery_view(row)

    def deliveries(self, installation_id=None):
        with self._db() as db:
            if installation_id is not None:
                self._installation(db, installation_id)
            return [self._delivery_view(row) for row in db.execute("""SELECT * FROM result_deliveries
                WHERE (? IS NULL OR installation_id=?) ORDER BY rowid""", (installation_id, installation_id))]

    def delivery_status(self, delivery_id):
        with self._db() as db:
            return self._delivery_view(self._delivery(db, delivery_id))

    def delivery_schedule(self, route_id):
        """Read one route's due deliveries and next lease/backoff wake deadline.

        This is a snapshot, not a claim. Expired sending records remain uncertain
        and still require claim_delivery to fence them; the query never replays.
        """
        with self._db() as db:
            route = self._route(db, route_id)
            now = self._clock_now()
            ready, waits = [], []
            rows = db.execute("""SELECT * FROM result_deliveries
                WHERE route_id=? AND state IN ('pending','claimed','sending') ORDER BY rowid""", (route_id,))
            for row in rows:
                if not route["delivery_open"] and row["state"] != "sending":
                    continue
                # Expiring an uncertain send only fences it; it is not another
                # send, and must not wait for a send backoff or an open route.
                due = (row["expires_at"] or 0) if row["state"] == "sending" else max(
                    row["expires_at"] or 0, row["next_send_at"] or 0)
                if due > now:
                    waits.append(due - now)
                else:
                    ready.append(self._delivery_view(row))
            return {"ready": ready, "next_wake_seconds": min(waits, default=None)}

    def _delivery_envelope(self, row):
        try:
            envelope = json.loads(row["envelope"])
            policy = self._delivery_policy(row)
            encoded = validate_envelope(envelope, policy["result_limit"], policy["envelope_limit"])
            if (encoded != row["envelope"] or digest(encoded) != row["envelope_sha256"]
                    or len(utf8(encoded)) != row["envelope_bytes"]):
                raise ValueError("envelope")
            keys = ("delivery_id", "admission_id", "route_id", "route_epoch", "installation_id", "role_id",
                    "generation", "task_id", "attempt_id", "destination_namespace", "destination_id",
                    "payload_version", "text", "text_bytes", "text_sha256")
            if any(envelope[key] != row[key] for key in keys) or envelope["result_id"] != row["result_task_id"]:
                raise ValueError("scope")
            return envelope
        except (ValueError, KeyError, TypeError):
            raise PlatformError("delivery_integrity", "Stored delivery envelope failed full integrity verification.") from None

    def delivery_envelope(self, delivery_id):
        """Explicit read of the immutable payload, separate from receipt/summary lists."""
        with self._db() as db:
            return self._delivery_envelope(self._delivery(db, delivery_id))

    @staticmethod
    def _delivery_open(db, row):
        route = db.execute("SELECT delivery_open FROM routes WHERE route_id=?", (row["route_id"],)).fetchone()
        if route is None or not route["delivery_open"]:
            raise PlatformError("delivery_frozen", "This route does not currently allow new result sends.")

    @staticmethod
    def _receiver_descriptor(receiver):
        try:
            descriptor = receiver.descriptor()
        except (AttributeError, OSError) as exc:
            raise PlatformError("destination_unavailable", "A readable initialized destination descriptor is required.") from exc
        keys = {"root", "destination_namespace", "destination_id", "destination_instance", "protocol_version",
                "max_text_bytes", "max_envelope_bytes"}
        if (not isinstance(descriptor, dict) or set(descriptor) != keys
                or descriptor["protocol_version"] != RECEIPT_PROTOCOL
                or type(descriptor["max_text_bytes"]) is not int or not 1 <= descriptor["max_text_bytes"] <= TEXT_LIMIT
                or type(descriptor["max_envelope_bytes"]) is not int
                or not 1 <= descriptor["max_envelope_bytes"] <= ENVELOPE_LIMIT):
            raise PlatformError("destination_protocol", "Destination must declare its durable receive/query protocol and limits.")
        for key in ("destination_namespace", "destination_id", "destination_instance"):
            identifier(descriptor[key])
        if str(_explicit_path(descriptor["root"])) != descriptor["root"]:
            raise PlatformError("destination_protocol", "Destination descriptor must use its canonical absolute root.")
        return dict(descriptor)

    @staticmethod
    def _match_destination(row, descriptor, *, require_bound=True):
        if (descriptor["destination_namespace"], descriptor["destination_id"]) != (
                row["destination_namespace"], row["destination_id"]):
            raise PlatformError("delivery_destination_mismatch", "The receiver does not match this delivery's fixed destination.")
        if row["destination_binding"]:
            if canonical_json(descriptor) != row["destination_binding"]:
                raise PlatformError("delivery_destination_mismatch", "The destination root or persistent instance changed; no receipt authority can be assumed.")
        elif require_bound:
            raise PlatformError("delivery_destination_unbound", "Bind an initialized destination before beginning any send.")

    def _delivery_owned(self, row, sender_id, claim_epoch, claim_token):
        if (not isinstance(claim_token, str) or type(claim_epoch) is not int
                or sender_id != row["sender_id"] or claim_epoch != row["claim_epoch"]
                or not row["claim_hash"] or not secrets.compare_digest(row["claim_hash"], digest(claim_token))):
            raise PlatformError("delivery_owner", "The exact sender and current delivery claim are required.")
        if row["state"] not in ("claimed", "sending") or row["expires_at"] <= self._clock_now():
            raise PlatformError("delivery_fenced", "This delivery claim has expired or been fenced.")

    def _delivery_hold(self, db, row, reason):
        db.execute("""UPDATE result_deliveries SET state='held',reason=?,revision=revision+1,
            claim_epoch=claim_epoch+1,claim_hash=NULL,expires_at=NULL,updated_at=? WHERE delivery_id=?""",
                   (reason, self._delivery_stamp(), row["delivery_id"]))
        updated = self._delivery(db, row["delivery_id"])
        self._delivery_event(db, updated, "delivery_held", {"reason": reason})
        return updated

    def claim_delivery(self, delivery_id, sender_id, receiver):
        identifier(sender_id)
        descriptor = self._receiver_descriptor(receiver)
        rejection = None
        result = None
        with self._db(write=True) as db:
            row = self._delivery(db, delivery_id)
            self._match_destination(row, descriptor, require_bound=False)
            now, policy = self._clock_now(), self._delivery_policy(row)
            if row["state"] == "sending" and row["expires_at"] <= now:
                self._delivery_hold(db, row, "sending_lease_expired")
                rejection = PlatformError("delivery_held", "Expired sending must be reconciled with the destination before retry.")
            else:
                self._delivery_open(db, row)
                if row["state"] not in ("pending", "claimed"):
                    raise PlatformError("delivery_not_pending", "Only pending or expired unsent claims may be claimed.")
                if row["state"] == "claimed" and row["expires_at"] > now:
                    raise PlatformError("delivery_busy", "Another sender still owns this delivery claim.")
                if row["next_send_at"] is not None and row["next_send_at"] > now:
                    raise PlatformError("delivery_backoff", "The durable retry interval has not elapsed.")
                if row["send_count"] >= policy["max_sends"]:
                    self._delivery_hold(db, row, "send_limit")
                    rejection = PlatformError("delivery_send_limit", "The send limit requires explicit local follow-up.")
                else:
                    if (descriptor["max_text_bytes"] < row["text_bytes"]
                            or descriptor["max_envelope_bytes"] < row["envelope_bytes"]):
                        raise PlatformError("destination_limit_mismatch", "The initialized receiver cannot store this complete delivery.")
                    token = secrets.token_urlsafe(32)
                    db.execute("""UPDATE result_deliveries SET state='claimed',claim_epoch=claim_epoch+1,
                        sender_id=?,claim_hash=?,expires_at=?,revision=revision+1,updated_at=?,reason=NULL
                        ,destination_binding=COALESCE(destination_binding,?)
                        WHERE delivery_id=?""", (sender_id, digest(token), now + policy["lease_seconds"],
                                                self._delivery_stamp(), canonical_json(descriptor), delivery_id))
                    updated = self._delivery(db, delivery_id)
                    self._delivery_event(db, updated, "delivery_claimed", {"claim_epoch": updated["claim_epoch"]})
                    result = {**self._delivery_view(updated), "claim_token": token}
        if rejection:
            raise rejection
        return result

    def renew_delivery(self, delivery_id, sender_id, claim_epoch, claim_token):
        with self._db(write=True) as db:
            row = self._delivery(db, delivery_id)
            self._delivery_owned(row, sender_id, claim_epoch, claim_token)
            db.execute("""UPDATE result_deliveries SET expires_at=?,revision=revision+1,updated_at=?
                WHERE delivery_id=?""", (self._clock_now() + self._delivery_policy(row)["lease_seconds"],
                                        self._delivery_stamp(), delivery_id))
            return self._delivery_view(self._delivery(db, delivery_id))

    def begin_delivery(self, delivery_id, sender_id, claim_epoch, claim_token):
        with self._db(write=True) as db:
            row = self._delivery(db, delivery_id)
            self._delivery_owned(row, sender_id, claim_epoch, claim_token)
            self._delivery_open(db, row)
            if not row["destination_binding"]:
                raise PlatformError("delivery_destination_unbound", "An initialized fixed destination must be bound before sending.")
            if row["state"] != "claimed":
                raise PlatformError("delivery_already_sending", "An existing sending intent must not start another receive call.")
            policy = self._delivery_policy(row)
            if row["send_count"] >= policy["max_sends"]:
                raise PlatformError("delivery_send_limit", "This delivery exhausted its send allowance.")
            envelope = self._delivery_envelope(row)
            now = self._clock_now()
            db.execute("""UPDATE result_deliveries SET state='sending',send_count=send_count+1,last_sent_at=?,
                next_send_at=?,revision=revision+1,updated_at=? WHERE delivery_id=?""",
                       (now, now + policy["retry_seconds"], self._delivery_stamp(), delivery_id))
            self._delivery_event(db, self._delivery(db, delivery_id), "delivery_sending", {"claim_epoch": claim_epoch})
            return envelope

    @staticmethod
    def _validate_receipt(row, receipt):
        if not row["destination_binding"]:
            raise PlatformError("delivery_destination_unbound", "A receipt requires a bound destination instance.")
        binding = json.loads(row["destination_binding"])
        expected = {"purpose": "masteragent.delivery-receipt", "protocol_version": RECEIPT_PROTOCOL,
                    "destination_namespace": row["destination_namespace"], "destination_id": row["destination_id"],
                    "destination_instance": binding["destination_instance"],
                    "delivery_id": row["delivery_id"], "text_bytes": row["text_bytes"],
                    "text_sha256": row["text_sha256"], "envelope_sha256": row["envelope_sha256"]}
        if (not isinstance(receipt, dict) or set(receipt) != set(expected) | {"receipt_id", "storage_revision", "received_at"}
                or any(receipt[key] != value for key, value in expected.items())
                or type(receipt["text_bytes"]) is not int
                or type(receipt["storage_revision"]) is not int or receipt["storage_revision"] < 1):
            raise PlatformError("receipt_mismatch", "Receipt does not prove storage of this exact delivery and destination.")
        identifier(receipt["receipt_id"])
        identifier(receipt["received_at"])

    def _record_receipt(self, db, row, receipt, kind):
        self._validate_receipt(row, receipt)
        db.execute("""UPDATE result_deliveries SET state='received',receipt=?,received_at=?,
            revision=revision+1,claim_hash=NULL,expires_at=NULL,reason=NULL,updated_at=? WHERE delivery_id=?""",
                   (canonical_json(receipt), receipt["received_at"], self._delivery_stamp(), row["delivery_id"]))
        updated = self._delivery(db, row["delivery_id"])
        self._delivery_event(db, updated, kind, {"receipt_id": receipt["receipt_id"],
                                               "storage_revision": receipt["storage_revision"]})
        return self._delivery_view(updated)

    def complete_delivery(self, delivery_id, sender_id, claim_epoch, claim_token, receipt):
        with self._db(write=True) as db:
            row = self._delivery(db, delivery_id)
            self._delivery_owned(row, sender_id, claim_epoch, claim_token)
            if row["state"] != "sending":
                raise PlatformError("delivery_not_sending", "A receipt requires a durable sending intent.")
            return self._record_receipt(db, row, receipt, "delivery_received")

    def hold_delivery(self, delivery_id, sender_id, claim_epoch, claim_token, reason="receive_unconfirmed"):
        # Reasons are finite codes: never copy endpoint errors or untrusted text
        # into ordinary audit logs.
        if reason not in ("receive_unconfirmed", "receipt_mismatch", "sender_stopped"):
            raise PlatformError("invalid_delivery_reason", "Use a supported delivery hold reason.")
        with self._db(write=True) as db:
            row = self._delivery(db, delivery_id)
            self._delivery_owned(row, sender_id, claim_epoch, claim_token)
            return self._delivery_view(self._delivery_hold(db, row, reason))

    def send_delivery_once(self, delivery_id, sender_id, receiver):
        """Explicit one-shot send; never retries an uncertain receive or a model."""
        claim = self.claim_delivery(delivery_id, sender_id, receiver)
        args = (delivery_id, sender_id, claim["claim_epoch"], claim["claim_token"])
        envelope = self.begin_delivery(*args)
        try:
            receipt = receiver.receive(envelope)
            return self.complete_delivery(*args, receipt)
        except Exception:
            # A thrown exception may occur after the other database commits.
            # Never infer absence, and never discard the original delivery ID.
            try:
                return self.hold_delivery(*args)
            except PlatformError:
                return self.delivery_status(delivery_id)

    def reconcile_delivery(self, delivery_id, receiver, *, retry_absent=False):
        """Fence uncertain sends, query their fixed destination, and optionally requeue.

        This operation can requeue the original bytes only on a matching
        authoritative absent response. It never calls receive or claims work.
        """
        if type(retry_absent) is not bool:
            raise PlatformError("invalid_reconcile", "Retry of authoritative absence must be explicit.")
        descriptor = self._receiver_descriptor(receiver)
        with self._db(write=True) as db:
            row = self._delivery(db, delivery_id)
            self._match_destination(row, descriptor)
            if row["state"] == "received":
                return self._delivery_view(row)
            if row["state"] == "sending":
                if row["expires_at"] > self._clock_now():
                    raise PlatformError("delivery_busy", "The live sender still owns this sending intent.")
                row = self._delivery_hold(db, row, "sending_lease_expired")
            if row["state"] != "held":
                raise PlatformError("delivery_not_held", "Only uncertain held deliveries need receipt reconciliation.")
            envelope = self._delivery_envelope(row)
            revision = row["revision"]
        # The destination is a separate authority. Never hold the core writer
        # lock while it reads its own database or raises an unavailable error.
        try:
            response = receiver.query(envelope)
        except Exception:
            response = {"status": "unavailable"}
        with self._db(write=True) as db:
            row = self._delivery(db, delivery_id)
            if row["state"] == "received":
                return self._delivery_view(row)
            if row["state"] != "held" or row["revision"] != revision:
                raise PlatformError("delivery_changed", "Delivery state changed during destination lookup; inspect its current state.")
            status = response.get("status") if isinstance(response, dict) else "unavailable"
            if status == "received":
                try:
                    self._validate_receipt(row, response.get("receipt"))
                except PlatformError:
                    status = "conflict"
                else:
                    return self._record_receipt(db, row, response["receipt"], "delivery_receipt_reconciled")
            if status == "absent":
                authority = {"protocol_version": RECEIPT_PROTOCOL,
                             "destination_namespace": row["destination_namespace"],
                             "destination_instance": descriptor["destination_instance"],
                             "destination_id": row["destination_id"], "delivery_id": row["delivery_id"],
                             "envelope_sha256": row["envelope_sha256"]}
                if any(response.get(key) != value for key, value in authority.items()):
                    status = "unavailable"
                elif retry_absent:
                    self._delivery_open(db, row)
                    if row["send_count"] >= self._delivery_policy(row)["max_sends"]:
                        status = "send_limit"
                    else:
                        db.execute("""UPDATE result_deliveries SET state='pending',reason='authoritative_absent',
                            revision=revision+1,updated_at=? WHERE delivery_id=?""", (self._delivery_stamp(), delivery_id))
                        self._delivery_event(db, row, "delivery_retry_authorized", {"lookup": "authoritative_absent"})
                        return self._delivery_view(self._delivery(db, delivery_id))
            if status not in ("absent", "conflict", "send_limit"):
                status = "unavailable"
            db.execute("""UPDATE result_deliveries SET reason=?,revision=revision+1,updated_at=?
                WHERE delivery_id=?""", ("receipt_" + status, self._delivery_stamp(), delivery_id))
            self._delivery_event(db, row, "delivery_receipt_checked", {"lookup": status})
            return self._delivery_view(self._delivery(db, delivery_id))
