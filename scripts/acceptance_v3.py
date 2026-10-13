"""Opt-in, local MasterAgent archive acceptance; never reads authentication.

--prepare builds synthetic reviewable inputs without native calls.
--run requires --allow-real-model-calls and native authenticated dedicated homes.
--verify reads only this harness's report. Incomplete runs are not auto-resumed.
--self-test uses synthetic protocol/exit evidence, visibly labelled as such.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
from unittest.mock import patch
import zipfile


MAX_CALLS = 10
MAX_TIMEOUT = 120
ROLES = ("reviewer", "recorder")
PHASES = (("A1", "A", 1), ("B1", "B", 1), ("A2", "A", 2))
TASKS = {
    "A1-reviewer-1": "Write 40 numbered short sentences about the fictional blue lantern. Use only the supplied fictional facts. A correction may replace this request.",
    "A1-reviewer-2": "Reply with exactly BLUE_SECOND_OK.",
    "A1-recorder-1": "Reply with exactly BLUE_RECORDER_FIRST_OK.",
    "A1-recorder-2": "Reply with exactly BLUE_RECORDER_SECOND_OK.",
    "A1-stop-retry": "Write 40 numbered short sentences describing a fictional paper boat. Use no tools or external facts.",
    "B1-reviewer-1": "Reply with exactly ORANGE_ONLY_OK.",
    "A2-reviewer-1": "Reply with exactly BLUE_RESTORED_REVIEWER_OK.",
    "A2-recorder-1": "Reply with exactly BLUE_RESTORED_RECORDER_OK.",
}
STEER = "Replace the entire requested response. Reply with exactly CORRECTION_ACCEPTED_BLUE and nothing else."
PREFLIGHT_ERROR_CODES = frozenset({
    "runtime_config_contamination", "runtime_home_bound", "runtime_home_overlap", "runtime_scope_mismatch",
    "unmanaged_runtime_home", "protected_runtime_home", "invalid_runtime_home", "missing_runtime_home",
    "invalid_runtime_workspace", "runtime_project_boundary", "missing_workspace", "linked_runtime_file",
    "invalid_runtime_file", "missing_runtime_file", "invalid_runtime_binding", "invalid_runtime_config",
    "invalid_runtime_executable", "stale_actor", "inactive_installation", "invalid_path", "linked_path",
})


def require(value, message):
    if not value:
        raise AssertionError(message)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def document(path, value):
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def select_bundle(bundle):
    bundle = Path(bundle).resolve(strict=True)
    with zipfile.ZipFile(bundle) as archive:
        manifest = json.loads(archive.read("BUILD-MANIFEST.json"))
        names = archive.namelist()
        require(len(names) == len(set(names)), "Duplicate archive entries")
        require(set(names) == set(manifest["files"]) | {"BUILD-MANIFEST.json"}, "Incomplete archive manifest")
        for name, expected in manifest["files"].items():
            require(hashlib.sha256(archive.read(name)).hexdigest() == expected, "Archive digest mismatch")
    require(not any(n == "masteragent" or n.startswith("masteragent.") for n in sys.modules), "Archive must be selected before imports")
    sys.path.insert(0, str(bundle))
    import masteragent
    import masteragent.runtime as runtime
    import masteragent.runtime_config as runtime_config
    import masteragent.service as service
    import masteragent.state_waiter as state_waiter
    import masteragent.worker as worker
    import masteragent.codex_app_server as app_server
    prefix = str(bundle).replace("\\", "/") + "/masteragent/"
    for name, module in sys.modules.items():
        if name == "masteragent" or name.startswith("masteragent."):
            require(str(module.__file__).replace("\\", "/").startswith(prefix), "Nonarchive module loaded")
    return dict(bundle=bundle, version=masteragent.__version__, runtime=runtime, config=runtime_config,
                service=service, waiter=state_waiter, worker=worker, app=app_server)


def prepare(root, api, executable, expected_version, model):
    root = Path(root).absolute()
    require(not root.exists() or not any(root.iterdir()), "Prepare requires a new empty dedicated root")
    root.mkdir(parents=True, exist_ok=True)
    packages, configs, payloads = {}, {}, {}
    for business in ("A", "B"):
        marker = "BLUE_FICTIONAL_CONTEXT" if business == "A" else "ORANGE_FICTIONAL_CONTEXT"
        package = {"schema_version": 1, "platform_api": 1, "package_id": "fictional-" + business.lower(), "version": "1.0.0",
                   "roles": [{"role_id": role, "display_name": role, "instructions": "Respond to fictional text only. " + marker,
                              "tools": ["compose"]} for role in ROLES],
                   "knowledge": {"fictional_fact": marker},
                   "tools": {"compose": {"adapter": "acceptance-text", "description": "Compose a text response from supplied fictional facts."}},
                   "workflows": {}}
        path = root / ("package-" + business + ".json")
        document(path, package)
        packages[business] = {"path": str(path), "sha256": digest(path)}
    platform = api["service"].Platform(root / "state", external_adapters={"acceptance-text"})
    for business in ("A", "B"):
        default = root / ("workspace-" + business)
        default.mkdir()
        bindings = {}
        for role in ROLES:
            path = root / ("workspace-" + business + "-" + role)
            path.mkdir()
            bindings[role] = {"workspace": str(path), "access": "none"}
        platform.install(business, packages[business]["path"], default, role_deployments=bindings)
    for phase, business, generation in PHASES:
        package = json.loads(Path(packages[business]["path"]).read_text(encoding="utf-8"))
        for role in ROLES:
            home = root / ("home-" + phase + "-" + role)
            home.mkdir()
            config = api["config"].RuntimeConfig(1, "codex-app-server", Path(executable).resolve(),
                expected_version, model, "acceptance-text", business, role, generation, home, MAX_TIMEOUT, 30)
            # Bind before an operator logs in: login may create native log files
            # alongside auth.json. The marker is platform metadata, not auth.
            api["config"].prepare_runtime_home(config, root / "state", root / ("workspace-" + business + "-" + role), bind=True)
            config_path = root / ("runtime-" + phase + "-" + role + ".json")
            document(config_path, config.as_public_dict())
            configs[phase + "-" + role] = {"path": str(config_path), "sha256": digest(config_path),
                                              "home": str(home), "used": not (phase == "B1" and role == "recorder")}
            context = {"package_id": package["package_id"], "role": next(r for r in package["roles"] if r["role_id"] == role),
                       "knowledge": package["knowledge"], "tools": package["tools"]}
            for key, task in TASKS.items():
                if key.startswith(phase + "-" + role + "-") or (key == "A1-stop-retry" and phase == "A1" and role == "reviewer"):
                    payloads[key] = api["runtime"].build_prompt(context, {"text": task}, "compose")
    reviewed = {"notice": "These fictional prompts and the steering text are the only model input text. No repository, fleet, credential or native-history content is included.",
                "prompts": payloads, "steering": STEER,
                "maximum_model_attempts": MAX_CALLS, "maximum_seconds_per_attempt": MAX_TIMEOUT,
                "attempt_plan": ["A1 reviewer 1 (steer)", "A1 reviewer 2", "A1 recorder 1", "A1 recorder 2",
                                 "A1 stop-retry: stop", "A1 stop-retry: explicit retry then disconnect",
                                 "A1 stop-retry: explicit retry then complete", "B1 reviewer 1", "A2 reviewer 1", "A2 recorder 1"]}
    document(root / "REVIEW_MODEL_PAYLOADS.json", reviewed)
    prepared = {"format": 1, "root": str(root), "bundle": str(api["bundle"]), "bundle_sha256": digest(api["bundle"]),
                "version": api["version"], "packages": packages, "configs": configs,
                "review_payload_sha256": digest(root / "REVIEW_MODEL_PAYLOADS.json"),
                "native_compatibility": "NOT_VERIFIED_BY_PREPARE", "authentication": "NOT_READ_OR_PROVISIONED"}
    document(root / "PREPARED.json", prepared)
    return prepared


def verify_saved(root, *, allow_simulated=False):
    """Release gate: correlate independent JSONL observations, not report claims."""
    root = Path(root)
    report = json.loads((root / "REPORT.json").read_text(encoding="utf-8"))
    prepared = json.loads((root / "PREPARED.json").read_text(encoding="utf-8"))
    review = json.loads((root / "REVIEW_MODEL_PAYLOADS.json").read_text(encoding="utf-8"))
    require(report.get("passed") is True, "Acceptance did not pass")
    require(type(report.get("simulated")) is bool, "Missing execution provenance")
    require(allow_simulated or report["simulated"] is False, "Simulated reports cannot pass formal verification")
    require(report["bundle_sha256"] == prepared["bundle_sha256"] == digest(prepared["bundle"]), "Reported bundle changed")
    require(report["evidence_sha256"] == digest(root / "evidence.jsonl"), "Evidence changed after reporting")
    require(report["review_payload_sha256"] == prepared["review_payload_sha256"]
            == digest(root / "REVIEW_MODEL_PAYLOADS.json"), "Reviewed payload changed")
    require(type(review["maximum_model_attempts"]) is int and review["maximum_model_attempts"] == MAX_CALLS,
            "Reviewed budget does not match this acceptance plan")
    require(type(review["maximum_seconds_per_attempt"]) in (int, float)
            and 0 < review["maximum_seconds_per_attempt"] <= MAX_TIMEOUT, "Invalid reviewed timeout")
    for record in list(prepared["packages"].values()) + list(prepared["configs"].values()):
        require(digest(record["path"]) == record["sha256"], "Prepared configuration changed")
    rows = [json.loads(line) for line in (root / "evidence.jsonl").read_text(encoding="utf-8").splitlines()]
    require(rows and all(row.get("simulated") is report["simulated"] for row in rows), "Mixed/unknown evidence provenance")
    require(not any(row["kind"] == "run_failed" for row in rows), "Failed execution in evidence")
    checks = [row["name"] for row in rows if row["kind"] == "check" and row.get("passed") is True]
    require(checks == report["checks"] and not any(row["kind"] == "check" and row.get("passed") is not True for row in rows),
            "Report checks do not match persisted evidence")
    expected = MAX_CALLS
    budgets = [row for row in rows if row["kind"] == "native_attempt_budget"]
    require(len(budgets) == expected and sorted(row["call"] for row in budgets) == list(range(1, expected + 1)),
            "Native attempt count/identity mismatch")
    require(report["real_native_model_attempts"] == (0 if report["simulated"] else expected)
            and report["synthetic_attempts"] == (expected if report["simulated"] else 0), "Reported counter mismatch")
    calls = report["calls"]
    require(len(calls) == expected and [c["call"] for c in calls] == list(range(1, expected + 1)), "Missing/duplicate reported calls")
    require(len({c["attempt_id"] for c in calls}) == expected, "Attempt replay in evidence")
    hashes = {hashlib.sha256(value.encode("utf-8")).hexdigest() for value in review["prompts"].values()}
    for call in calls:
        items = [(index, row) for index, row in enumerate(rows) if row.get("call") == call["call"]]
        def one(kind, method=None):
            found = [(i, row) for i, row in items if row["kind"] == kind and (method is None or row.get("method") == method)]
            require(len(found) == 1, "Missing/duplicate evidence: " + kind + "/" + str(method))
            return found[0]
        budget_i, budget = one("native_attempt_budget")
        for key in ("phase", "role", "mode", "attempt_id", "task_id", "model_requested"):
            require(budget[key] == call[key], "Budget/report identity mismatch")
        require(budget["prompt_sha256"] in hashes, "Unreviewed model prompt")
        require(0 < budget["timeout_seconds"] <= review["maximum_seconds_per_attempt"], "Unreviewed call timeout")
        config_item = prepared["configs"][call["phase"] + "-" + call["role"]]
        config = json.loads(Path(config_item["path"]).read_text(encoding="utf-8"))
        require(budget["isolated_home"] == call["home"] == config["codex_home"] == config_item["home"], "Home scope mismatch")
        require(call["model_requested"] == config["model"] == call["model"], "Model mismatch")
        require(Path(call["home"]) in Path(budget["private_execution_cwd"]).parents, "Execution cwd crossed home scope")
        spawn_i, spawn = one("owned_process_started")
        stop_i, stop = one("owned_process_stop_evidence")
        outcome_i, outcome = one("native_outcome")
        thread_i, thread = one("native_rpc_receipt", "thread/start")
        turn_i, turn = one("native_rpc_receipt", "turn/start")
        require(budget_i < spawn_i < thread_i < turn_i < stop_i < outcome_i, "Invalid native lifecycle order")
        require(type(spawn["pid"]) is int and spawn["pid"] > 0 and spawn["pid"] == stop["pid"], "Owned process mismatch")
        require(stop["termination_confirmed"] is True and call["exit"]["termination_confirmed"] is True
                and call["exit"]["pid"] == stop["pid"] and call["exit"]["returncode"] == stop["returncode"], "Owned exit evidence mismatch")
        for key in ("outcome", "thread_id", "turn_id", "returncode", "termination_confirmed", "controlled_shutdown"):
            require(outcome[key] == call["outcome"][key], "Outcome/report mismatch")
        require(outcome["returncode"] == stop["returncode"] and outcome["termination_confirmed"] is True, "Exit/outcome mismatch")
        require(thread.get("thread_id") == outcome["thread_id"] and thread.get("model") == call["model"]
                and turn.get("turn_id") == outcome["turn_id"] and thread["rpc_id"] != turn["rpc_id"], "RPC scope mismatch")
        require(isinstance(outcome["thread_id"], str) and outcome["thread_id"]
                and isinstance(outcome["turn_id"], str) and outcome["turn_id"], "Missing native identity")
        receipts = [{key: row[key] for key in row if key not in ("kind", "time_unix", "simulated")}
                    for _, row in items if row["kind"] == "native_rpc_receipt"]
        require(receipts == call["rpc_receipts"], "Report RPC list differs from raw evidence")
        if call["mode"] == "steer":
            input_i, persisted = one("steering_persisted")
            steer_i, steer = one("native_rpc_receipt", "turn/steer")
            require(input_i < steer_i < stop_i and persisted["input_id"] == steer["input_id"]
                    and steer["turn_id"] == outcome["turn_id"], "Steer receipt association mismatch")
            require(persisted["steering_sha256"] == hashlib.sha256(review["steering"].encode("utf-8")).hexdigest(),
                    "Steering text differs from reviewed payload")
        if call["mode"] in {"stop", "disconnect"}:
            require(outcome["outcome"] != "succeeded", "Interrupted native attempt was reported successful")
        else:
            require(outcome["outcome"] == "succeeded" and outcome["controlled_shutdown"] is True, "Missing successful native turn")
    require(len({c["outcome"]["thread_id"] for c in calls}) == expected, "Native thread replay")
    require([c["mode"] for c in calls[4:7]] == ["stop", "disconnect", "recovered"]
            and len({c["task_id"] for c in calls[4:7]}) == 1, "Recovery attempts changed task or order")
    require(all(c["phase"] == "A1" for c in calls[:7]) and calls[7]["phase"] == "B1"
            and all(c["phase"] == "A2" for c in calls[8:]), "A/B/A phase order mismatch")
    reconciles = [(i, row) for i, row in enumerate(rows) if row["kind"] == "explicit_reconcile"]
    require(len(reconciles) == 2 and all(r["resolution"] == "retry" and r["safe_to_retry"] is True
            and r["task_id"] == calls[4]["task_id"] for _, r in reconciles), "Explicit reconciliation missing")
    for offset, (reconcile_i, _) in enumerate(reconciles):
        prior_call, next_call = 5 + offset, 6 + offset
        stopped_i = next(i for i, r in enumerate(rows) if r["kind"] == "owned_process_stop_evidence" and r.get("call") == prior_call)
        next_i = next(i for i, r in enumerate(rows) if r["kind"] == "native_attempt_budget" and r.get("call") == next_call)
        require(stopped_i < reconcile_i < next_i, "Retry occurred before confirmed owned exit/reconciliation")
    require(not any(report["remaining_online"].values()), "Members remain online")
    if not report["simulated"]:
        auth = [r for r in rows if r["kind"] == "native_authentication_status"]
        require(len(auth) == 5 and {r["configuration"] for r in auth} == {k for k, v in prepared["configs"].items() if v["used"]}
                and all(r["authenticated"] is True and r["exit_code"] == 0 and r["termination_confirmed"] is True for r in auth),
                "Dedicated authentication evidence incomplete")
    return report


class Acceptance:
    def __init__(self, root, api, simulated=False):
        self.root, self.api, self.simulated = root, api, simulated
        self.lock, self.local = threading.Lock(), threading.local()
        self.prepared = json.loads((root / "PREPARED.json").read_text(encoding="utf-8"))
        self.review = json.loads((root / "REVIEW_MODEL_PAYLOADS.json").read_text(encoding="utf-8"))
        self.budget = self.review["maximum_model_attempts"]
        self.timeout_limit = self.review["maximum_seconds_per_attempt"]
        self.steering = self.review["steering"]
        self.platform = api["service"].Platform(root / "state", external_adapters={"acceptance-text"})
        self.calls, self.checks, self.workers = [], [], []
        self.authentication = []
        self.actors, self.tasks = {}, {}
        self.active = None

    def log(self, kind, **fields):
        row = {"time_unix": time.time(), "kind": kind, "simulated": self.simulated, **fields}
        with self.lock:
            with (self.root / "evidence.jsonl").open("a", encoding="utf-8", newline="\n") as stream:
                stream.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
                stream.flush()
                os.fsync(stream.fileno())

    def check(self, name, value, **fields):
        self.log("check", name=name, passed=bool(value), **fields)
        require(value, "Acceptance check failed: " + name)
        self.checks.append(name)

    def preflight(self):
        require(not (self.root / "evidence.jsonl").exists(), "Run already has evidence; verify it instead of automatic rerun")
        require(digest(self.api["bundle"]) == self.prepared["bundle_sha256"], "Bundle changed after review")
        require(digest(self.root / "REVIEW_MODEL_PAYLOADS.json") == self.prepared["review_payload_sha256"], "Reviewed payload changed")
        require(type(self.budget) is int and self.budget == MAX_CALLS, "Reviewed budget does not match this plan")
        require(type(self.timeout_limit) in (int, float) and 0 < self.timeout_limit <= MAX_TIMEOUT, "Invalid reviewed timeout")
        require(isinstance(self.steering, str) and self.steering.strip(), "Missing reviewed steering text")
        require(not self.platform.status()["active"], "Acceptance state must start inactive")
        for record in list(self.prepared["packages"].values()) + list(self.prepared["configs"].values()):
            require(digest(record["path"]) == record["sha256"], "Prepared file changed")
        if not self.simulated:
            for key, item in self.prepared["configs"].items():
                if not item["used"]:
                    continue
                config = self.api["config"].load_runtime_config(item["path"])
                inspected = self.api["runtime"].inspect_command(
                    [str(config.executable), "login", "status"], config.codex_home,
                    self.api["config"].child_environment(config), timeout_seconds=10, max_output_bytes=16384)
                # The native runtime resolves file/keyring/auto authentication.
                # Never inspect credential files or retain account/output text.
                observation = {"configuration": key,
                    "authenticated": inspected.get("outcome") == "completed" and inspected.get("returncode") == 0
                                     and inspected.get("termination_confirmed") is True,
                    "exit_code": inspected.get("returncode"),
                    "termination_confirmed": inspected.get("termination_confirmed") is True}
                inspected.clear()
                self.authentication.append(observation)
                require(observation["authenticated"], "Native authentication unavailable for dedicated configuration " + key)

    def native_observers(self, stack):
        app = self.api["app"]
        spawn, stop, accept = app.spawn_owned_process, app.stop_owned_process, app.AppServerProtocol.accept
        def observed_spawn(*args, **kwargs):
            process, job = spawn(*args, **kwargs)
            context = getattr(self.local, "context", None)
            if context is not None:
                context["process"], context["job"] = process, job
                self.log("owned_process_started", call=context["call"], pid=process.pid)
            return process, job
        def observed_stop(process, job):
            result = stop(process, job)
            context = getattr(self.local, "context", None)
            if context is not None:
                context["exit"] = {**result, "pid": process.pid, "returncode": process.returncode}
                self.log("owned_process_stop_evidence", call=context["call"], **context["exit"])
            return result
        def observed_accept(protocol, value):
            context = getattr(self.local, "context", None)
            if context is not None and isinstance(value, dict):
                pending = protocol.pending.get((type(value.get("id")), value.get("id"))) if type(value.get("id")) in (str, int) else None
                if pending and isinstance(value.get("error"), dict):
                    code = value["error"].get("code")
                    self.log("native_rpc_error", call=context["call"], method=pending[0],
                             error_code=code if type(code) is int else None)
                method = value.get("method")
                if isinstance(method, str) and method in {"turn/completed", "item/started", "item/completed", "error"}:
                    params = value.get("params")
                    params = params if isinstance(params, dict) else {}
                    item = params.get("item")
                    item_type = item.get("type") if isinstance(item, dict) else None
                    turn = params.get("turn")
                    status = turn.get("status") if isinstance(turn, dict) else None
                    self.log("native_protocol_shape", call=context["call"], method=method,
                        item_type=item_type if isinstance(item_type, str) and item_type in {"agentMessage", "userMessage", "reasoning", "plan", "commandExecution",
                            "fileChange", "mcpToolCall", "dynamicToolCall", "webSearch", "imageView", "imageGeneration"} else None,
                        status=status if isinstance(status, str) and status in {"completed", "failed", "interrupted", "inProgress"} else None)
            if context is not None and isinstance(value, dict) and "id" in value and "result" in value:
                key = (type(value["id"]), value["id"])
                pending = protocol.pending.get(key)
                response = value.get("result")
                if pending and isinstance(response, dict):
                    method, control = pending
                    fields = {"call": context["call"], "method": method, "rpc_id": value["id"]}
                    if method == "thread/start":
                        fields["thread_id"] = response.get("thread", {}).get("id")
                        fields["model"] = response.get("model")
                        context["model"] = fields["model"]
                    elif method == "turn/start":
                        fields["turn_id"] = response.get("turn", {}).get("id")
                    elif method == "turn/steer":
                        fields.update(turn_id=response.get("turnId"), input_id=control)
                    if method in {"thread/start", "turn/start", "turn/steer", "turn/interrupt"}:
                        context["rpc_receipts"].append(fields)
                        self.log("native_rpc_receipt", **fields)
            try:
                return accept(protocol, value)
            except app.ProtocolError:
                if context is not None:
                    self.log("native_protocol_rejected", call=context["call"], error_class="ProtocolError")
                raise
        stack.enter_context(patch.object(app, "spawn_owned_process", observed_spawn))
        stack.enter_context(patch.object(app, "stop_owned_process", observed_stop))
        stack.enter_context(patch.object(app.AppServerProtocol, "accept", observed_accept))

    def deployment_observers(self, stack):
        """Observe exceptions at their source; never change runtime decisions."""
        def observer(original, stage):
            def observed(*args, **kwargs):
                try:
                    return original(*args, **kwargs)
                except Exception as exc:
                    worker = getattr(self.local, "worker_scope", None) or {}
                    claim = getattr(self.local, "claim_scope", None) or {}
                    code = getattr(exc, "code", None)
                    try:
                        self.log("runtime_preflight_rejected", stage=stage,
                            phase=worker.get("phase"), role=worker.get("role"),
                            attempt_id=claim.get("attempt_id"), task_id=claim.get("task_id"),
                            supervisor_entered_for_attempt=False,
                            error_class="PlatformError" if isinstance(exc, self.api["service"].PlatformError) else "Exception",
                            code=code if isinstance(code, str) and code in PREFLIGHT_ERROR_CODES else "unclassified_preflight_error")
                    except Exception:
                        # Diagnostic storage cannot replace the runtime failure.
                        pass
                    raise
            return observed
        # Both call sites precede the supervisor. The worker imports its own
        # reference, so instrument both without replacing either decision.
        for module, stage in ((self.api["worker"], "member_start"), (self.api["runtime"], "claimed_task")):
            stack.enter_context(patch.object(module, "prepare_deployment", observer(module.prepare_deployment, stage)))

    def activate(self, phase, business, generation):
        if self.active:
            self.platform.deactivate(self.active)
        value = self.platform.activate(business)
        self.check("activation_" + phase, value["generation"] == generation)
        self.active = business
        for role in ROLES:
            self.actors[phase + "-" + role] = self.platform.bind(business, role)

    def submit(self, key, business, role):
        task = self.platform.submit(business, role, TASKS[key])
        self.tasks[key] = task
        self.log("task_submitted", key=key, task_id=task["task_id"], installation_id=business, role_id=role)
        return task

    def start_worker(self, phase, role, max_tasks, behavior="normal"):
        api, platform = self.api, self.platform
        config = api["config"].load_runtime_config(self.prepared["configs"][phase + "-" + role]["path"])
        actor = self.actors[phase + "-" + role]
        state = {"waiting": threading.Event(), "done": threading.Event(), "result": None, "error": None,
                 "phase": phase, "role": role, "behavior": behavior}
        harness = self
        class ObservedWaiter(api["waiter"].StateWaiter):
            def wait(waiter_self, timeout_seconds):
                state["waiting"].set()
                return super().wait(timeout_seconds)
        def supervisor(argv, prompt, cwd, env, timeout, heartbeat, event, **callbacks):
            require(prompt in self.review["prompts"].values(), "Unreviewed text would reach model")
            parsed = json.loads(prompt.split("\n", 1)[1])
            require(parsed["role"]["role_id"] == role, "Wrong role prompt")
            forbidden = "ORANGE_FICTIONAL_CONTEXT" if actor.installation_id == "A" else "BLUE_FICTIONAL_CONTEXT"
            require(forbidden not in prompt, "Cross-business prompt contamination")
            require(Path(env["CODEX_HOME"]).resolve() == config.codex_home.resolve(), "Wrong dedicated runtime home")
            require(config.codex_home.resolve() in Path(cwd).resolve().parents, "Execution cwd outside its dedicated home")
            mode = "steer" if behavior == "normal" and parsed["task"] == TASKS["A1-reviewer-1"] else behavior
            with self.lock:
                require(len(self.calls) < self.budget, "Native attempt budget exhausted")
                call = len(self.calls) + 1
                context = {"call": call, "phase": phase, "role": role, "mode": mode, "events": [], "rpc_receipts": [],
                           "model_requested": config.model, "home": str(config.codex_home), "pid": None}
                self.calls.append(context)
            self.local.context = context
            running = [a for a in platform.attempts(actor.installation_id) if a["role_id"] == role and a["state"] == "running"]
            require(len(running) == 1, "Exactly one scoped running attempt is required")
            context["attempt_id"] = running[0]["attempt_id"]
            context["task_id"] = running[0]["task_id"]
            self.log("native_attempt_budget", call=call, phase=phase, role=role, mode=mode,
                     attempt_id=context["attempt_id"], task_id=context["task_id"],
                     prompt_sha256=hashlib.sha256(prompt.encode("utf-8")).hexdigest(), timeout_seconds=timeout,
                     model_requested=config.model, isolated_home=str(config.codex_home), private_execution_cwd=str(cwd))
            require(timeout <= self.timeout_limit, "Runtime timeout exceeds reviewed timeout")
            def observed_event(kind, detail):
                selected = {key: detail[key] for key in ("phase", "pid", "thread_id", "turn_id", "input_id", "status", "method") if key in detail}
                context["events"].append({"kind": kind, **selected})
                self.log("runtime_event", call=call, event_kind=kind, **selected)
                event(kind, detail)
                if kind == "runtime_progress" and detail.get("phase") == "turn_started":
                    if mode == "steer":
                        control = platform.submit_runtime_input(actor.installation_id, context["attempt_id"], self.steering, "reviewed-steer-1")
                        context["input_id"] = control["input_id"]
                        self.log("steering_persisted", call=call, input_id=control["input_id"],
                                 steering_sha256=hashlib.sha256(self.steering.encode("utf-8")).hexdigest())
                    elif mode == "stop":
                        state["worker"].stop("Reviewed fictional acceptance stop")
                        member = next(v for v in platform.members(actor.installation_id) if v["member_id"] == state["worker"]._member)
                        self.check("stop_is_fence_before_process_confirmation", member["state"] == "stopping"
                                   and member["owned_attempt_active"] is False)
                    elif mode == "disconnect" and not self.simulated:
                        self.log("owned_disconnect_injected", call=call, pid=context["process"].pid)
                        context["process"].kill()  # Only this exact Popen; never locate or signal other PIDs.
            try:
                if self.simulated:
                    heartbeat()
                    context["model"] = "synthetic-model"
                    pid = 100000 + call
                    self.log("owned_process_started", call=call, pid=pid)
                    def synthetic_rpc(method, rpc_id, **fields):
                        row = {"call": call, "method": method, "rpc_id": rpc_id, **fields}
                        context["rpc_receipts"].append(row)
                        self.log("native_rpc_receipt", **row)
                    observed_event("runtime_started", {"pid": pid})
                    synthetic_rpc("thread/start", "ma-2", thread_id="synthetic-thread-" + str(call), model="synthetic-model")
                    observed_event("runtime_thread", {"thread_id": "synthetic-thread-" + str(call)})
                    synthetic_rpc("turn/start", "ma-3", turn_id="synthetic-turn-" + str(call))
                    observed_event("runtime_progress", {"phase": "turn_started", "turn_id": "synthetic-turn-" + str(call)})
                    if mode == "steer":
                        control = callbacks["controls"]()
                        callbacks["ack_control"](control["input_id"], "accepted")
                        synthetic_rpc("turn/steer", "ma-4", turn_id="synthetic-turn-" + str(call), input_id=control["input_id"])
                        observed_event("runtime_progress", {"phase": "steer_accepted", "input_id": control["input_id"], "turn_id": "synthetic-turn-" + str(call)})
                    success = mode not in {"stop", "disconnect"}
                    output = "CORRECTION_ACCEPTED_BLUE" if mode == "steer" else "synthetic answer"
                    if parsed["task"].startswith("Reply with exactly "):
                        output = parsed["task"][len("Reply with exactly "):].removesuffix(".")
                    outcome = {"outcome": "succeeded" if success else "interrupted", "output": output if success else None,
                               "thread_id": "synthetic-thread-" + str(call), "turn_id": "synthetic-turn-" + str(call),
                               "usage": None, "returncode": 1, "termination_confirmed": True, "controlled_shutdown": success}
                    context["exit"] = {"termination_confirmed": True, "termination_applied": True,
                                       "pre_stop_returncode": None, "pid": pid, "returncode": 1}
                    self.log("owned_process_stop_evidence", call=call, **context["exit"])
                else:
                    outcome = api["app"].supervise_app_server(argv, prompt, cwd, env, min(timeout, self.timeout_limit),
                                                               heartbeat, observed_event, **callbacks)
                context["outcome"] = outcome
                self.log("native_outcome", call=call, **{key: outcome.get(key) for key in (
                    "outcome", "thread_id", "turn_id", "returncode", "termination_confirmed", "controlled_shutdown")})
                return outcome
            finally:
                self.local.context = None
        class ObservedRunner(api["runtime"].CodexRunner):
            def run_claimed(runner_self, actor, issued, *args, **kwargs):
                # Only public claim IDs, never lease/member/Actor bearer tokens.
                harness.local.claim_scope = {key: issued.get(key) for key in ("attempt_id", "task_id")}
                try:
                    return super().run_claimed(actor, issued, *args, **kwargs)
                finally:
                    harness.local.claim_scope = None
        runner = ObservedRunner(platform, config, supervisor=supervisor,
            **({"probe": lambda cfg: {"compatible": True, "simulated": True}} if self.simulated else {}))
        def observe(value):
            self.log("member_state", phase=phase, role=role,
                     **{key: value.get(key) for key in ("member_id", "state", "online", "reception_waiting", "owned_attempt_active")})
        state["worker"] = api["worker"].MemberWorker(platform, config, actor, "compose", runner=runner,
                                                     waiter_factory=ObservedWaiter, on_state=observe)
        def serve():
            self.local.worker_scope = {"phase": phase, "role": role}
            try:
                state["result"] = state["worker"].run(max_tasks=max_tasks, max_seconds=MAX_TIMEOUT * max_tasks + 60)
            except BaseException as exc:
                state["error"] = {"type": type(exc).__name__, "code": getattr(exc, "code", None)}
            finally:
                self.local.worker_scope = None
                state["done"].set()
        state["thread"] = threading.Thread(target=serve, daemon=True)
        self.workers.append(state)
        state["thread"].start()
        return state

    def await_waiting(self, state):
        require(state["waiting"].wait(30), "Member did not reach actual armed wait")
        require(not state["done"].is_set(), "Member stopped before receiving work")

    def await_done(self, state, expected_complete):
        deadline = time.monotonic() + MAX_TIMEOUT * max(expected_complete, 1) + 45
        while not state["done"].wait(min(30, max(0, deadline - time.monotonic()))):
            require(time.monotonic() < deadline, "Bounded member deadline exceeded")
        require(state["error"] is None, "Member failed: " + str(state["error"]))
        result = state["result"]
        self.log("member_finished", phase=state["phase"], role=state["role"], behavior=state["behavior"],
                 **{key: result[key] for key in ("member_id", "state", "attempted_tasks", "completed_tasks", "reason")})
        self.check("member_finished_" + state["phase"] + "_" + state["role"] + "_" + state["behavior"],
                   result["state"] == "stopped" and result["completed_tasks"] == expected_complete)
        return result

    def retry(self, task, behavior):
        worker = self.start_worker("A1", "reviewer", 1, behavior)
        self.await_waiting(worker)
        before = self.platform.attempts("A", task["task_id"])
        current = next(t for t in self.platform.history("A")["tasks"] if t["task_id"] == task["task_id"])
        self.check("no_automatic_retry_before_" + behavior, current["state"] == "needs_reconcile"
                   and len(before) == (1 if behavior == "disconnect" else 2))
        self.check("no_result_before_explicit_retry_" + behavior,
                   not any(r["task_id"] == task["task_id"] for r in self.platform.results("A")))
        previous = next(c for c in reversed(self.calls) if c["task_id"] == task["task_id"])
        self.check("owned_exit_before_explicit_retry_" + behavior,
                   previous.get("exit", {}).get("termination_confirmed") is True)
        self.platform.reconcile_task("A", task["task_id"], "retry",
            "Fictional text-only acceptance: prior owned process exit confirmed; repeating has no external side effects.", safe_to_retry=True)
        self.log("explicit_reconcile", task_id=task["task_id"], resolution="retry", safe_to_retry=True)
        self.await_done(worker, 0 if behavior == "disconnect" else 1)

    def execute(self):
        self.preflight()
        self.log("run_started", bundle_sha256=self.prepared["bundle_sha256"], maximum_native_attempts=self.budget,
                 maximum_seconds_per_attempt=self.timeout_limit, review_payload_sha256=self.prepared["review_payload_sha256"],
                 real_model_calls_enabled=not self.simulated)
        for observation in self.authentication:
            self.log("native_authentication_status", **observation)
        try:
            with ExitStack() as stack:
                self.deployment_observers(stack)
                if not self.simulated:
                    self.native_observers(stack)
                self.activate("A1", "A", 1)
                first = [self.start_worker("A1", role, 2) for role in ROLES]
                for worker in first:
                    self.await_waiting(worker)
                    for ordinal in (1, 2):
                        self.submit("A1-" + worker["role"] + "-" + str(ordinal), "A", worker["role"])
                for worker in first:
                    result = self.await_done(worker, 2)
                    self.check("same_member_two_tasks_" + worker["role"], result["attempted_tasks"] == 2)
                inputs = self.platform.runtime_inputs("A")
                self.check("native_steering_receipt", len(inputs) == 1 and inputs[0]["state"] == "accepted")
                steered_task = self.tasks["A1-reviewer-1"]["task_id"]
                steered_result = next(r for r in self.platform.results("A") if r["task_id"] == steered_task)
                self.check("steering_effect_on_final_text", steered_result["output"].strip() == "CORRECTION_ACCEPTED_BLUE")
                stopped = self.start_worker("A1", "reviewer", 1, "stop")
                self.await_waiting(stopped)
                task = self.submit("A1-stop-retry", "A", "reviewer")
                self.await_done(stopped, 0)
                self.retry(task, "disconnect")
                self.retry(task, "recovered")
                attempts = self.platform.attempts("A", task["task_id"])
                self.check("uncertain_history_retained", [a["state"] for a in attempts] == ["expired", "expired", "completed"])
                old_actor = self.actors["A1-reviewer"]
                self.activate("B1", "B", 1)
                rejected = False
                try:
                    self.platform.context_for(old_actor)
                except self.api["service"].PlatformError:
                    rejected = True
                self.check("old_a_actor_rejected_in_b", rejected)
                other = self.start_worker("B1", "reviewer", 1)
                self.await_waiting(other)
                self.submit("B1-reviewer-1", "B", "reviewer")
                self.await_done(other, 1)
                self.activate("A2", "A", 2)
                restored = [self.start_worker("A2", role, 1) for role in ROLES]
                for worker in restored:
                    self.await_waiting(worker)
                    self.submit("A2-" + worker["role"] + "-1", "A", worker["role"])
                for worker in restored:
                    self.await_done(worker, 1)
                self.check("ten_bounded_attempts", len(self.calls) == 10)
                self.check("unique_native_threads", len({c["outcome"]["thread_id"] for c in self.calls}) == 10)
                self.check("native_thread_and_turn_present", all(c["outcome"].get("thread_id") and c["outcome"].get("turn_id") for c in self.calls))
                self.check("all_owned_processes_confirmed_stopped", all(c.get("exit", {}).get("termination_confirmed") is True for c in self.calls))
                self.check("native_model_observed", all(isinstance(c.get("model"), str) and c["model"] for c in self.calls))
                self.check("native_model_matches_requested", all(c.get("model") == c["model_requested"] for c in self.calls))
                if not self.simulated:
                    self.check("raw_native_rpc_thread_turn_receipts", all(
                        any(r["method"] == "thread/start" and r.get("thread_id") == c["outcome"]["thread_id"] for r in c["rpc_receipts"])
                        and any(r["method"] == "turn/start" and r.get("turn_id") == c["outcome"]["turn_id"] for r in c["rpc_receipts"])
                        for c in self.calls))
                    steered = next(c for c in self.calls if c["mode"] == "steer")
                    self.check("raw_native_steer_receipt_matches_attempt", any(
                        r["method"] == "turn/steer" and r.get("input_id") == steered["input_id"]
                        and r.get("turn_id") == steered["outcome"]["turn_id"] for r in steered["rpc_receipts"]))
                self.check("a_b_a_home_separation", len({c["home"] for c in self.calls}) == 5)
                for key, task in self.tasks.items():
                    if TASKS[key].startswith("Reply with exactly "):
                        business = "B" if key.startswith("B") else "A"
                        expected = TASKS[key][len("Reply with exactly "):].removesuffix(".")
                        result = next(r for r in self.platform.history(business)["results"] if r["task_id"] == task["task_id"])
                        self.check("fictional_output_" + key, result["output"].strip() == expected)
                self.check("archive_unchanged", digest(self.api["bundle"]) == self.prepared["bundle_sha256"])
                for business in ("A", "B"):
                    self.check("no_member_left_online_" + business, not any(m["online"] for m in self.platform.members(business)))
                self.report(True)
        except BaseException as exc:
            self.log("run_failed", error_type=type(exc).__name__, code=getattr(exc, "code", None))
            for worker in self.workers:
                if worker["thread"].is_alive():
                    try:
                        worker["worker"].stop("Acceptance stopped after a failed check")
                    except Exception:
                        pass
                    worker["thread"].join(45)
            self.report(False)
            raise

    def report(self, passed):
        def public_call(value):
            return {key: value.get(key) for key in ("call", "phase", "role", "mode", "attempt_id", "task_id", "model_requested",
                                                   "model", "home", "events", "rpc_receipts", "exit", "outcome")}
        document(self.root / "REPORT.json", {"passed": passed, "simulated": self.simulated,
            "bundle_sha256": self.prepared["bundle_sha256"], "version": self.prepared["version"],
            "evidence_sha256": digest(self.root / "evidence.jsonl"),
            "review_payload_sha256": self.prepared["review_payload_sha256"],
            "real_native_model_attempts": 0 if self.simulated else len(self.calls),
            "synthetic_attempts": len(self.calls) if self.simulated else 0,
            "authentication": self.authentication,
            "checks": self.checks, "calls": [public_call(c) for c in self.calls],
            "remaining_online": {b: [m["member_id"] for m in self.platform.members(b) if m["online"]] for b in ("A", "B")},
            "limitations": ["No native resume, file tools, approval grant, production migration or fleet takeover.",
                            "Accepted steering receipt and final text adoption are checked separately.",
                            "Online/owned_attempt_active are not OS process-exit evidence."]})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    for name in ("prepare", "run", "verify", "self-test"):
        mode.add_argument("--" + name, action="store_true")
    parser.add_argument("--root", type=Path)
    parser.add_argument("--bundle", type=Path)
    parser.add_argument("--executable", type=Path)
    parser.add_argument("--expected-version")
    parser.add_argument("--model")
    parser.add_argument("--allow-real-model-calls", action="store_true")
    args = parser.parse_args()
    if args.verify:
        require(args.root is not None, "--root is required")
        report = verify_saved(args.root)
        print(json.dumps({key: report[key] for key in ("passed", "simulated", "version", "real_native_model_attempts", "synthetic_attempts")}, ensure_ascii=False))
        return 0 if report["passed"] else 1
    if args.run:
        require(args.allow_real_model_calls, "--run requires explicit --allow-real-model-calls")
        require(args.root is not None, "--root is required")
        prepared = json.loads((args.root / "PREPARED.json").read_text(encoding="utf-8"))
        api = select_bundle(args.bundle or prepared["bundle"])
        Acceptance(args.root, api).execute()
        return 0
    require(args.bundle is not None, "--bundle is required")
    api = select_bundle(args.bundle)
    if args.self_test:
        def reject_external_activity(event, arguments):
            if (event.startswith(("subprocess.", "socket.", "os.exec", "os.spawn"))
                    or event in {"os.system", "os.fork", "os.forkpty", "os.startfile", "os.posix_spawn"}):
                raise AssertionError("Harness self-test forbids process/network activity")
        sys.addaudithook(reject_external_activity)
        with tempfile.TemporaryDirectory(prefix="ma3-release-harness-test-") as temporary:
            root = Path(temporary).resolve() / "prepared"
            prepare(root, api, Path(sys.executable), "synthetic-version", "synthetic-model")
            auth_check = Acceptance(root, api)
            with patch.object(api["runtime"], "inspect_command", side_effect=lambda *a, **k: {
                    "outcome": "completed", "returncode": 0, "termination_confirmed": True,
                    "stdout": "SYNTHETIC_ACCOUNT_TEXT_MUST_BE_DISCARDED"}) as inspection:
                auth_check.preflight()
                require(inspection.call_count == 5, "Native auth status must be scoped to every used home")
                require(len(auth_check.authentication) == 5, "Missing auth observations")
                require("SYNTHETIC_ACCOUNT_TEXT" not in json.dumps(auth_check.authentication), "Auth text retained")
                require(all(not (Path(c["home"]) / "auth.json").exists()
                            for c in auth_check.prepared["configs"].values()), "Self-test must not provision auth")
            Acceptance(root, api, simulated=True).execute()
            report = verify_saved(root, allow_simulated=True)
            rejected = False
            try:
                verify_saved(root)
            except AssertionError:
                rejected = True
            require(rejected, "Formal verifier accepted simulated evidence")
            # Even changing the report's flag cannot relabel the JSONL trace.
            original_report = (root / "REPORT.json").read_bytes()
            altered = dict(report, simulated=False, real_native_model_attempts=10, synthetic_attempts=0)
            document(root / "REPORT.json", altered)
            rejected = False
            try:
                verify_saved(root)
            except AssertionError:
                rejected = True
            finally:
                (root / "REPORT.json").write_bytes(original_report)
            require(rejected, "Formal verifier accepted relabelled synthetic evidence")
            # Recomputed report hashes cannot hide a missing owned-stop event.
            original_trace = (root / "evidence.jsonl").read_bytes()
            trace = [json.loads(line) for line in original_trace.decode("utf-8").splitlines()]
            removed = next(i for i, row in enumerate(trace) if row["kind"] == "owned_process_stop_evidence")
            del trace[removed]
            (root / "evidence.jsonl").write_text("".join(json.dumps(row) + "\n" for row in trace), encoding="utf-8")
            altered = dict(report, evidence_sha256=digest(root / "evidence.jsonl"))
            document(root / "REPORT.json", altered)
            rejected = False
            try:
                verify_saved(root, allow_simulated=True)
            except AssertionError:
                rejected = True
            finally:
                (root / "evidence.jsonl").write_bytes(original_trace)
                (root / "REPORT.json").write_bytes(original_report)
            require(rejected, "Verifier accepted an incomplete owned-process trace")
            diagnostic_root = Path(temporary).resolve() / "diagnostic"
            prepare(diagnostic_root, api, Path(sys.executable), "synthetic-version", "synthetic-model")
            diagnostic = Acceptance(diagnostic_root, api, simulated=True)
            sentinel = api["service"].PlatformError("runtime_config_contamination", "PRIVATE_EXCEPTION_MESSAGE_MUST_NOT_BE_LOGGED")
            with patch.object(api["runtime"], "prepare_deployment", side_effect=sentinel), ExitStack() as stack:
                diagnostic.deployment_observers(stack)
                diagnostic.local.worker_scope = {"phase": "A1", "role": "reviewer"}
                diagnostic.local.claim_scope = {"attempt_id": "synthetic-attempt", "task_id": "synthetic-task"}
                try:
                    api["runtime"].prepare_deployment(None, None)
                except api["service"].PlatformError as exc:
                    require(exc is sentinel, "Preflight observer changed the original failure")
                else:
                    raise AssertionError("Preflight observer swallowed a failure")
            diagnostic_text = (diagnostic_root / "evidence.jsonl").read_text(encoding="utf-8")
            diagnostic_row = json.loads(diagnostic_text)
            require(diagnostic_row["code"] == "runtime_config_contamination"
                    and diagnostic_row["stage"] == "claimed_task"
                    and diagnostic_row["supervisor_entered_for_attempt"] is False
                    and diagnostic_row["attempt_id"] == "synthetic-attempt"
                    and "PRIVATE_EXCEPTION" not in diagnostic_text,
                    "Preflight diagnostics lost safe identity or disclosed exception text")
            with patch.object(api["runtime"], "prepare_deployment", side_effect=sentinel), \
                    patch.object(diagnostic, "log", side_effect=OSError("SYNTHETIC_DIAGNOSTIC_WRITE_FAILURE")), \
                    ExitStack() as stack:
                diagnostic.deployment_observers(stack)
                try:
                    api["runtime"].prepare_deployment(None, None)
                except api["service"].PlatformError as exc:
                    require(exc is sentinel, "Diagnostic write failure replaced the original runtime failure")
                else:
                    raise AssertionError("Diagnostic write failure swallowed the original runtime failure")
            print(json.dumps({"harness_self_test": "passed", "synthetic_attempts": report["synthetic_attempts"],
                              "real_model_calls": 0, "checks": len(report["checks"]),
                              "formal_gate_negative_checks": 3, "preflight_diagnostic_checks": 2}, ensure_ascii=False))
        return 0
    require(all((args.root, args.executable, args.expected_version, args.model)),
            "--prepare requires --root --bundle --executable --expected-version --model")
    prepared = prepare(args.root, api, args.executable, args.expected_version, args.model)
    print(json.dumps({"prepared": True, "review": str(args.root / "REVIEW_MODEL_PAYLOADS.json"),
                      "native_compatibility": prepared["native_compatibility"],
                      "homes": {key: value["home"] for key, value in prepared["configs"].items()}}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
