# BUS 2.4 路线图 —— 项目存储(用户立项 2026-07-29)

> 用户令:「2.4 的目标是项目存储大小,现在源代码目录中挂了很多素材源数据文件」。
> 原则=平台三分格局:**项目根(代码+运行态方舟)/ _releases(发版快照)/ _data(素材源数据)**;
> 素材外置一律 junction 回链(canonical 路径零感知),迁移遵守【先广播→动刀→再确认】。

## P0(✅ 已完成 2026-07-29)
- session-archive(78MB 会话 raw jsonl + 素材源包)物理外置 `C:\apphome\agent_fleet_data\`,原位 junction;
  项目根目录 250→172MB;发版 zip 加排除(junction 会被 bsdtar 追进);path_registry data-home 登记;
  CLAUDE.md 迁移清单 +③c。

## P1(✅ 已完成 2026-07-29)
- **存储体检命令 `ab storage-audit`**(bus/storage.py):三分格局总览+项目根 top-level 分类分布
  (junction 不追体积·归物理家)+大文件 topN+素材违规检测(>5MB 落非豁免区);--json 供 P2 面板复用。
- **素材入库纪律**:已落 AGENTS.md 操作锚(命令速查+「素材入库纪律」节);onboarding 模块已派培训师。
- **剩余大件定性(storage-audit 实测 2026-07-29·项目根逐文件实算 145MB)**:
  usage.db 36.1MB=方舟不动;projects/C--apphome-agent-fleet 83MB=运行态(CC 自清+迁移包设计,
  其中单会话 jsonl 最大 17.1MB——归档清瘦走 sessions --by-size+Master数分闸,非 2.4 强动);
  agent_bus 50.2MB=源码+运行态(mailbox 积压卫生代谢在吃);.git 10.4MB=史。全部无素材违规。
- **附带产出**:_data/session-archive 建独立本地 git(969433c·只版本化 extracted/manifest 等小提炼
  产物,raw 不入库)——版本史+防误改,且不进项目根基建 git=业务数据可整体清除连史全清(平台纲领)。

## P2(已派发 2026-07-29·归下版)
- WebUI 存储面板(📁路径页加大小列·MasterUI,task 1785294339638,数据源 storage_audit() 已备好);
- _data 保留期策略(Master数分口径,task 1785294341970:raw jsonl/两素材源包保留期;extracted+manifest
  默认永久·已有独立 git)。

## 发版
- **v2.4(2026-07-29)**=P0+P1+wait 心跳修复;zip 30MB(v2.3 52.7MB→外置瘦身直接体现)。

## 非目标
- 不压缩/不动 usage.db 方舟;不动 projects 迁移包设计;素材绝不直接删(外置≠清除,删走Master数分闸)。
