"""Reproduce endpoint/workspace creation-order isolation on synthetic local data.

This is evidence tooling, not a product test or migration. It starts no service
or model and reads no production state, native home, credential or private chat.
Run with Python -B and an explicit, new --output JSON file.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import stat
import sys
import tempfile
import uuid


PROJECT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(PROJECT))

from masteragent import __version__  # noqa: E402
from masteragent.errors import PlatformError  # noqa: E402
from masteragent.paths import PathSafetyError  # noqa: E402
from masteragent.receipt_store import ReceiptStore  # noqa: E402
from masteragent.service import Platform  # noqa: E402
from masteragent.source_store import SourceStore  # noqa: E402


def action(call):
    try:
        call()
        return {"outcome": "accepted"}
    except (PlatformError, PathSafetyError) as exc:
        return {"outcome": "rejected", "code": exc.code}


def run_case(root: Path, endpoint: str, order: str) -> dict:
    root.mkdir()
    workspace = root / "workspace-a"
    workspace.mkdir()
    package = root / "synthetic.json"
    package.write_text(json.dumps({
        "schema_version": 1, "platform_api": 1,
        "package_id": "synthetic", "version": "1.0.0",
        "roles": [{"role_id": "worker", "display_name": "Synthetic worker",
                   "instructions": "Synthetic text only; do not run any tools.",
                   "tools": ["echo"]}],
        "knowledge": {},
        "tools": {"echo": {"adapter": "echo", "description": "Unused"}},
        "workflows": {},
    }), encoding="utf-8")
    platform = Platform(root / "state")
    platform.install("a", package, workspace)
    platform.activate("a")
    source = SourceStore(root / "source", "synthetic.source", initialize=True)
    receiver = ReceiptStore(root / "receiver", "synthetic.destination", "receiver",
                            initialize=True)
    platform.configure_route(
        "controlled", "synthetic.source", "v3", "a", "worker", 1,
        "synthetic.destination", "receiver")
    overlap = source.root if endpoint == "source" else receiver.root
    steps = [
        {"operation": "install/activate A with distinct state and workspace",
         "outcome": "accepted"},
        {"operation": "initialize independent SourceStore and ReceiptStore",
         "outcome": "accepted"},
        {"operation": "configure controlled V3 route", "outcome": "accepted"},
    ]

    def admit(source_id: str):
        claim = platform.claim_source("controlled", source_id, "connector")
        try:
            return platform.admit_source(
                "controlled", 1, source_id, "Synthetic text for boundary review.",
                "connector", claim["claim_epoch"], claim["claim_token"],
                source=source, receiver=receiver)
        finally:
            platform.release_source("controlled", source_id, "connector",
                                    claim["claim_epoch"], claim["claim_token"])

    def binding():
        return platform.bind_route_endpoints("controlled", 1, source, receiver)

    def install_b():
        return platform.install("b", package, overlap)

    if order == "endpoint_first":
        steps.append({"operation": "bind route endpoints", **action(binding)})
        steps.append({"operation": "baseline admit before overlapping installation",
                      **action(lambda: admit("before"))})
        steps.append({"operation": f"install inactive B with workspace equal to {endpoint} root",
                      "expected": "rejected before installation is persisted",
                      **action(install_b)})
        steps.append({"operation": "admit second distinct message on original A route",
                      "expected": "accepted because rejected B must not change A's valid route",
                      **action(lambda: admit("after"))})
        reproduced = (
            steps[-3]["outcome"] == "accepted"
            and steps[-2]["outcome"] == "accepted"
            and steps[-1] == {
                "operation": "admit second distinct message on original A route",
                "expected": "accepted because rejected B must not change A's valid route",
                "outcome": "rejected", "code": "route_endpoint_overlap",
            }
        )
    else:
        steps.append({"operation": f"install inactive B with workspace equal to {endpoint} root",
                      **action(install_b)})
        steps.append({"operation": "bind endpoints after B workspace reservation",
                      "expected": "rejected with route_endpoint_overlap",
                      **action(binding)})
        reproduced = False

    return {
        "case": f"{endpoint}_{order}",
        "steps": steps,
        "installation_ids": [item["installation_id"]
                             for item in platform.status()["installations"]],
        "admission_count": len(platform.admissions("controlled")),
        "attempt_count": len(platform.attempts("a")),
        "creation_order_gap_reproduced": reproduced,
    }


def checked_cleanup(root: Path, created: Path, parent: Path, token: str) -> None:
    """Delete only the exact new directory, without crossing aliases."""
    if (root != created or root.resolve() != created or root.parent != parent
            or not root.name.startswith("fleet-architecture-reassessment-")
            or root == PROJECT or root in PROJECT.parents or PROJECT in root.parents):
        raise RuntimeError("Temporary root identity or scope changed; refusing cleanup.")
    marker = root / ".architecture-reassessment-owner"
    if marker.read_text(encoding="utf-8") != token:
        raise RuntimeError("Temporary root owner marker changed; refusing cleanup.")
    for path in (root, *root.rglob("*")):
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise RuntimeError("Temporary tree contains an alias; refusing recursive cleanup.")
        resolved = path.resolve()
        if resolved != root and root not in resolved.parents:
            raise RuntimeError("Temporary tree escapes its root; refusing cleanup.")
    shutil.rmtree(root)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    output = args.output.resolve()
    if output.exists():
        raise ValueError("Evidence output already exists; choose a new filename.")
    if output.parent != Path(__file__).resolve().parent:
        raise ValueError("Evidence must be saved beside this review script.")

    parent = Path(tempfile.gettempdir()).resolve()
    if parent == PROJECT or PROJECT in parent.parents:
        raise RuntimeError("Temporary directory must be outside the project.")
    root = Path(tempfile.mkdtemp(prefix="fleet-architecture-reassessment-", dir=parent)).resolve()
    created, token = root, str(uuid.uuid4())
    report = {
        "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
        "product_version": __version__,
        "scope": "synthetic_local_public_API_only",
        "real_model": False,
        "service_started": False,
        "product_source_modified": False,
        "temporary_root": str(root),
        "source_sha256": {
            relative: hashlib.sha256((PROJECT / relative).read_bytes()).hexdigest()
            for relative in (
                "masteragent/service.py", "masteragent/deployment.py",
                "masteragent/paths.py", "masteragent/routing.py",
                "masteragent/source_store.py", "masteragent/receipt_store.py",
            )
        },
        "cases": [],
    }
    (root / ".architecture-reassessment-owner").write_text(token, encoding="utf-8")
    try:
        for endpoint in ("source", "receiver"):
            for order in ("endpoint_first", "workspace_first"):
                report["cases"].append(run_case(root / f"{endpoint}-{order}", endpoint, order))
    finally:
        checked_cleanup(root, created, parent, token)
        report["temporary_root_removed"] = not root.exists()
    report["gap_reproduced_cases"] = [
        case["case"] for case in report["cases"]
        if case["creation_order_gap_reproduced"]
    ]
    report["script_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "evidence": str(output),
        "gap_reproduced_cases": report["gap_reproduced_cases"],
        "temporary_root_removed": report["temporary_root_removed"],
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
