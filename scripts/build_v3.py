"""Build a reproducible stdlib-only v3 executable archive from an allowlist.

No legacy configuration, business data, runtime histories, or credentials are
read by this builder. Run the result with Python 3.11 or newer.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import tempfile
import zipfile


SOURCE_ROOT = Path(__file__).resolve().parent.parent


def build(output: Path, force: bool = False) -> dict:
    output = output.absolute()
    if output.suffix != ".pyz":
        raise ValueError("Output must have the .pyz extension.")
    if output.exists() and not force:
        raise ValueError("Output already exists; choose another path or pass --force.")
    files = sorted((SOURCE_ROOT / "masteragent").glob("*.py"))
    if not files or not (SOURCE_ROOT / "masteragent" / "cli.py").is_file():
        raise ValueError("The v3 source package is missing.")
    entries = {}
    for path in files:
        if path.is_symlink():
            raise ValueError("Source package entries must not be symlinks.")
        entries[f"masteragent/{path.name}"] = path.read_bytes()
    # Fixed local assets only: no source tree recursion, personal config or CDN.
    asset_root = SOURCE_ROOT / "masteragent" / "workbench_assets"
    for name in ("index.html", "styles.css", "app.mjs", "state.mjs", "api.mjs", "ui.mjs", "views.mjs", "navigation.mjs", "commands.mjs", "history.mjs"):
        path = asset_root / name
        if asset_root.is_symlink() or path.is_symlink() or not path.is_file():
            raise ValueError("The workbench requires regular packaged asset files.")
        entries[f"masteragent/workbench_assets/{name}"] = path.read_bytes()
    entries["__main__.py"] = b"from masteragent.cli import main\nraise SystemExit(main())\n"
    manifest = {"format": 1, "files": {name: hashlib.sha256(data).hexdigest() for name, data in sorted(entries.items())}}
    entries["BUILD-MANIFEST.json"] = (json.dumps(manifest, sort_keys=True, indent=2) + "\n").encode("utf-8")
    output.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix="ma3-build-", suffix=".tmp", dir=output.parent)
    os.close(descriptor)
    temporary = Path(temporary)
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, data in sorted(entries.items()):
                item = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                item.compress_type = zipfile.ZIP_DEFLATED
                item.external_attr = 0o644 << 16
                archive.writestr(item, data)
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)
    return {"path": str(output), "sha256": hashlib.sha256(output.read_bytes()).hexdigest(), "files": sorted(entries)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    try:
        print(json.dumps(build(args.output, args.force), sort_keys=True))
    except (ValueError, OSError) as exc:
        parser.exit(2, f"Build failed: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
