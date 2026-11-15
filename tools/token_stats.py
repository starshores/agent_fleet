"""SQLite-backed incremental ETL for Claude Code token usage.

存储(均可用环境变量改):
  ~/.claude/projects/                  -- jsonl 扫描根 (AGENTBUS_PROJECTS_DIR)
  ~/.claude/agent_bus/usage.db         -- fact: messages + cursor: scan_cursor (AGENTBUS_USAGE_DB)
  ~/.claude/projects/usage_config.json -- alias / merge / ignore (人可手编)

模式:
  python token_stats.py                # 增量 ETL + 表格输出
  python token_stats.py --json         # 增量 ETL + JSON (BI 接入)
  python token_stats.py --json --days N  # JSON 但 daily 只给最近 N 天 (默认全部)
  python token_stats.py --etl          # 仅跑 ETL 不输出 (cron 用)
  python token_stats.py --rebuild      # 删库 + 全量重建
  python token_stats.py --reconcile    # 用旧版全扫对账 (需同目录 token_stats_legacy.py,
                                        #   该旧版脚本不随仓提供)

去重: messages.message_id PRIMARY KEY + INSERT OR IGNORE (对应 jsonl 中 Claude Code 把
同一 API response 写成多条 tool_use 块, usage 重复)。
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

# jsonl 扫描根 (口径归本脚本;默认宿主 ~/.claude/projects,可换机/换用户)
ROOT = Path(os.path.expandvars(os.path.expanduser(
    os.environ.get("AGENTBUS_PROJECTS_DIR") or str(Path.home() / ".claude" / "projects"))))

# DB 存储路径优先级: 环境变量 AGENTBUS_USAGE_DB > bus config.json 的 "usage_db" 键
# > 默认 <AGENTBUS_DIR>/usage.db(默认 ~/.claude/agent_bus/usage.db)
# (用户 2026-06-26 定板: 存储归 bus 作 SSoT, 扫描/口径仍归 token_stats 维护)
_BUS_DIR = Path(os.path.expandvars(os.path.expanduser(
    os.environ.get("AGENTBUS_DIR") or str(Path.home() / ".claude" / "agent_bus"))))
_BUS_CONFIG_PATH = _BUS_DIR / "config.json"
_DEFAULT_DB = _BUS_DIR / "usage.db"


def _as_path(value):
    return Path(os.path.expandvars(os.path.expanduser(str(value))))


def _resolve_db_path():
    env = os.environ.get("AGENTBUS_USAGE_DB")
    if env:
        return _as_path(env)
    if _BUS_CONFIG_PATH.exists():
        try:
            cfg = json.loads(_BUS_CONFIG_PATH.read_text(encoding="utf-8"))
            v = cfg.get("usage_db")
            if v:
                return _as_path(v)
        except Exception as e:
            sys.stderr.write(f"[warn] bus config.json parse error: {e}\n")
    return _DEFAULT_DB


DB_PATH = _resolve_db_path()
# usage_config.json (口径配置, 留在 projects/ — 由本脚本管, 跟 bus 存储位置无关)
CFG_PATH = ROOT / "usage_config.json"

PRICING = {
    # Opus 4.5+ 官方现行价 $5/$25(cache读 $0.50)。2026-07-06 核官方 platform.claude.com 修正:
    # 原表误用 Opus 4.1/Opus4 老档 $15/$75/$1.50(恰 3× 现行)→ cost_usd 全表虚高 3×。
    "claude-opus-4-8":    {"in":  5.0, "cw5":  6.25, "cw1h": 10.0, "cr": 0.50, "out": 25.0},
    "claude-opus-4-7":    {"in":  5.0, "cw5":  6.25, "cw1h": 10.0, "cr": 0.50, "out": 25.0},
    "claude-opus-4-6":    {"in":  5.0, "cw5":  6.25, "cw1h": 10.0, "cr": 0.50, "out": 25.0},
    "claude-opus-4-5":    {"in":  5.0, "cw5":  6.25, "cw1h": 10.0, "cr": 0.50, "out": 25.0},
    "claude-sonnet-4-6":  {"in":  3.0, "cw5":  3.75, "cw1h":  6.0, "cr": 0.30, "out": 15.0},
    "claude-sonnet-4-5":  {"in":  3.0, "cw5":  3.75, "cw1h":  6.0, "cr": 0.30, "out": 15.0},
    "claude-haiku-4-5":   {"in":  1.0, "cw5":  1.25, "cw1h":  2.0, "cr": 0.10, "out":  5.0},
    "<synthetic>":        {"in":  0.0, "cw5":  0.0,  "cw1h":  0.0, "cr": 0.0,  "out":  0.0},
}


def price_for(model):
    m = (model or "").lower()
    for k, p in PRICING.items():
        if k in m:
            return p
    return PRICING["claude-opus-4-7"]


def open_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(str(DB_PATH))
    db.executescript("""
        CREATE TABLE IF NOT EXISTS messages (
            message_id   TEXT PRIMARY KEY,
            session_id   TEXT,
            project_raw  TEXT,
            model        TEXT,
            ts_utc       TEXT,
            sh_date      TEXT,
            input        INTEGER,
            output       INTEGER,
            cache_read   INTEGER,
            cache_w5m    INTEGER,
            cache_w1h    INTEGER,
            cost_usd     REAL,
            src_file     TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_sh_date     ON messages(sh_date);
        CREATE INDEX IF NOT EXISTS idx_project_raw ON messages(project_raw);
        CREATE INDEX IF NOT EXISTS idx_model       ON messages(model);
        CREATE INDEX IF NOT EXISTS idx_session     ON messages(session_id);

        CREATE TABLE IF NOT EXISTS scan_cursor (
            path         TEXT PRIMARY KEY,
            mtime        REAL,
            size         INTEGER,
            byte_offset  INTEGER,
            last_scanned TEXT
        );
    """)
    db.commit()
    return db


def parse_record(rec, project_raw, src_file):
    """jsonl 单行 dict → message row tuple,或 None。"""
    msg = rec.get("message") or {}
    usage = msg.get("usage")
    if not usage:
        return None
    mid = msg.get("id")
    if not mid:
        rid = rec.get("requestId")
        if not rid:
            return None
        mid = "rid:" + rid

    model = msg.get("model", "unknown")
    ts_utc = rec.get("timestamp", "")
    try:
        t = datetime.fromisoformat(ts_utc.replace("Z", "+00:00"))
        sh_date = (t + timedelta(hours=8)).strftime("%Y-%m-%d")
    except Exception:
        sh_date = "unknown"

    in_t  = usage.get("input_tokens", 0) or 0
    out_t = usage.get("output_tokens", 0) or 0
    cr    = usage.get("cache_read_input_tokens", 0) or 0
    cw    = usage.get("cache_creation_input_tokens", 0) or 0
    cwd   = usage.get("cache_creation") or {}
    cw5   = cwd.get("ephemeral_5m_input_tokens", 0) or 0
    cw1h  = cwd.get("ephemeral_1h_input_tokens", 0) or 0
    if cw5 + cw1h == 0:
        cw5 = cw

    p = price_for(model)
    cost = (in_t * p["in"] + out_t * p["out"] + cr * p["cr"]
            + cw5 * p["cw5"] + cw1h * p["cw1h"]) / 1_000_000

    return (
        mid,
        rec.get("sessionId"),
        project_raw,
        model,
        ts_utc,
        sh_date,
        in_t, out_t, cr, cw5, cw1h,
        cost,
        src_file,
    )


def run_etl(db, verbose=False):
    """增量扫描所有 jsonl, INSERT OR IGNORE 进 messages, 更新 scan_cursor。
    返回 dict: new_msgs / scanned_files / skipped_files / truncated_files / elapsed_seconds。
    """
    t0 = time.monotonic()
    cursors = {row[0]: (row[1], row[2], row[3])
               for row in db.execute("SELECT path, mtime, size, byte_offset FROM scan_cursor")}
    new_msgs = 0
    scanned = 0
    skipped = 0
    truncated = 0

    cur = db.cursor()
    batch = []
    cursor_updates = []

    for f in ROOT.rglob("*.jsonl"):
        path = str(f)
        st = f.stat()
        prev = cursors.get(path)
        if prev:
            old_mtime, old_size, old_offset = prev
            if st.st_mtime <= old_mtime and st.st_size == old_size:
                skipped += 1
                continue
            if st.st_size < old_size:
                truncated += 1
                start_offset = 0
            else:
                start_offset = old_offset
        else:
            start_offset = 0

        scanned += 1
        project_raw = f.parent.name
        src_file = f.name
        last_complete = start_offset

        with f.open("rb") as fp:
            fp.seek(start_offset)
            for raw_line in fp:
                if not raw_line.endswith(b"\n"):
                    # 半行,留给下次扫(不要 commit offset 过它)
                    break
                last_complete += len(raw_line)
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                row = parse_record(rec, project_raw, src_file)
                if row:
                    batch.append(row)
                    if len(batch) >= 500:
                        ins = cur.executemany(
                            "INSERT OR IGNORE INTO messages "
                            "(message_id,session_id,project_raw,model,ts_utc,sh_date,"
                            "input,output,cache_read,cache_w5m,cache_w1h,cost_usd,src_file) "
                            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", batch)
                        new_msgs += ins.rowcount
                        batch.clear()

        cursor_updates.append((
            path, st.st_mtime, st.st_size, last_complete,
            datetime.now(timezone.utc).isoformat()
        ))

    if batch:
        ins = cur.executemany(
            "INSERT OR IGNORE INTO messages "
            "(message_id,session_id,project_raw,model,ts_utc,sh_date,"
            "input,output,cache_read,cache_w5m,cache_w1h,cost_usd,src_file) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", batch)
        new_msgs += ins.rowcount
        batch.clear()

    cur.executemany(
        "INSERT OR REPLACE INTO scan_cursor (path,mtime,size,byte_offset,last_scanned) "
        "VALUES (?,?,?,?,?)", cursor_updates)
    db.commit()

    elapsed = time.monotonic() - t0
    info = {
        "new_msgs": new_msgs,
        "scanned_files": scanned,
        "skipped_files": skipped,
        "truncated_files": truncated,
        "elapsed_seconds": round(elapsed, 3),
    }
    if verbose:
        sys.stderr.write(f"ETL: {info}\n")
    return info


def load_config():
    if CFG_PATH.exists():
        try:
            cfg = json.loads(CFG_PATH.read_text(encoding="utf-8"))
        except Exception as e:
            sys.stderr.write(f"[warn] usage_config.json parse error: {e}\n")
            cfg = {}
    else:
        cfg = {}
    cfg.setdefault("alias", {})
    cfg.setdefault("merge", {})
    cfg.setdefault("ignore", {})
    cfg["ignore"].setdefault("projects", [])
    cfg["ignore"].setdefault("sessions", [])
    cfg["ignore"].setdefault("cwd_regex", [])
    return cfg


def project_to_logical(raw, cfg):
    """raw project 名 → (logical_name 或 None);None = ignored。"""
    if raw in (cfg.get("ignore") or {}).get("projects", []):
        return None
    for logical, members in (cfg.get("merge") or {}).items():
        if raw in members:
            return logical
    return (cfg.get("alias") or {}).get(raw, raw)


def aggregate_for_json(db, cfg, days=None):
    """从 db 聚合出 BI 期望的 JSON 结构。"""
    ignore_projects = set((cfg.get("ignore") or {}).get("projects") or [])
    ignore_sessions = set((cfg.get("ignore") or {}).get("sessions") or [])

    where = ["1=1"]
    params = []
    if ignore_projects:
        where.append("project_raw NOT IN (" + ",".join("?" * len(ignore_projects)) + ")")
        params.extend(ignore_projects)
    if ignore_sessions:
        where.append("session_id NOT IN (" + ",".join("?" * len(ignore_sessions)) + ")")
        params.extend(ignore_sessions)
    w = " AND ".join(where)

    # totals
    row = db.execute(f"""
        SELECT COUNT(*), COALESCE(SUM(cost_usd),0),
               COALESCE(SUM(input),0), COALESCE(SUM(output),0),
               COALESCE(SUM(cache_read),0),
               COALESCE(SUM(cache_w5m),0), COALESCE(SUM(cache_w1h),0)
        FROM messages WHERE {w}
    """, params).fetchone()
    totals = {
        "calls": row[0],
        "cost_usd": round(row[1], 4),
        "input": row[2], "output": row[3],
        "cache_read": row[4],
        "cache_write_5m": row[5], "cache_write_1h": row[6],
    }

    # token 分项 SELECT 片段 (与 totals 同口径):input/output/cache_read/cache_w5m/cache_w1h
    # 衍生:tokens_io = input+output;tokens_total = 五项之和
    token_cols = (
        "COALESCE(SUM(input),0), COALESCE(SUM(output),0), "
        "COALESCE(SUM(cache_read),0), "
        "COALESCE(SUM(cache_w5m),0), COALESCE(SUM(cache_w1h),0)"
    )

    def _row_tokens(r_in, r_out, r_cr, r_cw5, r_cw1h):
        io = (r_in or 0) + (r_out or 0)
        total = io + (r_cr or 0) + (r_cw5 or 0) + (r_cw1h or 0)
        return {
            "input": r_in or 0, "output": r_out or 0,
            "cache_read": r_cr or 0,
            "cache_write_5m": r_cw5 or 0, "cache_write_1h": r_cw1h or 0,
            "tokens_io": io, "tokens_total": total,
        }

    # daily
    daily_where = w
    daily_params = list(params)
    if days:
        sh_now = (datetime.now(timezone.utc) + timedelta(hours=8))
        cutoff = (sh_now - timedelta(days=days)).strftime("%Y-%m-%d")
        daily_where += " AND sh_date >= ?"
        daily_params.append(cutoff)
    daily = []
    for r in db.execute(
        f"SELECT sh_date, COUNT(*), SUM(cost_usd), {token_cols} "
        f"FROM messages WHERE {daily_where} "
        f"GROUP BY sh_date ORDER BY sh_date", daily_params):
        item = {"date": r[0], "calls": r[1], "cost_usd": round(r[2] or 0, 4)}
        item.update(_row_tokens(r[3], r[4], r[5], r[6], r[7]))
        daily.append(item)

    # 连续日期轴补零(用户令 2026-08-10:历史用量要看到每天,缺记录的日子补 0 显示而非整天跳过)。
    # 口径(数据真实性):仅在【已有数据的最早~最晚区间内】补——区间内某日无记录=该日真无使用
    #   (ETL 增量幂等·有用量必入库·相邻日期均有数据证明当时 ETL 在跑,故是【已知的零】非丢失/未知)。
    #   补的行标 src='zero'(区别 messages 真测行),保持可溯源;不外插区间外、不估算、不碰 totals。
    if daily:
        _present = {d["date"] for d in daily}
        _zero_tok = _row_tokens(0, 0, 0, 0, 0)
        _lo = datetime.strptime(daily[0]["date"], "%Y-%m-%d").date()
        _hi = datetime.strptime(daily[-1]["date"], "%Y-%m-%d").date()
        _cur = _lo
        while _cur <= _hi:
            _ds = _cur.isoformat()
            if _ds not in _present:
                _z = {"date": _ds, "calls": 0, "cost_usd": 0.0, "src": "zero"}
                _z.update(_zero_tok)
                daily.append(_z)
            _cur += timedelta(days=1)
        daily.sort(key=lambda x: x["date"])

    # by_project_raw (原始 cwd 目录)
    by_project_raw = []
    for r in db.execute(
        f"SELECT project_raw, COUNT(*), SUM(cost_usd), {token_cols} "
        f"FROM messages WHERE {w} "
        f"GROUP BY project_raw ORDER BY SUM(cost_usd) DESC", params):
        item = {"project": r[0], "calls": r[1], "cost_usd": round(r[2] or 0, 4)}
        item.update(_row_tokens(r[3], r[4], r[5], r[6], r[7]))
        by_project_raw.append(item)

    # by_project (套 alias/merge) — sum 所有 token 字段一并累加
    _TOKEN_FIELDS = ("input", "output", "cache_read", "cache_write_5m",
                     "cache_write_1h", "tokens_io", "tokens_total")
    by_logical = defaultdict(lambda: {"calls": 0, "cost_usd": 0.0,
                                       **{k: 0 for k in _TOKEN_FIELDS}})
    for r in by_project_raw:
        logical = project_to_logical(r["project"], cfg)
        if logical is None:
            continue
        by_logical[logical]["calls"] += r["calls"]
        by_logical[logical]["cost_usd"] += r["cost_usd"]
        for k in _TOKEN_FIELDS:
            by_logical[logical][k] += r[k]
    by_project = sorted(
        [{"project": k, "calls": v["calls"],
          "cost_usd": round(v["cost_usd"], 4),
          **{tk: v[tk] for tk in _TOKEN_FIELDS}}
         for k, v in by_logical.items()],
        key=lambda x: -x["cost_usd"]
    )

    # by_month_model
    by_month_model = [
        {"month": r[0], "model": r[1], "calls": r[2], "cost_usd": round(r[3] or 0, 4)}
        for r in db.execute(
            f"SELECT substr(ts_utc,1,7) AS month, model, COUNT(*), SUM(cost_usd) "
            f"FROM messages WHERE {w} GROUP BY month, model ORDER BY month, model",
            params)
    ]

    # meta
    cursor_count = db.execute("SELECT COUNT(*) FROM scan_cursor").fetchone()[0]

    return {
        "totals": totals,
        "daily": daily,
        "by_project": by_project,
        "by_project_raw": by_project_raw,
        "by_month_model": by_month_model,
        "meta": {
            "scan_root": str(ROOT),
            "db_path": str(DB_PATH),
            "config_path": str(CFG_PATH) if CFG_PATH.exists() else None,
            "tracked_jsonl_files": cursor_count,
            "alias_count": len(cfg.get("alias") or {}),
            "merge_groups": len(cfg.get("merge") or {}),
            "ignore_projects": len(ignore_projects),
            "ignore_sessions": len(ignore_sessions),
        },
    }


def output_json(db, cfg, etl_info, days=None):
    data = aggregate_for_json(db, cfg, days=days)
    data["as_of"] = datetime.now().strftime("%Y-%m-%dT%H:%M")
    data["etl"] = etl_info
    sys.stdout.write(json.dumps(data, ensure_ascii=False, separators=(",", ":")))
    sys.stdout.write("\n")
    sys.stdout.flush()


def output_table(db, cfg, etl_info):
    data = aggregate_for_json(db, cfg, days=None)
    totals = data["totals"]
    meta = data["meta"]

    def fmt_n(x):  return f"{int(x):>15,}"
    def fmt_usd(x): return f"${x:>10,.2f}"

    print(f"=== Claude Code token usage (SQLite-backed) ===")
    print(f"DB:          {meta['db_path']}")
    print(f"tracked jsonl: {meta['tracked_jsonl_files']}")
    print(f"ETL: new={etl_info['new_msgs']} scanned={etl_info['scanned_files']} "
          f"skipped={etl_info['skipped_files']} truncated={etl_info['truncated_files']} "
          f"elapsed={etl_info['elapsed_seconds']}s")
    if meta.get("config_path"):
        print(f"config: {meta['config_path']}  "
              f"(alias={meta['alias_count']} merge={meta['merge_groups']} "
              f"ignore_proj={meta['ignore_projects']} ignore_sess={meta['ignore_sessions']})")
    print()
    print(f"TOTAL:")
    print(f"  calls         {totals['calls']:>15,}")
    print(f"  input         {totals['input']:>15,}")
    print(f"  output        {totals['output']:>15,}")
    print(f"  cache_read    {totals['cache_read']:>15,}")
    print(f"  cache_w_5m    {totals['cache_write_5m']:>15,}")
    print(f"  cache_w_1h    {totals['cache_write_1h']:>15,}")
    print(f"  cost(USD)    {fmt_usd(totals['cost_usd'])}")
    print()
    print(f"### By project (logical, after alias/merge/ignore)")
    print(f"{'project':<55} {'calls':>8} {'cost(USD)':>12}")
    for r in data["by_project"]:
        print(f"{str(r['project'])[:55]:<55} {r['calls']:>8,} {fmt_usd(r['cost_usd'])}")
    print()
    print(f"### By month x model")
    print(f"{'month':<10} {'model':<35} {'calls':>8} {'cost(USD)':>12}")
    for r in data["by_month_model"]:
        print(f"{r['month']:<10} {str(r['model'])[:35]:<35} {r['calls']:>8,} {fmt_usd(r['cost_usd'])}")


def run_reconcile(db, cfg):
    """跟旧版全扫对账,差额应≈0。
    注意: legacy 没有 ignore/alias/merge 概念,reconcile 必须 bypass config 才公平。
    """
    sys.stderr.write("[reconcile] 跑旧版全扫...\n")
    import importlib.util
    legacy_path = Path(__file__).resolve().with_name("token_stats_legacy.py")
    if not legacy_path.exists():
        print("ERROR: token_stats_legacy.py 不存在(该旧版脚本不随仓提供),无法对账", file=sys.stderr)
        return 1
    spec = importlib.util.spec_from_file_location("token_stats_legacy", str(legacy_path))
    legacy = importlib.util.module_from_spec(spec)
    # 拦截 stdout 避免噪音
    old_stdout = sys.stdout
    sys.stdout = open(__import__("os").devnull, "w", encoding="utf-8")
    try:
        spec.loader.exec_module(legacy)
        old_data = legacy.scan_all()
    finally:
        sys.stdout.close()
        sys.stdout = old_stdout

    # 对账走空 config,bypass ignore/alias/merge,跟 legacy 同口径
    empty_cfg = {"alias": {}, "merge": {},
                 "ignore": {"projects": [], "sessions": [], "cwd_regex": []}}
    new_data = aggregate_for_json(db, empty_cfg)
    old_totals = old_data["totals"]
    new_totals = new_data["totals"]

    print("=== Reconcile: legacy full-scan vs SQLite ETL ===")
    for k_legacy, k_new in [
        ("msgs", "calls"), ("cost_usd", "cost_usd"),
        ("input", "input"), ("output", "output"),
        ("cache_read", "cache_read"),
        ("cache_write_5m", "cache_write_5m"),
        ("cache_write_1h", "cache_write_1h"),
    ]:
        ov = old_totals.get(k_legacy, 0)
        nv = new_totals.get(k_new, 0)
        diff = nv - ov
        pct = (diff / ov * 100) if ov else 0
        flag = "✓" if abs(diff) < 0.01 or (isinstance(ov, (int,float)) and abs(diff) < max(1, ov*0.0001)) else "⚠"
        print(f"  {flag} {k_new:<16} legacy={ov:>18,.4f}  etl={nv:>18,.4f}  diff={diff:>+12,.4f} ({pct:+.4f}%)")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true", help="JSON 输出 (BI 接入)")
    ap.add_argument("--days", type=int, default=None, help="daily 限制天数 (默认全部)")
    ap.add_argument("--etl", action="store_true", help="仅跑 ETL,无输出 (cron 用)")
    ap.add_argument("--rebuild", action="store_true", help="删库 + 全量重建")
    ap.add_argument("--reconcile", action="store_true", help="跟旧版全扫对账,差额应≈0")
    ap.add_argument("--no-etl", action="store_true", help="不跑 ETL,直接出 db 现有数据")
    ap.add_argument("--verbose", action="store_true", help="ETL 进度打到 stderr")
    args = ap.parse_args()

    if args.rebuild and DB_PATH.exists():
        DB_PATH.unlink()
        if args.verbose:
            sys.stderr.write(f"[rebuild] removed {DB_PATH}\n")

    db = open_db()
    cfg = load_config()

    if args.no_etl:
        etl_info = {"new_msgs": 0, "scanned_files": 0, "skipped_files": 0,
                    "truncated_files": 0, "elapsed_seconds": 0.0}
    else:
        etl_info = run_etl(db, verbose=args.verbose)

    if args.etl:
        # 只做 ETL,不输出聚合
        sys.stdout.write(json.dumps(etl_info, ensure_ascii=False) + "\n")
        return

    if args.reconcile:
        return run_reconcile(db, cfg)

    if args.json:
        output_json(db, cfg, etl_info, days=args.days)
    else:
        output_table(db, cfg, etl_info)


if __name__ == "__main__":
    sys.exit(main() or 0)
