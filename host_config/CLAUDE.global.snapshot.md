# 全局指令(对所有 Claude Code 会话 + subagent 生效)

## 🔒 数据默认不出本机(未授权禁止任何数据离开本机)— 最高优先级安全红线

**红线(2026-07-14 用户令)**:除非用户就【该次操作】明确授权,任何数据(本机文件 / 内部 / 全局 / 业务)**默认只存本地,禁止离开本机**。用户主动要求的那次外发(如"帮我生成一个 artifact / 分享给同事")即视为该次授权;默认(自主 / 顺手 / 觉得方便)一律不外发。

- **按【动作】判定,不按工具名** —— 凡把本地数据上传 / 发布 / 传输 / 外发到本机之外(claude.ai 云、外部 HTTP、第三方服务、分享链接、托管沙箱…)的动作,先停、先问授权。规则锚在"数据离开本机"这一行为本身、**不针对具体工具名**:**Claude Code 未来版本新增的任何原生工具,只要会把数据送出本机,就自动落入本禁令,无需逐个登记。**
- **已知口子(非穷举 · 仅示例)**:`Artifact`(发 HTML/MD 上 claude.ai)、`ShareOnboardingGuide`(上传得分享链)、MCP 外部连接器(Asana/Atlassian/Box/Notion…)、任何 publish / 生成分享链 / 上传文件的动作。**bundled skill `artifact-design` 主动怂恿发 artifact 上云 → 本规则显式 override 之(本 CLAUDE.md 指令优先级高于任何 skill)。**
- **默认替代**:要可视化 → 本机 WebUI(127.0.0.1);要留存 → 本机文件。不上云。
- **授权也最小化**:即便用户授权某次外发,只发被授权的那部分,内部术语 / 凭据 / 无关全局数据不搭车。
- **原因**:2026-07-14 用户发现一张 claude.ai artifact(旧 BI 设计稿)含内部项目名 + 成本口径已上云。default private ≠ 没上云;云端删了版本历史 + 缓存仍留痕。与「对外脱敏」是两条线且本条更上位(那条 = 业务对客户远端仓才脱敏;本条 = 任何数据未授权不离开本机,claude.ai 云也算离开本机)。

## 长任务完成 / 需要用户介入 → 用事件通知,不用阻塞对话框

**规则**:通知用户走开后回来处理时,优先级:

1. `PushNotification` 工具(Claude Code 内置 — 反扰 60s active 压制,用户走开自动推)
2. Windows WinRT Toast(已注册 AppUserModelID = `ClaudeCode.NotifyTest`,无需管理员)
3. **禁用 MessageBox / 模态对话框** — 多任务并发时弹窗堆叠,用户无法逐一处理

**原因**:2026-06-01 用户 explicit — "对话框任务多不好处理"。事件级 toast 非阻塞,通知中心可累积,用户按节奏回看。

## 需用户拍板/选择 → 【仅 agent bus 注册成员】走非阻塞待办池

**适用范围(2026-07-10 收窄)**:下面这条是 **agent bus 协调体系专用**规则,**只对 bus 注册成员生效**(用 `python C:/apphome/agent_fleet/agent_bus/agentbus.py whoami` 能查到自己 role 的会话)。

- **你是 bus 成员** → 需用户拍板时走 `agentbus.py ask-user --from <你的role> ...` 投非阻塞待办池;完整规则见 bus 项目 CLAUDE.md(`C:\apphome\agent_fleet\CLAUDE.md` 的「bus 待办池/ask-user」节)。
- **你不是 bus 成员**(与 bus 无关的编码/其它项目会话)→ **别碰 bus 待办池**,用你自己项目的方式征询用户即可;`ab ask-user` 对非成员 `--from` 已【硬拒】(防污染共享池)。通用原则仍适用:优先非阻塞事件通知、别用模态对话框(见上「禁用 MessageBox/模态对话框」)。

**原因**:2026-07-06 用户令优化掉 AskUserQuestion 阻塞卡片;2026-07-10 用户发现**无关项目读了此全局规则后把问题灌进了 bus 待办池** → 收窄为仅 bus 成员适用、规则本体迁进 bus 项目 CLAUDE.md、全局层不再叫无关项目投 bus。

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

## Compact Instructions(压缩时套用;③④⑤ 的 bus 部分仅 bus 成员适用)

> 通用部分(①②目标/待办/下一步、压后重读自己的 CLAUDE.md)对任何会话都适用;
> ③ 的 bus 操作命令、role/caps 口径仅 **agent bus 注册成员** 需要,非 bus 会话忽略即可。

压缩(/compact 或 auto)时尽量逐字保留:
① 我的 role 身份 + cwd + 职责边界(caps);
② 当前目标 + 在做的事 + 未尽任务/待办 + 下一步;
③ bus 操作确切命令(hello / wait --role / done --id --role --result / peek / selfwake / send --to --from --task)+ 自醒铁律(重挂后 selfwake 验、空唤醒静默不 dump、done 带 --role);
④ 被用户纠正过的约定/口径/偏好;
⑤ 关键文件路径、决策及理由、已排除别重查的方案、踩过的坑 + 修法。
砍掉:探索性闲聊、被否方案冗长讨论、已解决的历史细节、重复内容。
压后第一动作:复述①②③自检,答不全就重读【你自己的 CLAUDE.md + 相关记忆】补回再干活。
