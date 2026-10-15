"""Synthetic-only usage checks; never import the UI server or access host sessions."""
from __future__ import annotations

import ast
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch


SOURCE = Path(__file__).resolve().parents[1]
SID_A = "11111111-1111-1111-1111-111111111111"
SID_B = "22222222-2222-2222-2222-222222222222"
SID_C = "33333333-3333-3333-3333-333333333333"


def event(typ, payload, stamp="2026-10-12T00:01:00Z"):
    return {"type": typ, "payload": payload, "timestamp": stamp}


def record(inp, out=0):
    return event("token_usage_record", {"usage": {"input_tokens": inp,
                 "output_tokens": out, "cached_input_tokens": inp // 2}})


def context(count, stamp="2026-10-12T00:01:00Z", window=1000):
    return event("event_msg", {"type": "token_count", "info": {
        "last_token_usage": {"total_tokens": count},
        "model_context_window": window}}, stamp)


class CodexUsageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="codex-usage-synthetic-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.home = self.root / "synthetic-codex"
        self.bus = self.root / "synthetic-bus"
        self.bus.mkdir()
        spec = importlib.util.spec_from_file_location("synthetic_codex_usage", SOURCE / "ui" / "codex_usage.py")
        self.module = importlib.util.module_from_spec(spec)
        with patch.dict(os.environ, {"CODEX_HOME": str(self.home)}):
            spec.loader.exec_module(self.module)
        self.assertEqual(self.module.CODEX_HOME, self.home)

    def write_json(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding="utf-8")

    def bind(self, role="RoleA", sid=SID_A, kind="codex", registered=True):
        roster_path = self.bus / "agents.json"
        roster = json.loads(roster_path.read_text(encoding="utf-8")) if roster_path.exists() else {}
        if registered:
            roster[role] = {"desc": "synthetic"}
        self.write_json(roster_path, roster)
        self.write_json(self.bus / role / "presence.json", {
            "role": role, "host_kind": kind, "host_session_id": sid,
            "cwd": "synthetic-project"})

    def rollout(self, sid, records, archive=False, suffix="2026-10-12T00-00-00"):
        path = self.home / ("archived_sessions" if archive else "sessions") / "2026" / "10" / "12" / f"rollout-{suffix}-{sid}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(json.dumps(row) for row in records) + "\n", encoding="utf-8")
        return path

    def test_calls_survive_daily_model_and_session_aggregation(self):
        self.rollout(SID_A, [event("turn_context", {"model": "synthetic"}), record(10, 2), record(20, 3)])
        self.rollout(SID_B, [event("turn_context", {"model": "synthetic"}), record(7)], archive=True)
        data = self.module.historical_usage(allowed_session_ids=[SID_A, SID_B])
        self.assertEqual(data["totals"]["tokens_total"], 42)
        self.assertEqual(data["totals"]["calls"], 3)
        self.assertEqual(data["daily"][0]["calls"], 3)
        self.assertEqual(data["by_model"][0]["calls"], 3)
        self.assertEqual(data["by_session"][SID_A]["calls"], 2)
        self.assertEqual(data["totals"]["cached_input"], 18)

    def test_cumulative_snapshots_skip_duplicates_and_count_resets(self):
        rows = [event("event_msg", {"type": "token_count", "info": {
            "total_token_usage": {"input_tokens": value}}}) for value in (100, 150, 150, 20)]
        self.rollout(SID_A, rows)
        data = self.module.historical_usage(allowed_session_ids=[SID_A])
        self.assertEqual(data["totals"]["tokens_total"], 170)
        self.assertEqual(data["totals"]["calls"], 3)

    def test_no_scope_and_invalid_ids_never_enumerate_or_open_rollouts(self):
        self.rollout(SID_A, [record(99)])
        with patch.object(Path, "rglob", side_effect=AssertionError("unscoped enumeration")) as enumerated, patch.object(Path, "open", side_effect=AssertionError("unscoped read")) as opened:
            self.assertEqual(self.module.historical_usage()["sessions"], 0)
            self.assertEqual(self.module.historical_usage(allowed_session_ids=["*", "../private", "bad"])["sessions"], 0)
        enumerated.assert_not_called()
        opened.assert_not_called()

    def test_registered_exact_scope_never_reads_unrelated_decoys(self):
        self.bind()
        self.bind("Unregistered", SID_B, registered=False)
        self.bind("ClaudeRole", SID_C, kind="claude")
        allowed = self.rollout(SID_A, [record(10)])
        decoy = self.rollout(SID_B, [record(900)])
        claude_decoy = self.rollout(SID_C, [record(800)])
        original_open, original_glob = Path.open, Path.rglob
        opened, patterns = [], []

        def guarded_open(path, *args, **kwargs):
            opened.append(path)
            self.assertTrue(path.resolve().is_relative_to(self.root.resolve()))
            self.assertNotIn(path, [decoy, claude_decoy, self.bus / "Unregistered" / "presence.json"])
            return original_open(path, *args, **kwargs)

        def scoped_glob(path, pattern):
            patterns.append(pattern)
            self.assertIn(SID_A, pattern)
            return original_glob(path, pattern)

        with patch.object(Path, "open", guarded_open), patch.object(Path, "rglob", scoped_glob):
            data = self.module.historical_usage(bus_root=self.bus)
        self.assertEqual(data["totals"]["tokens_total"], 10)
        self.assertIn(allowed, opened)
        self.assertTrue(all(path.resolve().is_relative_to(self.root.resolve()) for path in opened))
        self.assertTrue(set(opened).isdisjoint({decoy, claude_decoy, self.bus / "Unregistered" / "presence.json"}))
        self.assertTrue(patterns)

    def test_cache_isolated_by_ids_and_by_codex_home(self):
        self.rollout(SID_A, [record(10)])
        self.rollout(SID_B, [record(20)])
        self.assertEqual(self.module.historical_usage(allowed_session_ids=[SID_A])["totals"]["tokens_total"], 10)
        second = self.module.historical_usage(allowed_session_ids=[SID_B])
        self.assertEqual(second["totals"]["tokens_total"], 20)
        self.assertNotIn(SID_A, second["by_session"])
        self.assertTrue(all(SID_B in key for key in self.module._usage_cache["files"]))
        self.module.CODEX_HOME = self.root / "other-empty-home"
        self.assertEqual(self.module.historical_usage(allowed_session_ids=[SID_B])["sessions"], 0)

    def test_roster_removal_and_binding_replacement_revoke_cached_usage(self):
        self.bind()
        self.rollout(SID_A, [record(10)])
        self.rollout(SID_B, [record(20)])
        self.assertEqual(self.module.historical_usage(bus_root=self.bus)["totals"]["tokens_total"], 10)
        self.bind(sid=SID_B)
        self.assertEqual(self.module.historical_usage(bus_root=self.bus)["totals"]["tokens_total"], 20)
        self.write_json(self.bus / "agents.json", {})
        data = self.module.historical_usage(bus_root=self.bus)
        self.assertEqual(data["sessions"], 0)
        self.assertEqual(self.module._usage_cache["files"], {})
        self.assertEqual(self.module._index_cache["paths"], {})

    def test_explicit_ids_narrow_bus_scope_and_invalid_registry_fails_closed(self):
        self.bind()
        self.rollout(SID_A, [record(10)])
        self.rollout(SID_B, [record(20)])
        self.assertEqual(self.module.historical_usage(bus_root=self.bus, allowed_session_ids=[SID_B])["sessions"], 0)
        for invalid in ([], "malformed"):
            self.write_json(self.bus / "agents.json", invalid)
            self.assertEqual(self.module.historical_usage(bus_root=self.bus)["sessions"], 0)
        (self.bus / "agents.json").unlink()
        self.assertEqual(self.module.historical_usage(bus_root=self.bus)["sessions"], 0)

    def test_legacy_session_alias_does_not_grant_or_expand_access(self):
        self.bind()
        self.rollout(SID_A, [record(10), context(100)])
        self.rollout(SID_B, [record(20), context(500)])
        path = self.bus / "RoleA" / "presence.json"
        self.write_json(path, {"role": "RoleA", "host_kind": "codex", "codex_session_id": SID_A})
        self.assertEqual(self.module.historical_usage(bus_root=self.bus)["sessions"], 0)
        self.assertEqual(self.module.active_context(self.bus), [])
        self.write_json(path, {"role": "RoleA", "host_kind": "codex",
                              "host_session_id": SID_A, "codex_session_id": SID_B})
        self.assertEqual(set(self.module.historical_usage(bus_root=self.bus)["by_session"]), {SID_A})
        self.assertEqual([row["sid_full"] for row in self.module.active_context(self.bus)], [SID_A])

    def test_revocation_during_parse_drops_result_and_metadata_cache(self):
        self.bind()
        self.rollout(SID_A, [record(10)])
        original = self.module._read_rollout_usage

        def read_then_revoke(path):
            result = original(path)
            self.write_json(self.bus / "agents.json", {})
            return result

        with patch.object(self.module, "_read_rollout_usage", side_effect=read_then_revoke):
            data = self.module.historical_usage(bus_root=self.bus)
        self.assertEqual(data["sessions"], 0)
        self.assertEqual(data["totals"]["tokens_total"], 0)
        self.assertEqual(self.module._usage_cache["files"], {})

    def test_force_bypasses_usage_file_and_index_caches(self):
        path = self.rollout(SID_A, [record(20)])
        data = self.module.historical_usage(allowed_session_ids=[SID_A])
        before = path.stat()
        self.rollout(SID_A, [record(28)])
        os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
        self.assertEqual(path.stat().st_size, before.st_size)
        self.assertEqual(self.module.historical_usage(allowed_session_ids=[SID_A])["totals"], data["totals"])
        self.assertEqual(self.module.historical_usage(force=True, allowed_session_ids=[SID_A])["totals"]["tokens_total"], 28)
        newer = self.rollout(SID_A, [record(30)], archive=True)
        os.utime(newer, ns=(before.st_atime_ns, before.st_mtime_ns + 1000000000))
        self.assertEqual(self.module.historical_usage(force=True, allowed_session_ids=[SID_A])["totals"]["tokens_total"], 30)

    def test_context_uses_latest_count_and_preceding_model_live_unknown(self):
        self.bind()
        self.rollout(SID_A, [event("turn_context", {"model": "synthetic"}),
            context(100), context(500, "2026-10-12T00:02:00Z"),
            event("turn_context", {"model": "future-turn"})])
        row, = self.module.active_context(self.bus)
        self.assertEqual((row["ctx"], row["pct"], row["ts"]), (500, 50.0, "2026-10-12T00:02:00Z"))
        self.assertEqual(row["model"], "synthetic")
        self.assertIsNone(row["role_live"])

    def test_latest_context_without_window_does_not_borrow_older_window(self):
        self.bind()
        self.rollout(SID_A, [event("turn_context", {"model": "synthetic"}), context(100), context(500, window=0)])
        self.assertEqual(self.module.active_context(self.bus), [])

    def test_context_rechecks_registration_before_returning(self):
        self.bind()
        self.rollout(SID_A, [context(500)])
        presences = self.module._registered_presences(self.bus)
        with patch.object(self.module, "_registered_presences", side_effect=[presences, []]):
            self.assertEqual(self.module.active_context(self.bus), [])


class UsageApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="codex-usage-api-synthetic-")
        self.addCleanup(self.tmp.cleanup)
        self.bus = Path(self.tmp.name) / "synthetic-bus"
        self.codex = Mock(return_value={"ok": True, "sessions": 1, "totals": {"calls": 2}, "by_session": {SID_A: {}}, "daily": []})
        self.run = Mock(return_value=SimpleNamespace(returncode=0, stdout=b'{"daily":[],"totals":{}}', stderr=b""))
        names = {"_with_codex_usage", "_run_token_stats", "get_usage", "_data_has_today", "_refresh_usage", "build_ctxpanel"}
        tree = ast.parse((SOURCE / "ui" / "api.py").read_text(encoding="utf-8-sig"))
        selected = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
        self.assertEqual({node.name for node in selected}, names)
        self.ns = {"json": json, "time": time, "threading": threading,
            "subprocess": SimpleNamespace(run=self.run), "sys": SimpleNamespace(executable="synthetic-python"),
            "TOKEN_STATS": "synthetic-token-stats.py", "_NO_WINDOW": 0,
            "BUS": self.bus, "_codex_historical_usage": self.codex,
            "_augment_daily_with_backfill": lambda data: data,
            "_usage_lock": threading.Lock(), "_usage_cache": {"ts": 0., "data": None},
            "_usage_refreshing": {"v": False}, "_USAGE_TTL": 300., "_USAGE_TTL_TODAY": 60.}
        exec(compile(ast.Module(body=selected, type_ignores=[]), "isolated-api-functions", "exec"), self.ns)

    def test_claude_failures_preserve_codex_and_forward_force_and_bus(self):
        failures = [FileNotFoundError("synthetic missing stats"), subprocess.TimeoutExpired("synthetic", 60),
            SimpleNamespace(returncode=1, stdout=b"", stderr=b"synthetic failure"),
            SimpleNamespace(returncode=0, stdout=b"not JSON", stderr=b""),
            SimpleNamespace(returncode=0, stdout=b'{"error":"synthetic error"}', stderr=b"")]
        for failure in failures:
            with self.subTest(failure=type(failure).__name__):
                self.run.side_effect = failure if isinstance(failure, Exception) else None
                self.run.return_value = failure
                data = self.ns["get_usage"](force=True)
                self.assertFalse(data["claude_available"])
                self.assertTrue(data["claude_error"])
                self.assertNotIn("error", data)
                self.assertTrue(data["codex_usage"]["ok"])
                self.assertNotIn("by_session", data["codex_usage"])
                self.codex.assert_called_with(force=True, bus_root=self.bus)

    def test_cached_ui_response_revalidates_codex_instead_of_leaking_old_payload(self):
        self.ns["get_usage"]()
        self.assertNotIn("codex_usage", self.ns["_usage_cache"]["data"])
        self.codex.return_value = {"ok": True, "sessions": 0, "totals": {}, "daily": [], "by_session": {}}
        data = self.ns["get_usage"]()
        self.assertEqual(data["codex_usage"]["sessions"], 0)
        self.assertEqual(self.run.call_count, 1)
        self.codex.assert_called_with(force=False, bus_root=self.bus)

    def test_codex_failure_removes_old_codex_payload_and_preserves_claude(self):
        self.ns["get_usage"]()
        self.codex.side_effect = OSError("synthetic unavailable")
        data = self.ns["get_usage"]()
        self.assertTrue(data["claude_available"])
        self.assertFalse(data["codex_usage"]["ok"])
        self.assertEqual(data["codex_usage"]["sessions"], 0)
        self.assertNotIn("by_session", data["codex_usage"])

    def test_context_panel_preserves_unknown_live_state(self):
        self.ns.update(get_ctx=lambda: {"rows": [{"source": "codex", "role": "RoleA", "role_live": None}]},
            get_who=lambda: {}, _CTX_RED=90, _CTX_YELLOW=70, _CTX_COMPACT_FRAC=0.65, _CTX_ACTIVE_MIN=90)
        row, = self.ns["build_ctxpanel"]()["rows"]
        self.assertIsNone(row["role_live"])


if __name__ == "__main__":
    unittest.main()
