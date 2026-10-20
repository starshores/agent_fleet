"""CLI transport checks over synthetic stores and a deterministic local adapter."""
from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import signal
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from masteragent.cli import main
from masteragent.service import Platform


class TransportCommandTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="ma31-transport-cli-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.state = self.root / "state"
        self.source = self.root / "source"
        self.receipt = self.root / "receipt"
        self.config = {
            "route_id": "synthetic-route", "owner_id": "synthetic-transport",
            "source_root": str(self.source), "source_namespace": "synthetic.input",
            "receipt_root": str(self.receipt), "destination_namespace": "synthetic.output",
            "destination_id": "results",
        }
        self.sequence = 0
        self.config_file = self.document(self.config)

    def document(self, value):
        self.sequence += 1
        path = self.root / f"document-{self.sequence}.json"
        path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        return path

    def cli(self, *args, expected=0):
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = main([str(arg) for arg in args])
        self.assertEqual(expected, code, stderr.getvalue())
        output = stderr.getvalue() if code else stdout.getvalue()
        return json.loads(output)

    def source_cli(self, command, *args, **kwargs):
        return self.cli(command, "--source-root", self.source,
                        "--source-namespace", self.config["source_namespace"], *args, **kwargs)

    def transport_cli(self, command, *args, **kwargs):
        return self.cli("--state-root", self.state, command,
                        "--transport-config", self.config_file, *args, **kwargs)

    def platform(self):
        platform = Platform(self.state)
        workspace = self.root / "workspace"
        workspace.mkdir()
        package = self.document({
            "schema_version": 1, "platform_api": 1,
            "package_id": "synthetic", "version": "1.0.0",
            "roles": [{"role_id": "reviewer", "display_name": "Reviewer",
                       "instructions": "Synthetic only", "tools": ["echo"]}],
            "knowledge": {}, "tools": {"echo": {"adapter": "echo", "description": "Echo"}},
            "workflows": {},
        })
        platform.install("demo", package, workspace)
        platform.activate("demo")
        platform.configure_route(
            self.config["route_id"], self.config["source_namespace"], "v3",
            "demo", "reviewer", 1, self.config["destination_namespace"], self.config["destination_id"])
        return platform

    def initialize_endpoints(self):
        self.source_cli("source-init")
        self.cli("receipt-init", "--receipt-root", self.receipt,
                 "--destination-namespace", self.config["destination_namespace"],
                 "--destination-id", self.config["destination_id"])

    def test_source_publish_query_preserves_typed_ids_and_is_readonly(self):
        initialized = self.source_cli("source-init")
        self.assertTrue(initialized["initialized"])
        self.assertFalse(self.state.exists())
        text = "合成文本 🙂 e\u0301\r\n尾段 SENTINEL"
        for source_id in (1, "1"):
            published = self.source_cli("source-publish", "--input-file", self.document({"source_id": source_id, "text": text}))
            self.assertEqual(type(source_id), type(published["envelope"]["source_id"]))
            self.assertFalse(published["source_confirmed"])
        before = (self.source / "sources.sqlite3").read_bytes()
        records = self.source_cli("source-query")
        self.assertEqual(2, len(records))
        self.assertEqual(1, len(self.source_cli("source-query", "--limit", "1")))
        for source_id in (1, "1"):
            record = self.source_cli("source-query", "--source-id-file", self.document(source_id))
            self.assertEqual(type(source_id), type(record["envelope"]["source_id"]))
            self.assertEqual(text, record["envelope"]["text"])
        self.assertEqual(before, (self.source / "sources.sqlite3").read_bytes())

    def test_source_query_and_publish_never_initialize_missing_store(self):
        self.source_cli("source-query", expected=2)
        self.source_cli("source-publish", "--input-file", self.document({"source_id": 1, "text": "synthetic"}), expected=2)
        self.assertFalse(self.source.exists())
        self.assertFalse(self.state.exists())

    def test_source_malformed_payloads_and_unknown_kinds_do_not_mutate(self):
        self.source_cli("source-init")
        before = (self.source / "sources.sqlite3").read_bytes()
        for payload in (
                {"source_id": 1}, {"source_id": 1, "text": "synthetic", "target": "other"},
                {"source_id": 1, "text": "synthetic", "kind": "notice"},
                {"source_id": True, "text": "synthetic"},
                {"source_id": 1, "text": "x" * 64001}):
            with self.subTest(payload_fields=sorted(payload)):
                self.source_cli("source-publish", "--input-file", self.document(payload), expected=2)
        for source_id in (True, None, 1.5, {}, []):
            with self.subTest(source_id_type=type(source_id)):
                self.source_cli("source-query", "--source-id-file", self.document(source_id), expected=2)
        for limit in ("0", "-1", "1001"):
            self.source_cli("source-query", "--limit", limit, expected=2)
        self.assertEqual(before, (self.source / "sources.sqlite3").read_bytes())

    def test_source_id_file_is_bounded_and_rejects_non_json_values(self):
        self.source_cli("source-init")
        for raw in ("NaN", "Infinity", "-Infinity", '"one" "two"', '"\\ud800"', '"' + "a" * 4096 + '"'):
            path = self.document(None)
            path.write_text(raw, encoding="utf-8")
            self.source_cli("source-query", "--source-id-file", path, expected=2)
        self.assertEqual([], self.source_cli("source-query"))

    def test_runtime_configuration_is_rejected_without_loading_or_initialization(self):
        with patch("masteragent.cli.load_runtime_config", side_effect=AssertionError("model configuration loaded")):
            for command in ("source-init", "source-publish", "source-query"):
                extra = ["--input-file", str(self.document({"source_id": 1, "text": "synthetic"}))] if command == "source-publish" else []
                result = self.cli("--runtime-config", self.root / "never-read.json", command,
                                  "--source-root", self.source, "--source-namespace", "synthetic.input", *extra, expected=2)
                self.assertEqual("invalid_command", result["error"]["code"])
            for command in ("transport-bind", "transport-once", "transport-serve"):
                extra = ["--expected-epoch", "1"] if command == "transport-bind" else []
                result = self.cli("--state-root", self.state, "--runtime-config", self.root / "never-read.json",
                                  command, "--transport-config", self.config_file, *extra, expected=2)
                self.assertEqual("invalid_command", result["error"]["code"])
        self.assertFalse(self.source.exists())
        self.assertFalse(self.receipt.exists())
        self.assertFalse(self.state.exists())

    def test_source_operations_reject_core_state_and_transport_requires_it(self):
        result = self.cli("--state-root", self.state, "source-init", "--source-root", self.source,
                          "--source-namespace", "synthetic.input", expected=2)
        self.assertEqual("invalid_command", result["error"]["code"])
        result = self.cli("transport-once", "--transport-config", self.config_file, expected=2)
        self.assertEqual("missing_state_root", result["error"]["code"])
        self.transport_cli("transport-bind", expected=2)
        self.assertFalse(self.source.exists())
        self.assertFalse(self.state.exists())

    def test_strict_config_rejects_unknown_missing_duplicate_or_nonfinite_fields(self):
        malformed = [
            {**self.config, "model": "unused"}, {key: value for key, value in self.config.items() if key != "owner_id"},
            {**self.config, "owner_id": False}, {**self.config, "source_root": "relative"},
            {**self.config, "receipt_root": str(self.source / "nested")},
            {**self.config, "source_root": str(self.state / "nested")},
        ]
        with patch("masteragent.transport_commands.Platform", side_effect=AssertionError("state opened")):
            for payload in malformed:
                with self.subTest(fields=sorted(payload)):
                    path = self.document(payload)
                    self.cli("--state-root", self.state, "transport-once", "--transport-config", path, expected=2)
            for raw in ('{"route_id":"a","route_id":"b"}', '{"owner_id":NaN}', "[]"):
                path = self.document(None)
                path.write_text(raw, encoding="utf-8")
                self.cli("--state-root", self.state, "transport-once", "--transport-config", path, expected=2)
        self.assertFalse(self.state.exists())
        self.assertFalse(self.source.exists())

    def test_transport_does_not_initialize_core_or_endpoints(self):
        self.transport_cli("transport-bind", "--expected-epoch", "1", expected=2)
        self.assertFalse(self.state.exists())
        platform = self.platform()
        before = platform.db_path.read_bytes()
        self.transport_cli("transport-bind", "--expected-epoch", "1", expected=2)
        self.assertEqual(before, platform.db_path.read_bytes())
        self.assertFalse(self.source.exists())
        self.assertFalse(self.receipt.exists())

    def test_transport_limits_rejected_before_opening_state(self):
        with patch("masteragent.transport_commands.Platform", side_effect=AssertionError("state opened")):
            for duration in ("0", "-1", "nan", "inf"):
                self.transport_cli("transport-serve", "--max-seconds", duration, expected=2)
            for epoch in ("0", "-1"):
                self.transport_cli("transport-bind", "--expected-epoch", epoch, expected=2)
        self.assertFalse(self.state.exists())

    def test_transport_once_calls_worker_without_model_config(self):
        self.platform()
        self.initialize_endpoints()
        factory = Mock()
        factory.return_value.run_once.return_value = {"sources": [], "deliveries": [], "errors": []}
        with patch.dict(sys.modules, {"masteragent.transport": SimpleNamespace(TransportWorker=factory)}), \
                patch("masteragent.cli.load_runtime_config", side_effect=AssertionError("model configuration loaded")):
            result = self.transport_cli("transport-once")
        self.assertEqual({"sources": [], "deliveries": [], "errors": []}, result)
        positional = factory.call_args.args
        self.assertEqual((self.config["route_id"], self.config["owner_id"]), positional[1:3])
        self.assertEqual(self.source, positional[3].root)
        self.assertEqual(self.receipt, positional[4].root)
        factory.return_value.run_once.assert_called_once_with()
        factory.return_value.run.assert_not_called()

    def test_serve_ctrl_c_requests_stop_and_restores_handler(self):
        self.platform()
        self.initialize_endpoints()
        previous = signal.getsignal(signal.SIGINT)
        worker = Mock()

        def run(*, max_seconds):
            self.assertEqual(2.5, max_seconds)
            signal.getsignal(signal.SIGINT)(signal.SIGINT, None)
            self.assertTrue(worker.stop.called)
            return {"sources": [], "deliveries": [], "errors": [], "reason": "stop_requested"}

        worker.run.side_effect = run
        factory = Mock(return_value=worker)
        with patch.dict(sys.modules, {"masteragent.transport": SimpleNamespace(TransportWorker=factory)}):
            result = self.transport_cli("transport-serve", "--max-seconds", "2.5")
        self.assertEqual("stop_requested", result["reason"])
        self.assertIs(previous, signal.getsignal(signal.SIGINT))
        worker.run_once.assert_not_called()

    def test_real_once_closed_loop_preserves_text_and_does_not_execute_models(self):
        platform = self.platform()
        self.initialize_endpoints()
        self.transport_cli("transport-bind", "--expected-epoch", "1")
        text = "仅合成来源，中文 🙂 e\u0301\r\n尾段 SENTINEL"
        self.source_cli("source-publish", "--input-file", self.document({"source_id": "message-1", "text": text}))
        with patch("masteragent.cli.load_runtime_config", side_effect=AssertionError("model configuration loaded")):
            self.transport_cli("transport-once")
        admissions = platform.admissions(self.config["route_id"])
        self.assertEqual(1, len(admissions))
        admission = admissions[0]
        self.assertTrue(admission["source_confirmed"])
        self.assertEqual([], platform.attempts("demo"))
        self.assertEqual([], self.source_cli("source-query"))
        source = self.source_cli("source-query", "--source-id-file", self.document("message-1"))
        self.assertTrue(source["transport_completed"])
        self.assertEqual(admission["admission_id"], source["admission_id"])
        # Execution remains a distinct deterministic test adapter operation.
        completed = platform.run("demo", admission["task_id"], "echo")
        with patch("masteragent.cli.load_runtime_config", side_effect=AssertionError("model configuration loaded")):
            self.transport_cli("transport-once")
            self.transport_cli("transport-once")
        deliveries = platform.deliveries("demo")
        self.assertEqual(1, len(deliveries))
        self.assertEqual("received", deliveries[0]["state"])
        self.assertEqual(1, len(platform.attempts("demo")))
        from masteragent.receipt_store import ReceiptStore
        receiver = ReceiptStore(self.receipt, self.config["destination_namespace"], self.config["destination_id"])
        envelope = platform.delivery_envelope(deliveries[0]["delivery_id"])
        self.assertEqual(completed["output"], envelope["text"])
        self.assertEqual("received", receiver.query(envelope)["status"])
        self.assertIsNone(deliveries[0]["agent_processed"])
        self.assertIsNone(deliveries[0]["user_read"])


if __name__ == "__main__":
    unittest.main()
