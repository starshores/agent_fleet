# -*- coding: utf-8 -*-
# [A3 转发桩] 分离 A3(2026-09-23·Master数分 K1):本文件已外迁业务侧 biz_tools/external/(自带 git)。此桩只做 __main__ 转发(不可 import 复用),
# 供仍按旧扁平路径调用的业务记忆/脚本过渡;业务侧引用改完(grep 旧路径=0)后删桩。物理位:cfg biz_tools > C:/apphome/agent_fleet_data/biz_tools。
import json, os, runpy, sys
if __name__ != "__main__":
    raise ImportError("external_read.py 已外迁 biz_tools/external·桩不可 import;请从新位导入")
def _target():
    here = os.path.dirname(os.path.abspath(__file__))
    try:
        base = json.load(open(os.path.join(here, "config.json"), encoding="utf-8")).get("biz_tools")
    except Exception:
        base = None
    base = os.environ.get("AGENTBUS_BIZ_TOOLS") or base or r"C:\apphome\agent_fleet_data\biz_tools"
    return os.path.join(base, "external", os.path.basename(__file__))
_t = _target()
if not os.path.exists(_t):
    sys.stderr.write("[fatal] 业务工具未安装:%s 不存在(业务侧 biz_tools 已清除或未迁入)\n" % _t); sys.exit(2)
sys.argv[0] = _t; sys.path.insert(0, os.path.dirname(_t))
runpy.run_path(_t, run_name="__main__")
