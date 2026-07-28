# -*- coding: utf-8 -*-
"""external_common.py — EDGE CDP 云文档工具族公共件(P2 收敛·2026-07-28)。

## edge 焦点互斥锁 _lock(原四份逐字复制体收敛:external_canvas / external_msg / external_read / external_sheet_read)
acquire 失败【硬退出 exit 3】(P0 定):edge-acquire 恒 exit 0、结果在 stdout JSON,
只捕异常不看结果=抢锁失败照操作 Edge=互斥形同虚设=串台病根。release 尽力而为只 warn。

## 焦点策略成文(消"自相矛盾"的文档漂移·P2)
- **理想态 = 焦点-free**(external_canvas 已证):CDP 的 element.click / evaluate / keyboard 是
  页面级注入、不是 OS 输入注入——【不 bring_to_front】即可后台驱动,不弹 Edge、不抢用户焦点。
  新工具一律按此写。
- **成文豁免**:依赖【系统剪贴板】或【OS 级键序】的路径(external_msg 发消息的粘贴流、external_sheet_read 的
  Ctrl+C 选区复制)——剪贴板/按键归前台窗口所有,不 bring_to_front 读不到/打不进,
  故这两处的 bring_to_front 是【已知必要例外】,且全程持 edge 锁(用户侧感知=Edge 短暂前台)。
  去豁免的路径 = 改用 CDP DOM 读取(如 external_canvas 截图+视觉读值)替代剪贴板,future 逐个迁移。
"""
import json as _json
import os as _os
import subprocess as _subprocess
import sys as _sys

_HERE = _os.path.dirname(_os.path.abspath(__file__))


def edge_lock(action, who):
    """edge 焦点互斥锁。action='acquire'(失败 exit 3 禁继续)/'release'(尽力而为)。"""
    try:
        r = _subprocess.run([_sys.executable, _os.path.join(_HERE, "agentbus.py"),
                             f"edge-{action}", "--who", who] + (["--timeout", "60"] if action == "acquire" else []),
                            capture_output=True, text=True, timeout=70)
        if action == "acquire":
            ok = False
            try:
                ok = bool(_json.loads((r.stdout or "").strip().splitlines()[-1]).get("acquired"))
            except Exception:
                ok = False
            if not ok:
                print(f"[fatal] edge-acquire 抢锁失败(被占/超时/异常)→ 禁止无锁操作 Edge,退出。"
                      f"稍后重试或 `ab edge-status` 看持有者。stdout={(r.stdout or '').strip()[:200]}", file=_sys.stderr)
                _sys.exit(3)
    except SystemExit:
        raise
    except Exception as e:
        if action == "acquire":
            print(f"[fatal] edge-acquire 调用异常:{e} —— 抢锁失败禁继续(P0),退出。", file=_sys.stderr)
            _sys.exit(3)
        print(f"[warn] edge-{action} 异常: {e}", file=_sys.stderr)
