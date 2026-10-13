import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from masteragent.paths import PathSafetyError, SOURCE_ROOT, prepare_state_root, validate_workspace


class PathBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="ma3-paths-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()

    def test_read_only_missing_state_does_not_create_directory(self):
        state = self.root / "missing" / "state"
        self.assertEqual(prepare_state_root(state, create=False), state)
        self.assertFalse((self.root / "missing").exists())

    def test_explicit_disjoint_state_and_workspace(self):
        state = prepare_state_root(self.root / "state")
        workspace = self.root / "business"
        workspace.mkdir()
        self.assertEqual(validate_workspace(workspace, state), workspace)
        self.assertEqual(list(state.iterdir()), [])

    def test_relative_or_blank_paths_rejected(self):
        for value in ("", "relative", Path("relative")):
            with self.subTest(value=value), self.assertRaises(PathSafetyError):
                prepare_state_root(value)

    def test_source_and_runtime_homes_cannot_be_state(self):
        for value in (SOURCE_ROOT, SOURCE_ROOT / "runtime", Path.home(), Path.home() / ".claude" / "bus", Path.home() / ".codex" / "ma3", Path(self.root.anchor)):
            with self.subTest(value=value), self.assertRaises(PathSafetyError):
                prepare_state_root(value, create=False)

    def test_legacy_checkout_detected_without_reading_config(self):
        legacy = self.root / "legacy"
        (legacy / "agent_bus").mkdir(parents=True)
        sentinel = legacy / "agent_bus" / "agentbus.py"
        sentinel.write_text("raise RuntimeError('must never execute')", encoding="utf-8")
        with self.assertRaises(PathSafetyError) as error:
            prepare_state_root(legacy / "runtime")
        self.assertEqual(error.exception.code, "legacy_root")
        self.assertFalse((legacy / "runtime").exists())

    def test_unmanaged_directory_is_not_adopted(self):
        state = self.root / "state"
        state.mkdir()
        bait = state / "keep.txt"
        bait.write_text("untouched", encoding="utf-8")
        with self.assertRaises(PathSafetyError):
            prepare_state_root(state)
        self.assertEqual(bait.read_text(encoding="utf-8"), "untouched")

    def test_workspace_must_exist_and_be_disjoint(self):
        state = prepare_state_root(self.root / "state")
        first = self.root / "business-a"
        second = first / "nested"
        second.mkdir(parents=True)
        for candidate, existing in ((self.root / "missing", []), (state, []), (self.root, []), (second, [first]), (first, [second]), (first, [first])):
            with self.subTest(candidate=candidate), self.assertRaises(PathSafetyError):
                validate_workspace(candidate, state, existing)

    def test_hardlinked_database_is_rejected(self):
        state = self.root / "state"
        state.mkdir()
        external = self.root / "external.db"
        external.write_bytes(b"foreign data")
        os.link(external, state / "state.sqlite3")
        with self.assertRaises(PathSafetyError) as error:
            prepare_state_root(state)
        self.assertEqual(error.exception.code, "linked_state")
        self.assertEqual(external.read_bytes(), b"foreign data")

    def test_symlink_directory_is_rejected(self):
        real = self.root / "real"
        real.mkdir()
        alias = self.root / "alias"
        try:
            alias.symlink_to(real, target_is_directory=True)
        except OSError as exc:
            self.skipTest(f"Creating symlinks is unavailable: {exc}")
        with self.assertRaises(PathSafetyError):
            prepare_state_root(alias / "state")
        self.assertFalse((real / "state").exists())

    @unittest.skipUnless(os.name == "nt", "Windows junction behavior")
    def test_junction_directory_is_rejected(self):
        real = self.root / "real"
        real.mkdir()
        alias = self.root / "junction"
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command",
             "New-Item -ItemType Junction -Path $env:MA3_TEST_ALIAS -Target $env:MA3_TEST_TARGET -ErrorAction Stop | Out-Null"],
            env={**os.environ, "MA3_TEST_ALIAS": str(alias), "MA3_TEST_TARGET": str(real)},
            text=True, capture_output=True, timeout=20,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        try:
            with self.assertRaises(PathSafetyError) as error:
                prepare_state_root(alias / "state")
            self.assertEqual(error.exception.code, "linked_path")
            self.assertFalse((real / "state").exists())
        finally:
            # Remove only this generated junction, never its target recursively.
            alias.rmdir()

    @unittest.skipUnless(os.name == "nt", "Windows UNC paths")
    def test_network_path_is_rejected_before_access(self):
        with self.assertRaises(PathSafetyError) as error:
            prepare_state_root(r"\\invalid-ma3-test-host\share\state", create=False)
        self.assertEqual(error.exception.code, "network_path")


if __name__ == "__main__":
    unittest.main()
