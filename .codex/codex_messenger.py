"""Detached Codex messenger entry point; keep diagnostics on this machine."""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--role", required=True)
    parser.add_argument("--session-id", required=True)
    args, _ = parser.parse_known_args()
    role_dir = ROOT / "agent_bus" / args.role
    role_dir.mkdir(parents=True, exist_ok=True)
    log_path = role_dir / "messenger.log"
    if log_path.exists() and log_path.stat().st_size > 1_000_000:
        log_path.replace(role_dir / "messenger.previous.log")
    with log_path.open("a", encoding="utf-8", buffering=1) as log:
        sys.stdout = sys.stderr = log
        print(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} messenger pid={os.getpid()} started")
        try:
            from codex_wait_bridge import main as bridge_main
            bridge_main()
        except BaseException as exc:
            print(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} messenger stopped: {exc!r}")
            # The bridge writes its own heartbeat while running. A terminal
            # marker makes launch failures visible even before that first beat.
            path = role_dir / "messenger.state.json"
            try:
                state = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
                if state.get("pid") in (None, os.getpid()):
                    state.update({"role": args.role, "session_id": args.session_id,
                                  "pid": os.getpid(), "status": "stopped",
                                  "heartbeat": time.strftime("%Y-%m-%dT%H:%M:%S"),
                                  "error": str(exc)[:500]})
                    tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
                    tmp.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
                    os.replace(tmp, path)
            except OSError:
                pass
            raise


if __name__ == "__main__":
    main()
