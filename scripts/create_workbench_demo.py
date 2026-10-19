"""Create a new, explicitly selected synthetic workbench deployment.

This helper never launches a model or reads an existing deployment. It refuses
an existing destination. All roles, tasks, receipts and workspaces are samples.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

SOURCE_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SOURCE_ROOT))

from masteragent.paths import prepare_state_root
from masteragent.service import Platform


def create_demo(directory: Path) -> dict:
    root = prepare_state_root(directory, create=False)
    if root.exists():
        raise ValueError("Choose a new directory; existing destinations are never replaced.")
    root.mkdir(parents=True, exist_ok=False)
    platform = Platform(root / "state", external_adapters={"demo-runtime"})
    for suffix, label in (("a", "样例资料整理"), ("b", "样例发布核对")):
        package = {
            "schema_version": 1, "platform_api": 1,
            "package_id": f"workbench-demo-{suffix}", "version": "1.0.0",
            "roles": [
                {"role_id": "reviewer", "display_name": "核对员", "tools": ["echo", "preview"],
                 "instructions": f"仅核对{label}的合成材料，不代表真实业务或模型执行。"},
                {"role_id": "editor", "display_name": "整理员", "tools": ["echo"],
                 "instructions": f"整理{label}的虚构文本。"},
            ],
            "knowledge": {"sample-scope": f"{label}：全部是本机生成的演示数据；没有调用 Codex 或 Claude。"},
            "tools": {"echo": {"adapter": "echo", "description": "本机确定性回显，用于演示成果记录。"},
                      "preview": {"adapter": "demo-runtime", "description": "仅记录合成执行归属，不启动执行器。"}},
            "workflows": {"review": {"roles": ["editor", "reviewer"], "description": "整理样例材料后核对。"}},
        }
        package_path = root / f"business-{suffix}.json"
        package_path.write_text(json.dumps(package, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        workspace = root / f"workspace-{suffix}"
        workspace.mkdir()
        platform.install(f"demo-{suffix}", package_path, workspace)

    # A -> B -> A uses the actual public lifecycle and retains earlier results.
    platform.activate("demo-a")
    old = platform.submit("demo-a", "editor", "第一代样例资料：该成果仅在第 1 代查询中显示。")
    platform.run("demo-a", old["task_id"], "echo")
    platform.deactivate("demo-a")
    platform.activate("demo-b")
    beta = platform.submit("demo-b", "reviewer", "业务 B 的独立核对结果，不属于业务 A。")
    platform.run("demo-b", beta["task_id"], "echo")
    platform.submit("demo-b", "editor", "业务 B 的待办样例。")
    platform.deactivate("demo-b")
    platform.activate("demo-a")

    done = platform.submit("demo-a", "editor", "整理了 3 份合成资料，已完成字段和来源核对。")
    platform.run("demo-a", done["task_id"], "echo")
    reviewer = platform.bind("demo-a", "reviewer")
    decision_task = platform.submit("demo-a", "reviewer", "核对样例资料的交付范围。")
    platform.ask_decision(reviewer, decision_task["task_id"], "是否将样例附录纳入本次交付？（演示）", "demo-question")
    failed = platform.submit("demo-a", "reviewer", "演示失败记录：合成资料缺少来源。")
    lease = platform.claim(reviewer, failed["task_id"], "preview")
    platform.complete(reviewer, lease["attempt_id"], lease["lease_token"],
                      error={"code": "demo_missing_source", "message": "合成失败样例；没有真实执行。"})
    for number in range(1, 26):
        platform.submit("demo-a", "editor", f"待整理的样例条目 {number:02d}。仅用于分页和详情展示。")
    running = platform.submit("demo-a", "reviewer", "合成进行中记录：核对资料目录及交付清单。没有启动原生模型。")
    lease = platform.claim(reviewer, running["task_id"], "preview", ttl_seconds=3600)
    platform.record_runtime_event(reviewer, lease["attempt_id"], lease["lease_token"],
                                  "runtime_start_requested", {"runtime": "demo-runtime", "supports_runtime_inputs": True})
    platform.submit_runtime_input("demo-a", lease["attempt_id"], "请保留每条样例资料的来源说明。（尚未发送的演示补充）", "demo-input")
    return {"synthetic_only": True, "models_started": 0, "state_root": str(platform.state_root),
            "installations": ["demo-a", "demo-b"], "active_installation_id": "demo-a",
            "active_generation": 2, "detail_task_id": running["task_id"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", required=True, type=Path)
    args = parser.parse_args()
    try:
        result = create_demo(args.directory)
    except (ValueError, OSError) as exc:
        parser.exit(2, f"Demo creation failed: {exc}\n")
    print(json.dumps(result, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
