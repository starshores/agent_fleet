"""Explicit filesystem boundaries for the isolated v3 deployment.

These checks protect the paths this platform opens. They are not an OS sandbox
for arbitrary programs, nor protection against a hostile local administrator.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path


SOURCE_ROOT = Path(__file__).resolve().parent.parent
_STATE_FILES = ("state.sqlite3", "state.sqlite3-wal", "state.sqlite3-shm", "state.sqlite3-journal")


class PathSafetyError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _overlap(left: Path, right: Path) -> bool:
    return left == right or left in right.parents or right in left.parents


def _explicit_path(value: str | Path) -> Path:
    if not isinstance(value, (str, Path)) or not str(value).strip():
        raise PathSafetyError("invalid_path", "An explicit absolute directory is required.")
    path = Path(value)
    if not path.is_absolute():
        raise PathSafetyError("invalid_path", "Relative directories are not accepted; supply an absolute path.")
    if path.drive.startswith("\\\\"):
        raise PathSafetyError("network_path", "This local deployment slice does not accept network or device paths.")
    # Validate before resolve(), so aliases cannot conceal a protected target.
    path = Path(os.path.abspath(path))
    for part in (path, *path.parents):
        try:
            info = part.lstat()
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise PathSafetyError("inaccessible_path", "Cannot inspect deployment directory.") from exc
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise PathSafetyError("linked_path", "Deployment directories cannot traverse symlinks or junctions.")
    return path.resolve()


def _check_directory_boundary(path: Path) -> None:
    home = Path.home().resolve()
    if path == Path(path.anchor) or path == home:
        raise PathSafetyError("unsafe_root", "Use a dedicated deployment directory, not a drive or home root.")
    protected = (SOURCE_ROOT, home / ".claude", home / ".codex")
    if any(_overlap(path, boundary) for boundary in protected):
        raise PathSafetyError("protected_path", "Deployment state and workspaces must be separate from source and runtime homes.")
    # Recognize a legacy checkout structurally, without reading its configuration
    # or hardcoding the current user's repository path or business name.
    for parent in (path, *path.parents):
        if (parent / "agent_bus" / "agentbus.py").is_file():
            raise PathSafetyError("legacy_root", "Use a directory outside the legacy bus checkout.")
    if path.exists() and not path.is_dir():
        raise PathSafetyError("invalid_directory", "The deployment path is not a directory.")


def prepare_state_root(value: str | Path, create: bool = True) -> Path:
    """Validate an isolated state root; observation mode never creates files."""
    path = _explicit_path(value)
    _check_directory_boundary(path)
    if path.exists():
        if not (path / "state.sqlite3").exists() and any(path.iterdir()):
            raise PathSafetyError("unmanaged_state", "A new state directory must be empty.")
        for name in _STATE_FILES:
            candidate = path / name
            try:
                info = candidate.lstat()
            except FileNotFoundError:
                continue
            if (stat.S_ISLNK(info.st_mode)
                    or getattr(info, "st_file_attributes", 0) & 0x400
                    or info.st_nlink > 1):
                raise PathSafetyError("linked_state", "State database files cannot be aliases or hard links.")
            if not stat.S_ISREG(info.st_mode):
                raise PathSafetyError("invalid_state", "State database paths must be regular files.")
    if create:
        path.mkdir(parents=True, exist_ok=True)
    return path


def validate_workspace(
    value: str | Path,
    state_root: str | Path,
    existing_workspaces: list[Path] | None = None,
) -> Path:
    """Resolve a private existing workspace, rejecting implicit shared access."""
    path = _explicit_path(value)
    _check_directory_boundary(path)
    if not path.is_dir():
        raise PathSafetyError("missing_workspace", "Create the intended workspace directory before installing.")
    state = _explicit_path(state_root)
    if _overlap(path, state):
        raise PathSafetyError("workspace_overlap", "Business workspace and platform state must be separate.")
    for existing in existing_workspaces or []:
        if _overlap(path, _explicit_path(existing)):
            raise PathSafetyError("workspace_overlap", "Business installations require separate workspaces.")
    return path
