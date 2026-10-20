# 端点与业务工作区创建顺序：合成复现

2026-10-20。当前 `3.1.0-dev.4` 确认存在路径隔离的创建顺序缺口：先绑定端点，再安装一个工作区与端点重叠的业务，安装被接受，随后原有路由的新消息准入失败。新业务保持 inactive 也能触发，无需执行模型或启用文件工具。

本次使用当前工作树的公开 API、真正的本机 SourceStore/ReceiptStore 和全新合成状态；不是生产服务、真实模型或并发测试。产品源码、已有测试和计划未修改。

## 操作与结果

每个案例均使用独立状态、A 工作区、来源库和目的端库。业务包只有一个合成角色，声明的 echo 工具没有运行。

| 顺序与端点 | 操作 | 预期 | 实际 |
| --- | --- | --- | --- |
| 先来源端点 | 安装并激活 A；初始化两端；配置并绑定路由；准入基线消息；以 source 根安装 inactive B；准入第二条消息 | 拒绝 B 的重叠工作区；原路由保持可用 | 基线准入成功，B 安装成功，第二条准入报 `route_endpoint_overlap` |
| 先目的端点 | 同上，B 工作区使用 receiver 根 | 同上 | 同样复现 |
| 先来源工作区 | 在绑定路由之前以 source 根安装 B，再绑定 | 绑定时拒绝重叠 | 报 `route_endpoint_overlap`，符合该方向现有守卫 |
| 先目的工作区 | 在绑定路由之前以 receiver 根安装 B，再绑定 | 同上 | 同上 |

前两个案例各留下 1 条已准入消息和 A/B 两项安装，第二条没有准入；四个案例 Attempt 数均为 0。没有验证祖先、子目录、并发创建、角色 override 或未来工具访问，不能把本次结果扩大为这些场景已经测试。

## 证据和复跑

- [逐步结果及源码摘要](architecture_endpoint_overlap_result.json)：记录每一步的预期/实际、源码 SHA256、脚本 SHA256 和临时根清理结果。
- [复现脚本](architecture_endpoint_overlap_repro.py)：仅创建项目外全新临时根，`finally` 验证所有权标记、精确根、无链接和不越界后，由同一 Python 进程清理。

复跑时指定一个不存在的证据文件名；脚本不会覆盖现存结果：

```powershell
& 'C:/anaconda3/envs/server/python.exe' -B 'C:/apphome/agent_fleet/docs/history/project-reassessment-20261020/architecture_endpoint_overlap_repro.py' --output 'C:/apphome/agent_fleet/docs/history/project-reassessment-20261020/architecture_endpoint_overlap_rerun.json'
```

本次最终运行退出码为 0，两个端点优先案例均复现缺口；输出确认临时根已删除，额外只读检查也确认路径不存在。最初启动时证据脚本错误导入了不存在的 `VERSION`，在创建临时根前退出；修正为公开 `__version__` 后执行上述最终运行。这是证据脚本修正，不是产品修复。

## 定位与建议

`service.py` 的 `_workspace_paths` / `install` 只把已有 installation 和 role workspace 交给 `normalize_deployments`；该路径没有将已绑定的来源/目的端根作为保留空间。`routing.py` 的 `_guard_endpoint_overlaps` 则在绑定和准入时检查所有已有工作区。因此后安装的 B 可以先持久化破坏原本有效的边界，错误延迟到原路由准入时才暴露。

下一步应在安装与端点绑定两端使用一致的保留根集合和事务内校验，补双向创建顺序及并发竞争测试。不能简单删除准入守卫来让第二条消息通过。本记录仅报告缺陷，没有修改产品或把修复标为完成。
