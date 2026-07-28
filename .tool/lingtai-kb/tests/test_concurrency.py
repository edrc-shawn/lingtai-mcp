# -*- coding: utf-8 -*-
"""测试灵台写操作并发保护 + 幂等键"""
import sys
import os
import time
import threading
import tempfile
import shutil
from datetime import timedelta

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


def test_memorybank_concurrent_writes():
    """MemoryBank 并发写入应全部成功且不丢失"""
    from memory_bank.bank import MemoryBank

    tmpdir = tempfile.mkdtemp()
    try:
        mb = MemoryBank(data_dir=tmpdir)
        n_threads = 5
        results = []
        rlock = threading.Lock()

        def writer(i):
            import uuid
            # 使用完全不同英文词根的内容，避免 merge 策略合并
            prefixes = ["apple", "banana", "cherry", "dolphin", "elephant"]
            uid = uuid.uuid4().hex[:8]
            content = f"{prefixes[i]}_{uid}_{i * 999}"
            r = mb.write(content, "test", tags=[f"concurrent_test_{i}"])
            with rlock:
                results.append(r)

        threads = [threading.Thread(target=writer, args=(i,)) for i in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(results) == n_threads, f"应写入 {n_threads} 条，实际 {len(results)}"
        successes = [r for r in results if r.get("success")]
        assert len(successes) == n_threads, f"应全部成功，实际 {len(successes)} 条成功"
        # 验证所有记忆都已持久化
        stats = mb.stats()
        assert stats["total"] >= n_threads, f"持久化记忆数 {stats['total']} < 写入数 {n_threads}"
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_idempotency_cache_hit():
    """相同幂等键的重复调用应返回缓存结果"""
    from idempotency import IdempotencyCache

    cache = IdempotencyCache(ttl=60, max_entries=100)
    call_count = 0

    def compute():
        nonlocal call_count
        call_count += 1
        return {"ok": True, "value": 42}

    # 第一次调用执行计算
    r1 = cache.get_or_compute("key-1", compute)
    assert r1["ok"] is True
    assert r1["_idempotency_cached"] is False
    assert call_count == 1

    # 第二次调用应返回缓存
    r2 = cache.get_or_compute("key-1", compute)
    assert r2["ok"] is True
    assert r2["_idempotency_cached"] is True
    assert call_count == 1, "不应再次执行计算"


def test_idempotency_cache_different_keys():
    """不同幂等键应各自独立计算"""
    from idempotency import IdempotencyCache

    cache = IdempotencyCache(ttl=60, max_entries=100)
    results = []

    def make_compute(val):
        def compute():
            results.append(val)
            return {"ok": True, "value": val}
        return compute

    r1 = cache.get_or_compute("k-a", make_compute(10))
    r2 = cache.get_or_compute("k-b", make_compute(20))
    assert r1["value"] == 10
    assert r2["value"] == 20
    assert len(results) == 2


def test_idempotency_cache_expiry():
    """过期后应重新计算"""
    from idempotency import IdempotencyCache

    cache = IdempotencyCache(ttl=0, max_entries=100)  # TTL=0 立即过期
    call_count = 0

    def compute():
        nonlocal call_count
        call_count += 1
        return {"ok": True, "value": call_count}

    r1 = cache.get_or_compute("key-1", compute)
    assert call_count == 1
    # TTL=0，所以第二次调用应重新计算
    r2 = cache.get_or_compute("key-1", compute)
    assert call_count == 2, "TTL=0 应重新计算"
    assert r2["value"] == 2


def test_idempotency_extract_key():
    """从 MCP params 中提取幂等键"""
    from idempotency import IdempotencyCache

    cache = IdempotencyCache()

    # 正常带 _meta 的请求
    params = {"name": "memory_write", "arguments": {}, "_meta": {"idempotency_key": "abc-123"}}
    assert cache.extract_key(params) == "abc-123"

    # 无 _meta 的请求
    assert cache.extract_key({"name": "memory_write", "arguments": {}}) == ""

    # 空 _meta
    assert cache.extract_key({"name": "memory_write", "arguments": {}, "_meta": {}}) == ""


def test_idempotency_stats():
    """幂等键缓存统计应正确"""
    from idempotency import IdempotencyCache

    cache = IdempotencyCache(ttl=60, max_entries=10)
    cache.get_or_compute("k1", lambda: {"ok": True})
    cache.get_or_compute("k2", lambda: {"ok": True})
    stats = cache.stats()
    assert stats["total_entries"] == 2
    assert stats["active"] == 2
    assert stats["max_entries"] == 10


def test_session_broker_register():
    """SessionBroker 注册会话应返回 session_id"""
    from session_tracker import SessionBroker

    broker = SessionBroker(ttl=timedelta(minutes=5))
    sid = broker.register("reasonix", "1.0.0")
    assert sid and len(sid) == 12
    status = broker.status()
    assert status["active_sessions"] == 1
    assert status["sessions"][0]["client"] == "reasonix"


def test_session_broker_heartbeat():
    """心跳应刷新 last_active"""
    from session_tracker import SessionBroker
    from datetime import timedelta

    broker = SessionBroker(ttl=timedelta(minutes=5))
    broker.register("workbuddy")
    # 模拟一次心跳
    broker.heartbeat("workbuddy")
    status = broker.status()
    assert status["active_sessions"] == 1
    assert status["sessions"][0]["tool_count"] >= 1


def test_session_broker_stale():
    """超过 TTL 无活动的会话应标记为 stale"""
    from session_tracker import SessionBroker

    broker = SessionBroker(ttl=timedelta(seconds=0))  # 立即过期
    broker.register("cursor")
    status = broker.status()
    assert status["stale_sessions"] == 1


def test_session_broker_same_client_reuse():
    """同一 client 多次注册应复用会话"""
    from session_tracker import SessionBroker

    broker = SessionBroker(ttl=timedelta(minutes=5))
    sid1 = broker.register("reasonix")
    sid2 = broker.register("reasonix")
    assert sid1 == sid2, "同 client 应复用 session_id"
    status = broker.status()
    assert status["active_sessions"] == 1


def test_session_broker_prune():
    """prune 应清理过期会话"""
    from session_tracker import SessionBroker

    broker = SessionBroker(ttl=timedelta(seconds=0))
    broker.register("old_client")
    pruned = broker.prune()
    assert pruned == 1
    status = broker.status()
    assert status["total_sessions"] == 0


def test_session_broker_client_summary():
    """按端聚合统计应正确"""
    from session_tracker import SessionBroker

    broker = SessionBroker(ttl=timedelta(minutes=5))
    broker.register("reasonix")
    broker.heartbeat("reasonix")
    broker.register("workbuddy")
    broker.heartbeat("workbuddy")
    status = broker.status()
    assert "reasonix" in status["clients"]
    assert "workbuddy" in status["clients"]


def test_memory_three_layer_namespace():
    """三层记忆模型：agent 私有记忆不应被其他端看到"""
    from memory_bank.bank import MemoryBank

    tmpdir = tempfile.mkdtemp()
    try:
        mb = MemoryBank(data_dir=tmpdir)

        # 使用完全不同的内容避免 merge
        # 模拟 reasonix 写入私有记忆
        r1 = mb.write("Reasonix 正在调试代码的中间状态", "user_correction", tags=["private"],
                       expected_consumer="agent:reasonix")
        assert r1["success"]

        # 模拟 workbuddy 写入私有记忆
        r2 = mb.write("WorkBuddy 正在处理写作草稿的缓存", "user_correction", tags=["private"],
                       expected_consumer="agent:workbuddy")
        assert r2["success"]

        # 模拟写入全局记忆（无 expected_consumer）
        r3 = mb.write("用户偏好：喜欢简洁的回答风格", "user_correction", tags=["global"])
        assert r3["success"]

        # reasonix 搜索：应看到自己的私有记忆 + 全局记忆
        reasonix_results = mb.query(keyword="", status="", consumer="agent:reasonix")
        reasonix_contents = [r["content"] for r in reasonix_results]
        assert any("Reasonix" in c for c in reasonix_contents), "应看到自己的私有记忆"
        assert any("用户偏好" in c for c in reasonix_contents), "应看到全局记忆"
        assert not any("WorkBuddy" in c for c in reasonix_contents), "不应看到其他端的私有记忆"

        # workbuddy 搜索：应看到自己的私有记忆 + 全局记忆
        wb_results = mb.query(keyword="", status="", consumer="agent:workbuddy")
        wb_contents = [r["content"] for r in wb_results]
        assert any("WorkBuddy" in c for c in wb_contents), "应看到自己的私有记忆"
        assert any("用户偏好" in c for c in wb_contents), "应看到全局记忆"
        assert not any("Reasonix" in c for c in wb_contents), "不应看到其他端的私有记忆"

        # 不过滤 consumer：应看到全部（向后兼容）
        all_results = mb.query(keyword="", status="")
        assert len(all_results) >= 3, "不过滤时应看到所有记忆"
        assert len(all_results) >= 3, "不过滤时应看到所有记忆"
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_event_bus_publish():
    """EventBus 发布事件后应可拉取"""
    from session_tracker import EventBus

    bus = EventBus(max_events=10)
    bus.publish("reasonix", "memory_write", "memory", "私有记忆写入")
    bus.publish("workbuddy", "page_create", "page", "新建知识页")

    events = bus.poll()
    assert len(events) == 2
    assert events[0]["client"] == "reasonix"
    assert events[1]["client"] == "workbuddy"


def test_event_bus_filter():
    """EventBus 按 client 和时间过滤"""
    from session_tracker import EventBus

    bus = EventBus(max_events=10)
    bus.publish("reasonix", "memory_write", "memory")
    bus.publish("workbuddy", "page_create", "page")
    bus.publish("reasonix", "refine_mark", "raw")

    reasonix_events = bus.poll(client_filter="reasonix")
    assert len(reasonix_events) == 2
    assert all(e["client"] == "reasonix" for e in reasonix_events)


def test_event_bus_ring_buffer():
    """EventBus 超出最大容量应自动丢弃最旧事件"""
    from session_tracker import EventBus

    bus = EventBus(max_events=3)
    for i in range(5):
        bus.publish("reasonix", f"tool_{i}", "memory")

    events = bus.poll()
    assert len(events) == 3
    assert events[0]["tool"] == "tool_2"  # 丢弃了 tool_0, tool_1


def test_event_bus_recent():
    """recent() 应返回最近 N 条"""
    from session_tracker import EventBus

    bus = EventBus(max_events=10)
    for i in range(5):
        bus.publish("reasonix", f"tool_{i}", "memory")

    recent = bus.recent(2)
    assert len(recent) == 2
    assert recent[0]["tool"] == "tool_3"
    assert recent[1]["tool"] == "tool_4"


def test_session_scope_cleanup():
    """session_scope 记忆应在会话过期后自动清理"""
    from memory_bank.bank import MemoryBank
    from session_tracker import SessionBroker
    from datetime import timedelta

    tmpdir = tempfile.mkdtemp()
    try:
        mb = MemoryBank(data_dir=tmpdir)
        broker = SessionBroker(ttl=timedelta(seconds=0))  # 立即过期

        # 写入 session_scope 记忆
        r = mb.write("临时工作记忆", "mcp", tags=["temp"],
                      expiry_policy="session_scope")
        assert r["success"]
        assert r["status"] in ("active", "pending")

        # 验证记忆存在
        before = mb.stats()
        total_before = before["total"]
        assert total_before >= 1

        # 触发 broker prune + cleanup
        broker.register("reasonix")  # 立即过期，因为 TTL=0
        broker.prune(cleanup_callback=mb.cleanup_session_scope)

        # 验证 session_scope 记忆已被归档
        after = mb.stats()
        assert after["archived"] >= 1, "session_scope 记忆应被归档"
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_lease_acquire():
    """获取租约应成功"""
    from session_tracker import LeaseManager

    lm = LeaseManager()
    r = lm.acquire("page:test", "reasonix", duration=30)
    assert r["success"] is True
    assert r["client"] == "reasonix"


def test_lease_conflict():
    """同一资源被另一个端获取应失败"""
    from session_tracker import LeaseManager

    lm = LeaseManager()
    lm.acquire("page:test", "reasonix", duration=30)
    r = lm.acquire("page:test", "workbuddy", duration=30)
    assert r["success"] is False
    assert "reasonix" in r.get("holder", "")


def test_lease_force():
    """强制获取应释放旧租约"""
    from session_tracker import LeaseManager

    lm = LeaseManager()
    lm.acquire("page:test", "reasonix", duration=30)
    r = lm.acquire("page:test", "workbuddy", duration=30, force=True)
    assert r["success"] is True
    assert r["client"] == "workbuddy"


def test_lease_release():
    """释放租约应成功"""
    from session_tracker import LeaseManager

    lm = LeaseManager()
    lm.acquire("page:test", "reasonix", duration=30)
    r = lm.release("page:test", "reasonix")
    assert r["success"] is True
    # 查询应显示无持有者
    status = lm.status("page:test")
    assert status["holder"] == ""


def test_lease_status():
    """查询租约状态应返回正确信息"""
    from session_tracker import LeaseManager

    lm = LeaseManager()
    lm.acquire("page:a", "reasonix", duration=30)
    lm.acquire("page:b", "workbuddy", duration=30)
    status = lm.status()
    assert status["active_leases"] == 2
    assert len(status["leases"]) == 2


def test_lease_expiry():
    """过期租约在 status 查询时应自动清理"""
    from session_tracker import LeaseManager
    from datetime import timedelta

    lm = LeaseManager()
    # 使用 duration=0 创建立即过期租约
    lm.acquire("page:expired", "reasonix", duration=0)
    status = lm.status()
    assert status["active_leases"] == 0
    # 再次查询应已清理
    assert lm.is_held("page:expired") is False