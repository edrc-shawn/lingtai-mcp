# -*- coding: utf-8 -*-
"""
幂等键缓存 — 防止 MCP 工具重复调用
=====================================
MCP 客户端可能因网络超时重发同一 tools/call 请求。
幂等键机制：缓存最近 N 个写操作的返回结果，相同幂等键的重复调用直接返回缓存。

用法：
    cache = IdempotencyCache()
    result = cache.get_or_compute(key, lambda: do_actual_work())
"""
import time
import threading
from collections import OrderedDict
from logger import get_logger

log = get_logger(__name__)

_DEFAULT_TTL = 300  # 5 分钟
_DEFAULT_MAX_ENTRIES = 500


class IdempotencyCache:
    """幂等键缓存：LRU + TTL，写操作去重。"""

    def __init__(self, ttl: int = _DEFAULT_TTL, max_entries: int = _DEFAULT_MAX_ENTRIES):
        self._ttl = ttl
        self._max_entries = max_entries
        self._cache: OrderedDict = OrderedDict()  # {key: (result, expiry)}
        self._lock = threading.Lock()

    def get_or_compute(self, key: str, compute_fn) -> dict:
        """获取缓存或计算新结果。
        
        Args:
            key: 幂等键
            compute_fn: 无参函数，实际执行写操作
            
        Returns:
            dict: 结果（含 is_cached 标记）
        """
        now = time.monotonic()
        with self._lock:
            cached = self._cache.get(key)
            if cached is not None:
                result, expiry = cached
                if now < expiry:
                    # 延长 LRU 顺序
                    self._cache.move_to_end(key)
                    log.debug("idempotency hit: %s", key)
                    result["_idempotency_cached"] = True
                    return result
                else:
                    # 过期，删除
                    del self._cache[key]

        # 未命中缓存，执行实际计算
        result = compute_fn()
        result["_idempotency_cached"] = False

        with self._lock:
            # LRU 淘汰
            while len(self._cache) >= self._max_entries:
                self._cache.popitem(last=False)
            self._cache[key] = (result, now + self._ttl)
            log.debug("idempotency store: %s", key)

        return result

    def extract_key(self, params: dict) -> str:
        """从 MCP tools/call 的 params 中提取幂等键。
        
        MCP 格式：params._meta.idempotency_key
        """
        meta = params.get("_meta") or {}
        key = meta.get("idempotency_key") or ""
        if key and isinstance(key, str):
            key = key.strip()
        return key

    def stats(self) -> dict:
        """返回缓存统计。"""
        with self._lock:
            now = time.monotonic()
            active = sum(1 for _, expiry in self._cache.values() if expiry > now)
            expired = len(self._cache) - active
            return {
                "total_entries": len(self._cache),
                "active": active,
                "expired": expired,
                "ttl_seconds": self._ttl,
                "max_entries": self._max_entries,
            }

    def clear(self):
        """清空缓存（测试用）。"""
        with self._lock:
            self._cache.clear()


# 全局单例（供 router 使用）
_idempotency_cache = IdempotencyCache()