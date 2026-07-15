# agent bus — 多个 Claude Code tab 之间分派任务

无服务端、纯文件 + watchdog 事件(不 sleep polling)。**一个 role 一个 tab**。
任意 tab 都能 `send` 派活给别的 role;worker tab 用 `/loop` 守在 `wait` 上,
有任务就自动拉取、执行、`done` 回填结果。可在各自 tab 里全程旁观(子代理做不到)。

目录:`~/.claude/agent_bus/<role>/{inbox,processing,archive,done}/`

## 命令(任一 tab)

调用(BUS 2.0 一律用短入口 `ab`):
- `ab <cmd> ...`（`ab`/`ab.cmd` 启动器已固化正确 env python + 真实项目根路径 + UTF-8;`agent_bus` 目录已在用户 PATH）
- 回退（新 shell PATH 未刷到时）：`"C:/path/to/python.exe" "C:/apphome/agent_fleet/agent_bus/agentbus.py" <cmd> ...`

```
ab register --role SQLAGent                                  # 注册 role(建目录)
ab send --to SQLAGent --from Master --title "..." --task "..."  # 派任务,打印 task id
ab wait --role SQLAGent [--timeout 110]                      # 阻塞到有任务(事件触发),领取并打印 JSON
ab done --id <id> --role SQLAGent --result "..." [--status ok|error]  # 回填结果给发起方
ab peek --role SQLAGent                                      # 看该 role 待办(不领取)
ab replies --role Master [--clear]                            # 读回填给我的结果
ab list                                                      # 所有 role 队列深度
ab hello --role Master                                        # 本 tab 自报身份(绑定本 tab 的 claude 进程)
ab who [--max-age 900]                                       # 谁在跑+是谁:presence 心跳 vs 实际 claude 进程
```

## 身份识别(关键)

OS 里所有 `claude.exe` 进程**长得一模一样**(同名、同命令行),系统能数出"有几个在跑",但**无法知道每个是什么 role**。身份只能**会话自报**:

- `ab hello --role X`:在某个 tab 里跑一次,它向上回溯父进程链找到**本 tab 的 claude 进程**,把 `role↔pid↔cwd` 写进 `<role>/presence.json`。
- worker tab 起了 `/loop` 后,`wait` 每轮**自动写心跳**=自动声明身份,无需手动 hello。
- **只有纯调度 tab(Master)需要手动 `hello` 一次。**
- `ab who`:把心跳和实际在跑的 claude 进程交叉核对,判定 `✓活的worker / 活着没在听 / 进程已退残留 / 未声明`,并列出在跑但没声明身份的进程。

> 铁律:一个会话只能**声明**自己是谁,它无法知道你在 PyCharm 给它的 tab 起了什么名。
> 在每个 tab 里各自跑一次 `ab hello --role <这个tab的名字>`(worker 靠 /loop 自动声明);在错的 tab 里 hello 就会贴错身份。

`--from` 缺省取环境变量 `AGENTBUS_ROLE`,再缺省 `unknown`。
建议每个 worker tab 启动时先 `set AGENTBUS_ROLE=<本tab的role>`(或 PowerShell `$env:AGENTBUS_ROLE="..."`)。

## worker tab 的启动方式(自动执行模式)

在该 worker tab(例:SQLAGent)里输入 `/loop`,prompt 用下面这段(把两处 role 换成本 tab 的):

```
你是 agent bus 上的 worker,role=SQLAGent。每轮只做一件事:
1) 用 Bash(超时设 600000ms)运行:
   ab wait --role SQLAGent --timeout 590
2) 它会阻塞到有人派任务,然后打印一段 JSON(含 id / from / task)。
   - 输出是 {"idle":true} → 本轮什么都不做,直接结束(loop 自动再起一轮继续等)。
   - 是任务 → 严格按 task 字段执行(用本 tab 的工具/sql_agent 等);完成后运行:
     ab done --id <那个id> --role SQLAGent --result "<简洁结果或产物路径>" --status ok
     失败则 --status error,把错误写进 --result。
3) 不要做任务以外的事。
```

> 责任边界:worker 会自动执行收到的任务。高风险动作(写库/外发)请派给"先审后做"的 tab,
> 或在 task 文本里要求 worker 先 EXPLAIN/确认再执行。SQL 写库仍遵循 sql_agent 审批协议。

## 派活示例(Master tab)

```
ab send --to SQLAGent --from Master --title "问题目标1" \
  --task "问题说明"
ab replies --role Master        # 过会儿看 SQLAGent 回填的结果
```

## 内部结构(BUS 2.0 一体化包)

`agentbus.py` 现为**薄 CLI 分发器**(~170 行:re-export 公共面 + `_CMD_GROUPS` 分组 + `main` 解析);
全部逻辑在 `bus/` 包(内部模块化、对外一体·`ab.*` 公共面经 re-export 不变)。依赖分层(叶→上·无环):

- **叶层**:`config`(路径/时间)· `io`(原子落地/id/审计)· `roster`(agents.json/角色分类)· `proc`(进程扫描/psutil)
- **中层**:`presence`(心跳/身份绑定)· `selfwake`(事件自醒/park)· `notify`(toast/notice)· `mailbox`(原子领取原语)
- **命令层**:`core`(send/wait/done/serve/request/peek/replies)· `pool`(工作组/fan-out)· `sessions`(会话元数据)·
  `tasks`(tasklines+ask-user+answer)· `edge`(CDP 互斥)· `roles`(retire/revive/route/roster/caps)· `exec`(dispatch)·
  `health`(reap/prune/health/supervise/webui-guard/stale)· `status`(_who_data/who/list)· `identity`(hello/whoami/selfwake/register/tag)

外部 importer(dispatch_agent / longrange_net / pool_dispatcher / worker_daemon / ui.server)`import agentbus as ab`
用 `ab.BUS`/`ab._now`/`ab.cmd_*` 等,名字经 re-export 全部不变。
