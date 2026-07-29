# -*- coding: utf-8 -*-
"""bus.storage —— 存储体检 `ab storage-audit`(BUS 2.4 P1·用户立项 2026-07-29「项目存储大小」)。

三分格局(单一源见 path_registry.json data-home 条):
  项目根(代码+运行态方舟)/ _releases(发版快照)/ _data(素材源数据)。
本命令给 2.4 长效巡检抓手:
  ① 三分格局总览(各家体积);
  ② 项目根内按 top-level 分类分布(代码/方舟/运行态/素材外置…),junction 不追体积(外置=不计入项目根);
  ③ 素材入库纪律违规检测:>阈值(默认 5MB)的大文件落在项目根非豁免区 = 违规候选
     (纪律:新素材/raw/中间产物一律落 _data 或 scratchpad,不落项目根/docs——AGENTS.md 操作锚)。
豁免区(运行态·各有自己的代谢/方舟机制,不算素材违规):
  agent_bus 根的 db/jsonl(usage.db 方舟·events.jsonl 卫生轮转)、projects/(CC 自清+迁移包设计)、
  .git/(史·发版 zip 不含)、memory/(记忆镜像·天然小)。
只读体检,零改动;数据求真:体积=逐文件 st_size 实算,junction 标注外置目标、不猜大小。
"""
import json
import os
import stat
import sys

from bus.config import cfg

_REPARSE = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)

# 项目根 top-level 分类(对齐 path_registry 口径;未列入的目录归"其他")
_CLASS = {
    "agent_bus": ("总线", "源码+运行态方舟(usage.db 不动·mailbox 卫生代谢在吃)"),
    "projects": ("运行态", "全员会话/记忆根(CC 自清老 jsonl·业务三库已外置 junction)"),
    "memory": ("记忆", "MasterAgent 运行记忆硬链镜像"),
    "ui": ("代码", ""), "hooks": ("代码", ""), "githooks": ("代码", ""),
    "docs": ("文档", ""), "host_config": ("宿主配置", ""),
    ".git": ("git史", "发版 zip 不含"),
    "session-archive": ("素材外置", "junction→_data(2.4 P0)"),
}

# 素材违规豁免前缀(相对项目根 home·小写正斜杠)
_EXEMPT_PREFIXES = (".git/", "projects/", "memory/")


def _is_reparse(st):
    return bool(getattr(st, "st_file_attributes", 0) & _REPARSE)


def _du(root, big=None, big_min=0, rel_base=None):
    """逐文件实算体积(junction/symlink 不追);big=收集器 [(size, relpath)]。返回 (bytes, files)。"""
    total, files = 0, 0
    stack = [str(root)]
    base = str(rel_base or root)
    while stack:
        d = stack.pop()
        try:
            it = os.scandir(d)
        except OSError:
            continue
        with it:
            for e in it:
                try:
                    st = e.stat(follow_symlinks=False)
                except OSError:
                    continue
                if _is_reparse(st):
                    continue          # junction/symlink=外置引用,体积归物理家
                if e.is_dir(follow_symlinks=False):
                    stack.append(e.path)
                else:
                    total += st.st_size
                    files += 1
                    if big is not None and st.st_size >= big_min:
                        rel = os.path.relpath(e.path, base).replace("\\", "/")
                        big.append((st.st_size, rel))
    return total, files


def _fmt(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return ("%.1f%s" % (n / 1.0, unit)) if unit == "B" else ("%.1f%s" % (n, unit))
        n /= 1024.0
    return "%dB" % n


def storage_audit(threshold_mb=5.0, top=10):
    """存储体检数据(供 CLI 与 WebUI 2.4 P2 面板共用)。"""
    home = str(cfg("agent_fleet_home", "C:/apphome/agent_fleet")).replace("\\", "/").rstrip("/")
    parent = os.path.dirname(home)
    name = os.path.basename(home)
    big_min = int(threshold_mb * 1024 * 1024)

    # ② 项目根 top-level 分布 + 大文件收集
    tops, big = [], []
    root_files = 0
    try:
        entries = list(os.scandir(home))
    except OSError:
        entries = []
    for e in entries:
        try:
            st = e.stat(follow_symlinks=False)
        except OSError:
            continue
        if e.is_dir(follow_symlinks=False):
            if _is_reparse(st):
                tops.append({"name": e.name, "bytes": 0, "files": 0, "junction": True,
                             "cls": _CLASS.get(e.name, ("素材外置", ""))[0],
                             "note": _CLASS.get(e.name, ("", "junction 外置·体积归物理家"))[1] or "junction 外置·体积归物理家"})
                continue
            b, f = _du(e.path, big=big, big_min=big_min, rel_base=home)
            cls, note = _CLASS.get(e.name, ("其他", ""))
            tops.append({"name": e.name, "bytes": b, "files": f, "junction": False, "cls": cls, "note": note})
        else:
            root_files += st.st_size
            if st.st_size >= big_min:
                big.append((st.st_size, e.name))
    tops.append({"name": "(根文件)", "bytes": root_files, "files": 0, "junction": False,
                 "cls": "锚点", "note": "CLAUDE/AGENTS/CAPABILITIES 等操作锚"})
    tops.sort(key=lambda t: -t["bytes"])
    root_total = sum(t["bytes"] for t in tops)

    # ③ 素材违规:>阈值且不在豁免区;agent_bus 只豁免其根 db/jsonl(方舟/代谢机制内)
    viol = []
    for size, rel in sorted(big, reverse=True):
        low = rel.lower()
        if low.startswith(_EXEMPT_PREFIXES):
            continue
        if low.startswith("agent_bus/") and low.count("/") == 1 and low.endswith((".db", ".jsonl")):
            continue
        viol.append({"path": rel, "bytes": size})

    # ① 三分格局总览(_releases/_data 只算总量·不细分)
    tri = [{"name": name, "path": home, "bytes": root_total, "role": "代码+运行态方舟"}]
    for suffix, role in ((name + "_releases", "发版快照"), (name + "_data", "素材源数据")):
        p = os.path.join(parent, suffix)
        if os.path.isdir(p):
            b, _f = _du(p)
            tri.append({"name": suffix, "path": p.replace("\\", "/"), "bytes": b, "role": role})

    big.sort(reverse=True)
    return {"home": home, "threshold_mb": threshold_mb, "tri": tri, "root_top": tops,
            "big_files": [{"path": r, "bytes": s} for s, r in big[:top]], "violations": viol}


def cmd_storage_audit(args):
    data = storage_audit(threshold_mb=args.threshold_mb, top=args.top)
    if getattr(args, "json", False):
        print(json.dumps(data, ensure_ascii=False, indent=1))
        return
    print("📦 三分格局(项目根=代码+方舟 / _releases=发版 / _data=素材)")
    for t in data["tri"]:
        print("  %-28s %9s  %s" % (t["name"], _fmt(t["bytes"]), t["role"]))
    print("\n📂 项目根内分布(junction 不计体积·归物理家)")
    for t in data["root_top"]:
        tag = "↗外置" if t["junction"] else _fmt(t["bytes"])
        note = ("  — " + t["note"]) if t["note"] else ""
        print("  %-16s %9s  [%s]%s" % (t["name"], tag, t["cls"], note))
    print("\n🔍 大文件 top%d(≥%.0fMB)" % (args.top, data["threshold_mb"]))
    if data["big_files"]:
        for f in data["big_files"]:
            print("  %9s  %s" % (_fmt(f["bytes"]), f["path"]))
    else:
        print("  (无)")
    if data["violations"]:
        print("\n⚠ 素材纪律违规候选(≥%.0fMB 落项目根非豁免区——素材应落 _data 或 scratchpad):" % data["threshold_mb"])
        for v in data["violations"]:
            print("  %9s  %s" % (_fmt(v["bytes"]), v["path"]))
        print("  处置:物理移 _data + 原位 junction(遵守【先广播→动刀→再确认】);确属运行态则来改豁免口径。")
    else:
        print("\n✅ 素材纪律:无违规候选(项目根内无 ≥%.0fMB 的非豁免大文件)。" % data["threshold_mb"])


if __name__ == "__main__":
    sys.exit(0)
