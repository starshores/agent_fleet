# 下一会话接手入口

更新：2026-10-20。本文件服务于后续开发者或能力不同的 AI：一次只完成一个有明确证据的工单，遇到未知保持未知，不凭文档名称推断实现完成。

本轮新增[项目整体重评](MASTERAGENT_PROJECT_REASSESSMENT_20261020.md)及[证据目录](history/project-reassessment-20261020/README.md)。重评发现一次性执行子结果遗漏、端点/工作区创建顺序及前端请求身份跨实例丢失等缺口，建议与 B-07/B-08 的用户闭环一起优先处理。建议未改写原工单，也未修复产品代码；后续实施前先读对应复现，不把分析交付当新版本。

## 唯一项目与当前事实

唯一开发根为 `C:/apphome/agent_fleet`。业务可替换、本机统一 Web 工作台、Codex/Claude 协调契约、存储一体化仍是目标。当前会话没有 agent-bus 精确身份绑定；本开发接手流程不注册角色，不因目录或 PID 自称 MasterAgent，不启动旧信使或接管现任角色。

| 项目 | 核对基线 |
| --- | --- |
| 正式版 | `3.0.0`，schema 4；`releases/3.0.0/masteragent-3.0.0.pyz` |
| 正式包 SHA256 | `69402eaad81035e121fed0d2af07b31f660ea49abc6f80b0516811831967addf` |
| 已验开发版 | `3.1.0-dev.4`，schema 6，公共 API `3.1-dev1`；`releases/3.1.0-dev.4/masteragent-3.1.0-dev.4.pyz` |
| 开发包 SHA256 | `65c7ae5a49260440bbbe9c5ac274e70aca94bca66a1470dd851dfae2c96094ca` |
| 当前能力 | A 完成；B 新增受控来源、准入端点绑定、独立常驻搬运；完整 held 管理及真实试点仍未完成 |
| 最近完整验证 | 以 `releases/3.1.0-dev.4/release-manifest.json` 与 `evidence/python-tests-final.log` 为准；前端 52 项通过，独立包 A→B→A 与 CLI 常驻合成闭环通过 |
| 发行索引 | `releases/release-catalog.json` 及各版 `release-manifest.json` |
| 外部开发副本归档 | `releases/development-archive/2026-10-19/`；实际文件与迁移记录以其中清单为准 |
| 临时预览 | 旧 55724、61670、54051、60141 预览已关闭或不存在；旧文档 URL 是历史证据，不能当现存服务 |

归档包里的源码只代表该包实际包含内容。当前测试/文档快照的采集时间另记，不冒充历史版本当时快照。正式包、开发包、已运行服务是三种独立事实。

## 首轮只读五份文件

依次读取以下五份，不批量装入全部角色记忆或历史：

1. [项目指令](../AGENTS.override.md)：权限、身份、测试和运行边界。
2. 本文件：事实、路径与接手动作。
3. [3.1 实施进度](MASTERAGENT_3_1_PROGRESS.md)：当前状态与最近一批证据；早期章节只作历史。
4. [逐项执行工单](MASTERAGENT_EXECUTION_BACKLOG.md)：选定唯一当前工单及依赖。
5. [持久路由与回执契约](MASTERAGENT_3_1_ROUTING_RECEIPT_CONTRACT.md)：开始 B 前必须理解的守卫。

工作到 C/D/候选版时再读工单关联契约；长期排序见 [版本路线](MASTERAGENT_VERSION_ROADMAP.md)。遇到说明冲突，先核对源码、当前包和证据，记录差异；不能擅自忽略守卫或宣称文档中的“设计”已实现。

## 低额度、低上下文接手方法

一次只做一票，首读最多上述五份；随后只读取该票涉及的函数和测试。先做最小失败复现或边界表，确认输入、授权、状态转换和失败结果，再动实现。先跑能区分正确/错误行为的相关测试，候选包再跑完整回归；把命令、结果和下一步及时落盘。遇到真实权限、环境或专业判断阻断，保存现有差异、复现和具体待决项，继续独立工作；不要为解一个局部问题推翻架构、放宽守卫或重写全仓。工单长表供按 ID 查找，不要求每轮全部重读。

## 路径矩阵：归档不是部署

| 路径或对象 | 用途 | 允许的接手操作 |
| --- | --- | --- |
| 项目根 `C:/apphome/agent_fleet` | 当前源码、测试、文档、既有旧服务代码 | 只修改工单明确文件，保留其他人的改动；不 reset/clean 全仓 |
| 项目 `releases/` | 不可变发行包、恢复出的包内源码、证据与清单 | 读、校验、按新版本新增；不得覆盖已验历史包 |
| 归档中的 SQLite、workspace、目的端目录 | 历史合成证据 | 只做文件完整性核对；不得就地启动平台、迁移、安装或运行模型 |
| 新合成测试/预览部署 | 运行期隔离状态和工作区 | 必须使用明确的项目外专用临时目录；状态、workspace、目的端分别独立，由路径守卫验证 |
| 专用原生 home | 经本次授权的原生认证与运行历史 | 仅在真实验收工单取得明确范围后使用；不读取旧认证，不复制个人凭据，不沿用 3.0 授权 |
| 旧 fleet、旧业务与未知外部目录 | 现有业务或未知归属 | 不作为合成测试输入，不据“副本”名称清理，不自行停服或迁移 |

`masteragent/paths.py` 拒绝与源码根重叠的状态/工作区，也拒绝祖先包含 `agent_bus/agentbus.py` 的部署位置；链接、junction、硬链接及不安全根另有检查。因此将证据移回项目不代表能从项目内继续运行。后续临时部署的外部目录是有明确用途和清理责任的运行根，不是遗忘副本。若要支持项目内部署，必须另立目录边界设计并独立验收，不能为了归档清理放宽现有检查。

归档目的端带固定 root + instance；移动后不能靠改库、改 descriptor 或新建同名目录继续核对旧 held。恢复原投递需遵守绑定契约；一般新验收应从公开 API 创建全新的合成场景。

## 已知未完成，不能口头跳过

- B：完整 held 管理闭环、B-08 整体放行、真实 Codex 单角色及预编排双角色试点；旧生产来源继续留旧。常驻搬运已有合成实现，现有 `legacy` lane 仍不是旧 bus 已遵守核心 fence 的证据。
- C：持久事件游标与断线补读、制品封存/索引、输入来源、协调状态及制品备份恢复。
- D：部署工具策略、OS 隔离行为证据、独立 RuntimeApproval、真实工具与原生协作桥；`command` / `file_change` 当前均拒绝。
- Claude：接入设计可继续，真实 Claude 与混合团队未验；不能用 Codex 或替身结果代替。
- UI：21 项目录和兼容链接不等于完整迁移；连接、原生恢复、历史用量、工作组等仍须依清单落实去向和受限状态。
- 发布：3.1 尚未正式发行，旧 fleet 尚未整体切换；3.1 原承诺不能移到 3.2 后宣称完成。

## 第一项可安全开始的工作

先核对本批包和 [工单状态](MASTERAGENT_EXECUTION_BACKLOG.md)，无需重做限定范围的 B-00～B-06。入口签收与常驻证据见 [受控搬运手册](MASTERAGENT_3_1_TRANSPORT.md)；下一项实现 B-07 的专用 held 清单、带请求 ID/revision/理由的共享核对命令与工作台入口，收齐 B-08。已有核心核对不代表工作台管理闭环；不得为跳过签收而扩读旧邮箱或切换在线服务。

推荐核查命令如下，均在项目根运行；路径和可执行文件不存在时先记录缺失，不能自行安装或改用未知版本：

```powershell
Set-Location -LiteralPath 'C:/apphome/agent_fleet'
Get-FileHash -LiteralPath 'C:/apphome/agent_fleet/releases/3.1.0-dev.4/masteragent-3.1.0-dev.4.pyz' -Algorithm SHA256
Get-FileHash -LiteralPath 'C:/apphome/agent_fleet/releases/3.0.0/masteragent-3.0.0.pyz' -Algorithm SHA256
Get-Content -LiteralPath 'C:/apphome/agent_fleet/masteragent/__init__.py' -Encoding UTF8
git status --short
```

每个工单先跑相关测试，最终候选构建跑完整回归。以下命令仅使用 `tests_v3` 的合成/临时根；不要换成旧 `bus_smoke.py`、`ui_smoke.py` 或场景脚本：

```powershell
& 'C:/anaconda3/envs/server/python.exe' -B -m unittest discover -s tests_v3 -v
& 'C:/Program Files/nodejs/node.exe' --test tests_v3/workbench_ui.test.mjs tests_v3/workbench_legacy_navigation.test.mjs
```

默认测试临时目录仍须通过路径检查。没有合法外部临时根时，停止依赖运行目录的测试，先完成静态工单；不要创建项目内 state 或修改 `SOURCE_ROOT`。构建入口是 `scripts/build_v3.py`，先读其参数和 `tests_v3/test_bundle.py`，不得覆盖旧包。`scripts/accept_transport.py` 可显式选择新包与旧包进行独立合成验收；`test_dev4_cli.py` 是旧文件名，本次搬运证据另在 `test_transport*.py`。

## 每轮交付格式

在进度文档中记录工单 ID、变更文件、实际测试命令/结果、合成或真实范围、证据目录、下一项及阻断。状态只取 `未开始 / 进行中 / 待验收 / 已完成 / 受阻`；只有工单所列证据齐全才写已完成。难点需决策时先写最小复现和可选方案，不让下一人重做调查。

schema 当前为 6，后续只在明确落库变更时分配下一实际版本，不照工单编号硬编码。运行时可执行版本、协议、模型来自显式部署与实际可用能力；文档示例版本和服务端报告不等于当前可用或实际推理模型。未完成验收不提前改正式版本号，不生成未来版本占位二进制。

## 可复制的接手提示

> 请从 `C:/apphome/agent_fleet/docs/MASTERAGENT_NEXT_SESSION.md` 接手。唯一项目根是 `C:/apphome/agent_fleet`；当前已验开发版 `3.1.0-dev.4`、schema 6、API `3.1-dev1`，正式版仍是 `3.0.0`。按首读五份文件恢复，核对包摘要和工作树，再按工单当前状态继续 B-07/B-08；B-00～B-06 已有限定范围证据，不从头重做。保留既有修改，不注册 agent-bus 角色，不读旧凭据、私人会话或真实邮箱，不自动切服务。`releases/` 只用于归档，合成运行用显式项目外临时部署并通过原路径守卫。记录可复现步骤与证据，区分合成/替身/真实。不得把未完 B 或 3.1 标完成、移到 3.2、提前发正式版或伪造未来二进制。需要真实环境时先完成独立实现和合成验收，再给出具体范围与下一动作。
