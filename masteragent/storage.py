"""Versioned SQLite storage; upgrades require an explicit migration command."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
import hashlib
from pathlib import Path
import sqlite3
import uuid

from .errors import PlatformError
from .paths import prepare_state_root, _explicit_path

APPLICATION_ID = 0x4D413330
SCHEMA_VERSION = 4

V1_COLUMNS = {
    "installations": ("installation_id", "package_id", "version", "digest", "snapshot", "snapshot_digest", "workspace", "state", "generation", "installed_at"),
    "registrations": ("installation_id", "role_id", "generation", "registered_at"),
    "actor_tokens": ("token_hash", "installation_id", "role_id", "generation"),
    "tasks": ("task_id", "installation_id", "role_id", "generation", "text", "client_request_id", "state", "created_at", "completed_at"),
    "results": ("task_id", "installation_id", "role_id", "generation", "tool_id", "output", "status", "error", "created_at"),
    "notifications": ("notification_id", "task_id", "installation_id", "role_id", "generation", "kind", "created_at"),
}
V2_COLUMNS = {
    **V1_COLUMNS,
    "tasks": (*V1_COLUMNS["tasks"], "dispatch_generation", "parent_task_id"),
    "results": (*V1_COLUMNS["results"], "attempt_id"),
    "attempts": ("attempt_id", "task_id", "installation_id", "role_id", "generation", "ordinal", "tool_id", "owner_hash", "lease_hash", "expires_at", "state", "created_at", "finished_at"),
    "attempt_events": ("event_id", "attempt_id", "installation_id", "kind", "detail", "created_at"),
    "continuations": ("continuation_id", "task_id", "installation_id", "from_generation", "to_generation", "reason", "created_at"),
    "decisions": ("decision_id", "installation_id", "task_id", "generation", "request_id", "title", "state", "revision", "expires_at", "answer_id", "answer", "created_at", "answered_at"),
    "outbox": ("event_id", "installation_id", "generation", "task_id", "recipient_role", "kind", "payload", "dedupe_key", "state", "created_at", "delivered_at"),
    "requests": ("request_id", "installation_id", "generation", "parent_task_id", "child_task_id", "from_role", "to_role", "client_request_id", "text", "state", "created_at", "replied_at"),
    "migration_log": ("version", "previous_version", "applied_at", "backup_path"),
}
V3_COLUMNS = {**V2_COLUMNS,
    "role_deployments": ("installation_id", "role_id", "workspace", "access", "created_at"),
    "attempt_inputs": ("input_id", "installation_id", "role_id", "generation", "attempt_id",
                       "sequence", "request_id", "text", "state", "created_at", "updated_at"),
}
COLUMNS = {**V3_COLUMNS,
    "member_sessions": ("member_id", "installation_id", "role_id", "generation", "owner_hash",
                        "lease_hash", "deployment_digest", "tool_id", "state", "expires_at",
                        "started_at", "updated_at", "stop_reason"),
    "attempt_members": ("attempt_id", "member_id"),
}

SCHEMA = {
    "installations": """CREATE TABLE installations (
        installation_id TEXT PRIMARY KEY, package_id TEXT NOT NULL,
        version TEXT NOT NULL, digest TEXT NOT NULL, snapshot TEXT NOT NULL,
        snapshot_digest TEXT NOT NULL, workspace TEXT NOT NULL,
        state TEXT NOT NULL CHECK(state IN ('inactive','active','uninstalled')),
        generation INTEGER NOT NULL DEFAULT 0, installed_at TEXT NOT NULL)""",
    "registrations": """CREATE TABLE registrations (
        installation_id TEXT NOT NULL REFERENCES installations(installation_id),
        role_id TEXT NOT NULL, generation INTEGER NOT NULL, registered_at TEXT NOT NULL,
        PRIMARY KEY(installation_id,role_id,generation))""",
    "actor_tokens": """CREATE TABLE actor_tokens (
        token_hash TEXT PRIMARY KEY, installation_id TEXT NOT NULL,
        role_id TEXT NOT NULL, generation INTEGER NOT NULL,
        FOREIGN KEY(installation_id,role_id,generation)
        REFERENCES registrations(installation_id,role_id,generation))""",
    "tasks": """CREATE TABLE tasks (
        task_id TEXT PRIMARY KEY, installation_id TEXT NOT NULL REFERENCES installations(installation_id),
        role_id TEXT NOT NULL, generation INTEGER NOT NULL, text TEXT NOT NULL,
        client_request_id TEXT, state TEXT NOT NULL CHECK(state IN
        ('queued','running','completed','failed','awaiting_decision','waiting','needs_reconcile','cancelled')),
        created_at TEXT NOT NULL, completed_at TEXT, dispatch_generation INTEGER NOT NULL,
        parent_task_id TEXT REFERENCES tasks(task_id), UNIQUE(installation_id,client_request_id))""",
    "results": """CREATE TABLE results (
        task_id TEXT PRIMARY KEY REFERENCES tasks(task_id), installation_id TEXT NOT NULL,
        role_id TEXT NOT NULL, generation INTEGER NOT NULL, tool_id TEXT NOT NULL,
        output TEXT, status TEXT NOT NULL CHECK(status IN ('ok','error')), error TEXT,
        created_at TEXT NOT NULL, attempt_id TEXT REFERENCES attempts(attempt_id))""",
    "notifications": """CREATE TABLE notifications (
        notification_id TEXT PRIMARY KEY, task_id TEXT NOT NULL UNIQUE REFERENCES tasks(task_id),
        installation_id TEXT NOT NULL, role_id TEXT NOT NULL, generation INTEGER NOT NULL,
        kind TEXT NOT NULL, created_at TEXT NOT NULL)""",
    "attempts": """CREATE TABLE attempts (
        attempt_id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(task_id),
        installation_id TEXT NOT NULL REFERENCES installations(installation_id), role_id TEXT NOT NULL,
        generation INTEGER NOT NULL, ordinal INTEGER NOT NULL, tool_id TEXT NOT NULL,
        owner_hash TEXT NOT NULL, lease_hash TEXT NOT NULL, expires_at REAL NOT NULL,
        state TEXT NOT NULL CHECK(state IN ('running','completed','failed','expired','cancelled')),
        created_at TEXT NOT NULL, finished_at TEXT, UNIQUE(task_id,ordinal))""",
    "attempt_events": """CREATE TABLE attempt_events (
        event_id TEXT PRIMARY KEY, attempt_id TEXT REFERENCES attempts(attempt_id),
        installation_id TEXT NOT NULL REFERENCES installations(installation_id),
        kind TEXT NOT NULL, detail TEXT NOT NULL, created_at TEXT NOT NULL)""",
    "continuations": """CREATE TABLE continuations (
        continuation_id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(task_id),
        installation_id TEXT NOT NULL, from_generation INTEGER NOT NULL,
        to_generation INTEGER NOT NULL, reason TEXT NOT NULL, created_at TEXT NOT NULL,
        UNIQUE(task_id,to_generation))""",
    "decisions": """CREATE TABLE decisions (
        decision_id TEXT PRIMARY KEY, installation_id TEXT NOT NULL,
        task_id TEXT NOT NULL REFERENCES tasks(task_id), generation INTEGER NOT NULL,
        request_id TEXT NOT NULL, title TEXT NOT NULL,
        state TEXT NOT NULL CHECK(state IN ('pending','approved','rejected','expired','superseded')),
        revision INTEGER NOT NULL, expires_at REAL, answer_id TEXT, answer TEXT,
        created_at TEXT NOT NULL, answered_at TEXT,
        UNIQUE(installation_id,request_id,generation))""",
    "outbox": """CREATE TABLE outbox (
        event_id TEXT PRIMARY KEY, installation_id TEXT NOT NULL REFERENCES installations(installation_id),
        generation INTEGER NOT NULL, task_id TEXT REFERENCES tasks(task_id), recipient_role TEXT NOT NULL,
        kind TEXT NOT NULL, payload TEXT NOT NULL, dedupe_key TEXT NOT NULL,
        state TEXT NOT NULL CHECK(state IN ('pending','delivered','held')),
        created_at TEXT NOT NULL, delivered_at TEXT,
        UNIQUE(installation_id,generation,dedupe_key))""",
    "requests": """CREATE TABLE requests (
        request_id TEXT PRIMARY KEY, installation_id TEXT NOT NULL, generation INTEGER NOT NULL,
        parent_task_id TEXT NOT NULL REFERENCES tasks(task_id),
        child_task_id TEXT NOT NULL UNIQUE REFERENCES tasks(task_id),
        from_role TEXT NOT NULL, to_role TEXT NOT NULL, client_request_id TEXT NOT NULL,
        text TEXT NOT NULL, state TEXT NOT NULL CHECK(state IN ('waiting','replied')),
        created_at TEXT NOT NULL, replied_at TEXT,
        UNIQUE(installation_id,parent_task_id,client_request_id))""",
    "migration_log": """CREATE TABLE migration_log (
        version INTEGER PRIMARY KEY, previous_version INTEGER NOT NULL,
        applied_at TEXT NOT NULL, backup_path TEXT)""",
}
SCHEMA.update({
    "member_sessions": """CREATE TABLE member_sessions (
        member_id TEXT PRIMARY KEY, installation_id TEXT NOT NULL REFERENCES installations(installation_id),
        role_id TEXT NOT NULL, generation INTEGER NOT NULL, owner_hash TEXT NOT NULL,
        lease_hash TEXT NOT NULL, deployment_digest TEXT NOT NULL, tool_id TEXT NOT NULL,
        state TEXT NOT NULL CHECK(state IN ('ready','waiting','busy','stopping','stopped','lost')),
        expires_at REAL NOT NULL, started_at TEXT NOT NULL, updated_at TEXT NOT NULL, stop_reason TEXT)""",
    "attempt_members": """CREATE TABLE attempt_members (
        attempt_id TEXT PRIMARY KEY REFERENCES attempts(attempt_id),
        member_id TEXT NOT NULL REFERENCES member_sessions(member_id))""",
    "role_deployments": """CREATE TABLE role_deployments (
        installation_id TEXT NOT NULL REFERENCES installations(installation_id),
        role_id TEXT NOT NULL, workspace TEXT NOT NULL,
        access TEXT NOT NULL CHECK(access IN ('none','read-only','read-write')),
        created_at TEXT NOT NULL, PRIMARY KEY(installation_id,role_id))""",
    "attempt_inputs": """CREATE TABLE attempt_inputs (
        input_id TEXT PRIMARY KEY, installation_id TEXT NOT NULL REFERENCES installations(installation_id),
        role_id TEXT NOT NULL, generation INTEGER NOT NULL,
        attempt_id TEXT NOT NULL REFERENCES attempts(attempt_id),
        sequence INTEGER NOT NULL CHECK(sequence>0), request_id TEXT NOT NULL, text TEXT NOT NULL,
        state TEXT NOT NULL CHECK(state IN ('pending','sending','accepted','held')),
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
        UNIQUE(attempt_id,sequence), UNIQUE(installation_id,generation,request_id))""",
})
INDEXES = {
    "one_active": "CREATE UNIQUE INDEX one_active ON installations(state) WHERE state='active'",
    "one_running_attempt": "CREATE UNIQUE INDEX one_running_attempt ON attempts(task_id) WHERE state='running'",
    "unique_decision_answer": "CREATE UNIQUE INDEX unique_decision_answer ON decisions(installation_id,answer_id) WHERE answer_id IS NOT NULL",
    "one_live_member": "CREATE UNIQUE INDEX one_live_member ON member_sessions(installation_id,role_id) WHERE state IN ('ready','waiting','busy','stopping')",
}


def check_journal_mode(path: Path) -> None:
    if path.exists():
        with path.open("rb") as source:
            header = source.read(20)
        if header.startswith(b"SQLite format 3\0") and 2 in header[18:20]:
            raise PlatformError("unsupported_journal_mode", "This deployment requires DELETE journal mode; WAL is not opened.")


def verify_schema(db: sqlite3.Connection, version: int = SCHEMA_VERSION) -> None:
    if db.execute("PRAGMA application_id").fetchone()[0] != APPLICATION_ID:
        raise PlatformError("unsupported_schema", "This is not a MasterAgent database.")
    actual_version = db.execute("PRAGMA user_version").fetchone()[0]
    if actual_version in (1, 2, 3) and version == SCHEMA_VERSION:
        raise PlatformError("migration_required", "Older state requires the explicit migrate command; it will preserve a backup.")
    if actual_version != version:
        raise PlatformError("unsupported_schema", "The platform database version is not supported.")
    columns = {1: V1_COLUMNS, 2: V2_COLUMNS, 3: V3_COLUMNS, 4: COLUMNS}[version]
    tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
    if tables != set(columns):
        raise PlatformError("schema_corrupt", "The platform database tables are incomplete or unexpected.")
    for table, expected in columns.items():
        actual = tuple(row[1] for row in db.execute(f"PRAGMA table_info({table})"))
        if actual != expected:
            raise PlatformError("schema_corrupt", "The platform database columns are incompatible.")
    for name, sql in INDEXES.items():
        if version < 4 and name == "one_live_member":
            continue
        if version == 1 and name != "one_active":
            continue
        row = db.execute("SELECT sql FROM sqlite_master WHERE type='index' AND name=?", (name,)).fetchone()
        normalize = lambda value: " ".join(value.lower().replace("if not exists ", "").split())
        if row is None or normalize(row[0]) != normalize(sql):
            raise PlatformError("schema_corrupt", "A required uniqueness constraint is missing or changed.")


class StorageMixin:
    @contextmanager
    def _db(self, write: bool = False):
        if write and self.readonly:
            raise PlatformError("read_only", "This platform handle is read-only.")
        prepare_state_root(self.state_root, create=False)
        if not self.db_path.exists():
            raise PlatformError("not_initialized", "This state root is not initialized.")
        check_journal_mode(self.db_path)
        uri = self.db_path.as_uri() + ("?mode=rw" if write else "?mode=ro")
        db = sqlite3.connect(uri, uri=True, timeout=10, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA foreign_keys = ON")
            db.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            verify_schema(db)
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def _initialize(self):
        check_journal_mode(self.db_path)
        db = sqlite3.connect(str(self.db_path), timeout=10, isolation_level=None)
        try:
            db.execute("PRAGMA foreign_keys = ON")
            db.execute("BEGIN IMMEDIATE")
            app = db.execute("PRAGMA application_id").fetchone()[0]
            version = db.execute("PRAGMA user_version").fetchone()[0]
            tables = db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchone()
            if app == APPLICATION_ID:
                verify_schema(db)
            elif app != 0 or version != 0 or tables:
                raise PlatformError("unsupported_schema", "Refusing to adopt an unrelated database.")
            else:
                for statement in (*SCHEMA.values(), *INDEXES.values()):
                    db.execute(statement)
                db.execute(f"PRAGMA application_id={APPLICATION_ID}")
                db.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()


def migrate_state(state_root: str | Path) -> dict:
    """Upgrade an explicitly selected v1/v2/v3 database, preserving a backup.

Readers do not migrate. A write reservation blocks other writers while a second
read connection takes a consistent backup; schema/data changes commit together.
"""
    root = prepare_state_root(state_root, create=False)
    path = root / "state.sqlite3"
    if not path.exists():
        raise PlatformError("not_initialized", "There is no database to migrate.")
    check_journal_mode(path)
    db = sqlite3.connect(path.as_uri() + "?mode=rw", uri=True, timeout=10, isolation_level=None)
    db.row_factory = sqlite3.Row
    backup_path = None
    try:
        # Necessary only for rebuilding the tasks CHECK constraint. This is local
        # to this connection; all references are checked before commit.
        db.execute("PRAGMA foreign_keys=OFF")
        db.execute("BEGIN IMMEDIATE")
        version = db.execute("PRAGMA user_version").fetchone()[0]
        if version == SCHEMA_VERSION:
            verify_schema(db)
            db.commit()
            return {"migrated": False, "schema_version": SCHEMA_VERSION, "backup_path": None}
        if version not in (1, 2, 3):
            raise PlatformError("unsupported_schema", "Only schema 1, 2 or 3 can be explicitly upgraded.")
        verify_schema(db, version=version)
        if version in (2, 3) and (db.execute("SELECT 1 FROM attempts WHERE state='running'").fetchone()
                             or db.execute("SELECT 1 FROM tasks WHERE state='running'").fetchone()):
            raise PlatformError("migration_busy", "Resolve active executions with the previous version before migration.")
        backups = _explicit_path(root / "backups")
        backups.mkdir(exist_ok=True)
        backup_path = backups / ("schema" + str(version) + "-" + uuid.uuid4().hex + ".sqlite3")
        source = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
        backup = sqlite3.connect(str(backup_path))
        try:
            source.backup(backup)
        finally:
            backup.close()
            source.close()
        timestamp = datetime.now(timezone.utc).isoformat()
        if version == 1:
            db.execute(SCHEMA["tasks"].replace("CREATE TABLE tasks (", "CREATE TABLE tasks_v2 (", 1))
            db.execute("""INSERT INTO tasks_v2 SELECT task_id,installation_id,role_id,generation,text,
                client_request_id,CASE WHEN state='running' THEN 'needs_reconcile' ELSE state END,
                created_at,completed_at,generation,NULL FROM tasks""")
            db.execute("DROP TABLE tasks")
            db.execute("ALTER TABLE tasks_v2 RENAME TO tasks")
            db.execute("ALTER TABLE results ADD COLUMN attempt_id TEXT REFERENCES attempts(attempt_id)")
            for table, statement in SCHEMA.items():
                if table in V2_COLUMNS and table not in V1_COLUMNS:
                    db.execute(statement)
            for name, statement in INDEXES.items():
                if name not in ("one_active", "one_live_member"):
                    db.execute(statement)
            for task in db.execute("SELECT task_id,installation_id FROM tasks WHERE state='needs_reconcile'").fetchall():
                db.execute("INSERT INTO attempt_events VALUES (?,?,?,?,?,?)", (
                    str(uuid.uuid4()), None, task["installation_id"], "migration_uncertain",
                    json.dumps({"task_id": task["task_id"], "source": "schema1_unknown_owner"}, sort_keys=True), timestamp))
            # Old delivery status is unknown. Preserve old notices as held, never
            # relabel them delivered or silently replay them into current sessions.
            for row in db.execute("SELECT * FROM notifications").fetchall():
                payload = json.dumps({"task_id": row["task_id"], "legacy_notification_id": row["notification_id"], "delivery": "unknown"}, sort_keys=True)
                db.execute("INSERT INTO outbox VALUES (?,?,?,?,?,?,?,?,?,?,?)", (
                    str(uuid.uuid4()), row["installation_id"], row["generation"], row["task_id"], row["role_id"],
                    row["kind"], payload, "legacy:" + row["notification_id"], "held", row["created_at"], None))
        if version < 3:
            for table in ("role_deployments", "attempt_inputs"):
                db.execute(SCHEMA[table])
        for installation in db.execute("SELECT * FROM installations").fetchall():
            raw = installation["snapshot"]
            try:
                if hashlib.sha256(raw.encode("utf-8")).hexdigest() != installation["snapshot_digest"]:
                    raise ValueError("digest")
                roles = json.loads(raw)["roles"]
                names = [role["role_id"] for role in roles]
                if not names or any(not isinstance(name, str) or not name for name in names) or len(set(names)) != len(names):
                    raise ValueError("roles")
            except (TypeError, ValueError, KeyError):
                raise PlatformError("invalid_snapshot", "Deployment migration requires an intact business snapshot.") from None
            if version < 3:
                for role_id in names:
                    db.execute("INSERT INTO role_deployments VALUES (?,?,?,?,?)", (
                        installation["installation_id"], role_id, installation["workspace"], "none", timestamp))
        for table in ("member_sessions", "attempt_members"):
            db.execute(SCHEMA[table])
        db.execute(INDEXES["one_live_member"])
        db.execute("DELETE FROM actor_tokens")
        db.execute("INSERT INTO migration_log VALUES (?,?,?,?)", (SCHEMA_VERSION, version, timestamp, str(backup_path)))
        db.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        if db.execute("PRAGMA foreign_key_check").fetchone():
            raise PlatformError("migration_integrity", "Migration would leave invalid references; changes were rolled back.")
        verify_schema(db)
        db.commit()
        return {"migrated": True, "schema_version": SCHEMA_VERSION, "backup_path": str(backup_path), "tokens_revoked": True, "legacy_notifications": "held" if version == 1 else "preserved"}
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()
