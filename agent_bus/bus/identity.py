# -*- coding: utf-8 -*-
"""bus.identity —— 身份/自醒/onboarding 域:register(注册)· hello(报到+onboarding 预览)
· whoami(反查本会话 role)· selfwake(自醒在岗查)· selfwake-guard(心跳巡检报警)· tag(pid 标注)。

依赖：config(BUS/_now)· io(_audit/_gen_id/_write_json)· roster(_load_agents/_save_agents/_list_roles/
_retired_roles/_pseudo_role_label/_role_dir/_case_collision)· presence(_touch_presence/_load_pidtags/_pidtags_file)
· selfwake(_ancestor_pids/_is_parked/_parked_path/_self_wake_state/_wait_proc_roles)· notify(_fire_toast)。无反向依赖=无环。
"""
import json, os, re, sys, time
from pathlib import Path

from bus.config import BUS, _now, cfg
from bus.io import _audit, _gen_id, _write_json
from bus.roster import _load_agents, _save_agents, update_agents, _list_roles, _retired_roles, _pseudo_role_label, _role_dir, _case_collision
from bus.presence import _touch_presence, _load_pidtags, _pidtags_file
from bus.selfwake import _ancestor_pids, _is_parked, _parked_path, _self_wake_state, _wait_proc_roles, _is_guard_exempt, _guard_exempt_path
from bus.notify import _fire_toast


def cmd_register(args):
    role = args.role
    dup = _case_collision(role)
    if dup:
        sys.stderr.write("⚠ 拒绝注册:角色名 %r 与既有 %r 仅大小写不同——Windows 文件系统不区分大小写、会共用同一 mailbox 目录(agents.json 却当两个角色→幽灵重复+路由错乱)。请改用既有名 %r,或换个真正不同的名。\n" % (role, dup, dup))
        sys.exit(2)
    _role_dir(role)
    def _mut(a):
        a[role] = {"registered": _now(), "desc": args.desc or ""}
    update_agents(_mut)   # 锁跨 RMW(P0):防与 hello/retire 并发覆盖
    print("registered role:", role)


def cmd_tag(args):
    """按 PID 打"非 fleet"标注(如"其他事务"):不是角色/无 inbox,只让 who 认得它、清掉未声明噪音。
    默认 **私有**:只存 label,**不存 cwd/备注、也不去探进程**(隐私:其他事务的信息对总线隐藏)。
    --public 才存可选备注。"""
    pid = args.pid
    private = not args.public
    entry = {"label": args.label, "private": private, "tagged_at": _now()}
    if not private and args.note:
        entry["note"] = args.note  # 仅 public 标注才留备注;private 一律不留任何细节
    tags = _load_pidtags()
    tags[str(pid)] = entry
    BUS.mkdir(parents=True, exist_ok=True)
    _write_json(_pidtags_file(), tags)
    print("已标注 pid %s = %s%s" % (pid, args.label, "(隐藏信息,不探不存细节)" if private else ""))


def cmd_hello(args):
    """会话自报身份:在本 tab 里跑一次,声明"我是 role X"。
    顺带打印【基本能力 onboarding】——事件自醒 + 待办记录(ask-user),新 agent 一注册就知道,不必翻文档。"""
    if getattr(args, "preview", False):
        # 纯预览(WebUI 提示词审核页 server.py 调 `ab hello --preview`):不带 --role 也能跑,
        # 只打印 onboarding 文本,【绝不】写 presence / 绑 pid / 改 mode —— 只读、零副作用。
        _print_onboarding(args.role or "<你的role>")
        return
    role = args.role
    if not role:
        sys.stderr.write("hello 需要 --role(仅预览可省:hello --preview)\n"); return
    dup = _case_collision(role)
    if dup:
        sys.stderr.write("⚠ 拒绝 hello:角色名 %r 与既有 %r 仅大小写不同——Windows 文件系统不区分大小写、会共用同一 mailbox(致幽灵重复+绑定错乱)。你多半想声明 %r,请用它的准确大小写重跑。\n" % (role, dup, dup))
        sys.exit(2)
    _role_dir(role)
    def _mut(a):
        new = role not in a         # 首次注册的新角色 → 培训闸(_auto_train_request)
        a.setdefault(role, {})["registered"] = a.get(role, {}).get("registered") or _now()
        if new:
            a[role]["trained"] = False  # 🎓待训:培训师 train-done 结业前,send 派活带警示(软闸·不硬拦,防培训师离线死锁)
        return new
    is_new = update_agents(_mut)    # 锁跨 RMW(P0):防与 register/train-done 并发覆盖
    try:
        _parked_path(role).unlink()   # 重开 hello=活跃 → 清 park/休眠标(秒回活跃·⑤)
        print("(已清除 park/休眠标——重新活跃)")
    except FileNotFoundError:
        pass
    except Exception:
        pass
    _touch_presence(role, "idle")
    pf = BUS / role / "presence.json"
    print(pf.read_text(encoding="utf-8"))
    _print_onboarding(role)
    _print_role_feed(role)
    if is_new:
        _auto_train_request(role, _load_agents().get(role) or {})   # P0 重构后 a 已不在作用域(锁内 mutator);重读取


def _print_onboarding(role):
    """打印【基本能力 onboarding】文本(事件自醒 / 待办 ask-user / 专一性 / 卡住别硬扛)。
    正常 hello 与 hello --preview 共用;纯打印、无副作用。role 可为占位符(preview 时)。"""
    abpy = os.path.abspath(__file__)
    py = sys.executable or "python"   # 绝对解释器路径,PATH-proof:后台 shell 偶发 PATH 缺 python 会让裸 `python` 启动 127→自醒掉线(Master数分/我都撞过)
    fb = '(若新 shell 提示 `ab: command not found`=PATH 未刷新,用全路径 "C:/apphome/agent_fleet/agent_bus/ab" <命令>,或兜底 "%s" "%s" <命令>)' % (py, abpy)
    print("\n【★★ 你的第一个动作·立刻照做,别往下读、也别先扎进任务 ★★】")
    print("你现在【还没在岗·收不到任何消息】。【开始任何任务之前】,先把下面这条 wait 挂成后台任务、再验在岗——【先在岗,再开工】。")
    print("就算你是被叫来干某个活的:也【先挂 wait 再干活】,否则干活期间收不到新消息、干完也没挂=直接掉线,bus 上没人联系得到你:")
    print('  ① 后台跑(交互 tab 用 run_in_background、别前台阻塞): ab wait --role %s --timeout 0   (0=自动日2h/夜6h idle·真消息仍即时唤醒)' % role)
    print('  ② 验在岗: ab selfwake   (看本 role 是否 ✓在岗;没起来就再挂+再验,直到确认)')
    print("  ③ 确认 ✓在岗后,才开始做你被交代的任务;之后每次被唤起、处理完,都要【再挂一次】wait(不重挂=掉线)。")
    print("  " + fb)
    print("  (注:hello 只登记身份+打印本说明,【不会替你把自醒跑起来】——必须你自己执行①②③。下面是完整能力说明。)")
    print("\n==== 你的基本能力(每个 agent 都该会;详见 agent_fleet/AGENTS.md + agent_fleet/BUS_ONBOARDING.md)====")
    print("\n[基本能力 1] 事件自醒(可对话又不漏消息;交互 tab 建议挂上):")
    print("把下面这条当【后台任务】跑(别前台阻塞):有人给你发消息/派活时它就完成 → 你的会话被自动唤起去处理:")
    print('  ab wait --role %s --timeout 0   (0=自动日2h/夜6h idle 空返·真消息事件仍即时唤醒接活)' % role)
    print("【铁律①】每次被唤起、处理完,必须【再挂一个】同样的 wait —— 不重挂就掉线,之后的消息收不到。")
    print("【铁律②】用短入口 `ab`(已固化【正确 env python + 真实项目根路径 + UTF-8】,免掉裸 `python`/长路径/junction/反斜杠诸坑)。" + fb)
    print("【铁律③·最重要】后台重挂会【偶发 127 静默失败】(harness 后台 shell 启动层抽风,非你的错)——")
    print("   所以重挂【发了不算数】:挂完【验证】自己在岗(跑 `ab selfwake` 看本 role 是否 ✓在岗),没起来就【再挂+再验】直到确认在岗。否则=静默掉聋。")
    print('收到 kind="notice" 的完成通知:读它 notice_of 指向的 %s/done/<id>.json 取完整结果,再 done 掉该 notice(读完即弃,勿当任务执行)。' % role)
    print("【铁律④·上下文卫生】被唤起后【若 inbox 无新活】(空唤醒/只是 ACK 通知/idle 超时)→【静默重挂 + 最多一行待命 + 绝不重新 dump selfwake/who/花名册/状态】。")
    print("   每次空醒都 dump 状态=自己给自己灌上下文→更快撑到 1M 顶→被迫 compact→失能,是失能的【自我加速器】。口诀:【空唤醒→静默重挂→不重述状态】。")
    print("\n[基本能力 2] 待办记录(要用户确认/拍板的事别只在对话里说——会滚走漏掉,用它投进待办池):")
    print('  ab ask-user --from %s --title "<要用户定的事>" --detail "<上下文/选项>"' % role)
    print("  → 落进 UI 待办池的🙋待确认队列,用户在一处统一处理、不再错过。【凡需用户拍板都走这个,是标配动作】。")
    print("\n[专一性纪律·防失能] 只做你的专长(见你的 caps/desc);【跨领域的活别自接】——")
    print('  用 `ab roster --grep <关键词>` 找对口角色转给它,不确定就问 Master/培训师 路由。')
    print("  原因:一肩挑全科会让上下文从源头就杂、越堆越乱,最终撑爆窗口→ compact 有损→ 失能退役")
    print("  (这是【业务侧某 agent】的真实教训:无分工时代被迫全科,积重难返。做减法=保专一=防失能)。")
    print("\n[基本能力 3] 卡住别硬扛——查能力池 / 请培训师补训(【是你的活、但有棘手子块】时):")
    print("  硬扛最贵(易做坏 + 烧窗口)。两条出路:")
    print('  ① 查能力池看谁会:`ab caps` / `ab roster --grep <关键词>`——若其实是别人专长,轻问或路由给他(换人做);')
    print('  ② 请培训师补训这块:`ab send --to 培训师 --from %s --task "我在做X、卡在Y这棘手块,请按能力池给我补训"`(培训师也【按需补训老人】,教我做)。' % role)
    print("  口诀:棘手 → 先查能力池 → 换人做 或 请培训师教我做,别硬扛。")


def _role_profile(role):
    """读项目根 role_profiles.json(角色页/compact hook 同一单一源)取该角色条目;失败/未注册返 None。
    路径经 cfg agent_fleet_home(换机只改 config.json),回退按物理文件位置推(resolve 穿 junction 落真实家)。"""
    try:
        home = cfg("agent_fleet_home") or str(Path(__file__).resolve().parents[2])
        data = json.loads(Path(home, "role_profiles.json").read_text(encoding="utf-8"))
        for r in data.get("roles", []):
            if r.get("role") == role:
                return r
    except Exception:
        pass
    return None


def _load_caps_pool():
    try:
        return json.loads((BUS / "capabilities.json").read_text(encoding="utf-8")).get("capabilities", {})
    except Exception:
        return {}


def _print_role_feed(role):
    """hello 即喂【角色页 + 能力池】(2026-07-28 用户令:机制上防"老角色的新会话空脑上岗"——
    实证:MasterUI 新会话首单把 WebUI 重启到 5678 且假阳性验证,根因=角色操作知识只在 compact 注入、上岗时没人喂)。
    数据同源 role_profiles.json + capabilities.json(与 WebUI 角色页/compact hook 同一份);纯打印无副作用。"""
    prof = _role_profile(role)
    print("\n==== 你的角色页(role_profiles.json 单一源)——【先读完并走完启动流程,再开始领活】 ====")
    if prof:
        print("【定位】" + (prof.get("one_liner") or ""))
        if prof.get("mode"):
            print("【形态】" + prof["mode"])
        if prof.get("boundary"):
            print("【边界】" + prof["boundary"])
        flow = prof.get("startup_flow") or []
        if flow:
            print("【启动流程·逐条走完才算上岗】")
            for s in flow:
                print("  " + s)
    else:
        print("(你不在角色注册表 role_profiles.json——新角色:先请培训师带训、补注册表条目,再开始接活;跨领域活别自接。)")
    rec = _load_agents().get(role) or {}
    caps = rec.get("caps") or []
    if caps:
        pool = _load_caps_pool()
        print("【能力池·你持有的 caps 与口径】")
        for c in caps:
            print("  - %s:%s" % (c, (pool.get(c) or {}).get("desc", "(池中无此能力描述)")))
        print("  (能力模块详解按需向培训师要,对应 BUS_ONBOARDING.md 各节)")
    # 角色记忆索引指针(记忆随角色走·2026-07-28 用户令:CC 记忆按 cwd 定位=同 cwd 多角色共锅,
    # 索引按角色分锅 <role>.MEMORY.md,根 MEMORY.md=路由器+公共段)
    try:
        slug = re.sub(r"[:\\/_. ]", "-", os.getcwd())
        pot = Path.home() / ".claude" / "projects" / slug / "memory"
        ri = pot / (role + ".MEMORY.md")
        if ri.exists():
            print("【角色记忆索引】读 %s(上岗/压后必读;你的新记忆也写这份,全角色公共纪律在同目录根 MEMORY.md)" % ri)
        elif (pot / "MEMORY.md").exists():
            print("【记忆索引】读 %s(本记忆库未分角色索引,读根索引)" % (pot / "MEMORY.md"))
    except Exception:
        pass
    if rec.get("trained") is False:
        print("【🎓待训期】你是新注册角色、尚未结业(已自动通知培训师带训):待训期间只接简单明确的活;"
              "运维/写库/改基建类复杂活等培训师 `ab train-done --role %s` 结业后再接。" % role)
    # boot 完备自检(2026-07-28 用户案:新会话 hello 后没走完启动流程就开始接话,被问"加载了吗"
    # 才发现指定必读文件没读——喂入起效了(它能列出缺什么),缺口在时序。此处把时序也定死。)
    print("【⛔ boot 未完成=不算上岗】上面 启动流程/必读文件/角色记忆索引 逐条走完之前:不领活、"
          "不答业务问题(用户插话也先答'boot 中,还差X'再补完)。")
    print("【机器自验·2.3】读完必读后跑 `ab boot-check --role %s`——它按你本会话的真实 Read 记录核验"
          "必读清单(不认自述),✅装载完成才开始领活;✗会列出还没读的,逐个读完重跑直到 ✅。" % role)


def _auto_train_request(role, rec):
    """新角色首次 hello → 自动给培训师投带训单(培训闸·软闸:派活仍可达,send 对未结业目标带警示)。"""
    try:
        _role_dir("培训师")
        tid = _gen_id("培训师")
        task = {
            "id": tid, "from": role, "to": "培训师",
            "title": "[自动·新人带训] %s 首次上岗" % role,
            "task": ("新角色 %s 刚 hello 注册(caps=%s),按 BUS_ONBOARDING.md 流程带训:先握手验自醒三步闭环、"
                     "再按 caps 只发对应能力模块;完成后跑 `ab train-done --role %s` 结业(解除待训警示)。"
                     % (role, ",".join(rec.get("caps") or []) or "未配·请顺带按最小授权配 caps", role)),
            "created": _now(), "status": "pending",
            "no_receipt": True,   # 机制单·fire-and-forget:完成不回执,防新人 inbox 被机制回执噪声灌
        }
        _write_json(BUS / "培训师" / "inbox" / (tid + ".json"), task)
        _audit("train_request", role=role)
        print("(已自动给培训师投带训单:%s)" % tid)
    except Exception as e:
        sys.stderr.write("(带训单投递失败:%s——请手动 `ab send --to 培训师` 请求带训)\n" % e)


def cmd_train_done(args):
    """培训师结业命令:置 agents.json[role].trained=true,解除 send 的待训警示(培训闸闭环)。
    配套:新角色首次 hello 自动标 trained=false + 投带训单给培训师(_auto_train_request)。"""
    role = args.role
    by = args.by or os.environ.get("AGENTBUS_ROLE") or "培训师"
    def _mut(a):
        if role not in a:
            return False
        a[role]["trained"] = True
        a[role]["trained_at"] = _now()
        a[role]["trained_by"] = by
        return True
    if not update_agents(_mut):     # 锁跨 RMW(P0)
        sys.stderr.write("角色 %s 不在册(agents.json),无法结业。\n" % role)
        sys.exit(2)
    _audit("train_done", role=role, by=by)
    from bus.fleetlog import fleet_log
    fleet_log("train", "%s 结业" % role, "trained=true by %s" % by, actor=by)
    print("🎓 %s 已结业(trained=true · by %s)。send 派活不再带待训警示。" % (role, by))


def cmd_whoami(args):
    """反查【本会话/进程】绑定的 role(同 cwd 多 role 时不靠 cwd 猜):
    ① 按 env CLAUDE_CODE_SESSION_ID 匹配各 role presence.session_id(权威);
    ② fallback 按 claude_pid(本进程的 claude 祖先 pid)匹配;
    查到→打印 role(exit 0);查不到→空 + exit 2(=真新未绑定 tab,恢复入口据此才默认 MasterAgent)。
    用途:新 CLI 恢复入口/PostCompact hook 先 whoami 认清真身份。"""
    sid = os.environ.get("CLAUDE_CODE_SESSION_ID")
    roles = _list_roles()
    def _pres(r):
        pf = BUS / r / "presence.json"
        try:
            return json.loads(pf.read_text(encoding="utf-8")) if pf.exists() else None
        except Exception:
            return None
    hit = None; how = None
    if sid:
        for r in roles:
            pr = _pres(r)
            if pr and pr.get("session_id") == sid:
                hit = r; how = "session_id"; break
    if not hit:
        anc = _ancestor_pids()
        for r in roles:
            pr = _pres(r)
            if pr and pr.get("claude_pid") in anc:
                hit = r; how = "claude_pid(祖先)"; break
    if getattr(args, "json", False):
        print(json.dumps({"role": hit, "matched_by": how, "session_id": sid}, ensure_ascii=False))
    else:
        print(hit or "")
    if not hit:
        sys.exit(2)


def cmd_selfwake(args):
    """自醒检测:列出每个 role 的事件自醒是否在岗(daemon watchdog / 会话后台 wait)。"""
    from bus.fleetlog import maint_active, maint_info
    if maint_active():
        _mi = maint_info()
        print("🔧 维修模式进行中(since %s·%s)——停机 agent 的⚠掉线为预期态;守卫已休眠。" % (_mi.get("since", "?"), _mi.get("note") or "-"))
    armed = recent = stale = off = retired = pseudo = parked = 0
    wait_roles = _wait_proc_roles()   # 一次性预扫,循环内复用
    retired_set = _retired_roles()
    print("%-14s %-8s %s" % ("role", "自醒", "说明"))
    print("-" * 78)
    for role in _list_roles():
        plabel = _pseudo_role_label(role)
        if plabel:                     # 伪角色/系统桶:非执行体,不计掉线/未醒,单列标注
            print("%-14s %-8s %s" % (role, "◇伪角色", plabel))
            pseudo += 1; continue
        if role in retired_set:        # 退役:不计掉线(否则误报),只标注
            sb = _load_agents().get(role, {}).get("succeeded_by")
            print("%-14s %-8s %s" % (role, "⊘退役", "已退役" + ("·接替=" + sb if sb else "") + "(留痕不删,不派活)"))
            retired += 1; continue
        st, detail = _self_wake_state(role, wait_roles=wait_roles)
        mark = {"armed": "✓在岗", "recent": "◐待确认", "stale": "⚠掉线", "off": "—未醒", "parked": "🅿️休眠", "polling": "⏱轮询"}.get(st, "?")
        print("%-14s %-8s %s" % (role, mark, detail))
        armed += st == "armed"; recent += st == "recent"; stale += st == "stale"; off += st == "off"; parked += st == "parked"
    print("-" * 78)
    print("自醒在岗 %d / 待确认 %d / 掉线 %d / 未醒 %d / 休眠 %d / 退役 %d / 伪角色 %d" % (armed, recent, stale, off, parked, retired, pseudo))
    if recent:
        print("◐待确认=有心跳无wait进程(刚fire待重挂/交互中·瞬时非告警);持续则下轮转掉线。")
    if stale:
        print("⚠ 掉线需重挂:daemon→`supervise`/`start_worker`;协调位会话→重新挂后台 `wait --role <自己>`。")


def _guard_prev_path():
    return BUS / ".selfwake_guard_prev.json"


def _load_guard_prev():
    """上一轮巡检判 stale 的 role 集 + 时间戳(用于连续2次确认降噪)。"""
    p = _guard_prev_path()
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return set(d.get("roles", [])), d.get("ts")
    except Exception:
        return set(), None


def _save_guard_prev(roles):
    try:
        tmp = _guard_prev_path().with_suffix(".json.tmp")
        tmp.write_text(json.dumps({"ts": time.time(), "roles": sorted(roles)}, ensure_ascii=False),
                       encoding="utf-8")
        os.replace(tmp, _guard_prev_path())
    except Exception:
        pass


def cmd_selfwake_guard(args):
    """【B·自醒巡检报警·2026-07-08 Master拍板】周期扫自醒,发现掉自醒(claude活/无wait/心跳超期)的
    agent → ①往其 inbox 投一条【重挂提醒】(dedup:已有未领的提醒就不重投)②WinRT toast 报警(汇总)。
    只报警+提醒,不代挂(外部无法替前台CLI重挂,见方案评估)。--dry-run 只报不动。
    周期驱动:可由 WebUI 常驻线程 / cron 每几分钟跑一次,把"18h没人发现"变"几分钟内报警"。"""
    from bus.fleetlog import maint_active, maint_info
    if maint_active():   # 🔧 维修模式(用户 2026-07-28):守卫休眠——停机 agent 大片"掉线"=预期,不报警不投提醒不 toast
        i = maint_info()
        print("🔧 维修模式进行中(since %s·%s)→ 自醒守卫休眠,本轮不巡检。`ab maint --off` 结束后恢复。"
              % (i.get("since", "?"), i.get("note") or "-"))
        return
    wait_roles = _wait_proc_roles()
    retired_set = _retired_roles()
    stale = []
    for role in _list_roles():
        if _pseudo_role_label(role) or role in retired_set or _is_parked(role) or _is_guard_exempt(role):
            continue                          # parked=故意暂离 / guard_exempt=轮询模式在岗 → 不巡检报警
        st, detail = _self_wake_state(role, wait_roles=wait_roles)
        if st == "stale":
            stale.append((role, detail))
    # 2-scan 确认降噪(2026-07-08):cycling agent 在 post-fire 无-wait 瞬间窗口会被误判 stale(wait 不刷
    # 心跳→>1h没re-arm的健康cycling者也显stale)。只对【连续2次扫描都掉线】的报警=瞬时假阳性(1次)被滤掉、
    # 真掉线(持续)照报。代价:检测延到~2轮(~6min),仍远好于原来靠人偶然发现。
    cur = {r for r, _ in stale}
    prev, prev_ts = _load_guard_prev()
    prev_fresh = prev_ts is not None and (time.time() - prev_ts) < 900  # 仅15min内的上轮基线算数
    confirmed = (cur & prev) if prev_fresh else set()
    _save_guard_prev(cur)
    unconfirmed = cur - confirmed
    if not stale:
        print("✓ 自醒巡检:全员在岗,无掉自醒"); return
    if not confirmed:
        print("自醒巡检:%d 疑掉线,均【首次/未连续确认】→本轮不报(待下轮确认,消瞬时假阳性):%s"
              % (len(stale), "、".join(sorted(cur))))
        return
    reminded, skipped = [], []
    marker = "[自醒守卫·请重挂]"
    stale_confirmed = [(r, d) for r, d in stale if r in confirmed]
    for role, detail in stale_confirmed:
        inbox = BUS / role / "inbox"
        # dedup:该 role inbox 已有未领的守卫提醒 → 跳过不重投
        dup = False
        try:
            for f in inbox.glob("*.json"):
                try:
                    if marker in (json.loads(f.read_text(encoding="utf-8")).get("title", "") or ""):
                        dup = True; break
                except Exception:
                    continue
        except Exception:
            pass
        if dup:
            skipped.append(role); continue
        if args.dry_run:
            reminded.append(role); continue
        tid = _gen_id(role)
        task = {
            "id": tid, "from": "MasterAgent", "to": role,
            "title": marker + " 你掉了自醒",
            "task": ("自醒巡检【连续2次】发现你掉了自醒(%s;尸检:%s)。看到即请【后台 run_in_background】跑 "
                     "`ab wait --role %s --timeout 0`(0=自动日2h/夜6h),再 `ab selfwake` 验在岗(别用 shell &)。"
                     "★禁轮询(用户令2026-07-28:轮询=错误·全员事件 wait;定时只允许闹钟式到点一次醒)。"
                     % (detail, _wait_autopsy(role), role)),
            "created": _now(), "status": "pending",
            "no_receipt": True,   # 系统 nudge·fire-and-forget:完成不回执给 from(MasterAgent),消守卫回执洪水噪声
        }
        _write_json(inbox / (tid + ".json"), task)
        reminded.append(role)
    _audit("selfwake_guard", stale=len(stale), confirmed=len(confirmed), reminded=len(reminded))
    if not args.dry_run and reminded:
        _fire_toast("⚠ %d 个 agent 掉自醒(连续2次确认)" % len(confirmed),
                    "已提醒重挂:" + "、".join(reminded) + ("(另%d已提醒过)" % len(skipped) if skipped else ""))
    print("⚠ 自醒巡检:%d 掉线(%d 连续确认 / %d 首次待下轮) → %s%d 已投重挂提醒%s%s" % (
        len(stale), len(confirmed), len(unconfirmed), ("[dry-run] " if args.dry_run else ""), len(reminded),
        (" · %d 已提醒过跳过" % len(skipped)) if skipped else "",
        "" if args.dry_run else " + toast报警"))
    for role, detail in stale:
        print("   - %s%s:%s | 尸检:%s" % (role, "" if role in confirmed else "(待确认)", detail, _wait_autopsy(role)))


def _wait_autopsy(role):
    """掉线尸检分类(2026-07-28 会话可靠性立项):读 <role>/wait_trace.json(_serve_wait 进场记 start、
    干净退场补 exit)把"掉了"升级为"怎么掉的"——根因分布从此有数据,不再靠猜:
    · exit=None       → wait 进程【异常死亡】(被杀/机器休眠丢/宿主壳回收)= 环境问题,非纪律
    · exit=idle/task  → wait 正常退出后【没重挂】= 纪律缺口(fire→重挂三步没走完)
    · 无 trace        → 旧版 wait 或从未挂过(观察期)"""
    try:
        t = json.loads((BUS / role / "wait_trace.json").read_text(encoding="utf-8"))
    except Exception:
        return "无 trace(旧版 wait/从未挂过)"
    if t.get("exit") is None:
        return "wait 进程异常死亡(start=%s 无 exit·被杀/休眠丢/壳回收=环境因)" % (t.get("started") or "?")[:19]
    return "上次 %s 正常退出(%s)后未重挂=纪律缺口" % (t.get("exit"), (t.get("exited") or "?")[:19])


def cmd_selfwake_exempt(args):
    """【轮询模式在岗·自愿豁免自醒巡检】给 role 放/撤 guard_exempt marker。
    用于 ScheduleWakeup 定时 drain(非 background wait·心跳必陈旧)的 agent:声明后守卫不再判其掉线/发[请重挂],
    selfwake/who 显示 ⏱轮询。与 park 区别=exempt 仍在岗收任务(任务 bus 排队·它定时 drain 取)、park 是暂离不收活。
    --off 撤销(回到正常事件自醒巡检)。marker=<role>/guard_exempt.json,外部可写=agent 自行声明。
    ★裸跑(无 --role)=【只读列出】当前豁免者(防手滑自豁免:本命令会改状态,与只读的 `ab selfwake` 易混·
    使用方 2026-07-16 踩坑);要放/撤豁免【必须显式 --role】(不再默认当前角色·静默改自己状态)。"""
    if not args.role:
        # 只读:列当前豁免者(裸跑安全·不改任何状态)
        ex = [(r, _is_guard_exempt(r)) for r in _list_roles()]
        ex = [(r, m) for r, m in ex if m]
        if not ex:
            print("当前无豁免巡检的 role。放豁免(轮询模式在岗者):ab selfwake-exempt --role <角色> [--mode polling] [--note ..]"); return
        print("巡检豁免中(⏱轮询·%d):" % len(ex))
        for r, m in ex:
            print("  %-14s mode=%s since=%s %s" % (r, m.get("mode", "?"), (m.get("since", "") or "")[:19],
                                                    ("· " + m["note"]) if m.get("note") else ""))
        print("撤销:ab selfwake-exempt --role <角色> --off")
        return
    role = args.role
    if not (BUS / role).is_dir():
        sys.stderr.write("角色 %r 无 bus 目录(未 hello/register)——不能对不存在的角色放/撤豁免。先 `ab hello --role %s` 或 `ab register --role %s`。\n" % (role, role, role))
        sys.exit(2)
    p = _guard_exempt_path(role)
    if args.off:
        try:
            p.unlink(); print("已撤销 %s 的巡检豁免(回到正常事件自醒巡检)。" % role)
        except FileNotFoundError:
            print("%s 本无豁免 marker,无需撤销。" % role)
        _audit("selfwake_exempt_off", role=role)
        return
    rec = {"mode": args.mode or "polling", "reason": args.reason or "", "note": args.note or "",
           "since": _now()}
    _write_json(p, rec)
    _audit("selfwake_exempt", role=role, mode=rec["mode"])
    print("已给 %s 放巡检豁免 marker(mode=%s)。守卫不再判其掉线;selfwake/who 显示 ⏱轮询。撤销:ab selfwake-exempt --role %s --off"
          % (role, rec["mode"], role))
