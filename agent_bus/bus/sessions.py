# -*- coding: utf-8 -*-
"""bus.sessions —— Claude Code 历史会话元数据 + 标记（ab sessions·仅元数据、可打标记）。

session_tags.json = 运行态磁盘文件（gitignore·不进 bus 核心 git）；按 session-id 存
{tags, note, cwd, title, tagged_by, tagged_at}。标时快照 cwd/title，便于会话 jsonl 被归档/prune
移出 projects/ 后，--tagged 仍能列出并给出 resume 所需 cwd。活死态实时算（有活 claude 进程=运行中）。

依赖：config(BUS/_now)· io(_audit)· roles(_load_retired_roster)· mailbox(_resolve_self_role)· psutil。无反向依赖=无环。
"""
import json, os
from pathlib import Path

try:
    import psutil
except Exception:
    psutil = None

from bus.config import BUS, _now
from bus.io import _audit
from bus.roles import _load_retired_roster
from bus.mailbox import _resolve_self_role


def _session_tags_path():
    return BUS / "session_tags.json"


def _load_session_tags():
    p = _session_tags_path()
    try:
        d = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _save_session_tags(d):
    p = _session_tags_path()
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, p)


def _bus_ids_and_cwds():
    """(bus会话id集, bus cwd集) —— 用于判一个历史会话是不是【bus 会话】。
    bus id = 任一 presence.json 的 session_id(活/停都算,文件持久)+ 退役档案里记的 session_ids;
    bus cwd = 任一 presence.json 的 cwd(bus agent 跑活的目录,即各角色自举的代码目录)。
    判定:session_id∈ids 或 cwd∈cwds → bus(含 agent 的旧会话);否则非bus(用户个人会话,如个人项目目录下的)。"""
    ids, cwds = set(), set()
    try:
        for pf in BUS.glob("*/presence.json"):
            try:
                d = json.loads(pf.read_text(encoding="utf-8"))
            except Exception:
                continue
            if d.get("session_id"):
                ids.add(d["session_id"])
            if d.get("cwd"):
                cwds.add(os.path.normcase(d["cwd"]))
    except Exception:
        pass
    try:
        for e in _load_retired_roster():
            for sid in (e.get("session_ids", []) or []):
                ids.add(sid)
    except Exception:
        pass
    return ids, cwds


def _live_session_roles():
    """{session_id: role} —— 当前【在运行】的会话:presence.json 有 session_id 且其 claude_pid/daemon_pid
    进程还活着。用于把"死的历史 jsonl"和"正在跑的会话"区分开(活死实时算,不存,进程一死即翻'死')。"""
    live = {}
    try:
        for pf in BUS.glob("*/presence.json"):
            try:
                d = json.loads(pf.read_text(encoding="utf-8"))
            except Exception:
                continue
            sid = d.get("session_id")
            if not sid:
                continue
            alive = False
            if psutil is not None:
                for k in ("claude_pid", "daemon_pid"):
                    pid = d.get(k)
                    if pid and psutil.pid_exists(pid):
                        alive = True
                        break
            else:
                alive = True  # 没 psutil 不敢判死,保守当活
            if alive:
                live[sid] = d.get("role")
    except Exception:
        pass
    return live


def _session_meta(sid):
    """按 session-id 在 ~/.claude/projects 找该会话 jsonl,读头部取 (cwd, title)。找不到→(None,None)。"""
    root = Path.home() / ".claude" / "projects"
    if not root.exists():
        return None, None
    for pdir in root.iterdir():
        f = pdir / (sid + ".jsonl")
        if not f.exists():
            continue
        cwd = title = label = None
        try:
            with open(f, encoding="utf-8", errors="replace") as fh:
                for i, ln in enumerate(fh):
                    if i > 80:
                        break
                    ln = ln.strip()
                    if not ln:
                        continue
                    try:
                        r = json.loads(ln)
                    except Exception:
                        continue
                    if not cwd and r.get("cwd"):
                        cwd = r.get("cwd")
                    if not title and r.get("type") == "ai-title":
                        title = r.get("aiTitle")
                    if not label and r.get("type") == "user":
                        m = r.get("message", {})
                        c = m.get("content") if isinstance(m, dict) else None
                        if isinstance(c, str):
                            label = c
                        elif isinstance(c, list):
                            label = " ".join(b.get("text", "") for b in c
                                              if isinstance(b, dict) and b.get("type") == "text")
                    if cwd and title:
                        break
        except Exception:
            pass
        return cwd, ((title or label or "").strip() or None)
    return None, None


def cmd_sessions(args):
    """列 Claude Code 历史会话(~/.claude/projects/<cwd编码>/<session-id>.jsonl),仅元数据、可【打标记】。
    - 列表:ab sessions [--project X] [--limit N] [--tagged](只看已标记)[--json];标记的会话显示 🏷标签。
    - 打标记:ab sessions --tag <会话id> [--label 可恢复] [--note "..."](默认标签=可恢复;顺带快照 cwd/title)。
    - 撤标记:ab sessions --untag <会话id> [--label X](不带 --label 撤该会话全部标签)。
    给 UI"可恢复会话"视图提供数据;真正 resume 在终端跑 `claude --resume <id>`(UI 只展示+给命令)。"""
    import datetime

    # ── 打标记 / 撤标记(mutate 分支)──
    sid_tag = getattr(args, "tag", None)
    sid_untag = getattr(args, "untag", None)
    if sid_tag or sid_untag:
        tags = _load_session_tags()
        label = (getattr(args, "label", None) or "").strip()
        if sid_tag:
            sid = sid_tag
            entry = tags.get(sid) or {"tags": [], "note": "", "cwd": "", "title": "",
                                      "tagged_by": "", "tagged_at": ""}
            lab = label or "可恢复"
            if lab not in entry["tags"]:
                entry["tags"].append(lab)
            if getattr(args, "note", None):
                entry["note"] = args.note.strip()
            cwd, title = _session_meta(sid)          # 快照元数据(防日后归档丢)
            if cwd:
                entry["cwd"] = cwd
            if title:
                entry["title"] = title[:70]
            entry["tagged_by"] = _resolve_self_role() or os.environ.get("AGENTBUS_ROLE", "unknown")
            entry["tagged_at"] = _now()
            tags[sid] = entry
            _save_session_tags(tags)
            _audit("session_tag", id=sid, label=lab)
            print("已标记会话 %s:🏷%s%s%s" % (
                sid, "/".join(entry["tags"]),
                ("  note=" + entry["note"]) if entry["note"] else "",
                ("  cwd=" + entry["cwd"]) if entry.get("cwd") else "  (jsonl 不在 projects/·可能已归档)" if not cwd else ""))
        else:
            sid = sid_untag
            if sid in tags:
                if label:
                    tags[sid]["tags"] = [t for t in tags[sid].get("tags", []) if t != label]
                    if tags[sid]["tags"]:
                        pass
                    else:
                        del tags[sid]
                else:
                    del tags[sid]
                _save_session_tags(tags)
                _audit("session_untag", id=sid, label=label or "*")
                print("已撤标记会话 %s(%s)" % (sid, ("label=" + label) if label else "全部标签"))
            else:
                print("会话 %s 无标记,无需撤" % sid)
        return

    # ── 列表 ──
    tags = _load_session_tags()
    live = _live_session_roles()          # {session_id: role} 当前在运行的
    bus_ids, bus_cwds = _bus_ids_and_cwds()
    only_tagged = getattr(args, "tagged", False)
    only_live = getattr(args, "live", False)
    only_dead = getattr(args, "dead", False)
    only_nonbus = getattr(args, "non_bus", False)
    only_bus = getattr(args, "bus", False)
    root = Path.home() / ".claude" / "projects"
    out = []
    if root.exists():
        for pdir in sorted(root.iterdir()):
            if not pdir.is_dir():
                continue
            if args.project and args.project.lower() not in pdir.name.lower():
                continue
            for f in pdir.glob("*.jsonl"):
                try:
                    st = f.stat()
                except Exception:
                    continue
                cwd = title = label = None
                try:
                    with open(f, encoding="utf-8", errors="replace") as fh:
                        for i, ln in enumerate(fh):
                            if i > 80:
                                break
                            ln = ln.strip()
                            if not ln:
                                continue
                            try:
                                r = json.loads(ln)
                            except Exception:
                                continue
                            if not cwd and r.get("cwd"):
                                cwd = r.get("cwd")
                            if not title and r.get("type") == "ai-title":
                                title = r.get("aiTitle")
                            if not label and r.get("type") == "user":
                                m = r.get("message", {})
                                c = m.get("content") if isinstance(m, dict) else None
                                if isinstance(c, str):
                                    label = c
                                elif isinstance(c, list):
                                    label = " ".join(b.get("text", "") for b in c
                                                      if isinstance(b, dict) and b.get("type") == "text")
                            if cwd and title and label:
                                break
                except Exception:
                    pass
                tg = tags.get(f.stem) or {}
                lr = live.get(f.stem)
                is_bus = (f.stem in bus_ids) or (bool(cwd) and os.path.normcase(cwd) in bus_cwds)
                out.append({
                    "id": f.stem,
                    "project": pdir.name,
                    "cwd": cwd or "",
                    "title": ((title or label or "").strip() or "(无标题)")[:70],
                    "last": datetime.datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M"),
                    "last_epoch": st.st_mtime,
                    "size_kb": round(st.st_size / 1024, 1),
                    "tags": tg.get("tags", []),
                    "note": tg.get("note", ""),
                    "state": "运行中" if lr else "死",
                    "live_role": lr,
                    "is_bus": is_bus,
                })
    seen = {s["id"] for s in out}
    # 已标记但 jsonl 不在 projects/(被归档/prune 移走)的会话:仍列出(用标记时快照的 cwd/title)
    for sid, tg in tags.items():
        if sid in seen or not tg.get("tags"):
            continue
        if args.project and args.project.lower() not in (tg.get("cwd", "") or "").lower():
            continue
        lr = live.get(sid)
        is_bus = (sid in bus_ids) or (bool(tg.get("cwd")) and os.path.normcase(tg.get("cwd")) in bus_cwds)
        out.append({
            "id": sid, "project": "(已归档/不在projects)", "cwd": tg.get("cwd", ""),
            "title": (tg.get("title") or "(无标题)")[:70], "last": "", "last_epoch": 0,
            "size_kb": 0, "tags": tg.get("tags", []), "note": tg.get("note", ""), "archived": True,
            "state": "运行中" if lr else "死", "live_role": lr, "is_bus": is_bus,
        })
    if only_tagged:
        out = [s for s in out if s.get("tags")]
    if only_live:
        out = [s for s in out if s.get("state") == "运行中"]
    if only_dead:
        out = [s for s in out if s.get("state") == "死"]
    if only_nonbus:
        out = [s for s in out if not s.get("is_bus")]
    if only_bus:
        out = [s for s in out if s.get("is_bus")]
    out.sort(key=lambda x: (x.get("state") != "运行中", -x["last_epoch"]))   # 运行中置顶,再按最近
    n_live = sum(1 for s in out if s.get("state") == "运行中")
    n_nonbus = sum(1 for s in out if not s.get("is_bus"))
    if args.limit and args.limit > 0:
        out = out[:args.limit]
    if args.json:
        print(json.dumps(out, ensure_ascii=False))
        return
    hdr = ("非bus会话" if only_nonbus else ("bus会话" if only_bus else
           ("已标记会话" if only_tagged else ("运行中会话" if only_live else
            ("死/可恢复会话" if only_dead else "Claude Code 历史会话")))))
    print("%s(~/.claude/projects/,共 %d · 运行中 %d · 死 %d · 非bus %d):" % (hdr, len(out), n_live, len(out) - n_live, n_nonbus))
    print("%-8s %-6s %-9s %-38s %-11s %s" % ("状态", "归属", "最近", "会话id", "🏷标签", "标题"))
    for s in out:
        tagstr = ("/".join(s["tags"]))[:11] if s.get("tags") else "-"
        last = s["last"][5:] if s["last"] else "(归档)"
        state = ("▶运行·" + (s.get("live_role") or "?")) if s.get("state") == "运行中" else "·死"
        owner = "bus" if s.get("is_bus") else "个人"
        print("%-8s %-6s %-9s %-38s %-11s %s" % (state[:8], owner, last, s["id"], tagstr, s["title"]))
        if s.get("note"):
            print("%48s ↳ %s" % ("", s["note"]))
    print("\n▶运行中=有活claude进程(bus在岗agent);·死=无活进程(真可恢复)。归属:bus=agent会话/个人=非bus(你自己的会话)。")
    print("筛选:--live / --dead / --tagged / --non-bus(只看个人)/ --bus   ·   标记:ab sessions --tag <id> [--label 可恢复]")
