# -*- coding: utf-8 -*-
"""bus.memory_history —— 记忆分层机制(memory tiering·用户令 2026-09-01·机制=MasterAgent lane·口径=Master数分)。

背景:陈旧/垃圾记忆占【默认加载空间】+ 带偏 agent。CC 的 MEMORY.md 前 200 行/25KB 逐字加载、
超出即丢(claude-code-guide 查官方 docs 钉死)——这就是"垃圾占默认加载空间"的物理根源。

三级分层(Master数分口径):
  ① active   —— 默认加载(在 MEMORY.md 索引里·CC 每会话读前 200 行·recall 按需读)= 当前有用正确。
  ② historical(历史空间)—— 归档:CC 默认【够不着/不 recall/不注入】·需显式命令才查。
                            只收 superseded-but-valuable(当时对·现过时);actively-wrong=垃圾走 ③。
  ③ deleted  —— 垃圾硬删(purge·留墓碑 DELETED.md·内容不可回)。

★成败关键(CC recall 机制·文档钉死):
  - CC 只 recall【MEMORY.md 索引里】的文件(不在索引=recall 看不见);且只扫 `<proj>/memory/`,
    【不扫兄弟/父目录】(子目录排除=未文档化不可靠·兄弟目录=100% 文档确认不扫)。
  → 故历史空间 = memory 库【外】的兄弟目录 `<proj>/memory-archive/`(仿 session-archive 冷归档范式),
    robust-by-construction 只依赖文档化 scoping。
  → 归档=【两步都要】:① .md 移到 memory-archive/(recall 够不着)+ ② 从 MEMORY.md【删该指针片段】
    (这步才真腾 200 行/25KB 加载预算;缺 ② 只是文件搬走、索引行还占预算=没腾到空间)。

授权门(Master数分定·轻门 v1):默认不查=满足"未授权不默认查阅";agent 显式跑 `memory-history recall/show`
  即【有意识 opt-in=授权动作】(非被动污染)·无需人批。护栏:读历史结果【必标注"历史·superseded·
  非当前有效"】防二次带偏;只收 superseded(非错的·只是旧)故查历史相对安全。轻门是可调旋钮,
  用户要强门(人批)后续收紧。

留痕(数据真实性·不伪装):归档写 HISTORY.md 一行(何时/为何/谁/原 description/★原 MEMORY.md 索引片段
  原文供 revive 回填);归档【可逆】(restore=文件移回 + 索引片段回填);purge 写 DELETED.md 墓碑。

平台化:先业务库(--lib 默认=config memory_index 的库);机制通用·任意记忆库可 --lib 指定。

命令:
  ab memory-history archive <name> --reason ... [--by <role>] [--lib <dir>]
  ab memory-history restore <name> [--lib <dir>]
  ab memory-history list [--lib <dir>]
  ab memory-history show <name> [--lib <dir>]
  ab memory-history recall <query> [--lib <dir>]
  ab memory-history purge  <name> --reason ... [--by <role>] [--from-active] [--lib <dir>]
"""
import json, os, re, shutil, time
from pathlib import Path
from bus.config import cfg, _now

try:
    from bus.io import _audit
except Exception:                       # _audit 缺失时降级为 no-op(不阻塞主流程)
    def _audit(*a, **k): pass

SUPERSEDED_BANNER = "⚠【历史·superseded·非当前有效】此为归档记忆(当时对·现可能过时)·勿当 current 真相·核对现状再用。"
_LINK_RE_TPL = r'\[[^\]]*\]\(%s\.md\)'          # [Title](name.md)
_ANY_LINK_RE = re.compile(r'\[[^\]]*\]\([^)]*\.md\)')


# ── 库/路径解析 ───────────────────────────────────────────────────────────
def _resolve_lib(args):
    """--lib 解析:传 memory 目录 或 其父项目目录 均可;缺省=业务库(config memory_index 的库)。"""
    lib = getattr(args, "lib", None)
    if lib:
        p = Path(lib)
        if (p / "MEMORY.md").exists():
            return p
        if (p / "memory" / "MEMORY.md").exists():
            return p / "memory"
        return p
    mi = cfg("memory_index")
    if not mi:
        raise SystemExit("无 --lib 且 config.memory_index 未配置,无法定位记忆库")
    return Path(mi).parent


def _archive_dir(lib_dir):
    """历史空间 = memory 库【外】的兄弟目录(CC 不扫)。lib_dir=<proj>/memory → <proj>/memory-archive。"""
    return lib_dir.parent / "memory-archive"


def _index_files(lib_dir):
    """库内所有【索引文件】(根 MEMORY.md + 各角色 *.MEMORY.md)——归档要从全部索引里剔指针。
    只有根 MEMORY.md 是 CC 每会话必载(前 200 行);角色索引按需读·一并剔保持无悬空指针。"""
    root = lib_dir / "MEMORY.md"
    files = [root] if root.exists() else []
    for p in sorted(lib_dir.glob("*.MEMORY.md")):
        files.append(p)
    return files


# ── 索引指针外科式剔除/回填(核心·处理密集多记忆挤一行)────────────────────────
def _excise_pointer(text, name):
    """从一份索引文本移除对 <name>.md 的指针。返回 (new_text, excised_fragment|None)。
    两种格式都处理:①一行一记忆→整行删;②密集多记忆挤一行(` · ` 分隔)→只切目标片段、余者以 ` · ` 重接。
    片段边界=从本链接 [ 起,到下一 [Title](*.md) 链接前(对 hook 内可能含的裸『·』鲁棒·不误切)。"""
    link_re = re.compile(_LINK_RE_TPL % re.escape(name))
    out, excised = [], None
    for line in text.split("\n"):
        if excised is not None or not link_re.search(line):
            out.append(line); continue
        links = list(_ANY_LINK_RE.finditer(line))
        if len(links) <= 1:                      # 一行一记忆 → 整行删
            excised = re.sub(r"^\s*-\s*", "", line).strip()
            continue
        # 密集行 → 外科切
        prefix_m = re.match(r"^(\s*-\s*)", line)
        prefix = prefix_m.group(1) if prefix_m else ""
        body = line[len(prefix):]
        blinks = list(_ANY_LINK_RE.finditer(body))
        frags = []
        for i, lm in enumerate(blinks):
            end = blinks[i + 1].start() if i + 1 < len(blinks) else len(body)
            frags.append(body[lm.start():end])
        tre = re.compile(_LINK_RE_TPL % re.escape(name))
        keep, tgt = [], None
        for f in frags:
            fs = f.strip()
            fs = re.sub(r"[·\s]+$", "", fs).strip()   # 去尾部分隔符/空白
            if tgt is None and tre.search(f):
                tgt = fs
            else:
                keep.append(fs)
        excised = tgt
        out.append((prefix + " · ".join(keep)).rstrip())
    return "\n".join(out), excised


def _reinsert_pointer(text, fragment):
    """revive 回填:把归档时存的索引片段以独立行 `- <fragment>` 加回(加在最后一条 `- ` 列表项之后·
    保留原 hook/title;不强求原行分组位置——数据真实性只要求指针回来·可 recall,不伪装原位)。"""
    frag = (fragment or "").strip()
    if not frag:
        return text
    lines = text.split("\n")
    last = -1
    for i, ln in enumerate(lines):
        if ln.lstrip().startswith("- "):
            last = i
    newline = "- " + frag
    if last >= 0:
        lines.insert(last + 1, newline)
    else:
        lines.append(newline)
    return "\n".join(lines)


def _read_desc(md_path):
    """读一份记忆 .md 的 frontmatter description(供留痕)。失败返 ''。"""
    try:
        t = md_path.read_text(encoding="utf-8")
    except Exception:
        return ""
    m = re.search(r"^description:\s*(.+)$", t, re.MULTILINE)
    return m.group(1).strip() if m else ""


# ── HISTORY.md / DELETED.md 留痕 ───────────────────────────────────────────
_HISTORY_HEADER = (
    "# 历史记忆空间(memory-archive)· 归档索引\n"
    "> 归档=superseded-but-valuable(当时对·现过时)。此目录在 memory 库【外】·CC 默认 recall/注入【够不着】。\n"
    "> 读取(显式授权):`ab memory-history show <name>` / `recall <query>`(结果标注 superseded)。\n"
    "> 可逆:`ab memory-history restore <name>`(文件移回 memory/ + 索引片段回填)。\n"
    "> actively-wrong=垃圾·不进此空间(`purge` 硬删·墓碑见 DELETED.md)。\n\n"
    "## 归档清单(每条:name · 何时 · 谁 · 因 · 原description · 原索引片段)\n"
)


def _append_history(archive_dir, name, by, reason, desc, fragment):
    hf = archive_dir / "HISTORY.md"
    if not hf.exists():
        hf.write_text(_HISTORY_HEADER, encoding="utf-8")
    row = "- **%s** · %s · by %s · 因:%s · 原description:%s · 原索引片段:`%s`\n" % (
        name, _now(), by or "?", (reason or "").strip() or "(未注明)",
        (desc or "").strip() or "(无)", (fragment or "").strip() or "(无·原不在索引)")
    with open(hf, "a", encoding="utf-8") as f:
        f.write(row)


def _find_history_fragment(archive_dir, name):
    """从 HISTORY.md 找 <name> 归档行里存的『原索引片段』(供 restore 回填)。返回 fragment|None。"""
    hf = archive_dir / "HISTORY.md"
    if not hf.exists():
        return None
    for ln in hf.read_text(encoding="utf-8").split("\n"):
        if ln.startswith("- **%s**" % name):
            m = re.search(r"原索引片段:`(.*?)`", ln)
            if m:
                frag = m.group(1)
                return None if frag in ("", "(无·原不在索引)") else frag
    return None


def _log_restore(archive_dir, name, by):
    hf = archive_dir / "HISTORY.md"
    if hf.exists():
        with open(hf, "a", encoding="utf-8") as f:
            f.write("  ↩ 已恢复(restore) **%s** · %s · by %s(文件移回 memory/·索引回填)\n" % (name, _now(), by or "?"))


def _tombstone(archive_dir, name, by, reason, desc):
    df = archive_dir / "DELETED.md"
    if not df.exists():
        df.write_text("# 硬删墓碑(DELETED)· 垃圾记忆 purge 留痕\n"
                      "> actively-wrong/误导垃圾硬删记录(内容已不可回·仅存墓碑=数据真实性不假装没删过)。\n\n",
                      encoding="utf-8")
    with open(df, "a", encoding="utf-8") as f:
        f.write("- **%s** · purged %s · by %s · 因:%s · 原description:%s\n" % (
            name, _now(), by or "?", (reason or "").strip() or "(未注明)", (desc or "").strip() or "(无)"))


# ── 命令入口 ───────────────────────────────────────────────────────────────
def cmd_memory_history(args):
    sub = getattr(args, "mh_cmd", None)
    lib = _resolve_lib(args)
    arch = _archive_dir(lib)
    if sub == "archive":
        return _do_archive(args, lib, arch)
    if sub == "restore":
        return _do_restore(args, lib, arch)
    if sub == "list":
        return _do_list(args, lib, arch)
    if sub == "show":
        return _do_show(args, lib, arch)
    if sub == "recall":
        return _do_recall(args, lib, arch)
    if sub == "purge":
        return _do_purge(args, lib, arch)
    print("用法:ab memory-history {archive|restore|list|show|recall|purge} ...")


def _do_archive(args, lib, arch):
    name = args.name
    src = lib / (name + ".md")
    if not src.exists():
        raise SystemExit("✗ 记忆不存在:%s" % src)
    arch.mkdir(parents=True, exist_ok=True)
    desc = _read_desc(src)
    # ② 从所有索引文件剔指针(捕获根 MEMORY.md 的片段供 revive 回填)
    root_fragment = None
    for idx in _index_files(lib):
        text = idx.read_text(encoding="utf-8")
        new_text, excised = _excise_pointer(text, name)
        if excised is not None:
            if idx.name == "MEMORY.md":
                root_fragment = excised
            elif root_fragment is None:
                root_fragment = excised
            idx.write_text(new_text, encoding="utf-8")
    # ① 文件移到库外兄弟归档目录
    dst = arch / (name + ".md")
    shutil.move(str(src), str(dst))
    _append_history(arch, name, getattr(args, "by", None), getattr(args, "reason", None), desc, root_fragment)
    _audit("memory_archive", name=name, lib=str(lib), by=getattr(args, "by", None))
    print("✓ 已归档【%s】→ %s" % (name, dst))
    print("  · 索引指针已剔(片段:%s)" % ((root_fragment or "(原不在索引)")[:80]))
    print("  · 可逆:ab memory-history restore %s%s" % (name, _lib_flag(args)))


def _do_restore(args, lib, arch):
    name = args.name
    src = arch / (name + ".md")
    if not src.exists():
        raise SystemExit("✗ 历史空间无此记忆:%s" % src)
    dst = lib / (name + ".md")
    if dst.exists():
        raise SystemExit("✗ active 库已存在同名(冲突,先处理):%s" % dst)
    fragment = _find_history_fragment(arch, name)
    shutil.move(str(src), str(dst))
    if fragment:
        root = lib / "MEMORY.md"
        if root.exists():
            root.write_text(_reinsert_pointer(root.read_text(encoding="utf-8"), fragment), encoding="utf-8")
    _log_restore(arch, name, getattr(args, "by", None))
    _audit("memory_restore", name=name, lib=str(lib))
    print("✓ 已恢复【%s】→ %s%s" % (name, dst, "(索引片段已回填)" if fragment else "(原无索引片段·未回填)"))


def _do_list(args, lib, arch):
    hf = arch / "HISTORY.md"
    if not hf.exists():
        print("(历史空间为空:%s)" % arch); return
    rows = [ln for ln in hf.read_text(encoding="utf-8").split("\n") if ln.startswith("- **")]
    print("历史记忆空间 %s · 共 %d 条:" % (arch, len(rows)))
    for r in rows:
        print("  " + r[2:])


def _do_show(args, lib, arch):
    name = args.name
    src = arch / (name + ".md")
    if not src.exists():
        raise SystemExit("✗ 历史空间无此记忆:%s" % src)
    _audit("memory_history_read", name=name, mode="show")
    print(SUPERSEDED_BANNER)
    print("─" * 60)
    print(src.read_text(encoding="utf-8"))


def _do_recall(args, lib, arch):
    q = (args.query or "").strip()
    if not arch.exists():
        print("(历史空间为空)"); return
    hits = []
    for p in sorted(arch.glob("*.md")):
        if p.name in ("HISTORY.md", "DELETED.md"):
            continue
        try:
            t = p.read_text(encoding="utf-8")
        except Exception:
            continue
        if q.lower() in t.lower() or q.lower() in p.stem.lower():
            desc = _read_desc(p)
            hits.append((p.stem, desc))
    _audit("memory_history_read", query=q, mode="recall", hits=len(hits))
    print(SUPERSEDED_BANNER)
    print("─" * 60)
    if not hits:
        print("历史空间无匹配【%s】的归档记忆。" % q); return
    print("历史空间匹配【%s】%d 条(superseded·非当前有效·show 看全文):" % (q, len(hits)))
    for name, desc in hits:
        print("  · %s — %s" % (name, (desc or "")[:100]))


def _do_purge(args, lib, arch):
    """③ deleted 硬删(垃圾·actively-wrong)。默认从历史空间 purge;--from-active 直接删 active 垃圾。
    先写墓碑再 rm(数据真实性:内容不可回但留删除记录)。"""
    name = args.name
    from_active = getattr(args, "from_active", False)
    base = lib if from_active else arch
    src = base / (name + ".md")
    if not src.exists():
        raise SystemExit("✗ 无此记忆:%s" % src)
    desc = _read_desc(src)
    arch.mkdir(parents=True, exist_ok=True)
    if from_active:                          # active 垃圾:先剔索引指针
        for idx in _index_files(lib):
            text = idx.read_text(encoding="utf-8")
            new_text, excised = _excise_pointer(text, name)
            if excised is not None:
                idx.write_text(new_text, encoding="utf-8")
    _tombstone(arch, name, getattr(args, "by", None), getattr(args, "reason", None), desc)
    src.unlink()
    _audit("memory_purge", name=name, lib=str(lib), from_active=from_active)
    print("✓ 已硬删【%s】(墓碑留 DELETED.md·内容不可回)" % name)


def _lib_flag(args):
    lib = getattr(args, "lib", None)
    return (" --lib %s" % lib) if lib else ""
