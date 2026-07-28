# OPS_FINDINGS — bus 运维/口径发现台账(按需查询·不进上下文)

> **口径(什么进这里 vs 进 MEMORY.md)** — 定于 2026-07-27(用户令:非紧要记录该在 bus 留存、按需查,落记忆太占上下文)。
>
> - **进本台账**(append-only·grep/Read 按需查·**零 per-turn 上下文税**):一次性分析结论、外部政策/环境发现、"对表"用的数据快照、诊断记录、"当时为什么"的事实。你**不需要每轮都记得**、只在相关时来查的东西。
> - **进 `~/.claude/projects/.../memory/MEMORY.md`**(每轮/每会话进上下文·**有上下文税**):身份、职责边界、被用户纠正过的约定/偏好、每轮都要遵守的**程序性约束**、高频高相关事实。
> - 判据:**"每轮都要它在场"→ 记忆;"相关时能查到就行"→ 本台账。** 拿不准就放本台账(便宜),真发现每轮都需要再升级进记忆。
>
> **查询**:`grep -i "<关键词/#tag>" docs/OPS_FINDINGS.md` 或直接 Read。**发现**:docs/DIRECTORY_MAP.md 有指针。
> **写入**:任何 bus 成员都可 append 一条(格式见下);别删旧条目(append-only·要更正就新加一条标 supersedes)。
> **可选后续(MasterAgent lane)**:若想要 `ab findings add/query` 命令糖,是个小 build,路由 MasterAgent;当前纯 markdown + grep 已够、无需工具。

---

## 条目格式

```
### YYYY-MM-DD · 一句话标题 · #tag1 #tag2
- **结论**:...(实测/推断分开标)
- **依据**:...(数据/信源)
- **启示**:...(可选)
- 记录人:<role>
```

---

### 2026-07-27 · 上周撞周限而上上周没撞 = +50% 周限加成 07-13 到期,非用量变多 · #usage-limit #weekly #opus #quota

- **结论(实测)**:上周(07-17 22:00→07-24)recorded 消耗**低于**上上周(07-10→07-17)——cache_read 4.16B vs 6.75B、out 5.1M vs 11.5M、calls 12.4k vs 20k,**两周均 ~100% Opus**(模型加权排除)。上上周 07-14/15/16/17 连续 4 天 ~1.4B/天高原都没撞;上周 ~0.4-1.0B/天却撞了。固定 token 周限下"少的撞、多的没撞"不可能 → 天花板本身变了。
- **结论(推断·公开信源证实)**:Anthropic 2026-05-13 给周限 **+50% 临时加成**,**到期日 2026-07-13**(除非延期)。07-13 正好落在两周窗之间:上上周窗(起 07-10)享加成=高天花板;**上周窗(起 07-17)全程在到期后=退回 base、比上上周低约 1/3(÷1.5≈−33%)**。所以上周用得更省却撞墙。
- **依据**:usage.db 按【周五22:00 本地周窗 + 模型family】聚合(ETL 时只到 07-23、缺 07-24,但落后 2.6B 补不平→结论不翻)。信源:morphllm / explainx.ai / verdent / anthropics-claude-code#17084(周限 +50% 于 05-13 起、07-13 到期;5h 窗翻倍与周限是两条独立线)。
- **待用户在 claude.ai 侧核**(usage.db 看不到):① 账号周 reset 槽是否真周五22:00;② 该 +50% 是否被延期(上周撞了=大概率没延)。
- **启示**:按【是否形成持续高原】判周限风险,别按总量;+50% 已到期=退 base 成常态,长会话及时 compact/开新 + 错峰削平顶比以前更要紧。
- 记录人:Master数分

### 2026-07-27 · 预置中转域名实为拼车中转,但因 env 变量名拼错(`f` 前缀)从未生效→CLI 一直直连 Anthropic · #privacy #relay #base-url #verify

- **背景**:`HKCU\Environment` 里有 `ANTHROPIC_BASE_URL=https://<中转域名>/api` + `ANTHROPIC_AUTH_TOKEN=<中转服务签发的 key>`。该域名指向一个开源 **claude-relay-service(CRS)类拼车中转**,架在境外 VPS(域名匿名注册)。可能是预置的中转配置。
- **结论(实测)**:两个变量的**真实名带隐藏前缀 `f`**(`fANTHROPIC_BASE_URL` len=19 / `fANTHROPIC_AUTH_TOKEN` len=21,.NET 精确枚举确认),CC 找的是无 f 的名字 → **从头到尾没读到 → CLI 一直直连 Anthropic**。实连证据:claude.exe 连 Anthropic 官方地址段(RDAP=**Anthropic, PBC**),**0 条**到该中转地址,DNS 缓存有 api.anthropic.com/claude.ai、无该中转域名。
- **处置**:2026-07-27 用 PowerShell `.NET SetEnvironmentVariable` 按精确名删除两条用户 env(`reg delete` 按无 f 名匹配失败,正因真名带 f),验证 CLEAN。该 key 挂在坏变量上、CC 从未用过,随删作废,不入任何文件。
- **判定规则(可复用)**:①走不走中转看【实际连接 IP】(netstat→RDAP),别看配置名表面;②`base_url` = `api.anthropic.com` 或空 → 直连,= 其它域名 → 中转、该域名运营方看得到全部明文;③删/查 env 用 .NET 精确名枚举,别被隐藏字符骗(reg query 里的 `f` 不是噪声、是真名的一部分)。
- **教训**:我最初凭 env 里"看着像 ANTHROPIC_BASE_URL"就报警"全程走中转",是**配置表面误读**;实测连接 IP 才把真相纠回来。data-truthfulness 对自己的结论同样适用。
- 记录人:Master数分
