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
        home = cfg("agent_fleet_home") or str(Path(__file__).resolve().parents[2])
        for r in json.loads(Path(home, "role_profiles.json").read_text(encoding="utf-8")).get("roles", []):
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
