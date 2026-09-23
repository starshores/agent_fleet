# 三闸提案 · 唤醒丢失型掉线的自动代理续跑(autoproxy-on-wake-loss)

> 提案方:MasterAgent · 2026-09-23 · 依 docs/SELF_REDESIGN_CHARTER.md(两铁律·三闸·预声明回滚线·escalate 判据)
> 签字方:Master数分(三闸各独立签字·不自审自批)· 消费方征询:Master(派活受益方)
> 状态:**搁置(SHELVED)** —— 用户拍板 2026-09-23(待办 t-0da2 答复):「现在只考虑使用在线 agent」= 不起代理替掉线 agent 干活。三闸签字请求已撤回;本文留档供日后若口径变化复议。出处:docs/FRAMEWORK_REVIEW_20260923.md 方向建议第 1 项。
> **含义**:③唤醒丢失型的解法须回到「让 agent 本身在线」这一侧(守卫可见性 / 用户侧点醒 / harness 层),而非代理续跑。

## 0. 问题与数据(为什么现在做)

近 7 天全舰 73 次掉线催单三分型:①忘重挂 33(45%·C′ Stop-hook 治)/ **③唤醒丢失 29(40%·零机制解)** / 环境死 11(15%)。
③型定义(Master数分 R1 口径·8a590b):wait 尽职 fire → agent 纪律尽了(重挂了)→ **harness 未唤醒会话** → 无 turn → 任务躺 inbox 无人领。现状唯一兜底=守卫 7min toast → 人点 tab;用户不在场=永久掉线(Master数分/示例组员4 各躺 40h+)。Master 三天 5 次"请拉活"落 MasterAgent = 缺口外溢。
**③型在 harness 层,机制层改不了唤醒本身;能改的是"掉线期间任务由谁做"。**

## 1. 机制设计(what)

**触发(守卫巡检内·全部客观判据)**:role 满足 ①selfwake=stale 且尸检=③型(exit 正常后零 turn 动作)②turn_state=idle 且 idle ≥ 15min ③inbox 有 ≥1 条【真任务】(非 notice/非守卫催单/非 requires_approval)④该 role 近 1h 未起过代理(`<role>/autoproxy.json` 锁·防重复)⑤role 在灰度白名单。
**动作**:`ab dispatch --name <role> --from bus守卫 --resume-session <presence.session_id> --timeout 3600 --title "[autoproxy] 代领 <role> 待办" --task "你是【<role>】的掉线代理:以 <role> 身份 `ab wait --role <role> --timeout 5` 领一条任务→处理→`ab done --role <role>`;只做一条即退;不重挂 wait;结果开头标 [autoproxy]"`。
**身份与归属**:`--name <role>`=在册 role → 消耗账本归该 role(非 _adhoc);done 结果带 `[autoproxy]` 标记=派发方可识别"这是代理做的"。events 落 `autoproxy` 事件(role/sid/task_id)=可审计可回溯。
**一次一条·一次性即退**:不是常驻执行体;做完退出,原会话随时可被人唤醒接管。
**代理硬边界(Master 派发方要求·09-23)**:①只执行不决策——遇决策点/需 ask-user/有取舍一律原样回报不自拍;②禁 commit/push/写生产/改配置中心(requires_approval 之外再加的硬线);③起手先读该 role inbox 全部 + 最近 Master 纠偏消息再做(resume 拿到的是旧上下文·可能漏 bus 里后发的纠偏);④结果必带 `[autoproxy resume=<sid前8>]`;⑤原会话醒来须能看到代领记录(代理 done 后向该 role 自投一条 fyi「代领了 <task_id>」)。

## 2. 与 2026-08-27 daemon 回退教训的正面对照(核心价值闸的关键)

daemon 化被回退的根因(genesis_20260827):**领任务不管不顾·不可打断·卡死只能手动 kill·协作频道退化为刚性队列·冷起贵**。本机制逐条对照:

| daemon 化(已回退) | autoproxy(本提案) |
|---|---|
| 常态执行体·替代前台 | **仅掉线期触发**·前台仍是主体 |
| 领任务不管不顾·队列化 | 一次一条·做完即退·inbox 其余留给原会话 |
| 不可打断 | 原会话被人唤醒即接管(代理已 claim 的那条在 processing·不重复);代理 1h 超时硬退 |
| 冷起·无上下文 | `--resume-session` 带原会话全上下文(层 C)·不是冷起 |
| 有 request 能力但实际不问 | 代理 prompt 保留 request/send 能力(dispatch 现有 prompt 已含) |

**结论(提案方自评·待Master数分判)**:强化协作(掉线不再阻断派活·Master 不必人肉拉活),不削弱可打断(原会话主权不变)。

## 3. 三闸自评(提案方视角·每闸待Master数分独立签字)

- **闸① 核心价值**:见 §2 对照表。风险=被当成"掉线也没事"从而放松重挂纪律 → 对策:代理结果标 `[autoproxy]` + 画像计"被代理次数"(中性呈现)+ C′ 继续治①型;autoproxy 只接③型不接①型(①型有 turn 却没挂=纪律问题不替它擦)。
- **闸② 分离(三问)**:归属=协调机制(守卫编排 dispatch)·零业务内容;清除=业务清空后守卫无任务可代=空载即静默;换域=白名单/阈值走 config·不改代码。✓
- **闸③ 在线(增量绞杀)**:纯新增守卫分支·不动现有 wait/dispatch/守卫催单路径;白名单灰度;回滚=白名单清空一行。

## 4. 预声明回滚触发线(触即自动回旧=白名单清空)

1. **误起**(代理起时该 role 实际 turn 活跃 / 或起后 5min 内原会话自己 claim 同任务)≥1 次 → 停·查判据。
2. 代理 done 被派发方标错/否决率 > 20%(周窗)→ 停。
3. 双重执行(同任务被代理和原会话各做一次)≥1 次 → 停(理论上 O_EXCL claim 已排,出现即 bug)。
4. 成本:单 role 日代理 > 3 次 → 该 role 摘白名单(说明它掉线太频·该治根不该续命)。

## 5. escalate 点(宪章 P5:决策 → 用户·非阻塞待办池)

- E1 **权责**:允许代理以 role 身份执行【非 requires_approval】业务任务?(与 daemon 时期 claude -p 执行同风险面·approval 闸不变)——默认是,请用户确认。
- E2 **成本**:一次 resume = 原会话全上下文读入(贵·token 量≈该会话累计上下文)。限流=每 role 每小时 ≤1 次、仅 inbox 有真任务时起。请用户认可此成本换"掉线不阻断"。

## 6. 验收指标(Master数分 L级B·两周窗)

③型催单 → 自动代理转化率;代理 done 成功率(status=ok);误起 = 0;派发方否决率 < 20%;Master"请拉活"请求数趋零;白名单 role 的③型"任务躺 inbox 时长"中位数 从 h 级降到 min 级。

## 7. 实现点(动工后)

bus/identity.py `cmd_selfwake_guard`:③型判定后加 autoproxy 分支 → bus/exec.py 编程调用 dispatch(或 subprocess `ab dispatch`)→ `<role>/autoproxy.json` 锁 + events `autoproxy` → smoke 用例:guard 判据成对(白名单外不起/①型不起/无真任务不起/approval 任务不起/1h 锁生效)+ 真路径一条。config:`autoproxy_whitelist`/`autoproxy_idle_min`/`autoproxy_per_role_hourly`。

## 8. 灰度计划

段1 白名单 {示例组员4, Master数分, 业务侧}(前两者③型最多·4/5 次周;业务侧=纯只读核查·风险最低·Master 派活最多)→ 1 周 → Master数分按 §6 验 → 段2 +示例组员2 → 全员。**调度 agent 暂不纳入**(Master:其手上是跨集群/部署/门控类生产邻接操作 + PAUSED 待信号语义·代理误判'该开建'风险高)。

## 9. 消费方意见并入(Master · 派发方 · 2026-09-23 ebc43c)

支持方向。四个坑与对策:
- (a) **双处理竞态**(原会话恰在代理期间醒来):claim 走 O_EXCL 单胜者=同任务不可能双领;代理已 claim 的在 processing·原会话 peek 看不到=天然不重做;补:代理起时写 `<role>/autoproxy.json`(含 task_id),原会话 hello/wait 起手若见此锁且代理活着 → stderr 提示「代理在领 X·勿重做」。
- (b) **旧上下文漏纠偏**:硬边界③(起手先读 inbox 全部 + 最近 Master 纠偏)。
- (c) **成本**:idle≥15min + 每 role 每小时 ≤1 + 仅有真任务才起(已在 §1/§5 E2)。
- (d) **不得削弱可打断**:代理跑在 claude -p·bus 消息可达(dispatch prompt 含 send/request 能力);对方新消息进该 role inbox·代理做完当条即退不再领=自然让位;硬超时 1h。
