# -*- coding: utf-8 -*-
"""bus.roster —— agents.json 读写 / 角色目录 / 角色分类(叶子层,仅依赖 bus.config)。

注:更上层的可达校验 _warn_unreachable 依赖 _self_wake_state,仍留在 agentbus.py,不在此。
"""
import json
from bus.config import BUS

# 人值守角色:它们的回填才值得弹给人(toast 分级 + 可达校验豁免用)。角色分类,归 roster。
HUMAN_ATTENDED = ("Master", "MasterAgent")


def _agents_file():
    return BUS / "agents.json"


def _load_agents():
    f = _agents_file()
    if f.exists():
        try:
            return json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _save_agents(a):
    BUS.mkdir(parents=True, exist_ok=True)
    _agents_file().write_text(json.dumps(a, ensure_ascii=False, indent=1), encoding="utf-8")


def _role_dir(role):
    d = BUS / role
    for sub in ("inbox", "processing", "archive", "done"):
        (d / sub).mkdir(parents=True, exist_ok=True)
    return d


def _list_roles():
    """BUS 下的 role 目录(排除 __pycache__ / 以 . 或 _ 开头的非 role 目录)。"""
    if not BUS.exists():
        return []
    return sorted([p.name for p in BUS.iterdir()
                   if p.is_dir() and not p.name.startswith((".", "_"))])


def _retired_roles():
    """已退役 role 集合(agents.json 里 retired 字段为真)。退役语义:不再派活/onboarding、
    不当"掉线/缺失"误报、supervise 不拉起、留痕不删(贡献档案在记忆)。succeeded_by 记接替者。"""
    try:
        return {r for r, v in _load_agents().items() if isinstance(v, dict) and v.get("retired")}
    except Exception:
        return set()


def _is_test_role(name):
    """测试/临时角色:zz* / __* / 单字符 ?。统计/健康/校验默认过滤,免测试 send 污染真实指标
    (Master数分 实证:未claim 17 条里 6 条是 zztest_loss/__todotest 等测试,制造假"漏接")。"""
    return bool(name) and (name.startswith(("zz", "__")) or name == "?")


# 伪角色/系统桶:有 mailbox 目录但本质【非执行体】——不是 CLI tab、永不 hello、无 presence=正常,
# 不该在 selfwake/who 里被判"掉线/未醒"混进真 agent(用户 2026-07-02·数分 转)。显式名单为准,纯显示层标注。
_PSEUDO_ROLE_LABELS = {
    "用户": "伪角色·待办池(ask-user 投给人类的收件箱,人类永不 hello)",
    "unknown": "伪角色·死信桶(role 解析不出的消息/回执落此,无害待清)",
}


def _pseudo_role_label(role):
    """伪角色返回其标注串;真 agent 返回 None。"""
    return _PSEUDO_ROLE_LABELS.get(role)


def _case_collision(role):
    """返回与 role【仅大小写不同】的既有角色名,无则 None。
    Windows 文件系统大小写不敏感 → Masteragent 与 MasterAgent 会共用同一 mailbox 目录,
    但 agents.json 是大小写敏感 dict → 两个 key 指同一目录 = 幽灵重复 + 路由错乱。
    register / hello 前调用拦截,强制用既有 exact 名(2026-07-15 大小写碰撞事故根治)。
    检既有 agents.json 注册名 + BUS 下物理目录名;exact 相等不算碰撞。"""
    if not role:
        return None
    rl = role.lower()
    seen = set(_load_agents().keys())
    try:
        seen |= {p.name for p in BUS.iterdir() if p.is_dir() and not p.name.startswith((".", "_"))}
    except Exception:
        pass
    for name in seen:
        if name != role and name.lower() == rl:
            return name
    return None
