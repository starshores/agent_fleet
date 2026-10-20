"""An explicit, durable local text source, separate from routing and execution.

Confirmation records an admission identity, not execution ownership or completion.
The second, transport-only completion step keeps interrupted confirmation
handshakes discoverable after a restart.
"""
from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
import stat
import uuid

from .errors import PlatformError
from .paths import _check_directory_boundary, _explicit_path
from .routing import _digest, _identifier, _json, _source_key, _text


SOURCE_PROTOCOL = "ma-source-v1"
TEXT_LIMIT = 64000
ENVELOPE_LIMIT = 1048576
_APPLICATION_ID = 0x4D415331
_ENVELOPE_FIELDS = frozenset((
    "purpose", "protocol_version", "source_namespace", "source_instance",
    "source_id", "kind", "text", "text_bytes", "text_sha256", "envelope_sha256",
))
_SCHEMA = {
    "source": """CREATE TABLE source (
        singleton INTEGER PRIMARY KEY CHECK(singleton=1), namespace TEXT NOT NULL,
        source_instance TEXT NOT NULL, text_limit INTEGER NOT NULL,
        envelope_limit INTEGER NOT NULL, revision INTEGER NOT NULL CHECK(revision>=0))""",
    "messages": """CREATE TABLE messages (
        source_key TEXT PRIMARY KEY, envelope TEXT NOT NULL,
        sequence INTEGER NOT NULL UNIQUE CHECK(sequence>0),
        admission_id TEXT, source_confirmed INTEGER NOT NULL CHECK(source_confirmed IN (0,1)),
        transport_completed INTEGER NOT NULL CHECK(transport_completed IN (0,1)),
        CHECK((source_confirmed=0 AND admission_id IS NULL AND transport_completed=0)
            OR (source_confirmed=1 AND admission_id IS NOT NULL)))""",
}


def _uuid(value):
    try:
        if type(value) is not str or str(uuid.UUID(value)) != value:
            raise ValueError("instance")
    except (ValueError, TypeError, AttributeError):
        raise PlatformError("source_integrity", "A canonical source instance UUID is required.") from None
    return value


def _sha256(value):
    if (type(value) is not str or len(value) != 64
            or any(char not in "0123456789abcdef" for char in value)):
        raise PlatformError("source_integrity", "A lowercase SHA-256 digest is required.")
    return value


def validate_envelope(envelope):
    """Validate a complete v1 source envelope; return its canonical serialization."""
    if type(envelope) is not dict or set(envelope) != _ENVELOPE_FIELDS:
        raise PlatformError("invalid_source_envelope", "A complete versioned source envelope is required.")
    if (envelope["purpose"] != "masteragent.source-task"
            or envelope["protocol_version"] != SOURCE_PROTOCOL):
        raise PlatformError("source_protocol", "Unsupported source envelope protocol.")
    _identifier(envelope["source_namespace"])
    _uuid(envelope["source_instance"])
    _source_key(envelope["source_id"])
    if type(envelope["kind"]) is not str or envelope["kind"] != "task":
        raise PlatformError("unsupported_source_kind", "This source publishes explicit text tasks only.")
    text = _text(envelope["text"], TEXT_LIMIT)
    if (type(envelope["text_bytes"]) is not int
            or envelope["text_bytes"] != len(text.encode("utf-8"))
            or _sha256(envelope["text_sha256"]) != _digest(text)):
        raise PlatformError("source_integrity", "Source text length or digest differs from its full content.")
    payload = {key: value for key, value in envelope.items() if key != "envelope_sha256"}
    if _sha256(envelope["envelope_sha256"]) != _digest(_json(payload)):
        raise PlatformError("source_integrity", "Source envelope digest differs from its full content.")
    encoded = _json(envelope)
    if len(encoded.encode("utf-8")) > ENVELOPE_LIMIT:
        raise PlatformError("source_envelope_limit", "The complete source envelope exceeds the byte limit.")
    return encoded


class SourceStore:
    """One explicitly initialized local source with an immutable namespace and UUID."""

    def __init__(self, root: str | Path, source_namespace: str, *, initialize: bool = False):
        if type(initialize) is not bool:
            raise PlatformError("invalid_initialization", "Source initialization must be explicit.")
        self.root = _explicit_path(root)
        _check_directory_boundary(self.root)
        self.source_namespace = _identifier(source_namespace)
        self.db_path = self.root / "sources.sqlite3"
        self._known_instance = None
        if initialize:
            self._initialize()

    def _paths(self, create=False):
        root = _explicit_path(self.root)
        _check_directory_boundary(root)
        if root.exists() and not self.db_path.exists() and any(root.iterdir()):
            raise PlatformError("unmanaged_source", "A new source directory must be empty.")
        for name in ("sources.sqlite3", "sources.sqlite3-journal", "sources.sqlite3-wal", "sources.sqlite3-shm"):
            candidate = root / name
            try:
                info = candidate.lstat()
            except FileNotFoundError:
                continue
            if (stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400
                    or info.st_nlink > 1 or not stat.S_ISREG(info.st_mode)):
                raise PlatformError("unsafe_source", "Source database files cannot be linked or nonregular files.")
        # Opening even a read-only SQLite connection to a WAL database can
        # create -wal/-shm files. Reject unsupported journals before connecting.
        if self.db_path.is_file():
            with self.db_path.open("rb") as source:
                header = source.read(20)
            if header.startswith(b"SQLite format 3\0") and 2 in header[18:20]:
                raise PlatformError("source_schema", "The source requires its supported rollback journal mode.")
        if create:
            root.mkdir(parents=True, exist_ok=True)

    def _verify(self, db):
        if (db.execute("PRAGMA application_id").fetchone()[0] != _APPLICATION_ID
                or db.execute("PRAGMA user_version").fetchone()[0] != 1):
            raise PlatformError("source_schema", "Unsupported source database.")
        names = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if names != set(_SCHEMA):
            raise PlatformError("source_schema", "Source tables do not match the protocol.")
        if db.execute("""SELECT 1 FROM sqlite_master WHERE type IN ('trigger','view')
                OR (type='index' AND sql IS NOT NULL) LIMIT 1""").fetchone():
            raise PlatformError("source_schema", "Source database contains unsupported schema objects.")
        normalize = lambda value: " ".join(value.lower().split())
        for name, sql in _SCHEMA.items():
            actual = db.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone()[0]
            if normalize(actual) != normalize(sql):
                raise PlatformError("source_schema", "Source database structure has changed.")
        rows = db.execute("SELECT * FROM source").fetchall()
        if (len(rows) != 1 or rows[0]["singleton"] != 1
                or tuple(rows[0][key] for key in ("namespace", "text_limit", "envelope_limit")) != (
                    self.source_namespace, TEXT_LIMIT, ENVELOPE_LIMIT)):
            raise PlatformError("source_scope", "The source belongs to a different fixed namespace or limits.")
        row = rows[0]
        _uuid(row["source_instance"])
        if type(row["revision"]) is not int or row["revision"] < 0:
            raise PlatformError("source_integrity", "Source revision is invalid.")
        if self._known_instance is not None and self._known_instance != row["source_instance"]:
            raise PlatformError("source_instance_changed", "The selected source database instance has changed.")
        if db.execute("PRAGMA journal_mode").fetchone()[0].lower() != "delete":
            raise PlatformError("source_schema", "The source requires its supported rollback journal mode.")
        self._known_instance = row["source_instance"]

    def _initialize(self):
        self._paths(create=True)
        db = sqlite3.connect(str(self.db_path), timeout=10, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            db.execute("BEGIN IMMEDIATE")
            app = db.execute("PRAGMA application_id").fetchone()[0]
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if app == _APPLICATION_ID:
                self._verify(db)
            elif app or version or db.execute("SELECT 1 FROM sqlite_master").fetchone():
                raise PlatformError("source_schema", "Refusing to adopt an unrelated database.")
            else:
                for statement in _SCHEMA.values():
                    db.execute(statement)
                db.execute("INSERT INTO source VALUES (1,?,?,?,?,0)", (
                    self.source_namespace, str(uuid.uuid4()), TEXT_LIMIT, ENVELOPE_LIMIT))
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
            raise PlatformError("source_unavailable", "The source has not been initialized.")
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

    @staticmethod
    def _instance(db):
        return db.execute("SELECT source_instance FROM source WHERE singleton=1").fetchone()[0]

    def descriptor(self):
        """Read the fixed source identity; never initialize storage implicitly."""
        with self._db() as db:
            return {"root": str(self.root), "source_namespace": self.source_namespace,
                    "source_instance": self._instance(db), "protocol_version": SOURCE_PROTOCOL,
                    "max_text_bytes": TEXT_LIMIT, "max_envelope_bytes": ENVELOPE_LIMIT}

    def _record(self, row, instance):
        try:
            envelope = json.loads(row["envelope"])
            encoded = validate_envelope(envelope)
            if (encoded != row["envelope"] or _source_key(envelope["source_id"]) != row["source_key"]
                    or envelope["source_namespace"] != self.source_namespace
                    or envelope["source_instance"] != instance
                    or type(row["sequence"]) is not int or row["sequence"] < 1
                    or type(row["source_confirmed"]) is not int or row["source_confirmed"] not in (0, 1)
                    or type(row["transport_completed"]) is not int or row["transport_completed"] not in (0, 1)):
                raise ValueError("content")
            if row["source_confirmed"]:
                _identifier(row["admission_id"])
            elif row["admission_id"] is not None or row["transport_completed"]:
                raise ValueError("confirmation")
        except (ValueError, TypeError, KeyError):
            raise PlatformError("source_integrity", "Stored source content or confirmation could not be verified.") from None
        return {"envelope": envelope, "admission_id": row["admission_id"],
                "source_confirmed": bool(row["source_confirmed"]),
                "transport_completed": bool(row["transport_completed"])}

    @staticmethod
    def _message(db, source_key):
        row = db.execute("SELECT * FROM messages WHERE source_key=?", (source_key,)).fetchone()
        if row is None:
            raise PlatformError("source_not_found", "This source identifier has not been published.")
        return row

    def publish(self, source_id, text, *, kind="task"):
        """Persist complete immutable text; an exact duplicate returns the original."""
        source_key = _source_key(source_id)
        text = _text(text, TEXT_LIMIT)
        if type(kind) is not str or kind != "task":
            raise PlatformError("unsupported_source_kind", "This source publishes explicit text tasks only.")
        with self._db(write=True) as db:
            instance = self._instance(db)
            envelope = {"purpose": "masteragent.source-task", "protocol_version": SOURCE_PROTOCOL,
                        "source_namespace": self.source_namespace, "source_instance": instance,
                        "source_id": source_id, "kind": kind, "text": text,
                        "text_bytes": len(text.encode("utf-8")), "text_sha256": _digest(text)}
            envelope["envelope_sha256"] = _digest(_json(envelope))
            encoded = validate_envelope(envelope)
            previous = db.execute("SELECT * FROM messages WHERE source_key=?", (source_key,)).fetchone()
            if previous is not None:
                record = self._record(previous, instance)
                if previous["envelope"] != encoded:
                    raise PlatformError("source_conflict", "This source identifier already belongs to different immutable content.")
                return record
            revision = db.execute("SELECT revision FROM source WHERE singleton=1").fetchone()[0] + 1
            db.execute("UPDATE source SET revision=? WHERE singleton=1", (revision,))
            db.execute("INSERT INTO messages VALUES (?,?,?,NULL,0,0)", (source_key, encoded, revision))
            return self._record(self._message(db, source_key), instance)

    def pending(self, limit=100, *, after_id=None):
        """List unfinished handshakes after an optional exact typed source cursor.

        A completed message remains a valid cursor. Callers can advance past a
        failing batch without losing earlier unfinished messages on the next scan.
        """
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise PlatformError("invalid_source_limit", "Pending limit must be an integer from 1 through 1000.")
        source_key = _source_key(after_id) if after_id is not None else None
        with self._db() as db:
            instance = self._instance(db)
            after_sequence = 0
            if source_key is not None:
                cursor = self._message(db, source_key)
                self._record(cursor, instance)
                after_sequence = cursor["sequence"]
            rows = db.execute("""SELECT * FROM messages
                WHERE transport_completed=0 AND sequence>? ORDER BY sequence LIMIT ?""", (after_sequence, limit))
            return [self._record(row, instance) for row in rows]

    def get(self, source_id):
        """Read one exact typed identifier without acknowledging or claiming it."""
        source_key = _source_key(source_id)
        with self._db() as db:
            return self._record(self._message(db, source_key), self._instance(db))

    def confirm(self, source_id, admission_id, envelope_sha256):
        """Durably bind this exact envelope to one admission; keep it discoverable."""
        source_key = _source_key(source_id)
        admission_id = _identifier(admission_id)
        envelope_sha256 = _sha256(envelope_sha256)
        with self._db(write=True) as db:
            record = self._record(self._message(db, source_key), self._instance(db))
            if record["envelope"]["envelope_sha256"] != envelope_sha256:
                raise PlatformError("source_confirmation_conflict", "The confirmation refers to different source content.")
            if record["admission_id"] is not None and record["admission_id"] != admission_id:
                raise PlatformError("source_confirmation_conflict", "This source is already confirmed to a different admission.")
            if not record["source_confirmed"]:
                db.execute("UPDATE messages SET admission_id=?,source_confirmed=1 WHERE source_key=?", (admission_id, source_key))
            return self._record(self._message(db, source_key), self._instance(db))

    def complete(self, source_id, admission_id):
        """End only the transport handshake after platform confirmation is durable."""
        source_key = _source_key(source_id)
        admission_id = _identifier(admission_id)
        with self._db(write=True) as db:
            record = self._record(self._message(db, source_key), self._instance(db))
            if not record["source_confirmed"] or record["admission_id"] != admission_id:
                raise PlatformError("source_confirmation_conflict", "Transport completion requires the same confirmed admission.")
            if not record["transport_completed"]:
                db.execute("UPDATE messages SET transport_completed=1 WHERE source_key=?", (source_key,))
            return self._record(self._message(db, source_key), self._instance(db))
