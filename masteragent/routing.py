"""Transactional routing for explicitly configured, coordinated sources.

This module does not discover or read legacy inboxes. Administrative route
configuration is separate from bearer source/legacy execution leases.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import secrets
import sqlite3
import unicodedata
import uuid

from .errors import PlatformError
from .paths import PathSafetyError, _check_directory_boundary, _explicit_path, _overlap


def _now():
    return datetime.now(timezone.utc).isoformat()


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _digest(text):
    try:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()
    except UnicodeError:
        raise PlatformError("invalid_route_text", "Protocol text must be valid UTF-8.") from None


def _identifier(value):
    try:
        valid = (type(value) is str and bool(value.strip()) and len(value.encode("utf-8")) <= 200
                 and not any(unicodedata.category(char) == "Cc" for char in value))
    except UnicodeError:
        valid = False
    if not valid:
        raise PlatformError("invalid_route_identifier", "Identifiers require nonempty UTF-8 text within 200 bytes, without control characters.")
    return value


def _source_key(value):
    if type(value) is int:
        try:
            encoded = _json({"type": "integer", "value": value})
        except ValueError:
            raise PlatformError("invalid_source_id", "Source identifiers must fit the canonical byte limit.") from None
    elif type(value) is str:
        encoded = _json({"type": "string", "value": _identifier(value)})
    else:
        raise PlatformError("invalid_source_id", "Source identifiers must be strings or integers, never booleans.")
    if len(encoded.encode("utf-8")) > 200:
        raise PlatformError("invalid_source_id", "Canonical source identifiers must fit 200 UTF-8 bytes.")
    return encoded


def _text(value, limit, *, empty=False):
    try:
        valid = type(value) is str and (empty or bool(value.strip())) and len(value.encode("utf-8")) <= limit
    except UnicodeError:
        valid = False
    if not valid:
        raise PlatformError("invalid_route_text", f"Text must be valid UTF-8 within {limit} bytes.")
    return value


def _integer(value, minimum, maximum, label):
    if type(value) is not int or not minimum <= value <= maximum:
        raise PlatformError("invalid_route_policy", f"{label} must be an integer within {minimum}–{maximum}.")
    return value


class RoutingMixin:
    @staticmethod
    def _routing_event(db, route_id, admission_id, kind, detail):
        db.execute("INSERT INTO routing_events VALUES (?,?,?,?,?,?)", (
            str(uuid.uuid4()), route_id, admission_id, kind, _json(detail), _now()))

    @staticmethod
    def _route(db, route_id):
        row = db.execute("SELECT * FROM routes WHERE route_id=?", (_identifier(route_id),)).fetchone()
        if row is None:
            raise PlatformError("route_not_found", "The explicit route does not exist.")
        return row

    @staticmethod
    def _route_view(row):
        value = dict(row)
        value["policy"] = json.loads(value["policy"])
        for name in ("admission_open", "claim_open", "delivery_open"):
            value[name] = bool(value[name])
        return value

    @staticmethod
    def _admission_view(row):
        value = dict(row)
        value["source_id"] = json.loads(value.pop("source_key"))["value"]
        value["policy"] = json.loads(value["policy"])
        value["source_confirmed"] = bool(value["source_confirmed"])
        return value

    @staticmethod
    def _admission(db, admission_id):
        row = db.execute("SELECT * FROM source_admissions WHERE admission_id=?", (_identifier(admission_id),)).fetchone()
        if row is None:
            raise PlatformError("admission_not_found", "The source admission does not exist.")
        return row

    @staticmethod
    def _save_route_version(db, row):
        snapshot = _json(dict(row))
        db.execute("INSERT INTO route_versions VALUES (?,?,?,?,?)", (
            row["route_id"], row["route_epoch"], snapshot, _digest(snapshot), _now()))

    @staticmethod
    def _source_descriptor(source):
        """Read a fresh descriptor before acquiring the core write reservation."""
        try:
            descriptor = source.descriptor()
        except (AttributeError, OSError) as exc:
            raise PlatformError("source_unavailable", "A readable initialized source descriptor is required.") from exc
        keys = {"root", "source_namespace", "source_instance", "protocol_version",
                "max_text_bytes", "max_envelope_bytes"}
        if (not isinstance(descriptor, dict) or set(descriptor) != keys
                or descriptor["protocol_version"] != "ma-source-v1"
                or type(descriptor["max_text_bytes"]) is not int
                or not 1 <= descriptor["max_text_bytes"] <= 64000
                or type(descriptor["max_envelope_bytes"]) is not int
                or not 1 <= descriptor["max_envelope_bytes"] <= 1048576):
            raise PlatformError("source_protocol", "Source must declare its durable text protocol and limits.")
        for key in ("source_namespace", "source_instance"):
            _identifier(descriptor[key])
        try:
            canonical_root = str(_explicit_path(descriptor["root"]))
        except PathSafetyError as exc:
            raise PlatformError("source_protocol", "Source descriptor must use its canonical absolute root.") from exc
        if canonical_root != descriptor["root"]:
            raise PlatformError("source_protocol", "Source descriptor must use its canonical absolute root.")
        # Detach from endpoint-owned dictionaries: a caller cannot mutate the
        # validated snapshot while we wait for the core transaction.
        return dict(descriptor)

    @staticmethod
    def _endpoint_view(row):
        if row is None:
            return None
        return {"route_id": row["route_id"], "source_binding": json.loads(row["source_binding"]),
                "destination_binding": json.loads(row["destination_binding"]), "created_at": row["created_at"]}

    @staticmethod
    def _check_endpoint_policy(route, source, destination):
        policy = json.loads(route["policy"])
        if source["source_namespace"] != route["source_namespace"]:
            raise PlatformError("route_source_mismatch", "The source does not match this route's fixed namespace.")
        if (destination["destination_namespace"], destination["destination_id"]) != (
                route["destination_namespace"], route["destination_id"]):
            raise PlatformError("route_destination_mismatch", "The receiver does not match this route's fixed destination.")
        if (source["max_text_bytes"] < policy["input_limit"]
                or source["max_envelope_bytes"] < policy["envelope_limit"]
                or destination["max_text_bytes"] < policy["result_limit"]
                or destination["max_envelope_bytes"] < policy["envelope_limit"]
                or destination["protocol_version"] != policy["receipt_protocol"]):
            raise PlatformError("route_endpoint_limits", "Route limits exceed the bound source or destination protocol.")

    @staticmethod
    def _preflight_endpoint_roots(source, destination):
        """Filesystem boundary checks belong outside the core transaction."""
        for descriptor in (source, destination):
            if descriptor is not None:
                _check_directory_boundary(Path(descriptor["root"]))

    def _guard_endpoint_overlaps(self, db, source, destination):
        # These paths were normalized before the transaction. Compare them with
        # the authoritative current workspace inventory without filesystem I/O.
        endpoints = (Path(source["root"]), Path(destination["root"]))
        workspaces = [Path(row[0]) for row in db.execute("SELECT workspace FROM installations")]
        workspaces.extend(Path(row[0]) for row in db.execute("SELECT workspace FROM role_deployments"))
        if (_overlap(*endpoints)
                or any(_overlap(endpoint, boundary) for endpoint in endpoints
                       for boundary in (self.state_root, *workspaces))):
            raise PlatformError("route_endpoint_overlap", "Source, receiver, platform state and business workspaces must use separate roots.")

    def bind_route_endpoints(self, route_id, expected_epoch, source, receiver):
        """Persist the two endpoint identities before the first controlled admission."""
        _integer(expected_epoch, 1, 2**53 - 1, "Expected epoch")
        source_descriptor = self._source_descriptor(source)
        destination_descriptor = self._receiver_descriptor(receiver)
        self._preflight_endpoint_roots(source_descriptor, destination_descriptor)
        with self._db(write=True) as db:
            route = self._route(db, route_id)
            if route["route_epoch"] != expected_epoch:
                raise PlatformError("stale_route", "Endpoint binding requires the current route epoch.")
            self._check_endpoint_policy(route, source_descriptor, destination_descriptor)
            self._guard_endpoint_overlaps(db, source_descriptor, destination_descriptor)
            previous = db.execute("SELECT * FROM route_endpoints WHERE route_id=?", (route_id,)).fetchone()
            encoded_source, encoded_destination = _json(source_descriptor), _json(destination_descriptor)
            if previous is not None:
                if (previous["source_binding"], previous["destination_binding"]) != (encoded_source, encoded_destination):
                    raise PlatformError("route_endpoints_immutable", "A route's bound roots and persistent endpoint instances cannot change.")
                return self._endpoint_view(previous)
            if db.execute("SELECT 1 FROM source_admissions WHERE route_id=?", (route_id,)).fetchone():
                raise PlatformError("route_has_admissions", "Historical admissions cannot acquire endpoint provenance retroactively; use a new source namespace.")
            db.execute("INSERT INTO route_endpoints VALUES (?,?,?,?)", (
                route_id, encoded_source, encoded_destination, _now()))
            self._routing_event(db, route_id, None, "route_endpoints_bound", {
                "source_instance": source_descriptor["source_instance"],
                "destination_instance": destination_descriptor["destination_instance"]})
            return self._endpoint_view(db.execute("SELECT * FROM route_endpoints WHERE route_id=?", (route_id,)).fetchone())

    def route_endpoints(self, route_id):
        with self._db() as db:
            self._route(db, route_id)
            return self._endpoint_view(db.execute("SELECT * FROM route_endpoints WHERE route_id=?", (route_id,)).fetchone())

    def configure_route(self, route_id, source_namespace, owner_lane, installation_id,
                        role_id, generation, destination_namespace, destination_id, *,
                        expected_epoch=0, admission_open=True, claim_open=True,
                        delivery_open=True, destination_limit=512000,
                        lease_seconds=60, retry_seconds=5):
        for value in (route_id, source_namespace, installation_id, role_id, destination_namespace, destination_id):
            _identifier(value)
        if owner_lane not in ("legacy", "v3") or type(owner_lane) is not str:
            raise PlatformError("invalid_route_lane", "A route must select legacy or v3 explicitly.")
        _integer(expected_epoch, 0, 2**53 - 2, "Expected epoch")
        _integer(generation, 1, 2**53 - 1, "Generation")
        _integer(destination_limit, 1, 512000, "Destination text limit")
        _integer(lease_seconds, 15, 300, "Transport lease")
        _integer(retry_seconds, 5, 60, "Retry interval")
        if any(type(value) is not bool for value in (admission_open, claim_open, delivery_open)):
            raise PlatformError("invalid_route_policy", "Route controls must be explicit booleans.")
        policy = _json({"input_limit": 64000, "result_limit": destination_limit,
                        "envelope_limit": 1048576, "lease_seconds": lease_seconds,
                        "retry_seconds": retry_seconds, "max_sends": 3,
                        "receipt_protocol": "ma-receipt-v1"})
        with self._db(write=True) as db:
            installation = self._installation(db, installation_id, active=True)
            self._guard_command_generation(installation, generation)
            self._role(installation, role_id)
            previous = db.execute("SELECT * FROM routes WHERE route_id=?", (route_id,)).fetchone()
            if (previous is None and expected_epoch != 0) or (previous is not None and previous["route_epoch"] != expected_epoch):
                raise PlatformError("stale_route", "Route configuration changed; read its current epoch before updating.")
            if previous is not None and previous["source_namespace"] != source_namespace:
                raise PlatformError("route_source_immutable", "A route cannot change its source namespace.")
            binding = db.execute("SELECT * FROM route_endpoints WHERE route_id=?", (route_id,)).fetchone()
            if binding is not None:
                if any(previous[key] != value for key, value in (
                        ("installation_id", installation_id), ("role_id", role_id),
                        ("destination_namespace", destination_namespace), ("destination_id", destination_id))):
                    raise PlatformError("route_endpoints_immutable", "A bound route cannot change its installation, role or destination.")
                self._check_endpoint_policy(
                    {**dict(previous), "policy": policy},
                    json.loads(binding["source_binding"]), json.loads(binding["destination_binding"]))
            other = db.execute("SELECT route_id FROM routes WHERE source_namespace=?", (source_namespace,)).fetchone()
            if other is not None and other["route_id"] != route_id:
                raise PlatformError("source_route_conflict", "The source namespace already has one authoritative route.")
            timestamp = _now()
            values = (source_namespace, expected_epoch + 1, owner_lane, installation_id,
                      role_id, generation, destination_namespace, destination_id,
                      int(admission_open), int(claim_open), int(delivery_open), policy,
                      _digest(policy), previous["created_at"] if previous else timestamp, timestamp)
            if previous is None:
                db.execute("INSERT INTO routes VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (route_id, *values))
            else:
                db.execute("""UPDATE routes SET source_namespace=?,route_epoch=?,owner_lane=?,
                    installation_id=?,role_id=?,generation=?,destination_namespace=?,destination_id=?,
                    admission_open=?,claim_open=?,delivery_open=?,policy=?,policy_digest=?,created_at=?,updated_at=?
                    WHERE route_id=?""", (*values, route_id))
            row = self._route(db, route_id)
            self._save_route_version(db, row)
            self._routing_event(db, route_id, None, "route_configured", {"route_epoch": row["route_epoch"], "owner_lane": owner_lane})
            return self._route_view(row)

    def set_route_controls(self, route_id, expected_epoch, *, admission_open=None, claim_open=None, delivery_open=None):
        _integer(expected_epoch, 1, 2**53 - 2, "Expected epoch")
        controls = {key: value for key, value in (("admission_open", admission_open), ("claim_open", claim_open), ("delivery_open", delivery_open)) if value is not None}
        if not controls or any(type(value) is not bool for value in controls.values()):
            raise PlatformError("invalid_route_policy", "At least one boolean route control is required.")
        with self._db(write=True) as db:
            row = self._route(db, route_id)
            if row["route_epoch"] != expected_epoch:
                raise PlatformError("stale_route", "Route controls require the current epoch.")
            flags = [int(controls.get(name, bool(row[name]))) for name in ("admission_open", "claim_open", "delivery_open")]
            db.execute("UPDATE routes SET route_epoch=?,admission_open=?,claim_open=?,delivery_open=?,updated_at=? WHERE route_id=?",
                       (expected_epoch + 1, *flags, _now(), route_id))
            row = self._route(db, route_id)
            self._save_route_version(db, row)
            self._routing_event(db, route_id, None, "route_controls", {"route_epoch": row["route_epoch"], **controls})
            return self._route_view(row)

    def routes(self):
        with self._db() as db:
            return [self._route_view(row) for row in db.execute("SELECT * FROM routes ORDER BY route_id")]

    def admissions(self, route_id=None):
        with self._db() as db:
            if route_id is not None:
                self._route(db, route_id)
            rows = db.execute("SELECT * FROM source_admissions WHERE (? IS NULL OR route_id=?) ORDER BY rowid", (route_id, route_id))
            return [self._admission_view(row) for row in rows]

    def admission(self, admission_id):
        with self._db() as db:
            return self._admission_view(self._admission(db, admission_id))

    def source_admission(self, route_id, source_id):
        source_key = _source_key(source_id)
        with self._db() as db:
            route = self._route(db, route_id)
            row = db.execute("SELECT * FROM source_admissions WHERE source_namespace=? AND source_key=?",
                             (route["source_namespace"], source_key)).fetchone()
            return self._admission_view(row) if row is not None else None

    def claim_source(self, route_id, source_id, owner_id):
        source_key, owner_id = _source_key(source_id), _identifier(owner_id)
        with self._db(write=True) as db:
            route = self._route(db, route_id)
            args = (route["source_namespace"], source_key)
            prior_admission = db.execute("SELECT 1 FROM source_admissions WHERE source_namespace=? AND source_key=?", args).fetchone()
            if not route["admission_open"] and prior_admission is None:
                raise PlatformError("route_admission_closed", "New source admissions are frozen.")
            previous = db.execute("SELECT * FROM source_claims WHERE source_namespace=? AND source_key=?", args).fetchone()
            now = self._clock_now()
            if previous is not None and previous["expires_at"] > now:
                raise PlatformError("source_claim_busy", "A source transport lease is already active.")
            epoch = previous["claim_epoch"] + 1 if previous else 1
            token = secrets.token_urlsafe(32)
            lease_seconds = json.loads(route["policy"])["lease_seconds"]
            expires = now + lease_seconds
            db.execute("""INSERT INTO source_claims VALUES (?,?,?,?,?,?,?,?,?)
                ON CONFLICT(source_namespace,source_key) DO UPDATE SET
                route_id=excluded.route_id,claim_epoch=excluded.claim_epoch,owner_id=excluded.owner_id,
                lease_hash=excluded.lease_hash,expires_at=excluded.expires_at,policy=excluded.policy,updated_at=excluded.updated_at""",
                       (*args, route_id, epoch, owner_id, _digest(token), expires, route["policy"], _now()))
            return {"route_id": route_id, "route_epoch": route["route_epoch"], "source_id": source_id,
                    "owner_id": owner_id, "claim_epoch": epoch, "claim_token": token,
                    "expires_at": expires, "lease_seconds": lease_seconds, "owner_lane": route["owner_lane"]}

    def _source_owner(self, db, route, source_key, owner_id, claim_epoch, claim_token):
        _identifier(owner_id)
        row = db.execute("SELECT * FROM source_claims WHERE source_namespace=? AND source_key=?",
                         (route["source_namespace"], source_key)).fetchone()
        if (row is None or row["route_id"] != route["route_id"] or row["owner_id"] != owner_id
                or type(claim_epoch) is not int or row["claim_epoch"] != claim_epoch
                or type(claim_token) is not str or not secrets.compare_digest(row["lease_hash"], _digest(claim_token))):
            raise PlatformError("source_claim_owner", "The exact source transport lease is required.")
        if row["expires_at"] <= self._clock_now():
            raise PlatformError("source_claim_expired", "The source transport lease has expired.")
        return row

    def renew_source(self, route_id, source_id, owner_id, claim_epoch, claim_token):
        source_key = _source_key(source_id)
        with self._db(write=True) as db:
            route = self._route(db, route_id)
            lease = self._source_owner(db, route, source_key, owner_id, claim_epoch, claim_token)
            lease_seconds = json.loads(lease["policy"])["lease_seconds"]
            expires = self._clock_now() + lease_seconds
            db.execute("UPDATE source_claims SET expires_at=?,updated_at=? WHERE source_namespace=? AND source_key=?",
                       (expires, _now(), route["source_namespace"], source_key))
            return {"claim_epoch": claim_epoch, "expires_at": expires, "lease_seconds": lease_seconds}

    def release_source(self, route_id, source_id, owner_id, claim_epoch, claim_token):
        source_key = _source_key(source_id)
        with self._db(write=True) as db:
            route = self._route(db, route_id)
            self._source_owner(db, route, source_key, owner_id, claim_epoch, claim_token)
            expires = self._clock_now()
            db.execute("UPDATE source_claims SET expires_at=?,updated_at=? WHERE source_namespace=? AND source_key=?",
                       (expires, _now(), route["source_namespace"], source_key))
            return {"route_id": route_id, "source_id": source_id, "claim_epoch": claim_epoch, "expires_at": expires}

    def admit_source(self, route_id, route_epoch, source_id, text, owner_id,
                     source_claim_epoch, source_claim_token, kind="task", *, source=None, receiver=None):
        source_key = _source_key(source_id)
        _integer(route_epoch, 1, 2**53 - 1, "Route epoch")
        text = _text(text, 64000)
        if kind != "task" or type(kind) is not str:
            raise PlatformError("unsupported_source_kind", "This adapter admits explicit text tasks only.")
        # Endpoint I/O occurs outside the core transaction, including on replay.
        # The immutable stored binding is compared again under the write lock.
        source_descriptor = self._source_descriptor(source) if source is not None else None
        destination_descriptor = self._receiver_descriptor(receiver) if receiver is not None else None
        self._preflight_endpoint_roots(source_descriptor, destination_descriptor)
        with self._db(write=True) as db:
            current = self._route(db, route_id)
            self._source_owner(db, current, source_key, owner_id, source_claim_epoch, source_claim_token)
            binding = db.execute("SELECT * FROM route_endpoints WHERE route_id=?", (route_id,)).fetchone()
            if binding is not None:
                if source_descriptor is None or destination_descriptor is None:
                    raise PlatformError("route_endpoint_preflight_required", "Bound routes require fresh source and receiver descriptors for every admission.")
                if (_json(source_descriptor), _json(destination_descriptor)) != (
                        binding["source_binding"], binding["destination_binding"]):
                    raise PlatformError("route_endpoint_mismatch", "The source or receiver root, persistent instance, protocol or limits changed.")
                self._guard_endpoint_overlaps(db, source_descriptor, destination_descriptor)
            elif source_descriptor is not None or destination_descriptor is not None:
                raise PlatformError("route_endpoints_unbound", "Explicitly bind the route before admitting from controlled endpoints.")
            version = db.execute("SELECT * FROM route_versions WHERE route_id=? AND route_epoch=?", (route_id, route_epoch)).fetchone()
            if version is None or _digest(version["snapshot"]) != version["snapshot_digest"]:
                raise PlatformError("stale_route", "The route epoch is unavailable or invalid.")
            route = json.loads(version["snapshot"])
            envelope = {key: route[key] for key in ("route_id", "source_namespace", "owner_lane", "installation_id", "role_id", "generation", "destination_namespace", "destination_id")}
            envelope.update(source_key=source_key, kind=kind, text_sha256=_digest(text), text_bytes=len(text.encode("utf-8")))
            encoded = _json(envelope)
            envelope_digest = _digest(encoded)
            previous = db.execute("SELECT * FROM source_admissions WHERE source_namespace=? AND source_key=?", (route["source_namespace"], source_key)).fetchone()
            if previous is not None:
                if previous["envelope_sha256"] != envelope_digest:
                    raise PlatformError("source_conflict", "This source identity already refers to different immutable content or routing.")
                return self._admission_view(previous)
            if route_epoch != current["route_epoch"]:
                raise PlatformError("stale_route", "An old route epoch cannot admit new work.")
            if not current["admission_open"]:
                raise PlatformError("route_admission_closed", "New source admissions are frozen.")
            installation = self._installation(db, route["installation_id"], active=True)
            self._guard_command_generation(installation, route["generation"])
            self._role(installation, route["role_id"])
            policy = json.loads(route["policy"])
            _text(text, policy["input_limit"])
            if len(_json({**envelope, "text": text}).encode("utf-8")) > policy["envelope_limit"]:
                raise PlatformError("route_envelope_limit", "The complete source envelope exceeds its byte limit.")
            if binding is not None:
                policy["source_binding"] = json.loads(binding["source_binding"])
                policy["destination_binding"] = json.loads(binding["destination_binding"])
            task_id = str(uuid.uuid4()) if route["owner_lane"] == "v3" else None
            admission_id, timestamp = str(uuid.uuid4()), _now()
            if task_id is not None:
                db.execute("""INSERT INTO tasks (task_id,installation_id,role_id,generation,text,
                    client_request_id,state,created_at,completed_at,dispatch_generation,parent_task_id)
                    VALUES (?,?,?,?,?,NULL,'queued',?,NULL,?,NULL)""", (
                    task_id, route["installation_id"], route["role_id"], route["generation"], text, timestamp, route["generation"]))
            db.execute("INSERT INTO source_admissions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
                admission_id, route_id, route_epoch, route["source_namespace"], source_key, kind, text,
                envelope["text_bytes"], envelope["text_sha256"], envelope_digest, route["owner_lane"],
                route["installation_id"], route["role_id"], route["generation"], task_id,
                route["destination_namespace"], route["destination_id"], _json(policy), 0, timestamp, None))
            self._routing_event(db, route_id, admission_id, "source_admitted", {"task_id": task_id, "owner_lane": route["owner_lane"], "route_epoch": route_epoch})
            self._routing_event(db, route_id, admission_id, "source_confirmation_pending", {})
            return self._admission_view(self._admission(db, admission_id))

    def confirm_source(self, admission_id, owner_id, source_claim_epoch, source_claim_token):
        with self._db(write=True) as db:
            admission = self._admission(db, admission_id)
            route = self._route(db, admission["route_id"])
            self._source_owner(db, route, admission["source_key"], owner_id, source_claim_epoch, source_claim_token)
            if not admission["source_confirmed"]:
                db.execute("UPDATE source_admissions SET source_confirmed=1,confirmed_at=? WHERE admission_id=?", (_now(), admission_id))
                self._routing_event(db, route["route_id"], admission_id, "source_confirmation_recorded", {})
            return self._admission_view(self._admission(db, admission_id))

    @staticmethod
    def _route_claim_allowed(db, task):
        row = db.execute("""SELECT a.owner_lane,r.claim_open,a.installation_id,a.role_id
            FROM source_admissions a JOIN routes r ON a.route_id=r.route_id WHERE a.task_id=?""", (task["task_id"],)).fetchone()
        return row is None or (row["owner_lane"] == "v3" and bool(row["claim_open"])
            and row["installation_id"] == task["installation_id"] and row["role_id"] == task["role_id"])

    def _guard_route_claim(self, db, task):
        if not self._route_claim_allowed(db, task):
            raise PlatformError("route_claim_closed", "The route has not authorized this task's new execution claim.")

    @staticmethod
    def _legacy_view(row):
        return {key: row[key] for key in row.keys() if key != "lease_hash"}

    def claim_legacy(self, admission_id, owner_id):
        owner_id = _identifier(owner_id)
        rejection = None
        result = None
        with self._db(write=True) as db:
            admission = self._admission(db, admission_id)
            route = self._route(db, admission["route_id"])
            if admission["owner_lane"] != "legacy":
                raise PlatformError("route_lane_conflict", "This source belongs exclusively to the V3 lane.")
            if not route["claim_open"]:
                raise PlatformError("route_claim_closed", "New execution claims are frozen.")
            installation = self._installation(db, admission["installation_id"], active=True)
            self._guard_command_generation(installation, admission["generation"])
            previous = db.execute("SELECT * FROM legacy_claims WHERE admission_id=?", (admission_id,)).fetchone()
            now = self._clock_now()
            if previous is not None and previous["state"] == "running" and previous["expires_at"] <= now:
                db.execute("UPDATE legacy_claims SET state='held',updated_at=? WHERE admission_id=?", (_now(), admission_id))
                self._routing_event(db, route["route_id"], admission_id, "legacy_execution_held", {"reason": "lease_expired"})
                rejection = PlatformError("legacy_needs_reconcile", "Expired execution effects require explicit reconciliation; no automatic retry.")
            elif previous is not None and previous["state"] != "retryable":
                raise PlatformError("legacy_claim_unavailable", "This legacy execution is active, completed or requires reconciliation.")
            else:
                epoch = previous["claim_epoch"] + 1 if previous else 1
                token, timestamp = secrets.token_urlsafe(32), _now()
                expires = now + json.loads(admission["policy"])["lease_seconds"]
                db.execute("""INSERT INTO legacy_claims VALUES (?,?,?,?,?,'running',NULL,?,?)
                    ON CONFLICT(admission_id) DO UPDATE SET claim_epoch=excluded.claim_epoch,
                    owner_id=excluded.owner_id,lease_hash=excluded.lease_hash,expires_at=excluded.expires_at,
                    state='running',result_digest=NULL,updated_at=excluded.updated_at""",
                    (admission_id, epoch, owner_id, _digest(token), expires, timestamp, timestamp))
                row = db.execute("SELECT * FROM legacy_claims WHERE admission_id=?", (admission_id,)).fetchone()
                self._routing_event(db, route["route_id"], admission_id, "legacy_claimed", {"claim_epoch": epoch})
                result = {**self._legacy_view(row), "claim_token": token, "text": admission["text"]}
        if rejection:
            raise rejection
        return result

    @staticmethod
    def _legacy_owner(db, admission_id, owner_id, claim_epoch, claim_token):
        row = db.execute("SELECT * FROM legacy_claims WHERE admission_id=?", (admission_id,)).fetchone()
        if (row is None or row["owner_id"] != owner_id or type(claim_epoch) is not int
                or row["claim_epoch"] != claim_epoch or type(claim_token) is not str
                or not secrets.compare_digest(row["lease_hash"], _digest(claim_token))):
            raise PlatformError("legacy_claim_owner", "The exact legacy execution lease is required.")
        return row

    def renew_legacy(self, admission_id, owner_id, claim_epoch, claim_token):
        with self._db(write=True) as db:
            admission = self._admission(db, admission_id)
            row = self._legacy_owner(db, admission_id, owner_id, claim_epoch, claim_token)
            installation = self._installation(db, admission["installation_id"], active=True)
            self._guard_command_generation(installation, admission["generation"])
            if row["state"] != "running" or row["expires_at"] <= self._clock_now():
                raise PlatformError("legacy_claim_expired", "Only a live legacy execution may renew its lease.")
            expires = self._clock_now() + json.loads(admission["policy"])["lease_seconds"]
            db.execute("UPDATE legacy_claims SET expires_at=?,updated_at=? WHERE admission_id=?", (expires, _now(), admission_id))
            return self._legacy_view(db.execute("SELECT * FROM legacy_claims WHERE admission_id=?", (admission_id,)).fetchone())

    def complete_legacy(self, admission_id, owner_id, claim_epoch, claim_token, result_text):
        result_digest = _digest(_text(result_text, 512000, empty=True))
        with self._db(write=True) as db:
            admission = self._admission(db, admission_id)
            row = self._legacy_owner(db, admission_id, owner_id, claim_epoch, claim_token)
            if row["state"] == "completed":
                if row["result_digest"] != result_digest:
                    raise PlatformError("legacy_completion_conflict", "The completed execution has different result content.")
                return self._legacy_view(row)
            installation = self._installation(db, admission["installation_id"], active=True)
            self._guard_command_generation(installation, admission["generation"])
            if row["state"] != "running" or row["expires_at"] <= self._clock_now():
                raise PlatformError("legacy_claim_expired", "Expired or fenced executions cannot publish completion.")
            db.execute("UPDATE legacy_claims SET state='completed',result_digest=?,updated_at=? WHERE admission_id=?", (result_digest, _now(), admission_id))
            self._routing_event(db, admission["route_id"], admission_id, "legacy_completed", {"claim_epoch": claim_epoch, "result_sha256": result_digest})
            return self._legacy_view(db.execute("SELECT * FROM legacy_claims WHERE admission_id=?", (admission_id,)).fetchone())

    def reconcile_legacy(self, admission_id, reason, *, safe_to_retry=False):
        self._reason(reason)
        if safe_to_retry is not True:
            raise PlatformError("retry_not_confirmed", "Legacy execution retry requires an explicit safe-to-retry decision.")
        with self._db(write=True) as db:
            admission = self._admission(db, admission_id)
            row = db.execute("SELECT * FROM legacy_claims WHERE admission_id=?", (admission_id,)).fetchone()
            if row is None or not (row["state"] == "held" or (row["state"] == "running" and row["expires_at"] <= self._clock_now())):
                raise PlatformError("legacy_not_uncertain", "Only uncertain executions may be explicitly retried.")
            db.execute("UPDATE legacy_claims SET state='retryable',updated_at=? WHERE admission_id=?", (_now(), admission_id))
            self._routing_event(db, admission["route_id"], admission_id, "legacy_retry_authorized", {"claim_epoch": row["claim_epoch"], "reason": reason})
            return self._legacy_view(db.execute("SELECT * FROM legacy_claims WHERE admission_id=?", (admission_id,)).fetchone())
