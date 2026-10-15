"""Re-inject the Codex-specific MasterAgent role card after compaction.

This hook only claims the role when the current Codex session id exactly matches
the role's host_session_id in the existing agent-bus presence record.
"""
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PRESENCE = ROOT / "agent_bus" / "MasterAgent" / "presence.json"
AGENTS = ROOT / "agent_bus" / "agents.json"
ROLE_CARD = ROOT / ".codex" / "MASTERAGENT_ROLE_CONTEXT.md"


def _read_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _build_context(session_id):
    base = (
        "身份判定规则：仅当 agent_bus/MasterAgent/presence.json 的 host_kind=codex，"
        "且 host_session_id 与本次 Codex session_id 完全一致时，才认定本会话是 MasterAgent。"
        "不要按工作目录或旧 Claude 记忆猜角色；匹配失败时先运行 whoami 核对，不要冒名报到。"
    )
    presence = _read_json(PRESENCE)
    if not (session_id and presence.get("role") == "MasterAgent"
            and presence.get("host_kind") == "codex"
            and presence.get("host_session_id") == session_id):
        return base + "\n当前 session 未与 MasterAgent bus 身份匹配；不得执行角色专属操作。"

    parts = [base, ROLE_CARD.read_text(encoding="utf-8")]
    agents = _read_json(AGENTS)
    caps = (agents.get("MasterAgent") or {}).get("caps") or []
    if caps:
        parts.append("当前登记能力：" + "、".join(caps))
    return "\n\n".join(parts)


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        event = json.load(sys.stdin)
    except Exception:
        return 0
    if event.get("hook_event_name") != "SessionStart" or event.get("source") != "compact":
        return 0
    context = _build_context(event.get("session_id"))
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": context,
        }
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
