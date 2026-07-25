# -*- coding: utf-8 -*-
"""
灵台并发控制——per-resource RWLock + 超时
==========================================
替代全局单锁，按资源分锁：
- 不同资源的写操作可并行（页面写入不阻塞记忆写入）
- 同一资源：读共享 / 写排他（RWLock 语义）
- 保持 with_write_lock 装饰器接口向后兼容

资源分类：
- "page": 丹房页面写操作（page_create/update/append/add_link）
- "memory": 记忆银行写操作（memory_write/feedback/decay）
- "raw": 原料写操作（raw_save/refine_mark）
- "index": 索引重建（system_refresh_index）
- "default": 未分类写操作（兜底）

用法：
    # 旧接口（兼容）：使用 default 锁
    @with_write_lock
    def some_write_op(...): ...

    # 新接口：指定资源
    @with_write_lock(resource="page")
    def page_create(...): ...

    # 读锁（共享）：
    with read_lock("page"):
        data = read_page(...)
"""
__all__ = ["RWLock", "ResourceLockManager", "with_write_lock", "read_lock", "write_lock", "lock_stats"]

import threading
import time
from contextlib import contextmanager
from errors import fail, ErrorCode

_LOCK_TIMEOUT = 5.0  # 秒


# ─── RWLock 实现 ──────────────────────────────────


class RWLock:
    """读写锁：多读共享 / 写排他。

    写者优先（writer-preference）：有写等待时，新读请求排队，
    防止写饥饿。
    """

    def __init__(self):
        self._read_ready = threading.Condition(threading.Lock())
        self._readers = 0
        self._writers_waiting = 0
        self._writer_active = False

    def acquire_read(self, timeout: float = _LOCK_TIMEOUT) -> bool:
        """获取读锁（共享）。超时返回 False。"""
        deadline = time.monotonic() + timeout
        with self._read_ready:
            while self._writer_active or self._writers_waiting > 0:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._read_ready.wait(timeout=remaining)
            self._readers += 1
            return True

    def release_read(self):
        with self._read_ready:
            self._readers -= 1
            if self._readers == 0:
                self._read_ready.notify_all()

    def acquire_write(self, timeout: float = _LOCK_TIMEOUT) -> bool:
        """获取写锁（排他）。超时返回 False。"""
        deadline = time.monotonic() + timeout
        with self._read_ready:
            self._writers_waiting += 1
            while self._writer_active or self._readers > 0:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    self._writers_waiting -= 1
                    return False
                self._read_ready.wait(timeout=remaining)
            self._writers_waiting -= 1
            self._writer_active = True
            return True

    def release_write(self):
        with self._read_ready:
            self._writer_active = False
            self._read_ready.notify_all()


# ─── 资源锁管理器 ─────────────────────────────────


class ResourceLockManager:
    """按资源名管理 RWLock 实例（懒创建）。"""

    def __init__(self):
        self._locks: dict[str, RWLock] = {}
        self._mgr_lock = threading.Lock()

    def get(self, resource: str) -> RWLock:
        if resource not in self._locks:
            with self._mgr_lock:
                if resource not in self._locks:
                    self._locks[resource] = RWLock()
        return self._locks[resource]

    @property
    def resources(self) -> list[str]:
        return list(self._locks.keys())


# 全局单例
_lock_manager = ResourceLockManager()


# ─── 公共接口 ─────────────────────────────────────


def with_write_lock(fn=None, *, resource: str = "default"):
    """写操作装饰器/包装器（向后兼容）。

    用法：
        @with_write_lock                    # 旧接口，resource="default"
        @with_write_lock(resource="page")   # 新接口，指定资源
        with_write_lock(fn)                 # 函数包装
        with_write_lock(fn, resource="raw") # 函数包装 + 指定资源
    """
    def decorator(func):
        def wrapper(*args, **kwargs):
            lock = _lock_manager.get(resource)
            acquired = lock.acquire_write(timeout=_LOCK_TIMEOUT)
            if not acquired:
                return fail(
                    ErrorCode.RATE_LIMITED,
                    f"写操作并发冲突（{resource}），{_LOCK_TIMEOUT}s 内未获取到锁",
                )
            try:
                return func(*args, **kwargs)
            finally:
                lock.release_write()
        wrapper.__name__ = getattr(func, "__name__", "locked")
        wrapper.__doc__ = getattr(func, "__doc__", None)
        return wrapper

    if fn is not None:
        # @with_write_lock 或 with_write_lock(fn) 形式
        return decorator(fn)
    # @with_write_lock(resource="page") 形式
    return decorator


@contextmanager
def read_lock(resource: str = "default", timeout: float = _LOCK_TIMEOUT):
    """读锁上下文管理器（共享，不阻塞其他读者）。

    用法：
        with read_lock("page"):
            data = read_page(...)
    """
    lock = _lock_manager.get(resource)
    acquired = lock.acquire_read(timeout=timeout)
    if not acquired:
        raise TimeoutError(f"读锁超时（{resource}），{timeout}s 内未获取")
    try:
        yield
    finally:
        lock.release_read()


@contextmanager
def write_lock(resource: str = "default", timeout: float = _LOCK_TIMEOUT):
    """写锁上下文管理器（排他）。

    用法：
        with write_lock("page"):
            update_page(...)
    """
    lock = _lock_manager.get(resource)
    acquired = lock.acquire_write(timeout=timeout)
    if not acquired:
        raise TimeoutError(f"写锁超时（{resource}），{timeout}s 内未获取")
    try:
        yield
    finally:
        lock.release_write()


# ─── 统计（调试用） ───────────────────────────────


def lock_stats() -> dict:
    """返回当前活跃的资源锁列表。"""
    return {"resources": _lock_manager.resources}
