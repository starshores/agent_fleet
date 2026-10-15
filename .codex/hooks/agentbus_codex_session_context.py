"""Inject the explicitly selected role card and its project memory into Codex."""
import json
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
BUS = ROOT / "agent_bus"
sys.path.insert(0, str(BUS))
from bus.roster import load_role_profiles
from bus.codex_startup import (claim_role_assignment, restore_registered_codex_session,
                               prepare_session_start_identity)


def _read(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _profiles():
    return {row.get("role"): row for row in load_role_profiles().get("roles", [])
            if row.get("role")}


def _exact_role(session_id):
    if not isinstance(session_id, str) or not session_id.strip() or session_id != session_id.strip():
        return None, "A nonempty exact Codex session id is required."
    roles = []
    for role in _read(BUS / "agents.json"):
        presence = _read(BUS / role / "presence.json")
        if (presence.get("host_kind") == "codex"
                and presence.get("host_session_id") == session_id):
            roles.append(role)
    if len(roles) > 1:
        return None, "This session id is bound to multiple roles; stop and report the conflict."
    return (roles[0], "") if roles else (None, "")


def _ticket_role(session_id, cwd):
    """Expose the role only after the ticket is bound to this exact session."""
    return claim_role_assignment(session_id, cwd)


def _memory_index(role, profile):
    role_cwd = profile.get("memory_cwd") or profile.get("cwd") or ""
    if not role_cwd:
        return None
    # Prefer the active project's own memory entry when available. The
    # ~/.claude/projects tree may be a hard-link/junction alias of this same
    # store; using both spellings in Codex context makes one index look like
    # two competing locations.
    project_memory = Path(role_cwd) / "memory"
    role_index = project_memory / (role + ".MEMORY.md")
    if role_index.is_file():
        return role_index
    root_index = project_memory / "MEMORY.md"
    if root_index.is_file():
        return root_index

    slug = re.sub(r"[:\\/_. ]", "-", role_cwd)
    memory_dir = Path.home() / ".claude" / "projects" / slug / "memory"
    role_index = memory_dir / (role + ".MEMORY.md")
    if role_index.is_file():
        return role_index
    root_index = memory_dir / "MEMORY.md"
    if root_index.is_file():
        return root_index
    if Path(role_cwd).resolve() == ROOT.resolve():
        root_index = ROOT / "memory" / "MEMORY.md"
        if root_index.is_file():
            return root_index
    return None


def _role_memory_files(role, profile):
    """Find role-specific session memory when no curated <role>.MEMORY.md exists.

    Extracted role notes live under memory/roles/<role>-*.md. This is a generic
    exact-role-prefix lookup; it does not infer identity from a filename or cwd.
    The role must already have been resolved from exact presence or a role ticket.
    """
    role_cwd = profile.get("memory_cwd") or profile.get("cwd") or ""
    if not role_cwd or not role:
        return []
    slug = re.sub(r"[:\\/_. ]", "-", role_cwd)
    role_dir = Path.home() / ".claude" / "projects" / slug / "memory" / "roles"
    if not role_dir.is_dir():
        return []
    try:
        prefix = role + "-"
        return sorted(p for p in role_dir.iterdir()
                      if p.is_file() and p.suffix.lower() == ".md"
                      and p.name.startswith(prefix))
    except Exception:
        return []


def _project_claude_context(cwd):
    """Keep project rules available without injecting legacy CC operations.

    This reads only the instruction file directly under Codex's reported cwd;
    cwd is never used to infer an agent-bus role.
    """
    if not isinstance(cwd, str) or not cwd.strip():
        return ""
    path = Path(cwd) / "CLAUDE.md"
    try:
        if not path.is_file():
            return ""
        if path.parent.resolve() == ROOT.resolve():
            return (
                "## 项目 CLAUDE.md（旧 Claude Code 操作参考）\n"
                "来源：" + str(path) + "\n"
                "Codex 项目规则见 AGENTS.override.md；涉及旧总线、迁移或历史约定时，"
                "按当前任务读取 CLAUDE.md 的相关章节。其中手动 hello/wait/loop 步骤不适用于 Codex。"
            )
        content = path.read_text(encoding="utf-8")
        return "## 项目 CLAUDE.md（Codex 项目指令层；原文载入）\n来源：" + str(path) + "\n\n" + content
    except Exception as exc:
        return "## 项目 CLAUDE.md\n读取失败：%s（%s）" % (path, exc)


def _codex_boundary(boundary):
    """Keep the shared role card's duties while replacing its Claude wait recipe."""
    old = re.search(r"★★自醒不降级.*?(?=★fork 分岔发布职责)", boundary, re.DOTALL)
    if old:
        boundary = (boundary[:old.start()]
                    + "★★即时收信：Codex 信使维持事件驱动投递，不退化为长间隔轮询。"
                    + boundary[old.end():])
    return boundary


def _build_context(session_id, cwd):
    base = (
        "Agent-bus role context requires this exact Codex session id to match "
        "presence.json or a valid role ticket atomically reserved for this session. "
        "A reserved ticket does not prove hello or bridge startup has completed. "
        "Never infer a role from cwd or PID."
    )
    role, error = _exact_role(session_id)
    if error:
        # Conflicting or absent identity must not consume a ticket or read any
        # role-specific project instructions or memory while reporting failure.
        return base + "\n\nIdentity error: " + error
    resolved_role = role or _ticket_role(session_id, cwd)
    profile = _profiles().get(resolved_role) or {}
    # Once identity is exact/explicit, use that role's registered project root.
    # For an unbound thread, load only the CLAUDE.md at Codex's reported cwd.
    project_cwd = profile.get("cwd") or cwd
    project_prompt = _project_claude_context(project_cwd)
    project_parts = [base]
    if project_prompt:
        project_parts.append(project_prompt)
    role = resolved_role
    if not role:
        project_parts.append("This session has no explicit agent-bus role binding. "
                             "Do not assume Master, MasterAgent, or a business role. "
                             "Wait for an exact presence binding or explicitly assign a role.")
        return "\n\n".join(project_parts)

    profile = _profiles().get(role) or {}
    parts = project_parts + ["## Explicit agent-bus role: " + role]
    if profile.get("one_liner"):
        parts.append("定位：" + profile["one_liner"])
    if profile.get("boundary"):
        parts.append("职责边界：" + _codex_boundary(profile["boundary"]))
    parts.append(
        "Codex 信使由 SessionStart 管理收件与内部 wait；角色不手动挂 wait。"
        "怀疑失联时用 `ab selfwake` 核对信使、wait 和 App Server，再由基础设施恢复。"
    )
    # The shared profile predates Codex's AGENTS.override.md. Its large
    # agent_fleet/AGENTS.md is a legacy bus manual, not a Codex boot read.
    boot_reads = [x for x in profile.get("boot_reads", [])
                  if x.replace("\\", "/") != "agent_fleet/AGENTS.md"]
    if boot_reads:
        parts.append("角色必读文件：\n" + "\n".join("- " + x for x in boot_reads))

    memory_path = _memory_index(role, profile)
    role_memory_paths = _role_memory_files(role, profile)
    if memory_path:
        parts.append("角色记忆索引（按角色工作区解析）：" + str(memory_path))
        try:
            memory_text = memory_path.read_text(encoding="utf-8")
            if len(memory_text) <= 9000:
                parts.append("## 已载入角色记忆索引\n" + memory_text)
            else:
                parts.append("该索引较长，请在处理首个任务前用 Read 完整读取此文件。")
        except Exception:
            parts.append("记忆索引读取失败；开始任务前需从上述路径读取。")
        if memory_path.name == "MEMORY.md":
            parts.append("这是项目记忆路由索引；开始相关任务前，按任务主题展开对应子索引和记忆正文。")
    if role_memory_paths:
        # A curated role index remains the single preferred source. Extracted
        # session notes are a fallback entry point and can be opened on demand.
        if memory_path and memory_path.name == role + ".MEMORY.md":
            parts.append("已优先加载角色专属索引；另有历史提炼记忆可按当前任务需要读取：\n" +
                         "\n".join("- " + str(p) for p in role_memory_paths))
        elif len(role_memory_paths) == 1:
            note = role_memory_paths[0]
            parts.append("按已绑定角色找到专属提炼记忆：" + str(note))
            try:
                note_text = note.read_text(encoding="utf-8")
                if len(note_text) <= 9000:
                    parts.append("## 已载入角色专属记忆\n" + note_text)
                else:
                    parts.append("角色记忆较长；开始任务前按任务需要用 Read 读取上述文件。")
            except Exception:
                parts.append("角色记忆读取失败；开始相关任务前需从上述路径读取。")
        else:
            parts.append("按已绑定角色找到多份历史提炼记忆；按当前任务需要选择读取：\n" +
                         "\n".join("- " + str(p) for p in role_memory_paths))
    else:
        if not memory_path:
            parts.append("未找到该角色工作区的记忆索引；不得借用其他项目/角色记忆，先报告缺失并继续可独立完成的部分。")
    return "\n\n".join(parts)


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        event = json.load(sys.stdin)
    except Exception:
        return 0
    if (event.get("hook_event_name") != "SessionStart"
            or event.get("source") not in ("startup", "resume")):
        return 0
    prepare_session_start_identity(event.get("session_id"))
    restore_registered_codex_session(event.get("session_id"))
    context = _build_context(event.get("session_id"), event.get("cwd"))
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "SessionStart", "additionalContext": context,
    }}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
