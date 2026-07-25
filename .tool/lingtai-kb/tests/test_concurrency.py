# -*- coding: utf-8 -*-
"""测试灵台写操作并发保护"""
import sys
import os
import time
import threading

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from concurrency import with_write_lock


def test_lock_serializes_writes():
    """连续调用应都能正常通过（锁串行化不超时）"""
    results = []

    @with_write_lock
    def fast_write(n):
        return {"ok": True, "data": n}

    for i in range(3):
        r = fast_write(i)
        assert r["ok"] is True
        assert r["data"] == i


def test_lock_blocks_concurrent_writes():
    """并发写应串行执行，全部返回"""
    results = []

    @with_write_lock
    def slow_write(n):
        time.sleep(0.2)
        results.append(n)
        return {"ok": True, "data": n}

    threads = []
    for i in range(3):
        t = threading.Thread(target=lambda i=i: slow_write(i))
        t.start()
        threads.append(t)

    for t in threads:
        t.join()

    assert len(results) == 3
    assert set(results) == {0, 1, 2}, "并发写应串行化，全部完成"


def test_lock_timeout_returns_error():
    """锁超时应返回 rate_limited 错误"""
    from concurrency import _lock_manager

    # 获取 default 资源的写锁但不释放
    lock = _lock_manager.get("default")
    lock.acquire_write(timeout=1.0)

    @with_write_lock
    def blocked_write():
        return {"ok": True}

    # 临时缩短超时以加速测试
    import concurrency as c
    old_timeout = c._LOCK_TIMEOUT
    c._LOCK_TIMEOUT = 0.3
    r = blocked_write()
    c._LOCK_TIMEOUT = old_timeout

    lock.release_write()

    assert r["ok"] is False
    assert r["code"] == "rate_limited"


def test_with_write_lock_on_function():
    """测试 with_write_lock 作为包装器而非装饰器"""
    def plain_func(x):
        return {"ok": True, "data": x}

    wrapped = with_write_lock(plain_func)
    r = wrapped("test")
    assert r["ok"] is True
    assert r["data"] == "test"