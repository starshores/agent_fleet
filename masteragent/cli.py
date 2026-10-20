"""JSON command interface for explicitly selected isolated deployments."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys

from . import __version__
from .contracts import ContractError, load_package
from .paths import PathSafetyError
from .service import Actor, Platform, PlatformError
from .storage import migrate_state
from .runtime_config import load_runtime_config
from .runtime import CodexRunner, prepare_deployment, probe_runtime


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise PlatformError("invalid_command", message)


def parser() -> argparse.ArgumentParser:
    result = Parser(description="MasterAgent business-independent coordination platform.")
    result.add_argument("--version", action="version", version=__version__)
    result.add_argument("--state-root", help="Explicit absolute directory outside the source and runtime homes.")
    result.add_argument("--runtime-config", help="Explicit deployment JSON; never inherited from a business package.")
    commands = result.add_subparsers(dest="command", required=True)
    from .routing_commands import ACTIONS, QUERY_ACTIONS
    routing_command = commands.add_parser("routing-command", help="Apply an explicit local routing action to existing state; never initialize or execute models.")
    routing_command.add_argument("--action", required=True, choices=tuple(ACTIONS))
    routing_command.add_argument("--payload-file", required=True)
    routing_command.add_argument("--lease-file")
    routing_command.add_argument("--lease-output", help="New local file for an issued transport lease; secrets are not printed.")
    routing_query = commands.add_parser("routing-query", help="Read persistent routing and destination receipt facts without maintenance.")
    routing_query.add_argument("resource", choices=QUERY_ACTIONS)
    for field in ("route-id", "admission-id", "installation-id", "delivery-id"):
        routing_query.add_argument("--" + field)
    receipt_init = commands.add_parser("receipt-init", help="Explicitly initialize an isolated local destination receipt store.")
    receipt_init.add_argument("--receipt-root", required=True)
    receipt_init.add_argument("--destination-namespace", required=True)
    receipt_init.add_argument("--destination-id", required=True)
    for operation in ("source-init", "source-publish", "source-query"):
        source = commands.add_parser(operation, help="Operate one explicitly selected isolated local source; never run a model.")
        source.add_argument("--source-root", required=True)
        source.add_argument("--source-namespace", required=True)
        if operation == "source-publish":
            source.add_argument("--input-file", required=True, help="UTF-8 JSON with source_id, text and optional kind.")
        elif operation == "source-query":
            selection = source.add_mutually_exclusive_group()
            selection.add_argument("--source-id-file", help="UTF-8 JSON containing the exact string or integer source ID.")
            selection.add_argument("--limit", type=int, help="Maximum pending records, from 1 through 1000; default 100.")
    for operation in ("transport-bind", "transport-once", "transport-serve"):
        transport = commands.add_parser(operation, help="Bind or operate an explicitly configured local transport; no endpoint initialization or model execution.")
        transport.add_argument("--transport-config", required=True, help="Strict local JSON selecting one source, route, owner and receipt destination.")
        if operation == "transport-bind":
            transport.add_argument("--expected-epoch", type=int, required=True)
        elif operation == "transport-serve":
            transport.add_argument("--max-seconds", type=float, help="Optional finite positive serving duration.")
    for operation in ("delivery-send", "delivery-reconcile", "receipt-query"):
        delivery = commands.add_parser(operation, help="One explicit transport operation against a selected local receipt store.")
        delivery.add_argument("delivery_id")
        delivery.add_argument("--receipt-root", required=True)
        if operation == "delivery-send":
            delivery.add_argument("--sender-id", required=True)
        if operation == "delivery-reconcile":
            delivery.add_argument("--retry-absent", action="store_true")
    workbench = commands.add_parser("workbench", help="Start the independent local Web workbench (read-only by default).")
    workbench.add_argument("--port", type=int, default=8801, help="Local port; 0 selects an unused port.")
    workbench.add_argument("--enable-commands", action="store_true", help="Enable guarded commands on the explicitly selected existing deployment.")
    workbench.add_argument("--legacy-console-url", help="Explicit local legacy console origin; navigation only.")
    query = commands.add_parser("workbench-query", help="Read the same bounded projections used by the Web workbench.")
    query.add_argument("resource", choices=("deployment", "overview", "tasks", "task", "task-history", "team", "resources", "connections"))
    query.add_argument("--installation-id")
    query.add_argument("--task-id")
    query.add_argument("--generation", type=int)
    query.add_argument("--limit", type=int)
    query.add_argument("--cursor")
    query.add_argument("--state")
    query.add_argument("--role-id")
    query.add_argument("--record-generation", type=int)
    query.add_argument("--section")
    command = commands.add_parser("workbench-command", help="Apply one guarded workbench command to existing state; never start a runtime.")
    command.add_argument("--installation-id", required=True)
    command.add_argument("--action", required=True, choices=("submit", "input", "answer", "stop"))
    command.add_argument("--payload-file", required=True, help="Local UTF-8 JSON file with the exact action fields.")
    commands.add_parser("status", help="Read-only deployment status; never starts runtimes or maintenance.")
    commands.add_parser("migrate", help="Explicitly upgrade an older state database, preserving a backup.")
    commands.add_parser("runtime-check", help="Check pinned Codex binary and flags without starting a model.")
    commands.add_parser("runtime-prepare", help="Bind a dedicated runtime directory before separate Codex login.")
    execute = commands.add_parser("execute-task", help="Run a scoped task with the selected one-shot Codex deployment.")
    execute.add_argument("task_id")
    execute.add_argument("--actor-file", required=True)
    execute.add_argument("--tool", required=True)
    serve = commands.add_parser("serve", help="Serve one explicitly bound role in this foreground process; never register a role automatically.")
    serve.add_argument("--actor-file", required=True)
    serve.add_argument("--tool", required=True)
    serve.add_argument("--max-tasks", type=int, help="Optional positive task count for a bounded serving session.")
    serve.add_argument("--max-seconds", type=float, help="Optional positive serving duration.")
    members = commands.add_parser("members", help="Read member lease, reception and execution status without maintenance.")
    members.add_argument("installation_id")
    expired_members = commands.add_parser("expire-members", help="Fence expired member executions without requeuing uncertain work.")
    expired_members.add_argument("installation_id")
    stop_member = commands.add_parser("stop-member", help="Persist a member stop fence; the owning process stops its own runtime.")
    stop_member.add_argument("installation_id")
    stop_member.add_argument("member_id")
    stop_member.add_argument("--reason", required=True)
    stop = commands.add_parser("cancel-attempt", help="Ask the owning runtime supervisor to stop; never kill by stored PID.")
    stop.add_argument("installation_id")
    stop.add_argument("attempt_id")
    stop.add_argument("--reason", required=True)
    steering = commands.add_parser("steer-attempt", help="Persist a text supplement for one running attempt; this grants no tool approval.")
    steering.add_argument("installation_id")
    steering.add_argument("attempt_id")
    steering.add_argument("--text", required=True)
    steering.add_argument("--request-id", required=True)
    inputs = commands.add_parser("runtime-inputs", help="Read persisted inputs and delivery states without sending them.")
    inputs.add_argument("installation_id")
    inputs.add_argument("--attempt-id")
    check = commands.add_parser("validate-package", help="Validate a business definition without installing it.")
    check.add_argument("path")
    install = commands.add_parser("install", help="Install a versioned business snapshot with an explicit workspace.")
    install.add_argument("installation_id")
    install.add_argument("--package", required=True)
    install.add_argument("--workspace", required=True)
    install.add_argument("--role-deployments", help="Explicit JSON mapping of role IDs to workspace/access; separate from the business package.")
    for name in ("activate", "deactivate", "uninstall", "view", "results", "notifications", "history"):
        sub = commands.add_parser(name)
        sub.add_argument("installation_id")
    for name in ("register", "context"):
        sub = commands.add_parser(name)
        sub.add_argument("installation_id")
        sub.add_argument("role_id")
    submit = commands.add_parser("submit")
    submit.add_argument("installation_id")
    submit.add_argument("role_id")
    submit.add_argument("--text", required=True)
    submit.add_argument("--request-id")
    run = commands.add_parser("run", help="Run a deterministic test adapter; does not start an AI model.")
    run.add_argument("installation_id")
    run.add_argument("task_id")
    run.add_argument("--tool", required=True)
    for name in ("attempts", "expire-attempts", "expire-decisions", "decisions", "outbox"):
        sub = commands.add_parser(name)
        sub.add_argument("installation_id")
        if name == "attempts":
            sub.add_argument("--task-id")
        elif name == "decisions":
            sub.add_argument("--include-history", action="store_true")
        elif name == "outbox":
            sub.add_argument("--include-delivered", action="store_true")
    for name in ("resume-task", "reconcile"):
        sub = commands.add_parser(name)
        sub.add_argument("installation_id")
        sub.add_argument("task_id")
        sub.add_argument("--reason", required=True)
        if name == "reconcile":
            sub.add_argument("--resolution", required=True, choices=("retry", "cancel"))
            sub.add_argument("--safe-to-retry", action="store_true")
    answer = commands.add_parser("answer-decision")
    answer.add_argument("installation_id")
    answer.add_argument("decision_id")
    answer.add_argument("--revision", type=int, required=True)
    answer.add_argument("--choice", choices=("approve", "reject"), required=True)
    answer.add_argument("--answer-id", required=True)
    ack = commands.add_parser("ack-outbox")
    ack.add_argument("installation_id")
    ack.add_argument("event_id")
    for name in ("claim", "renew", "complete", "ask-decision", "request-child", "child-replies"):
        sub = commands.add_parser(name)
        sub.add_argument("--actor-file", required=True, help="Coordinator-issued registration JSON; worker scope is validated.")
        if name in ("renew", "complete"):
            sub.add_argument("attempt_id")
            sub.add_argument("--lease-file", required=True, help="JSON returned by claim, containing its lease_token.")
        else:
            sub.add_argument("task_id")
        if name in ("claim", "renew"):
            sub.add_argument("--ttl", type=float, default=60)
        if name == "claim":
            sub.add_argument("--tool", required=True)
        elif name == "complete":
            payload = sub.add_mutually_exclusive_group(required=True)
            payload.add_argument("--output")
            payload.add_argument("--error", help="Explicit failure message, not a tool approval.")
        elif name == "ask-decision":
            sub.add_argument("--title", required=True)
            sub.add_argument("--request-id", required=True)
            sub.add_argument("--expires-at", type=float)
        elif name == "request-child":
            sub.add_argument("--to-role", required=True)
            sub.add_argument("--text", required=True)
            sub.add_argument("--request-id", required=True)
    return result


def _document(path):
    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise PlatformError("invalid_document", "Duplicate fields are not accepted in capability documents.")
            value[key] = item
        return value
    try:
        with open(path, encoding="utf-8-sig") as source:
            value = json.load(source, object_pairs_hook=unique)
    except (OSError, ValueError) as exc:
        raise PlatformError("invalid_document", "Cannot read the selected UTF-8 JSON capability document.") from exc
    if not isinstance(value, dict):
        raise PlatformError("invalid_document", "A capability document must be an object.")
    return value


def _actor(path):
    data = _document(path)
    if (not all(isinstance(data.get(key), str) and data[key] for key in ("installation_id", "role_id", "token"))
            or type(data.get("generation")) is not int):
        raise PlatformError("invalid_actor", "Registration requires installation_id, role_id, generation and token.")
    return Actor(data["installation_id"], data["role_id"], data["generation"], data["token"])


def dispatch(args):
    if args.command in {"source-init", "source-publish", "source-query",
                        "transport-bind", "transport-once", "transport-serve"}:
        if args.runtime_config:
            raise PlatformError("invalid_command", "Source and transport commands do not load model runtime configuration.")
        from . import transport_commands
        if args.command in transport_commands.SOURCE_COMMANDS:
            if args.state_root:
                raise PlatformError("invalid_command", "Source operations use only --source-root, not a platform state root.")
            return transport_commands.execute_source(args.command, args.source_root, args.source_namespace,
                input_file=getattr(args, "input_file", None), source_id_file=getattr(args, "source_id_file", None),
                limit=getattr(args, "limit", None))
        if not args.state_root:
            raise PlatformError("missing_state_root", "Supply an explicit isolated platform state root.")
        return transport_commands.execute_transport(args.command, args.state_root,
            transport_commands.load_transport_config(args.transport_config),
            expected_epoch=getattr(args, "expected_epoch", None), max_seconds=getattr(args, "max_seconds", None))
    if args.command in {"routing-command", "routing-query", "receipt-init", "delivery-send", "delivery-reconcile", "receipt-query"}:
        if args.runtime_config:
            raise PlatformError("invalid_command", "Routing transport does not load model runtime configuration.")
        from . import routing_commands
        if args.command == "receipt-init":
            if args.state_root:
                raise PlatformError("invalid_command", "Receipt initialization uses only --receipt-root, not a platform state root.")
            receiver = routing_commands.ReceiptStore(args.receipt_root, args.destination_namespace,
                                                     args.destination_id, initialize=True)
            return {"initialized": True, "destination": receiver.descriptor()}
        if not args.state_root:
            raise PlatformError("missing_state_root", "Supply an explicit isolated platform state root.")
        if args.command == "routing-command":
            return routing_commands.execute_command(args.state_root, args.action,
                routing_commands.load_command_payload(args.payload_file), lease_file=args.lease_file, lease_output=args.lease_output)
        if args.command == "routing-query":
            return routing_commands.query(args.state_root, args.resource, **{
                field: getattr(args, field) for field in ("route_id", "admission_id", "installation_id", "delivery_id")})
        return routing_commands.deliver(args.state_root, args.delivery_id, args.receipt_root,
            sender_id=getattr(args, "sender_id", None), retry_absent=getattr(args, "retry_absent", False),
            operation={"delivery-send": "send", "delivery-reconcile": "reconcile", "receipt-query": "query"}[args.command])
    if args.command == "workbench-command":
        if not args.state_root:
            raise PlatformError("missing_state_root", "Supply --state-root explicitly; there is no host or business fallback.")
        if args.runtime_config:
            raise PlatformError("invalid_command", "Workbench commands do not load runtime configuration.")
        from .workbench_commands import WorkbenchCommands, load_command_payload
        payload = load_command_payload(args.payload_file)
        return WorkbenchCommands(args.state_root).execute(args.installation_id, args.action, payload)
    if args.command == "workbench-query":
        if not args.state_root:
            raise PlatformError("missing_state_root", "Supply --state-root explicitly; there is no host or business fallback.")
        if args.runtime_config:
            raise PlatformError("invalid_command", "Workbench queries do not load runtime configuration.")
        from .workbench_query import WorkbenchQueries
        queries = WorkbenchQueries(args.state_root)
        parameters = {key: getattr(args, key) for key in ("generation", "limit", "cursor", "state", "role_id", "record_generation", "section")
                      if getattr(args, key) is not None}
        allowed = {"generation", "limit", "cursor", "state", "role_id"} if args.resource == "tasks" else {"generation"}
        if args.resource == "task-history":
            allowed |= {"record_generation", "section", "limit", "cursor"}
        if args.resource == "deployment":
            if parameters or args.installation_id or args.task_id:
                raise PlatformError("invalid_query", "Deployment queries do not accept installation filters.")
            return queries.deployment()
        if not args.installation_id:
            raise PlatformError("invalid_query", "This query requires --installation-id.")
        if set(parameters) - allowed or (args.task_id is not None and args.resource not in {"task", "task-history"}):
            raise PlatformError("invalid_query", "This resource does not accept the selected filters.")
        if args.resource in {"task", "task-history"}:
            if not args.task_id:
                raise PlatformError("invalid_query", "Task detail requires --task-id.")
            method = queries.task_history if args.resource == "task-history" else queries.task
            return method(args.installation_id, args.task_id, **parameters)
        return getattr(queries, args.resource)(args.installation_id, **parameters)
    if args.command == "validate-package":
        package = load_package(args.path)
        return {"valid": True, "package_id": package.package_id, "version": package.version,
                "digest": package.digest, "roles": [role["role_id"] for role in package.roles]}
    config = load_runtime_config(args.runtime_config) if args.runtime_config else None
    if args.command in {"runtime-check", "runtime-prepare", "execute-task", "serve"} and config is None:
        raise PlatformError("missing_runtime_config", "Select an explicit deployment runtime configuration.")
    if args.command == "runtime-check":
        return probe_runtime(config)
    if config is not None and hasattr(args, "installation_id") and args.installation_id != config.installation_id:
        raise PlatformError("runtime_scope", "The runtime deployment belongs to another installation.")
    if not args.state_root:
        raise PlatformError("missing_state_root", "Supply --state-root explicitly; there is no host or business fallback.")
    if args.command == "migrate":
        return migrate_state(args.state_root)
    read_only = args.command in {"status", "view", "context", "results", "notifications", "history", "attempts", "decisions", "outbox", "child-replies", "runtime-inputs", "members"}
    platform = Platform(args.state_root, readonly=read_only,
                        external_adapters={config.adapter_id} if config else None)
    if args.command == "runtime-prepare":
        _, home = prepare_deployment(platform, config, bind=True)
        return {"prepared": True, "codex_home": str(home), "authentication": "not_checked"}
    if args.command == "status":
        return platform.status()
    if args.command == "install":
        bindings = _document(args.role_deployments) if args.role_deployments else None
        return platform.install(args.installation_id, args.package, args.workspace, role_deployments=bindings)
    if args.command in {"activate", "deactivate", "uninstall", "view", "results", "notifications", "history"}:
        return getattr(platform, args.command)(args.installation_id)
    if args.command in {"register", "context"}:
        return getattr(platform, args.command)(args.installation_id, args.role_id)
    if args.command == "submit":
        return platform.submit(args.installation_id, args.role_id, args.text, args.request_id)
    if args.command == "run":
        return platform.run(args.installation_id, args.task_id, args.tool)
    if args.command == "attempts":
        return platform.attempts(args.installation_id, args.task_id)
    if args.command in {"expire-attempts", "expire-decisions"}:
        return getattr(platform, args.command.replace("-", "_"))(args.installation_id)
    if args.command == "decisions":
        return platform.decisions(args.installation_id, args.include_history)
    if args.command == "outbox":
        return platform.outbox(args.installation_id, args.include_delivered)
    if args.command == "ack-outbox":
        return platform.ack_outbox(args.installation_id, args.event_id)
    if args.command == "resume-task":
        return platform.resume_task(args.installation_id, args.task_id, args.reason)
    if args.command == "reconcile":
        return platform.reconcile_task(args.installation_id, args.task_id, args.resolution, args.reason, args.safe_to_retry)
    if args.command == "answer-decision":
        return platform.answer_decision(args.installation_id, args.decision_id, args.revision, args.choice, args.answer_id)
    if args.command == "cancel-attempt":
        return platform.request_runtime_stop(args.installation_id, args.attempt_id, args.reason)
    if args.command == "steer-attempt":
        return platform.submit_runtime_input(args.installation_id, args.attempt_id, args.text, args.request_id)
    if args.command == "runtime-inputs":
        return platform.runtime_inputs(args.installation_id, args.attempt_id)
    if args.command == "members":
        return platform.members(args.installation_id)
    if args.command == "expire-members":
        return platform.expire_members(args.installation_id)
    if args.command == "stop-member":
        return platform.request_member_stop(args.installation_id, args.member_id, args.reason)
    actor = _actor(args.actor_file)
    if args.command == "serve":
        from .worker import MemberWorker
        return MemberWorker(platform, config, actor, args.tool).run(
            max_tasks=args.max_tasks, max_seconds=args.max_seconds)
    if args.command == "execute-task":
        return CodexRunner(platform, config).run(actor, args.task_id, args.tool)
    if args.command == "claim":
        return platform.claim(actor, args.task_id, args.tool, args.ttl)
    if args.command in {"renew", "complete"}:
        lease = _document(args.lease_file)
        token = lease.get("lease_token")
        if lease.get("attempt_id") != args.attempt_id or not isinstance(token, str) or not token:
            raise PlatformError("invalid_lease", "The lease document must match the requested attempt.")
        if args.command == "renew":
            return platform.renew(actor, args.attempt_id, token, args.ttl)
        error = {"code": "worker_failed", "message": args.error} if args.error is not None else None
        return platform.complete(actor, args.attempt_id, token, args.output, error)
    if args.command == "ask-decision":
        return platform.ask_decision(actor, args.task_id, args.title, args.request_id, args.expires_at)
    if args.command == "request-child":
        return platform.request_child(actor, args.task_id, args.to_role, args.text, args.request_id)
    if args.command == "child-replies":
        return platform.child_replies(actor, args.task_id)
    raise PlatformError("invalid_command", "Unknown command.")


def main(argv=None) -> int:
    try:
        args = parser().parse_args(argv)
        if args.command == "workbench":
            if not args.state_root:
                raise PlatformError("missing_state_root", "Supply --state-root explicitly; there is no host or business fallback.")
            if args.runtime_config:
                raise PlatformError("invalid_command", "The workbench does not load runtime configuration or start executors.")
            from .workbench import main as workbench_main
            forwarded = ["--state-root", args.state_root, "--port", str(args.port)]
            if args.enable_commands:
                forwarded.append("--enable-commands")
            if args.legacy_console_url is not None:
                forwarded.extend(["--legacy-console-url", args.legacy_console_url])
            return workbench_main(forwarded)
        result = dispatch(args)
        print(json.dumps(result, ensure_ascii=True, sort_keys=True))
        return 0
    except (ContractError, PlatformError, PathSafetyError) as exc:
        print(json.dumps({"error": {"code": exc.code, "message": str(exc)}}, ensure_ascii=True), file=sys.stderr)
        return 2
    except (OSError, sqlite3.Error) as exc:
        print(json.dumps({"error": {"code": "storage_error", "message": str(exc)}}, ensure_ascii=True), file=sys.stderr)
        return 2
