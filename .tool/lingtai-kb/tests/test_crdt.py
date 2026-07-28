# -*- coding: utf-8 -*-
"""
CRDT 无锁合并 — 单元测试
=========================
"""
import json
import os
import tempfile
from pathlib import Path

import pytest
from memory_bank.crdt import (
    VersionVector,
    CrdtState,
    crdt_merge_entries,
)


class TestVersionVector:
    """版本向量比较"""

    def test_equal(self):
        assert VersionVector.compare({"a": 1}, {"a": 1}) == "equal"
        assert VersionVector.compare({}, {}) == "equal"
        assert VersionVector.compare({"a": 1, "b": 2}, {"a": 1, "b": 2}) == "equal"

    def test_before(self):
        """a ⪯ b：a 的所有键值 <= b，且至少一个严格小于"""
        assert VersionVector.compare({"a": 1}, {"a": 2}) == "before"
        assert VersionVector.compare({"a": 1}, {"a": 1, "b": 1}) == "before"
        assert VersionVector.compare({"a": 1, "b": 1}, {"a": 2, "b": 1}) == "before"

    def test_after(self):
        """b ⪯ a：b 的所有键值 <= a，且至少一个严格小于"""
        assert VersionVector.compare({"a": 2}, {"a": 1}) == "after"
        assert VersionVector.compare({"a": 1, "b": 1}, {"a": 1}) == "after"

    def test_concurrent(self):
        """互不支配"""
        assert VersionVector.compare({"a": 2}, {"b": 2}) == "concurrent"
        assert VersionVector.compare({"a": 2, "b": 1}, {"a": 1, "b": 2}) == "concurrent"

    def test_merge(self):
        merged = VersionVector.merge({"a": 1}, {"b": 2})
        assert merged == {"a": 1, "b": 2}

        merged = VersionVector.merge({"a": 3}, {"a": 1, "b": 2})
        assert merged == {"a": 3, "b": 2}


class TestCrdtState:
    """CRDT 状态持久化"""

    def test_increment(self):
        with tempfile.TemporaryDirectory(prefix="crdt_test_") as tmp:
            state = CrdtState(end_id="reasonix", data_dir=tmp)
            dot = state.increment()
            assert dot == {"reasonix": 1}
            assert state.vector == {"reasonix": 1}

            dot = state.increment()
            assert dot == {"reasonix": 2}

    def test_persistence(self):
        with tempfile.TemporaryDirectory(prefix="crdt_test_") as tmp:
            state = CrdtState(end_id="reasonix", data_dir=tmp)
            state.increment()
            state.increment()

            # 新实例读取同一文件
            state2 = CrdtState(end_id="reasonix", data_dir=tmp)
            assert state2.vector == {"reasonix": 2}

    def test_multiple_ends(self):
        with tempfile.TemporaryDirectory(prefix="crdt_test_") as tmp:
            a = CrdtState(end_id="reasonix", data_dir=tmp)
            b = CrdtState(end_id="workbuddy", data_dir=tmp)

            a.increment()  # reasonix: 1
            a.increment()  # reasonix: 2
            b.increment()  # workbuddy: 1

            assert a.vector == {"reasonix": 2}
            assert b.vector == {"workbuddy": 1}

    def test_merge_received(self):
        with tempfile.TemporaryDirectory(prefix="crdt_test_") as tmp:
            a = CrdtState(end_id="reasonix", data_dir=tmp)
            a.increment()  # reasonix: 1

            # reasonix 收到 workbuddy 的版本向量
            a.merge_received({"workbuddy": 3})
            assert a.vector == {"reasonix": 1, "workbuddy": 3}

            # 再次递增：reasonix: 2
            dot = a.increment()
            assert dot == {"reasonix": 2, "workbuddy": 3}

    def test_sync_from_peers(self):
        """sync_from_peers 扫描所有对端状态文件并合并"""
        with tempfile.TemporaryDirectory(prefix="crdt_test_") as tmp:
            # 先创建 workbuddy 的状态文件（模拟 workbuddy 已经写过）
            wb_dir = Path(tmp) / "crdt"
            wb_dir.mkdir(parents=True, exist_ok=True)
            (wb_dir / "workbuddy.json").write_text(
                '{"workbuddy": 3, "reasonix": 1}', encoding="utf-8"
            )

            # reasonix 启动，同步对端
            a = CrdtState(end_id="reasonix", data_dir=tmp)
            a.sync_from_peers()
            assert a.vector.get("workbuddy") == 3
            assert a.vector.get("reasonix") == 1

            # 递增后，dot 包含对端信息
            dot = a.increment()
            assert dot["reasonix"] == 2
            assert dot["workbuddy"] == 3


class TestCrdtMergeEntries:
    """CRDT entries 合并"""

    def make_entry(self, content: str, dot: dict, origin: str = "test", conf: float = 0.5) -> dict:
        return {
            "content": content,
            "confidence": conf,
            "source": "test",
            "timestamp": "2026-07-28T00:00:00",
            "status": "active",
            "crdt": {"dot": dot, "origin": origin},
        }

    def test_empty_existing(self):
        entry = self.make_entry("hello", {"reasonix": 1})
        result = crdt_merge_entries([], entry)
        assert len(result) == 1
        assert result[0]["content"] == "hello"

    def test_same_end_sequential(self):
        """同一端顺序写入：新支配旧，旧被 supersede"""
        existing = [self.make_entry("old", {"reasonix": 1}, origin="reasonix")]
        new = self.make_entry("new", {"reasonix": 2}, origin="reasonix")
        result = crdt_merge_entries(existing, new)
        assert len(result) == 2
        assert result[0]["status"] == "superseded"
        assert result[0]["content"] == "old"
        assert result[1]["content"] == "new"

    def test_concurrent_ends(self):
        """不同端并发写入：双方都保留"""
        existing = [self.make_entry("from_a", {"a": 1}, origin="a")]
        new = self.make_entry("from_b", {"b": 1}, origin="b")
        result = crdt_merge_entries(existing, new)
        assert len(result) == 2
        # 两条都 active
        assert result[0]["status"] == "active"
        assert result[0]["content"] == "from_a"
        assert result[1]["status"] == "active"
        assert result[1]["content"] == "from_b"

    def test_causal_remote(self):
        """远端端写入新版本：新支配旧"""
        existing = [self.make_entry("old", {"a": 1, "b": 1}, origin="a")]
        new = self.make_entry("newer", {"a": 1, "b": 2}, origin="b")
        result = crdt_merge_entries(existing, new)
        assert len(result) == 2
        assert result[0]["status"] == "superseded"
        assert result[0]["content"] == "old"
        assert result[1]["content"] == "newer"

    def test_legacy_entries_preserved(self):
        """无 CRDT 字段的旧 entry：保守保留"""
        existing = [{"content": "legacy", "confidence": 0.5, "status": "active"}]
        new = self.make_entry("new", {"reasonix": 1}, origin="reasonix")
        result = crdt_merge_entries(existing, new)
        assert len(result) == 2
        assert result[0]["content"] == "legacy"
        assert result[0]["status"] == "active"
        assert result[1]["content"] == "new"

    def test_three_way_concurrent(self):
        """三端并发：所有都保留"""
        existing = [
            self.make_entry("from_a", {"a": 1}, origin="a"),
            self.make_entry("from_b", {"b": 1}, origin="b"),
        ]
        new = self.make_entry("from_c", {"c": 1}, origin="c")
        result = crdt_merge_entries(existing, new)
        assert len(result) == 3
        for e in result:
            assert e["status"] == "active"

    def test_already_superseded_not_affected(self):
        """已 superseded 的 entry 不参与比较，保持原样"""
        existing = [
            self.make_entry("old", {"reasonix": 1}, origin="reasonix"),
        ]
        existing[0]["status"] = "superseded"  # 模拟已被标记
        new = self.make_entry("new", {"reasonix": 2}, origin="reasonix")
        result = crdt_merge_entries(existing, new)
        assert len(result) == 2
        assert result[0]["status"] == "superseded"  # 保持原样
        assert result[1]["content"] == "new"

    def test_max_entries_trim(self):
        """超过上限时保留最新的"""
        existing = [self.make_entry(f"entry_{i}", {"a": i}, origin="a") for i in range(1, 6)]
        new = self.make_entry("new", {"a": 6}, origin="a")
        result = crdt_merge_entries(existing, new, max_entries=3)
        assert len(result) == 3
        assert result[-1]["content"] == "new"

    def test_concurrent_same_dot(self):
        """相同 dot 但不同 origin：视为 concurrent"""
        existing = [self.make_entry("from_a", {"a": 1, "b": 1}, origin="a")]
        new = self.make_entry("from_b", {"a": 1, "b": 1}, origin="b")
        result = crdt_merge_entries(existing, new)
        assert len(result) == 2
        assert result[0]["status"] == "active"
        assert result[1]["status"] == "active"