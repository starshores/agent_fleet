"""Offline local session maintenance. No cleanup occurs without an explicit apply.

Only the CLI invokes the Windows process guard. Tests use synthetic roots and
inject the guard; there is deliberately no command-line flag to disable it.
"""
from __future__ import annotations

import argparse
import contextlib
import ctypes
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import uuid


class CleanupError(RuntimeError):
    pass


PURPOSE = "masteragent.local-session-cleanup.v1"
UUID = re.compile(r"^[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}$")
PROTECTED = {"memory", "memories", "auth.json", ".credentials.json", "config.toml",
             "settings.json", "hooks.json", "skills", "plugins", "agent_bus"}
STATE_DELETE = {"threads", "thread_dynamic_tools", "thread_spawn_edges",
                "thread_attachments", "guardian_review_feedback", "thread_read_receipts",
                "backfill_state", "rollout_migration_state", "rollout_migration_skipped_rollouts"}
STATE_KEEP = {"_sqlx_migrations", "remote_control_enrollments", "external_agent_config_imports",
              "thread_sections", "projects", "project_roots", "project_idempotency_keys"}
DB_RULES = {
    "state_5.sqlite": (STATE_DELETE, STATE_KEEP),
    "thread_history_1.sqlite": ({"thread_turns", "thread_items", "thread_history_projection_state",
                                 "thread_realtime_items"}, {"_sqlx_migrations"}),
    "goals_1.sqlite": ({"thread_goals", "thread_goal_continuation_deferrals"}, {"_sqlx_migrations"}),
    "queue_1.sqlite": (set(), set()),
    "logs_2.sqlite": ({"logs"}, {"_sqlx_migrations", "sqlite_sequence"}),
}
GLOBAL_SESSION_KEYS = {
    "projectless-thread-ids", "thread-projectless-output-directories", "thread-workspace-root-hints",
    "sidebar-project-thread-orders", "electron-thread-read-state-v1", "queued-follow-ups",
    "thread-project-assignments",
}
LIMITATIONS = [
    "Only explicitly supplied local Codex/Claude homes and physical Claude project roots are covered.",
    "Cloud/remote conversations, browser caches, managed worktrees, other homes and backups are not deleted.",
    "Mixed nested UI state (including electron-persisted-atom-state), memories and agent-bus records are preserved; residual references may remain.",
    "This is a local maintenance implementation, not an official vendor reset protocol or a secure erase.",
]


def contains_session_keys(value, depth=0):
    """Classify mixed state without logging any values or unknown key names."""
    if depth > 64:
        return True  # Unknown deep structure cannot establish complete coverage.
    if isinstance(value, dict):
        return any(re.search(r"thread|session|conversation|follow.?up", str(key), re.I) or
                   contains_session_keys(child, depth + 1) for key, child in value.items())
    if isinstance(value, list):
        return any(contains_session_keys(child, depth + 1) for child in value)
    return False


def reparse(path: Path) -> bool:
    stat = path.lstat()
    return path.is_symlink() or bool(getattr(stat, "st_file_attributes", 0) & 0x400)


def absolute(path, *, existing=True) -> Path:
    raw = str(path)
    if raw.startswith(("\\\\", "//")) or re.match(r"^[A-Za-z]:.*:", raw):
        raise CleanupError("UNC, device, network and alternate-stream paths are not allowed")
    path = Path(path)
    if not path.is_absolute() or ".." in path.parts:
        raise CleanupError("An absolute path without '..' is required")
    # Inspect every existing component before resolve(), which otherwise follows junctions.
    for part in reversed((path, *path.parents)):
        if part.exists() or part.is_symlink():
            if reparse(part):
                raise CleanupError("Reparse-point paths are not permitted: " + str(part))
    if existing and not path.exists():
        raise CleanupError("Path does not exist: " + str(path))
    return path.resolve(strict=existing)


def within(path: Path, root: Path) -> bool:
    return path != root and root in path.parents


def root_path(value, *, project=False) -> Path:
    path = absolute(value)
    if not path.is_dir() or path == Path(path.anchor) or path == Path.home().resolve():
        raise CleanupError("A filesystem root or user home is not an allowed maintenance root")
    return path


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def file_record(path: Path, root: Path, kind: str, group=None):
    path = absolute(path)
    if not within(path, root) or not path.is_file() or path.stat().st_nlink != 1:
        raise CleanupError("Candidate is outside its root, not a regular file, or hard-linked")
    if any(part.lower() in PROTECTED for part in path.relative_to(root).parts):
        raise CleanupError("Protected configuration, credential or memory path")
    stat = path.stat()
    value = {"path": str(path), "root": str(root), "kind": kind,
             "size": stat.st_size, "mtime_ns": stat.st_mtime_ns, "sha256": digest(path)}
    if group:
        value["group"] = str(group)
    return value


@contextlib.contextmanager
def schema_copy(path: Path):
    # Even mode=ro may update/create SHM. Inspect an ephemeral copy instead of
    # opening the user's database. An active writer can invalidate this snapshot;
    # the offline apply checks the complete plan again before any mutation.
    with tempfile.TemporaryDirectory(prefix="session-schema-") as temporary:
        copy = Path(temporary) / path.name
        for suffix in ("", "-wal", "-shm", "-journal"):
            source = Path(str(path) + suffix)
            if source.exists():
                absolute(source)
                shutil.copyfile(source, Path(str(copy) + suffix))
        with contextlib.closing(sqlite3.connect(copy.as_uri() + "?mode=ro", uri=True, timeout=0)) as db:
            yield db


def schema(path: Path):
    with schema_copy(path) as db:
        return db.execute("SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name").fetchall()


def validate_db(path: Path):
    rows = schema(path)
    delete, keep = DB_RULES[path.name]
    tables = {row[1] for row in rows if row[0] == "table"}
    if tables - delete - keep or any(row[0] in {"trigger", "view"} for row in rows):
        raise CleanupError("Unrecognized database schema: " + path.name)
    if path.name == "state_5.sqlite" and "threads" not in tables:
        raise CleanupError("Missing threads table")
    if path.name == "logs_2.sqlite" and "logs" in tables:
        with schema_copy(path) as db:
            cols = {row[1] for row in db.execute('PRAGMA table_info("logs")')}
        if "thread_id" not in cols:
            raise CleanupError("Unrecognized log schema")
    return {"path": str(path), "tables": sorted(tables),
            "schema_sha256": hashlib.sha256(json.dumps(rows, ensure_ascii=False).encode()).hexdigest()}


def collect_tree(path, root, records, uncovered, *, projects=False, physical_roots=()):
    if not path.exists() and not path.is_symlink():
        return
    if reparse(path):
        target = path.resolve()
        if target not in physical_roots:
            uncovered.append({"path": str(path), "reason": "junction/symlink requires an explicit physical project root"})
        return
    if path.name.lower() in PROTECTED:
        return
    if path.is_dir():
        for child in sorted(path.iterdir()):
            collect_tree(child, root, records, uncovered, projects=projects, physical_roots=physical_roots)
        return
    if projects:
        # Shared project trees retain memory, arbitrary project files and configuration.
        rel = path.relative_to(root)
        session_owned = any(UUID.fullmatch(part) for part in rel.parts[:-1])
        selected = (path.suffix == ".jsonl" and UUID.fullmatch(path.stem)) or path.name == "sessions-index.json"
        selected = selected or (session_owned and ("subagents" in rel.parts or "tool-results" in rel.parts))
        if not selected:
            if path.suffix == ".jsonl" or path.name.startswith("session"):
                uncovered.append({"path": str(path), "reason": "unrecognized Claude session artifact"})
            return
    try:
        records.append(file_record(path, root, "remove"))
    except CleanupError as exc:
        uncovered.append({"path": str(path), "reason": str(exc)})


def make_plan(project_root, codex_homes=(), claude_homes=(), claude_projects_roots=()):
    project = root_path(project_root, project=True)
    codex = [root_path(x) for x in codex_homes]
    claude = [root_path(x) for x in claude_homes]
    projects = [root_path(x) for x in claude_projects_roots]
    homes = codex + claude
    if any(root == project or within(project, root) for root in homes + projects):
        raise CleanupError("A source root must not contain the maintenance project")
    if not homes:
        raise CleanupError("Supply at least one explicit Codex or Claude home")
    if len(set(homes + projects)) != len(homes + projects):
        raise CleanupError("Duplicate source roots")
    records, databases, uncovered = [], [], []
    for home in codex:
        for name in ("sessions", "archived_sessions", "shell_snapshots", "thread-writer-locks",
                     "tui-thread-reference-capabilities", "rollout-migrations"):
            collect_tree(home / name, home, records, uncovered)
        for name in ("history.jsonl", "session_index.jsonl"):
            if (home / name).exists():
                collect_tree(home / name, home, records, uncovered)
        for child in home.iterdir():
            if child.name.startswith(("state_", "thread_history_", "goals_", "queue_", "logs_")) and child.suffix == ".sqlite" and child.name not in DB_RULES:
                uncovered.append({"path": str(child), "reason": "unrecognized session database version"})
        for name in DB_RULES:
            path = home / name
            if not path.exists():
                continue
            try:
                absolute(path)
                db = validate_db(path)
                family = [p for p in (path, Path(str(path)+"-wal"), Path(str(path)+"-shm"), Path(str(path)+"-journal")) if p.exists()]
                entries = [file_record(p, home, "database" if p == path else "sidecar", path) for p in family]
                databases.append(db)
                records.extend(entries)
            except (CleanupError, sqlite3.Error) as exc:
                uncovered.append({"path": str(path), "reason": str(exc)})
        for name in (".codex-global-state.json", ".codex-global-state.json.bak"):
            path = home / name
            if path.exists():
                try:
                    absolute(path)
                    value = json.loads(path.read_text(encoding="utf-8"))
                    if not isinstance(value, dict):
                        raise CleanupError("Global state must be a JSON object")
                    if "electron-persisted-atom-state" in value:
                        mixed = value["electron-persisted-atom-state"]
                        if not isinstance(mixed, (dict, list)) or contains_session_keys(mixed):
                            uncovered.append({"path": str(path), "reason": "nested mixed UI state contains session-like keys or an unknown encoding; retained"})
                    unknown_session_keys = set(value) - GLOBAL_SESSION_KEYS - {"electron-persisted-atom-state"}
                    if any(re.search(r"thread|session|conversation|follow.?up", key, re.I) for key in unknown_session_keys):
                        uncovered.append({"path": str(path), "reason": "unrecognized top-level session-like UI keys retained"})
                    if GLOBAL_SESSION_KEYS.intersection(value):
                        records.append(file_record(path, home, "global_state"))
                except (CleanupError, ValueError) as exc:
                    uncovered.append({"path": str(path), "reason": str(exc)})
    for home in claude:
        for name in ("history.jsonl", "sessions", "session-env", "file-history", "paste-cache",
                     "shell-snapshots", "todos", "tasks", "debug"):
            collect_tree(home / name, home, records, uncovered)
        collect_tree(home / "projects", home / "projects", records, uncovered,
                     projects=True, physical_roots=projects)
    for root in projects:
        collect_tree(root, root, records, uncovered, projects=True, physical_roots=projects)
    unique = {}
    for item in records:
        unique[item["path"]] = item
    return {"purpose": PURPOSE, "scope": "AllSessions", "project_root": str(project),
            "codex_homes": list(map(str, codex)), "claude_homes": list(map(str, claude)),
            "claude_projects_roots": list(map(str, projects)),
            "files": sorted(unique.values(), key=lambda x: x["path"]), "databases": databases,
            "uncovered": uncovered, "limitations": LIMITATIONS}


def json_write(path: Path, value, *, exclusive=False):
    owner = value.get("manifest_id", "") if isinstance(value, dict) else ""
    temporary = path if exclusive else path.with_name("." + path.name + "." +
                ((owner + ".") if owner else "") + uuid.uuid4().hex + ".next")
    with temporary.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    if not exclusive:
        os.replace(temporary, path)


def verify_file(path, root, expected):
    path = absolute(path)
    if not within(path, absolute(root)) or not path.is_file() or path.stat().st_nlink != 1:
        raise CleanupError("Unsafe file boundary, type or link count")
    if digest(path) != expected:
        raise CleanupError("File hash changed: " + str(path))
    return path


def prepare_copy(source, temporary, expected, created):
    source = absolute(source)
    temporary = absolute(temporary, existing=False)
    with temporary.open("xb") as output:
        created.append(temporary)
        with source.open("rb") as input_file:
            shutil.copyfileobj(input_file, output, length=1024 * 1024)
        output.flush()
        os.fsync(output.fileno())
    verify_file(temporary, temporary.parent, expected)


def publish_new(temporary, destination, expected):
    temporary = verify_file(temporary, Path(temporary).parent, expected)
    destination = absolute(destination, existing=False)
    if destination.exists():
        raise CleanupError("A new destination already exists; it will not be overwritten")
    if os.name == "nt":
        os.rename(temporary, destination)  # Windows rename refuses an existing destination.
    else:
        os.link(temporary, destination)  # Exclusive publication, including on POSIX.
        temporary.unlink()


def save_temporary_residue(manifest, created):
    records = {item["path"]: item for item in manifest.get("temporary_residue", [])}
    for path in created:
        if path.exists() and not reparse(path) and path.is_file() and path.stat().st_nlink == 1:
            records[str(path)] = {"path": str(path), "sha256": digest(path)}
    manifest["temporary_residue"] = list(records.values())


def temporary_intent(archive, manifest, parent):
    path = absolute(Path(parent) / (".session-cleanup-" + archive.name + "-" + uuid.uuid4().hex + ".tmp"), existing=False)
    if path.exists():
        raise CleanupError("Maintenance temporary already exists")
    manifest.setdefault("temporary_intents", []).append(str(path))
    json_write(archive / "manifest.json", manifest)
    return path


def clean_temporary_residue(archive, manifest):
    records = {x["path"]: x for x in manifest.get("temporary_residue", [])}
    allowed_parents = {archive} | {Path(x["path"]).parent for x in manifest["operations"]}
    def checked_temporary(value):
        path = absolute(value, existing=False)
        if path.parent not in allowed_parents or not re.fullmatch(
                r"\.session-cleanup-" + re.escape(archive.name) + r"-[0-9a-f]{32}\.tmp", path.name):
            raise CleanupError("Unrecognized maintenance temporary path")
        return path
    for value in manifest.get("temporary_intents", []):
        path = checked_temporary(value)
        if path.exists() and str(path) not in records:
            # Exact unique intent was durably recorded before exclusive creation.
            if not path.is_file() or path.stat().st_nlink != 1:
                raise CleanupError("Invalid interrupted maintenance temporary")
            records[str(path)] = {"path": str(path), "sha256": digest(path)}
    manifest["temporary_residue"] = list(records.values())
    json_write(archive / "manifest.json", manifest)
    for item in manifest["temporary_residue"]:
        path = checked_temporary(item["path"])
        if not path.exists():
            continue
        verify_file(path, path.parent, item["sha256"])
        path.unlink()
    owner = manifest.get("manifest_id", "")
    if not re.fullmatch(r"[0-9a-f]{32}", owner):
        raise CleanupError("Invalid maintenance manifest identity")
    for path in archive.glob(".manifest.json." + owner + ".*.next"):
        if not re.fullmatch(r"\.manifest\.json\." + owner + r"\.[0-9a-f]{32}\.next", path.name):
            raise CleanupError("Unrecognized journal temporary file")
        absolute(path)
        if not path.is_file() or path.stat().st_nlink != 1:
            raise CleanupError("Invalid journal temporary file")
        path.unlink()


def process_guard():
    if os.name != "nt":
        raise CleanupError("Real maintenance is supported only on Windows")
    # Return no command lines: they can contain private prompt content.
    script = r"""
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
$items = @(Get-CimInstance Win32_Process | Where-Object {
  $_.Name -match '^(codex|claude)([.-].*)?\.exe$' -or
  ($_.Name -match '^(node|python|pythonw|pwsh|powershell)\.exe$' -and
   $_.CommandLine -match '(agentbus\.py|worker_daemon\.py|agent_bus[/\\].*\.py|codex[_-](bridge|messenger)|claude[/\\].*(cli|index)\.[cm]?js|@anthropic-ai[/\\]claude-code|@openai[/\\]codex)')
} | Select-Object ProcessId,Name)
ConvertTo-Json -InputObject $items -Compress
"""
    result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
                            capture_output=True, encoding="utf-8", errors="strict", timeout=30)
    if result.returncode:
        raise CleanupError("Unable to verify that all clients and messengers are stopped")
    try:
        active = json.loads(result.stdout.lstrip("\ufeff"))
    except ValueError as exc:
        raise CleanupError("Unrecognized process inventory") from exc
    if active:
        raise CleanupError("Close Codex, Claude Code and agent messengers before maintenance; active processes: " +
                           json.dumps(active, ensure_ascii=False))


@contextlib.contextmanager
def file_locks(paths):
    handles = []
    try:
        if os.name == "nt":
            import ctypes.wintypes as wt
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            create = kernel.CreateFileW
            create.argtypes = [wt.LPCWSTR, wt.DWORD, wt.DWORD, wt.LPVOID, wt.DWORD, wt.DWORD, wt.HANDLE]
            create.restype = wt.HANDLE
            kernel.CloseHandle.argtypes = [wt.HANDLE]
            for path in paths:
                # Deny write access, allow read + rename into quarantine while held.
                handle = create(str(path), 0x80000000, 0x1 | 0x4, None, 3, 0x80, None)
                if handle == ctypes.c_void_p(-1).value:
                    raise CleanupError("File is locked or unavailable: " + str(path))
                handles.append((kernel, handle))
        else:
            import fcntl
            for path in paths:
                handle = open(path, "rb")
                try:
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except OSError:
                    handle.close()
                    raise CleanupError("File is locked: " + str(path))
                handles.append((None, handle))
        yield
    finally:
        for kernel, handle in reversed(handles):
            kernel.CloseHandle(handle) if kernel else handle.close()


def check_plan(plan):
    if plan.get("purpose") != PURPOSE or plan.get("scope") != "AllSessions":
        raise CleanupError("Invalid cleanup manifest")
    fresh = make_plan(plan["project_root"], plan["codex_homes"], plan["claude_homes"], plan["claude_projects_roots"])
    if fresh != plan:
        raise CleanupError("Plan is stale or edited; create and review a fresh plan")


def stage_database(db_info, plan, work):
    original = Path(db_info["path"])
    target = work / original.name
    for item in plan["files"]:
        if item.get("group") == str(original):
            shutil.copyfile(item["path"], work / Path(item["path"]).name)
    with contextlib.closing(sqlite3.connect(target, timeout=0)) as db:
        if db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise CleanupError("Database integrity check failed")
        db.execute("PRAGMA foreign_keys=OFF")
        db.execute("PRAGMA secure_delete=ON")
        db.execute("BEGIN EXCLUSIVE")
        for table in sorted(DB_RULES[original.name][0].intersection(db_info["tables"])):
            if original.name == "logs_2.sqlite":
                db.execute('DELETE FROM "logs" WHERE thread_id IS NOT NULL')
            else:
                db.execute('DELETE FROM "' + table + '"')
        db.commit()
        if db.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise CleanupError("Database foreign-key integrity check failed")
        db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        db.execute("PRAGMA journal_mode=DELETE")
        db.execute("VACUUM")
    return target


def quarantine_root(project):
    path = absolute(root_path(project) / "maintenance" / "session-quarantine", existing=False)
    return path


def load_archive(archive):
    archive = absolute(archive)
    manifest_path = absolute(archive / "manifest.json")
    if not manifest_path.is_file() or manifest_path.stat().st_nlink != 1:
        raise CleanupError("Unsafe manifest file type/link count")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("purpose") != PURPOSE:
        raise CleanupError("Not a session cleanup archive")
    root = quarantine_root(manifest["plan"]["project_root"])
    if archive.parent != root or not UUID.fullmatch(archive.name):
        raise CleanupError("Archive is outside the exact project quarantine boundary")
    planned = {item["path"]: item for item in manifest["plan"]["files"]}
    allowed_roots = set(manifest["plan"]["codex_homes"] + manifest["plan"]["claude_homes"] +
                        manifest["plan"]["claude_projects_roots"])
    allowed_roots.update(str(Path(home) / "projects") for home in manifest["plan"]["claude_homes"])
    operations = manifest["operations"]
    if (len(planned) != len(manifest["plan"]["files"]) or
        {x["path"] for x in operations} != set(planned) or
        len(operations) != len(planned) or
        len({x["backup"] for x in operations}) != len(operations)):
        raise CleanupError("Archive operations do not match its plan")
    for item in manifest["operations"]:
        if not re.fullmatch(r"[0-9]{6}\.bin", item["backup"]):
            raise CleanupError("Invalid backup locator")
        source = absolute(item["path"], existing=False)
        source_root = root_path(item["root"])
        if str(source_root) not in allowed_roots or not within(source, source_root):
            raise CleanupError("Restore path escapes source root")
        if any(part.lower() in PROTECTED for part in source.relative_to(source_root).parts):
            raise CleanupError("Restore targets a protected path")
        if {k: v for k, v in item.items() if k not in {"backup", "replacement_sha256"}} != planned.get(item["path"]):
            raise CleanupError("Archive operation differs from its plan")
    return archive, manifest


def apply_plan(plan, *, allow_supported_subset=False, guard=process_guard):
    guard()
    check_plan(plan)
    if plan["uncovered"] and not allow_supported_subset:
        raise CleanupError("Uncovered items exist; supply missing physical roots or explicitly choose --allow-supported-subset")
    root = quarantine_root(plan["project_root"])
    root.mkdir(parents=True, exist_ok=True)
    archive = root / str(uuid.uuid4())
    archive.mkdir()
    manifest = {"purpose": PURPOSE, "manifest_id": uuid.uuid4().hex, "status": "preparing", "partial": bool(plan["uncovered"]),
                "plan": plan, "operations": []}
    # Staging is inside the project and never exposes content in output.
    with tempfile.TemporaryDirectory(prefix=".staging-", dir=archive) as stage_dir:
        stage = Path(stage_dir)
        with file_locks([Path(item["path"]) for item in plan["files"]]):
            # SQLite read-only connections may require a writable SHM handle;
            # schema was checked immediately before acquiring deny-write locks.
            for item in plan["files"]:
                if file_record(Path(item["path"]), Path(item["root"]), item["kind"], item.get("group")) != item:
                    raise CleanupError("A source changed while acquiring locks")
            replacements = {}
            for idx, db in enumerate(plan["databases"]):
                work = stage / ("db-" + str(idx))
                work.mkdir()
                replacements[db["path"]] = stage_database(db, plan, work)
            for idx, item in enumerate(plan["files"]):
                if item["kind"] == "global_state":
                    value = json.loads(Path(item["path"]).read_text(encoding="utf-8"))
                    for key in GLOBAL_SESSION_KEYS:
                        value.pop(key, None)
                    target = stage / ("json-" + str(idx))
                    json_write(target, value)
                    replacements[item["path"]] = target
                replacement = replacements.get(item["path"])
                manifest["operations"].append({**item, "backup": f"{idx:06d}.bin",
                    "replacement_sha256": digest(replacement) if replacement else None})
            json_write(archive / "manifest.json", manifest, exclusive=True)
            guard()
            # Metadata and full hashes must still match immediately before the first move.
            for item in plan["files"]:
                if file_record(Path(item["path"]), Path(item["root"]), item["kind"], item.get("group")) != item:
                    raise CleanupError("A source changed after staging")
            created = []
            try:
                manifest["status"] = "applying"
                json_write(archive / "manifest.json", manifest)
                for item in manifest["operations"]:
                    source = verify_file(item["path"], item["root"], item["sha256"])
                    backup = archive / item["backup"]
                    temporary = temporary_intent(archive, manifest, archive)
                    prepare_copy(source, temporary, item["sha256"], created)
                    publish_new(temporary, backup, item["sha256"])
                    replacement_temp = None
                    if item["path"] in replacements:
                        replacement_temp = temporary_intent(archive, manifest, source.parent)
                        prepare_copy(replacements[item["path"]], replacement_temp, item["replacement_sha256"], created)
                    # Deny-write handles allow rename: verify the path again just
                    # before unlink so an offline replacement is never discarded.
                    verify_file(backup, archive, item["sha256"])
                    source = verify_file(source, item["root"], item["sha256"])
                    source.unlink()
                    if replacement_temp:
                        publish_new(replacement_temp, source, item["replacement_sha256"])
                manifest["status"] = "applied"
                json_write(archive / "manifest.json", manifest)
            except Exception as exc:
                manifest["status"] = "interrupted"
                save_temporary_residue(manifest, created)
                json_write(archive / "manifest.json", manifest)
                # Keep the original copies and explicit recovery manifest on any failure.
                raise CleanupError("Maintenance interrupted; recover from " + str(archive)) from exc
    return archive


def check_restore_item(archive, manifest, item):
    verify_file(archive / item["backup"], archive, item["sha256"])
    source = absolute(item["path"], existing=False)
    root = root_path(item["root"])
    if not within(source, root):
        raise CleanupError("Restore path escaped its root")
    current = None
    if source.exists():
        if not source.is_file() or source.stat().st_nlink != 1:
            raise CleanupError("Unsafe restore destination type/link count")
        current = digest(source)
        if current not in {item["sha256"], item["replacement_sha256"]}:
            raise CleanupError("Current source has changed; restore will not overwrite it")
    group = item.get("group")
    if group:
        family = {x["path"] for x in manifest["operations"] if x.get("group") == group}
        for suffix in ("-wal", "-shm", "-journal"):
            extra = absolute(group + suffix, existing=False)
            if extra.exists() and str(extra) not in family:
                raise CleanupError("New database sidecar exists; restore is unsafe")
    return source, current


def restore_archive(archive, *, guard=process_guard):
    guard()
    archive, manifest = load_archive(archive)
    if manifest["status"] not in {"applied", "interrupted", "applying"}:
        raise CleanupError("Archive is not restorable")
    operations = [x for x in manifest["operations"] if (archive / x["backup"]).exists()]
    if manifest["status"] == "applied" and len(operations) != len(manifest["operations"]):
        raise CleanupError("A completed archive is missing a backup")
    def verify_unbacked_originals():
        for item in manifest["operations"]:
            if not (archive / item["backup"]).exists():
                try:
                    verify_file(item["path"], item["root"], item["sha256"])
                except (CleanupError, OSError) as exc:
                    raise CleanupError("Missing backup has no verified original; recovery is incomplete") from exc
    verify_unbacked_originals()
    for item in operations:
        check_restore_item(archive, manifest, item)
    created = []
    try:
        with file_locks([Path(x["path"]) for x in manifest["operations"] if Path(x["path"]).exists()]):
            guard()
            verify_unbacked_originals()
            # Re-check every path after taking locks and before any change.
            for item in operations:
                check_restore_item(archive, manifest, item)
            for item in operations:
                source, current = check_restore_item(archive, manifest, item)
                if current == item["sha256"]:
                    continue
                source.parent.mkdir(parents=True, exist_ok=True)
                temporary = temporary_intent(archive, manifest, source.parent)
                prepare_copy(archive / item["backup"], temporary, item["sha256"], created)
                source, current = check_restore_item(archive, manifest, item)
                if current == item["sha256"]:
                    save_temporary_residue(manifest, created)
                    continue
                if source.exists():
                    verify_file(source, item["root"], item["replacement_sha256"])
                    source.unlink()
                publish_new(temporary, source, item["sha256"])
                verify_file(source, item["root"], item["sha256"])
            verify_unbacked_originals()
        clean_temporary_residue(archive, manifest)
        manifest["status"] = "restored"
        manifest["restored_backups"] = [item["backup"] for item in operations]
        json_write(archive / "manifest.json", manifest)
    except Exception:
        manifest["status"] = "interrupted"
        save_temporary_residue(manifest, created)
        json_write(archive / "manifest.json", manifest)
        raise


def purge_archive(archive, *, confirmation, guard=process_guard):
    guard()
    archive, manifest = load_archive(archive)
    if confirmation != archive.name or manifest["status"] not in {"applied", "restored", "purging"}:
        raise CleanupError("Purge requires the exact archive UUID and a completed state")
    operations = manifest["operations"]
    original_status = manifest.get("purge_original_status", manifest["status"])
    if original_status == "restored":
        restored = set(manifest.get("restored_backups", []))
        if not restored.issubset({x["backup"] for x in operations}):
            raise CleanupError("Invalid restored backup inventory")
        operations = [x for x in operations if x["backup"] in restored]
    clean_temporary_residue(archive, manifest)
    deleted = set(manifest.get("purge_deleted", []))
    deleting = manifest.get("purge_deleting")
    names = {x["backup"] for x in operations}
    if not deleted.issubset(names) or (deleting and deleting not in names):
        raise CleanupError("Invalid purge journal")
    if deleting and not (archive / deleting).exists():
        deleted.add(deleting)
    expected = {"manifest.json"} | (names - deleted)
    children = list(archive.iterdir())
    if {x.name for x in children} != expected or any(reparse(x) or not x.is_file() for x in children):
        raise CleanupError("Unexpected archive contents; purge refused")
    for item in operations:
        if item["backup"] not in deleted:
            verify_file(archive / item["backup"], archive, item["sha256"])
    manifest.update(status="purging", purge_original_status=original_status,
                    purge_deleted=sorted(deleted), purge_deleting=None)
    json_write(archive / "manifest.json", manifest)
    for item in operations:
        if item["backup"] in deleted:
            continue
        manifest["purge_deleting"] = item["backup"]
        json_write(archive / "manifest.json", manifest)
        backup = verify_file(archive / item["backup"], archive, item["sha256"])
        backup.unlink()
        deleted.add(item["backup"])
        manifest.update(purge_deleted=sorted(deleted), purge_deleting=None)
        json_write(archive / "manifest.json", manifest)
    if {x.name for x in archive.iterdir()} != {"manifest.json"}:
        raise CleanupError("Unexpected files appeared during purge")
    # The recovery journal is the last file removed.
    (archive / "manifest.json").unlink()
    archive.rmdir()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    plan = sub.add_parser("plan", help="read-only inventory; no session mutation")
    plan.add_argument("--scope", choices=["AllSessions"], required=True)
    plan.add_argument("--project-root", required=True)
    plan.add_argument("--codex-home", action="append", default=[])
    plan.add_argument("--claude-home", action="append", default=[])
    plan.add_argument("--claude-projects-root", "--claude-project-root", action="append", default=[])
    plan.add_argument("--output", help="new plan file; otherwise print plan to stdout")
    apply = sub.add_parser("apply")
    apply.add_argument("--plan", required=True)
    apply.add_argument("--scope", choices=["AllSessions"], required=True)
    apply.add_argument("--clients-stopped", action="store_true", required=True)
    apply.add_argument("--allow-supported-subset", action="store_true")
    restore = sub.add_parser("restore")
    restore.add_argument("--archive", required=True)
    restore.add_argument("--clients-stopped", action="store_true", required=True)
    purge = sub.add_parser("purge")
    purge.add_argument("--archive", required=True)
    purge.add_argument("--confirm-archive-id", required=True)
    purge.add_argument("--clients-stopped", action="store_true", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "plan":
            result = make_plan(args.project_root, args.codex_home, args.claude_home, args.claude_projects_root)
            if args.output:
                output = absolute(args.output, existing=False)
                if not within(output, root_path(args.project_root)):
                    raise CleanupError("Save the plan inside the project")
                json_write(output, result, exclusive=True)
                print(json.dumps({"plan": str(output), "files": len(result["files"]),
                                  "uncovered": result["uncovered"], "limitations": LIMITATIONS}, ensure_ascii=False))
            else:
                print(json.dumps(result, ensure_ascii=False, indent=2))
        elif args.command == "apply":
            path = absolute(args.plan)
            value = json.loads(path.read_text(encoding="utf-8"))
            if not within(path, root_path(value["project_root"])):
                raise CleanupError("Plan must be inside its project")
            archive = apply_plan(value, allow_supported_subset=args.allow_supported_subset)
            print(json.dumps({"archive": str(archive), "partial": bool(value["uncovered"]), "limitations": LIMITATIONS}, ensure_ascii=False))
        elif args.command == "restore":
            restore_archive(args.archive)
            print(json.dumps({"restored": str(args.archive)}, ensure_ascii=False))
        else:
            purge_archive(args.archive, confirmation=args.confirm_archive_id)
            print(json.dumps({"purged": str(args.archive)}, ensure_ascii=False))
        return 0
    except (CleanupError, OSError, ValueError, KeyError, sqlite3.Error) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
