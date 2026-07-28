# -*- coding: utf-8 -*-
"""会话跟踪 — 工具调用即时落盘 + Session Broker 活跃会话管理

职责分层：
  SessionTracker — 日志落盘（write-through，kill 安全）
  SessionBroker  — 活跃会话注册表 + 心跳 + 过期检测 + 跨端感知

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
import threading
from datetime import datetime, timedelta

_ = (_ltime,)  # 保留引用，避免未使用告警

_SESSION_TTL = timedelta(minutes=5)  # 5 分钟无活动视为过期


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


# ═══════════════════════════════════════════
# Session Broker — 活跃会话注册表
# ═══════════════════════════════════════════


class SessionBroker:
    """活跃会话注册表：心跳 + 过期检测 + 跨端感知。

    每个 MCP initialize 注册一个会话，record_call 刷新心跳，
    5 分钟无活动自动标记为 stale。"""

    def __init__(self, ttl: timedelta = _SESSION_TTL):
        self._ttl = ttl
        self._sessions: dict = {}  # session_id → session_info
        self._lock = threading.Lock()

    def register(self, client: str, version: str = "", session_id: str = "") -> str:
        """注册一个新的会话，返回 session_id。
        如果同一个 client 已有活跃会话，复用旧 session_id 并刷新心跳。"""
        sid = session_id or _luuid.uuid4().hex[:12]
        now = datetime.now()
        with self._lock:
            # 同 client 复用：已有活跃会话则刷新，否则新建
            existing = self._find_active_client(client)
            if existing:
                sid = existing["session_id"]
                existing["last_active"] = now
                existing["version"] = version or existing.get("version", "")
                existing["tool_count"] = existing.get("tool_count", 0)
                return sid
            self._sessions[sid] = {
                "session_id": sid,
                "client": client,
                "version": version,
                "first_seen": now,
                "last_active": now,
                "tool_count": 0,
            }
        return sid

    def heartbeat(self, client: str, session_id: str = ""):
        """刷新会话心跳。session_id 为空时按 client 查找。"""
        now = datetime.now()
        with self._lock:
            target = self._sessions.get(session_id) if session_id else self._find_active_client(client)
            if target:
                target["last_active"] = now
                target["tool_count"] = target.get("tool_count", 0) + 1

    def _find_active_client(self, client: str) -> dict:
        """按 client 名查找活跃会话（不包含 stale）。"""
        cutoff = datetime.now() - self._ttl
        for sid, info in list(self._sessions.items()):
            last = info.get("last_active")
            if last and last < cutoff:
                continue  # stale
            if info.get("client") == client:
                return info
        return None

    def status(self) -> dict:
        """返回当前会话状态：活跃 / 过期 / 按端聚合。"""
        now = datetime.now()
        cutoff = now - self._ttl
        active = []
        stale = []
        client_summary = {}

        with self._lock:
            for sid, info in list(self._sessions.items()):
                last = info.get("last_active")
                is_active = last and last >= cutoff
                entry = {
                    "session_id": sid,
                    "client": info.get("client", "unknown"),
                    "version": info.get("version", ""),
                    "first_seen": info["first_seen"].isoformat() if info.get("first_seen") else "",
                    "last_active": last.isoformat() if last else "",
                    "tool_count": info.get("tool_count", 0),
                }
                if is_active:
                    entry["status"] = "active"
                    active.append(entry)
                else:
                    entry["status"] = "stale"
                    stale.append(entry)

                c = info.get("client", "unknown")
                if c not in client_summary:
                    client_summary[c] = {"tools_called": 0, "last_seen": ""}
                client_summary[c]["tools_called"] += info.get("tool_count", 0)
                cs_last = client_summary[c]["last_seen"]
                if last and (not cs_last or last.isoformat() > cs_last):
                    client_summary[c]["last_seen"] = last.isoformat()

        return {
            "active_sessions": len(active),
            "stale_sessions": len(stale),
            "total_sessions": len(active) + len(stale),
            "sessions": active + stale,
            "clients": client_summary,
        }

    def prune(self, cleanup_callback=None):
        """清理过期会话（由定时任务或手动调用）。

        Args:
            cleanup_callback: 可选回调函数，会话过期后调用（如清理 session 记忆）
        """
        cutoff = datetime.now() - self._ttl
        with self._lock:
            expired = [sid for sid, info in list(self._sessions.items())
                       if info.get("last_active", datetime.min) < cutoff]
            for sid in expired:
                del self._sessions[sid]
            pruned = len(expired)
        if pruned and cleanup_callback:
            try:
                cleanup_callback()
            except Exception:
                pass
        return pruned


# 全局单例
_broker = SessionBroker()


# ═══════════════════════════════════════════
# Event Bus — 变更广播（黑板模式）
# ═══════════════════════════════════════════


class EventBus:
    """变更事件总线——环形缓冲区，记录写操作的变更事件。

    每个写操作完成后向总线发布一个事件，其他端通过 poll 或
    session_broker_status 感知变更。

    事件结构：
        {"client": "reasonix", "tool": "memory_write",
         "resource": "memory", "summary": "记忆写入",
         "timestamp": "2026-07-28T12:00:00"}
    """

    def __init__(self, max_events: int = 100):
        self._events: list = []
        self._max_events = max_events
        self._lock = threading.Lock()

    def publish(self, client: str, tool: str, resource: str, summary: str = ""):
        """发布一个变更事件。"""
        event = {
            "client": client,
            "tool": tool,
            "resource": resource,
            "summary": summary or f"{tool} by {client}",
            "timestamp": datetime.now().isoformat(),
        }
        with self._lock:
            self._events.append(event)
            if len(self._events) > self._max_events:
                self._events = self._events[-self._max_events:]

    def poll(self, since: str = "", client_filter: str = "") -> list:
        """拉取事件，可选按时间过滤和按客户端过滤。

        Args:
            since: ISO 时间戳，只返回该时间之后的事件
            client_filter: 只返回指定客户端的事件（空=不过滤）
        """
        with self._lock:
            results = list(self._events)
        if since:
            results = [e for e in results if e["timestamp"] > since]
        if client_filter:
            results = [e for e in results if e["client"] == client_filter]
        return results

    def recent(self, n: int = 10) -> list:
        """返回最近 N 条事件。"""
        with self._lock:
            return list(self._events[-n:])

    def clear(self):
        """清空事件（测试用）。"""
        with self._lock:
            self._events.clear()


# 全局单例
_event_bus = EventBus()


# ═══════════════════════════════════════════
# Lease Manager — 排他性资源租约（单写者模式）
# ═══════════════════════════════════════════


class LeaseManager:
    """排他性资源租约：确保同一时间只有一个端操作排他性资源。

    与 with_write_lock 的区别：
    - 写锁是短期的、自动的（router 层自动加锁）
    - 租约是长期的、显式的（AI 手动 acquire/release）

    资源命名空间（建议）：
        "page:{path}" — 页面级排他
        "system:restart" — 系统重启排他
        "config:{key}" — 配置修改排他
    """

    def __init__(self):
        self._leases: dict = {}  # resource → {holder, client, acquired_at, expires_at}
        self._lock = threading.Lock()

    def acquire(self, resource: str, client: str, duration: int = 30,
                force: bool = False) -> dict:
        """获取排他租约。

        Args:
            resource: 资源标识（如 "page:丹房/xx/xxx"）
            client: 持有者客户端名
            duration: 租约时长（秒，默认 30）
            force: 是否强制获取（会释放已有租约）

        Returns:
            dict: 成功/失败 + 租约信息
        """
        now = datetime.now()
        with self._lock:
            existing = self._leases.get(resource)
            if existing and existing["expires_at"] > now:
                if force:
                    # 强制获取：释放旧租约
                    expired = existing
                else:
                    remaining = (existing["expires_at"] - now).total_seconds()
                    return {
                        "success": False,
                        "holder": existing["client"],
                        "remaining_seconds": round(remaining, 1),
                        "message": f"资源已被 {existing['client']} 持有",
                    }
            expires_at = now + timedelta(seconds=duration)
            self._leases[resource] = {
                "resource": resource,
                "client": client,
                "acquired_at": now.isoformat(),
                "expires_at": expires_at,
            }
        return {
            "success": True,
            "resource": resource,
            "client": client,
            "duration": duration,
            "expires_at": expires_at.isoformat(),
        }

    def release(self, resource: str, client: str = "") -> dict:
        """释放租约。client 为空时允许任何端释放。"""
        now = datetime.now()
        with self._lock:
            existing = self._leases.get(resource)
            if not existing:
                return {"success": False, "error": "资源未被持有", "resource": resource}
            if client and existing["client"] != client and existing["expires_at"] > now:
                return {"success": False, "error": f"租约由 {existing['client']} 持有，无法释放"}
            del self._leases[resource]
        return {"success": True, "resource": resource, "released": True}

    def status(self, resource: str = "") -> dict:
        """查询租约状态。resource 为空时返回全部。"""
        now = datetime.now()
        with self._lock:
            if resource:
                entry = self._leases.get(resource)
                if entry:
                    expired = entry["expires_at"] < now
                    return {
                        "resource": resource,
                        "holder": entry["client"],
                        "acquired_at": entry["acquired_at"],
                        "expires_at": entry["expires_at"].isoformat(),
                        "expired": expired,
                        "remaining_seconds": 0 if expired else round(
                            (entry["expires_at"] - now).total_seconds(), 1
                        ),
                    }
                return {"resource": resource, "holder": "", "expired": True}
            # 返回全部租约
            leases = []
            for res, entry in list(self._leases.items()):
                expired = entry["expires_at"] < now
                leases.append({
                    "resource": res,
                    "holder": entry["client"],
                    "acquired_at": entry["acquired_at"],
                    "expires_at": entry["expires_at"].isoformat(),
                    "expired": expired,
                    "remaining_seconds": 0 if expired else round(
                        (entry["expires_at"] - now).total_seconds(), 1
                    ),
                })
            # 清理过期租约
            active = [l for l in leases if not l["expired"]]
            expired_list = [l for l in leases if l["expired"]]
            for l in expired_list:
                if l["resource"] in self._leases:
                    del self._leases[l["resource"]]
            return {
                "active_leases": len(active),
                "expired_leases": len(expired_list),
                "leases": active,
            }

    def is_held(self, resource: str) -> bool:
        """检查资源是否被持有（供 router 层快速判断）。"""
        now = datetime.now()
        with self._lock:
            entry = self._leases.get(resource)
            return entry is not None and entry["expires_at"] > now


# 全局单例
_lease_manager = LeaseManager()
