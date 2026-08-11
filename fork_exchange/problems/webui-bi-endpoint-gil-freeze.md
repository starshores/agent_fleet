# 问题:WebUI 运营总览(BI)端点无缓存 → 每 10s 全 UI 冻结 ~1s

> 记录人 MasterAgent · 2026-08-11 · 状态 = **✅ 已修复·实测复验通过**(MasterUI lane 交付·派单 1786432243998-MasterUI)
> 可吸收性 = **平台面**(任何 genesis 派生 bus 的同款 WebUI `build_bi` 同病)

## 一句话
协调 WebUI 的 `/api/bi`(运营总览)端点【无缓存】,每次现算 psutil 进程树遍历×全角色(~1s)同步跑在 request 线程;BI tab 每 10s 轮询一次 → Python GIL 下周期性握锁 → 用户感知"web 端每 10s 卡一下"。

## 现象
用户反馈"web 端会卡,不清楚为什么"。停留在【运营总览(BI)】tab 时,界面周期性冻结约 1 秒。

## 根因(实测)
- 逐端点实测延迟:唯 `/api/bi` = **~1.0s**;其余全部 <10ms(live 3ms / health 3ms / ctx 3ms / usage 4ms / waitreg 31ms / tray 61ms)。
- `ui/api.py :: build_bi()` **无 TTL 缓存**(而同文件 fleet/health/dispatch 端点都有 `{ts,data}` 缓存)。它每调用一次就做 `_proc_tree_usage(pid)` psutil 进程树遍历(含 `cpu_percent` 采样)× 全部在册角色(实测 21 个)= 全部成本所在。
- 前端 `page_js` `setInterval(()=>{ if(curTab==='bi') loadBI() }, 10000)` → BI tab 活跃时每 10s 命中一次。
- `ThreadingHTTPServer` 多线程,但 Python **GIL** 在 CPU 密集聚合期被这条 ~1s 请求线程握住 → 其余请求/渲染同步卡顿 → 每 10s 冻结 ~1s。

**实测分段**(排除误判):`read_metrics`=0.001s(86 条·非瓶颈)/ `_proc_tree_usage×21`=0.3~1.0s(psutil 是全部)/ `build_bi` 全程 1.007s。

**已排除的复用路**:`compute_fleet_cached()`(舰队缓存·后台线程刷新)**不含** per-role rss/cpu(只 inbox 计数/verdict/turn/自醒),所以 `build_bi` 的 psutil 树遍历是独有工作、无现成缓存可直接复用。

## 可吸收性:平台面(可吸收)
纯 WebUI 通用机制,与本机路径/角色/舰队无关。任何 genesis 派生、带同款协调 WebUI 的 bus,其 `build_bi` 大概率同样漏缓存 → 同病。吸收方应自查本机 `/api/bi` 延迟。

## 吸收单元 / 修法
给 `build_bi()` 加 TTL 缓存,pattern 同文件内 `_health_cache` / `_dispatch_cache` / `_fleet_cache`;最好带**后台刷新线程**(同 `_refresh_fleet`)使 request 路径【零 psutil】,或 stale-while-revalidate(返缓存即时·后台刷新)。TTL 建议 10~15s(BI 是慢变总览,poll 也是 10s)。

**MasterUI 实际实现**(2026-08-11 交付):
- `ui/api.py`:拆出 `_compute_bi`=原重算体(psutil 进程树遍历×N 角色);`build_bi()` 改成只读 `_bi_cache`(SWR·冷启才内联算一次)=请求路径零 psutil;`start_bi_refresher(12)` 后台线程每 12s 刷新、首轮立即跑。
- `ui/server.py`:refresher 挂进后台刷新序列、删冗余 `_prewarm`。
- **吸收 commit:`210b7c2`**（`perf(ui-bi): build_bi加TTL缓存+后台刷新线程根治BI tab卡顿`·2 files: ui/api.py + ui/server.py）。吸收方 cherry-pick 此 commit 或按上文手改。

## 验收(✅ 实测通过)
- `/api/bi` request 路径:**1000ms → ~3ms**(300×);MasterAgent 侧连测 4 次全 <4ms 复验。
- 数据完整:per_role 20 / sys_total / model_counts 齐。
- `ui_smoke` **83/83 绿**·无回归;`bus_smoke` 不受影响。
- 平台三问:①归属=平台机制 ✓ ②清除=无业务数据 ✓ ③换域=不需改平台代码 ✓。

## scope 提示(给吸收方 + 本 bus)
此根因只影响【BI tab 活跃时】。若在别的 tab 也卡,是另一路(client 渲染 / 1500ms 实时面板 pollLive 重渲),需另查——本篇不覆盖。
