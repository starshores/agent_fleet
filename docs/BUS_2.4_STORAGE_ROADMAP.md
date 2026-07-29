# BUS 2.4 路线图 —— 项目存储(用户立项 2026-07-29)

> 用户令:「2.4 的目标是项目存储大小,现在源代码目录中挂了很多素材源数据文件」。
> 原则=平台三分格局:**项目根(代码+运行态方舟)/ _releases(发版快照)/ _data(素材源数据)**;
> 素材外置一律 junction 回链(canonical 路径零感知),迁移遵守【先广播→动刀→再确认】。

## P0(✅ 已完成 2026-07-29)
- session-archive(78MB 会话 raw jsonl + 素材源包)物理外置 `C:\apphome\agent_fleet_data\`,原位 junction;
  项目根目录 250→172MB;发版 zip 加排除(junction 会被 bsdtar 追进);path_registry data-home 登记;
  CLAUDE.md 迁移清单 +③c。

## P1
- **存储体检命令 `ab storage-audit`**:du 分布+按注册表分类(代码/方舟/素材/缓存/业务)+超标告警
  (素材落在项目根非 junction 处=违规项),给 2.4 长效巡检抓手。
- **素材入库纪律**:新素材(>1MB 的 raw/中间产物)一律落 _data 或 scratchpad,不落项目根/docs;
  写进 AGENTS.md 操作锚 + onboarding(培训师)。
- **剩余大件逐项定性**:agent_bus mailbox 积压(卫生代谢在吃)/ projects\C--apphome-agent-fleet
  会话库 81MB(CC 自清+设计内)/ usage.db 37MB(方舟·必迁·不动)。

## P2
- WebUI 存储面板(📁路径页加大小列/趋势·MasterUI);_data 目录自身的保留期策略(Master数分口径)。

## 非目标
- 不压缩/不动 usage.db 方舟;不动 projects 迁移包设计;素材绝不直接删(外置≠清除,删走Master数分闸)。
