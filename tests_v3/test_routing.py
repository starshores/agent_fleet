"""Independent route/admission tests using disposable synthetic state only.

No legacy bus module, production inbox, credential or model is used. The legacy
lane below is the new controlled-source API, not an adapted legacy fleet.
"""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from masteragent.errors import PlatformError
from masteragent.service import Platform


class RoutingTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="ma31-routing-synthetic-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.now = 1000.0
        self.platform = self.handle()
        self.install("a")
        self.platform.activate("a")
        self.actor = self.platform.bind("a", "worker")
        self.route = self.configure()

    def handle(self, readonly=False):
        return Platform(self.root / "state", readonly=readonly,
                        external_adapters={"model_text"}, clock=lambda: self.now)

    def install(self, installation):
        workspace = self.root / ("workspace-" + installation)
        workspace.mkdir()
        package = self.root / (installation + ".json")
        package.write_text(json.dumps({
            "schema_version": 1, "platform_api": 1,
            "package_id": installation, "version": "1.0.0",
            "roles": [{"role_id": role, "display_name": role,
                       "instructions": "Synthetic fixture only", "tools": ["echo", "think"]}
                      for role in ("worker", "other")],
            "knowledge": {}, "tools": {
                "echo": {"adapter": "echo", "description": "Synthetic echo"},
                "think": {"adapter": "model_text", "description": "Never started"}},
            "workflows": {},
        }), encoding="utf-8")
        self.platform.install(installation, package, workspace)

    def configure(self, **updates):
        values = {
            "route_id": "controlled", "source_namespace": "synthetic.source.v1",
            "owner_lane": "v3", "installation_id": "a", "role_id": "worker",
            "generation": 1, "destination_namespace": "synthetic.destination.v1",
            "destination_id": "receiver", "expected_epoch": 0,
        }
        values.update(updates)
        return self.platform.configure_route(**values)

    def claim_source(self, source_id="message", route_id="controlled", owner="connector"):
        return self.platform.claim_source(route_id, source_id, owner)

    def admit(self, source_id="message", text="Synthetic task", *, claim=None,
              route=None, owner="connector", platform=None, kind="task"):
        route = route or self.route
        claim = claim or self.claim_source(source_id, route["route_id"], owner)
        return (platform or self.platform).admit_source(
            route["route_id"], route["route_epoch"], source_id, text, owner,
            claim["claim_epoch"], claim["claim_token"], kind=kind)

    def deny(self, method, *args, **kwargs):
        with self.assertRaises(PlatformError):
            method(*args, **kwargs)

    def counts(self):
        with closing(sqlite3.connect(self.platform.db_path)) as db:
            return {table: db.execute("SELECT COUNT(*) FROM " + table).fetchone()[0]
                    for table in ("tasks", "source_admissions", "routing_events", "attempts")}

    def test_concurrent_duplicate_admission_creates_exactly_one_task(self):
        claim = self.claim_source()
        with ThreadPoolExecutor(max_workers=4) as pool:
            records = list(pool.map(lambda _: self.admit(claim=claim, platform=self.handle()), range(8)))
        self.assertEqual(1, len({r["admission_id"] for r in records}))
        self.assertEqual(1, len({r["task_id"] for r in records}))
        self.assertEqual(1, self.counts()["tasks"])
        self.assertEqual(1, len(self.platform.admissions()))
        self.assertEqual("v3", records[0]["owner_lane"])

    def test_source_replay_after_reopen_keeps_task_and_source_confirmation_separate(self):
        claim = self.claim_source()
        saved = self.admit(claim=claim)
        self.assertFalse(saved["source_confirmed"])
        replay = self.admit(claim=claim, platform=self.handle())
        self.assertEqual(saved, replay)
        confirmed = self.platform.confirm_source(
            saved["admission_id"], "connector", claim["claim_epoch"], claim["claim_token"])
        self.assertTrue(confirmed["source_confirmed"])
        self.assertEqual(confirmed, self.platform.confirm_source(
            saved["admission_id"], "connector", claim["claim_epoch"], claim["claim_token"]))
        self.assertEqual(1, self.counts()["tasks"])
        self.assertEqual([], self.platform.attempts("a"))

    def test_same_source_with_changed_text_or_kind_never_mutates_original(self):
        claim = self.claim_source()
        original = self.admit(claim=claim)
        before = self.counts()
        self.deny(self.admit, text="Changed task", claim=claim)
        for kind in ("fyi", "notice", "request", "urgent", "approval", ""):
            with self.subTest(kind=kind):
                self.deny(self.admit, kind=kind, claim=claim)
        self.assertEqual(original, self.platform.admission(original["admission_id"]))
        self.assertEqual(before, self.counts())

    def test_same_source_with_changed_target_conflicts_but_old_epoch_can_replay(self):
        claim = self.claim_source()
        original = self.admit(claim=claim)
        next_route = self.configure(expected_epoch=1, role_id="other", destination_id="other")
        self.deny(self.admit, claim=claim, route=next_route)
        self.assertEqual(original, self.admit(claim=claim))
        self.assertEqual(1, self.counts()["tasks"])

    def test_source_integer_string_and_namespace_are_distinct(self):
        integer = self.admit(1)
        string = self.admit("1")
        second = self.configure(route_id="second", source_namespace="synthetic.source.v2")
        namespaced = self.admit("1", route=second)
        self.assertEqual(3, len({r["admission_id"] for r in (integer, string, namespaced)}))
        self.assertEqual(3, len({r["task_id"] for r in (integer, string, namespaced)}))

    def test_invalid_source_identifiers_do_not_leave_claims_or_tasks(self):
        for source_id in (True, False, None, 1.5, [], {}, "", "line\nbreak", "a\x00b",
                          "中" * 67, "\ud800"):
            with self.subTest(source_id=repr(source_id)):
                self.deny(self.claim_source, source_id)
        self.assertEqual(0, self.counts()["tasks"])
        self.assertEqual([], self.platform.admissions())

    def test_invalid_route_scope_policy_or_identifiers_do_not_create_versions(self):
        for updates in (
            {"owner_lane": "both"}, {"role_id": "missing"}, {"generation": 99},
            {"source_namespace": ""}, {"source_namespace": "a\nb"},
            {"destination_id": "\x00"}, {"destination_namespace": "中" * 67},
            {"admission_open": 1}, {"lease_seconds": 14}, {"lease_seconds": 301},
            {"retry_seconds": 4}, {"retry_seconds": 61}, {"destination_limit": 0},
        ):
            with self.subTest(updates=updates):
                self.deny(self.configure, **{"route_id": "invalid", "source_namespace": "invalid.source", **updates})
        self.assertEqual([self.route], self.platform.routes())

    def test_route_epoch_compare_and_set_has_one_winner(self):
        def revise(index):
            try:
                return self.handle().set_route_controls("controlled", 1, admission_open=bool(index % 2))
            except PlatformError:
                return None
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(revise, range(8)))
        winners = [r for r in results if r is not None]
        self.assertEqual(1, len(winners))
        self.assertEqual(2, winners[0]["route_epoch"])
        self.assertEqual(1, len(self.platform.routes()))

    def test_new_source_cannot_use_old_epoch(self):
        revised = self.platform.set_route_controls("controlled", 1, delivery_open=False)
        claim = self.claim_source("new")
        self.assertEqual(revised["route_epoch"], claim["route_epoch"])
        self.deny(self.admit, "new", claim=claim)
        self.assertEqual(0, self.counts()["tasks"])
        self.assertEqual(revised["route_epoch"], self.admit("new", route=revised, claim=claim)["route_epoch"])

    def test_source_claim_lease_fences_old_owner_and_does_not_duplicate_task(self):
        old_claim = self.claim_source()
        self.deny(self.claim_source, owner="rival")
        self.now += 61
        fresh = self.claim_source(owner="rival")
        self.assertGreater(fresh["claim_epoch"], old_claim["claim_epoch"])
        self.deny(self.admit, claim=old_claim)
        saved = self.admit(owner="rival", claim=fresh)
        self.deny(self.platform.confirm_source, saved["admission_id"], "connector",
                  old_claim["claim_epoch"], old_claim["claim_token"])
        self.assertTrue(self.platform.confirm_source(saved["admission_id"], "rival",
                                                    fresh["claim_epoch"], fresh["claim_token"])["source_confirmed"])
        self.assertEqual(1, self.counts()["tasks"])

    def test_source_claim_cannot_be_reused_for_another_source_or_owner(self):
        claim = self.claim_source("first")
        self.deny(self.admit, "second", claim=claim)
        self.deny(self.admit, "first", claim=claim, owner="rival")
        self.deny(self.admit, "first", claim={**claim, "claim_token": "forged"})
        self.assertEqual(0, self.counts()["tasks"])

    def test_source_renewal_preserves_epoch_and_revoked_lease_cannot_renew(self):
        original = self.claim_source()
        self.now += 20
        renewed = self.platform.renew_source("controlled", "message", "connector",
                                              original["claim_epoch"], original["claim_token"])
        self.assertEqual(original["claim_epoch"], renewed["claim_epoch"])
        self.assertGreater(renewed["expires_at"], original["expires_at"])
        self.now = renewed["expires_at"] + 1
        replacement = self.claim_source(owner="replacement")
        self.deny(self.platform.renew_source, "controlled", "message", "connector",
                  original["claim_epoch"], original["claim_token"])
        self.assertGreater(replacement["claim_epoch"], original["claim_epoch"])

    def test_source_renewal_keeps_issued_lease_policy_after_route_update(self):
        original = self.claim_source()
        self.assertEqual(self.now + 60, original["expires_at"])
        revised = self.configure(expected_epoch=1, lease_seconds=300)
        self.assertEqual(300, revised["policy"]["lease_seconds"])
        self.now += 20
        renewed = self.platform.renew_source("controlled", "message", "connector",
                                              original["claim_epoch"], original["claim_token"])
        self.assertEqual(self.now + 60, renewed["expires_at"])
        self.assertEqual(original["claim_epoch"], renewed["claim_epoch"])
        self.now = renewed["expires_at"] + 1
        replacement = self.claim_source(owner="replacement")
        self.assertEqual(self.now + 300, replacement["expires_at"])
        self.assertGreater(replacement["claim_epoch"], original["claim_epoch"])

    def test_surrogate_claim_tokens_raise_public_errors_without_changing_authority(self):
        claim = self.claim_source()
        malformed = "invalid\ud800"
        before = self.counts()
        self.deny(self.admit, claim={**claim, "claim_token": malformed})
        self.deny(self.platform.renew_source, "controlled", "message", "connector",
                  claim["claim_epoch"], malformed)
        self.assertEqual(before, self.counts())
        saved = self.admit(claim=claim)
        self.deny(self.platform.confirm_source, saved["admission_id"], "connector",
                  claim["claim_epoch"], malformed)
        self.assertFalse(self.platform.admission(saved["admission_id"])["source_confirmed"])
        route = self.configure(expected_epoch=1, owner_lane="legacy")
        legacy = self.admit("legacy-message", route=route)
        owner = self.platform.claim_legacy(legacy["admission_id"], "legacy-worker")
        self.deny(self.platform.renew_legacy, legacy["admission_id"], "legacy-worker",
                  owner["claim_epoch"], malformed)
        self.deny(self.platform.complete_legacy, legacy["admission_id"], "legacy-worker",
                  owner["claim_epoch"], malformed, "Malformed token must not publish")
        self.assertEqual("completed", self.platform.complete_legacy(
            legacy["admission_id"], "legacy-worker", owner["claim_epoch"], owner["claim_token"], "Valid owner")["state"])

    def test_raw_utf8_text_is_preserved_with_exact_size_and_digest(self):
        text = "  中文 👩🏽‍💻 e\u0301\r\nline\n尾段哨兵  "
        record = self.admit(text=text)
        self.assertEqual(text, record["text"])
        self.assertEqual(len(text.encode("utf-8")), record["text_bytes"])
        self.assertEqual(hashlib.sha256(text.encode("utf-8")).hexdigest(), record["text_sha256"])
        self.assertEqual(text, self.platform.task_for(self.actor, record["task_id"])["text"])

    def test_initial_text_byte_limit_is_exact_and_not_character_count(self):
        exact = "中" * 21333 + "a"
        self.assertEqual(64000, len(exact.encode("utf-8")))
        record = self.admit("exact", text=exact)
        self.assertEqual(64000, record["text_bytes"])
        self.deny(self.admit, "over", text=exact + "a")
        self.deny(self.admit, "surrogate", text="invalid\ud800")
        self.assertEqual(1, self.counts()["tasks"])

    def test_failure_after_task_and_admission_insert_rolls_back_both(self):
        claim = self.claim_source()
        before = self.counts()
        observed = []
        def fail_after_insert(db, route_id, admission_id, kind, detail):
            self.assertEqual("source_admitted", kind)
            self.assertEqual(1, db.execute("SELECT COUNT(*) FROM tasks").fetchone()[0])
            self.assertEqual(1, db.execute("SELECT COUNT(*) FROM source_admissions").fetchone()[0])
            observed.append(admission_id)
            raise RuntimeError("Synthetic failure after task and mapping insert")
        with patch.object(self.platform, "_routing_event", side_effect=fail_after_insert):
            with self.assertRaisesRegex(RuntimeError, "Synthetic failure"):
                self.admit(claim=claim)
        self.assertEqual(1, len(observed))
        self.assertEqual(before, self.counts())
        self.assertIsNotNone(self.admit(claim=claim)["task_id"])

    def test_source_confirmation_event_failure_rolls_back_task_mapping_and_acceptance_event(self):
        claim = self.claim_source()
        before = self.counts()
        original = self.platform._routing_event
        observed = []
        def fail_after_event(db, route_id, admission_id, kind, detail):
            if kind == "source_confirmation_pending":
                self.assertEqual(1, db.execute("SELECT COUNT(*) FROM tasks").fetchone()[0])
                self.assertEqual(1, db.execute("SELECT COUNT(*) FROM source_admissions").fetchone()[0])
                self.assertEqual(before["routing_events"] + 1,
                                 db.execute("SELECT COUNT(*) FROM routing_events").fetchone()[0])
                observed.append(admission_id)
                raise RuntimeError("Synthetic failure after acceptance event")
            return original(db, route_id, admission_id, kind, detail)
        with patch.object(self.platform, "_routing_event", side_effect=fail_after_event):
            with self.assertRaisesRegex(RuntimeError, "Synthetic failure"):
                self.admit(claim=claim)
        self.assertEqual(1, len(observed))
        self.assertEqual(before, self.counts())
        self.assertIsNotNone(self.admit(claim=claim)["task_id"])

    def test_closed_admission_keeps_replays_and_existing_execution_available(self):
        claim = self.claim_source()
        saved = self.admit(claim=claim)
        closed = self.platform.set_route_controls("controlled", 1, admission_open=False)
        self.assertFalse(closed["admission_open"])
        self.assertTrue(closed["claim_open"])
        self.deny(self.claim_source, "new")
        self.assertEqual(saved, self.admit(claim=claim))
        result = self.platform.run("a", saved["task_id"], "echo")
        self.assertEqual("ok", result["status"])
        self.assertEqual(1, len(self.platform.attempts("a")))

    def test_freeze_claim_blocks_all_direct_execution_entries_but_not_plain_tasks(self):
        saved = self.admit()
        self.platform.set_route_controls("controlled", 1, claim_open=False)
        for action, args in (
            (self.platform.run, ("a", saved["task_id"], "echo")),
            (self.platform.run_as, (self.actor, saved["task_id"], "echo")),
            (self.platform.claim, (self.actor, saved["task_id"], "echo")),
        ):
            with self.subTest(action=action.__name__):
                self.deny(action, *args)
        self.assertEqual([], self.platform.attempts("a"))
        plain = self.platform.submit("a", "worker", "Direct synthetic task")
        self.assertEqual("ok", self.platform.run("a", plain["task_id"], "echo")["status"])
        self.assertEqual("queued", self.platform.task_for(self.actor, saved["task_id"])["state"])

    def test_frozen_claim_does_not_cancel_inflight_attempt_or_republish_it(self):
        saved = self.admit()
        attempt = self.platform.claim(self.actor, saved["task_id"], "echo")
        self.platform.set_route_controls("controlled", 1, claim_open=False, admission_open=False)
        result = self.platform.complete(self.actor, attempt["attempt_id"], attempt["lease_token"], "Finished before drain")
        self.assertEqual("ok", result["status"])
        self.assertEqual(result, self.platform.complete(
            self.actor, attempt["attempt_id"], attempt["lease_token"], "Finished before drain"))
        self.assertEqual(1, len(self.platform.attempts("a")))

    def test_member_skips_frozen_routed_task_and_claims_plain_task(self):
        frozen = self.admit()
        self.platform.set_route_controls("controlled", 1, claim_open=False)
        plain = self.platform.submit("a", "worker", "Plain task behind frozen route")
        member = self.platform.start_member(self.actor, "a" * 64, "think")
        claimed = self.platform.claim_member_task(self.actor, member["member_id"], member["member_token"])
        self.assertEqual(plain["task_id"], claimed["task_id"])
        self.assertEqual("queued", self.platform.task_for(self.actor, frozen["task_id"])["state"])
        self.platform.complete(self.actor, claimed["attempt_id"], claimed["lease_token"], "Synthetic completion")
        self.assertIsNone(self.platform.claim_member_task(self.actor, member["member_id"], member["member_token"]))

    def test_claim_reopen_preserves_original_admission_and_fixed_destination(self):
        saved = self.admit()
        closed = self.platform.set_route_controls("controlled", 1, claim_open=False)
        updated = self.configure(expected_epoch=closed["route_epoch"], destination_id="next-destination")
        self.assertGreater(updated["route_epoch"], saved["route_epoch"])
        claimed = self.platform.claim(self.actor, saved["task_id"], "echo")
        self.assertEqual(saved["task_id"], claimed["task_id"])
        self.assertEqual("receiver", self.platform.admission(saved["admission_id"])["destination_id"])

    def test_changed_target_for_new_messages_does_not_reassign_prior_task(self):
        saved = self.admit()
        revised = self.configure(expected_epoch=1, role_id="other", destination_id="other")
        next_message = self.admit("next", route=revised)
        self.assertEqual("other", next_message["role_id"])
        old_claim = self.platform.claim(self.actor, saved["task_id"], "echo")
        self.assertEqual("worker", old_claim["role_id"])
        self.assertEqual(saved["task_id"], old_claim["task_id"])
        self.assertEqual("receiver", self.platform.admission(saved["admission_id"])["destination_id"])

    def test_lane_change_only_affects_new_admissions(self):
        original_claim = self.claim_source()
        original = self.admit(claim=original_claim)
        legacy_route = self.configure(expected_epoch=1, owner_lane="legacy")
        self.assertEqual(original, self.admit(claim=original_claim))
        new = self.admit("next", route=legacy_route)
        self.assertEqual("legacy", new["owner_lane"])
        self.assertIsNone(new["task_id"])
        self.assertEqual("ok", self.platform.run("a", original["task_id"], "echo")["status"])
        self.deny(self.platform.claim_legacy, original["admission_id"], "legacy-worker")
        self.assertEqual(1, self.counts()["tasks"])

    def test_legacy_lane_has_no_task_and_completion_is_exactly_idempotent(self):
        route = self.configure(expected_epoch=1, owner_lane="legacy")
        saved = self.admit(route=route)
        self.assertIsNone(saved["task_id"])
        self.assertEqual(0, self.counts()["tasks"])
        self.deny(self.platform.claim, self.actor, saved["admission_id"], "echo")
        claim = self.platform.claim_legacy(saved["admission_id"], "legacy-worker")
        self.deny(self.platform.claim_legacy, saved["admission_id"], "rival")
        result = self.platform.complete_legacy(saved["admission_id"], "legacy-worker",
                                               claim["claim_epoch"], claim["claim_token"], "Synthetic legacy result")
        self.assertEqual("completed", result["state"])
        self.now += 61
        self.assertEqual(result, self.platform.complete_legacy(
            saved["admission_id"], "legacy-worker", claim["claim_epoch"], claim["claim_token"], "Synthetic legacy result"))
        self.deny(self.platform.complete_legacy, saved["admission_id"], "legacy-worker",
                  claim["claim_epoch"], claim["claim_token"], "Different result")
        self.assertEqual(0, self.counts()["attempts"])

    def test_legacy_admission_stays_legacy_after_new_epoch_selects_v3(self):
        old_route = self.configure(expected_epoch=1, owner_lane="legacy")
        source_claim = self.claim_source()
        old = self.admit(route=old_route, claim=source_claim)
        next_route = self.configure(expected_epoch=old_route["route_epoch"], owner_lane="v3")
        new = self.admit("next", route=next_route)
        self.assertIsNotNone(new["task_id"])
        self.assertEqual(old, self.admit(route=old_route, claim=source_claim))
        self.deny(self.admit, route=next_route, claim=source_claim)
        issued = self.platform.claim_legacy(old["admission_id"], "legacy-worker")
        self.assertEqual("running", issued["state"])
        self.assertIsNone(self.platform.admission(old["admission_id"])["task_id"])
        self.assertEqual(1, self.counts()["tasks"])

    def test_legacy_claim_freeze_allows_live_owner_to_finish(self):
        route = self.configure(expected_epoch=1, owner_lane="legacy")
        first = self.admit("first", route=route)
        second = self.admit("second", route=route)
        issued = self.platform.claim_legacy(first["admission_id"], "legacy-worker")
        self.platform.set_route_controls("controlled", route["route_epoch"], claim_open=False)
        self.deny(self.platform.claim_legacy, second["admission_id"], "next-worker")
        result = self.platform.complete_legacy(first["admission_id"], "legacy-worker",
                                               issued["claim_epoch"], issued["claim_token"], "Drained")
        self.assertEqual("completed", result["state"])
        self.assertEqual(0, self.counts()["tasks"])

    def test_expired_legacy_execution_requires_explicit_reconciliation_and_fences_old_token(self):
        route = self.configure(expected_epoch=1, owner_lane="legacy")
        saved = self.admit(route=route)
        claim = self.platform.claim_legacy(saved["admission_id"], "legacy-worker")
        self.now += 61
        self.deny(self.platform.claim_legacy, saved["admission_id"], "next-worker")
        self.deny(self.platform.complete_legacy, saved["admission_id"], "legacy-worker",
                  claim["claim_epoch"], claim["claim_token"], "Late completion")
        self.deny(self.platform.reconcile_legacy, saved["admission_id"], "Synthetic uncertainty retained")
        self.deny(self.platform.claim_legacy, saved["admission_id"], "next-worker")
        self.platform.reconcile_legacy(saved["admission_id"], "Synthetic effect proven absent", safe_to_retry=True)
        fresh = self.platform.claim_legacy(saved["admission_id"], "next-worker")
        self.assertGreater(fresh["claim_epoch"], claim["claim_epoch"])
        self.deny(self.platform.complete_legacy, saved["admission_id"], "legacy-worker",
                  claim["claim_epoch"], claim["claim_token"], "Late completion")
        self.assertEqual("completed", self.platform.complete_legacy(
            saved["admission_id"], "next-worker", fresh["claim_epoch"], fresh["claim_token"], "Reconciled result")["state"])

    def test_public_queries_are_readonly_and_exclude_claim_secrets(self):
        claim = self.claim_source()
        saved = self.admit(claim=claim)
        before = self.platform.db_path.read_bytes()
        reader = self.handle(readonly=True)
        views = [reader.routes(), reader.admissions(), reader.admissions("controlled"), reader.admission(saved["admission_id"])]
        self.assertEqual(before, self.platform.db_path.read_bytes())
        encoded = json.dumps(views)
        for secret in (claim["claim_token"], self.actor.token, "lease_hash", "claim_token", "owner_hash"):
            self.assertNotIn(secret, encoded)
        self.deny(reader.claim_source, "controlled", "new", "connector")
        self.assertEqual(before, self.platform.db_path.read_bytes())

    def test_business_switch_prevents_cross_installation_claim_and_old_epoch_new_admission(self):
        original_claim = self.claim_source()
        saved = self.admit(claim=original_claim)
        self.install("b")
        self.platform.deactivate("a")
        self.platform.activate("b")
        actor_b = self.platform.bind("b", "worker")
        self.deny(self.platform.claim, actor_b, saved["task_id"], "echo")
        second = self.configure(route_id="business-b", source_namespace="synthetic.b.v1", installation_id="b")
        task_b = self.admit("message", route=second)
        self.assertNotEqual(saved["task_id"], task_b["task_id"])
        self.platform.deactivate("b")
        self.platform.activate("a")
        actor_a2 = self.platform.bind("a", "worker")
        self.deny(self.platform.claim, actor_a2, saved["task_id"], "echo")
        self.platform.resume_task("a", saved["task_id"], "Explicit synthetic continuation")
        refreshed = self.configure(expected_epoch=1, generation=2)
        issued = self.platform.claim(actor_a2, saved["task_id"], "echo")
        self.assertEqual(2, issued["generation"])
        self.assertEqual(1, self.platform.admission(saved["admission_id"])["generation"])
        self.assertEqual(refreshed["route_epoch"], 2)


if __name__ == "__main__":
    unittest.main()
