# -*- coding: utf-8 -*-
"""
合并写（Coalesced JSON Writer）
================================
消除灵台 MCP 写路径的"整库 json.dumps 重写放大"问题：

原状：content_registry / memory_bank / observation_engine 每次 register/write/save
都把整个 JSON（registry 1.3MB / memories 104KB / observations 11MB）全量 dump 到磁盘。
observation_* 每次调用重写 11MB → 单次 ~190ms，且随数据线性增长。

本模块把"在短时间内发生的多次写"合并为一次落盘：
- 调用方用 coalesced_dump(path, data) 提交最新数据 + 标记 dirty；
- 一个后台守护线程在 DEFAULT_INTERVAL 秒后（或显式 force）执行一次原子写（tmp + os.replace）；
- 进程退出时 atexit 兜底 flush，避免 <interval 窗口内数据丢失。

取舍：牺牲 <interval 秒的写耐久性（进程崩溃时该窗口内未落盘的写入可能丢失），
换取查询/写入路径的显著加速。对观察/记忆/注册表这类可重建/可重算数据是可接受的。
"""
import os
import json
import threading
import atexit

_writers = {}          # path -> {"data":..., "indent":..., "dirty":..., "timer":...}
_lock = threading.Lock()
DEFAULT_INTERVAL = 0.5  # 秒：合并窗口，超过即落盘


def coalesced_dump(path, data, indent=2, force=False, interval=DEFAULT_INTERVAL):
    """提交一次 JSON 写请求。重复调用（同 path）在窗口内合并为一次落盘。"""
    path = str(path)
    with _lock:
        entry = _writers.get(path)
        if entry is None:
            entry = {"data": data, "indent": indent, "dirty": True, "timer": None, "path": path}
            _writers[path] = entry
        else:
            entry["data"] = data
            entry["indent"] = indent
            entry["dirty"] = True
        if force:
            _flush_locked(path, entry)
            return
        if entry["timer"] is not None:
            entry["timer"].cancel()
        entry["timer"] = threading.Timer(interval, _flush_timer_cb, args=(path,))
        entry["timer"].daemon = True
        entry["timer"].start()


def _flush_timer_cb(path):
    with _lock:
        entry = _writers.get(path)
        if entry is None:
            return
        _flush_locked(path, entry)


def _flush_locked(path, entry):
    if not entry["dirty"]:
        return
    try:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(entry["data"], f, ensure_ascii=False, indent=entry["indent"])
        os.replace(tmp, path)
    except Exception:
        pass
    entry["dirty"] = False
    if entry["timer"] is not None:
        entry["timer"].cancel()
        entry["timer"] = None


def flush_all():
    """立即落盘所有待写。用于进程退出兜底与显式强制刷新。"""
    with _lock:
        for path, entry in list(_writers.items()):
            _flush_locked(path, entry)


atexit.register(flush_all)
