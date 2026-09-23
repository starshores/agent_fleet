# -*- coding: utf-8 -*-
"""bus.edge —— 【别名层·分离 A1·2026-09-23】resource="edge" 的共享资源互斥。

算法已迁 bus.lock 通用化(任何域的独占资源同一原语 `ab lock acquire/release --resource <name>`);
本模块只保命令面(edge-acquire / edge-release)与读方名字(_edge_holder / _edge_lock_path·status/health 用),
输出 JSON 与旧实现 byte-identical。过渡期 edge 锁仍落 BUS/edge.lock(A4 迁 locks/ 后本模块可删)。
"""
import json

from bus.lock import lock_path as _lock_path, lock_holder as _lock_holder, acquire as _acquire, release as _release
from bus.lock import _role_proc_alive, _wait_file_gone, _stale as _edge_stale   # noqa: F401  旧名 re-export(agentbus 契约·re-export 盲点纪律)


def _edge_lock_path():
    return _lock_path("edge")


def _edge_holder():
    return _lock_holder("edge")


def cmd_edge_acquire(args):
    print(json.dumps(_acquire("edge", args.who, args.timeout, args.ttl), ensure_ascii=False))


def cmd_edge_release(args):
    print(json.dumps(_release("edge", args.who, args.force), ensure_ascii=False))
