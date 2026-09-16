#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""ui.page_js —— WebUI 应用 JS 段·装配入口(自我迭代波·案① 拆分·2026-09-16)。
U4 曾把 page 拆 css/body/js;本次把 181KB 单体 page_js 再按功能域拆 8 子模块
(morphdom vendor + core/bi/caps/panels_a/panels_b/tabs/tail),此文件只装配。
契约铁律:纯搬运·装配结果与拆前【sha256 字节一致】(Master数分 L级B 亲验:new page_js.JS
== 冻结基线 543965c3·逐字节=0)。装配=纯 concat 按原书写序拼接(零加料·无分隔符);
page.py `from page_js import JS` 照旧不动。"""
from page_js_morphdom import MORPHDOM
from page_js_core import CORE
from page_js_bi import BI
from page_js_caps import CAPS
from page_js_panels_a import PANELS_A
from page_js_panels_b import PANELS_B
from page_js_tabs import TABS
from page_js_tail import TAIL

# 装配序 = 原书写序(顶到底)·纯 join 零加料 → JS 与拆前字节一致
JS = "\n".join([MORPHDOM, CORE, BI, CAPS, PANELS_A, PANELS_B, TABS, TAIL])
