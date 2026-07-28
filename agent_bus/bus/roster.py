# -*- coding: utf-8 -*-
"""bus.roster —— agents.json 读写 / 角色目录 / 角色分类(叶子层,仅依赖 bus.config)。

注:更上层的可达校验 _warn_unreachable 依赖 _self_wake_state,仍留在 agentbus.py,不在此。
"""
import json
from pathlib import Path
from bus.config import BUS, cfg
from bus.io import _write_json, json_lock


def _policy():
    """角色策略注册表(P1·2026-07-28 出代码):role_profiles.json meta.policy——
    human_attended / mgmt_owner / biz_default_owner 原散在多处硬编码,角色增减要改代码且守卫可被伪造名绕过。
    读不到返 {}(调用方用降级默认兜底,绝不抛)。路径经 cfg agent_fleet_home,回退按物理位推
    (resolve 穿 junction 落真实家,与 identity._role_profile 同款)。"""
    try:
        home = cfg("agent_fleet_home") or str(Path(__file__).resolve().parents[2])
        return json.loads(Path(home, "role_profiles.json").read_text(encoding="utf-8")).get("meta", {}).get("policy") or {}
    except Exception:
        return {}


# 人值守角色:它们的回填才值得弹给人(toast 分级 + 可达校验豁免用)。角色分类,归 roster。
# 单一源=role_profiles.json meta.policy.human_attended;硬编码仅为注册表不可读时的降级默认。
HUMAN_ATTENDED = tuple(_policy().get("human_attended") or ("Master", "MasterAgent"))


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
    """原子写(P0·2026-07-28:原 write_text 直写,崩溃截断=全花名册消失)。
    做【读-改-写】的调用方应走 update_agents(锁跨 RMW),别 load→改→本函数裸写。"""
    BUS.mkdir(parents=True, exist_ok=True)
    _write_json(_agents_file(), a)


def update_agents(mutator):
    """agents.json 并发安全 RMW(P0·2026-07-28:register/hello/retire/revive/train-done
    多方并发 load→改→save 会互相覆盖丢更新)。json_lock 跨整个读-改-写持锁;
    mutator(a) 就地改 dict,其返回值透传给调用方。"""
    with json_lock(_agents_file()):
        a = _load_agents()
        r = mutator(a)
        _save_agents(a)
        return r


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


def _retired_roster_names():
    """归档式退役名单:retired_roster.json(list of {role,...})。归档式退役=移进此档 + 从 agents.json 删除,
    占退役的绝大多数(11 条实测全是归档式,无一用 agents.json 标志位)。best-effort·读不出返空。"""
    try:
        d = json.loads((BUS / "retired_roster.json").read_text(encoding="utf-8"))
        if isinstance(d, list):
            return {e.get("role") for e in d if isinstance(e, dict) and e.get("role")}
    except Exception:
        pass
    return set()


def _retired_roles():
    """已退役 role 集合(archive-aware·2026-07-17 培训师诊断修)。两来源:
    ①agents.json retired 标志位(旗标式退役);②retired_roster.json 归档(归档式退役·占绝大多数)。
    旧实现只读①→归档式退役恒判不出(实测返空)→refs.check 的②accepted 永不命中、supervise/派活防误报失效。
    ★活跃优先(防复用基名误伤):归档名若【当前又是活跃 role】(在 agents.json 在册 或 有 role 目录),
    以活跃为准、不判退役——否则把 revive 重用基名的现役 agent 误标⊘退役(实证:Master数分 旧会话入档 +
    新会话重注册为 active,若裸 union 会把现役 Master数分误判退役)。退役语义:不再派活/onboarding、不当"掉线"误报、
    supervise 不拉起、留痕不删。返回 flagged ∪ (archived − live);live=agents.json 在册(除已 flagged)+ role 目录。"""
    try:
        agents = _load_agents()
    except Exception:
        agents = {}
    flagged = {r for r, v in agents.items() if isinstance(v, dict) and v.get("retired")}
    archived = _retired_roster_names()
    live = (set(agents.keys()) - flagged) | set(_list_roles())  # 现役 role(旗标退役的不算现役)
    return flagged | (archived - live)


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
