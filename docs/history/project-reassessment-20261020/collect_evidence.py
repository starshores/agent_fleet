"""Collect local, allowlisted project-review evidence without executing the product.

Use a NEW output filename for every capture. This does not run the three
reproductions, start services, inspect credentials, or read live application state.
"""
from __future__ import annotations

import argparse
import ast
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess
import zipfile

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parents[2]
REPORT = PROJECT / "docs/MASTERAGENT_PROJECT_REASSESSMENT_20261020.md"
EXTRA_SOURCES = (
    "AGENTS.override.md",
    "docs/MASTERAGENT_NEXT_SESSION.md",
    "docs/MASTERAGENT_3_TAKEOVER_20261013.md",
    "docs/MASTERAGENT_3_1_UI_COVERAGE.md",
    "docs/MASTERAGENT_3_1_ROUTING_ENTRYPOINTS.md",
    "docs/MASTERAGENT_3_1_NATIVE_APPROVAL_CONTRACT.md",
    "masteragent/contracts.py",
    "masteragent/deployment.py",
    "masteragent/coordination.py",
    "masteragent/codex_app_server.py",
    "masteragent/source_store.py",
    "masteragent/receipt_store.py",
    "tests_v3/test_route_endpoints.py",
    "tests_v3/test_worker.py",
    "agent_bus/worker_daemon.py",
    "agent_bus/bus/exec.py",
    "releases/release-catalog.json",
    "releases/3.0.0/release-manifest.json",
    "releases/3.1.0-dev.4/release-manifest.json",
    "releases/3.1.0-dev.4/evidence/python-tests-final.log",
    "releases/3.1.0-dev.4/evidence/node-tests.log",
    "releases/3.1.0-dev.4/evidence/acceptance-final.json",
)
FINDINGS = (
    ("R01", "P1", "runtime_child_input", "synthetic_real_domain_fake_native",
     ["masteragent/runtime.py", "masteragent/cli.py"], ["B-11"]),
    ("R02", "P1", "endpoint_workspace_creation_order", "synthetic_real_domain",
     ["masteragent/service.py", "masteragent/routing.py"], ["B-02", "B-08"]),
    ("R03", "P1", "uncertain_request_identity_recreation", "frontend_module_memory_double",
     ["masteragent/workbench_assets/commands.mjs", "masteragent/service.py"], ["B-07", "B-08"]),
    ("R04", "P1", "delivery_exception_discoverability", "static",
     ["masteragent/workbench_query.py", "masteragent/workbench_assets/views.mjs"], ["B-07"]),
    ("R05", "P2", "two_delivery_paths", "static",
     ["masteragent/workbench_commands.py", "masteragent/deliveries.py"], ["B-10", "B-11"]),
    ("R06", "P2", "deployment_connection_continuity", "static",
     ["masteragent/runtime_config.py", "masteragent/workbench_query.py"], ["B-09"]),
    ("R07", "P2", "runtime_vendor_coupling", "static",
     ["masteragent/runtime_config.py", "masteragent/worker.py"], ["D"]),
    ("R08", "P2", "observation_continuity", "static",
     ["masteragent/workbench_assets/app.mjs"], ["C-01", "C-02"]),
    ("R09", "P2", "coordinated_restore", "static_and_plan",
     ["masteragent/storage.py", "masteragent/routing.py", "masteragent/transport.py"], ["C-06", "C-07"]),
    ("R10", "P2", "unbounded_delivery_query", "static_not_benchmarked",
     ["masteragent/deliveries.py", "masteragent/transport.py", "masteragent/delivery_schema.py"], ["B-06"]),
    ("R11", "P2", "current_fact_drift", "static",
     ["README.md", "docs/MASTERAGENT_VERSION_ROADMAP.md", "masteragent/workbench_assets/views.mjs"], []),
    ("R12", "P1_conditional_before_tools", "resource_takeover_fencing", "future_capability_gate",
     ["masteragent/members.py", "tests_v3/test_members.py"], ["D"]),
    ("R13", "P2", "source_delivery_reproducibility", "git_metadata_and_archive",
     [".gitignore", "scripts/build_v3.py", "releases/README.md"], []),
)


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def metadata(path: Path) -> dict:
    resolved = path.resolve(strict=True)
    resolved.relative_to(PROJECT)
    if not resolved.is_file():
        raise ValueError(f"Not a file: {path}")
    data = resolved.read_bytes()
    return {"path": resolved.relative_to(PROJECT).as_posix(),
            "bytes": len(data), "sha256": sha(data)}


def local_links(path: Path) -> list[Path]:
    paths = []
    for target in re.findall(r"\[[^\]]*\]\(([^)]+)\)", path.read_text(encoding="utf-8")):
        if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", target) or target.startswith("#"):
            continue
        candidate = (path.parent / target.split("#", 1)[0]).resolve()
        candidate.relative_to(PROJECT)
        # The generated index links to itself; hashing it would be circular.
        if candidate == HERE / "evidence.json":
            continue
        if not candidate.exists():
            raise FileNotFoundError(candidate)
        paths.append(candidate)
    return paths


def git(*args: str) -> str:
    result = subprocess.run(["git", "-C", str(PROJECT), *args],
                            capture_output=True, check=True, encoding="utf-8")
    return result.stdout.strip()


def verify_bundle(version: str, compare_current: bool) -> dict:
    release = PROJECT / "releases" / version
    manifest = json.loads((release / "release-manifest.json").read_text(encoding="utf-8"))
    binary = release / f"masteragent-{version}.pyz"
    data = binary.read_bytes()
    digest = sha(data)
    assert digest == manifest["sha256"]
    with zipfile.ZipFile(binary) as archive:
        entries = json.loads(archive.read("BUILD-MANIFEST.json"))["files"]
        for name, expected in entries.items():
            assert sha(archive.read(name)) == expected, name
        product_entries = [name for name in entries if name.startswith("masteragent/")]
        mismatches = []
        if compare_current:
            for name in product_entries:
                if archive.read(name) != (PROJECT / name).read_bytes():
                    mismatches.append(name)
            assert not mismatches, mismatches
    return {"version": version, "schema_version": manifest["schema_version"],
            "binary": binary.relative_to(PROJECT).as_posix(), "sha256": digest,
            "bytes": len(data), "manifest_entries_verified": len(entries),
            "product_entries": len(product_entries),
            "current_source_compared": compare_current,
            "current_source_mismatches": mismatches if compare_current else None}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    if output.parent != HERE or output.exists():
        raise ValueError("Choose a new evidence filename beside this script.")

    paths = {REPORT, Path(__file__).resolve(), HERE / "README.md"}
    paths.update(local_links(REPORT))
    paths.update(local_links(HERE / "README.md"))
    paths.update(PROJECT / name for name in EXTRA_SOURCES)
    paths.update(PROJECT / name for row in FINDINGS for name in row[4])
    files = [metadata(path) for path in sorted(paths)]
    modules, imports = [], []
    for path in sorted((PROJECT / "masteragent").glob("*.py")):
        modules.append(path.name)
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        for node in ast.walk(tree):
            names = ([alias.name for alias in node.names] if isinstance(node, ast.Import)
                     else [node.module or ""] if isinstance(node, ast.ImportFrom) else [])
            imports.extend({"file": path.name, "line": node.lineno, "module": name}
                           for name in names if name == "agent_bus" or name.startswith("agent_bus."))
    assert not imports
    dev_manifest = json.loads((PROJECT / "releases/3.1.0-dev.4/release-manifest.json")
                              .read_text(encoding="utf-8"))
    python_tail = (PROJECT / "releases/3.1.0-dev.4/evidence/python-tests-final.log").read_text(
        encoding="utf-8").splitlines()[-5:]
    node_tail = (PROJECT / "releases/3.1.0-dev.4/evidence/node-tests.log").read_text(
        encoding="utf-8").splitlines()[-9:]
    architecture = json.loads((HERE / "architecture_endpoint_overlap_result.json").read_text(encoding="utf-8"))
    runtime = json.loads((HERE / "runtime_child_reply_result.json").read_text(encoding="utf-8"))
    product = json.loads((HERE / "product_uncertain_request_reproduction.json").read_text(encoding="utf-8"))
    assert len(architecture["cases"]) == 4 and len(architecture["gap_reproduced_cases"]) == 2
    assert architecture["temporary_root_removed"] and runtime["temporary_root_removed"]
    assert runtime["difference_reproduced"]
    assert runtime["native_subprocess_attempts"] == runtime["native_probe_attempts"] == 0
    assert product["observed"]["simulated_distinct_saved_requests"] == 2
    source_hash_count = 0
    for source in (architecture, runtime):
        for name, expected in source["source_sha256"].items():
            assert sha((PROJECT / name).read_bytes()) == expected, name
            source_hash_count += 1
    for record in product["source_hashes"]:
        assert sha((PROJECT / record["path"]).read_bytes()) == record["sha256"], record["path"]
        source_hash_count += 1
    result = {
        "format": 1, "assessment_id": "masteragent-project-reassessment-20261020",
        "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
        "scope": "whole_project_reassessment_local_only",
        "not_performed": ["full_test_suite_rerun", "real_model_execution", "browser_crash_test",
                          "production_state_read", "credential_read", "fleet_start_or_cutover",
                          "source_fix", "external_publication"],
        "git": {
            "head": git("rev-parse", "HEAD"),
            "head_commit_date": git("show", "-s", "--format=%cI", "HEAD"),
            "tracked_core_files": len(git("ls-files", "--", "masteragent").splitlines()),
            "tracked_v3_test_files": len(git("ls-files", "--", "tests_v3").splitlines()),
            "archive_ignored": bool(git("check-ignore", "releases")),
        },
        "bundles": [verify_bundle("3.0.0", False), verify_bundle("3.1.0-dev.4", True)],
        "static": {"python_modules_parsed": len(modules),
                   "direct_legacy_imports": imports, "scope": "AST import statements only"},
        "historical_tests_not_rerun": {"manifest_counts": dev_manifest["tests"],
                                       "python_log_tail": python_tail, "node_log_tail": node_tail},
        "new_reproductions": {
            "R01": {"file": "runtime_child_reply_result.json", "difference_reproduced": True,
                    "scope": runtime["scope"], "native_process_attempts": 0},
            "R02": {"file": "architecture_endpoint_overlap_result.json", "cases": 4,
                    "gap_cases": architecture["gap_reproduced_cases"], "attempts": 0},
            "R03": {"file": "product_uncertain_request_reproduction.json",
                    "observed": product["observed"], "scope": product["evidence_kind"]},
            "source_hashes_rechecked": source_hash_count,
        },
        "findings": [{"id": row[0], "priority": row[1], "topic": row[2],
                      "evidence_level": row[3], "source_paths": row[4],
                      "related_existing_work": row[5], "implementation_changed": False}
                     for row in FINDINGS],
        "files": files,
        "integrity_note": "Hashes establish the local reviewed bytes; they are not external attestation.",
    }
    with output.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    print(json.dumps({"evidence": str(output), "files": len(files),
                      "findings": len(FINDINGS), "all_assertions_passed": True}))


if __name__ == "__main__":
    main()
