import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import zipfile


ROOT = Path(__file__).resolve().parents[1]


class BundleTests(unittest.TestCase):
    def test_standalone_clean_reproducible_build(self):
        with tempfile.TemporaryDirectory(prefix="ma3-bundle-") as directory:
            root = Path(directory).resolve()
            archives = [root / "one.pyz", root / "two.pyz"]
            for archive in archives:
                result = subprocess.run([sys.executable, "-B", str(ROOT / "scripts" / "build_v3.py"), "--output", str(archive)], cwd=root, text=True, capture_output=True, check=False, timeout=20)
                self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(archives[0].read_bytes(), archives[1].read_bytes())
            with zipfile.ZipFile(archives[0]) as archive:
                names = set(archive.namelist())
                self.assertTrue(all(name.startswith("masteragent/") or name in {"__main__.py", "BUILD-MANIFEST.json"} for name in names))
                manifest = json.loads(archive.read("BUILD-MANIFEST.json"))
                self.assertEqual(names - {"BUILD-MANIFEST.json"}, set(manifest["files"]))
                for name, digest in manifest["files"].items():
                    self.assertEqual(hashlib.sha256(archive.read(name)).hexdigest(), digest)
            state = root / "uninitialized"
            result = subprocess.run([sys.executable, "-B", str(archives[0]), "--state-root", str(state), "status"], cwd=root, text=True, capture_output=True, check=False, timeout=20)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(json.loads(result.stdout)["initialized"])
            self.assertFalse(state.exists())


if __name__ == "__main__":
    unittest.main()
