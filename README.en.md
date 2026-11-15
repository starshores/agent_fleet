# agent_fleet — Agent Bus Infrastructure

[简体中文](README.md) · English

`agent_fleet` is a file-based coordination infrastructure for multiple Claude Code CLI sessions. Each session acts as a *role*; roles dispatch tasks to each other through a shared file bus. It runs without a server: coordination uses plain files plus watchdog events (no polling). One role uses one tab, wakes on events, and can observe the others.

## Branches and versions

| Branch | Purpose | Status |
| --- | --- | --- |
| `main` | Primary version for Claude Code (2.x series) | Stable and suitable for daily use; latest tag `v2.16` |
| `codex/masteragent-3-boundary` | Codex preview (MasterAgent 3.x) | Preview; features are incomplete, APIs and behavior may change, and it is not recommended for production or critical workflows |

The Chinese `README.md` on each branch is the primary entry point and contains branch-specific status. Detailed documentation is currently written in Chinese.

## What is included

- `agent_bus/` — the bus itself: CLI dispatcher, role registry, mailboxes, presence, self-wake, worker daemon, dispatch, and runtime support.
- `ui/` — local coordination WebUI (`127.0.0.1:8799`).
- `hooks/` — Claude Code lifecycle hooks: identity discovery, non-blocking questions, compact re-injection, and turn observation.
- `docs/` — design, review, handoff, and roadmap documents.
- `host_config/` — host-level configuration snapshots (`CLAUDE.md`, settings).
- `githooks/` — release snapshot hook.
- `fork_exchange/` — exchange area for evolving forks of the bus.
- `recover.py` — liveness self-check: active roles, inbox, Edge lock, and fleet expectations.
- `bus_smoke.py` — regression smoke test for the command surface.

## Quick start

```bash
python recover.py                    # liveness self-check
ab whoami                            # show the role bound to this session
ab commands                          # list command groups
python bus_smoke.py                  # regression smoke test
powershell -File ui/start_ui.ps1     # start the local WebUI
```

Requirements: Python 3 with `watchdog` and `psutil`, and the Claude Code CLI. See `CLAUDE.md` for recovery, migration, and host-setup details.

## Documentation

- `CLAUDE.md` — session recovery entry point and role discipline.
- `AGENTS.md` — bus operations manual: commands, execution model, and boundaries.
- `CAPABILITIES.md` — external channels that have been wired up.
- `docs/DIRECTORY_MAP.md` — file-by-file directory map.

## License

Released under the [MIT License](LICENSE).
