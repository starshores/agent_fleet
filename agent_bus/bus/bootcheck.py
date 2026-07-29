# -*- coding: utf-8 -*-
"""bus.bootcheck —— 「该读没读」确定性核验(BUS 2.3 P0·用户点名 2026-07-28)。

链路:turn_state.py hook 把 bus 成员会话每次 Read 落 <role>/session_reads.jsonl(可观测层)
→ role_profiles.json 各角色 boot_reads 声明必读文件(单一源)+ 角色记忆索引自动纳入
→ `ab boot-check` 比对:必读 ⊆ 本会话真读过 → ✓装载完成 / ✗列缺失清单。
从此"角色和能力都加载了吗"有机器答案,不靠 agent 自述;培训师握手核这个结果。

匹配口径:路径规格化(小写+正斜杠)后【后缀匹配】——boot_reads 可写相对路径
(如 session-archive/extracted/EXTRACTION_PLAYBOOK.md),对 junction/物理双路径都健壮。
软闸(不硬拦·防文件缺失死锁):hello 尾部提示自验;未过只列缺失,领活不禁止。
"""
import json, os, re, sys
from pathlib import Path

from bus.config import BUS, cfg


def _norm(p):
    return str(p).replace("\\", "/").strip().lower()


def _profile(role):
    try:
        from bus.roster import load_role_profiles   # 两表合并视图(2026-07-29 分仓:基建+业务)
        for r in load_role_profiles().get("roles", []):
            if r.get("role") == role:
                return r
    except Exception:
        pass
    return None


def _required_reads(role):
    """角色必读清单 = role_profiles.boot_reads + 角色记忆索引(存在即自动纳入·按角色 cwd 推 slug)。
    返回 [(标签, 规格化后缀)];空=无声明(只查记忆索引,索引也没有则免检)。"""
    req = []
    prof = _profile(role) or {}
    for f in (prof.get("boot_reads") or []):
        req.append((f, _norm(f)))
    cwd = prof.get("cwd") or os.getcwd()
    try:
        slug = re.sub(r"[:\\/_. ]", "-", cwd)
        ri = Path.home() / ".claude" / "projects" / slug / "memory" / (role + ".MEMORY.md")
        if ri.exists():
            req.append(("角色记忆索引 " + str(ri), _norm(ri)))
    except Exception:
        pass
    # 能力→必读聚合(2.3 P1):capabilities.json 各 cap 可挂 reads(掌握该能力该读什么),
    # 角色必读=boot_reads+记忆索引+其所持 caps 的 reads 并集——清单随能力走,不用逐角色手维护。
    try:
        from bus.roster import _load_agents
        pool = json.loads((BUS / "capabilities.json").read_text(encoding="utf-8")).get("capabilities", {})
        have = {s for _, s in req}
        for c in ((_load_agents().get(role) or {}).get("caps") or []):
            for f in ((pool.get(c) or {}).get("reads") or []):
                nf = _norm(f)
                if nf not in have:
                    req.append(("cap:%s→%s" % (c, f), nf)); have.add(nf)
    except Exception:
        pass
    return req


def _session_reads(role, sid):
    f = BUS / role / "session_reads.jsonl"
    reads = set()
    if not f.exists():
        return reads
    try:
        for ln in f.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                r = json.loads(ln)
            except Exception:
                continue
            if sid and r.get("sid") != sid:
                continue
            reads.add(_norm(r.get("file") or ""))
    except Exception:
        pass
    return reads


def _sid_of(role):
    sid = os.environ.get("CLAUDE_CODE_SESSION_ID")
    if sid:
        return sid
    try:
        return json.loads((BUS / role / "presence.json").read_text(encoding="utf-8")).get("session_id")
    except Exception:
        return None


def boot_check(role, sid=None):
    """核验本会话装载完备度。返回 {ok, missing, matched, required_n, sid, note}。
    无任何必读声明(boot_reads 空且无角色记忆索引)→ ok=True + note 提示补声明(P1 角色页体检)。"""
    sid = sid or _sid_of(role)
    req = _required_reads(role)
    if not req:
        return {"ok": True, "missing": [], "matched": [], "required_n": 0, "sid": sid,
                "note": "该角色无 boot_reads 声明且无角色记忆索引——免检;建议随角色页体检补声明"}
    reads = _session_reads(role, sid)
    matched, missing = [], []
    for label, suffix in req:
        hit = any(r.endswith(suffix) or suffix.endswith(r) for r in reads if r)
        (matched if hit else missing).append(label)
    return {"ok": not missing, "missing": missing, "matched": matched,
            "required_n": len(req), "sid": sid}


def cmd_boot_check(args):
    role = args.role
    if not role:
        from bus.mailbox import _resolve_self_role
        role = _resolve_self_role()
        if not role:
            print("boot-check 需要 --role(或先 hello 绑定本会话)"); sys.exit(2)
    r = boot_check(role, sid=getattr(args, "sid", None))
    if getattr(args, "json", False):
        r["role"] = role
        print(json.dumps(r, ensure_ascii=False)); sys.exit(0 if r["ok"] else 1)
    if r["ok"]:
        extra = ("(%s)" % r["note"]) if r.get("note") else ("(%d/%d 必读均已读)" % (len(r["matched"]), r["required_n"]))
        print("✅ boot-check %s:装载完成 %s" % (role, extra))
        sys.exit(0)
    print("⛔ boot-check %s:装载未完成——以下必读【本会话还没读】,用 Read 逐个读完再领活:" % role)
    for m in r["missing"]:
        print("   ✗ " + m)
    if r["matched"]:
        print("   (已读:%s)" % "、".join(x[:60] for x in r["matched"]))
    print("   读完重跑 ab boot-check --role %s 直到 ✅。" % role)
    sys.exit(1)


# ---------------- 2.3 P1 体检三件(角色页/记忆孤儿/caps 池)----------------

def cmd_role_audit(args):
    """角色页完备度体检:role_profiles 各角色缺哪些字段;agents.json 活跃角色谁没建档。
    机制归 MasterAgent·内容补齐归培训师(体检报告即培训师的工单)。"""
    from bus.roster import _load_agents, _retired_roles, _pseudo_role_label, _is_test_role
    from bus.roster import load_role_profiles
    prof = load_role_profiles()   # 两表合并视图(基建+业务·2026-07-29 分仓)
    need = ("one_liner", "cwd", "mode", "boundary", "startup_flow", "boot_reads")
    rows, ok = [], 0
    for r in prof.get("roles", []):
        if r.get("_abstract"):
            ok += 1; continue      # 抽象条目(如『业务角色』)=分类卡,不按具体角色字段硬要求
        miss = [k for k in need if not r.get(k)]
        if miss:
            rows.append((r.get("role"), "缺:" + "、".join(miss)))
        else:
            ok += 1
    retired = _retired_roles()
    reg = {r.get("role") for r in prof.get("roles", [])}
    undoc = [a for a in _load_agents()
             if a not in reg and a not in retired and not _pseudo_role_label(a) and not _is_test_role(a)]
    print("角色页体检:已注册 %d(完备 %d/缺字段 %d);在册未建档 %d" % (len(prof.get("roles", [])), ok, len(rows), len(undoc)))
    for role, msg in rows:
        print("  ◪ %-12s %s" % (role, msg))
    for a in undoc:
        print("  ☐ %-12s 不在 role_profiles(角色页/boot-check 均无从谈起)——请培训师建档" % a)
    if not rows and not undoc:
        print("  ✅ 全部完备")


def cmd_memory_audit(args):
    """记忆孤儿巡检:记忆库里存在、但没有任何 *MEMORY.md 索引指到的 .md(写了记忆忘登记=下次谁也想不起)。
    默认扫 MasterAgent 记忆库;--dir 指定其它库(如业务库)。"""
    d = Path(getattr(args, "dir", None) or (Path.home() / ".claude" / "projects" / "C--apphome-agent-fleet" / "memory"))
    if not d.is_dir():
        print("记忆库不存在:%s" % d); sys.exit(1)
    idx_text = ""
    for f in d.glob("*MEMORY.md"):
        try:
            idx_text += f.read_text(encoding="utf-8", errors="replace")
        except Exception:
            pass
    orphans = []
    for f in sorted(d.glob("*.md")):
        if f.name.endswith("MEMORY.md"):
            continue
        if f.name not in idx_text and f.stem not in idx_text:
            orphans.append(f.name)
    print("记忆孤儿巡检(%s):共 %d 个 .md,孤儿 %d" % (d, len(list(d.glob('*.md'))), len(orphans)))
    for o in orphans:
        print("  ☐ %s(无索引指向——补进对应角色索引或删)" % o)
    if not orphans:
        print("  ✅ 无孤儿(全部记忆均有索引指向)")


def cmd_caps_audit(args):
    """caps 池联检:①角色持有但池里没有的 cap ②池里存在但无人持有的 cap(dormant·非错)
    ③池 cap 缺 desc ④池 cap 挂了 reads 的(供 boot-check 聚合)。登记归培训师·机制归 MasterAgent。"""
    from bus.roster import _load_agents, _retired_roles, _is_test_role
    try:
        pool = json.loads((BUS / "capabilities.json").read_text(encoding="utf-8")).get("capabilities", {})
    except Exception as e:
        print("capabilities.json 不可读:%s" % e); sys.exit(1)
    agents = _load_agents(); retired = _retired_roles()
    held = {}
    for role, v in agents.items():
        if role in retired or _is_test_role(role) or not isinstance(v, dict):
            continue
        for c in (v.get("caps") or []):
            held.setdefault(c, []).append(role)
    ghost = {c: rs for c, rs in held.items() if c not in pool}
    dormant = [c for c in pool if c not in held]
    nodesc = [c for c, v in pool.items() if not (v or {}).get("desc")]
    withreads = [c for c, v in pool.items() if (v or {}).get("reads")]
    print("caps 池联检:池 %d 个 · 被持有 %d 个" % (len(pool), len(held)))
    for c, rs in ghost.items():
        print("  ✗ 幽灵 cap「%s」:%s 持有但池里没有——池补登记或角色摘除(培训师)" % (c, "、".join(rs)))
    for c in nodesc:
        print("  ◪ 池 cap「%s」缺 desc(hello 喂入会显『池中无此能力描述』)" % c)
    if dormant:
        print("  · dormant(池有人无·非错):%s" % "、".join(dormant))
    print("  · 挂 reads 的 cap(boot-check 聚合生效):%s" % ("、".join(withreads) or "(暂无——培训师可给 cap 挂 reads,角色必读自动聚合)"))
    if not ghost and not nodesc:
        print("  ✅ 无幽灵/无缺 desc")
