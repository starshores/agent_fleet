"""Explicit host deployment settings for the Codex text runtime.

Business packages never provide these settings. The path checks protect files
this module opens; the CLI flags are defense in depth, not an OS sandbox.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass
import json
import math
import os
from pathlib import Path
import re
import stat

from .errors import PlatformError
from .paths import _check_directory_boundary, _explicit_path, _overlap


_FIELDS = {
    "schema_version", "runtime", "executable", "expected_version", "model",
    "adapter_id", "installation_id", "role_id", "generation", "codex_home",
    "timeout_seconds", "lease_seconds",
}
_GENERIC_ID = re.compile(r"[a-z][a-z0-9_-]{0,63}\Z", re.ASCII)
_INSTALLATION_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\Z", re.ASCII)
_MARKER = ".masteragent-runtime.json"
_MARKER_FIELDS = {"schema_version", "state_root", "installation_id", "role_id", "generation"}
_DISABLED_FEATURES = (
    "shell_tool", "unified_exec", "apps", "plugins", "hooks", "memories",
    "browser_use", "browser_use_external", "computer_use", "image_generation",
    "view_image", "multi_agent", "skill_search", "skill_mcp_dependency_install",
    "shell_snapshot", "daemon_auto_start", "remote_plugin",
)
_ENVIRONMENT_NAMES = {
    name.casefold(): name for name in (
        "SystemRoot", "WINDIR", "ComSpec", "PATH", "PATHEXT", "TEMP", "TMP",
        "HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "LANG",
    )
}


def _fail(code: str, message: str) -> None:
    raise PlatformError(code, message)


def _text(value: object, field: str) -> str:
    if (not isinstance(value, str) or not value.strip()
            or value != value.strip() or any(ord(char) < 32 for char in value)):
        _fail("invalid_runtime_config", f"{field} must be explicit nonempty text without control characters.")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        _fail("invalid_runtime_config", f"{field} must contain valid Unicode text.")
    return value


def _positive_integer(value: object, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        _fail("invalid_runtime_config", f"{field} must be a positive integer.")
    return value


def _duration(value: object, field: str, minimum: float, maximum: float) -> float:
    if (not isinstance(value, (int, float)) or isinstance(value, bool)
            or not minimum <= value <= maximum or not math.isfinite(value)):
        _fail("invalid_runtime_config", f"{field} must be finite and between {minimum:g} and {maximum:g} seconds.")
    return float(value)


def _regular_file(path: Path, *, missing_ok: bool = False) -> os.stat_result | None:
    try:
        info = path.lstat()
    except FileNotFoundError:
        if missing_ok:
            return None
        _fail("missing_runtime_file", "A required runtime file does not exist.")
    except OSError:
        _fail("invalid_runtime_file", "Cannot inspect a runtime file.")
    if (stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400
            or info.st_nlink > 1 or not stat.S_ISREG(info.st_mode)):
        _fail("linked_runtime_file", "Runtime files must be regular files without links or aliases.")
    return info


def _executable(value: str | Path) -> Path:
    if not isinstance(value, (str, Path)):
        _fail("invalid_runtime_config", "executable must be an explicit absolute path.")
    path = _explicit_path(value)
    _regular_file(path)
    if path.suffix.casefold() in {".cmd", ".bat", ".ps1", ".py", ".sh"}:
        _fail("invalid_runtime_executable", "Use a native executable, not a shell or script wrapper.")
    if os.name == "nt" and path.suffix.casefold() != ".exe":
        _fail("invalid_runtime_executable", "The Windows runtime requires an explicit native .exe path.")
    if os.name != "nt" and not os.access(path, os.X_OK):
        _fail("invalid_runtime_executable", "The runtime executable is not executable.")
    return path


def _unique_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            _fail("invalid_runtime_config", "Runtime JSON must not repeat fields.")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    _fail("invalid_runtime_config", "Runtime JSON must not contain nonfinite numbers.")


def _parse_json(text: str) -> dict:
    try:
        value = json.loads(text, object_pairs_hook=_unique_object, parse_constant=_reject_constant)
    except (ValueError, RecursionError) as exc:
        if isinstance(exc, PlatformError):
            raise
        _fail("invalid_runtime_config", "Runtime settings must contain valid JSON.")
    if not isinstance(value, dict):
        _fail("invalid_runtime_config", "Runtime settings must be a JSON object.")
    return value


@dataclass(frozen=True)
class RuntimeConfig:
    schema_version: int
    runtime: str
    executable: Path
    expected_version: str
    model: str
    adapter_id: str
    installation_id: str
    role_id: str
    generation: int
    codex_home: Path
    timeout_seconds: float
    lease_seconds: float

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != 1:
            _fail("invalid_runtime_config", "Only runtime configuration schema 1 is supported.")
        if self.runtime not in {"codex-exec", "codex-app-server"}:
            _fail("invalid_runtime_config", "Select codex-exec or codex-app-server explicitly.")
        for field in ("expected_version", "model", "adapter_id", "installation_id", "role_id"):
            _text(getattr(self, field), field)
        for field in ("adapter_id", "role_id"):
            if not _GENERIC_ID.fullmatch(getattr(self, field)):
                _fail("invalid_runtime_config", f"{field} must be a generic lowercase identifier.")
        if not _INSTALLATION_ID.fullmatch(self.installation_id):
            _fail("invalid_runtime_config", "installation_id is not a valid deployment identifier.")
        _positive_integer(self.generation, "generation")
        object.__setattr__(self, "executable", _executable(self.executable))
        object.__setattr__(self, "codex_home", _explicit_path(self.codex_home))
        object.__setattr__(self, "timeout_seconds", _duration(self.timeout_seconds, "timeout_seconds", 1, 3600))
        object.__setattr__(self, "lease_seconds", _duration(self.lease_seconds, "lease_seconds", 5, 300))

    def validate_scope(self, installation_id: str, role_id: str, generation: int) -> None:
        if (type(generation) is not int
                or (self.installation_id, self.role_id, self.generation)
                != (installation_id, role_id, generation)):
            _fail("runtime_scope_mismatch", "Runtime settings do not match this installation, role and activation.")

    def as_public_dict(self) -> dict:
        value = asdict(self)
        value["executable"] = str(self.executable)
        value["codex_home"] = str(self.codex_home)
        return value


def load_runtime_config(path: str | Path) -> RuntimeConfig:
    try:
        with Path(path).open("r", encoding="utf-8-sig") as stream:
            text = stream.read(65537)
    except (OSError, UnicodeError, ValueError):
        _fail("invalid_runtime_config", "Cannot read the explicit runtime configuration file.")
    if len(text) > 65536:
        _fail("invalid_runtime_config", "Runtime configuration exceeds the supported size.")
    value = _parse_json(text)
    if set(value) != _FIELDS:
        _fail("invalid_runtime_config", "Runtime configuration must contain exactly the documented fields; no defaults or credentials are accepted.")
    return RuntimeConfig(**value)


def _read_marker(path: Path, expected: dict | None = None) -> dict:
    before = _regular_file(path)
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(descriptor, "r", encoding="utf-8") as stream:
            opened = os.fstat(stream.fileno())
            if ((before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino)
                    or opened.st_nlink > 1):
                _fail("linked_runtime_file", "Runtime binding changed during inspection.")
            text = stream.read(8193)
    except (OSError, UnicodeError):
        _fail("invalid_runtime_binding", "Cannot read the runtime binding marker.")
    if len(text) > 8192:
        _fail("invalid_runtime_binding", "The runtime binding marker is invalid.")
    value = _parse_json(text)
    if (set(value) != _MARKER_FIELDS or type(value.get("schema_version")) is not int
            or value.get("schema_version") != 1 or type(value.get("generation")) is not int
            or value.get("generation", 0) < 1
            or not all(isinstance(value.get(field), str) and value[field]
                       for field in ("state_root", "installation_id", "role_id"))
            or (expected is not None and value != expected)):
        _fail("runtime_home_bound", "This runtime home belongs to a different state root, installation, role or activation.")
    return value


def prepare_runtime_home(config: RuntimeConfig, state_root: str | Path,
                         workspace: str | Path, bind: bool = False) -> Path:
    """Validate a dedicated existing home; optionally create only its binding.

    A preprovisioned regular auth.json is allowed but never opened or copied.
    Other existing contents require a matching binding. Each new activation
    needs another home, so no native session crosses an activation boundary.
    """
    home = _explicit_path(config.codex_home)
    _check_directory_boundary(home)
    if not home.is_dir():
        _fail("missing_runtime_home", "Create the dedicated runtime home before configuring this deployment.")
    state = _explicit_path(state_root)
    business = _explicit_path(workspace)
    if _overlap(home, state) or _overlap(home, business):
        _fail("runtime_home_overlap", "The runtime home, platform state and business workspace must be disjoint.")
    for variable in ("CODEX_HOME", "CLAUDE_CONFIG_DIR"):
        inherited = os.environ.get(variable)
        if inherited and Path(inherited).is_absolute() and _overlap(home, Path(inherited).resolve()):
            _fail("protected_runtime_home", "The runtime home must be separate from the host's existing runtime home.")
    expected = {"schema_version": 1, "state_root": str(state),
                "installation_id": config.installation_id, "role_id": config.role_id,
                "generation": config.generation}
    _regular_file(home / "auth.json", missing_ok=True)
    if config.runtime == "codex-app-server":
        # This pinned app-server has no exec-style --ignore-user-config flag.
        # Refuse user-added configuration in its dedicated home, by metadata.
        for name in ("config.toml", "hooks.json", "AGENTS.md", ".agents", "skills"):
            if (home / name).exists() or (home / name).is_symlink():
                _fail("runtime_config_contamination", "The App Server home must not contain custom configuration or skills.")
    marker = home / _MARKER
    if _regular_file(marker, missing_ok=True) is not None:
        _read_marker(marker, expected)
        return home
    try:
        unmanaged = any(entry.name != "auth.json" for entry in home.iterdir())
    except OSError:
        _fail("invalid_runtime_home", "Cannot inspect the dedicated runtime directory.")
    if unmanaged:
        _fail("unmanaged_runtime_home", "An unbound runtime home must be empty or contain only its preprovisioned auth.json.")
    if not bind:
        return home
    try:
        descriptor = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL
                             | getattr(os, "O_NOFOLLOW", 0), 0o600)
    except FileExistsError:
        _read_marker(marker, expected)
        return home
    except OSError:
        _fail("invalid_runtime_home", "Cannot create the dedicated runtime binding.")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(expected, stream, ensure_ascii=False, sort_keys=True, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
    except OSError:
        _fail("invalid_runtime_binding", "The runtime binding could not be persisted; inspect the dedicated home before retrying.")
    _read_marker(marker, expected)
    return home


def _reject_project_discovery(path: Path) -> None:
    """Inspect metadata only, below the personal-home boundary.

    The personal home is a separate user configuration scope. It is not read
    here: custom CODEX_HOME and disabled host skill discovery handle that scope.
    CLI project_root_markers=[] independently disables parent project search.
    """
    personal_home = Path.home().resolve()
    for parent in (path, *path.parents):
        if parent == personal_home:
            break
        for name in (".codex", ".agents", ".git"):
            try:
                (parent / name).lstat()
            except FileNotFoundError:
                continue
            except OSError:
                _fail("runtime_project_boundary", "Cannot inspect the private execution directory's project boundary.")
            _fail("runtime_project_boundary", "Private execution directories cannot inherit project configuration, skills or repository roots.")


def prepare_execution_workspace(config: RuntimeConfig, attempt_id: str) -> Path:
    """Create a fresh empty cwd for one owned attempt, below its bound home.

    Never use the business workspace as process cwd in this text-only mode.
    Existing leaves are rejected rather than reused or cleaned. An interrupted
    attempt retains its directory as evidence; another attempt gets a new ID.
    """
    if (not isinstance(attempt_id, str) or not _INSTALLATION_ID.fullmatch(attempt_id)
            or attempt_id.endswith(".")
            or attempt_id.split(".", 1)[0].casefold() in {"con", "prn", "aux", "nul",
                                        *(f"com{i}" for i in range(1, 10)),
                                        *(f"lpt{i}" for i in range(1, 10))}):
        _fail("invalid_attempt_id", "An explicit safe attempt identifier is required for the execution directory.")
    home = _explicit_path(config.codex_home)
    _check_directory_boundary(home)
    if not home.is_dir():
        _fail("missing_runtime_home", "The dedicated runtime home must already exist and be bound.")
    _reject_project_discovery(home)
    binding = _read_marker(home / _MARKER)
    config.validate_scope(binding["installation_id"], binding["role_id"], binding["generation"])
    state = _explicit_path(binding["state_root"])
    _check_directory_boundary(state)
    if _overlap(home, state):
        _fail("runtime_home_overlap", "The bound platform state must be outside the runtime home.")
    _regular_file(home / "auth.json", missing_ok=True)
    container = _explicit_path(home / "execution")
    leaf = _explicit_path(container / attempt_id)
    _reject_project_discovery(leaf)
    try:
        container.mkdir(exist_ok=True)
        leaf.mkdir(exist_ok=False)
    except FileExistsError:
        _fail("runtime_execution_exists", "This attempt already has an execution path; it cannot be reused.")
    except OSError:
        _fail("invalid_runtime_workspace", "Cannot create the private execution directory.")
    # Recheck after creation so detected aliases/configuration never reach spawn.
    leaf = _explicit_path(leaf)
    _reject_project_discovery(leaf)
    if any(leaf.iterdir()):
        _fail("runtime_execution_exists", "The new execution directory must be empty before launch.")
    return leaf


def build_argv(config: RuntimeConfig, workspace: str | Path) -> list[str]:
    """Build arguments only; prompts travel on stdin and never in argv."""
    _executable(config.executable)
    business = _explicit_path(workspace)
    _check_directory_boundary(business)
    if not business.is_dir():
        _fail("missing_workspace", "The explicit runtime workspace must already exist.")
    _reject_project_discovery(business)
    if config.runtime == "codex-app-server":
        argv = [str(config.executable), "app-server", "--listen", "stdio://",
                "-c", "model=" + json.dumps(config.model), "-c", 'sandbox_mode="read-only"']
    else:
        argv = [str(config.executable), "exec", "--json", "--ignore-user-config",
                "--sandbox", "read-only", "--skip-git-repo-check", "--color", "never",
                "--model", config.model, "-C", str(business)]
    # TOML inline table syntax keeps Windows drive separators and path dots
    # inside one quoted project key. No shell evaluates this argv element.
    project_key = json.dumps(str(business), ensure_ascii=False)
    overrides = ["project_doc_max_bytes=0", "project_root_markers=[]",
                 f'projects={{{project_key}={{trust_level="untrusted"}}}}',
                 'web_search="disabled"',
                 'approval_policy="never"', "mcp_servers={}",
                 # Host discovery does not control the native bundled skills.
                 # Disable their generation and instruction injection so each
                 # next task can keep rejecting every skills directory.
                 "skills.bundled.enabled=false", "skills.include_instructions=false"]
    overrides.extend(f"features.{feature}=false" for feature in _DISABLED_FEATURES)
    overrides.append("features.skip_host_skill_discovery=true")
    for override in overrides:
        argv.extend(["-c", override])
    return argv if config.runtime == "codex-app-server" else [*argv, "-"]


def child_environment(config: RuntimeConfig, base_env: Mapping[str, str] | None = None) -> dict[str, str]:
    """Allow only OS essentials; do not inherit credentials or agent sessions."""
    environment = os.environ if base_env is None else base_env
    result = {}
    for key, value in environment.items():
        target = _ENVIRONMENT_NAMES.get(key.casefold())
        if target is not None and isinstance(value, str):
            result[target] = value
    result["CODEX_HOME"] = str(config.codex_home)
    return result
