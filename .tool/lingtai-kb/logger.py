# -*- coding: utf-8 -*-
"""
灵台 MCP Server 结构化日志
=========================
统一日志出口，替代散落的 print(..., file=sys.stderr) 和 silent except pass。

用法：
    from logger import get_logger
    log = get_logger(__name__)
    log.warning("index load failed", exc_info=True)
    log.debug("cache hit", extra={"key": keyword})

输出格式（stderr，JSON per line）：
    {"ts":"08:30:01","level":"WARNING","module":"memory_engine","msg":"index load failed","exc":"..."}

环境变量：
    LINGTAI_LOG_LEVEL: 日志级别（默认 WARNING，开发时设 DEBUG）
    LINGTAI_LOG_PLAIN: 设为 1 时输出人类可读格式（非 JSON）
"""
import json
import logging
import os
import sys
from datetime import datetime


class JsonFormatter(logging.Formatter):
    """单行 JSON 日志，写入 stderr（不污染 MCP stdout 协议流）"""

    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "ts": datetime.fromtimestamp(record.created).strftime("%H:%M:%S"),
            "level": record.levelname,
            "module": record.name.split(".")[-1],
            "msg": record.getMessage(),
        }
        if record.exc_info and record.exc_info[0]:
            entry["exc"] = self.formatException(record.exc_info)[:300]
        return json.dumps(entry, ensure_ascii=False, separators=(",", ":"))


class PlainFormatter(logging.Formatter):
    """人类可读格式（开发调试用）"""

    def format(self, record: logging.LogRecord) -> str:
        ts = datetime.fromtimestamp(record.created).strftime("%H:%M:%S")
        mod = record.name.split(".")[-1]
        msg = f"[{ts}] {record.levelname:7s} {mod}: {record.getMessage()}"
        if record.exc_info and record.exc_info[0]:
            msg += f"\n  {self.formatException(record.exc_info)[:200]}"
        return msg


_initialized = False


def _init_root():
    """初始化根 logger（只执行一次）"""
    global _initialized
    if _initialized:
        return
    _initialized = True

    level_name = os.environ.get("LINGTAI_LOG_LEVEL", "WARNING").upper()
    level = getattr(logging, level_name, logging.WARNING)
    plain = os.environ.get("LINGTAI_LOG_PLAIN", "") == "1"

    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(PlainFormatter() if plain else JsonFormatter())

    root = logging.getLogger("lingtai")
    root.setLevel(level)
    root.addHandler(handler)
    root.propagate = False


def get_logger(name: str) -> logging.Logger:
    """获取模块级 logger。name 通常传 __name__。"""
    _init_root()
    # 将 server_mixins.perception → lingtai.perception 简化
    short = name.split(".")[-1] if "." in name else name
    return logging.getLogger(f"lingtai.{short}")
