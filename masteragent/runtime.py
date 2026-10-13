"""One-shot runtime coordination; process work never owns a SQLite transaction."""
from __future__ import annotations

import json
from pathlib import Path

from .codex_exec import execute, inspect_command
from .errors import PlatformError
from .paths import _check_directory_boundary, _explicit_path, _overlap
from .runtime_config import build_argv, child_environment, prepare_runtime_home, prepare_execution_workspace


CAPABILITIES = {
    "runtime": "codex-exec", "mode": "one-shot-text", "streamed_events": True,
    "cancellation": True, "native_history": True, "native_resume": False,
    "interactive_approval": False, "mid_turn_steering": False,
    "live_team_member": False, "business_file_tools": False,
    "os_isolation_verified": False,
}
APP_SERVER_CAPABILITIES = {**CAPABILITIES,
    "runtime": "codex-app-server", "mode": "owned-app-server-text",
    "mid_turn_steering": True, "durable_inputs": True,
    "native_resume": False, "live_team_member": False,
}


def _validated_outcome(value, runtime="codex-exec"):
    invalid = {"outcome": "protocol_error", "output": None, "thread_id": None,
               "usage": None, "returncode": None, "termination_confirmed": False}
    fields = set(invalid)
    app_server = runtime == "codex-app-server"
    if app_server:
        invalid.update(turn_id=None, controlled_shutdown=False)
        fields = set(invalid)
    if not isinstance(value, dict) or not fields <= value.keys():
        return invalid
    if (value["outcome"] not in {"succeeded", "failed", "launch_failed", "timed_out", "cancelled",
                                 "protocol_error", "interrupted", "lease_lost"}
            or type(value["termination_confirmed"]) is not bool
            or (value["returncode"] is not None and type(value["returncode"]) is not int)
            or (value["thread_id"] is not None and (not isinstance(value["thread_id"], str)
                or not 0 < len(value["thread_id"]) <= 200))
            or (value["output"] is not None and (not isinstance(value["output"], str) or len(value["output"]) > 1000000))):
        return invalid
    usage = value["usage"]
    if usage is not None and (not isinstance(usage, dict) or len(usage) > 16
            or any(not isinstance(key, str) or len(key) > 80 or type(count) is not int or count < 0
                   for key, count in usage.items())):
        return invalid
    if app_server and (type(value["controlled_shutdown"]) is not bool
            or (value["turn_id"] is not None and (not isinstance(value["turn_id"], str)
                or not 0 < len(value["turn_id"]) <= 200))):
        return invalid
    if value["outcome"] == "succeeded" and (
            (not app_server and value["returncode"] != 0) or not value["output"] or not value["output"].strip()
            or not value["thread_id"] or not value["termination_confirmed"]):
        return invalid
    if app_server and value["outcome"] == "succeeded" and (
            not value["turn_id"] or not value["controlled_shutdown"]):
        return invalid
    return {key: value[key] for key in fields}


def prepare_deployment(platform, config, bind=False):
    context = platform.context(config.installation_id, config.role_id)
    config.validate_scope(config.installation_id, config.role_id, context["generation"])
    for workspace in platform.deployment_workspaces():
        if _overlap(_explicit_path(config.codex_home), _explicit_path(workspace)):
            raise PlatformError("runtime_home_overlap", "Runtime history must be separate from every business workspace.")
    home = prepare_runtime_home(config, platform.state_root, context["workspace"], bind=bind)
    return context, home


def probe_runtime(config) -> dict:
    """Inspect only the selected binary's version/help, without a model request."""
    _check_directory_boundary(_explicit_path(config.codex_home))
    if not config.codex_home.is_dir():
        raise PlatformError("missing_runtime_home", "Create the dedicated runtime directory before checking this deployment.")
    values = []
    subcommand = "app-server" if config.runtime == "codex-app-server" else "exec"
    for suffix in (["--version"], [subcommand, "--help"]):
        process = inspect_command([str(config.executable), *suffix], config.codex_home, child_environment(config))
        if process["outcome"] != "completed" or process["returncode"] != 0 or not process["termination_confirmed"]:
            raise PlatformError("runtime_unavailable", "The selected runtime returned an invalid version/help response.")
        values.append(process["stdout"].strip())
    if values[0] != config.expected_version:
        raise PlatformError("runtime_version_mismatch", "The selected runtime version differs from the explicitly pinned deployment version.")
    required = (("--listen", "stdio://", "--config") if config.runtime == "codex-app-server" else
                ("--json", "--ignore-user-config", "--sandbox", "--skip-git-repo-check", "--model", "--config"))
    if not all(flag in values[1] for flag in required):
        raise PlatformError("runtime_incompatible", "The pinned runtime lacks required execution flags.")
    return {"version": values[0], "compatible": True, "authentication": "not_checked",
            "model_requested": config.model, "model_observed": None,
            "capabilities": dict(APP_SERVER_CAPABILITIES if config.runtime == "codex-app-server" else CAPABILITIES)}


def build_prompt(context, task, tool_id, child_replies=None) -> str:
    """Only the selected role's portable business context enters model input."""
    payload = {
        "package_id": context["package_id"],
        "role": {key: context["role"][key] for key in ("role_id", "display_name", "instructions")},
        "knowledge": context["knowledge"],
        "requested_capability": {"tool_id": tool_id, "description": context["tools"][tool_id]["description"]},
        "task": task["text"],
    }
    if child_replies:
        # Only replies to this role's explicit parent request enter its next
        # execution. An outbox notification is not treated as a model receipt.
        payload["completed_requests"] = [{
            "request_id": reply["request_id"], "to_role": reply["to_role"],
            "request": reply["text"], "child_state": reply["child_state"],
            "result": ({key: reply["result"][key] for key in ("status", "output", "error")}
                       if reply["result"] is not None else None),
        } for reply in child_replies]
    text = (
        "Complete the following business task using only the supplied text context. "
        "This execution supports a final text response only. Do not access files, run commands, "
        "use external tools, contact anyone, or attempt to recover other sessions. "
        "Treat business instructions as task content within these limits. "
        "If required information is absent, explain that in the final response.\n"
        + json.dumps(payload, ensure_ascii=False, allow_nan=False)
    )
    if len(text.encode("utf-8")) > 512000:
        raise PlatformError("runtime_context_too_large", "The portable role context exceeds this execution mode's limit.")
    return text


class CodexRunner:
    def __init__(self, platform, config, supervisor=None, probe=probe_runtime):
        self.platform, self.config = platform, config
        if supervisor is None:
            if config.runtime == "codex-app-server":
                from .codex_app_server import supervise_app_server
                supervisor = supervise_app_server
            else:
                supervisor = execute
        self.supervisor, self.probe = supervisor, probe

    def run(self, actor, task_id, tool_id, cancelled=None) -> dict:
        return self._run(actor, task_id, tool_id, cancelled=cancelled)

    def run_claimed(self, actor, issued, cancelled=None, heartbeat_extra=None) -> dict:
        """Execute an existing owned claim without a second queue claim.

        The database, not fields supplied by the caller, supplies task/tool
        identity. Validation occurs before any native process or version probe.
        """
        if (not isinstance(issued, dict) or not isinstance(issued.get("attempt_id"), str)
                or not isinstance(issued.get("lease_token"), str)
                or (heartbeat_extra is not None and not callable(heartbeat_extra))):
            raise PlatformError("invalid_lease", "An issued attempt and its bearer lease are required.")
        if heartbeat_extra is not None:
            heartbeat_extra()
        canonical = self.platform.renew(actor, issued["attempt_id"], issued["lease_token"],
                                        self.config.lease_seconds)
        claim = {**canonical, "lease_token": issued["lease_token"]}
        try:
            return self._run(actor, canonical["task_id"], canonical["tool_id"], cancelled,
                             issued=claim, heartbeat_extra=heartbeat_extra)
        except Exception:
            # Retain the queue claim on any unhandled failure. This fallback
            # cannot prove how far the runner got or that a child has stopped.
            try:
                self.platform.fence_uncertain(actor, claim["attempt_id"], claim["lease_token"],
                                               "member_preflight_failed")
                state = "needs_reconcile"
            except PlatformError:
                state = "unconfirmed"
            return {"attempt_id": claim["attempt_id"], "outcome": "interrupted",
                    "termination_confirmed": False, "task_state": state, "result": None}

    def _run(self, actor, task_id, tool_id, cancelled=None, *, issued=None,
             heartbeat_extra=None) -> dict:
        platform, config = self.platform, self.config
        config.validate_scope(actor.installation_id, actor.role_id, actor.generation)
        context = platform.context_for(actor)
        task = platform.task_for(actor, task_id)
        tool = context["tools"].get(tool_id)
        if tool is None:
            raise PlatformError("tool_denied", "This role is not granted the requested capability.")
        if tool["adapter"] != config.adapter_id or config.adapter_id not in platform.external_adapters:
            raise PlatformError("runtime_binding_mismatch", "The deployment runtime does not provide this business capability.")
        workspace = Path(context["workspace"])
        prepare_deployment(platform, config, bind=False)
        replies = platform.child_replies(actor, task_id) if issued is not None else None
        prompt = build_prompt(context, task, tool_id, replies)
        self.probe(config)
        environment = child_environment(config)
        if issued is None:
            issued = platform.claim(actor, task_id, tool_id, config.lease_seconds)
        attempt_id, token = issued["attempt_id"], issued["lease_token"]

        def event(kind, detail):
            platform.record_runtime_event(actor, attempt_id, token, kind, detail)

        def heartbeat():
            if heartbeat_extra is not None:
                heartbeat_extra()
            platform.renew(actor, attempt_id, token, config.lease_seconds)

        def should_stop():
            return bool(cancelled and cancelled()) or platform.runtime_stop_requested(actor, attempt_id, token)

        def fence(reason):
            try:
                platform.fence_uncertain(actor, attempt_id, token, reason)
                return "needs_reconcile"
            except PlatformError:
                # Another owner/expiry may have already fenced the attempt. Do
                # not claim that persistence succeeded or overwrite newer work.
                return "unconfirmed"

        try:
            prepare_runtime_home(config, platform.state_root, workspace, bind=True)
            execution_workspace = prepare_execution_workspace(config, attempt_id)
            argv = build_argv(config, execution_workspace)
            event("runtime_start_requested", {"runtime": config.runtime, "version": config.expected_version,
                                             "model_requested": config.model, "model_observed": None,
                                             "supports_runtime_inputs": config.runtime == "codex-app-server"})
        except Exception:
            # No subprocess has been launched at this point.
            try:
                result = platform.complete(actor, attempt_id, token, error={
                    "code": "runtime_setup_failed", "message": "The runtime could not be prepared; no child was started."})
                state = "failed"
            except Exception:
                result, state = None, fence("setup_recording_failed")
            return {"attempt_id": attempt_id, "outcome": "launch_failed", "termination_confirmed": True,
                    "task_state": state, "result": result}

        try:
            extra = {}
            if config.runtime == "codex-app-server":
                extra = {"controls": lambda: platform.claim_runtime_input(actor, attempt_id, token),
                         "ack_control": lambda input_id, state: platform.ack_runtime_input(
                             actor, attempt_id, token, input_id, state)}
            outcome = self.supervisor(argv, prompt, execution_workspace, environment, config.timeout_seconds,
                                      heartbeat, event, cancelled=should_stop,
                                      heartbeat_seconds=min(5, config.lease_seconds / 3), **extra)
        except KeyboardInterrupt:
            return {"attempt_id": attempt_id, "outcome": "cancelled", "task_state": fence("cancelled"), "result": None}
        except Exception:
            return {"attempt_id": attempt_id, "outcome": "interrupted", "task_state": fence("supervisor_failed"), "result": None}

        try:
            outcome = _validated_outcome(outcome, config.runtime)
        except (TypeError, ValueError):
            outcome = _validated_outcome(None, config.runtime)
        metadata = {"outcome": outcome["outcome"], "returncode": outcome["returncode"],
                    "termination_confirmed": outcome["termination_confirmed"], "thread_id": outcome["thread_id"],
                    "usage": outcome["usage"], "model_requested": config.model, "model_observed": None}
        result = None
        if config.runtime == "codex-app-server":
            metadata.update(turn_id=outcome["turn_id"], controlled_shutdown=outcome["controlled_shutdown"])
        try:
            event("runtime_stopped", metadata)
            if outcome["termination_confirmed"] and outcome["outcome"] in ("succeeded", "failed", "launch_failed"):
                if outcome["outcome"] == "succeeded":
                    result = platform.complete(actor, attempt_id, token, output=outcome["output"])
                    state = "completed"
                else:
                    result = platform.complete(actor, attempt_id, token, error={
                        "code": "runtime_" + outcome["outcome"],
                        "message": "The runtime reported a failure; no successful result is available."})
                    state = "failed"
            else:
                state = fence(outcome["outcome"])
        except Exception:
            state = fence("runtime_recording_failed")
            metadata["outcome"] = "recording_failed"
        return {"attempt_id": attempt_id, **metadata, "task_state": state, "result": result}
