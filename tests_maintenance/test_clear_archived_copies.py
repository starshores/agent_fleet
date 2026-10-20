"""Synthetic-only archive/cleanup tests; never inspect user homes or processes."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


PROJECT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("archive_external_development", PROJECT / "scripts/archive_external_development.py")
ARCHIVER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ARCHIVER)
SHELL = shutil.which("pwsh") or shutil.which("powershell")


def quote(value):
    return "'" + str(value).replace("'", "''") + "'"


@unittest.skipUnless(os.name == "nt" and SHELL, "Windows PowerShell is required")
class ClearArchivedCopiesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="ma-cleanup-test-")
        self.root = Path(self.temp.name)
        self.project = self.root / "project"
        self.source = self.root / "copies"
        self.source.mkdir()
        scripts = self.project / "scripts"
        scripts.mkdir(parents=True)
        for name in ("archive_external_development.py", "Clear-Archived-DevelopmentCopies.ps1"):
            shutil.copyfile(PROJECT / "scripts" / name, scripts / name)
        self.script = scripts / "Clear-Archived-DevelopmentCopies.ps1"
        self.archive = self.project / "releases" / "synthetic"
        self.manifest = self.archive / "archive-manifest.json"
        self.report = self.archive / "cleanup-result.json"
        self.junctions = []

    def tearDown(self):
        # Remove only known synthetic junctions themselves before temp cleanup.
        for path in self.junctions:
            if path.exists() and getattr(path.lstat(), "st_file_attributes", 0) & 0x400:
                os.rmdir(path)
        self.temp.cleanup()

    def write(self, relative, text="synthetic evidence"):
        path = self.source / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def pack(self):
        ARCHIVER.archive(self.source, self.archive)

    def invoke(self, *, apply=False, prelude="", source=None):
        command = "function Get-CimInstance { param($ClassName) }\n" + prelude + "\n"
        command += "& " + quote(self.script) + " -Manifest " + quote(self.manifest)
        command += " -SourceRoot " + quote(source or self.source) + " -Python " + quote(sys.executable)
        if apply:
            command += " -Apply"
        return subprocess.run([SHELL, "-NoProfile", "-NonInteractive", "-Command", command],
                              capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)

    def load_report(self):
        return json.loads(self.report.read_text(encoding="utf-8-sig"))

    def test_dry_run_preserves_every_file(self):
        evidence = self.write("nested/evidence.txt")
        self.pack()
        result = self.invoke()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(evidence.exists())
        self.assertFalse(self.report.exists())

    def test_apply_preserves_excluded_home_and_worktree(self):
        evidence = self.write("nested/evidence.txt")
        auth = self.write("home-test/auth.json", "synthetic credential fixture")
        worktree_file = self.write("work/master-agent-v3/source.py")
        self.write("work/master-agent-v3/.git", "synthetic worktree marker")
        self.pack()
        result = self.invoke(apply=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(evidence.exists())
        self.assertTrue(auth.exists())
        self.assertTrue(worktree_file.exists())
        report = self.load_report()
        self.assertEqual(report["Outcome"], "completed")
        self.assertTrue(report["Completed"])
        self.assertIsNone(report["Error"])
        self.assertEqual(report["RemovedFiles"], 1)
        self.assertEqual(report["UnremovedArchivedFiles"], 1)
        self.assertEqual(report["SkippedWorktreePaths"], [str(worktree_file)])

    def test_content_changed_after_archive_blocks_before_delete(self):
        evidence = self.write("evidence.txt")
        self.pack()
        evidence.write_text("new work after archive", encoding="utf-8")
        result = self.invoke(apply=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(evidence.read_text(encoding="utf-8"), "new work after archive")

    def test_archive_manifest_directories_and_exclusions_are_bound(self):
        self.write("nested/evidence.txt")
        self.pack()
        original = json.loads(self.manifest.read_text(encoding="utf-8"))
        for key, replacement in (("directories", ["unarchived"]),
                                 ("excluded", [{"path": "fake", "reason": "fake"}])):
            with self.subTest(key=key):
                changed = {**original, key: replacement}
                self.manifest.write_text(json.dumps(changed), encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "Manifest mismatch"):
                    ARCHIVER.verify(self.manifest)
        self.manifest.write_text(json.dumps(original), encoding="utf-8")

    def junction_swap(self, relative):
        old = self.source / relative
        held = self.source / (relative + "-original")
        outside = self.root / "outside"
        outside.mkdir()
        self.junctions.append(old)
        return old, held, outside, (
            "Move-Item -LiteralPath " + quote(old) + " -Destination " + quote(held) + "; "
            "New-Item -ItemType Junction -Path " + quote(old) + " -Target " + quote(outside) + " | Out-Null; "
        )

    def test_ancestor_reparse_added_after_preflight_blocks_delete(self):
        self.write("nested/evidence.txt")
        self.pack()
        old, held, outside, swap = self.junction_swap("nested")
        external = outside / "evidence.txt"
        external.write_text("synthetic evidence", encoding="utf-8")
        prelude = "function Get-CimInstance { param($ClassName) " + swap + " }"
        result = self.invoke(apply=True, prelude=prelude)
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(external.exists())
        self.assertTrue((held / "evidence.txt").exists())
        report = self.load_report()
        self.assertEqual(report["Outcome"], "failed")
        self.assertFalse(report["Completed"])
        self.assertEqual(report["RemovedFiles"], 0)
        self.assertIn("Linked path", report["Error"]["Message"])

    def test_ancestor_reparse_added_after_final_hash_is_rechecked(self):
        self.write("nested/evidence.txt")
        self.pack()
        old, held, outside, swap = self.junction_swap("nested")
        external = outside / "evidence.txt"
        external.write_text("synthetic evidence", encoding="utf-8")
        prelude = """
$global:cleanupHashCount = 0
function Get-FileHash {
    param($LiteralPath, $Algorithm)
    $value = Microsoft.PowerShell.Utility\Get-FileHash -LiteralPath $LiteralPath -Algorithm $Algorithm
    $global:cleanupHashCount++
    if ($global:cleanupHashCount -eq 2) { SWAP }
    return $value
}
""".replace("SWAP", swap)
        result = self.invoke(apply=True, prelude=prelude)
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(external.exists())
        self.assertEqual(self.load_report()["RemovedFiles"], 0)

    def test_empty_directory_reparse_is_not_followed_and_reports_partial(self):
        evidence = self.write("evidence.txt")
        (self.source / "empty").mkdir()
        self.pack()
        old, held, outside, swap = self.junction_swap("empty")
        prelude = "function Get-CimInstance { param($ClassName) " + swap + " }"
        result = self.invoke(apply=True, prelude=prelude)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(evidence.exists())
        self.assertTrue(outside.is_dir())
        report = self.load_report()
        self.assertEqual(report["Outcome"], "partial")
        self.assertFalse(report["Completed"])
        self.assertEqual(report["RemovedFiles"], 1)
        self.assertIsNotNone(report["Error"])

    def test_mid_apply_change_records_removed_and_remaining(self):
        first = self.write("a.txt")
        second = self.write("b.txt")
        self.pack()
        prelude = """
function Remove-Item {
    param($LiteralPath, [switch]$Force)
    Microsoft.PowerShell.Management\Remove-Item -LiteralPath $LiteralPath -Force:$Force
    if ($LiteralPath -eq FIRST) { [IO.File]::WriteAllText(SECOND, 'changed during cleanup') }
}
""".replace("FIRST", quote(first)).replace("SECOND", quote(second))
        result = self.invoke(apply=True, prelude=prelude)
        self.assertNotEqual(result.returncode, 0)
        report = self.load_report()
        self.assertEqual(report["Outcome"], "partial")
        self.assertFalse(report["Completed"])
        self.assertEqual(report["RemovedPaths"], [str(first)])
        self.assertEqual(report["UnremovedArchivedPaths"], [str(second)])
        self.assertEqual(second.read_text(encoding="utf-8"), "changed during cleanup")

    def test_project_descendant_is_not_an_external_copy_root(self):
        self.write("evidence.txt")
        self.pack()
        result = self.invoke(apply=True, source=self.project / "docs")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Unsafe source root", result.stderr)
        self.assertTrue((self.source / "evidence.txt").exists())


if __name__ == "__main__":
    unittest.main()
