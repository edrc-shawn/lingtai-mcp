# -*- coding: utf-8 -*-
"""
BM25 + 向量 + RRF 融合引擎测试
================================
覆盖：分词 / BM25 索引 / RRF 融合 / 混合检索
"""
import pytest

from bm25_engine import BM25Index, tokenize_bm25, rrf_fuse


# ─── 分词测试 ─────────────────────────────────────


class TestTokenizeBM25:
    def test_cjk_bigrams_and_trigrams(self):
        tokens = tokenize_bm25("知识管理")
        assert "知识" in tokens
        assert "识管" in tokens
        assert "管理" in tokens
        assert "知识管" in tokens
        assert "识管理" in tokens

    def test_ascii_words(self):
        tokens = tokenize_bm25("MCP Server test")
        assert "mcp" in tokens
        assert "server" in tokens
        assert "test" in tokens

    def test_mixed(self):
        tokens = tokenize_bm25("灵台MCP知识库")
        assert "mcp" in tokens
        assert "灵台" in tokens
        assert "知识" in tokens

    def test_stopwords_removed(self):
        tokens = tokenize_bm25("the 知识管理 is a 方法")
        # ASCII 停用词被过滤
        assert "the" not in tokens
        assert "is" not in tokens
        assert "a" not in tokens
        # 有意义的词保留
        assert "知识" in tokens
        assert "管理" in tokens

    def test_empty(self):
        assert tokenize_bm25("") == []


# ─── BM25 索引测试 ────────────────────────────────


@pytest.fixture
def sample_pages():
    return [
        {"path": "p1.md", "title": "知识管理方法论", "summary": "个人知识管理的系统方法", "tags": ["知识", "管理"]},
        {"path": "p2.md", "title": "短视频创作", "summary": "短视频创作方法论与实操", "tags": ["短视频"]},
        {"path": "p3.md", "title": "MCP工具速查", "summary": "灵台MCP Server工具清单", "tags": ["MCP", "工具"]},
        {"path": "p4.md", "title": "记忆系统", "summary": "AI记忆系统与遗忘曲线", "tags": ["记忆", "AI"]},
        {"path": "p5.md", "title": "含人量", "summary": "AI时代判断力与含人量概念", "tags": ["含人量"]},
    ]


@pytest.fixture
def bm25(sample_pages):
    return BM25Index(sample_pages)


class TestBM25Index:
    def test_build_stats(self, bm25):
        stats = bm25.stats
        assert stats["pages"] == 5
        assert stats["vocab_size"] > 0
        assert stats["avg_doc_len"] > 0

    def test_search_relevant(self, bm25):
        results = bm25.search("知识管理", top_k=3)
        assert len(results) >= 1
        assert results[0]["path"] == "p1.md"
        assert results[0]["rank"] == 1
        assert results[0]["score"] > 0

    def test_search_ascii(self, bm25):
        results = bm25.search("MCP", top_k=3)
        assert len(results) >= 1
        assert results[0]["path"] == "p3.md"

    def test_search_no_match(self, bm25):
        results = bm25.search("量子力学", top_k=3)
        assert len(results) == 0

    def test_idf_ranking(self, bm25):
        # "含人量" 只出现在 1 个文档中（高 IDF），应排在首位
        results = bm25.search("含人量", top_k=3)
        assert results[0]["path"] == "p5.md"

    def test_top_k_limit(self, bm25):
        results = bm25.search("方法", top_k=2)
        assert len(results) <= 2


# ─── RRF 融合测试 ─────────────────────────────────


class TestRRFFuse:
    def test_basic_fusion(self):
        list1 = [{"path": "a", "score": 10, "rank": 1}, {"path": "b", "score": 5, "rank": 2}]
        list2 = [{"path": "b", "score": 0.9, "rank": 1}, {"path": "c", "score": 0.8, "rank": 2}]
        fused = rrf_fuse([list1, list2], k=60)
        # "b" 出现在两路中，应排第一
        assert fused[0]["path"] == "b"
        assert fused[0]["rrf_score"] > fused[1]["rrf_score"]

    def test_single_list(self):
        list1 = [{"path": "x", "score": 1, "rank": 1}]
        fused = rrf_fuse([list1], k=60)
        assert len(fused) == 1
        assert fused[0]["path"] == "x"

    def test_empty_lists(self):
        fused = rrf_fuse([[], []], k=60)
        assert fused == []

    def test_disjoint_lists(self):
        list1 = [{"path": "a", "score": 1, "rank": 1}]
        list2 = [{"path": "b", "score": 1, "rank": 1}]
        fused = rrf_fuse([list1, list2], k=60)
        assert len(fused) == 2
        # 两路 rank=1 的 RRF 分数相同
        assert fused[0]["rrf_score"] == fused[1]["rrf_score"]
