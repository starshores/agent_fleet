"""Synthetic endpoint binding and schema 5 upgrade fault tests; no native runtime."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
import sqlite3
import unittest
from unittest.mock import patch

from masteragent import storage
from masteragent.errors import PlatformError
from masteragent.receipt_store import ReceiptStore
from masteragent.service import Platform
from tests_v3 import test_routing


class SyntheticSource:
    def __init__(self, root):
        root.mkdir()
        self.value = {
            "root": str(root), "source_namespace": "synthetic.source.v1",
            "source_instance": "synthetic-source-instance", "protocol_version": "ma-source-v1",
            "max_text_bytes": 64000, "max_envelope_bytes": 1048576}

    def descriptor(self):
        return dict(self.value)


class EndpointBase(unittest.TestCase):
    setUp = test_routing.RoutingTests.setUp
    handle = test_routing.RoutingTests.handle
    install = test_routing.RoutingTests.install
    configure = test_routing.RoutingTests.configure
    claim_source = test_routing.RoutingTests.claim_source
    counts = test_routing.RoutingTests.counts

    def assert_code(self, code, action, *args, **kwargs):
        with self.assertRaises(PlatformError) as caught:
            action(*args, **kwargs)
        self.assertEqual(code, caught.exception.code)


class RouteEndpointTests(EndpointBase):
    def setUp(self):
        super().setUp()
        self.source = SyntheticSource(self.root / "source")
        self.receiver = ReceiptStore(self.root / "receiver", "synthetic.destination.v1", "receiver",
                                     initialize=True, clock=lambda: self.now)

    def bind_endpoints(self, epoch=1):
        return self.platform.bind_route_endpoints("controlled", epoch, self.source, self.receiver)

    def admit(self, *, claim=None, source_id="message", epoch=1, **updates):
        claim = claim or self.claim_source(source_id)
        kwargs = {"source": self.source, "receiver": self.receiver, **updates}
        return self.platform.admit_source("controlled", epoch, source_id, "Synthetic task", "connector",
                                          claim["claim_epoch"], claim["claim_token"], **kwargs)

    def test_binding_persists_and_same_binding_is_idempotent_after_admission(self):
        bound = self.bind_endpoints()
        self.assertEqual(bound, self.handle().route_endpoints("controlled"))
        admitted = self.admit()
        self.assertEqual(bound, self.bind_endpoints())
        self.assertEqual(bound["destination_binding"], admitted["policy"]["destination_binding"])
        self.assertEqual(bound["source_binding"], admitted["policy"]["source_binding"])
        self.assertEqual(admitted, self.platform.source_admission("controlled", "message"))
        self.assertIsNone(self.platform.source_admission("controlled", 1))

    def test_binding_and_evidence_commit_atomically(self):
        before = self.counts()
        with patch.object(self.platform, "_routing_event", side_effect=RuntimeError("synthetic evidence failure")):
            with self.assertRaises(RuntimeError):
                self.bind_endpoints()
        self.assertIsNone(self.platform.route_endpoints("controlled"))
        self.assertEqual(before, self.counts())

    def test_competing_endpoint_bindings_have_one_winner(self):
        other = SyntheticSource(self.root / "other-source")
        other.value["source_instance"] = "other-instance"
        def bind(source):
            try:
                return self.handle().bind_route_endpoints("controlled", 1, source, self.receiver)
            except PlatformError as exc:
                return exc.code
        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(bind, (self.source, other)))
        self.assertEqual(1, sum(isinstance(value, dict) for value in outcomes))
        self.assertEqual(1, outcomes.count("route_endpoints_immutable"))
        self.assertEqual(0, self.counts()["tasks"])

    def test_unbound_route_requires_explicit_binding_for_endpoint_arguments(self):
        self.assertIsNone(self.platform.route_endpoints("controlled"))
        self.assert_code("route_endpoints_unbound", self.admit)
        self.assertEqual(0, self.counts()["tasks"])

    def test_binding_does_not_backfill_old_admission_or_allow_retargeting(self):
        claim = self.claim_source()
        old = self.platform.admit_source("controlled", 1, "message", "Synthetic task", "connector",
                                         claim["claim_epoch"], claim["claim_token"])
        self.assert_code("route_has_admissions", self.bind_endpoints)
        self.assertNotIn("destination_binding", self.platform.admission(old["admission_id"])["policy"])
        self.assertIsNone(self.platform.route_endpoints("controlled"))
        self.assertEqual(old, self.platform.source_admission("controlled", "message"))

    def test_bound_route_requires_both_fresh_descriptors_even_for_replay(self):
        self.bind_endpoints()
        claim = self.claim_source()
        self.admit(claim=claim)
        before = self.counts()
        for missing in ({"source": None}, {"receiver": None}, {"source": None, "receiver": None}):
            self.assert_code("route_endpoint_preflight_required", self.admit, claim=claim, **missing)
        self.assertEqual(before, self.counts())

    def test_descriptor_read_failure_leaves_no_task_or_attempt(self):
        self.bind_endpoints()
        for endpoint in (self.source, self.receiver):
            with self.subTest(endpoint=type(endpoint).__name__):
                claim = self.claim_source(type(endpoint).__name__)
                before = self.counts()
                with patch.object(endpoint, "descriptor", side_effect=OSError("synthetic endpoint unavailable")):
                    self.assert_code("source_unavailable" if endpoint is self.source else "destination_unavailable",
                                     self.admit, claim=claim, source_id=type(endpoint).__name__)
                self.assertEqual(before, self.counts())
                self.assertEqual(0, self.counts()["attempts"])

    def test_descriptor_calls_never_hold_core_write_transaction(self):
        for endpoint in (self.source, self.receiver):
            original = endpoint.descriptor
            def observed(original=original):
                with closing(sqlite3.connect(self.platform.db_path, timeout=0)) as db:
                    db.execute("BEGIN IMMEDIATE")
                    db.rollback()
                return original()
            endpoint.descriptor = observed
        self.bind_endpoints()
        self.admit()
        self.assertEqual(1, self.counts()["tasks"])

    def test_source_instance_root_or_protocol_change_prevents_new_task(self):
        self.bind_endpoints()
        claim = self.claim_source()
        original = dict(self.source.value)
        changes = {
            "source_instance": "replaced",
            "root": str(self.root / "replacement"),
            "max_text_bytes": 1,
        }
        for field, value in changes.items():
            with self.subTest(field=field):
                self.source.value = {**original, field: value}
                self.assert_code("route_endpoint_mismatch", self.admit, claim=claim)
                self.assert_code("route_endpoints_immutable" if field != "max_text_bytes" else "route_endpoint_limits",
                                 self.bind_endpoints)
        self.source.value = original
        self.assertEqual(0, self.counts()["tasks"])
        self.admit(claim=claim)

    def test_destination_instance_replacement_between_binding_and_admission_fails(self):
        self.bind_endpoints()
        replacement = ReceiptStore(self.root / "replacement", "synthetic.destination.v1", "receiver", initialize=True)
        self.assert_code("route_endpoint_mismatch", self.admit, receiver=replacement)
        self.assertEqual(0, self.counts()["tasks"])

    def test_post_admission_replacement_cannot_become_first_delivery_destination(self):
        bound = self.bind_endpoints()
        admission = self.admit()
        attempt = self.platform.claim(self.actor, admission["task_id"], "echo")
        self.platform.complete(self.actor, attempt["attempt_id"], attempt["lease_token"], "full result")
        delivery = self.platform.deliveries()[0]
        self.assertEqual(bound["destination_binding"], delivery["destination_binding"])
        replacement = ReceiptStore(self.root / "replacement", "synthetic.destination.v1", "receiver", initialize=True)
        self.assert_code("delivery_destination_mismatch", self.platform.send_delivery_once,
                         delivery["delivery_id"], "sender", replacement)
        received = self.platform.send_delivery_once(delivery["delivery_id"], "sender", self.receiver)
        self.assertEqual("received", received["state"])
        self.assertEqual(1, self.counts()["attempts"])

    def test_bound_admission_failure_rolls_back_task_and_binding_snapshot_together(self):
        binding = self.bind_endpoints()
        claim = self.claim_source()
        before = self.counts()
        with patch.object(self.platform, "_routing_event", side_effect=RuntimeError("synthetic admission failure")):
            with self.assertRaises(RuntimeError):
                self.admit(claim=claim)
        self.assertEqual(before, self.counts())
        self.assertEqual(binding, self.platform.route_endpoints("controlled"))
        self.assertIsNone(self.platform.source_admission("controlled", "message"))

    def test_same_root_database_recreated_before_first_send_is_rejected(self):
        self.bind_endpoints()
        admission = self.admit()
        attempt = self.platform.claim(self.actor, admission["task_id"], "echo")
        self.platform.complete(self.actor, attempt["attempt_id"], attempt["lease_token"], "full result")
        delivery = self.platform.deliveries()[0]
        # All paths here belong to this test's disposable TemporaryDirectory.
        self.receiver.db_path.unlink()
        recreated = ReceiptStore(self.receiver.root, "synthetic.destination.v1", "receiver", initialize=True)
        self.assert_code("delivery_destination_mismatch", self.platform.claim_delivery,
                         delivery["delivery_id"], "sender", recreated)
        self.assertEqual("pending", self.platform.delivery_status(delivery["delivery_id"])["state"])

    def test_bound_route_cannot_change_destination_or_role_but_can_freeze(self):
        bound = self.bind_endpoints()
        for changes in ({"role_id": "other"}, {"destination_id": "other"},
                        {"destination_namespace": "other.namespace"}):
            self.assert_code("route_endpoints_immutable", self.configure, expected_epoch=1, **changes)
        route = self.platform.set_route_controls("controlled", 1, admission_open=False)
        self.assertEqual(bound, self.bind_endpoints(epoch=route["route_epoch"]))
        route = self.configure(expected_epoch=2, owner_lane="legacy", lease_seconds=90)
        self.assertEqual("legacy", route["owner_lane"])
        self.assertEqual(bound, self.platform.route_endpoints("controlled"))

    def test_new_generation_keeps_bound_endpoints(self):
        bound = self.bind_endpoints()
        self.platform.deactivate("a")
        self.platform.activate("a")
        route = self.configure(expected_epoch=1, generation=2)
        self.assertEqual(2, route["generation"])
        self.assertEqual(bound, self.platform.route_endpoints("controlled"))
        admitted = self.admit(epoch=2)
        self.assertEqual(2, admitted["generation"])
        self.assertEqual(bound["destination_binding"], admitted["policy"]["destination_binding"])

    def test_larger_route_limit_than_fixed_receiver_is_rejected(self):
        self.route = self.configure(expected_epoch=1, destination_limit=100)
        limited = ReceiptStore(self.root / "limited", "synthetic.destination.v1", "receiver",
                               initialize=True, max_text_bytes=100)
        self.platform.bind_route_endpoints("controlled", 2, self.source, limited)
        self.assert_code("route_endpoint_limits", self.configure, expected_epoch=2, destination_limit=101)
        self.assertEqual(2, self.platform.routes()[0]["route_epoch"])

    def test_binding_invalid_namespace_limits_or_epoch_writes_no_evidence(self):
        before = self.counts()
        self.assert_code("stale_route", self.bind_endpoints, epoch=2)
        self.source.value["source_namespace"] = "other.source"
        self.assert_code("route_source_mismatch", self.bind_endpoints)
        self.source.value["source_namespace"] = "synthetic.source.v1"
        self.source.value["max_text_bytes"] = 1
        self.assert_code("route_endpoint_limits", self.bind_endpoints)
        self.assertEqual(before, self.counts())
        self.assertIsNone(self.platform.route_endpoints("controlled"))

    def test_binding_rejects_state_workspace_or_other_endpoint_overlap(self):
        original = self.source.value["root"]
        for root in (self.root / "state", self.root / "state" / "nested",
                     self.root / "workspace-a", self.root / "workspace-a" / "nested",
                     self.receiver.root, self.root):
            with self.subTest(root=root):
                self.source.value["root"] = str(root)
                self.assert_code("route_endpoint_overlap", self.bind_endpoints)
        self.source.value["root"] = original
        self.assertIsNone(self.platform.route_endpoints("controlled"))
        self.assertEqual(0, self.counts()["tasks"])

    def test_same_source_concurrent_admissions_preserve_exactly_one_task_and_binding(self):
        bound = self.bind_endpoints()
        claim = self.claim_source()
        def admit(_):
            return self.handle().admit_source(
                "controlled", 1, "message", "Synthetic task", "connector", claim["claim_epoch"],
                claim["claim_token"], source=self.source, receiver=self.receiver)
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(admit, range(8)))
        self.assertEqual(1, len({row["task_id"] for row in results}))
        self.assertEqual(1, self.counts()["tasks"])
        self.assertTrue(all(row["policy"]["destination_binding"] == bound["destination_binding"] for row in results))

    def test_release_fences_previous_owner_without_deleting_claim_epoch(self):
        first = self.claim_source(1)
        self.assert_code("source_claim_owner", self.platform.release_source, "controlled", 1, "wrong",
                         first["claim_epoch"], first["claim_token"])
        released = self.platform.release_source("controlled", 1, "connector", first["claim_epoch"], first["claim_token"])
        self.assertNotIn("claim_token", released)
        second = self.claim_source(1, owner="next")
        self.assertEqual(first["claim_epoch"] + 1, second["claim_epoch"])
        self.assert_code("source_claim_owner", self.platform.release_source, "controlled", 1, "connector",
                         first["claim_epoch"], first["claim_token"])
        self.assert_code("source_claim_owner", self.platform.release_source, "controlled", "1", "next",
                         second["claim_epoch"], second["claim_token"])

    def test_source_claim_reports_its_fixed_lease_policy_across_reconfiguration(self):
        self.configure(expected_epoch=1, lease_seconds=15)
        claim = self.claim_source()
        self.assertEqual(15, claim["lease_seconds"])
        self.assertEqual("v3", claim["owner_lane"])
        self.assertEqual(self.now + 15, claim["expires_at"])
        self.configure(expected_epoch=2, lease_seconds=300)
        self.now += 5
        args = ("controlled", "message", "connector", claim["claim_epoch"], claim["claim_token"])
        renewed = self.platform.renew_source(*args)
        self.assertEqual(15, renewed["lease_seconds"])
        self.assertEqual(self.now + 15, renewed["expires_at"])
        self.platform.release_source(*args)
        replacement = self.claim_source()
        self.assertEqual(300, replacement["lease_seconds"])
        self.assertEqual(self.now + 300, replacement["expires_at"])

    def test_source_claim_reports_lane_at_claim_time_not_old_route_read(self):
        old_route = self.platform.routes()[0]
        self.configure(expected_epoch=1, owner_lane="legacy")
        claim = self.claim_source()
        self.assertEqual("v3", old_route["owner_lane"])
        self.assertEqual("legacy", claim["owner_lane"])
        self.assertEqual(2, claim["route_epoch"])

    def test_delivery_schedule_is_scoped_readonly_and_tracks_expiry_without_replay(self):
        self.bind_endpoints()
        admission = self.admit()
        attempt = self.platform.claim(self.actor, admission["task_id"], "echo")
        self.platform.complete(self.actor, attempt["attempt_id"], attempt["lease_token"], "full result")
        delivery_id = self.platform.deliveries()[0]["delivery_id"]
        before = self.platform.db_path.read_bytes()
        schedule = self.platform.delivery_schedule("controlled")
        self.assertEqual([delivery_id], [row["delivery_id"] for row in schedule["ready"]])
        self.assertIsNone(schedule["next_wake_seconds"])
        self.assertEqual(before, self.platform.db_path.read_bytes())
        claim = self.platform.claim_delivery(delivery_id, "sender", self.receiver)
        args = (delivery_id, "sender", claim["claim_epoch"], claim["claim_token"])
        self.platform.begin_delivery(*args)
        schedule = self.platform.delivery_schedule("controlled")
        self.assertEqual([], schedule["ready"])
        self.assertEqual(60, schedule["next_wake_seconds"])
        self.now += 61
        before = self.platform.db_path.read_bytes()
        expired = self.platform.delivery_schedule("controlled")
        self.assertEqual(["sending"], [row["state"] for row in expired["ready"]])
        self.assertEqual(before, self.platform.db_path.read_bytes())
        self.configure(route_id="other", source_namespace="other.source")
        self.assertEqual({"ready": [], "next_wake_seconds": None}, self.platform.delivery_schedule("other"))

    def test_frozen_delivery_schedule_wakes_only_to_fence_expired_send(self):
        self.configure(expected_epoch=1, lease_seconds=15, retry_seconds=60)
        self.bind_endpoints(epoch=2)
        admission = self.admit(epoch=2)
        attempt = self.platform.claim(self.actor, admission["task_id"], "echo")
        self.platform.complete(self.actor, attempt["attempt_id"], attempt["lease_token"], "full result")
        delivery_id = self.platform.deliveries()[0]["delivery_id"]
        self.platform.set_route_controls("controlled", 2, delivery_open=False)
        self.assertEqual({"ready": [], "next_wake_seconds": None},
                         self.platform.delivery_schedule("controlled"))
        self.platform.set_route_controls("controlled", 3, delivery_open=True)
        claim = self.platform.claim_delivery(delivery_id, "sender", self.receiver)
        args = (delivery_id, "sender", claim["claim_epoch"], claim["claim_token"])
        self.platform.set_route_controls("controlled", 4, delivery_open=False)
        self.assertEqual({"ready": [], "next_wake_seconds": None},
                         self.platform.delivery_schedule("controlled"))
        self.platform.set_route_controls("controlled", 5, delivery_open=True)
        self.platform.begin_delivery(*args)
        self.platform.set_route_controls("controlled", 6, delivery_open=False)
        self.assertEqual({"ready": [], "next_wake_seconds": 15}, self.platform.delivery_schedule("controlled"))
        self.now += 16
        schedule = self.platform.delivery_schedule("controlled")
        self.assertEqual([delivery_id], [row["delivery_id"] for row in schedule["ready"]])
        self.assertIsNone(schedule["next_wake_seconds"])
        self.assert_code("delivery_held", self.platform.claim_delivery, delivery_id, "replacement", self.receiver)
        self.assertEqual("held", self.platform.delivery_status(delivery_id)["state"])
        self.assertEqual({"ready": [], "next_wake_seconds": None},
                         self.platform.delivery_schedule("controlled"))


class RouteEndpointMigrationTests(EndpointBase):
    def setUp(self):
        super().setUp()
        claim = self.claim_source()
        admission = self.platform.admit_source("controlled", 1, "message", "Historical synthetic task", "connector",
                                               claim["claim_epoch"], claim["claim_token"])
        attempt = self.platform.claim(self.actor, admission["task_id"], "echo")
        self.platform.complete(self.actor, attempt["attempt_id"], attempt["lease_token"], "Historical complete result")
        self.platform.release_source("controlled", "message", "connector", claim["claim_epoch"], claim["claim_token"])
        self.state5 = self.root / "state5"
        self.state5.mkdir()
        self.database = self.state5 / "state.sqlite3"
        self.fixture = Path(__file__).parent / "fixtures" / "schema_v5.sql"
        with closing(sqlite3.connect(self.database)) as target, self.platform._db() as source:
            target.executescript(self.fixture.read_text(encoding="utf-8"))
            for table in storage.V5_COLUMNS:
                rows = source.execute("SELECT * FROM " + table).fetchall()
                if rows:
                    placeholders = ",".join("?" for _ in storage.V5_COLUMNS[table])
                    target.executemany("INSERT INTO " + table + " VALUES (" + placeholders + ")", rows)
            target.commit()
            storage.verify_schema(target, version=5)
        self.admission_id = admission["admission_id"]

    def rows(self, path=None):
        with closing(sqlite3.connect(path or self.database)) as db:
            names = [row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")]
            return {name: db.execute("SELECT * FROM " + name).fetchall() for name in names}

    def update(self, sql, values=()):
        with closing(sqlite3.connect(self.database)) as db:
            db.execute(sql, values)
            db.commit()

    def test_frozen_schema5_is_distinct_and_readers_do_not_upgrade(self):
        before = self.database.read_bytes()
        self.assertNotIn("route_endpoints", self.rows())
        self.assert_code("migration_required", Platform, self.state5)
        self.assertEqual(before, self.database.read_bytes())
        self.assertFalse((self.state5 / "backups").exists())

    def test_upgrade_preserves_legacy_routes_admissions_and_unbound_pending_delivery(self):
        before = self.rows()
        result = storage.migrate_state(self.state5)
        self.assertEqual(6, result["schema_version"])
        self.assertEqual(before, self.rows(result["backup_path"]))
        after = self.rows()
        for table, rows in before.items():
            if table not in ("actor_tokens", "migration_log"):
                self.assertEqual(rows, after[table], table)
        self.assertEqual([], after["route_endpoints"])
        platform = Platform(self.state5)
        self.assertIsNone(platform.route_endpoints("controlled"))
        self.assertNotIn("destination_binding", platform.admission(self.admission_id)["policy"])
        self.assertIsNone(platform.deliveries()[0]["destination_binding"])
        with closing(sqlite3.connect(self.database)) as db:
            storage.verify_schema(db)
            self.assert_code("unsupported_schema", storage.verify_schema, db, version=5)
        before = self.database.read_bytes()
        self.assertFalse(storage.migrate_state(self.state5)["migrated"])
        self.assertEqual(before, self.database.read_bytes())

    def test_new_routing_busy_states_refuse_before_creating_backup(self):
        cases = [
            ("UPDATE source_claims SET expires_at=2000", ()),
            ("UPDATE result_deliveries SET state='claimed',expires_at=0", ()),
            ("UPDATE result_deliveries SET state='sending',expires_at=0", ()),
            ("UPDATE result_deliveries SET state='held',expires_at=0", ()),
        ]
        original = self.database.read_bytes()
        for sql, values in cases:
            with self.subTest(sql=sql):
                self.database.write_bytes(original)
                self.update(sql, values)
                before = self.database.read_bytes()
                with patch.object(storage.time, "time", return_value=1000.0):
                    self.assert_code("migration_busy", storage.migrate_state, self.state5)
                self.assertEqual(before, self.database.read_bytes())
                self.assertFalse((self.state5 / "backups").exists())

    def test_legacy_running_or_held_blocks_even_if_expired(self):
        for state in ("running", "held"):
            with self.subTest(state=state):
                self.update("DELETE FROM legacy_claims")
                self.update("INSERT INTO legacy_claims VALUES (?,1,'owner','lease',0,?,NULL,'before','before')",
                            (self.admission_id, state))
                before = self.database.read_bytes()
                self.assert_code("migration_busy", storage.migrate_state, self.state5)
                self.assertEqual(before, self.database.read_bytes())
                self.assertFalse((self.state5 / "backups").exists())

    def test_late_migration_failure_rolls_back_and_keeps_frozen_backup(self):
        before, rows = self.database.read_bytes(), self.rows()
        verify = storage.verify_schema
        def fail_final(db, version=storage.SCHEMA_VERSION):
            if version == storage.SCHEMA_VERSION:
                raise RuntimeError("synthetic final verification failure")
            return verify(db, version=version)
        with patch.object(storage, "verify_schema", side_effect=fail_final):
            with self.assertRaises(RuntimeError):
                storage.migrate_state(self.state5)
        self.assertEqual(before, self.database.read_bytes())
        self.assertEqual(rows, self.rows())
        backups = list((self.state5 / "backups").glob("*.sqlite3"))
        self.assertEqual(1, len(backups))
        self.assertEqual(rows, self.rows(backups[0]))
        with closing(sqlite3.connect(backups[0])) as db:
            storage.verify_schema(db, version=5)

    def test_schema5_foreign_key_failure_rolls_back_without_fabricating_binding(self):
        self.update("UPDATE source_claims SET route_id='missing'")
        before, rows = self.database.read_bytes(), self.rows()
        self.assert_code("migration_integrity", storage.migrate_state, self.state5)
        self.assertEqual(before, self.database.read_bytes())
        self.assertEqual(rows, self.rows())
        self.assertNotIn("route_endpoints", self.rows())
        self.assertEqual(1, len(list((self.state5 / "backups").glob("*.sqlite3"))))

    def test_schema5_restore_into_separate_root_keeps_new_history_untouched(self):
        migrated = storage.migrate_state(self.state5)
        platform = Platform(self.state5)
        platform.submit("a", "worker", "post-migration task")
        before = self.database.read_bytes()
        restored = self.root / "restored-schema5"
        restored.mkdir()
        restored_db = restored / "state.sqlite3"
        restored_db.write_bytes(Path(migrated["backup_path"]).read_bytes())
        with closing(sqlite3.connect(restored_db)) as db:
            storage.verify_schema(db, version=5)
            self.assertEqual(1, db.execute("SELECT count(*) FROM tasks").fetchone()[0])
        self.assertEqual(before, self.database.read_bytes())
        self.assertEqual(2, len(platform.history("a")["tasks"]))


if __name__ == "__main__":
    unittest.main()
