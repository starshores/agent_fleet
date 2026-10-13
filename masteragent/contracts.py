"""Portable business-package contract, independent of host and runtime state.

Only the declared fields are accepted. Host paths, credentials, runtime session
identities, and mutable operational state belong in separate deployment/state
stores, never additional package fields. Free-form business prose is preserved;
schema validation is not a credential scanner.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
from typing import Any


class ContractError(ValueError):
    """A package cannot be used; ``code`` is stable for callers and CLI output."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


def _readonly(*args: Any, **kwargs: Any) -> None:
    raise TypeError("Business packages are immutable; parse a new manifest instead.")


class _FrozenDict(dict):
    __setitem__ = __delitem__ = clear = pop = popitem = setdefault = update = _readonly
    __ior__ = _readonly

    def __deepcopy__(self, memo: dict) -> _FrozenDict:
        return self


class _FrozenList(list):
    __setitem__ = __delitem__ = append = clear = extend = insert = pop = remove = _readonly
    reverse = sort = __iadd__ = __imul__ = _readonly

    def __deepcopy__(self, memo: dict) -> _FrozenList:
        return self


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return _FrozenDict((key, _freeze(item)) for key, item in value.items())
    if isinstance(value, (list, tuple)):
        return _FrozenList(_freeze(item) for item in value)
    return value


def _plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value


@dataclass(frozen=True)
class BusinessPackage:
    package_id: str
    version: str
    roles: tuple[dict, ...]
    knowledge: dict[str, str]
    tools: dict[str, dict]
    workflows: dict[str, dict]
    manifest: dict

    @property
    def digest(self) -> str:
        canonical = json.dumps(
            self.manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        return hashlib.sha256(canonical).hexdigest()

    def get_role(self, role_id: str) -> dict:
        for role in self.roles:
            if role["role_id"] == role_id:
                return _plain(role)
        raise ContractError("role_not_found", f"Role {role_id!r} is not declared in package {self.package_id!r}.")


_IDENTIFIER = re.compile(r"[a-z][a-z0-9_-]{0,63}\Z", re.ASCII)
_SEMVER = re.compile(
    r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
    r"(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
    r"(?:\+([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?\Z",
    re.ASCII,
)
_WINDOWS_RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}
_MANIFEST_FIELDS = {"schema_version", "platform_api", "package_id", "version", "roles", "knowledge", "tools", "workflows"}


def _object(value: Any, location: str, fields: set[str] | None = None) -> Mapping:
    if not isinstance(value, Mapping):
        raise ContractError("invalid_type", f"{location} must be a JSON object.")
    if any(not isinstance(key, str) for key in value):
        raise ContractError("invalid_type", f"{location} keys must be strings.")
    if fields is not None:
        unknown = set(value) - fields
        if unknown:
            raise ContractError("unknown_field", f"{location} has unsupported fields: {', '.join(sorted(unknown))}. Keep host configuration and runtime state outside the package.")
        missing = fields - set(value)
        if missing:
            raise ContractError("missing_field", f"{location} is missing required fields: {', '.join(sorted(missing))}.")
    return value


def _array(value: Any, location: str, *, nonempty: bool = False) -> list:
    if not isinstance(value, list):
        raise ContractError("invalid_type", f"{location} must be a JSON array.")
    if nonempty and not value:
        raise ContractError("empty_collection", f"{location} must contain at least one entry.")
    return value


def _text(value: Any, location: str, *, nonempty: bool = True) -> str:
    if not isinstance(value, str):
        raise ContractError("invalid_type", f"{location} must be a string.")
    if nonempty and not value.strip():
        raise ContractError("empty_text", f"{location} must not be blank.")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ContractError("invalid_text", f"{location} must contain valid Unicode text.") from exc
    return value


def _identifier(value: Any, location: str) -> str:
    _text(value, location)
    if not _IDENTIFIER.fullmatch(value) or value.casefold() in _WINDOWS_RESERVED:
        raise ContractError("invalid_identifier", f"{location} must be 1-64 lowercase ASCII characters, begin with a letter, use only letters, digits, '-' or '_', and avoid Windows reserved names.")
    return value


def _unique(values: list, location: str, code: str) -> None:
    seen: set[str] = set()
    for index, value in enumerate(values):
        _text(value, f"{location}[{index}]")
        identity = value.casefold()
        if identity in seen:
            raise ContractError(code, f"{location} repeats identifier {value!r}, including case-insensitive aliases.")
        seen.add(identity)


def _named_map(value: Any, location: str) -> Mapping:
    result = _object(value, location)
    _unique(list(result), location, "identifier_collision")
    for key in result:
        _identifier(key, f"{location} key")
    return result


def _references(value: Any, available: set[str], location: str, *, nonempty: bool = False) -> None:
    refs = _array(value, location, nonempty=nonempty)
    _unique(refs, location, "duplicate_reference")
    for item in refs:
        _identifier(item, location)
        if item not in available:
            raise ContractError("invalid_reference", f"{location} references undeclared identifier {item!r}; declare it in this package first.")


def parse_package(mapping: Mapping) -> BusinessPackage:
    """Validate one complete manifest; never merge defaults or a previous domain."""
    manifest = _object(mapping, "manifest", _MANIFEST_FIELDS)
    for field, code in (("schema_version", "unsupported_schema"), ("platform_api", "unsupported_platform_api")):
        if type(manifest[field]) is not int or manifest[field] != 1:
            raise ContractError(code, f"{field} must be integer 1; use a package compatible with this platform.")
    package_id = _identifier(manifest["package_id"], "package_id")
    version = _text(manifest["version"], "version")
    match = _SEMVER.fullmatch(version)
    if not match or len(version) > 128 or (match.group(4) and any(part.isdigit() and len(part) > 1 and part.startswith("0") for part in match.group(4).split("."))):
        raise ContractError("invalid_version", "version must be Semantic Versioning text such as '1.0.0' or '1.0.0-rc.1'.")

    tools = _named_map(manifest["tools"], "tools")
    for tool_id, tool in tools.items():
        location = f"tools.{tool_id}"
        _object(tool, location, {"adapter", "description"})
        # A business package names an extension; deployment decides whether the
        # implementation is available. New tools must not require core edits.
        _identifier(tool["adapter"], f"{location}.adapter")
        _text(tool["description"], f"{location}.description", nonempty=False)

    roles = _array(manifest["roles"], "roles", nonempty=True)
    for index, role in enumerate(roles):
        _object(role, f"roles[{index}]", {"role_id", "display_name", "instructions", "tools"})
    _unique([role["role_id"] for role in roles], "roles.role_id", "identifier_collision")
    for index, role in enumerate(roles):
        location = f"roles[{index}]"
        _identifier(role["role_id"], f"{location}.role_id")
        _text(role["display_name"], f"{location}.display_name")
        _text(role["instructions"], f"{location}.instructions")
        _references(role["tools"], set(tools), f"{location}.tools")

    knowledge = _named_map(manifest["knowledge"], "knowledge")
    for key, content in knowledge.items():
        _text(content, f"knowledge.{key}")

    workflows = _named_map(manifest["workflows"], "workflows")
    for flow_id, flow in workflows.items():
        location = f"workflows.{flow_id}"
        _object(flow, location, {"roles", "description"})
        _references(flow["roles"], {role["role_id"] for role in roles}, f"{location}.roles", nonempty=True)
        _text(flow["description"], f"{location}.description", nonempty=False)

    frozen = _freeze(manifest)
    return BusinessPackage(package_id, version, tuple(frozen["roles"]), frozen["knowledge"], frozen["tools"], frozen["workflows"], frozen)


def _json_object(pairs: list[tuple[str, Any]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ContractError("duplicate_key", f"JSON contains duplicate key {key!r}; remove the ambiguity instead of relying on last-value wins.")
        result[key] = value
    return result


def _json_constant(value: str) -> None:
    raise ContractError("invalid_json", f"JSON constant {value} is not permitted; use standard JSON values.")


def load_package(path: str | Path) -> BusinessPackage:
    """Read an explicitly selected UTF-8 package; a missing file is an error."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except (OSError, ValueError, TypeError, UnicodeError) as exc:
        raise ContractError("package_read_error", f"Cannot read the explicitly selected package {str(path)!r}: {exc}. Check its path and UTF-8 encoding.") from exc
    try:
        manifest = json.loads(text, object_pairs_hook=_json_object, parse_constant=_json_constant)
    except (json.JSONDecodeError, RecursionError) as exc:
        raise ContractError("invalid_json", f"Package {str(path)!r} is not valid JSON: {exc}.") from exc
    return parse_package(manifest)
