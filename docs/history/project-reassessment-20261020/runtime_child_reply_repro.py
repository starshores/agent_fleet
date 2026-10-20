"""Reproduce parent-context parity using real Platform state and a fake native runner.

No native process, model call, credential, production state, or bus is accessed.
Run with Python -B. The output is JSON; --output refuses to replace evidence.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_case(root, mode):
    from masteragent.runtime import CodexRunner
    from masteragent.runtime_config import RuntimeConfig
    from masteragent.service import Platform

    root.mkdir()
    workspace, home = root / "workspace", root / "runtime-home"
    workspace.mkdir()
    home.mkdir()
    package = root / "synthetic-package.json"
    package.write_text(json.dumps({
        "schema_version": 1, "platform_api": 1,
        "package_id": "synthetic_context_parity", "version": "1.0.0",
        "roles": [
            {"role_id": "parent", "display_name": "Parent",
             "instructions": "Review the explicit child result.", "tools": ["respond"]},
            {"role_id": "child", "display_name": "Child",
             "instructions": "Echo synthetic text.", "tools": ["echo"]},
        ],
        "knowledge": {},
        "tools": {
            "respond": {"adapter": "agent-text", "description": "Synthetic text response"},
            "echo": {"adapter": "echo", "description": "Local synthetic echo"},
        },
        "workflows": {},
    }, ensure_ascii=False), encoding="utf-8")
    platform = Platform(root / "state", external_adapters={"agent-text"})
    platform.install("synthetic", package, workspace)
    platform.activate("synthetic")
    parent = platform.bind("synthetic", "parent")
    child = platform.bind("synthetic", "child")
    task = platform.submit("synthetic", "parent", "Use the completed child result.")
    request = platform.request_child(parent, task["task_id"], "child",
                                     "SYNTHETIC_CHILD_REPLY_20261020", "request-1")
    platform.run_as(child, request["child_task_id"], "echo")
    replies_before = platform.child_replies(parent, task["task_id"])
    assert len(replies_before) == 1
    assert replies_before[0]["child_state"] == "completed"
    assert platform.task_for(parent, task["task_id"])["state"] == "queued"
    config = RuntimeConfig(
        1, "codex-app-server", Path(sys.executable).resolve(),
        "synthetic-version-no-native-probe", "synthetic-model-no-model-call",
        "agent-text", "synthetic", "parent", 1, home, 20, 30,
    )
    prompts = []

    def supervisor(argv, prompt, cwd, env, timeout_seconds, heartbeat, on_event,
                   **kwargs):
        prompts.append(json.loads(prompt.split("\n", 1)[1]))
        heartbeat()
        on_event("runtime_thread", {"thread_id": "synthetic-thread"})
        return {
            "outcome": "succeeded", "output": "SYNTHETIC_PARENT_RESULT",
            "thread_id": "synthetic-thread", "turn_id": "synthetic-turn",
            "usage": None, "returncode": 0,
            "termination_confirmed": True, "controlled_shutdown": True,
        }

    runner = CodexRunner(platform, config, supervisor=supervisor,
                         probe=lambda selected: {"compatible": True, "synthetic": True})
    if mode == "direct_run":
        outcome = runner.run(parent, task["task_id"], "respond")
    else:
        deployment_digest = hashlib.sha256(json.dumps(
            {"runtime": config.as_public_dict(), "tool_id": "respond"},
            sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")).hexdigest()
        member = platform.start_member(parent, deployment_digest, "respond", 30)
        issued = platform.claim_member_task(
            parent, member["member_id"], member["member_token"], 30,
        )
        assert issued["task_id"] == task["task_id"]
        outcome = runner.run_claimed(parent, issued, heartbeat_extra=lambda:
            platform.renew_member(parent, member["member_id"], member["member_token"], 30))
        platform.finish_member(parent, member["member_id"], member["member_token"],
                               outcome["termination_confirmed"])
    assert len(prompts) == 1
    prompt = prompts[0]
    return {
        "mode": mode,
        "durable_completed_child_replies_before_execution": len(replies_before),
        "durable_child_output": replies_before[0]["result"]["output"],
        "parent_state_before_execution": "queued",
        "prompt_has_completed_requests": "completed_requests" in prompt,
        "prompt_completed_requests": prompt.get("completed_requests"),
        "parent_task_state_after_execution": outcome["task_state"],
        "parent_attempt_count": len(platform.attempts("synthetic", task["task_id"])),
        "native_supervisor": "in-process synthetic function; no subprocess",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path,
                        default=Path(__file__).resolve().parents[3])
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    project = args.project_root.resolve(strict=True)
    if not (project / "masteragent" / "runtime.py").is_file():
        parser.error("project-root must identify this MasterAgent source checkout")
    if args.output is not None and args.output.exists():
        parser.error("--output already exists; use another filename to preserve evidence")
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(project))
    recorded_sources = [
        "masteragent/runtime.py", "masteragent/runtime_config.py",
        "masteragent/worker.py", "masteragent/members.py",
        "masteragent/coordination.py", "masteragent/cli.py",
    ]
    temp_parent = Path(tempfile.gettempdir()).resolve(strict=True)
    temporary = tempfile.TemporaryDirectory(prefix="fleet-reassessment-runtime-", dir=temp_parent)
    root = Path(temporary.name).resolve(strict=True)
    if (root.parent != temp_parent or not root.name.startswith("fleet-reassessment-runtime-")
            or root == project or project in root.parents or root in project.parents):
        raise RuntimeError("Refusing temporary root outside the intended disposable boundary")
    root_identity = root.stat().st_dev, root.stat().st_ino
    evidence = {
        "schema_version": 1,
        "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
        "scope": "Real Platform SQLite, child completion, member claim, and runtime prompt construction; native probe and execution are synthetic.",
        "not_verified": [
            "No real Codex or Claude model execution.",
            "No persistent MemberWorker event loop or live fleet acceptance.",
            "Synthetic supervisor success does not prove model semantic adoption.",
        ],
        "source_sha256": {path: digest(project / path) for path in recorded_sources},
        "script_sha256": digest(Path(__file__).resolve()),
    }
    try:
        with ExitStack() as stack:
            spawn = stack.enter_context(patch("subprocess.Popen",
                                             side_effect=AssertionError("native subprocess forbidden")))
            probe = stack.enter_context(patch("masteragent.runtime.inspect_command",
                                             side_effect=AssertionError("native probe forbidden")))
            evidence["cases"] = [run_case(root / mode, mode)
                                 for mode in ("direct_run", "member_claimed_run")]
            evidence["native_subprocess_attempts"] = spawn.call_count
            evidence["native_probe_attempts"] = probe.call_count
        direct, claimed = evidence["cases"]
        evidence["difference_reproduced"] = (
            not direct["prompt_has_completed_requests"]
            and claimed["prompt_has_completed_requests"]
            and all(case["durable_completed_child_replies_before_execution"] == 1
                    and case["parent_task_state_after_execution"] == "completed"
                    and case["parent_attempt_count"] == 1 for case in evidence["cases"])
        )
    finally:
        # Remove only this freshly created root after rechecking its exact identity.
        current = Path(temporary.name).resolve(strict=True)
        if (current != root or current.parent != temp_parent
                or (current.stat().st_dev, current.stat().st_ino) != root_identity):
            raise RuntimeError("Temporary root identity changed; automatic cleanup refused")
        temporary.cleanup()
        evidence["temporary_root_removed"] = not root.exists()
    text = json.dumps(evidence, ensure_ascii=False, indent=2) + "\n"
    if args.output is not None:
        output = args.output.resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
    print(text, end="")


if __name__ == "__main__":
    main()
