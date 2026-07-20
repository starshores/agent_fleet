# CAPABILITIES — 已打通的通道,怎么用(自包含,能力不丢失)

> 这份是 Agent Fleet 的"通道说明书"。即使项目记忆没加载,照此即可恢复信息获取能力。
> 更深的坑/细节去查项目记忆索引(见 CLAUDE.md 的关键坐标)。

## 1. sql_agent — 查数据库(唯一合规通道)
- 位置:`C:/BaseSpace/sql_agent/`(stdlib HTTP 代理,127.0.0.1:5678)。
- **绝不** 直接 psycopg2 连库;一律走 inbox/shared 文件协议(机密数据闸门)。
- 发查询:Write `sql_agent/inbox/<task_id>.sql`,文件头:
  ```
  -- intent: 一句话人类可读意图
  -- share: auto
  SELECT ...        -- 单语句;正文里有 ; 会被拒;用聚合(COUNT/SUM/GROUP BY)压进 30 行闸门
  ```
  `task_id` = 文件名 stem,如 `20260622-150000-rowcount`。
- 读结果:`sql_agent/shared/<task_id>.meta.json`(必写:status/rowcount/columns/elapsed/error);
  通过闸门则明细在 `shared/<task_id>.data.json`。**绝不读 `sql_agent/private/`**(机密镜像)。
- 写库(UPDATE/DELETE/DDL):用户在 UI(http://127.0.0.1:5678)点"批准执行"。先 EXPLAIN(不带 ANALYZE,走自动通道)验证计划再发审批。
- **连接路由**: 见协议
- 自动执行类:SELECT / WITH...SELECT / EXPLAIN(无 ANALYZE)/ SHOW / CREATE TEMP;其余审批。

## 2. Web 自动化 CDP — 通用(云文档/读消息只是其一)
- 定位:**任意 web 任务**的 CDP 入口,非仅云文档。
- 机制:Edge 带调试口启动(桌面 `启动Edge-可自动化.bat`,端口 9222)。
- **★2026-07-20 变更(必读,别再踩)**:Edge 已到 150,**136+ 禁止在【默认用户档】上开 CDP**(安全新政:防本机进程借用你登录态 + app-bound cookie 加密)。故自动化改用**专用档 `C:\apphome\edge_cdp_profile`**(`--user-data-dir` 指它 + `--profile-directory=Default`);bat 已按此配。**代价**:专用档 = 与你日常浏览**不共享 cookie**,要自动化的站点须在此档**各登录一次**(登录态常驻该目录)。想恢复"共享 live session"只能回退 Edge<136(动主浏览器+关安全闸,已评估否决)。
- 探活:`curl http://localhost:9222/json/version`(Browser 应为 Edg/…)。
- 连接:Playwright `connect_over_cdp("http://localhost:9222")` → `b.contexts[0]`(**专用档**登录态,非你日常档)→ 找已开页面(别新开)。**别 `b.close()`**(会关掉自动化 Edge;它是独立实例,关它【不】影响你日常 Edge,但也别中途关),只断开。
- 写表格:OS 剪贴板若被锁(本机常见),改**逐行键入**(每行 Tab 分列、行末 Enter 后 ArrowLeft 回 A 列);整块 TSV 粘贴要 Set-Clipboard,本机不稳。
- 读消息:连 messenger 页,点会话,从 DOM 提取(截图常太小)。
- 隐私:仅在本机操作其本人会话,**表/消息内容不外发**。

## 3. 调度系统 — 调度 / 重跑 / clear task
- 地址、账号、任务名按部署环境配置,不写入仓库。
- clear task 重跑:POST `/clearTaskInstances`,body 必带 `only_failed:false` + `reset_dag_runs:true`,用 start/end_date 框 execution_date;**不要** include_downstream/dag_run_id(2.4+ 才有)。精准 clear 走 web /clear form + cookie(REST 不支持 downstream filter)。
- 代码发布与任务重跑按项目实际环境执行,具体脚本/路径不入仓。

## 4. 项目记忆 — 深度知识库
- 索引:`~/.claude/projects/XXX/memory/MEMORY.md`。
- 用法:Read 索引看一行行 hook,需要哪条再 Read 那个 .md。
- 真相源优先级:**数据库实测数据 > 任何 spec 文档**。

## 5. ERP系统
- dev:按部署环境配置;系统配置项增删改**一律走 web API**,不 SQL 直写(直写跳校验/派生/注册)。
- REST 走不通时 fallback:requests + CookieJar 模拟 UI form。

---
**通道健康自检**:`python C:/apphome/agent_fleet/recover.py` 会探 agent bus + Edge 调试口,并打印上述指针。
