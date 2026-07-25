# -*- coding: utf-8 -*-
"""会话跟踪 — 工具调用即时落盘（kill 安全，无缓冲丢失）。

旧实现：调用先入内存缓冲，靠「5 分钟窗口跨越」或进程退出 atexit 才落盘。
缺陷：宿主用 `Stop-Process -Force`（Windows TerminateProcess）重启进程时
atexit 不触发，最后一段（<300s 窗口内）调用记录静默丢失 —— 表现为日志断流。

新实现：每次 record_call 即时 append 落盘（write-through），进程被强杀也不会
丢任何已发生的调用记录。同时保留 atexit / SIGTERM 兜底以覆盖正常退出场景。
"""
import time as _ltime
import uuid as _luuid
import json
import os
import atexit
import signal
from datetime import datetime

_ = (_ltime,)  # 保留引用，避免未使用告警


class SessionTracker:
    """记录工具调用活动到 logs/tool_sessions.jsonl（write-through）。"""

    def __init__(self):
        vault = os.environ.get("LINGTAI_VAULT", r".")
        logs_dir = os.path.join(vault, ".tool", "lingtai-kb", "logs")
        os.makedirs(logs_dir, exist_ok=True)
        self.log_path = os.path.join(logs_dir, "tool_sessions.jsonl")
        # 端标识：默认从环境变量取（宿主 MCP 配置可设 LINGTAI_CLIENT_ID 强制打标），
        # 否则由 router 在 initialize 时调 set_client() 覆盖为真实客户端名
        self._client = os.environ.get("LINGTAI_CLIENT_ID", "unknown")
        atexit.register(self._flush)
        # 优雅退出兜底：捕获 SIGTERM 强制落盘（对 Stop-Process -Force 无效，
        # 但覆盖 POSIX 正常终止 / 宿主发 SIGTERM 的场景）。包在 try 里避免
        # 非主线程或平台限制导致注册失败而中断启动。
        try:
            signal.signal(signal.SIGTERM, lambda *_a: self._flush())
        except (ValueError, OSError, AttributeError):
            pass

    def set_client(self, client: str):
        """由 router 在 MCP initialize 时调用，覆盖默认端标识。"""
        if client:
            self._client = str(client)

    def record_call(self, tool_name: str, data_chars: int = 0, client: str = None, outcome: str = "success"):
        """记录一次工具调用并即时落盘（write-through，无缓冲丢失）。"""
        client = client or self._client
        entry = {
            "session_id": _luuid.uuid4().hex[:12],
            "client": client,
            "tool_calls": [{"name": tool_name, "data_chars": int(data_chars or 0)}],
            "outcome": outcome,
            "summary": f"1 次工具调用（{client}）",
            "total_data_chars": int(data_chars or 0),
            "timestamp": datetime.now().isoformat(),
        }
        try:
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
                f.flush()
        except OSError:
            pass

    def _flush(self):
        """兼容旧调用点（system.py restart 在 os._exit 前调 _flush）。
        write-through 后已无内存缓冲，这里为空实现。"""
        pass


_session_logger = SessionTracker()


def record(tool_name: str, data_chars: int = 0, client: str = None, outcome: str = "success"):
    """脚本 / 非 router 路径的便捷记录入口。"""
    _session_logger.record_call(tool_name, data_chars, client, outcome)
