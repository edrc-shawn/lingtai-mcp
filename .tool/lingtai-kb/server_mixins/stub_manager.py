# -*- coding: utf-8 -*-
"""
跨工具轻量存根层（Stub Layer）— 减少同会话链中重复 IO

设计原则：
1. 只缓存「幂等读」结果（index.json 解析、refine-map.json、embedding 缓存等）
2. 不缓存写结果（page_create/update 等），不跨工具传递中间产物
3. 由源文件的 mtime 变化触发失效，无需显式 invalidate
4. 线程安全（CPython GIL）且零锁开销
5. 纯内存缓存，不落盘——跨工具调用之间共享，进程重启后重新计算

用法：
    from server_mixins.stub_manager import stub_pages, stub_refine_map, stub_embeddings
    pages = stub_pages.read(memory_engine)
    rmap = stub_refine_map.read(vault_path)
"""

import os
import json
import time
from datetime import datetime

# ─── 通用缓存基类 ───

class _MtimeCache:
    """基于 mtime 的惰性缓存。
    
    读时检查源文件 mtime；未变则返回缓存值。
    线程安全性由 CPython GIL 保证，无需额外锁。
    """
    def __init__(self, label: str = ""):
        self._cached_value = None
        self._last_mtime = 0.0
        self._label = label
        self._misses = 0
        self._hits = 0

    def _get_mtime(self, path: str) -> float:
        try:
            return os.path.getmtime(path)
        except OSError:
            return 0.0

    def read(self, *args, **kwargs):
        """子类实现：接收来源参数，返回缓存或重算的结果"""
        raise NotImplementedError

    def invalidate(self):
        """强制失效（写入方调用）"""
        self._last_mtime = 0.0
        self._cached_value = None

    @property
    def stats(self) -> dict:
        return {"label": self._label, "hits": self._hits, "misses": self._misses}


# ─── 专用存根 ───

class _PagesStub(_MtimeCache):
    """self.memory.pages 的存根。
    
    memory_engine.pages 本身已由 MemoryEngine 缓存，
    此处缓存的是 pages 列表的「引用计数」快照（len + 元组转换），
    使后续工具能快速检查 pages 是否已变而无需重新遍历。
    """
    # index.json 路径（相对 vault）
    INDEX_REL = ["丹房", ".meta", "index.json"]

    def read(self, memory_engine) -> list:
        """读取 pages，优先走缓存。memory_engine 必须有 .pages 属性。"""
        vault = getattr(memory_engine, 'vault_path', None)
        if not vault:
            return getattr(memory_engine, 'pages', [])

        idx_path = os.path.join(vault, *self.INDEX_REL)
        mtime = self._get_mtime(idx_path)

        if self._cached_value is not None and mtime == self._last_mtime and mtime > 0:
            self._hits += 1
            return self._cached_value

        # mtime 变了 → 重读
        self._misses += 1
        pages = getattr(memory_engine, 'pages', [])
        self._cached_value = pages
        self._last_mtime = mtime
        return pages


class _RefineMapStub(_MtimeCache):
    """refine-map.json 的存根。
    
    refine_mark / refine_status / refine_list_sources 都会读它。
    避免同一提炼流程中多次重复解析。
    """
    REFINE_MAP_REL = [".lingtai", "refine-map.json"]

    def read(self, vault_path: str) -> dict:
        path = os.path.join(vault_path, *self.REFINE_MAP_REL)
        mtime = self._get_mtime(path)

        if self._cached_value is not None and mtime == self._last_mtime and mtime > 0:
            self._hits += 1
            return self._cached_value

        self._misses += 1
        if not os.path.isfile(path):
            self._cached_value = {}
            self._last_mtime = mtime
            return {}

        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        self._cached_value = data
        self._last_mtime = mtime
        return data


class _EmbeddingStub(_MtimeCache):
    """danfang_embeddings.json 的存根。
    
    被 ingest_ripple 和 concept_collide 同时读取。
    避免同一会话中两次嵌入计算或两次文件 IO。
    """
    EMBED_REL = [".tool", "lingtai-kb", "data", "danfang_embeddings.json"]

    def read(self, vault_path: str) -> dict:
        path = os.path.join(vault_path, *self.EMBED_REL)
        mtime = self._get_mtime(path)

        if self._cached_value is not None and mtime == self._last_mtime and mtime > 0:
            self._hits += 1
            return self._cached_value

        self._misses += 1
        if not os.path.isfile(path):
            self._cached_value = {}
            self._last_mtime = mtime
            return {}

        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        self._cached_value = data
        self._last_mtime = mtime
        return data


class _ToolSessionsStub(_MtimeCache):
    """tool_sessions.jsonl 的存根。
    
    被 session_end 和 health_indicators 读取全文。
    缓存最近 N 条记录 + 文件尾偏移量，避免全量重读。
    """
    SESSIONS_REL = [".tool", "lingtai-kb", "logs", "tool_sessions.jsonl"]

    def __init__(self, label: str = "tool_sessions"):
        super().__init__(label)
        self._tail_cache = []  # 最近 100 条
        self._tail_mtime = 0.0
        self._tail_size = 0

    def read_tail(self, vault_path: str, n: int = 100) -> list:
        """读取最近 N 条会话记录。优先用缓存。"""
        path = os.path.join(vault_path, *self.SESSIONS_REL)
        mtime = self._get_mtime(path)

        if self._tail_cache and mtime == self._tail_mtime and mtime > 0:
            self._hits += 1
            return self._tail_cache

        self._misses += 1
        if not os.path.isfile(path):
            self._tail_cache = []
            self._tail_mtime = mtime
            return []

        size = os.path.getsize(path)
        chunk = min(size, 8192)
        lines = []
        with open(path, "r", encoding="utf-8") as f:
            if size > 0 and chunk > 0:
                f.seek(max(0, size - chunk))
                raw = f.read()
                lines = [l.strip() for l in raw.strip().split("\n") if l.strip()]

        result = []
        for line in lines[-n:]:
            try:
                result.append(json.loads(line))
            except json.JSONDecodeError:
                continue

        self._tail_cache = result
        self._tail_mtime = mtime
        self._tail_size = size
        return result

    def invalidate(self):
        super().invalidate()
        self._tail_cache = []
        self._tail_mtime = 0.0
        self._tail_size = 0


# ─── 全局实例（进程级共享，所有 mixin 共用） ───

stub_pages = _PagesStub("pages")
stub_refine_map = _RefineMapStub("refine_map")
stub_embeddings = _EmbeddingStub("embeddings")
stub_tool_sessions = _ToolSessionsStub("tool_sessions")

# ─── 快捷辅助 ───

def invalidate_all():
    """强制失效所有缓存（写操作后调用）"""
    stub_pages.invalidate()
    stub_refine_map.invalidate()
    stub_embeddings.invalidate()
    stub_tool_sessions.invalidate()

def invalidate_knowledge():
    """知识写操作后调用（页面/索引变更）"""
    stub_pages.invalidate()

def invalidate_refine():
    """提炼操作后调用（refine-map.json 变更）"""
    stub_refine_map.invalidate()

def get_all_stats() -> dict:
    """返回所有存根的命中率统计（供 health_inspect 消费）"""
    return {
        "pages": stub_pages.stats,
        "refine_map": stub_refine_map.stats,
        "embeddings": stub_embeddings.stats,
        "tool_sessions": stub_tool_sessions.stats,
    }
