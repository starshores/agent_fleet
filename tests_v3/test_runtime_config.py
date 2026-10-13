from dataclasses import FrozenInstanceError, replace
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import tomllib
import unittest
from unittest.mock import patch

from masteragent.errors import PlatformError
from masteragent.paths import PathSafetyError, SOURCE_ROOT
from masteragent.runtime_config import (
    RuntimeConfig, build_argv, child_environment, load_runtime_config,
    prepare_execution_workspace, prepare_runtime_home,
)


class RuntimeConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="ma3-runtime-config-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.home = self.root / "runtime-a"
        self.home.mkdir()
        self.workspace = self.root / "business-a"
        self.workspace.mkdir()
        self.state = self.root / "state"
        self.state.mkdir()
        # Inspecting the Python native executable is harmless; no model, shell
        # wrapper, authentication, personal session or external process is run.
        self.values = {
            "schema_version": 1, "runtime": "codex-exec",
            "executable": str(Path(sys.executable).resolve()),
            "expected_version": "codex-cli 0.synthetic", "model": "synthetic-model",
            "adapter_id": "agent-text", "installation_id": "business-a",
            "role_id": "editor", "generation": 1, "codex_home": str(self.home),
            "timeout_seconds": 90, "lease_seconds": 30,
        }
        self.config_path = self.root / "runtime.json"

    def load(self, values=None):
        self.config_path.write_text(json.dumps(self.values if values is None else values), encoding="utf-8")
        return load_runtime_config(self.config_path)

    def test_explicit_configuration_is_frozen_and_public_serializable(self):
        config = self.load()
        self.assertIsInstance(config.executable, Path)
        self.assertEqual(config.codex_home, self.home)
        self.assertEqual(config.as_public_dict(), self.values)
        with self.assertRaises(FrozenInstanceError):
            config.model = "other"
        config.validate_scope("business-a", "editor", 1)
        for scope in (("business-b", "editor", 1), ("business-a", "reviewer", 1),
                      ("business-a", "editor", 2), ("business-a", "editor", True)):
            with self.subTest(scope=scope), self.assertRaises(PlatformError) as error:
                config.validate_scope(*scope)
            self.assertEqual(error.exception.code, "runtime_scope_mismatch")

    def test_exact_fields_with_no_defaults_or_credentials(self):
        for key in self.values:
            values = dict(self.values)
            del values[key]
            with self.subTest(missing=key), self.assertRaises(PlatformError):
                self.load(values)
        for key in ("token", "api_key", "env", "arguments", "workspace", "prompt"):
            with self.subTest(extra=key), self.assertRaises(PlatformError):
                self.load({**self.values, key: "never echo this"})

    def test_duplicate_nonobject_malformed_and_nonfinite_json(self):
        base = json.dumps(self.values)
        samples = ["[]", "null", "{", base[:-1] + ',"model":"other"}',
                   base.replace('"timeout_seconds": 90', '"timeout_seconds": NaN'),
                   base.replace('"timeout_seconds": 90', '"timeout_seconds": Infinity'),
                   base.replace('"timeout_seconds": 90', '"timeout_seconds": 1e999'),
                   "{" + '"x":' * 1100 + "0" + "}" * 1100]
        for sample in samples:
            self.config_path.write_text(sample, encoding="utf-8")
            with self.subTest(sample=sample[:30]), self.assertRaises(PlatformError):
                load_runtime_config(self.config_path)

    def test_types_identifiers_and_durations_are_validated(self):
        bad = {
            "schema_version": [True, 2, "1"], "runtime": ["auto", None],
            "expected_version": ["", " version", "v\n1", None],
            "model": ["", "\tmodel", 1, "\ud800"],
            "adapter_id": ["role/exec", "Agent", "a b", "a" * 65],
            "installation_id": ["../a", "", "a/b"],
            "role_id": ["../a", "Editor", ""], "generation": [0, -1, True, 1.0],
            "timeout_seconds": [0, 3601, True, math.nan, math.inf, 10 ** 400],
            "lease_seconds": [0, 4.99, 300.1, False, math.nan, math.inf],
        }
        for key, values in bad.items():
            for value in values:
                with self.subTest(key=key, value=repr(value)), self.assertRaises(PlatformError):
                    RuntimeConfig(**{**self.values, key: value})
        self.load({**self.values, "timeout_seconds": 1, "lease_seconds": 5})
        self.load({**self.values, "timeout_seconds": 3600, "lease_seconds": 300})

    def test_bom_size_and_invalid_encoding(self):
        self.config_path.write_text(json.dumps(self.values), encoding="utf-8-sig")
        self.assertEqual(load_runtime_config(self.config_path).model, "synthetic-model")
        for data in (b"\xff", b" " * 65537):
            self.config_path.write_bytes(data)
            with self.assertRaises(PlatformError):
                load_runtime_config(self.config_path)

    def test_executable_requires_existing_native_absolute_file(self):
        for value in ("codex", "relative.exe", self.root / "missing.exe"):
            with self.subTest(value=value), self.assertRaises((PlatformError, PathSafetyError)):
                RuntimeConfig(**{**self.values, "executable": value})
        for suffix in (".cmd", ".bat", ".ps1", ".py", ".sh"):
            executable = self.root / ("wrapper" + suffix)
            executable.write_text("never executed", encoding="utf-8")
            with self.subTest(suffix=suffix), self.assertRaises(PlatformError):
                RuntimeConfig(**{**self.values, "executable": executable})
        executable = self.root / "not-an-executable.exe"
        executable.mkdir()
        with self.assertRaises(PlatformError):
            RuntimeConfig(**{**self.values, "executable": executable})

    def test_home_preflight_does_not_create_or_bind(self):
        config = self.load()
        self.assertEqual(prepare_runtime_home(config, self.state, self.workspace), self.home)
        self.assertEqual(list(self.home.iterdir()), [])
        missing = self.root / "missing"
        with self.assertRaises(PlatformError):
            prepare_runtime_home(replace(config, codex_home=missing), self.state, self.workspace, bind=True)
        self.assertFalse(missing.exists())

    def test_binding_is_durable_idempotent_and_scoped(self):
        config = self.load()
        prepare_runtime_home(config, self.state, self.workspace, bind=True)
        marker = self.home / ".masteragent-runtime.json"
        before = marker.read_bytes()
        expected = {"schema_version": 1, "state_root": str(self.state),
                    "installation_id": "business-a", "role_id": "editor", "generation": 1}
        self.assertEqual(json.loads(before), expected)
        (self.home / "sessions").mkdir()
        for bind in (False, True):
            prepare_runtime_home(config, self.state, self.workspace, bind=bind)
            self.assertEqual(marker.read_bytes(), before)
            for changed in ({"installation_id": "business-b"}, {"role_id": "reviewer"}, {"generation": 2}):
                with self.subTest(bind=bind, changed=changed), self.assertRaises(PlatformError) as error:
                    prepare_runtime_home(replace(config, **changed), self.state, self.workspace, bind=bind)
                self.assertEqual(error.exception.code, "runtime_home_bound")
            with self.assertRaises(PlatformError):
                prepare_runtime_home(config, self.root / "other-state", self.workspace, bind=bind)
        self.assertEqual(marker.read_bytes(), before)

    def test_auth_may_be_preprovisioned_but_is_never_read(self):
        config = self.load()
        auth = self.home / "auth.json"
        auth.write_bytes(b"synthetic secret; deliberately not JSON")
        original_open = Path.open

        def guarded_open(path, *args, **kwargs):
            if path == auth:
                raise AssertionError("auth contents must never be read")
            return original_open(path, *args, **kwargs)

        with patch.object(Path, "open", guarded_open):
            prepare_runtime_home(config, self.state, self.workspace)
            prepare_runtime_home(config, self.state, self.workspace, bind=True)
        self.assertEqual(auth.read_bytes(), b"synthetic secret; deliberately not JSON")

    def test_unmanaged_and_malformed_binding_rejected_unchanged(self):
        config = self.load()
        file = self.home / "config.toml"
        file.write_text("synthetic = true", encoding="utf-8")
        for bind in (False, True):
            with self.assertRaises(PlatformError):
                prepare_runtime_home(config, self.state, self.workspace, bind=bind)
        self.assertEqual(file.read_text(encoding="utf-8"), "synthetic = true")
        marker = self.home / ".masteragent-runtime.json"
        for content in ("[]", "{", '{"schema_version":1,"schema_version":1}', "x" * 8193):
            marker.write_text(content, encoding="utf-8")
            with self.subTest(content=content[:20]), self.assertRaises(PlatformError):
                prepare_runtime_home(config, self.state, self.workspace, bind=True)
            self.assertEqual(marker.read_text(encoding="utf-8"), content)

    def test_home_rejects_protected_overlap_and_host_runtime(self):
        config = self.load()
        candidates = (self.state, self.workspace, self.root, Path.home(), SOURCE_ROOT,
                      Path.home() / ".codex", Path.home() / ".claude")
        for candidate in candidates:
            with self.subTest(candidate=candidate), self.assertRaises((PlatformError, PathSafetyError)):
                prepare_runtime_home(replace(config, codex_home=candidate), self.state, self.workspace)
        with patch.dict(os.environ, {"CODEX_HOME": str(self.home)}):
            with self.assertRaises(PlatformError) as error:
                prepare_runtime_home(config, self.state, self.workspace)
            self.assertEqual(error.exception.code, "protected_runtime_home")
        legacy = self.root / "old-runtime"
        (legacy / "agent_bus").mkdir(parents=True)
        (legacy / "agent_bus" / "agentbus.py").write_text("must never execute", encoding="utf-8")
        legacy_home = legacy / "codex"
        legacy_home.mkdir()
        with self.assertRaises(PathSafetyError):
            prepare_runtime_home(replace(config, codex_home=legacy_home), self.state, self.workspace)

    def test_linked_auth_and_binding_rejected_without_mutating_target(self):
        config = self.load()
        for filename in ("auth.json", ".masteragent-runtime.json"):
            home = self.root / ("links-" + filename.replace(".", ""))
            home.mkdir()
            target = self.root / (filename + ".target")
            target.write_bytes(b"synthetic-untouched")
            os.link(target, home / filename)
            with self.subTest(filename=filename), self.assertRaises(PlatformError) as error:
                prepare_runtime_home(replace(config, codex_home=home), self.state, self.workspace, bind=True)
            self.assertEqual(error.exception.code, "linked_runtime_file")
            self.assertEqual(target.read_bytes(), b"synthetic-untouched")

    @unittest.skipUnless(os.name == "nt", "Windows junction behavior")
    def test_home_junction_rejected(self):
        alias = self.root / "alias-home"
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command",
             "New-Item -ItemType Junction -Path $env:MA3_TEST_ALIAS -Target $env:MA3_TEST_TARGET -ErrorAction Stop | Out-Null"],
            env={**os.environ, "MA3_TEST_ALIAS": str(alias), "MA3_TEST_TARGET": str(self.home)},
            text=True, capture_output=True, timeout=20,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        with self.assertRaises(PathSafetyError):
            RuntimeConfig(**{**self.values, "codex_home": alias})
        self.assertEqual(list(self.home.iterdir()), [])

    def test_execution_workspace_requires_binding_and_is_fresh_per_attempt(self):
        config = self.load()
        with self.assertRaises(PlatformError):
            prepare_execution_workspace(config, "attempt-1")
        self.assertFalse((self.home / "execution").exists())
        prepare_runtime_home(config, self.state, self.workspace, bind=True)
        marker = (self.home / ".masteragent-runtime.json").read_bytes()
        first = prepare_execution_workspace(config, "attempt-1")
        self.assertEqual(first, self.home / "execution" / "attempt-1")
        self.assertEqual(list(first.iterdir()), [])
        (first / "evidence.txt").write_text("preserve previous execution", encoding="utf-8")
        with self.assertRaises(PlatformError) as error:
            prepare_execution_workspace(config, "attempt-1")
        self.assertEqual(error.exception.code, "runtime_execution_exists")
        second = prepare_execution_workspace(config, "attempt-2")
        self.assertNotEqual(first, second)
        self.assertEqual(list(second.iterdir()), [])
        self.assertEqual((first / "evidence.txt").read_text(encoding="utf-8"), "preserve previous execution")
        self.assertEqual((self.home / ".masteragent-runtime.json").read_bytes(), marker)
        for attempt_id in ("../escape", "a/b", "..", "con", "con.txt", "LPT1", "trailing.", "", None):
            with self.subTest(attempt_id=attempt_id), self.assertRaises(PlatformError):
                prepare_execution_workspace(config, attempt_id)
        with self.assertRaises(PlatformError):
            prepare_execution_workspace(replace(config, generation=2), "attempt-3")

    def test_execution_rejects_project_entries_without_reading_them(self):
        config = self.load()
        for index, name in enumerate((".codex", ".agents", ".git")):
            parent = self.root / f"project-{index}"
            home = parent / "runtime"
            home.mkdir(parents=True)
            local_config = replace(config, codex_home=home)
            prepare_runtime_home(local_config, self.state, self.workspace, bind=True)
            sentinel = parent / name
            sentinel.write_text("synthetic configuration must never be read", encoding="utf-8")
            with self.subTest(name=name), self.assertRaises(PlatformError) as error:
                prepare_execution_workspace(local_config, "attempt-1")
            self.assertEqual(error.exception.code, "runtime_project_boundary")
            self.assertFalse((home / "execution").exists())
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "synthetic configuration must never be read")

    def test_execution_ancestry_stops_at_personal_home_boundary(self):
        personal = self.root / "synthetic-user"
        home = personal / "deployments" / "runtime"
        home.mkdir(parents=True)
        for name in (".codex", ".agents"):
            (personal / name).mkdir()
        config = replace(self.load(), codex_home=home)
        with patch.object(Path, "home", return_value=personal):
            prepare_runtime_home(config, self.state, self.workspace, bind=True)
            leaf = prepare_execution_workspace(config, "attempt-1")
            self.assertEqual(leaf, home / "execution" / "attempt-1")
            argv = build_argv(config, leaf)
        overrides = [argv[index + 1] for index, value in enumerate(argv) if value == "-c"]
        self.assertIn("project_root_markers=[]", overrides)
        self.assertIn("features.skip_host_skill_discovery=true", overrides)

    def test_argv_is_explicit_text_only_without_prompt_or_shell_interpolation(self):
        config = self.load()
        argv = build_argv(config, self.workspace)
        self.assertEqual(argv[:4], [str(config.executable), "exec", "--json", "--ignore-user-config"])
        self.assertEqual(argv[-1], "-")
        for flag, value in (("--sandbox", "read-only"), ("--color", "never"),
                            ("--model", "synthetic-model"), ("-C", str(self.workspace))):
            self.assertEqual(argv[argv.index(flag) + 1], value)
        overrides = {argv[index + 1] for index, value in enumerate(argv) if value == "-c"}
        self.assertTrue({"project_doc_max_bytes=0", 'web_search="disabled"',
                         'approval_policy="never"', "mcp_servers={}",
                         "features.skip_host_skill_discovery=true"}.issubset(overrides))
        self.assertIn("project_root_markers=[]", overrides)
        project_setting = next(value for value in overrides if value.startswith("projects="))
        self.assertEqual(tomllib.loads(project_setting), {
            "projects": {str(self.workspace): {"trust_level": "untrusted"}},
        })
        for feature in ("shell_tool", "unified_exec", "apps", "plugins", "hooks", "memories",
                        "browser_use", "browser_use_external", "computer_use", "image_generation",
                        "view_image", "multi_agent", "skill_search", "skill_mcp_dependency_install",
                        "shell_snapshot", "daemon_auto_start", "remote_plugin"):
            self.assertIn(f"features.{feature}=false", overrides)
        for forbidden in ("--ephemeral", "--ignore-rules", "--yolo", "--dangerously-bypass-approvals-and-sandbox", "resume"):
            self.assertNotIn(forbidden, argv)
        for invalid_workspace in (self.root / "missing", SOURCE_ROOT, Path.home()):
            with self.subTest(workspace=invalid_workspace), self.assertRaises((PlatformError, PathSafetyError)):
                build_argv(config, invalid_workspace)

    def test_child_environment_strips_all_credentials_and_session_context(self):
        config = self.load()
        source = {"SystemRoot": "synthetic-system", "PATH": "synthetic-path",
                  "TEMP": "synthetic-temp", "USERPROFILE": "synthetic-user",
                  "OPENAI_API_KEY": "never-inherited", "CODEX_API_KEY": "never-inherited",
                  "CODEX_HOME": "personal-home", "CODEX_THREAD_ID": "host-thread",
                  "AGENTBUS_ROLE": "legacy-role", "PYTHONPATH": "foreign-module",
                  "NODE_OPTIONS": "foreign-module", "ANTHROPIC_API_KEY": "never-inherited",
                  "AZURE_OPENAI_API_KEY": "never-inherited", "UNLISTED": "not-inherited"}
        self.assertEqual(child_environment(config, source), {
            "SystemRoot": "synthetic-system", "PATH": "synthetic-path",
            "TEMP": "synthetic-temp", "USERPROFILE": "synthetic-user",
            "CODEX_HOME": str(self.home),
        })
        self.assertEqual(source["CODEX_HOME"], "personal-home")
        self.assertEqual(child_environment(config, {}), {"CODEX_HOME": str(self.home)})

    def test_both_text_runtimes_disable_native_bundled_skill_generation(self):
        config = self.load()
        for runtime in ("codex-exec", "codex-app-server"):
            with self.subTest(runtime=runtime):
                argv = build_argv(replace(config, runtime=runtime), self.workspace)
                overrides = [argv[index + 1] for index, value in enumerate(argv) if value == "-c"]
                parsed = tomllib.loads("\n".join(overrides))
                self.assertIs(parsed["skills"]["bundled"]["enabled"], False)
                self.assertIs(parsed["skills"]["include_instructions"], False)
                self.assertIs(parsed["features"]["skip_host_skill_discovery"], True)
        self.assertEqual(child_environment(config, {"path": "case-safe"})["PATH"], "case-safe")


if __name__ == "__main__":
    unittest.main()
