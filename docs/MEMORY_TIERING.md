# 记忆分层机制(memory tiering)· 历史记忆空间

> 用户令 2026-09-01;机制=MasterAgent(基建 lane),口径=Master数分。实现:`agent_bus/bus/memory_history.py` + `ab memory-history` 命令。

## 为什么(问题)
陈旧/垃圾记忆占**默认加载空间** + 带偏 agent(实例:dormant DAG 的错误关联记忆)。
CC 的 `MEMORY.md` **前 200 行 / 25KB 逐字加载、超出即丢**(claude-code-guide 查官方 docs 钉死)——
这就是"垃圾占默认加载空间"的**物理根源**。

## 三级分层(Master数分口径)
| 级 | 定义 | 默认加载/recall | 访问 |
|---|---|---|---|
| **active** | 当前有用正确 | ✅ 在 `MEMORY.md` 索引·CC 每会话读前 200 行 | 默认 |
| **historical** | superseded-but-valuable(当时对·现过时) | ❌ 够不着/不注入 | 显式命令(轻门) |
| **deleted** | actively-wrong 垃圾 | — 硬删 | 不可回(留墓碑) |

## 成败关键:CC recall 机制(文档钉死)
- CC **只 recall `MEMORY.md` 索引里的文件**(不在索引=recall 看不见);
- CC **只扫 `<proj>/memory/`**,**不扫兄弟/父目录**(子目录排除=未文档化不可靠;**兄弟目录=100% 文档确认不扫**)。

→ **历史空间 = memory 库【外】的兄弟目录 `<proj>/memory-archive/`**(仿 `session-archive` 冷归档范式),
robust-by-construction,只依赖文档化 scoping、不赌未定义行为。

→ **归档 = 两步都要**:① `.md` 移到 `memory-archive/`(recall 够不着)+ ② **从 `MEMORY.md` 删该指针片段**
(这步才真腾 200 行/25KB 预算;缺 ② 只是文件搬走、索引行还占预算=没腾到空间)。
密集多记忆挤一行的索引(业务库格式 `[A](a.md) · [B](b.md) · …`)用**外科式切片**:只切目标片段、
以 ` · ` 重接余者,对 hook 内的裸『·』鲁棒不误切。

## 授权门(轻门 v1·可调旋钮)
默认不查=满足"未授权不默认查阅";agent 显式跑 `recall/show` 即**有意识 opt-in=授权动作**(非被动污染),
无需人批。**护栏**(数据真实性):① 历史只收 superseded(非错·只是旧)故查历史相对安全;
② 读历史结果**必标注「历史·superseded·非当前有效」**防二次带偏。用户要强门(人批)后续收紧。

## 留痕(数据真实性·不伪装)
- `HISTORY.md`:每条归档一行=何时/为何/谁/原 description/**原 `MEMORY.md` 索引片段原文**(revive 回填用);
- 归档**可逆**:`restore` = 文件移回 `memory/` + 索引片段回填;
- `DELETED.md`:purge 墓碑(内容不可回·但留删除记录=不假装没删过)。

## 命令
```
ab memory-history archive <name> --reason "..." [--by <role>] [--lib <dir>]   # active → historical
ab memory-history restore <name> [--lib <dir>]                                # historical → active(回填索引)
ab memory-history list   [--lib <dir>]                                        # 历史清单
ab memory-history show   <name>  [--lib <dir>]                                # 看全文(带 superseded 横幅)
ab memory-history recall <query> [--lib <dir>]                                # 检索历史(带 superseded 横幅)
ab memory-history purge  <name> --reason "..." [--from-active] [--lib <dir>]  # 硬删垃圾(留墓碑)
```
`--lib` 缺省=业务库(config `memory_index` 的库);机制通用,任意记忆库可 `--lib` 指定(平台化:先业务库·后推全部)。

## 验收(Master数分终验点·temp lib 实测全过)
① 默认 recall 真够不着历史(文件出 `memory/` + 索引指针 removed=确定性代理)·
② 归档后 `MEMORY.md` 预算真降(实测 290→191 字节)·
③ 读历史有 superseded 标注·④ 留痕不伪装(HISTORY/DELETED 全字段)·⑤ revive 可回(文件+指针回填)。
