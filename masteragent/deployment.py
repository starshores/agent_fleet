"""Machine-local role bindings; portable business packages contain no paths.

Access is an authorization ceiling for future file adapters, not a grant of
runtime file tools or an operating-system sandbox. Bindings are immutable for
the lifetime of an installation.
"""
from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from .errors import PlatformError
from .paths import PathSafetyError, _overlap, validate_workspace


WORKSPACE_ACCESS = frozenset({"none", "read-only", "read-write"})


def normalize_deployments(role_ids: set[str], workspace: str | Path,
                          overrides: Mapping | None, state_root: Path,
                          existing_workspaces: list[Path]) -> tuple[Path, dict]:
    """Resolve and validate all bindings before any installation is written."""
    if overrides is None:
        overrides = {}
    if not isinstance(overrides, Mapping) or set(overrides) - role_ids:
        raise PlatformError("invalid_role_deployments", "Deployment bindings must name only roles in this package.")
    for binding in overrides.values():
        if (not isinstance(binding, Mapping) or set(binding) != {"workspace", "access"}
                or not isinstance(binding["access"], str) or binding["access"] not in WORKSPACE_ACCESS):
            raise PlatformError("invalid_role_deployments", "Each role binding requires exactly workspace and a supported access ceiling.")
    default = validate_workspace(workspace, state_root, existing_workspaces)
    result = {}
    selected: list[Path] = []
    for role_id in sorted(role_ids):
        binding = overrides.get(role_id, {"workspace": default, "access": "none"})
        path = validate_workspace(binding["workspace"], state_root, existing_workspaces)
        # Equal roots are deliberately shared by the supplied role mapping or
        # the compatible default. Nested role roots would silently grant a
        # parent role access to the other role's workspace.
        if any(path != other and _overlap(path, other) for other in selected):
            raise PathSafetyError("workspace_overlap", "Role workspaces may be explicitly shared or disjoint, but not nested.")
        selected.append(path)
        result[role_id] = {"workspace": str(path), "access": binding["access"]}
    return default, result
