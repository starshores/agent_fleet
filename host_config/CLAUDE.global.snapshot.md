# 全局指令(对所有 Claude Code 会话 + subagent 生效)

## 长任务完成 / 需要用户介入 → 用事件通知,不用阻塞对话框

**规则**:通知用户走开后回来处理时,优先级:

1. `PushNotification` 工具(Claude Code 内置 — 反扰 60s active 压制,用户走开自动推)
2. Windows WinRT Toast(已注册 AppUserModelID = `ClaudeCode.NotifyTest`,无需管理员)
3. **禁用 MessageBox / 模态对话框** — 多任务并发时弹窗堆叠,用户无法逐一处理

**原因**:2026-06-01 用户 explicit — "对话框任务多不好处理"。事件级 toast 非阻塞,通知中心可累积,用户按节奏回看。

## 需用户拍板/选择 → 走非阻塞待办池,禁用 AskUserQuestion 卡片

**规则**:任何"要用户拍板/二选一/确认"的场景,**一律**跑
`python C:/apphome/agent_fleet/agent_bus/agentbus.py ask-user --from <你的role> --title "<问题>" --detail "<背景+选项如 A=… / B=…>"`
把决策投进**非阻塞待办池**(todos.json / WebUI),投完**继续手头的活、别等**;用户按自己节奏批量看、回填,你下轮再看结果。

- **禁用 `AskUserQuestion` 交互卡片** —— 它是【阻塞式】:问了若用户没及时点,发问 agent 的会话就被**占住干等**。用户一般没时间实时看 cc 询问。
- **已机制层硬拦**:PreToolUse hook `C:/apphome/agent_fleet/hooks/block_askuser.py`(settings.json 注册·matcher=AskUserQuestion)会拦下任何 AskUserQuestion → 自动把问题转投待办池 + deny,并提示"已代投、继续别等"。**所以就算你忘了这条约定,卡片也会被拦并转投**,别依赖硬拦、优先自己走 ask-user。
- 这是 [[ask-user-standard]] 记忆 + 上面「禁用模态对话框」的合并强化。

**原因**:2026-07-06 用户 explicit — "AskUserQuestion 会占用会话,要想办法优化掉"(展示了 agent 用卡片阻塞会话的实例)。

## 等本地长任务 / 文件变化 → 事件机制,不要 sleep polling

- 文件变化用 `watchdog.observers.Observer` 或 `pywin32.win32file.ReadDirectoryChangesW`
- 子进程退出用 `subprocess.Popen.wait()` / `WaitForSingleObject`
- 远端长任务用 wait long-poll endpoint(sql_agent / Airflow 都支持)
- 不要写 `while true; do sleep N; done` — 浪费 token + idle 开销

详细方案见 `~/.claude/projects/XXX/memory/windows_event_notification.md`。

## Windows 管道/文件编码 → 显式 UTF-8,别信默认(GBK 坑·高频)

Windows 默认 codepage = GBK/cp936,且【嵌太深无法消除】,只能【同时兼容 UTF-8 与 GBK = 数据 I/O 一律显式 UTF-8】。Python 的 `open()`/`sys.stdin` 默认按 GBK 解码 → 读 UTF-8 中文就乱码 / `UnicodeDecodeError: 'gbk' codec can't decode` / 中文 dict key 匹配不上(`d.get('中文')` 莫名返 None)。规则:

- **读文件**:`open(path, encoding='utf-8')` —— 别裸 `open(path)`。
- **读管道 / stdin**(如 `git show … | python -c "…"`、`cmd | python`):`sys.stdin.buffer.read().decode('utf-8')` —— 别 `json.load(sys.stdin)` / `sys.stdin.read()`(它们走 GBK)。
- **写文件**(供他处再读):`open(p,'w',encoding='utf-8')`;PowerShell 用 `Out-File/Set-Content -Encoding utf8`。
- **控制台显示 ≠ 数据**:GBK 终端会把 UTF-8 中文【显示成乱码,即使数据本身完好】——别据控制台乱码判定"数据坏了/读到 None";要核实就 `.encode('unicode_escape').decode()` 或 ascii-safe 输出再看。
- **症状速认**:`'gbk' codec can't decode` / 中文 key `.get()` 意外 None / 中文输出乱码 → 先怀疑没显式 UTF-8,而非数据真错。

**原因**:2026-07-02 用户 explicit —— 这个坑经常碰到,Win 对 GBK 支持太深入没法消除,必须两者兼容(实操=显式 UTF-8)。

## WinRT Toast 模板

```powershell
$AppID = "ClaudeCode.NotifyTest"
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType=WindowsRuntime] | Out-Null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType=WindowsRuntime] | Out-Null
$xml = New-Object Windows.Data.Xml.Dom.XmlDocument
$xml.LoadXml('<toast><visual><binding template="ToastGeneric"><text>标题</text><text>正文</text></binding></visual></toast>')
$toast = [Windows.UI.Notifications.ToastNotification]::new($xml)
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($AppID).Show($toast)
```

AppID 已通过 HKCU:\SOFTWARE\Classes\AppUserModelId\ClaudeCode.NotifyTest 注册(DisplayName=Claude Code, ShowInSettings=1)。

## Compact Instructions(bus 通用·压缩时套用)

压缩(/compact 或 auto)时尽量逐字保留:
① 我的 role 身份 + cwd + 职责边界(caps);
② 当前目标 + 在做的事 + 未尽任务/待办 + 下一步;
③ bus 操作确切命令(hello / wait --role / done --id --role --result / peek / selfwake / send --to --from --task)+ 自醒铁律(重挂后 selfwake 验、空唤醒静默不 dump、done 带 --role);
④ 被用户纠正过的约定/口径/偏好;
⑤ 关键文件路径、决策及理由、已排除别重查的方案、踩过的坑 + 修法。
砍掉:探索性闲聊、被否方案冗长讨论、已解决的历史细节、重复内容。
压后第一动作:复述①②③自检,答不全就重读【你自己的 CLAUDE.md + 相关记忆】补回再干活。
