"""Real CLI dispatch and public read projections over synthetic routing state."""
from contextlib import redirect_stdout, redirect_stderr
import io
import json
from pathlib import Path
import tempfile
import unittest

from masteragent.cli import main
from masteragent.service import Platform
from masteragent.workbench_query import WorkbenchQueries


class RoutingCliTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="ma31-route-cli-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.state = self.root / "state"
        self.p = Platform(self.state)
        workspace = self.root / "workspace"
        workspace.mkdir()
        package = self.root / "business.json"
        package.write_text(json.dumps({"schema_version": 1, "platform_api": 1,
            "package_id": "synthetic", "version": "1.0.0",
            "roles": [{"role_id": "reviewer", "display_name": "Reviewer", "instructions": "Synthetic only", "tools": ["echo"]}],
            "knowledge": {}, "tools": {"echo": {"adapter": "echo", "description": "Echo"}}, "workflows": {}}), encoding="utf-8")
        self.p.install("demo", package, workspace)
        self.p.activate("demo")
        self.lease = self.root / "transport-lease.json"
        self.destination = self.root / "receipt-store"
        self.sequence = 0

    def cli(self, *args, with_state=True, expected=0):
        stdout, stderr = io.StringIO(), io.StringIO()
        command = (["--state-root", str(self.state)] if with_state else []) + list(args)
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = main(command)
        self.assertEqual(expected, code, stderr.getvalue())
        return json.loads(stdout.getvalue() if code == 0 else stderr.getvalue())

    def command(self, action, payload, *args, expected=0):
        self.sequence += 1
        path = self.root / f"payload-{self.sequence}.json"
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return self.cli("routing-command", "--action", action, "--payload-file", str(path), *args, expected=expected)

    def configure(self):
        return self.command("configure", {"route_id": "text-route", "source_namespace": "synthetic.input",
            "owner_lane": "v3", "installation_id": "demo", "role_id": "reviewer", "generation": 1,
            "destination_namespace": "synthetic.output", "destination_id": "receipt"})

    def admit(self):
        self.configure()
        claimed = self.command("claim-source", {"route_id": "text-route", "source_id": 42, "owner_id": "connector"},
                               "--lease-output", str(self.lease))
        self.assertNotIn("claim_token", claimed)
        self.assertTrue(self.lease.is_file())
        saved = self.command("admit-source", {"route_id": "text-route", "route_epoch": 1,
            "source_id": 42, "text": "合成文本 🌏\r\n尾段完整", "owner_id": "connector"}, "--lease-file", str(self.lease))
        self.assertFalse(saved["source_confirmed"])
        return saved

    def test_public_cli_end_to_end_preserves_receipt_and_never_prints_lease(self):
        admission = self.admit()
        confirmed = self.command("confirm-source", {"admission_id": admission["admission_id"], "owner_id": "connector"},
                                 "--lease-file", str(self.lease))
        self.assertTrue(confirmed["source_confirmed"])
        self.cli("run", "demo", admission["task_id"], "--tool", "echo")
        delivery = self.cli("routing-query", "deliveries", "--installation-id", "demo")[0]
        self.assertEqual("pending", delivery["state"])
        self.cli("receipt-init", "--receipt-root", str(self.destination), "--destination-namespace", "synthetic.output",
                 "--destination-id", "receipt", with_state=False)
        result = self.cli("delivery-send", delivery["delivery_id"], "--receipt-root", str(self.destination), "--sender-id", "sender")
        self.assertEqual("received", result["state"])
        receipt = self.cli("receipt-query", delivery["delivery_id"], "--receipt-root", str(self.destination))
        self.assertEqual("received", receipt["status"])
        self.assertEqual(result["receipt"]["receipt_id"], receipt["receipt"]["receipt_id"])
        self.assertIsNone(result["agent_processed"])
        self.assertIsNone(result["user_read"])
        token = json.loads(self.lease.read_text(encoding="utf-8"))["claim_token"]
        self.assertNotIn(token, json.dumps([confirmed, delivery, result, receipt]))
        self.assertEqual(1, len(self.p.attempts("demo")))

    def test_existing_lease_file_does_not_claim_or_overwrite(self):
        self.configure()
        self.lease.write_text("preserve-this-file", encoding="utf-8")
        before = self.p.db_path.read_bytes()
        self.command("claim-source", {"route_id": "text-route", "source_id": "new", "owner_id": "connector"},
                     "--lease-output", str(self.lease), expected=2)
        self.assertEqual("preserve-this-file", self.lease.read_text(encoding="utf-8"))
        self.assertEqual(before, self.p.db_path.read_bytes())

    def test_malformed_payload_rejected_before_mutation_and_claims_require_lease_output(self):
        self.configure()
        before = self.p.db_path.read_bytes()
        self.command("controls", {"route_id": "text-route", "expected_epoch": 1, "unsupported": True}, expected=2)
        self.command("claim-source", {"route_id": "text-route", "source_id": "new", "owner_id": "connector"}, expected=2)
        self.assertEqual(before, self.p.db_path.read_bytes())

    def test_queries_never_initialize_state_or_receipt_store(self):
        missing = self.root / "missing"
        self.cli("--state-root", str(missing), "routing-query", "routes", with_state=False, expected=2)
        self.assertFalse(missing.exists())
        admission = self.admit()
        self.p.run("demo", admission["task_id"], "echo")
        delivery = self.p.deliveries()[0]
        response = self.cli("receipt-query", delivery["delivery_id"], "--receipt-root", str(self.destination))
        self.assertEqual("unavailable", response["status"])
        self.assertFalse(self.destination.exists())
        self.assertEqual("pending", self.p.delivery_status(delivery["delivery_id"])["state"])

    def test_public_workbench_receipts_are_bounded_scoped_and_exclude_private_transport_data(self):
        admission = self.admit()
        self.p.run("demo", admission["task_id"], "echo")
        delivery = self.p.deliveries()[0]
        self.cli("receipt-init", "--receipt-root", str(self.destination), "--destination-namespace", "synthetic.output",
                 "--destination-id", "receipt", with_state=False)
        self.cli("delivery-send", delivery["delivery_id"], "--receipt-root", str(self.destination), "--sender-id", "sender")
        queries = WorkbenchQueries(self.state)
        before = self.p.db_path.read_bytes()
        detail = queries.task("demo", admission["task_id"], generation=1)["data"]
        history = queries.task_history("demo", admission["task_id"], generation=1, record_generation=1, section="results")["data"]
        self.assertEqual(before, self.p.db_path.read_bytes())
        self.assertFalse(detail["deliveries"]["truncated"])
        visible = detail["deliveries"]["items"][0]
        self.assertEqual("received", visible["state"])
        self.assertEqual(visible, history["items"][0]["delivery"])
        self.assertIsNone(visible["agent_processed"])
        self.assertIsNone(visible["user_read"])
        encoded = json.dumps(visible)
        for private in ("claim_hash", "lease_hash", "claim_token", "destination_binding", "envelope", "policy", str(self.destination)):
            self.assertNotIn(private, encoded)


if __name__ == "__main__":
    unittest.main()
