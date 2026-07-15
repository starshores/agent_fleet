# 长程任务锚定生长网 · 用法 how-to(消费方看这份)

**给谁**:Master(及各业务 agent)——你是这套机制的消费方,用它管长程任务。
**状态(2026-07-13)**:P0 骨架 + P1 机制层【已上线可用】。P2(跨任务边/机器自设锚点/复杂度熔断)未做。**实际并行执行(fork 分支接工作组池真跑)暂搁**(用户令 P1 并行度~1.1 不攻)——即 fork 结构能建、但分支目前是串行执行,不影响用法。
**设计背景**:见 `LONGRANGE_TASK_BRIEF_2026-07-13.md`。

## 它是什么(先摆正定位)
引擎 = **状态/协调 + 决策纪律的底座**,**不是执行器**——它不替你跑 SQL/写代码。它替你:
① 记住整张网(要做什么·依赖·验收标准·状态);② 强制【自主分层】(哪些动作可自动、哪些必须请示用户);③【决策合批】(N 个待拍点攒成一张单一次给用户,不再零售 5 来回);④ 判收敛/塌缩(活工作集恒=一段·卡进 mem-cap);⑤ 喂 WebUI 🗂️长程任务看板。
你(拆解者/执行者/跟踪者三顶帽子)驱动它,实际活还是你/业务 agent 干。

## 引擎入口
```
python C:/apphome/agent_fleet/agent_bus/longrange_net.py <子命令> ...
```
看板:WebUI 8799 → 🗂️长程任务 tab(读 `~/.claude/agent_bus/longrange_tasks.json`)。

## 核心概念速记
- **脊柱 spine**=你钉的里程碑序列 `起点→中间点…→终点`(锚点)。网只长到下个锚点就收敛塌缩,不无脑长。
- **节点 node**=一个原子工作单元(带 status/owner/验收标准/autonomy)。**边**=依赖(父 done 才解锁子)。
- **autonomy 自主等级**(★反复确认杀手):`auto`=只读/可逆·自动推进不请示;`notify`=做了报一声;`ask`=不可逆/对客/DDL·必须请示。
- **校验闸 precheck**:ask 节点执行前先跑预检(owner 预检/引用扫描)——失败就 blocked、**炸在提审批前**,不去烦用户。
- **决策合批**:同段所有 `ask`+预检通过的节点攒【一张单】,用户一次批完(WebUI 决策合批单一处点批)。

## 一条任务的完整流程(可照抄)
```bash
LR="python C:/apphome/agent_fleet/agent_bus/longrange_net.py"

# ① 播种:钉里程碑脊柱(author=user 的锚点=你要用户拍的里程碑)
$LR init --id demo-longrange-0713 --title "示例长程任务" --spine '[
 {"anchor":"起点:输入就位","author":"user","type":"start"},
 {"anchor":"里程碑:迁移到目标环境","author":"user","type":"mid","acceptance":"目标环境行数/结构与源一致","decision_gate":true},
 {"anchor":"终点:全量核对通过","author":"user","type":"end","acceptance":"抽样+全量diff=0"}
]'

# ② 拆解者:在当前段续接节点(线性默认接上一个;--autonomy 打自主标)
$LR add --id demo-longrange-0713 --label "读源表schema+行数" --autonomy auto
$LR add --id demo-longrange-0713 --label "DDL 迁移到prod" --autonomy ask   # DDL=ask

# ③ 执行者:auto/notify 节点自己跑完标完成;ask 节点先过校验闸
$LR done-node --id demo-longrange-0713 --node <auto节点id>
$LR precheck  --id demo-longrange-0713 --node <ask节点id>            # 通过→await_decision;加 --fail --findings "…"=拦下不烦用户

# ④ 决策合批:把本段所有待拍点攒一张单(不零售)
$LR decide-batch --id demo-longrange-0713
#    用户在 WebUI 决策合批单一处点批(或命令):
$LR resolve-batch --id demo-longrange-0713 --approvals '{"<node>":"approve","<node2>":"reject"}'
#    approve→节点转 ready 交执行;reject→failed;未答默认 reject(保守)

# ⑤ 分叉/合并(需要并行分支时)
$LR fork  --id demo-longrange-0713 --parent <父> --labels '["分支A","分支B","分支C"]'
$LR merge --id demo-longrange-0713 --label "汇总3分支" --parents '["<A>","<B>","<C>"]'   # 三父全 done 才解锁

# ⑥ 达锚收敛→塌缩(该段全 done + 前沿封闭 → 归档成单节点·推进下一段)
$LR close-frontier --id demo-longrange-0713
$LR collapse --id demo-longrange-0713 --summary "段完成:迁移达锚"

# 查看
$LR show  --id demo-longrange-0713     # 权威态 JSON
$LR board                            # 重建看板视图
```

## 编辑/回退命令(网跑着改·2026-07-13 补·demo-feat-migrate 实战逼出)
长程网跑起来难免标错/改目标,这些让你不用加占位节点绕:
```bash
# 改最终目标 / 任意锚点(index 支持负数·-1=终点)
$LR set-goal      --id X --goal "新终点目标" [--acceptance "…"]
$LR update-anchor --id X --index 2 [--anchor "…"] [--acceptance "…"] [--decision-gate true|false]
# 重开已塌缩/误标达成的段(un-collapse·默认最后一段):status 回 growing·锚点回 active
$LR reopen        --id X [--segment N]
# 跳过被驳回/废弃的节点(status=skipped·不挡收敛,免它卡住 collapse);或直接删
$LR skip-node     --id X --node <n> [--reason "…"]
$LR remove-node   --id X --node <n>
# 往【已 done 的网】加节点 → 自动把 status 翻回 growing + 重开末段(不用手动 reopen)
$LR add           --id X --label "…"      # done 网上直接 add 即自动复活
# show 现附 _complexity + _efficiency(看"已省 N 次反复确认")
$LR show          --id X
```

## 三顶帽子(§2.3·你怎么戴)
- **拆解者**:播种脊柱 + 段内 `add` 续接节点(每节点带验收标准);节点完成后重判"要不要催生新节点",不长了就 `close-frontier`。
- **执行者**:找 `ready` 节点跑;跑前对 ask 节点 `precheck`;干完 `done-node`。**按 autonomy 分层**:auto/notify 别问用户直接干,ask 才进合批请示。
- **跟踪者**:盯全局网、判 merge 就绪、到锚点主动 `collapse` + 向用户汇报,**别停在"待命"**。

## 关键纪律(这机制的目的)
**把用户当"里程碑审阅者"不是"单步决策器"**:只读/可逆的活自动推进;要用户拍的攒成合批单一次给;校验失败自己拦不上抛。目标=砍掉 Master 那种 单表去留反复 5 来回 / 一批 DDL 拆 4 审批点的反复确认。

## 看板价值徽标
每任务卡显 `已省 N 次反复确认`(=合批省的往返 + 校验闸免的白批),实时反映机制帮你省了多少人工介入。

## 未做/边界
- 实际并行(fork 分支池化真跑)暂搁~1.1(独立战线·待 pool 成员持续监听修复)。fork 结构照建,分支目前串行执行,不影响流程正确性。
- P2:跨任务边、机器自设锚点、复杂度熔断——未做。
- 有用法疑问 / 要我把某条真实任务线建成 net,找基础设施层 MasterAgent(--to MasterAgent)。
