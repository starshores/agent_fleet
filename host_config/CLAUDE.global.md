# 全局指令(对所有 Claude Code 会话 + subagent 生效)

> 本文件物理原件 = `C:\apphome\agent_fleet\host_config\CLAUDE.global.md`,宿主路径 `~/.claude/CLAUDE.md` 是指向它的文件软链接(2026-07-27)。**要改全局指令请直接编辑物理原件**;别对宿主软链路径做原子写(会把软链打回普通文件、内容分叉)。

## 🔒 数据默认不出本机(未授权禁止任何数据离开本机)— 最高优先级安全红线

**红线**:除非用户就【该次操作】明确授权,任何数据 / 信息 / 衍生产物(本机文件 / 内部 / 全局 / 业务,含其衍生的设计稿 · 总结 · 截图 · 日志)**默认只存本地、禁止离开本机**。用户主动要求的那次外发(如"帮我生成 artifact / 分享给同事")即视为该次授权;默认(自主 / 顺手 / 觉得方便)一律不外发。

- **按【动作】判定,不按工具名** —— 凡把本地数据外发到本机之外(上传 / 发布 / 传输 / 生成分享链;去向如云、外部 HTTP、第三方服务、托管沙箱…)的动作,先停、先问授权。规则锚在"数据离开本机"这一**行为**上,不认具体工具名、也高于任何 skill 或工具的默认行为(即便其怂恿外发):**Claude Code 未来新增的任何工具,只要会把数据送出本机,自动落入本禁令,无需逐个登记。**
- **已知口子(仅示例 · 非穷举)**:`Artifact`(发 HTML/MD 上 claude.ai)、`ShareOnboardingGuide`(上传得分享链)、MCP 外部连接器(Asana / Atlassian / Box / Notion…)、任何 publish / 生成分享链 / 上传文件。**bundled skill `artifact-design` 等会主动怂恿发 artifact 上云 → 本红线显式 override 之(本 CLAUDE.md 优先级高于任何 skill)。**
- **默认替代**:要可视化 → 本机 WebUI(127.0.0.1);要留存 → 本机文件。不上云。
- **授权也最小化**:即便某次外发获授权,只发被授权的那部分;内部术语 / 凭据 / 无关全局数据不搭车。
- **与「对外脱敏」的分界**:两条独立的线、本条更上位。对外脱敏 = 业务提交客户远端仓 / 对客时才脱敏;本条(egress)= 任何数据未授权就不离开本机——**云(claude.ai 等)也算离开本机**。
- **为何默认不外发**:用户曾发现一张 claude.ai artifact(旧 BI 设计稿)含内部项目名 + 成本口径已上云(2026-07-14)。教训:default private ≠ 没上云;云端即便删了版本历史,**缓存仍留痕**——所以默认就不外发,而非"发了再删"。

## 长任务完成 / 需要用户介入 → 用事件通知,不用阻塞对话框

**规则**:通知用户走开后回来处理时,优先级:

1. `PushNotification` 工具(Claude Code 内置 — 反扰 60s active 压制,用户走开自动推)
2. Windows WinRT Toast(已注册 AppUserModelID = `ClaudeCode.NotifyTest`,无需管理员)
3. **禁用 MessageBox / 模态对话框** — 多任务并发时弹窗堆叠,用户无法逐一处理

**原因**:2026-06-01 用户 explicit — "对话框任务多不好处理"。事件级 toast 非阻塞,通知中心可累积,用户按节奏回看。

## 需用户拍板/确认 → 非阻塞征询,别占住会话

- **慎用阻塞式交互卡片(如 `AskUserQuestion`)**:用户一般没时间实时盯屏答卡片,卡片一挂、发问会话就被**占住干等**。
- **优先非阻塞**:把问题(含背景 + 选项)落到本项目的待办文件/清单 + 按上节发事件通知,然后**继续手头可并行的活、别等**;用户按自己节奏回来批量答复。
- 确实必须当场拍板才能继续、且用户在场时,才用交互卡片。

**原因**:2026-07-06 用户 explicit — "AskUserQuestion 会占用会话,要想办法优化掉"(agent 用卡片阻塞会话的实例)。

## agent bus 标准用法(hello / 自醒)— 所有会话通用

> 2026-07-28 用户令:所有项目/会话都纳入 bus 管理,无"无关项目"例外。

- **入口**:`ab <命令>`(已在用户 PATH;固化正确 env python + UTF-8)。新 shell 报 `ab: command not found` 时用全路径回退:`"C:/path/to/python.exe" "C:/apphome/agent_fleet/agent_bus/agentbus.py" <命令>`。
- **标准上岗三步**(顺序别乱):
  1. `ab whoami` — 先反查本会话是否已绑定 role(**compact 重载 presence 不丢,不必重 hello**,重认身份即可);
  2. 查不到才是真·新 tab → `ab hello --role <本tab角色>`(每个 tab 仅一次;登记身份、绑定进程,**不会自动接活**)。还没有角色名的新项目会话:用 `ab roster` 看在册角色、或问 Master/培训师 定名注册,别匿名干活;
  3. 挂自醒:后台跑 `ab wait --role <角色> --timeout 0`(run_in_background、别前台阻塞)→ `ab selfwake` 验本 role **✓在岗**才算完。
- **铁律**:每次被唤醒处理完,必须**重挂 wait + selfwake 再验**(不重挂=掉线收不到消息);空唤醒→静默重挂+最多一行,别重复 dump 状态。
- 完整协议(send/done/peek/replies/roster/route 等)见 `C:\apphome\agent_fleet\AGENTS.md` + `BUS_ONBOARDING.md`。

## 等其他 agent 完成任务 → 先看它的 turn 状态,别盲挂 wait 空等

**规则**:当你在【等另一个 agent 交付/完成某活】时,别只挂个 wait 干等——先看目标 agent 的【turn 状态 + 自醒态】,确认它真的在推进这件事,再决定等还是催:
- **在推进**(turn=🟢思考 / 🔧跑工具·age 新鲜,且自醒 ✓armed)→ 可以等它,事件唤醒即可。
- **没在推进**(turn=⚪空闲 / ◌停顿·age 陈旧,或自醒 ⚠掉线 / recent 刚fire未重挂)→ 【别空等】:它可能停顿 / 中断 / 掉线,你派的活只在它 inbox 躺着没人处理,你干等永远等不到。此时:① `ab send` 催一下 / 确认它接了没;② 它掉线就催它重挂(或让用户 / 培训师推),③ 或改道给在岗的对口 agent。
- **怎么看 turn 状态**:WebUI 运维面板 turn 列(_turn_state)/ `<role>/turn_state.json`(hook 盖:thinking/tool/idle + age)/ `ab selfwake` 看自醒态(armed/recent/stale/off)。
- **别把"我挂了 wait"当成"事情在推进"**:wait 只保证【对方发消息时你能收到】,不保证【对方在做】。推进与否看对方 turn/自醒,不看你自己挂没挂 wait。

**原因**:2026-08-10 用户 explicit —— MasterAgent 说"等 MasterUI 提交"却【没看 MasterUI 的 turn 状态】就盲挂 wait 等(MasterUI 那时若停顿/掉线,就是空等)。等待前先确认对方在动,避免空等。

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

## Compact Instructions(压缩时套用)

压缩(/compact 或 auto)时尽量逐字保留:
① 我的身份 / 角色 + cwd + 职责边界;
② 当前目标 + 在做的事 + 未尽任务/待办 + 下一步;
③ 被用户纠正过的约定/口径/偏好;
④ 关键文件路径、决策及理由、已排除别重查的方案、踩过的坑 + 修法。
砍掉:探索性闲聊、被否方案冗长讨论、已解决的历史细节、重复内容。
压后第一动作:复述①②自检,答不全就重读【你自己的 CLAUDE.md + 相关记忆】补回再干活。




