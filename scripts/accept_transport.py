"""Verify a selected development bundle using disposable synthetic local stores."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import zipfile


def sha(data):
    return hashlib.sha256(data).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", required=True, type=Path)
    parser.add_argument("--old-bundle", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    bundle, old_bundle = args.bundle.resolve(strict=True), args.old_bundle.resolve(strict=True)
    with zipfile.ZipFile(bundle) as archive:
        manifest = json.loads(archive.read("BUILD-MANIFEST.json"))
        for name, digest in manifest["files"].items():
            assert sha(archive.read(name)) == digest, name
    sys.path.insert(0, str(bundle))
    import masteragent
    from masteragent.errors import PlatformError
    from masteragent.service import Platform
    from masteragent.source_store import SourceStore
    from masteragent.receipt_store import ReceiptStore
    from masteragent.transport import TransportWorker
    assert str(bundle) in masteragent.__file__, masteragent.__file__
    evidence = {
        "version": masteragent.__version__, "bundle_sha256": sha(bundle.read_bytes()),
        "manifest_files": len(manifest["files"]), "scope": "synthetic_local_only",
        "real_model": False, "legacy_fleet": False, "steps": [],
    }
    with tempfile.TemporaryDirectory(prefix="ma-dev4-package-acceptance-") as temporary:
        root = Path(temporary).resolve()
        platform = Platform(root / "state")
        assert platform.status()["active"] is None
        evidence["steps"].append({"phase": "empty", "active": None})
        endpoints = {}
        def serve_once(installation, source, receiver):
            config = root / ("transport-" + installation + ".json")
            config.write_text(json.dumps({
                "route_id": "route-" + installation, "owner_id": "package-cli-check",
                "source_root": str(source.root), "source_namespace": "source." + installation,
                "receipt_root": str(receiver.root), "destination_namespace": "destination." + installation,
                "destination_id": "inbox",
            }), encoding="utf-8")
            completed = subprocess.run(
                [sys.executable, "-B", str(bundle), "--state-root", str(platform.state_root),
                 "transport-serve", "--transport-config", str(config), "--max-seconds", "1"],
                cwd=root, capture_output=True, text=True, encoding="utf-8", timeout=15)
            assert completed.returncode == 0, completed.stderr
            report = json.loads(completed.stdout)
            assert report["stopped"] and not report["errors"], report
            return report

        for installation, role in (("a", "author"), ("b", "reviewer")):
            workspace = root / ("workspace-" + installation)
            workspace.mkdir()
            package = root / (installation + ".json")
            package.write_text(json.dumps({
                "schema_version": 1, "platform_api": 1,
                "package_id": "synthetic-" + installation, "version": "1.0.0",
                "roles": [{"role_id": role, "display_name": role, "instructions": "Synthetic text only",
                           "tools": ["echo"]}],
                "knowledge": {}, "tools": {"echo": {"adapter": "echo", "description": "Local echo"}},
                "workflows": {},
            }), encoding="utf-8")
            platform.install(installation, package, workspace)
            source = SourceStore(root / ("source-" + installation), "source." + installation, initialize=True)
            receiver = ReceiptStore(root / ("receiver-" + installation), "destination." + installation,
                                    "inbox", initialize=True)
            endpoints[installation] = (role, source, receiver)
        last_output = None
        for index, installation in enumerate(("a", "b", "a"), 1):
            active = platform.activate(installation)
            role, source, receiver = endpoints[installation]
            route_id = "route-" + installation
            current = next((r for r in platform.routes() if r["route_id"] == route_id), None)
            route = platform.configure_route(
                route_id, "source." + installation, "v3", installation, role, active["generation"],
                "destination." + installation, "inbox", expected_epoch=current["route_epoch"] if current else 0)
            if current is None:
                platform.bind_route_endpoints(route_id, route["route_epoch"], source, receiver)
            worker = TransportWorker(platform, route_id, "package-check", source, receiver)
            text = ("中文🙂 e\u0301\r\n" * 2000 + "完整尾段 END") if last_output is None else last_output
            source.publish(index, text)
            if index == 2:
                assert serve_once(installation, source, receiver)["sources"] == 1
            admission = worker.transfer_source(index)
            same = worker.transfer_source(index)
            assert same["task_id"] == admission["task_id"]
            actor = platform.bind(installation, role)
            result = platform.run_as(actor, admission["task_id"], "echo")
            delivery = next(r for r in platform.deliveries(installation) if r["task_id"] == admission["task_id"])
            if index == 3:
                original = receiver.receive
                def lose_response(envelope):
                    original(envelope)
                    raise OSError("synthetic response loss")
                receiver.receive = lose_response
                held = worker.send_result(delivery["delivery_id"])
                assert held["state"] == "held"
                receiver.receive = original
                assert worker.run_once()["deliveries"] == []
                replacement = ReceiptStore(root / "replacement", "destination.a", "inbox", initialize=True)
                try:
                    platform.reconcile_delivery(delivery["delivery_id"], replacement)
                except PlatformError as exc:
                    assert exc.code == "delivery_destination_mismatch", exc.code
                else:
                    raise AssertionError("replacement receiver authorized reconciliation")
                sent = platform.reconcile_delivery(delivery["delivery_id"], receiver)
            else:
                if index == 2:
                    assert serve_once(installation, source, receiver)["deliveries"] == 1
                    sent = platform.delivery_status(delivery["delivery_id"])
                else:
                    sent = worker.send_result(delivery["delivery_id"])
            assert sent["state"] == "received" and sent["send_count"] == 1
            envelope = platform.delivery_envelope(delivery["delivery_id"])
            stored = receiver.read_text(envelope)
            assert stored == text and stored.endswith("完整尾段 END")
            last_output = stored
            evidence["steps"].append({
                "phase": installation, "generation": active["generation"], "task_id": admission["task_id"],
                "admission_id": admission["admission_id"], "delivery_id": delivery["delivery_id"],
                "text_bytes": len(stored.encode("utf-8")), "text_sha256": sha(stored.encode("utf-8")),
                "delivery": sent["state"], "send_count": sent["send_count"],
                "response_loss_reconciled": index == 3,
            })
            platform.deactivate(installation)
        assert len(platform.admissions()) == 3
        assert sum(len(platform.attempts(i)) for i in ("a", "b")) == 3
        evidence["attempts"] = 3
        evidence["standalone_cli_transport_serve"] = {"admitted": 1, "delivered": 1, "clean_exit": True}
        before = sha(platform.db_path.read_bytes())
        old = subprocess.run(
            [sys.executable, "-B", str(old_bundle), "--state-root", str(platform.state_root), "status"],
            cwd=root, capture_output=True, text=True, encoding="utf-8", timeout=20)
        assert old.returncode != 0
        response = json.loads(old.stderr)
        assert response["error"]["code"] == "unsupported_schema", response
        assert sha(platform.db_path.read_bytes()) == before
        evidence["old_reader"] = {"sha256": sha(old_bundle.read_bytes()),
                                  "error": "unsupported_schema", "database_unchanged": True}
        evidence["temporary_runtime_cleaned_on_exit"] = True
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"version": evidence["version"], "passed": True, "output": str(args.output)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
