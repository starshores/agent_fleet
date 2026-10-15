"""User-global SessionStart hook for explicit Codex role memory loading."""
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CONTEXT_HOOK_NAME = "agentbus_codex_session_context.py"
sys.path.insert(0, str(ROOT / ".codex" / "hooks"))
import agentbus_codex_session_context as context
from bus.codex_startup import prepare_session_start_identity


def _project_has_startup_context(cwd):
    """Avoid duplicate startup injection where a trusted project hook owns it."""
    if not cwd:
        return False
    try:
        current = Path(cwd).resolve()
        for parent in (current, *current.parents):
            config = parent / ".codex" / "config.toml"
            if config.is_file() and CONTEXT_HOOK_NAME in config.read_text(encoding="utf-8"):
                return True
    except Exception:
        return False
    return False


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        event = json.load(sys.stdin)
    except Exception:
        return 0
    if event.get("hook_event_name") != "SessionStart":
        return 0
    source = event.get("source")
    cwd = event.get("cwd")
    if source in ("startup", "resume") and _project_has_startup_context(cwd):
        return 0
    session_id = event.get("session_id")
    if source in ("startup", "resume"):
        prepare_session_start_identity(session_id)
        context.restore_registered_codex_session(session_id)
    result = context._build_context(session_id, cwd)
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "SessionStart", "additionalContext": result,
    }}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
