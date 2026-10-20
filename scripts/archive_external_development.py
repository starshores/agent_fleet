"""Archive this project's explicitly selected development copies; never delete.

The archive is local evidence, not a portable deployment or credential backup.
Only the source directory named by the caller is read; native homes are excluded.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import stat
import zipfile

def digest(data):
    return hashlib.sha256(data).hexdigest()

def boundary(path):
    path = Path(path)
    if not path.is_absolute() or str(path).startswith("\\\\"):
        raise ValueError("Use an explicit local absolute path")
    for item in (path, *path.parents):
        try:
            info = item.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError("Linked path is not allowed: " + str(item))
    return path.resolve()

def excluded(path, is_dir):
    name = path.name.lower()
    if name == ".git":
        return "git_control_requires_worktree_removal"
    if is_dir and (name.startswith("home-") or name.endswith("-home") or name in
                   {"home", "codex_home", "codex-home", "native_home", "native-home", "sessions", "archived_sessions"}):
        return "native_home_or_session_reserved_for_user"
    if not is_dir and (name in {"auth.json", "credentials.json", ".credentials.json", ".env"} or name.startswith(".env.")):
        return "credentials_reserved_for_user"
    return None

def inventory(source):
    files, skip, directories = [], [], []
    def visit(directory):
        for p in sorted(directory.iterdir()):
            info = p.lstat()
            relative = p.relative_to(source).as_posix()
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                skip.append({"path": relative, "reason": "reparse_not_followed"})
                continue
            reason = excluded(p, stat.S_ISDIR(info.st_mode))
            if reason:
                skip.append({"path": relative, "reason": reason})
            elif stat.S_ISDIR(info.st_mode):
                directories.append(relative)
                visit(p)
            elif stat.S_ISREG(info.st_mode):
                if info.st_nlink != 1:
                    raise ValueError("Hard-linked file is not eligible: " + str(p))
                files.append(p)
            else:
                raise ValueError("Unexpected file type: " + str(p))
    visit(source)
    return files, skip, directories

def archive(source, destination):
    source, destination = boundary(source), boundary(destination)
    if not source.is_dir() or source == Path(source.anchor) or source == Path.home().resolve():
        raise ValueError("Choose a dedicated source directory")
    if destination == source or source in destination.parents:
        raise ValueError("Archive must be outside the source")
    destination.mkdir(parents=True, exist_ok=False)
    files, skip, directories = inventory(source)
    archive_path = destination / "development-evidence.zip"
    rows = []
    with zipfile.ZipFile(archive_path, "x", zipfile.ZIP_DEFLATED) as z:
        for path in files:
            before = path.stat()
            payload = path.read_bytes()
            after = path.stat()
            if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                raise ValueError("Source changed while archiving: " + str(path))
            name = path.relative_to(source).as_posix()
            z.writestr(name, payload)
            rows.append({"path": name, "bytes": len(payload), "sha256": digest(payload)})
        z.writestr("ARCHIVE-MANIFEST.json", json.dumps({"source_root": str(source), "files": rows,
                      "excluded": skip, "directories": directories}, ensure_ascii=False, indent=2))
    with zipfile.ZipFile(archive_path) as z:
        for row in rows:
            assert digest(z.read(row["path"])) == row["sha256"]
            assert digest((source / row["path"]).read_bytes()) == row["sha256"]
    report = {"source_root": str(source), "archive": str(archive_path), "archive_sha256": digest(archive_path.read_bytes()),
              "files": rows, "excluded": skip, "directories": directories, "verified": True,
              "notes": "Local evidence only. Native homes/credentials/sessions excluded. Embedded historical absolute paths are unchanged; archived databases are not runnable deployments."}
    (destination / "archive-manifest.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"files": len(rows), "source_bytes": sum(row["bytes"] for row in rows),
            "archive_bytes": archive_path.stat().st_size, "excluded": skip, "archive": str(archive_path)}

def verify(report_path, *, verify_source=True):
    report_path = boundary(report_path)
    report = json.loads(report_path.read_text(encoding="utf-8"))
    source, archive_path = Path(report["source_root"]), boundary(report["archive"])
    if verify_source:
        source = boundary(source)
    if archive_path.parent != report_path.parent or digest(archive_path.read_bytes()) != report["archive_sha256"]:
        raise ValueError("Archive identity changed")
    with zipfile.ZipFile(archive_path) as z:
        manifest = json.loads(z.read("ARCHIVE-MANIFEST.json"))
        if any(manifest.get(key) != report.get(key)
               for key in ("files", "source_root", "directories", "excluded")):
            raise ValueError("Manifest mismatch")
        for row in report["files"]:
            relative = PurePosixPath(row["path"])
            if relative.is_absolute() or '..' in relative.parts or '\\' in row["path"] or ':' in row["path"]:
                raise ValueError("Unsafe archive member")
            if digest(z.read(row["path"])) != row["sha256"]:
                raise ValueError("Archive mismatch: " + row["path"])
            if not verify_source:
                continue
            p = boundary(source / row["path"])
            if source not in p.parents or digest(p.read_bytes()) != row["sha256"]:
                raise ValueError("Source/archive mismatch: " + row["path"])
    return {"verified": True, "source_verified": verify_source, "files": len(report["files"]), "source_root": str(source)}

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    create = sub.add_parser("archive")
    create.add_argument("--source", required=True)
    create.add_argument("--destination", required=True)
    check = sub.add_parser("verify")
    check.add_argument("--manifest", required=True)
    check.add_argument("--archive-only", action="store_true", help="Verify sealed evidence after source cleanup; never authorizes deletion")
    args = parser.parse_args()
    result = archive(args.source, args.destination) if args.action == "archive" else verify(args.manifest, verify_source=not args.archive_only)
    print(json.dumps(result, ensure_ascii=False))

if __name__ == "__main__":
    main()
