# fork_exchange —— 分岔改动交换所(给另一个 bus 吸收)

> 用户令 2026-08-10 建。本 bus 实例名 = **`genesis`**(见 `FORK_NAME`,单一源)。
> 落地 [[MULTI_BUS_EVOLUTION]] 备忘:多 bus 各自进化,本目录 = 本 bus 的【出港转移单元】。

## 这是什么
本 bus(genesis)相对 fork 点的进化改动,打成【人可读 + 可吸收】的发布单元,供另一个 bus 拉取评审后吸收。**出港目录**:只放 genesis 流出的东西,不放吸收进来的(吸收进来的走各自 merge)。

## 命名规范
- **分岔改动说明**(本目录 .md):`<fork名>_<日期YYYYMMDD>_<改动简说>.md`
  例:`genesis_20260810_selfwake-reliability.md`。文件名用 ASCII 简说(跨机/跨 bus 安全,避 GBK 坑),标题/正文中文详述。
- **发布包**(生成在 `../agent_fleet_releases/`):`<fork名>_agent_fleet_<版本>.zip`
  例:`genesis_agent_fleet_v2.8.zip`。打 `vX` tag 时 githooks/reference-transaction 自动生成,前缀读 `FORK_NAME`。

## 每个分岔 .md 该写什么(吸收方视角)
按 MULTI_BUS_EVOLUTION 四层分可吸收性,一条改动必标:
1. **改了什么 + 为什么**(根因/收益);
2. **可吸收性**:平台面机制(可吸收)/ 实例面(路径·角色·舰队,不吸收)/ 需适配(带本机语境要改);
3. **吸收单元**:commit 号 / 文件 / config 项;
4. **验收**:吸收后跑什么冒烟(bus_smoke / ui_smoke)+ 平台三问。

## 吸收方(另一个 bus)怎么用
1. 读本目录 .md 清单,挑要吸收的分岔;
2. 平台面→cherry-pick 对应 commit 或按说明手改;实例面→跳过;需适配→按本机语境改;
3. 跑齐两套金标准冒烟 + 平台三问(归属/清除/换域)验收;
4. ⚠ egress 红线:跨机拉取=数据离开本机,须用户对该通道一次性授权。

## 纪律
- 每次版本发布(打 tag)配一个分岔 .md(简说该版本可吸收增量);
- .md 进 git(演进说明);.zip 不进 git(发版产物,在 releases 目录);
- 只写去语境化可迁的教训/机制,本机路径类坑标"需适配"不直接外发。
