#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""ui.page —— WebUI 前端页面装配(BUS 2.0 P4 建 · UI2.0 U4 拆分 · 2026-07-29)。

P4 时为单文件 ~2875 行常量;U4 按 <style>/<body>/<script> 三分为 page_css / page_body / page_js,
本文件只装配。契约不变:routes.py `from page import INDEX_HTML` 照旧;拆分=纯搬运,
装配结果与拆前【sha256 字节一致】(拆分脚本断言 + ui_smoke 锚回归网)。
"""
from page_css import CSS
from page_body import BODY
from page_js import JS

INDEX_HTML = (r"""<!doctype html>
<html lang="zh-CN"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Agent Bus 控制台</title>
<style>
"""
              + CSS + """</style></head>
<body>
"""
              + BODY + """<script>
"""
              + JS + """</script>
</body></html>""")
