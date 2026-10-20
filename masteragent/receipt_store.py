"""Explicit local durable text destination; receipt means storage, never processing.

The database is a separate storage domain from Platform. No operation imports
bus state, opens a network connection, launches a worker, or interprets text.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import sqlite3
import stat
import time
import unicodedata
import uuid

from .errors import PlatformError
from .paths import _check_directory_boundary, _explicit_path

PAYLOAD_VERSION = "text.v1"
RECEIPT_PROTOCOL = "ma-receipt-v1"
TEXT_LIMIT = 512000
ENVELOPE_LIMIT = 1048576
_APPLICATION_ID = 0x4D415231
_ENVELOPE_FIELDS = frozenset((
    "purpose", "payload_version", "delivery_id", "admission_id", "route_id", "route_epoch",
    "installation_id", "role_id", "generation", "task_id", "attempt_id", "result_id",
    "destination_namespace", "destination_id", "result_status", "text", "text_bytes", "text_sha256"))
_SCHEMA = {
    "destination": """CREATE TABLE destination (
        singleton INTEGER PRIMARY KEY CHECK(singleton=1), namespace TEXT NOT NULL,
        destination_id TEXT NOT NULL, text_limit INTEGER NOT NULL, envelope_limit INTEGER NOT NULL,
        store_instance_id TEXT NOT NULL,
        revision INTEGER NOT NULL CHECK(revision>=0))""",
    "receipts": """CREATE TABLE receipts (
        delivery_id TEXT PRIMARY KEY, receipt_id TEXT NOT NULL UNIQUE,
        text TEXT NOT NULL, text_bytes INTEGER NOT NULL, text_sha256 TEXT NOT NULL,
        envelope TEXT NOT NULL, envelope_sha256 TEXT NOT NULL,
        storage_revision INTEGER NOT NULL UNIQUE, received_at TEXT NOT NULL)""",
}


def canonical_json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def utf8(value, *, code="invalid_delivery"):
    if not isinstance(value, str):
        raise PlatformError(code, "Text must be a losslessly encodable UTF-8 string.")
    try:
        return value.encode("utf-8", errors="strict")
    except UnicodeError:
        raise PlatformError(code, "Text must be a losslessly encodable UTF-8 string.") from None


def identifier(value):
    encoded = utf8(value)
    if (not value.strip() or len(encoded) > 200
            or any(unicodedata.category(char) == "Cc" for char in value)):
        raise PlatformError("invalid_delivery", "Identifiers require 1–200 UTF-8 bytes without control characters.")
    return value


def digest(value):
    return hashlib.sha256(utf8(value)).hexdigest()


def validate_envelope(envelope, text_limit=TEXT_LIMIT, envelope_limit=ENVELOPE_LIMIT):
    if not isinstance(envelope, dict) or set(envelope) != _ENVELOPE_FIELDS:
        raise PlatformError("invalid_delivery", "A complete versioned delivery envelope is required.")
    if (envelope["purpose"] != "masteragent.result-delivery"
            or envelope["payload_version"] != PAYLOAD_VERSION
            or envelope["result_status"] not in ("ok", "error")):
        raise PlatformError("delivery_protocol", "Unsupported delivery payload protocol.")
    for key in ("delivery_id", "admission_id", "route_id", "installation_id", "role_id", "task_id",
                "attempt_id", "result_id", "destination_namespace", "destination_id"):
        identifier(envelope[key])
    if any(type(envelope[key]) is not int or envelope[key] < 1 for key in ("route_epoch", "generation")):
        raise PlatformError("invalid_delivery", "Generation and route epoch must be positive integers.")
    body = utf8(envelope["text"])
    if (type(envelope["text_bytes"]) is not int or envelope["text_bytes"] != len(body)
            or envelope["text_sha256"] != hashlib.sha256(body).hexdigest()):
        raise PlatformError("delivery_integrity", "Delivery text size or digest does not match its full content.")
    if len(body) > text_limit:
        raise PlatformError("delivery_text_limit", "Complete text exceeds the destination byte limit.")
    encoded = canonical_json(envelope)
    if len(utf8(encoded)) > envelope_limit:
        raise PlatformError("delivery_envelope_limit", "Serialized envelope exceeds the destination byte limit.")
    return encoded


class ReceiptStore:
    """One explicitly initialized, fixed local destination and receipt namespace."""

    def __init__(self, root: str | Path, destination_namespace: str, destination_id: str, *,
                 initialize: bool = False, max_text_bytes: int = TEXT_LIMIT,
                 max_envelope_bytes: int = ENVELOPE_LIMIT, clock=None):
        if type(initialize) is not bool:
            raise PlatformError("invalid_initialization", "Destination initialization must be explicit.")
        if (type(max_text_bytes) is not int or not 1 <= max_text_bytes <= TEXT_LIMIT
                or type(max_envelope_bytes) is not int or not 1 <= max_envelope_bytes <= ENVELOPE_LIMIT):
            raise PlatformError("invalid_delivery_limit", "Destination limits must fit the supported text and envelope bounds.")
        self.root = _explicit_path(root)
        _check_directory_boundary(self.root)
        self.destination_namespace = identifier(destination_namespace)
        self.destination_id = identifier(destination_id)
        self.max_text_bytes = max_text_bytes
        self.max_envelope_bytes = max_envelope_bytes
        self.clock = clock or time.time
        self.db_path = self.root / "receipts.sqlite3"
        self._known_instance = None
        if initialize:
            self._initialize()

    def _paths(self, create=False):
        root = _explicit_path(self.root)
        _check_directory_boundary(root)
        if root.exists() and not self.db_path.exists() and any(root.iterdir()):
            raise PlatformError("unmanaged_destination", "A new receipt destination directory must be empty.")
        for name in ("receipts.sqlite3", "receipts.sqlite3-journal", "receipts.sqlite3-wal", "receipts.sqlite3-shm"):
            candidate = root / name
            try:
                info = candidate.lstat()
            except FileNotFoundError:
                continue
            if (stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400
                    or info.st_nlink > 1 or not stat.S_ISREG(info.st_mode)):
                raise PlatformError("unsafe_destination", "Receipt database files cannot be linked or nonregular files.")
        # Even a read-only SQLite connection may create WAL/shm sidecars before
        # _verify can reject WAL. Inspect the existing header before connecting.
        if self.db_path.exists():
            with self.db_path.open("rb") as source:
                header = source.read(20)
            if header.startswith(b"SQLite format 3\0") and 2 in header[18:20]:
                raise PlatformError("destination_schema", "Receipt destination requires DELETE journal mode; WAL is not opened.")
        if create:
            root.mkdir(parents=True, exist_ok=True)

    def _verify(self, db):
        if (db.execute("PRAGMA application_id").fetchone()[0] != _APPLICATION_ID
                or db.execute("PRAGMA user_version").fetchone()[0] != 1):
            raise PlatformError("destination_schema", "Unsupported receipt destination database.")
        names = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if names != set(_SCHEMA):
            raise PlatformError("destination_schema", "Receipt destination tables do not match the protocol.")
        unexpected = db.execute("""SELECT 1 FROM sqlite_master WHERE type IN ('trigger','view')
            OR (type='index' AND sql IS NOT NULL) LIMIT 1""").fetchone()
        if unexpected:
            raise PlatformError("destination_schema", "Receipt destination contains unsupported schema objects.")
        normalize = lambda value: " ".join(value.lower().split())
        for name, sql in _SCHEMA.items():
            actual = db.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone()[0]
            if normalize(actual) != normalize(sql):
                raise PlatformError("destination_schema", "Receipt destination structure has changed.")
        row = db.execute("SELECT * FROM destination WHERE singleton=1").fetchone()
        if row is None or tuple(row[key] for key in ("namespace", "destination_id", "text_limit", "envelope_limit")) != (
                self.destination_namespace, self.destination_id, self.max_text_bytes, self.max_envelope_bytes):
            raise PlatformError("destination_scope", "This receipt store belongs to a different fixed destination or limits.")
        try:
            if str(uuid.UUID(row["store_instance_id"])) != row["store_instance_id"]:
                raise ValueError("instance")
        except (ValueError, TypeError, AttributeError):
            raise PlatformError("destination_schema", "Receipt destination has no valid immutable instance identity.") from None
        if self._known_instance is not None and self._known_instance != row["store_instance_id"]:
            raise PlatformError("destination_instance_changed", "The destination database instance changed after it was selected.")
        self._known_instance = row["store_instance_id"]
        if db.execute("PRAGMA journal_mode").fetchone()[0].lower() != "delete":
            raise PlatformError("destination_schema", "Receipt destination requires its supported rollback journal mode.")

    def _initialize(self):
        self._paths(create=True)
        db = sqlite3.connect(str(self.db_path), timeout=10, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            db.execute("BEGIN IMMEDIATE")
            app, version = db.execute("PRAGMA application_id").fetchone()[0], db.execute("PRAGMA user_version").fetchone()[0]
            if app == _APPLICATION_ID:
                self._verify(db)
            elif app or version or db.execute("SELECT 1 FROM sqlite_master WHERE type='table'").fetchone():
                raise PlatformError("destination_schema", "Refusing to adopt an unrelated receipt database.")
            else:
                for statement in _SCHEMA.values():
                    db.execute(statement)
                db.execute("INSERT INTO destination VALUES (1,?,?,?,?,?,0)", (
                    self.destination_namespace, self.destination_id, self.max_text_bytes, self.max_envelope_bytes,
                    str(uuid.uuid4())))
                db.execute(f"PRAGMA application_id={_APPLICATION_ID}")
                db.execute("PRAGMA user_version=1")
                self._verify(db)
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    @contextmanager
    def _db(self, write=False):
        self._paths()
        if not self.db_path.is_file():
            raise PlatformError("destination_unavailable", "Receipt destination has not been initialized.")
        db = sqlite3.connect(self.db_path.as_uri() + ("?mode=rw" if write else "?mode=ro"),
                             uri=True, timeout=10, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            db.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            self._verify(db)
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def _envelope(self, envelope):
        encoded = validate_envelope(envelope, self.max_text_bytes, self.max_envelope_bytes)
        if (envelope["destination_namespace"], envelope["destination_id"]) != (
                self.destination_namespace, self.destination_id):
            raise PlatformError("destination_scope", "Delivery is addressed to another destination.")
        return encoded

    @staticmethod
    def _instance(db):
        return db.execute("SELECT store_instance_id FROM destination WHERE singleton=1").fetchone()[0]

    def descriptor(self):
        """Read the initialized destination's immutable identity without creating it."""
        with self._db() as db:
            return {"root": str(self.root), "destination_namespace": self.destination_namespace,
                    "destination_id": self.destination_id, "destination_instance": self._instance(db),
                    "protocol_version": RECEIPT_PROTOCOL, "max_text_bytes": self.max_text_bytes,
                    "max_envelope_bytes": self.max_envelope_bytes}

    def _receipt(self, row, instance):
        try:
            stored = json.loads(row["envelope"])
            encoded = self._envelope(stored)
            if (encoded != row["envelope"] or digest(encoded) != row["envelope_sha256"]
                    or (stored["text"], stored["text_bytes"], stored["text_sha256"], stored["delivery_id"]) !=
                    (row["text"], row["text_bytes"], row["text_sha256"], row["delivery_id"])):
                raise ValueError("content")
        except (ValueError, TypeError, KeyError):
            raise PlatformError("destination_integrity", "Stored receipt content could not be verified.") from None
        return {"purpose": "masteragent.delivery-receipt", "protocol_version": RECEIPT_PROTOCOL,
                "destination_namespace": self.destination_namespace, "destination_id": self.destination_id,
                "destination_instance": instance,
                "delivery_id": row["delivery_id"], "receipt_id": row["receipt_id"],
                "text_bytes": row["text_bytes"], "text_sha256": row["text_sha256"],
                "envelope_sha256": row["envelope_sha256"], "storage_revision": row["storage_revision"],
                "received_at": row["received_at"]}

    def receive(self, envelope):
        """Atomically store the full immutable text and return its original receipt."""
        encoded = self._envelope(envelope)
        with self._db(write=True) as db:
            previous = db.execute("SELECT * FROM receipts WHERE delivery_id=?", (envelope["delivery_id"],)).fetchone()
            if previous is not None:
                if previous["envelope"] != encoded:
                    raise PlatformError("receipt_conflict", "This delivery identifier already belongs to different content.")
                return self._receipt(previous, self._instance(db))
            now = self.clock()
            if isinstance(now, bool) or not isinstance(now, (int, float)) or not math.isfinite(now):
                raise PlatformError("invalid_clock", "Receipt clock must return finite epoch seconds.")
            stamp = datetime.fromtimestamp(now, timezone.utc).isoformat()
            revision = db.execute("SELECT revision FROM destination WHERE singleton=1").fetchone()[0] + 1
            db.execute("UPDATE destination SET revision=? WHERE singleton=1", (revision,))
            db.execute("INSERT INTO receipts VALUES (?,?,?,?,?,?,?,?,?)", (
                envelope["delivery_id"], str(uuid.uuid4()), envelope["text"], envelope["text_bytes"],
                envelope["text_sha256"], encoded, digest(encoded), revision, stamp))
            return self._receipt(db.execute("SELECT * FROM receipts WHERE delivery_id=?", (envelope["delivery_id"],)).fetchone(), self._instance(db))

    def query(self, envelope):
        """Read-only authoritative lookup; missing/inaccessible storage is unavailable."""
        try:
            encoded = self._envelope(envelope)
        except PlatformError as exc:
            return {"status": "conflict", "reason": exc.code}
        try:
            with self._db() as db:
                row = db.execute("SELECT * FROM receipts WHERE delivery_id=?", (envelope["delivery_id"],)).fetchone()
                if row is None:
                    return {"status": "absent", "protocol_version": RECEIPT_PROTOCOL,
                            "destination_namespace": self.destination_namespace,
                            "destination_instance": self._instance(db),
                            "destination_id": self.destination_id, "delivery_id": envelope["delivery_id"],
                            "envelope_sha256": digest(encoded)}
                if row["envelope"] != encoded:
                    return {"status": "conflict", "reason": "receipt_conflict"}
                return {"status": "received", "receipt": self._receipt(row, self._instance(db))}
        except (OSError, sqlite3.Error, ValueError):
            return {"status": "unavailable", "reason": "destination_unavailable"}

    def read_text(self, envelope):
        """Explicit read of this exact received payload, never a claim or acknowledgement."""
        encoded = self._envelope(envelope)
        with self._db() as db:
            row = db.execute("SELECT * FROM receipts WHERE delivery_id=?", (envelope["delivery_id"],)).fetchone()
            if row is None:
                raise PlatformError("receipt_absent", "This destination has no stored delivery with that identifier.")
            if row["envelope"] != encoded:
                raise PlatformError("receipt_conflict", "Stored delivery content differs from this envelope.")
            self._receipt(row, self._instance(db))
            return row["text"]
