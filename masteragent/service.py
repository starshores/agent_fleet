"""Installation-scoped coordinator for the 3.0 foundation slice.

The synchronous convenience API runs only deterministic text adapters. External
runtime controllers claim and commit ownership before starting their processes.
This module never starts a model or imports the legacy bus, and does not establish
an OS security boundary. Worker APIs require a coordinator-issued Actor.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import secrets
import sqlite3
import time
import uuid

from .contracts import load_package
from . import __version__ as VERSION
from .errors import PlatformError
from .storage import StorageMixin, SCHEMA_VERSION, APPLICATION_ID
from .coordination import CoordinationMixin
from .interactions import RuntimeInteractionMixin
from .members import MemberMixin
from .paths import prepare_state_root
from .deployment import WORKSPACE_ACCESS, normalize_deployments


_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
_RUNTIME_REASON = re.compile(r"[a-z][a-z0-9_.-]{0,99}\Z", re.ASCII)
_RUNTIME_EVENTS = frozenset({"runtime_start_requested", "runtime_started",
                             "runtime_thread", "runtime_progress", "runtime_stopped"})


@dataclass(frozen=True)
class Actor:
    installation_id: str
    role_id: str
    generation: int
    token: str = field(repr=False)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _identifier(value: str) -> str:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise PlatformError("invalid_id", "Installation identifiers must be 1–64 letters, digits, dots, dashes or underscores.")
    return value


class Platform(StorageMixin, CoordinationMixin, RuntimeInteractionMixin, MemberMixin):
    """Explicit state root, immutable package snapshots, and scoped execution."""

    def __init__(self, state_root: Path | str, readonly: bool = False,
                 adapters: Mapping[str, Callable[[str], str]] | None = None,
                 clock: Callable[[], float] | None = None,
                 external_adapters: set[str] | None = None):
        self.readonly = readonly
        self.clock = clock or time.time
        # Deployment code may explicitly supply side-effect-free text transforms.
        # No adapter module is imported from a business package.
        self.adapters = dict(adapters) if adapters is not None else {
            "echo": lambda text: text, "uppercase": str.upper,
        }
        if any(not isinstance(key, str) or not callable(value) for key, value in self.adapters.items()):
            raise PlatformError("invalid_adapters", "Adapter registrations must map names to text transform functions.")
        if external_adapters is not None and (
                not isinstance(external_adapters, (set, frozenset))
                or any(not isinstance(name, str) or not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", name)
                       for name in external_adapters)):
            raise PlatformError("invalid_adapters", "External adapters must be a set of portable adapter names.")
        self.external_adapters = frozenset(external_adapters or ())
        if self.external_adapters & self.adapters.keys():
            raise PlatformError("invalid_adapters", "External and synchronous adapter registrations must be distinct.")
        self.state_root = prepare_state_root(state_root, create=not readonly)
        self.db_path = self.state_root / "state.sqlite3"
        if not readonly:
            self._initialize()

    def _check_adapters(self, package: dict) -> None:
        available = self.adapters.keys() | self.external_adapters
        missing = sorted({tool["adapter"] for tool in package["tools"].values()} - available)
        if missing:
            raise PlatformError("adapter_unavailable", "This deployment lacks package adapters: " + ", ".join(missing))

    @staticmethod
    def _workspace_paths(db: sqlite3.Connection, exclude: str | None = None) -> list[Path]:
        rows = db.execute("""SELECT workspace FROM installations
            WHERE (? IS NULL OR installation_id<>?)
            UNION SELECT workspace FROM role_deployments
            WHERE (? IS NULL OR installation_id<>?) ORDER BY workspace""",
            (exclude, exclude, exclude, exclude))
        return list(dict.fromkeys(Path(row[0]) for row in rows))

    def _deployments(self, db: sqlite3.Connection, installation: sqlite3.Row) -> dict:
        rows = list(db.execute("""SELECT role_id,workspace,access FROM role_deployments
            WHERE installation_id=? ORDER BY role_id""", (installation["installation_id"],)))
        expected = {role["role_id"] for role in self._snapshot(installation)["roles"]}
        if ({row["role_id"] for row in rows} != expected
                or any(not isinstance(row["workspace"], str) or not row["workspace"]
                       or row["access"] not in WORKSPACE_ACCESS for row in rows)):
            raise PlatformError("deployment_corrupt", "The installed role bindings are incomplete or invalid.")
        return {row["role_id"]: {"workspace": row["workspace"], "access": row["access"]} for row in rows}

    def _check_workspace(self, db: sqlite3.Connection, installation: sqlite3.Row) -> None:
        bindings = self._deployments(db, installation)
        normalize_deployments(set(bindings), installation["workspace"], bindings,
                              self.state_root, self._workspace_paths(db, installation["installation_id"]))

    def deployment_workspaces(self) -> list[Path]:
        """All reserved deployment roots, including inactive and retained history.

        Runtime homes must avoid every root, not only the selected role's path.
        This administrative read neither creates nor enumerates business files.
        """
        if not self.db_path.exists():
            return []
        with self._db() as db:
            for installation in db.execute("SELECT * FROM installations"):
                self._deployments(db, installation)
            return self._workspace_paths(db)

    @staticmethod
    def _installation(db: sqlite3.Connection, installation_id: str, active: bool = False) -> sqlite3.Row:
        row = db.execute("SELECT * FROM installations WHERE installation_id=?", (_identifier(installation_id),)).fetchone()
        if row is None:
            raise PlatformError("installation_not_found", "No installation exists with this identifier.")
        if active and row["state"] != "active":
            raise PlatformError("installation_inactive", "The installation is not active.")
        return row

    @staticmethod
    def _snapshot(installation: sqlite3.Row) -> dict:
        if _hash(installation["snapshot"]) != installation["snapshot_digest"]:
            raise PlatformError("snapshot_corrupt", "The installed package snapshot failed its integrity check.")
        try:
            return json.loads(installation["snapshot"])
        except (ValueError, TypeError) as exc:
            raise PlatformError("snapshot_corrupt", "The installed package snapshot is invalid.") from exc

    @classmethod
    def _role(cls, installation: sqlite3.Row, role_id: str) -> dict:
        for role in cls._snapshot(installation)["roles"]:
            if role["role_id"] == role_id:
                return role
        raise PlatformError("role_not_found", "This role does not belong to this installation.")

    @staticmethod
    def _summary(installation: sqlite3.Row) -> dict:
        return {key: installation[key] for key in (
            "installation_id", "package_id", "version", "digest", "workspace",
            "state", "generation", "installed_at")}

    def install(self, installation_id: str, package_path: Path | str, workspace: Path | str,
                *, role_deployments: Mapping[str, Mapping] | None = None) -> dict:
        _identifier(installation_id)
        package = load_package(package_path)
        self._check_adapters({"tools": package.tools})
        snapshot = _json({
            "manifest": package.manifest, "roles": list(package.roles),
            "knowledge": package.knowledge, "tools": package.tools,
            "workflows": package.workflows,
        })
        with self._db(write=True) as db:
            if db.execute("SELECT 1 FROM installations WHERE installation_id=?", (installation_id,)).fetchone():
                raise PlatformError("installation_exists", "Installation identifiers cannot overwrite existing history.")
            workspace_path, bindings = normalize_deployments(
                {role["role_id"] for role in package.roles}, workspace, role_deployments,
                self.state_root, self._workspace_paths(db))
            timestamp = _now()
            db.execute(
                "INSERT INTO installations VALUES (?,?,?,?,?,?,?,?,?,?)",
                (installation_id, package.package_id, package.version, package.digest,
                 snapshot, _hash(snapshot), str(workspace_path), "inactive", 0, timestamp),
            )
            db.executemany("""INSERT INTO role_deployments
                (installation_id,role_id,workspace,access,created_at) VALUES (?,?,?,?,?)""",
                [(installation_id, role_id, binding["workspace"], binding["access"], timestamp)
                 for role_id, binding in bindings.items()])
            return self._summary(self._installation(db, installation_id))

    def activate(self, installation_id: str) -> dict:
        with self._db(write=True) as db:
            installation = self._installation(db, installation_id)
            self._check_adapters(self._snapshot(installation))
            if installation["state"] == "active":
                return self._summary(installation)
            if installation["state"] == "uninstalled":
                raise PlatformError("installation_uninstalled", "This installation has been uninstalled; its history is retained.")
            if db.execute("SELECT 1 FROM installations WHERE state='active'").fetchone():
                raise PlatformError("active_conflict", "Deactivate the current installation before activating another.")
            self._check_workspace(db, installation)
            db.execute("DELETE FROM actor_tokens WHERE installation_id=?", (installation_id,))
            db.execute("UPDATE installations SET state='active',generation=generation+1 WHERE installation_id=?", (installation_id,))
            return self._summary(self._installation(db, installation_id))

    def deactivate(self, installation_id: str) -> dict:
        with self._db(write=True) as db:
            installation = self._installation(db, installation_id)
            if db.execute("SELECT 1 FROM tasks WHERE installation_id=? AND state IN ('running','needs_reconcile')", (installation_id,)).fetchone():
                raise PlatformError("unfinished_work", "Running or uncertain work must be resolved before deactivation.")
            if installation["state"] == "active":
                db.execute("UPDATE installations SET state='inactive' WHERE installation_id=?", (installation_id,))
            db.execute("DELETE FROM actor_tokens WHERE installation_id=?", (installation_id,))
            return self._summary(self._installation(db, installation_id))

    def uninstall(self, installation_id: str) -> dict:
        with self._db(write=True) as db:
            installation = self._installation(db, installation_id)
            if installation["state"] == "active":
                raise PlatformError("installation_active", "Deactivate the installation before uninstalling it.")
            if db.execute("SELECT 1 FROM tasks WHERE installation_id=? AND state IN ('running','needs_reconcile')", (installation_id,)).fetchone():
                raise PlatformError("unfinished_work", "Running or uncertain work must be resolved before uninstalling.")
            db.execute("UPDATE installations SET state='uninstalled' WHERE installation_id=?", (installation_id,))
            db.execute("DELETE FROM actor_tokens WHERE installation_id=?", (installation_id,))
            return self._summary(self._installation(db, installation_id))

    def status(self) -> dict:
        if not self.db_path.exists():
            return {"version": VERSION, "initialized": False, "active": None, "installations": []}
        with self._db() as db:
            rows = list(db.execute("SELECT * FROM installations ORDER BY installation_id"))
            return {"version": VERSION, "initialized": True,
                    "active": next((row["installation_id"] for row in rows if row["state"] == "active"), None),
                    "installations": [self._summary(row) for row in rows]}

    def view(self, installation_id: str) -> dict:
        with self._db() as db:
            installation = self._installation(db, installation_id)
            return {**self._summary(installation), "package": self._snapshot(installation),
                    "role_deployments": self._deployments(db, installation)}

    def _context(self, db: sqlite3.Connection, installation: sqlite3.Row, role_id: str) -> dict:
        role = self._role(installation, role_id)
        package = self._snapshot(installation)
        binding = self._deployments(db, installation)[role_id]
        return {"installation_id": installation["installation_id"], "generation": installation["generation"],
                "package_id": installation["package_id"], "role": role,
                "workspace": binding["workspace"], "workspace_access": binding["access"],
                "knowledge": package["knowledge"],
                "tools": {tool_id: package["tools"][tool_id] for tool_id in role["tools"]}}

    def context(self, installation_id: str, role_id: str) -> dict:
        with self._db() as db:
            installation = self._installation(db, installation_id, active=True)
            self._check_workspace(db, installation)
            return self._context(db, installation, role_id)

    def register(self, installation_id: str, role_id: str) -> dict:
        with self._db(write=True) as db:
            installation = self._installation(db, installation_id, active=True)
            self._role(installation, role_id)
            generation, timestamp, token = installation["generation"], _now(), secrets.token_urlsafe(32)
            db.execute("DELETE FROM actor_tokens WHERE installation_id=? AND role_id=?", (installation_id, role_id))
            db.execute("""INSERT INTO registrations VALUES (?,?,?,?)
                ON CONFLICT(installation_id,role_id,generation)
                DO UPDATE SET registered_at=excluded.registered_at""", (installation_id, role_id, generation, timestamp))
            db.execute("INSERT INTO actor_tokens VALUES (?,?,?,?)", (_hash(token), installation_id, role_id, generation))
            return {"installation_id": installation_id, "role_id": role_id,
                    "generation": generation, "registered_at": timestamp, "token": token,
                    "runtime": None, "online": False}

    def bind(self, installation_id: str, role_id: str) -> Actor:
        registration = self.register(installation_id, role_id)
        return Actor(installation_id, role_id, registration["generation"], registration["token"])

    @classmethod
    def _actor(cls, db: sqlite3.Connection, actor: Actor) -> sqlite3.Row:
        if (not isinstance(actor, Actor) or not isinstance(actor.token, str)
                or type(actor.generation) is not int or actor.generation < 1):
            raise PlatformError("invalid_actor", "Worker access requires a valid coordinator-issued actor.")
        record = db.execute("SELECT * FROM actor_tokens WHERE token_hash=?", (_hash(actor.token),)).fetchone()
        if record is None or (record["installation_id"], record["role_id"], record["generation"]) != (
                actor.installation_id, actor.role_id, actor.generation):
            raise PlatformError("invalid_actor", "This actor is forged, revoked or stale.")
        installation = cls._installation(db, actor.installation_id, active=True)
        if installation["generation"] != actor.generation:
            raise PlatformError("invalid_actor", "This actor belongs to an earlier activation.")
        cls._role(installation, actor.role_id)
        return installation

    def context_for(self, actor: Actor) -> dict:
        with self._db() as db:
            installation = self._actor(db, actor)
            self._check_workspace(db, installation)
            return self._context(db, installation, actor.role_id)

    def submit(self, installation_id: str, role_id: str, text: str,
               client_request_id: str | None = None) -> dict:
        if not isinstance(text, str) or not text.strip():
            raise PlatformError("invalid_task", "A task must contain nonempty text.")
        if client_request_id is not None and (not isinstance(client_request_id, str) or not client_request_id.strip() or len(client_request_id) > 200):
            raise PlatformError("invalid_request_id", "Request identifiers must be nonempty strings up to 200 characters.")
        with self._db(write=True) as db:
            installation = self._installation(db, installation_id, active=True)
            self._role(installation, role_id)
            if client_request_id is not None:
                previous = db.execute("SELECT * FROM tasks WHERE installation_id=? AND client_request_id=?", (installation_id, client_request_id)).fetchone()
                if previous is not None:
                    if previous["role_id"] != role_id or previous["text"] != text:
                        raise PlatformError("request_conflict", "This request identifier already refers to different task content.")
                    return dict(previous)
            task_id = str(uuid.uuid4())
            db.execute("""INSERT INTO tasks
                (task_id,installation_id,role_id,generation,text,client_request_id,state,
                 created_at,completed_at,dispatch_generation,parent_task_id)
                VALUES (?,?,?,?,?,?,?,?,NULL,?,NULL)""",
                (task_id, installation_id, role_id, installation["generation"], text,
                 client_request_id, "queued", _now(), installation["generation"]))
            return dict(db.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone())

    @staticmethod
    def _task(db: sqlite3.Connection, installation_id: str, role_id: str,
              generation: int, task_id: str) -> sqlite3.Row:
        task = db.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone()
        if task is None or task["installation_id"] != installation_id or task["role_id"] != role_id:
            raise PlatformError("task_scope", "This task is not accessible to this actor.")
        if task["dispatch_generation"] != generation:
            raise PlatformError("stale_task", "This task belongs to an earlier activation and requires explicit continuation.")
        return task

    def task_for(self, actor: Actor, task_id: str) -> dict:
        with self._db() as db:
            self._actor(db, actor)
            return dict(self._task(db, actor.installation_id, actor.role_id, actor.generation, task_id))

    def run(self, installation_id: str, task_id: str, tool_id: str) -> dict:
        # Trusted local administrative convenience; worker callers use run_as.
        with self._db(write=True) as db:
            installation = self._installation(db, installation_id, active=True)
            task = db.execute("SELECT role_id FROM tasks WHERE task_id=? AND installation_id=?", (task_id, installation_id)).fetchone()
            if task is None:
                raise PlatformError("task_scope", "This task is not part of this installation.")
            return self._execute(db, installation, task["role_id"], task_id, tool_id)

    def run_as(self, actor: Actor, task_id: str, tool_id: str) -> dict:
        with self._db(write=True) as db:
            installation = self._actor(db, actor)
            return self._execute(db, installation, actor.role_id, task_id, tool_id,
                                 owner_hash=_hash(actor.token))

    def _clock_now(self) -> float:
        value = self.clock()
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise PlatformError("invalid_clock", "The deployment clock must return finite epoch seconds.")
        return float(value)

    @staticmethod
    def _ttl(ttl_seconds: float) -> float:
        if (isinstance(ttl_seconds, bool) or not isinstance(ttl_seconds, (int, float))
                or not 0 < ttl_seconds <= 86400 or not math.isfinite(ttl_seconds)):
            raise PlatformError("invalid_lease", "Lease duration must be finite and within 0–86400 seconds.")
        return float(ttl_seconds)

    @staticmethod
    def _attempt_view(attempt: sqlite3.Row) -> dict:
        return {key: attempt[key] for key in attempt.keys()
                if key not in ("owner_hash", "lease_hash")}

    @staticmethod
    def _attempt_event(db: sqlite3.Connection, attempt: sqlite3.Row, kind: str, detail: dict) -> None:
        db.execute("""INSERT INTO attempt_events
            (event_id,attempt_id,installation_id,kind,detail,created_at) VALUES (?,?,?,?,?,?)""",
            (str(uuid.uuid4()), attempt["attempt_id"], attempt["installation_id"], kind, _json(detail), _now()))

    def _claim(self, db: sqlite3.Connection, installation: sqlite3.Row, role_id: str,
               task_id: str, tool_id: str, owner_hash: str, ttl_seconds: float,
               member_id: str | None = None) -> dict:
        installation_id, generation = installation["installation_id"], installation["generation"]
        self._guard_member_claim(db, installation_id, role_id, member_id)
        self._check_workspace(db, installation)
        task = self._task(db, installation_id, role_id, generation, task_id)
        role = self._role(installation, role_id)
        if tool_id not in role["tools"]:
            raise PlatformError("tool_denied", "This role is not granted this tool.")
        if task["state"] != "queued":
            raise PlatformError("task_not_queued", "Only queued tasks can be claimed; uncertain attempts need reconciliation.")
        adapter = self._snapshot(installation)["tools"][tool_id]["adapter"]
        if adapter not in self.adapters and adapter not in self.external_adapters:
            raise PlatformError("adapter_unavailable", "This deployment does not provide the task's adapter.")
        ordinal = db.execute("SELECT COALESCE(MAX(ordinal),0)+1 FROM attempts WHERE task_id=?", (task_id,)).fetchone()[0]
        attempt_id, lease_token = str(uuid.uuid4()), secrets.token_urlsafe(32)
        expires_at = self._clock_now() + self._ttl(ttl_seconds)
        db.execute("""INSERT INTO attempts
            (attempt_id,task_id,installation_id,role_id,generation,ordinal,tool_id,
             owner_hash,lease_hash,expires_at,state,created_at,finished_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,'running',?,NULL)""",
            (attempt_id, task_id, installation_id, role_id, generation, ordinal, tool_id,
             owner_hash, _hash(lease_token), expires_at, _now()))
        db.execute("UPDATE tasks SET state='running' WHERE task_id=?", (task_id,))
        attempt = db.execute("SELECT * FROM attempts WHERE attempt_id=?", (attempt_id,)).fetchone()
        self._attempt_event(db, attempt, "claimed", {"ordinal": ordinal})
        return {**self._attempt_view(attempt), "lease_token": lease_token}

    def claim(self, actor: Actor, task_id: str, tool_id: str, ttl_seconds: float = 60) -> dict:
        """Persist exclusive ownership before an external worker begins work.

        The bearer lease is issued once. State stores only its hash, and an
        expired lease never authorizes an automatic retry of unknown effects.
        """
        self._ttl(ttl_seconds)
        with self._db(write=True) as db:
            installation = self._actor(db, actor)
            return self._claim(db, installation, actor.role_id, task_id, tool_id,
                               _hash(actor.token), ttl_seconds)

    @staticmethod
    def _known_owner(attempt: sqlite3.Row, actor: Actor, lease_token: str) -> bool:
        # Rejected completions may be audited even after revocation. A caller
        # must still demonstrate original ownership before writing that audit.
        return (isinstance(actor, Actor) and isinstance(actor.token, str)
                and type(actor.generation) is int and actor.generation > 0
                and isinstance(lease_token, str)
                and (actor.installation_id, actor.role_id, actor.generation) ==
                    (attempt["installation_id"], attempt["role_id"], attempt["generation"])
                and secrets.compare_digest(attempt["owner_hash"], _hash(actor.token))
                and secrets.compare_digest(attempt["lease_hash"], _hash(lease_token)))

    def _owned_attempt(self, db: sqlite3.Connection, actor: Actor,
                       attempt: sqlite3.Row, lease_token: str) -> sqlite3.Row:
        self._actor(db, actor)
        if (attempt["installation_id"], attempt["role_id"], attempt["generation"]) != (
                actor.installation_id, actor.role_id, actor.generation):
            raise PlatformError("attempt_scope", "This attempt is not accessible to this actor.")
        if not secrets.compare_digest(attempt["owner_hash"], _hash(actor.token)):
            raise PlatformError("attempt_owner", "This actor does not own this attempt.")
        if not isinstance(lease_token, str) or not secrets.compare_digest(attempt["lease_hash"], _hash(lease_token)):
            raise PlatformError("lease_denied", "The attempt lease is invalid.")
        if attempt["state"] == "running":
            self._guard_attempt_member(db, attempt)
        return self._task(db, actor.installation_id, actor.role_id, actor.generation, attempt["task_id"])

    @staticmethod
    def _attempt(db: sqlite3.Connection, attempt_id: str) -> sqlite3.Row:
        attempt = db.execute("SELECT * FROM attempts WHERE attempt_id=?", (attempt_id,)).fetchone()
        if attempt is None:
            raise PlatformError("attempt_scope", "No accessible attempt exists with this identifier.")
        return attempt

    def renew(self, actor: Actor, attempt_id: str, lease_token: str,
              ttl_seconds: float = 60) -> dict:
        ttl_seconds = self._ttl(ttl_seconds)
        with self._db(write=True) as db:
            attempt = self._attempt(db, attempt_id)
            task = self._owned_attempt(db, actor, attempt, lease_token)
            if attempt["state"] != "running" or task["state"] != "running":
                raise PlatformError("stale_attempt", "Only the current running attempt can renew its lease.")
            timestamp = self._clock_now()
            if attempt["expires_at"] <= timestamp:
                raise PlatformError("lease_expired", "An expired lease cannot be renewed.")
            expires_at = timestamp + ttl_seconds
            db.execute("UPDATE attempts SET expires_at=? WHERE attempt_id=?", (expires_at, attempt_id))
            self._attempt_event(db, attempt, "renewed", {"expires_at": expires_at})
            return self._attempt_view(self._attempt(db, attempt_id))

    @staticmethod
    def _runtime_detail(kind: str, detail: dict) -> dict:
        if not isinstance(kind, str) or kind not in _RUNTIME_EVENTS or not isinstance(detail, dict):
            raise PlatformError("invalid_runtime_event", "A supported runtime event and JSON object detail are required.")

        def validate(value: object, depth: int = 0) -> None:
            if depth > 8:
                raise ValueError("depth")
            if value is None or type(value) in (str, bool, int):
                return
            if type(value) is float and math.isfinite(value):
                return
            if isinstance(value, dict) and all(type(key) is str for key in value):
                for item in value.values():
                    validate(item, depth + 1)
                return
            if isinstance(value, list):
                for item in value:
                    validate(item, depth + 1)
                return
            raise ValueError("type")

        try:
            validate(detail)
            encoded = json.dumps(detail, ensure_ascii=False, sort_keys=True,
                                 separators=(",", ":"), allow_nan=False)
            if len(encoded.encode("utf-8")) > 8192:
                raise ValueError("size")
            return json.loads(encoded)
        except (ValueError, TypeError, RecursionError, OverflowError, UnicodeError) as exc:
            raise PlatformError("invalid_runtime_event", "Runtime event detail must be JSON within 8 KiB and 8 nesting levels.") from exc

    def record_runtime_event(self, actor: Actor, attempt_id: str, lease_token: str,
                             kind: str, detail: dict) -> dict:
        """Record normalized runtime metadata only while ownership is live.

        Controllers must choose their metadata fields explicitly; raw protocol
        output, prompts and deployment credentials do not belong in this log.
        """
        detail = self._runtime_detail(kind, detail)
        with self._db(write=True) as db:
            attempt = self._attempt(db, attempt_id)
            task = self._owned_attempt(db, actor, attempt, lease_token)
            if attempt["state"] != "running" or task["state"] != "running":
                raise PlatformError("stale_attempt", "Runtime metadata requires the current running attempt.")
            if attempt["expires_at"] <= self._clock_now():
                raise PlatformError("lease_expired", "An expired lease cannot publish runtime metadata.")
            encoded = _json(detail)
            if actor.token in encoded or lease_token in encoded:
                raise PlatformError("invalid_runtime_event", "Runtime metadata cannot contain ownership credentials.")
            self._attempt_event(db, attempt, kind, detail)
            return self._attempt_view(attempt)

    def fence_uncertain(self, actor: Actor, attempt_id: str, lease_token: str,
                        reason: str) -> dict:
        """Fence a lost external execution without asserting its effects stopped.

        The exact original owner can report uncertainty after registration
        revocation or lease expiry, but cannot affect any newer execution.
        Reconciliation remains an explicit administrative operation.
        """
        if not isinstance(reason, str) or not _RUNTIME_REASON.fullmatch(reason):
            raise PlatformError("invalid_runtime_reason", "A stable lowercase runtime reason of at most 100 characters is required.")
        with self._db(write=True) as db:
            attempt = self._attempt(db, attempt_id)
            if not self._known_owner(attempt, actor, lease_token):
                raise PlatformError("attempt_owner", "Only the original actor and lease can fence this execution.")
            installation = self._installation(db, actor.installation_id, active=True)
            task = self._task(db, actor.installation_id, actor.role_id, actor.generation, attempt["task_id"])
            latest = db.execute("SELECT attempt_id FROM attempts WHERE task_id=? ORDER BY ordinal DESC LIMIT 1",
                                (attempt["task_id"],)).fetchone()
            if (installation["generation"] != actor.generation or attempt["state"] != "running"
                    or task["state"] != "running" or latest["attempt_id"] != attempt_id
                    or db.execute("SELECT 1 FROM results WHERE task_id=?", (attempt["task_id"],)).fetchone()):
                raise PlatformError("stale_attempt", "Only the current running execution can be fenced.")
            timestamp = _now()
            self._hold_runtime_inputs(db, attempt)
            db.execute("UPDATE attempts SET state='expired',finished_at=? WHERE attempt_id=?", (timestamp, attempt_id))
            db.execute("UPDATE tasks SET state='needs_reconcile' WHERE task_id=?", (attempt["task_id"],))
            self._attempt_event(db, attempt, "runtime_uncertain", {"reason": reason, "effects": "unknown"})
            return self._attempt_view(self._attempt(db, attempt_id))

    @staticmethod
    def _completion_payload(output: str | None, error: dict | None) -> tuple[str | None, str | None]:
        if output is not None and not isinstance(output, str):
            raise PlatformError("invalid_result", "Completion output must be text or null.")
        if error is not None:
            if (not isinstance(error, dict) or set(error) != {"code", "message"}
                    or any(not isinstance(error[key], str) or not error[key].strip() for key in ("code", "message"))
                    or output is not None):
                raise PlatformError("invalid_result", "Failed completion requires code/message text and no output.")
        return output, _json(error) if error is not None else None

    def _finish(self, db: sqlite3.Connection, task: sqlite3.Row, attempt: sqlite3.Row,
                output: str | None, error_json: str | None) -> dict:
        task_id, installation_id = task["task_id"], task["installation_id"]
        role_id, generation = attempt["role_id"], attempt["generation"]
        failed, timestamp = error_json is not None, _now()
        db.execute("""INSERT INTO results
            (task_id,installation_id,role_id,generation,tool_id,output,status,error,created_at,attempt_id)
            VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (task_id, installation_id, role_id, generation, attempt["tool_id"], output,
             "error" if failed else "ok", error_json, timestamp, attempt["attempt_id"]))
        kind, state = ("task_failed", "failed") if failed else ("task_completed", "completed")
        db.execute("INSERT INTO notifications VALUES (?,?,?,?,?,?,?)",
                   (str(uuid.uuid4()), task_id, installation_id, role_id, generation, kind, timestamp))
        db.execute("UPDATE tasks SET state=?,completed_at=? WHERE task_id=?", (state, timestamp, task_id))
        db.execute("UPDATE attempts SET state=?,finished_at=? WHERE attempt_id=?",
                   (state, timestamp, attempt["attempt_id"]))
        self._attempt_event(db, attempt, state, {"status": "error" if failed else "ok"})
        self._enqueue(db, installation_id, generation, task_id, role_id, kind,
                      {"task_id": task_id, "attempt_id": attempt["attempt_id"],
                       "status": "error" if failed else "ok"}, "terminal:" + task_id)
        self._child_terminal(db, db.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone())
        return self._result(db.execute("SELECT * FROM results WHERE task_id=?", (task_id,)).fetchone())

    def complete(self, actor: Actor, attempt_id: str, lease_token: str,
                 output: str | None = None, error: dict | None = None) -> dict:
        output, error_json = self._completion_payload(output, error)
        rejection = None
        result = None
        with self._db(write=True) as db:
            attempt = self._attempt(db, attempt_id)
            try:
                task = self._owned_attempt(db, actor, attempt, lease_token)
                if attempt["state"] in ("completed", "failed"):
                    previous = db.execute("SELECT * FROM results WHERE task_id=?", (attempt["task_id"],)).fetchone()
                    if (previous is None or previous["attempt_id"] != attempt_id
                            or previous["output"] != output or previous["error"] != error_json):
                        raise PlatformError("completion_conflict", "This attempt already completed with a different result.")
                    result = self._result(previous)
                else:
                    if attempt["state"] != "running" or task["state"] != "running":
                        raise PlatformError("stale_attempt", "This attempt is no longer authorized to complete the task.")
                    if attempt["expires_at"] <= self._clock_now():
                        raise PlatformError("lease_expired", "This attempt's lease expired; effects require reconciliation.")
                    self._ensure_runtime_inputs_resolved(db, attempt)
            except PlatformError as exc:
                # Raise only after the transaction commits the diagnostic.
                # No untrusted payload or credential is copied into the audit.
                rejection = exc
                if self._known_owner(attempt, actor, lease_token):
                    self._attempt_event(db, attempt, "completion_rejected", {"code": exc.code})
            if rejection is None and result is None:
                # Persistence and coordination failures must roll back the
                # whole completion, not be confused with rejected input.
                result = self._finish(db, task, attempt, output, error_json)
        if rejection is not None:
            raise rejection
        return result

    def expire_attempts(self, installation_id: str) -> list[dict]:
        """Fence overdue attempts without guessing whether their work happened."""
        with self._db(write=True) as db:
            self._installation(db, installation_id)
            timestamp = self._clock_now()
            rows = list(db.execute("""SELECT * FROM attempts WHERE installation_id=?
                AND state='running' AND expires_at<=? ORDER BY created_at,attempt_id""", (installation_id, timestamp)))
            expired = []
            for attempt in rows:
                self._hold_runtime_inputs(db, attempt)
                db.execute("UPDATE attempts SET state='expired',finished_at=? WHERE attempt_id=?", (_now(), attempt["attempt_id"]))
                db.execute("""UPDATE tasks SET state='needs_reconcile' WHERE task_id=?
                    AND state='running' AND dispatch_generation=?""", (attempt["task_id"], attempt["generation"]))
                self._attempt_event(db, attempt, "expired", {"effects": "unknown"})
                expired.append(dict(db.execute("SELECT * FROM tasks WHERE task_id=?", (attempt["task_id"],)).fetchone()))
            return expired

    @staticmethod
    def _reason(reason: str) -> str:
        if not isinstance(reason, str) or not reason.strip() or len(reason) > 2000:
            raise PlatformError("invalid_reason", "An explicit nonempty reason of at most 2000 characters is required.")
        return reason

    def reconcile_task(self, installation_id: str, task_id: str, resolution: str,
                       reason: str, safe_to_retry: bool = False) -> dict:
        self._reason(reason)
        if resolution not in ("retry", "cancel"):
            raise PlatformError("invalid_resolution", "Uncertain work may only be explicitly retried or cancelled.")
        if resolution == "retry" and safe_to_retry is not True:
            raise PlatformError("unsafe_retry", "Retry requires an explicit determination that repeating the work is safe.")
        with self._db(write=True) as db:
            installation = self._installation(db, installation_id, active=True)
            task = db.execute("SELECT * FROM tasks WHERE task_id=? AND installation_id=?", (task_id, installation_id)).fetchone()
            if task is None:
                raise PlatformError("task_scope", "This task does not belong to the installation.")
            if task["dispatch_generation"] != installation["generation"]:
                raise PlatformError("stale_task", "This task belongs to an earlier activation.")
            if task["state"] != "needs_reconcile":
                raise PlatformError("reconciliation_required", "Only uncertain work can be reconciled.")
            attempt = db.execute("SELECT * FROM attempts WHERE task_id=? ORDER BY ordinal DESC LIMIT 1", (task_id,)).fetchone()
            migrated_unknown = False
            if attempt is None:
                evidence = db.execute("""SELECT detail FROM attempt_events WHERE installation_id=?
                    AND attempt_id IS NULL AND kind='migration_uncertain'""", (installation_id,))
                migrated_unknown = any(json.loads(row["detail"]) == {
                    "task_id": task_id, "source": "schema1_unknown_owner"} for row in evidence)
                migrated_unknown = migrated_unknown and not db.execute(
                    "SELECT 1 FROM results WHERE task_id=?", (task_id,)).fetchone()
            if not migrated_unknown and (attempt is None or attempt["state"] != "expired"):
                raise PlatformError("reconciliation_required", "No expired attempt exists to reconcile.")
            state = "queued" if resolution == "retry" else "cancelled"
            db.execute("UPDATE tasks SET state=?,completed_at=? WHERE task_id=?",
                       (state, None if resolution == "retry" else _now(), task_id))
            detail = {"resolution": resolution, "reason": reason, "safe_to_retry": safe_to_retry is True}
            if migrated_unknown:
                detail.update({"task_id": task_id, "source": "schema1_unknown_owner"})
                db.execute("""INSERT INTO attempt_events
                    (event_id,attempt_id,installation_id,kind,detail,created_at)
                    VALUES (?,NULL,?,'reconciled',?,?)""",
                    (str(uuid.uuid4()), installation_id, _json(detail), _now()))
            else:
                self._attempt_event(db, attempt, "reconciled", detail)
            updated = db.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone()
            if resolution == "cancel":
                self._enqueue(db, installation_id, installation["generation"], task_id, task["role_id"],
                              "task_cancelled", {"task_id": task_id}, "terminal:" + task_id)
                self._child_terminal(db, updated)
            return dict(updated)

    def resume_task(self, installation_id: str, task_id: str, reason: str) -> dict:
        self._reason(reason)
        with self._db(write=True) as db:
            installation = self._installation(db, installation_id, active=True)
            task = db.execute("SELECT * FROM tasks WHERE task_id=? AND installation_id=?", (task_id, installation_id)).fetchone()
            if task is None:
                raise PlatformError("task_scope", "This task does not belong to the installation.")
            generation = installation["generation"]
            if task["dispatch_generation"] == generation:
                previous = db.execute("SELECT reason FROM continuations WHERE task_id=? AND to_generation=?", (task_id, generation)).fetchone()
                if previous is not None and previous["reason"] == reason:
                    return dict(task)
                raise PlatformError("continuation_conflict", "This task is already bound to the current activation.")
            if task["state"] not in ("queued", "waiting", "awaiting_decision"):
                raise PlatformError("task_not_resumable", "Only retained queued or waiting tasks can be continued.")
            self._role(installation, task["role_id"])
            self._check_workspace(db, installation)
            state = self._resume_coordination(db, task, generation)
            if state not in ("queued", "waiting", "awaiting_decision"):
                raise PlatformError("invalid_continuation", "Coordination returned an invalid continuation state.")
            db.execute("""INSERT INTO continuations
                (continuation_id,task_id,installation_id,from_generation,to_generation,reason,created_at)
                VALUES (?,?,?,?,?,?,?)""", (str(uuid.uuid4()), task_id, installation_id,
                task["dispatch_generation"], generation, reason, _now()))
            db.execute("UPDATE tasks SET dispatch_generation=?,state=? WHERE task_id=?", (generation, state, task_id))
            return dict(db.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone())

    def attempts(self, installation_id: str, task_id: str | None = None) -> list[dict]:
        with self._db() as db:
            self._installation(db, installation_id)
            if task_id is not None and not db.execute("SELECT 1 FROM tasks WHERE task_id=? AND installation_id=?", (task_id, installation_id)).fetchone():
                raise PlatformError("task_scope", "This task does not belong to the installation.")
            query = "SELECT * FROM attempts WHERE installation_id=?"
            params = [installation_id]
            if task_id is not None:
                query += " AND task_id=?"
                params.append(task_id)
            rows = db.execute(query + " ORDER BY created_at,task_id,ordinal", params)
            records = []
            for row in rows:
                events = []
                for event in db.execute("SELECT * FROM attempt_events WHERE attempt_id=? ORDER BY rowid", (row["attempt_id"],)):
                    events.append({**dict(event), "detail": json.loads(event["detail"])})
                records.append({**self._attempt_view(row), "events": events})
            return records

    def request_runtime_stop(self, installation_id: str, attempt_id: str, reason: str) -> dict:
        """Record a request; only the live owning supervisor stops its process."""
        self._reason(reason)
        with self._db(write=True) as db:
            installation = self._installation(db, installation_id, active=True)
            attempt = self._attempt(db, attempt_id)
            if attempt["installation_id"] != installation_id:
                raise PlatformError("attempt_scope", "This attempt belongs to another installation.")
            if attempt["generation"] != installation["generation"] or attempt["state"] != "running":
                raise PlatformError("stale_attempt", "Only a current running attempt can receive a stop request.")
            if not db.execute("SELECT 1 FROM attempt_events WHERE attempt_id=? AND kind='runtime_start_requested'", (attempt_id,)).fetchone():
                raise PlatformError("runtime_not_started", "This attempt has no external runtime supervisor.")
            previous = db.execute("SELECT detail FROM attempt_events WHERE attempt_id=? AND kind='runtime_cancel_requested'", (attempt_id,)).fetchone()
            if previous is not None:
                if json.loads(previous["detail"])["reason"] != reason:
                    raise PlatformError("cancel_conflict", "A different stop request is already recorded.")
            else:
                self._attempt_event(db, attempt, "runtime_cancel_requested", {"reason": reason})
            return {"attempt_id": attempt_id, "stop_requested": True, "termination_confirmed": False}

    def runtime_stop_requested(self, actor: Actor, attempt_id: str, lease_token: str) -> bool:
        with self._db() as db:
            attempt = self._attempt(db, attempt_id)
            self._owned_attempt(db, actor, attempt, lease_token)
            if attempt["state"] != "running" or attempt["expires_at"] <= self._clock_now():
                raise PlatformError("lease_expired", "The runtime no longer has a live attempt lease.")
            return db.execute("SELECT 1 FROM attempt_events WHERE attempt_id=? AND kind='runtime_cancel_requested'", (attempt_id,)).fetchone() is not None

    def _execute(self, db: sqlite3.Connection, installation: sqlite3.Row,
                 role_id: str, task_id: str, tool_id: str, owner_hash: str = "admin") -> dict:
        """Execute under a validated caller's existing write transaction."""
        installation_id, generation = installation["installation_id"], installation["generation"]
        self._check_workspace(db, installation)
        task = self._task(db, installation_id, role_id, generation, task_id)
        role = self._role(installation, role_id)
        if tool_id not in role["tools"]:
            raise PlatformError("tool_denied", "This role is not granted this tool.")
        adapter = self._snapshot(installation)["tools"][tool_id]["adapter"]
        if adapter in self.external_adapters:
            raise PlatformError("external_adapter_required", "This tool requires an external runtime controller and explicit attempt ownership.")
        existing = db.execute("SELECT * FROM results WHERE task_id=?", (task_id,)).fetchone()
        if existing is not None:
            if existing["tool_id"] != tool_id:
                raise PlatformError("result_conflict", "This task already completed with another tool.")
            return self._result(existing)
        if task["state"] != "queued":
            raise PlatformError("task_not_queued", "Only queued tasks can run; unfinished attempts require recovery.")
        adapter = self._snapshot(installation)["tools"][tool_id]["adapter"]
        if adapter not in self.adapters:
            raise PlatformError("adapter_unavailable", "This deployment does not provide the task's adapter.")
        issued = self._claim(db, installation, role_id, task_id, tool_id, owner_hash, 60)
        attempt = self._attempt(db, issued["attempt_id"])
        error = None
        try:
            output = self.adapters[adapter](task["text"])
            if not isinstance(output, str):
                output = None
                error = {"code": "invalid_adapter_result", "message": "Text adapters must return a string."}
        except Exception:
            output = None
            # Exception messages may contain deployment secrets or paths.
            error = {"code": "adapter_failed", "message": "The text adapter failed."}
        return self._finish(db, task, attempt, output, _json(error) if error else None)

    @staticmethod
    def _result(row: sqlite3.Row) -> dict:
        value = dict(row)
        if value["error"] is not None:
            value["error"] = json.loads(value["error"])
        return value

    def _active_records(self, installation_id: str, table: str) -> list[dict]:
        with self._db() as db:
            installation = self._installation(db, installation_id, active=True)
            return [(self._result(row) if table == "results" else dict(row)) for row in db.execute(
                f"SELECT * FROM {table} WHERE installation_id=? AND generation=? ORDER BY created_at,task_id",
                (installation_id, installation["generation"]))]

    def results(self, installation_id: str) -> list[dict]:
        return self._active_records(installation_id, "results")

    def notifications(self, installation_id: str) -> list[dict]:
        return self._active_records(installation_id, "notifications")

    def history(self, installation_id: str) -> dict:
        with self._db() as db:
            installation = self._installation(db, installation_id)
            history = {"installation": self._summary(installation)}
            for table in ("registrations", "tasks", "results", "notifications"):
                history[table] = [(self._result(row) if table == "results" else dict(row)) for row in db.execute(
                    f"SELECT * FROM {table} WHERE installation_id=? ORDER BY rowid", (installation_id,))]
            history["attempts"] = [self._attempt_view(row) for row in db.execute(
                "SELECT * FROM attempts WHERE installation_id=? ORDER BY rowid", (installation_id,))]
            history["attempt_events"] = [{**dict(row), "detail": json.loads(row["detail"])} for row in db.execute(
                "SELECT * FROM attempt_events WHERE installation_id=? ORDER BY rowid", (installation_id,))]
            history["continuations"] = [dict(row) for row in db.execute(
                "SELECT * FROM continuations WHERE installation_id=? ORDER BY rowid", (installation_id,))]
            for table in ("decisions", "requests", "outbox"):
                history[table] = [dict(row) for row in db.execute(
                    f"SELECT * FROM {table} WHERE installation_id=? ORDER BY rowid", (installation_id,))]
            return history
