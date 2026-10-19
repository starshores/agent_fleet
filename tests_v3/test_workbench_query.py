"""Read-only workbench contracts against disposable synthetic installations."""
import base64
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from masteragent.errors import PlatformError
from masteragent.service import Platform
from masteragent.workbench_query import WorkbenchQueries


class WorkbenchQueryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="ma31-query-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.now = 1000.0
        timer = patch("masteragent.workbench_query.time.time", side_effect=lambda: self.now)
        timer.start()
        self.addCleanup(timer.stop)
        self.state_root = self.root / "state"
        self.platform = Platform(self.state_root, clock=lambda: self.now, external_adapters={"model_text"})
        self.packages = {}
        self.workspaces = {}
        for name in ("a", "b"):
            workspace = self.root / (name + "-workspace")
            workspace.mkdir()
            package = self.root / (name + ".json")
            package.write_text(json.dumps({
                "schema_version": 1, "platform_api": 1, "package_id": "business-" + name,
                "version": "1.0.0", "roles": [
                    {"role_id": role, "display_name": name + " " + role,
                     "instructions": "PRIVATE_TO_" + name, "tools": ["local", "think"]}
                    for role in ("reviewer", "helper")],
                "knowledge": {"policy": "KNOWLEDGE_" + name},
                "tools": {"local": {"adapter": "echo", "description": "synthetic echo"},
                          "think": {"adapter": "model_text", "description": "synthetic external"}},
                "workflows": {"review": {"roles": ["reviewer"], "description": "FLOW_" + name}},
            }), encoding="utf-8")
            self.packages[name], self.workspaces[name] = package, workspace
            self.platform.install(name, package, workspace)
        self.platform.activate("a")
        self.actor = self.platform.bind("a", "reviewer")
        self.queries = WorkbenchQueries(self.state_root)

    def assert_code(self, code, method, *args, **kwargs):
        with self.assertRaises(PlatformError) as caught:
            method(*args, **kwargs)
        self.assertEqual(code, caught.exception.code)

    def snapshot(self, path=None):
        return {str(p.relative_to(path or self.root)): (p.stat().st_mtime_ns, hashlib.sha256(p.read_bytes()).hexdigest())
                for p in (path or self.root).rglob("*") if p.is_file()}

    def submit(self, name="a", role="reviewer", text="synthetic task"):
        return self.platform.submit(name, role, text)

    def runtime_attempt(self, supports_input=True, lease_seconds=60):
        task = self.submit()
        attempt = self.platform.claim(self.actor, task["task_id"], "think", lease_seconds)
        self.platform.record_runtime_event(self.actor, attempt["attempt_id"], attempt["lease_token"],
            "runtime_start_requested", {"runtime": "codex-app-server" if supports_input else "codex-exec",
                "version": "synthetic-version", "model_requested": "requested-model",
                "model_observed": None, "supports_runtime_inputs": supports_input})
        return task, attempt

    def switch(self, name):
        current = self.platform.status()["active"]
        if current:
            self.platform.deactivate(current)
        self.platform.activate(name)

    def outputs(self, task):
        return [self.queries.deployment(), self.queries.overview("a"), self.queries.tasks("a"),
                self.queries.task("a", task["task_id"]), self.queries.team("a"),
                self.queries.resources("a"), self.queries.connections("a")]

    def test_empty_deployment_creates_nothing_and_ignores_legacy_environment(self):
        missing = self.root / "never-created"
        bait = self.root / "legacy-bait"
        bait.mkdir()
        (bait / "marker.txt").write_text("DO_NOT_TOUCH", encoding="utf-8")
        before = self.snapshot()
        with patch.dict(os.environ, {"AGENT_BUS_ROOT": str(bait), "AGENT_FLEET_HOME": str(bait),
                                     "AGENTBUS_HOME": str(bait)}):
            result = WorkbenchQueries(missing).deployment()
        self.assertFalse(result["data"]["initialized"])
        self.assertIsNone(result["data"]["schema_version"])
        self.assertEqual(result["data"]["installations"], [])
        self.assertIsNone(result["source"]["installation_id"])
        self.assertFalse(missing.exists())
        self.assertEqual(self.snapshot(), before)

    def test_envelope_and_unverified_connection_facts(self):
        task = self.submit()
        for result in self.outputs(task):
            self.assertEqual(result["api_version"], "3.1-dev1")
            self.assertEqual(result["source"]["kind"], "masteragent")
            self.assertEqual(result["source"]["state_root"], str(self.state_root))
            self.assertIsNone(result["revision"])
            self.assertEqual(result["observed_at"], "1970-01-01T00:16:40+00:00")
        deployment = self.queries.deployment()["data"]
        self.assertEqual(deployment["active_installation_id"], "a")
        self.assertEqual(deployment["schema_version"], 6)
        self.assertTrue(deployment["initialized"])
        connections = self.queries.connections("a")["data"]["items"]
        codex, claude = connections
        self.assertEqual(codex["runtime"], "codex")
        self.assertTrue(codex["supported"])
        self.assertEqual(codex["connectivity"], "not_verified")
        self.assertFalse(claude["supported"])
        self.assertEqual(claude["status"], "unavailable")
        for connection in connections:
            self.assertIsNone(connection["model_requested"])
            self.assertIsNone(connection["model_observed"])
            self.assertIsNone(connection["usage"])
            self.assertEqual(connection["authentication"], "not_verified")

    def test_business_scope_and_validated_snapshot_survive_source_removal(self):
        task_a = self.submit(text="A_BODY")
        self.switch("b")
        task_b = self.submit("b", text="B_BODY")
        for name, expected in (("a", task_a), ("b", task_b)):
            listing = self.queries.tasks(name)
            self.assertEqual(listing["source"]["installation_id"], name)
            self.assertEqual([row["task_id"] for row in listing["data"]["items"]], [expected["task_id"]])
            self.assertNotIn("text", listing["data"]["items"][0])
            self.assertNotIn("result", listing["data"]["items"][0])
            team = self.queries.team(name)["data"]
            self.assertEqual(team["roles"][0]["instructions"], "PRIVATE_TO_" + name)
            self.assertEqual(team["roles"][0]["workspace"], str(self.workspaces[name]))
            self.assertEqual(team["roles"][0]["access"], "none")
            self.packages[name].unlink()
            self.workspaces[name].rename(self.root / (name + "-moved"))
            resource = self.queries.resources(name)["data"]
            self.assertEqual(resource["knowledge"]["policy"], "KNOWLEDGE_" + name)
            self.assertFalse(resource["artifact_catalog_available"])
        self.assert_code("task_not_found", self.queries.task, "a", task_b["task_id"])
        self.assert_code("task_not_found", self.queries.task, "b", task_a["task_id"])

    def test_pagination_is_bounded_stable_and_filter_scoped(self):
        expected = [self.submit()["task_id"] for _ in range(7)]
        self.submit(role="helper")
        first = self.queries.tasks("a", limit=2, role_id="reviewer")["data"]
        self.assertEqual([row["task_id"] for row in first["items"]], expected[-1:-3:-1])
        newer = self.submit()["task_id"]
        found = [row["task_id"] for row in first["items"]]
        cursor = first["next_cursor"]
        while cursor is not None:
            page = self.queries.tasks("a", limit=2, role_id="reviewer", cursor=cursor)["data"]
            self.assertLessEqual(len(page["items"]), 2)
            found.extend(row["task_id"] for row in page["items"])
            cursor = page["next_cursor"]
        self.assertEqual(found, list(reversed(expected)))
        self.assertNotIn(newer, found)
        for kwargs in ({"role_id": "helper"}, {"role_id": "reviewer", "state": "queued"},
                       {"role_id": "reviewer", "generation": 0}):
            self.assert_code("invalid_cursor", self.queries.tasks, "a", cursor=first["next_cursor"], **kwargs)
        self.assert_code("invalid_cursor", self.queries.tasks, "b", cursor=first["next_cursor"], role_id="reviewer")
        foreign = WorkbenchQueries(self.state_root)
        foreign._root_key = "other-state-root"
        self.assert_code("invalid_cursor", foreign.tasks, "a", cursor=first["next_cursor"], role_id="reviewer")
        encoded = first["next_cursor"]
        payload = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
        payload["scope"]["generation"] = True
        altered = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
        self.assert_code("invalid_cursor", self.queries.tasks, "a", cursor=altered, role_id="reviewer")

    def test_query_inputs_are_strictly_validated(self):
        for limit in (True, False, 0, -1, 101, "2", 2.0, None):
            self.assert_code("invalid_limit", self.queries.tasks, "a", limit=limit)
        for cursor in ("", "a", "====", "not a cursor", 1, {}, "x" * 2049):
            self.assert_code("invalid_cursor", self.queries.tasks, "a", cursor=cursor)
        for state in ("unknown", True, [], 0):
            self.assert_code("invalid_state", self.queries.tasks, "a", state=state)
        for generation in (True, False, "1", -1, 2, 1.0, [], 2**100):
            for method in (self.queries.overview, self.queries.tasks, self.queries.team,
                           self.queries.resources, self.queries.connections):
                self.assert_code("invalid_generation", method, "a", generation=generation)
        for name in ("", "../a", "a\n", "a" * 65, True, None):
            self.assert_code("invalid_id", self.queries.overview, name)
        self.assert_code("role_not_found", self.queries.tasks, "a", role_id="absent")
        self.assert_code("invalid_id", self.queries.tasks, "a", role_id="../reviewer")

    def test_generation_selection_and_continuation_explain_incomplete_history(self):
        completed = self.submit(text="old completed")
        self.platform.run_as(self.actor, completed["task_id"], "local")
        continued = self.submit(text="old queued")
        decision = self.platform.ask_decision(self.actor, continued["task_id"], "Old decision", "old-question")
        self.switch("b")
        self.switch("a")
        self.platform.resume_task("a", continued["task_id"], "explicit synthetic continuation")
        current = self.queries.tasks("a")
        self.assertEqual(current["source"]["generation"], 2)
        self.assertEqual([row["task_id"] for row in current["data"]["items"]], [continued["task_id"]])
        detail = self.queries.task("a", continued["task_id"])["data"]
        self.assertEqual(detail["task"]["generation"], 1)
        self.assertEqual(detail["task"]["dispatch_generation"], 2)
        self.assertTrue(detail["history_available"])
        self.assertIn("not a historical point-in-time snapshot", detail["scope_note"])
        self.assertNotIn(decision["decision_id"], [row["decision_id"] for row in detail["decisions"]])
        self.assertTrue(all(row["generation"] == 2 for row in detail["decisions"]))
        self.assert_code("task_not_found", self.queries.task, "a", continued["task_id"], generation=1)
        self.assert_code("task_not_found", self.queries.task, "a", completed["task_id"])
        old = self.queries.task("a", completed["task_id"], generation=1)
        self.assertEqual(old["data"]["result"]["output"], "old completed")
        self.assertEqual(old["source"]["generation"], 1)
        self.assertEqual(self.queries.overview("a", generation=1)["data"]["counts"]["tasks_total"], 1)

    def test_queued_continuation_chain_is_reachable_from_every_ui_generation(self):
        task = self.submit(text="queued across activations")
        for number in (2, 3):
            self.switch("b")
            self.switch("a")
            self.platform.resume_task("a", task["task_id"], "continue " + str(number))
        before = self.snapshot()
        for selected in (0, 1, 2, 3):
            first = self.queries.task_history("a", task["task_id"], generation=selected, limit=1)
            self.assertEqual(first["source"]["generation"], selected)
            data = first["data"]
            self.assertEqual(data["scope"], {"kind": "task_history", "task_id": task["task_id"]})
            self.assertTrue(data["readonly"])
            self.assertEqual(data["task"]["dispatch_generation"], 3)
            items = list(data["items"])
            while data["next_cursor"]:
                data = self.queries.task_history("a", task["task_id"], generation=selected,
                    limit=1, cursor=data["next_cursor"])["data"]
                items.extend(data["items"])
            self.assertEqual([item["generation"] for item in items], [3, 2, 1])
            self.assertTrue(items[0]["is_current_dispatch"])
            self.assertTrue(items[0]["is_installation_current"])
            self.assertTrue(items[-1]["is_original"])
        detail = self.queries.task("a", task["task_id"])["data"]
        self.assertTrue(detail["history_available"])
        self.assertEqual(detail["attempts"], [])
        # The old endpoint still selects dispatch_generation; history is additive.
        self.assert_code("task_not_found", self.queries.task, "a", task["task_id"], generation=1)
        edges = self.queries.task_history("a", task["task_id"], section="continuations", record_generation=2, limit=1)["data"]
        self.assertEqual((edges["items"][0]["from_generation"], edges["items"][0]["to_generation"]), (2, 3))
        tail = self.queries.task_history("a", task["task_id"], section="continuations", record_generation=2,
                                         cursor=edges["next_cursor"], limit=1)["data"]
        self.assertEqual((tail["items"][0]["from_generation"], tail["items"][0]["to_generation"]), (1, 2))
        self.assertIsNone(tail["next_cursor"])
        self.assertEqual(self.snapshot(), before)

    def test_history_recovers_old_attempt_decision_inputs_and_new_result_without_operations(self):
        task, attempt = self.runtime_attempt()
        self.platform.submit_runtime_input("a", attempt["attempt_id"], "retained supplement", "old-input")
        self.now += 61
        self.platform.expire_attempts("a")
        self.platform.reconcile_task("a", task["task_id"], "retry", "synthetic safe retry", safe_to_retry=True)
        decision = self.platform.ask_decision(self.actor, task["task_id"], "Old decision", "old-decision")
        self.switch("b")
        self.switch("a")
        self.platform.resume_task("a", task["task_id"], "new activation")
        before = self.snapshot()
        values = {section: self.queries.task_history("a", task["task_id"], section=section,
            record_generation=1)["data"]["items"] for section in
            ("attempts", "decisions", "inputs", "events", "notifications", "results")}
        self.assertEqual(values["attempts"][0]["attempt_id"], attempt["attempt_id"])
        self.assertNotIn("operations", values["attempts"][0])
        self.assertEqual(values["decisions"][0]["decision_id"], decision["decision_id"])
        self.assertEqual(values["decisions"][0]["state"], "superseded")
        self.assertEqual(values["inputs"][0]["text"], "retained supplement")
        self.assertTrue(any(event["kind"] == "reconciled" for event in values["events"]))
        self.assertEqual(values["results"], [])
        for secret in (self.actor.token, attempt["lease_token"], "owner_hash", "lease_hash", "payload"):
            self.assertNotIn(secret, json.dumps(values))
        self.assertEqual(self.snapshot(), before)
        # A completed task retained in an old activation still exposes its result.
        actor = self.platform.bind("a", "reviewer")
        completed = self.submit(text="old completed result")
        self.platform.run_as(actor, completed["task_id"], "local")
        self.switch("b")
        self.switch("a")
        result = self.queries.task_history("a", completed["task_id"], section="results", record_generation=2)["data"]
        self.assertEqual(result["items"][0]["output"], "old completed result")
        self.assertIsNone(result["next_cursor"])

    def test_history_cursors_bind_every_scope_and_preserve_first_page_ceiling(self):
        task = self.submit()
        for number in (2, 3):
            self.switch("b")
            self.switch("a")
            self.platform.resume_task("a", task["task_id"], "continue " + str(number))
        first = self.queries.task_history("a", task["task_id"], generation=3, limit=1)["data"]
        cursor = first["next_cursor"]
        other = self.submit()
        for kwargs in ({"generation": 2}, {"section": "attempts", "record_generation": 1},
                       {"section": "continuations", "record_generation": 2}):
            self.assert_code("invalid_cursor", self.queries.task_history, "a", task["task_id"], cursor=cursor, **kwargs)
        self.assert_code("invalid_cursor", self.queries.task_history, "a", other["task_id"], cursor=cursor)
        foreign = WorkbenchQueries(self.state_root)
        foreign._root_key = "another-root"
        self.assert_code("invalid_cursor", foreign.task_history, "a", task["task_id"], cursor=cursor)
        self.assert_code("task_not_found", self.queries.task_history, "b", task["task_id"], cursor=cursor)
        edge_cursor = self.queries.task_history("a", task["task_id"], section="continuations", record_generation=2, limit=1)["data"]["next_cursor"]
        self.assert_code("invalid_cursor", self.queries.task_history, "a", task["task_id"],
                         section="continuations", record_generation=1, cursor=edge_cursor)
        self.switch("b")
        self.switch("a")
        self.platform.resume_task("a", task["task_id"], "newer than first page")
        tail = self.queries.task_history("a", task["task_id"], generation=3, cursor=cursor)["data"]
        self.assertEqual([item["generation"] for item in tail["items"]], [2, 1])
        self.assertEqual(tail["task"]["dispatch_generation"], 4)  # not a time snapshot

    def test_history_rejects_unknown_generation_bad_filters_and_missing_state_without_writes(self):
        task = self.submit()
        for limit in (True, 0, 101, "2"):
            self.assert_code("invalid_limit", self.queries.task_history, "a", task["task_id"], limit=limit)
        for section in ("unknown", None, [], True):
            self.assert_code("invalid_section", self.queries.task_history, "a", task["task_id"], section=section)
        self.assert_code("invalid_query", self.queries.task_history, "a", task["task_id"], record_generation=1)
        for generation in (None, True, -1, "1"):
            self.assert_code("invalid_generation", self.queries.task_history, "a", task["task_id"],
                             section="attempts", record_generation=generation)
        for generation in (0, 2, 2**100):
            self.assert_code("history_generation_not_found", self.queries.task_history, "a", task["task_id"],
                             section="attempts", record_generation=generation)
        missing = self.root / "missing-history"
        before = self.snapshot()
        self.assert_code("not_initialized", WorkbenchQueries(missing).task_history, "a", task["task_id"])
        self.assertFalse(missing.exists())
        self.assertEqual(self.snapshot(), before)

    def test_expired_decision_and_member_are_projected_without_writes(self):
        task = self.submit()
        decision = self.platform.ask_decision(self.actor, task["task_id"], "Choose", "question", expires_at=self.now + 10)
        member = self.platform.start_member(self.actor, "b" * 64, "think", 10)
        live = self.queries.team("a")["data"]["members"][0]
        self.assertTrue(live["lease_valid"])
        self.assertTrue(live["accepting_tasks"])
        self.assertEqual(live["model_connectivity"], "not_verified")
        self.now += 11
        before = self.snapshot()
        outputs = self.outputs(task)
        self.assertEqual(self.snapshot(), before)
        detail, team = outputs[3]["data"], outputs[4]["data"]
        self.assertEqual(detail["decisions"][0]["state"], "expired")
        self.assertEqual(detail["decisions"][0]["stored_state"], "pending")
        self.assertEqual(outputs[1]["data"]["counts"]["pending_decisions"], 0)
        self.assertEqual(outputs[1]["data"]["pending_decisions"], [])
        self.assertEqual(team["members"][0]["state"], "lost")
        self.assertEqual(team["members"][0]["stored_state"], "ready")
        self.assertFalse(team["members"][0]["online"])
        self.assertFalse(team["members"][0]["accepting_tasks"])
        with self.platform._db() as db:
            self.assertEqual(db.execute("SELECT state FROM decisions WHERE decision_id=?", (decision["decision_id"],)).fetchone()[0], "pending")
            self.assertEqual(db.execute("SELECT state FROM member_sessions WHERE member_id=?", (member["member_id"],)).fetchone()[0], "ready")
        serialized = json.dumps(outputs)
        for secret in (self.actor.token, member["member_token"], "owner_hash", "lease_hash", "deployment_digest"):
            self.assertNotIn(secret, serialized)

    def test_task_detail_public_events_and_inputs_do_not_expose_private_metadata(self):
        task = self.submit(text="public task body")
        member = self.platform.start_member(self.actor, "b" * 64, "think", 60)
        attempt = self.platform.claim_member_task(self.actor, member["member_id"], member["member_token"], 60)
        self.platform.record_runtime_event(self.actor, attempt["attempt_id"], attempt["lease_token"],
                                           "runtime_start_requested", {"supports_runtime_inputs": True})
        self.platform.submit_runtime_input("a", attempt["attempt_id"], "public supplement", "input-1")
        with self.platform._db(write=True) as db:
            for event_id, kind in (("public-event", "runtime_progress"), ("private-event", "raw_reasoning")):
                db.execute("INSERT INTO attempt_events VALUES (?,?,?,?,?,?)", (event_id, attempt["attempt_id"], "a", kind,
                           json.dumps({"phase": "working", "state": {"private": "HIDDEN_NESTED"},
                                       "raw": "HIDDEN_RAW", "private_reasoning": "HIDDEN_REASONING",
                                       "token": self.actor.token}), "synthetic-time"))
            hashes = [row[0] for row in db.execute("SELECT token_hash FROM actor_tokens")]
        before = self.snapshot()
        outputs = self.outputs(task)
        self.assertEqual(self.snapshot(), before)
        detail = outputs[3]["data"]
        self.assertEqual(detail["task"]["text"], "public task body")
        self.assertEqual(detail["inputs"][0]["text"], "public supplement")
        self.assertEqual(next(row for row in detail["events"] if row["event_id"] == "public-event")["detail"], {"phase": "working"})
        self.assertNotIn("private-event", [row["event_id"] for row in detail["events"]])
        serialized = json.dumps(outputs)
        for secret in (*hashes, self.actor.token, member["member_token"], attempt["lease_token"],
                       "owner_hash", "lease_hash", "HIDDEN_RAW", "HIDDEN_REASONING", "HIDDEN_NESTED"):
            self.assertNotIn(secret, serialized)
        team = outputs[4]["data"]["members"][0]
        self.assertTrue(team["owned_attempt_active"])
        self.assertFalse(team["accepting_tasks"])
        self.assertEqual(team["active_attempt_id"], attempt["attempt_id"])

    def test_detail_and_team_collections_are_bounded_with_explicit_truncation(self):
        task = self.submit()
        stamp = "synthetic-time"
        with self.platform._db(write=True) as db:
            for number in range(101):
                aid = "attempt-" + str(number)
                db.execute("INSERT INTO attempts VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                           (aid, task["task_id"], "a", "reviewer", 1, number + 1, "think",
                            "owner-secret", "lease-secret", 1010, "failed", stamp, stamp))
                db.execute("INSERT INTO attempt_events VALUES (?,?,?,?,?,?)",
                           ("event-" + str(number), aid, "a", "failed", '{"status":"error"}', stamp))
                db.execute("INSERT INTO attempt_inputs VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                           ("input-" + str(number), "a", "reviewer", 1, aid, 1, "input-request-" + str(number),
                            "bounded input", "held", stamp, stamp))
                db.execute("INSERT INTO decisions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                           ("decision-" + str(number), "a", task["task_id"], 1, "request-" + str(number),
                            "old decision", "rejected", 1, None, None, "reject", stamp, stamp))
                db.execute("INSERT INTO member_sessions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                           ("member-" + str(number), "a", "reviewer", 1, "owner-secret", "lease-secret",
                            "b" * 64, "think", "stopped", 1010, stamp, stamp, "synthetic stop"))
                db.execute("INSERT INTO outbox VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                           ("notice-" + str(number), "a", 1, task["task_id"], "reviewer", "task_failed",
                            '{"raw":"SECRET_NOTICE"}', "notice-key-" + str(number), "pending", stamp, None))
        detail = self.queries.task("a", task["task_id"])["data"]
        for field in ("attempts", "events", "inputs", "decisions", "notifications"):
            self.assertEqual(len(detail[field]), 100)
            self.assertTrue(detail["truncated"][field])
        self.assertNotIn("SECRET_NOTICE", json.dumps(detail))
        team = self.queries.team("a")["data"]
        self.assertEqual(len(team["members"]), 100)
        self.assertTrue(team["truncated"]["members"])
        before = self.snapshot()
        for section in ("attempts", "events", "inputs", "decisions", "notifications"):
            cursor, items = None, []
            while True:
                page = self.queries.task_history("a", task["task_id"], section=section,
                    record_generation=1, limit=37, cursor=cursor)["data"]
                self.assertLessEqual(len(page["items"]), 37)
                items.extend(page["items"])
                cursor = page["next_cursor"]
                if cursor is None:
                    break
            self.assertEqual(len(items), 101)
            for secret in ("SECRET_NOTICE", "owner-secret", "lease-secret", "operations"):
                self.assertNotIn(secret, json.dumps(items))
        self.assertEqual(self.snapshot(), before)

    def test_every_response_uses_one_read_transaction(self):
        task = self.submit()
        calls = ((self.queries.deployment, ()), (self.queries.overview, ("a",)),
                 (self.queries.tasks, ("a",)), (self.queries.task, ("a", task["task_id"])),
                 (self.queries.team, ("a",)), (self.queries.resources, ("a",)),
                 (self.queries.connections, ("a",)), (self.queries.task_history, ("a", task["task_id"])))
        for method, args in calls:
            with patch.object(self.queries._platform, "_db", wraps=self.queries._platform._db) as reader:
                method(*args)
            reader.assert_called_once_with()
        self.assertTrue(self.queries._platform.readonly)

    def test_result_only_returns_public_error_fields(self):
        task = self.submit()
        attempt = self.platform.claim(self.actor, task["task_id"], "local")
        self.platform.complete(self.actor, attempt["attempt_id"], attempt["lease_token"],
                               error={"code": "synthetic_failure", "message": "public failure"})
        with self.platform._db(write=True) as db:
            db.execute("UPDATE results SET error=? WHERE task_id=?",
                       (json.dumps({"code": "synthetic_failure", "message": "public failure", "raw": "SECRET_ERROR"}), task["task_id"]))
        data = self.queries.task("a", task["task_id"])["data"]
        self.assertEqual(data["result"]["error"], {"code": "synthetic_failure", "message": "public failure"})
        self.assertIsNone(data["result"]["output"])
        self.assertNotIn("SECRET_ERROR", json.dumps(data))

    def test_runtime_facts_keep_requested_model_separate_and_sanitize_usage(self):
        task, attempt = self.runtime_attempt()
        self.platform.record_runtime_event(self.actor, attempt["attempt_id"], attempt["lease_token"],
                                           "runtime_started", {"pid": 123})
        self.platform.record_runtime_event(self.actor, attempt["attempt_id"], attempt["lease_token"],
                                           "runtime_thread", {"thread_id": "synthetic-thread"})
        before = self.snapshot()
        facts = self.queries.task("a", task["task_id"])["data"]["attempts"][0]["runtime"]
        self.assertEqual(facts["model_requested"], "requested-model")
        self.assertIsNone(facts["model_observed"])
        self.assertIsNone(facts["usage"])
        self.assertTrue(facts["started"])
        self.assertEqual(facts["thread_id"], "synthetic-thread")
        self.assertEqual(self.snapshot(), before)
        self.platform.record_runtime_event(self.actor, attempt["attempt_id"], attempt["lease_token"],
            "runtime_stopped", {"outcome": "succeeded", "termination_confirmed": True,
                "model_requested": "requested-model", "model_observed": "observed-model",
                "thread_id": "synthetic-thread", "turn_id": "synthetic-turn",
                "usage": {"input_tokens": 10, "cached_input_tokens": 2, "output_tokens": 3,
                          "reasoning_output_tokens": True, "private_reasoning": "HIDDEN_USAGE",
                          "nested": {"secret": "HIDDEN_NESTED_USAGE"}}, "raw": "HIDDEN_RUNTIME_RAW"})
        self.platform.complete(self.actor, attempt["attempt_id"], attempt["lease_token"], output="synthetic output")
        before = self.snapshot()
        detail = self.queries.task("a", task["task_id"])["data"]
        facts = detail["attempts"][0]["runtime"]
        self.assertEqual(facts["model_observed"], "observed-model")
        self.assertEqual(facts["usage"], {"input_tokens": 10, "cached_input_tokens": 2, "output_tokens": 3})
        self.assertEqual(facts["turn_id"], "synthetic-turn")
        self.assertTrue(facts["termination_confirmed"])
        connection = self.queries.connections("a")["data"]["items"][0]
        self.assertEqual(connection["model_observed"], "observed-model")
        self.assertEqual(connection["usage"], facts["usage"])
        self.assertEqual(connection["latest_attempt"]["attempt_id"], attempt["attempt_id"])
        self.assertEqual(connection["latest_attempt"]["evidence_source"], "persisted_attempt_events")
        self.assertEqual(connection["connectivity"], "not_verified")
        self.assertEqual(connection["authentication"], "not_verified")
        for secret in ("HIDDEN_USAGE", "HIDDEN_NESTED_USAGE", "HIDDEN_RUNTIME_RAW", attempt["lease_token"], self.actor.token):
            self.assertNotIn(secret, json.dumps([detail, connection]))
        self.assertEqual(self.snapshot(), before)

    def test_operation_availability_tracks_lease_stop_and_runtime_capability(self):
        task, attempt = self.runtime_attempt(lease_seconds=10)
        def operations():
            return self.queries.task("a", task["task_id"])["data"]["attempts"][0]["operations"]
        self.assertEqual(operations(), {"can_input": True, "input_reason": None, "can_stop": True, "stop_reason": None})
        self.platform.request_runtime_stop("a", attempt["attempt_id"], "synthetic stop")
        self.assertEqual(operations(), {"can_input": False, "input_reason": "stop_requested",
                                        "can_stop": False, "stop_reason": "stop_requested"})
        self.now += 11
        before = self.snapshot()
        self.assertEqual(operations()["input_reason"], "lease_expired")
        self.assertEqual(operations()["stop_reason"], "lease_expired")
        self.assertEqual(self.snapshot(), before)
        with self.platform._db() as db:
            self.assertEqual(db.execute("SELECT state FROM attempts WHERE attempt_id=?", (attempt["attempt_id"],)).fetchone()[0], "running")
        task2, attempt2 = self.runtime_attempt(supports_input=False)
        operations2 = self.queries.task("a", task2["task_id"])["data"]["attempts"][0]["operations"]
        self.assertEqual(operations2["input_reason"], "runtime_input_unsupported")
        self.assertTrue(operations2["can_stop"])
        self.platform.record_runtime_event(self.actor, attempt2["attempt_id"], attempt2["lease_token"],
                                           "runtime_stopped", {"termination_confirmed": False})
        operations2 = self.queries.task("a", task2["task_id"])["data"]["attempts"][0]["operations"]
        self.assertEqual(operations2["input_reason"], "runtime_finished")
        self.assertEqual(operations2["stop_reason"], "runtime_finished")

    def test_input_availability_requires_current_owner_and_member(self):
        task = self.submit()
        member = self.platform.start_member(self.actor, "b" * 64, "think", 10)
        attempt = self.platform.claim_member_task(self.actor, member["member_id"], member["member_token"], 60)
        self.platform.record_runtime_event(self.actor, attempt["attempt_id"], attempt["lease_token"],
                                           "runtime_start_requested", {"supports_runtime_inputs": True})
        self.now += 11
        ops = self.queries.task("a", task["task_id"])["data"]["attempts"][0]["operations"]
        self.assertEqual(ops["input_reason"], "member_unavailable")
        self.assertTrue(ops["can_stop"])
        with self.platform._db(write=True) as db:
            db.execute("DELETE FROM actor_tokens WHERE installation_id='a'")
        ops = self.queries.task("a", task["task_id"])["data"]["attempts"][0]["operations"]
        self.assertEqual(ops["input_reason"], "owner_unavailable")
        self.assertTrue(ops["can_stop"])

    def test_connection_evidence_is_installation_and_generation_scoped(self):
        task, attempt = self.runtime_attempt()
        self.platform.complete(self.actor, attempt["attempt_id"], attempt["lease_token"], output="synthetic completed")
        self.switch("b")
        self.assertEqual(self.queries.task("a", task["task_id"])["data"]["attempts"][0]["operations"]["stop_reason"], "inactive_installation")
        self.assertIsNone(self.queries.connections("b")["data"]["items"][0]["latest_attempt"])
        old = self.queries.connections("a")["data"]["items"][0]["latest_attempt"]
        self.assertEqual(old["attempt_id"], attempt["attempt_id"])
        self.assertEqual(old["generation"], 1)
        self.switch("a")
        current = self.queries.connections("a")
        self.assertEqual(current["source"]["generation"], 2)
        self.assertIsNone(current["data"]["items"][0]["latest_attempt"])
        historic = self.queries.connections("a", generation=1)
        self.assertEqual(historic["source"]["generation"], 1)
        self.assertEqual(historic["data"]["items"][0]["latest_attempt"]["attempt_id"], attempt["attempt_id"])
        ops = self.queries.task("a", task["task_id"], generation=1)["data"]["attempts"][0]["operations"]
        self.assertEqual(ops["input_reason"], "historical_generation")
        self.assertFalse(ops["can_stop"])

    def test_runtime_launch_facts_survive_timeline_truncation(self):
        task, attempt = self.runtime_attempt()
        with self.platform._db(write=True) as db:
            for number in range(101):
                db.execute("INSERT INTO attempt_events VALUES (?,?,?,?,?,?)", ("progress-" + str(number),
                    attempt["attempt_id"], "a", "runtime_progress", '{"phase":"working"}', "synthetic-time"))
        data = self.queries.task("a", task["task_id"])["data"]
        self.assertTrue(data["truncated"]["events"])
        self.assertEqual(len(data["events"]), 100)
        self.assertFalse(any(row["kind"] == "runtime_start_requested" for row in data["events"]))
        self.assertEqual(data["attempts"][0]["runtime"]["model_requested"], "requested-model")
        self.assertTrue(data["attempts"][0]["operations"]["can_input"])

    def test_notification_read_never_acknowledges_or_implies_user_receipt(self):
        task = self.submit()
        self.platform.run_as(self.actor, task["task_id"], "local")
        before = self.snapshot()
        data = self.queries.task("a", task["task_id"])["data"]
        notice = next(row for row in data["notifications"] if row["kind"] == "task_completed")
        self.assertEqual(notice["state"], "pending")
        self.assertIsNone(notice["user_read"])
        self.assertNotIn("payload", notice)
        self.assertNotIn("dedupe_key", notice)
        self.assertEqual(self.snapshot(), before)
        self.platform.ack_outbox("a", notice["event_id"])
        before = self.snapshot()
        notice = next(row for row in self.queries.task("a", task["task_id"])["data"]["notifications"] if row["kind"] == "task_completed")
        self.assertEqual(notice["state"], "delivered")
        self.assertIsNone(notice["user_read"])
        self.assertEqual(self.snapshot(), before)

    def test_live_runtime_observation_is_visible_before_stopping_without_inventing_actual_model(self):
        task, attempt = self.runtime_attempt()
        usage = {"input_tokens": 60, "cached_input_tokens": 10, "output_tokens": 20,
                 "reasoning_output_tokens": 4, "total_tokens": 80, "cache_write_input_tokens": 3}
        self.platform.record_runtime_event(self.actor, attempt["attempt_id"], attempt["lease_token"],
            "runtime_observation", {"model_reported": "server-config-model", "model_reported_source": "thread/start",
                "thread_id": "thread-one", "turn_id": "turn-one", "usage": usage,
                "usage_source": "thread/tokenUsage/updated", "usage_scope": "fresh_thread_total",
                "raw": "HIDDEN_PROTOCOL_BODY"})
        before = self.snapshot()
        detail = self.queries.task("a", task["task_id"])["data"]
        facts = detail["attempts"][0]["runtime"]
        self.assertEqual(facts["model_requested"], "requested-model")
        self.assertEqual(facts["model_reported"], "server-config-model")
        self.assertIsNone(facts["model_observed"])
        self.assertEqual(facts["usage"], usage)
        self.assertEqual(facts["usage_scope"], "fresh_thread_total")
        self.assertIsNotNone(facts["last_observation_at"])
        self.assertFalse(facts["termination_confirmed"])
        connection = self.queries.connections("a")["data"]["items"][0]
        self.assertEqual(connection["model_reported"], "server-config-model")
        self.assertEqual(connection["usage"], usage)
        self.assertEqual(connection["connectivity"], "not_verified")
        self.assertEqual(connection["authentication"], "not_verified")
        self.assertNotIn("HIDDEN_PROTOCOL_BODY", json.dumps([detail, connection]))
        self.assertEqual(self.snapshot(), before)

    def test_observation_is_preserved_on_unknown_stop_and_after_visible_timeline_truncation(self):
        task, attempt = self.runtime_attempt()
        self.platform.record_runtime_event(self.actor, attempt["attempt_id"], attempt["lease_token"],
            "runtime_observation", {"model_reported": "routed-model", "model_reported_source": "model/rerouted",
                "thread_id": "thread-one", "turn_id": "turn-one", "usage": {"input_tokens": 3, "output_tokens": 2},
                "usage_source": "thread/tokenUsage/updated", "usage_scope": "fresh_thread_total"})
        with self.platform._db(write=True) as db:
            for number in range(101):
                db.execute("INSERT INTO attempt_events VALUES (?,?,?,?,?,?)", ("later-" + str(number),
                    attempt["attempt_id"], "a", "runtime_progress", '{"phase":"working"}', "synthetic-time"))
        self.platform.record_runtime_event(self.actor, attempt["attempt_id"], attempt["lease_token"],
            "runtime_stopped", {"outcome": "protocol_error", "usage": None, "termination_confirmed": False})
        detail = self.queries.task("a", task["task_id"])["data"]
        self.assertTrue(detail["truncated"]["events"])
        self.assertFalse(any(event["kind"] == "runtime_observation" for event in detail["events"]))
        facts = detail["attempts"][0]["runtime"]
        self.assertEqual(facts["model_reported"], "routed-model")
        self.assertEqual(facts["model_reported_source"], "model/rerouted")
        self.assertEqual(facts["usage"], {"input_tokens": 3, "output_tokens": 2})
        self.assertFalse(facts["termination_confirmed"])

    def test_observation_sources_and_denial_fields_are_allowlisted_and_read_only(self):
        task, attempt = self.runtime_attempt()
        for method in ("item/fileChange/requestApproval", "item/commandExecution/requestApproval"):
            self.platform.record_runtime_event(self.actor, attempt["attempt_id"], attempt["lease_token"],
                "runtime_native_request_denied", {"method": method, "reason": "runtime_text_only",
                    "thread_id": "thread-one", "turn_id": "turn-one", "command": "HIDDEN_COMMAND",
                    "patch": "HIDDEN_PATCH", "arguments": {"token": "HIDDEN_ARGUMENT"}})
        self.platform.record_runtime_event(self.actor, attempt["attempt_id"], attempt["lease_token"],
            "runtime_observation", {"model_reported": "HIDDEN_UNTRUSTED_MODEL", "model_reported_source": "HIDDEN_SOURCE",
                "usage": {"total_tokens": 0, "input_tokens": -1, "output_tokens": True,
                          "cache_write_input_tokens": 2**63, "raw": "HIDDEN_USAGE"},
                "usage_source": "HIDDEN_SOURCE", "usage_scope": "HIDDEN_SCOPE"})
        before = self.snapshot()
        detail = self.queries.task("a", task["task_id"])["data"]
        facts = detail["attempts"][0]["runtime"]
        self.assertIsNone(facts["model_reported"])
        self.assertIsNone(facts["model_reported_source"])
        self.assertEqual(facts["usage"], {"total_tokens": 0})
        self.assertIsNone(facts["usage_source"])
        self.assertIsNone(facts["usage_scope"])
        self.assertEqual(facts["native_requests_denied"]["count"], 2)
        self.assertEqual(facts["native_requests_denied"]["last_method"], "item/commandExecution/requestApproval")
        self.assertNotIn("HIDDEN_", json.dumps(detail))
        self.assertTrue(detail["attempts"][0]["operations"]["can_input"])
        self.assertEqual(detail["task"]["state"], "running")
        self.assertEqual(self.snapshot(), before)

    def test_observation_is_not_shared_with_another_activation(self):
        task, attempt = self.runtime_attempt()
        self.platform.record_runtime_event(self.actor, attempt["attempt_id"], attempt["lease_token"],
            "runtime_observation", {"model_reported": "first-generation-model", "model_reported_source": "thread/start",
                "usage": {"total_tokens": 12}, "usage_source": "thread/tokenUsage/updated", "usage_scope": "fresh_thread_total"})
        self.platform.complete(self.actor, attempt["attempt_id"], attempt["lease_token"], output="done")
        self.switch("b")
        self.assertIsNone(self.queries.connections("b")["data"]["items"][0]["model_reported"])
        self.switch("a")
        self.assertIsNone(self.queries.connections("a")["data"]["items"][0]["model_reported"])
        historic = self.queries.connections("a", 1)["data"]["items"][0]
        self.assertEqual(historic["model_reported"], "first-generation-model")
        self.assertEqual(historic["usage"], {"total_tokens": 12})


if __name__ == "__main__":
    unittest.main()
