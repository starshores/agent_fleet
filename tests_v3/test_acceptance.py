"""Black-box acceptance for the isolated 3.0 package and stub-runtime boundary.

Only the new ``masteragent`` CLI is executed. Every mutable path and legacy
environment bait lives in a TemporaryDirectory outside the source checkout.
These tests do not claim to verify a Codex or operating-system sandbox.
"""

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class AcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="masteragent-v3-accept-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.assertNotIn(ROOT, self.base.parents)
        self.state = self.base / "state"
        self.workspace_a = self.base / "workspace-a"
        self.workspace_b = self.base / "workspace-b"
        self.workspace_a.mkdir()
        self.workspace_b.mkdir()
        self.bait = self.base / "legacy-bait"
        self.bait.mkdir()
        (self.bait / "config.json").write_text("invalid legacy config", encoding="utf-8")
        (self.bait / "agents.json").write_text(
            '{"LegacyOnly":{"desc":"LEGACY_BAIT_MUST_NOT_LOAD"}}',
            encoding="utf-8",
        )
        (self.bait / "marker.txt").write_text("leave this bait unchanged", encoding="utf-8")
        self.env = os.environ.copy()
        self.env.update({
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONUTF8": "1",
            "AGENTBUS_DIR": str(self.bait),
            "AGENTBUS_AGENT_FLEET_HOME": str(self.bait),
            "AGENTBUS_BIZ_REGISTRY": str(self.bait),
            "AGENTBUS_DEFAULT_WORKER_CWD": str(self.bait),
            "CLAUDE_CODE_SESSION_ID": "synthetic-legacy-bait-session",
        })
        self.initial_bait = self.tree_snapshot(self.bait)

    def tearDown(self):
        self.assertEqual(self.initial_bait, self.tree_snapshot(self.bait))

    @staticmethod
    def tree_snapshot(path):
        if not path.exists():
            return None
        result = {}
        for child in sorted(path.rglob("*")):
            key = child.relative_to(path).as_posix()
            if child.is_symlink():
                result[key] = ("symlink", os.readlink(child))
            elif child.is_dir():
                result[key] = ("directory",)
            else:
                result[key] = ("file", hashlib.sha256(child.read_bytes()).hexdigest())
        return result

    @staticmethod
    def platform_hash():
        """Hash the new platform source only; do not inspect legacy host files."""
        paths = sorted((ROOT / "masteragent").rglob("*.py"))
        if not paths:
            raise AssertionError("new masteragent platform source is missing")
        return {
            path.relative_to(ROOT).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in paths
        }

    def cli(self, *args, expected=0):
        completed = subprocess.run(
            [sys.executable, "-B", "-m", "masteragent", "--state-root", str(self.state),
             *map(str, args)],
            cwd=ROOT,
            env=self.env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=20,
        )
        self.assertEqual(
            expected, completed.returncode,
            f"CLI {args!r}: stdout={completed.stdout!r}; stderr={completed.stderr!r}",
        )
        payload = completed.stdout if expected == 0 else completed.stderr
        self.assertTrue(payload.strip(), f"CLI {args!r} returned no JSON")
        try:
            data = json.loads(payload)
        except json.JSONDecodeError as error:
            self.fail(f"CLI {args!r} returned invalid JSON: {error}: {payload!r}")
        if expected == 0:
            self.assertEqual("", completed.stderr.strip())
        else:
            self.assertEqual("", completed.stdout.strip())
            self.assertIsInstance(data, dict)
            self.assertIn("error", data)
        return data

    def test_empty_status_is_read_only_and_ignores_legacy_environment(self):
        before = self.tree_snapshot(self.base)
        data = self.cli("status")
        self.assertIsInstance(data, dict)
        self.assertNotIn("LegacyOnly", json.dumps(data))
        self.assertNotIn("LEGACY_BAIT_MUST_NOT_LOAD", json.dumps(data))
        self.assertFalse(self.state.exists())
        self.assertEqual(before, self.tree_snapshot(self.base))
        self.assertEqual(data, self.cli("status"))
        self.assertFalse(self.state.exists())

    def test_malformed_package_is_explicit_and_does_not_register(self):
        bad = self.base / "malformed.json"
        bad.write_text('{"package_id":', encoding="utf-8")
        self.cli("validate-package", bad, expected=2)
        self.assertFalse(self.state.exists())
        self.cli("install", "broken", "--package", bad, "--workspace", self.workspace_a,
                 expected=2)
        after = self.cli("status")
        self.assertEqual([], after["installations"])
        self.assertIsNone(after["active"])

    def test_missing_package_is_explicit_and_does_not_register(self):
        self.cli("validate-package", self.base / "does-not-exist.json", expected=2)
        self.assertFalse(self.state.exists())
        self.cli("install", "missing", "--package", self.base / "does-not-exist.json",
                 "--workspace", self.workspace_a, expected=2)
        after = self.cli("status")
        self.assertEqual([], after["installations"])
        self.assertIsNone(after["active"])

    def install(self, installation, fixture, workspace):
        package = ROOT / "examples" / "v3" / fixture
        validated = self.cli("validate-package", package)
        self.assertTrue(validated["valid"])
        installed = self.cli("install", installation, "--package", package,
                             "--workspace", workspace)
        self.assertEqual(installation, installed["installation_id"])
        self.assertEqual("inactive", installed["state"])
        self.assertEqual(validated["digest"], installed["digest"])
        return installed

    def test_same_build_a_b_a_lifecycle_preserves_scoped_history(self):
        platform_before = self.platform_hash()
        fixture_before = self.tree_snapshot(ROOT / "examples" / "v3")
        self.install("alpha", "business-a.json", self.workspace_a)
        self.assertEqual([], self.cli("history", "alpha")["registrations"])
        inactive = self.cli("register", "alpha", "reviewer", expected=2)
        self.assertEqual("installation_inactive", inactive["error"]["code"])

        alpha = self.cli("activate", "alpha")
        self.assertEqual(1, alpha["generation"])
        registration_a = self.cli("register", "alpha", "reviewer")
        self.assertIsNone(registration_a["runtime"])
        self.assertFalse(registration_a["online"])
        context_a = self.cli("context", "alpha", "reviewer")
        self.assertIn("ALPHA_CATALOGUE_ONLY", json.dumps(context_a))
        self.assertNotIn("BETA_WORKSHOP_ONLY", json.dumps(context_a))
        self.assertEqual({"catalogue-echo"}, set(context_a["tools"]))
        self.assertEqual(str(self.workspace_a), context_a["workspace"])
        task_a = self.cli("submit", "alpha", "reviewer", "--text", "alpha task payload",
                          "--request-id", "same-local-request")
        result_a = self.cli("run", "alpha", task_a["task_id"], "--tool", "catalogue-echo")
        self.assertEqual("alpha task payload", result_a["output"])
        self.assertEqual([result_a], self.cli("results", "alpha"))
        notices_a = self.cli("notifications", "alpha")
        self.assertEqual(1, len(notices_a))
        self.assertEqual(task_a["task_id"], notices_a[0]["task_id"])
        self.assertEqual("alpha", notices_a[0]["installation_id"])
        parked_a = self.cli("submit", "alpha", "reviewer", "--text", "alpha parked payload")
        self.cli("deactivate", "alpha")
        history_a = self.cli("history", "alpha")
        self.assertEqual(["reviewer"], [row["role_id"] for row in history_a["registrations"]])
        self.assertEqual([result_a], history_a["results"])

        self.install("beta", "business-b.json", self.workspace_b)
        self.cli("activate", "beta")
        self.assertEqual([], self.cli("history", "beta")["registrations"])
        self.cli("register", "beta", "reviewer")
        context_b = self.cli("context", "beta", "reviewer")
        self.assertIn("BETA_WORKSHOP_ONLY", json.dumps(context_b))
        self.assertNotIn("ALPHA_CATALOGUE_ONLY", json.dumps(context_b))
        self.assertEqual({"workshop-uppercase"}, set(context_b["tools"]))
        self.assertEqual(str(self.workspace_b), context_b["workspace"])
        self.assertEqual([], self.cli("results", "beta"))
        self.assertEqual([], self.cli("notifications", "beta"))
        beta_view = self.cli("view", "beta")
        self.assertNotIn("ALPHA_CATALOGUE_ONLY", json.dumps(beta_view))
        self.assertEqual({"reviewer", "facilitator"},
                         {role["role_id"] for role in beta_view["package"]["roles"]})
        task_b = self.cli("submit", "beta", "reviewer", "--text", "beta task payload",
                          "--request-id", "same-local-request")
        self.assertNotEqual(task_a["task_id"], task_b["task_id"])
        result_b = self.cli("run", "beta", task_b["task_id"], "--tool", "workshop-uppercase")
        self.assertEqual("BETA TASK PAYLOAD", result_b["output"])
        self.assertEqual([result_b], self.cli("results", "beta"))
        self.assertEqual([task_b["task_id"]],
                         [notice["task_id"] for notice in self.cli("notifications", "beta")])
        self.assertEqual(history_a, self.cli("history", "alpha"))
        self.cli("deactivate", "beta")

        restored_a = self.cli("activate", "alpha")
        self.assertGreater(restored_a["generation"], alpha["generation"])
        restored_context = self.cli("context", "alpha", "reviewer")
        self.assertEqual(context_a["knowledge"], restored_context["knowledge"])
        self.assertEqual(context_a["tools"], restored_context["tools"])
        self.assertEqual([], self.cli("results", "alpha"))
        self.assertEqual([], self.cli("notifications", "alpha"))
        self.assertEqual([result_a], self.cli("history", "alpha")["results"])
        stale = self.cli("run", "alpha", parked_a["task_id"], "--tool", "catalogue-echo",
                         expected=2)
        self.assertEqual("stale_task", stale["error"]["code"])
        new_a = self.cli("submit", "alpha", "reviewer", "--text", "new alpha payload")
        self.cli("run", "alpha", new_a["task_id"], "--tool", "catalogue-echo")
        self.assertEqual([new_a["task_id"]],
                         [row["task_id"] for row in self.cli("results", "alpha")])
        self.assertEqual({task_a["task_id"], new_a["task_id"]},
                         {row["task_id"] for row in self.cli("history", "alpha")["results"]})
        self.assertEqual(platform_before, self.platform_hash())
        self.assertEqual(fixture_before, self.tree_snapshot(ROOT / "examples" / "v3"))

    def test_repeated_submission_and_run_do_not_duplicate_result_or_notice(self):
        self.install("sample", "business-a.json", self.workspace_a)
        self.cli("activate", "sample")
        task = self.cli("submit", "sample", "reviewer", "--text", "one action",
                        "--request-id", "retryable-client-request")
        repeated = self.cli("submit", "sample", "reviewer", "--text", "one action",
                            "--request-id", "retryable-client-request")
        self.assertEqual(task["task_id"], repeated["task_id"])
        first = self.cli("run", "sample", task["task_id"], "--tool", "catalogue-echo")
        self.assertEqual(first, self.cli("run", "sample", task["task_id"],
                                        "--tool", "catalogue-echo"))
        history = self.cli("history", "sample")
        self.assertEqual(1, len(history["tasks"]))
        self.assertEqual(1, len(history["results"]))
        self.assertEqual(1, len(history["notifications"]))
        rejected = self.cli("submit", "sample", "reviewer", "--text", "different action",
                            "--request-id", "retryable-client-request", expected=2)
        self.assertEqual("request_conflict", rejected["error"]["code"])
        self.assertEqual(history, self.cli("history", "sample"))

    def test_read_views_do_not_mutate_initialized_state_or_workspace(self):
        self.install("sample", "business-a.json", self.workspace_a)
        self.cli("activate", "sample")
        task = self.cli("submit", "sample", "reviewer", "--text", "sample payload")
        self.cli("run", "sample", task["task_id"], "--tool", "catalogue-echo")
        state_before = self.tree_snapshot(self.state)
        workspace_before = self.tree_snapshot(self.workspace_a)
        for args in [("status",), ("view", "sample"), ("context", "sample", "reviewer"),
                     ("results", "sample"), ("notifications", "sample"), ("history", "sample")]:
            with self.subTest(command=args):
                self.cli(*args)
                self.assertEqual(state_before, self.tree_snapshot(self.state))
                self.assertEqual(workspace_before, self.tree_snapshot(self.workspace_a))

    def test_uninstall_preserves_history_and_does_not_reuse_installation_identity(self):
        self.install("sample", "business-a.json", self.workspace_a)
        self.cli("activate", "sample")
        task = self.cli("submit", "sample", "reviewer", "--text", "retained payload")
        result = self.cli("run", "sample", task["task_id"], "--tool", "catalogue-echo")
        self.cli("deactivate", "sample")
        before = self.cli("history", "sample")
        uninstalled = self.cli("uninstall", "sample")
        self.assertEqual("uninstalled", uninstalled["state"])
        after = self.cli("history", "sample")
        for table in ("registrations", "tasks", "results", "notifications"):
            self.assertEqual(before[table], after[table])
        self.assertEqual([result], after["results"])
        self.cli("activate", "sample", expected=2)
        duplicate = self.cli("install", "sample", "--package",
                             ROOT / "examples" / "v3" / "business-b.json",
                             "--workspace", self.workspace_b, expected=2)
        self.assertEqual("installation_exists", duplicate["error"]["code"])
        self.assertEqual(after, self.cli("history", "sample"))


if __name__ == "__main__":
    unittest.main()
