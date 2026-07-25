# -*- coding: utf-8 -*-
"""
fulltext_search FTS5 倒排索引测试
=================================
覆盖：tokenize 正确性 / 索引构建 / 查询 / 增量更新 / scope 过滤 / TTL 短路
"""
import os
import time
from pathlib import Path

import pytest

from fts_index import FulltextIndex, tokenize, _build_query_tokens


# ─── tokenize 单元测试 ───────────────────────────


class TestTokenize:
    def test_chinese_chars_split(self):
        assert tokenize("全文搜索") == "全 文 搜 索"

    def test_ascii_word_kept_whole(self):
        assert tokenize("hello world") == "hello world"

    def test_mixed_cjk_ascii(self):
        assert tokenize("灵台MCP知识库") == "灵 台 mcp 知 识 库"

    def test_digits_and_underscore(self):
        assert tokenize("test_123 abc") == "test_123 abc"

    def test_punctuation_dropped(self):
        assert tokenize("你好！世界。") == "你 好 世 界"

    def test_empty_string(self):
        assert tokenize("") == ""

    def test_case_insensitive(self):
        assert tokenize("Hello WORLD") == "hello world"


class TestBuildQueryTokens:
    def test_single_ascii_word(self):
        assert _build_query_tokens("test") == "test"

    def test_multi_cjk_phrase(self):
        assert _build_query_tokens("全文搜索") == '"全 文 搜 索"'

    def test_single_cjk_char(self):
        assert _build_query_tokens("灵") == "灵"

    def test_mixed_phrase(self):
        result = _build_query_tokens("MCP服务")
        assert result == '"mcp 服 务"'


# ─── 索引集成测试 ────────────────────────────────


@pytest.fixture
def fts_vault(tmp_path):
    """创建带测试文件的临时 vault"""
    # 原料目录
    raw = tmp_path / "原料"
    raw.mkdir()
    (raw / "短视频创作.md").write_text(
        "---\n标题: 短视频创作\n---\n\n短视频创作方法论：吸引→痛点→解决方案。",
        encoding="utf-8",
    )
    (raw / "AI工具.md").write_text(
        "---\n标题: AI工具\n---\n\nMCP Server 是 AI 工具协议。",
        encoding="utf-8",
    )

    # 技能目录
    skill = tmp_path / "技能"
    skill.mkdir()
    (skill / "提炼技能.md").write_text(
        "---\n标题: 提炼技能\n---\n\n提炼流程：选料→三检→执行→收尾。",
        encoding="utf-8",
    )

    # .tool/lingtai-kb 目录（db 存放位置）
    tool_dir = tmp_path / ".tool" / "lingtai-kb"
    tool_dir.mkdir(parents=True)

    return tmp_path


@pytest.fixture
def scope_map():
    return {
        "原料": ("原料", "灵台·原料"),
        "技能": ("技能", "灵台·技能"),
    }


@pytest.fixture
def index(fts_vault):
    idx = FulltextIndex(str(fts_vault))
    yield idx
    idx.close()


class TestFulltextIndex:
    def test_build_and_stats(self, index, scope_map):
        index.ensure_built(scope_map)
        stats = index.stats()
        assert stats["total_files"] == 3
        assert stats["by_scope"]["原料"] == 2
        assert stats["by_scope"]["技能"] == 1

    def test_query_chinese(self, index, scope_map):
        index.ensure_built(scope_map)
        results = index.query("短视频", scope="all", max_results=10)
        assert len(results) >= 1
        assert "短视频创作.md" in results[0]["path"]
        assert results[0]["source_label"] == "灵台·原料"

    def test_query_ascii(self, index, scope_map):
        index.ensure_built(scope_map)
        results = index.query("MCP", scope="all", max_results=10)
        assert len(results) >= 1
        assert "AI工具.md" in results[0]["path"]

    def test_query_scope_filter(self, index, scope_map):
        index.ensure_built(scope_map)
        results = index.query("提炼", scope="技能", max_results=10)
        assert len(results) == 1
        assert "提炼技能.md" in results[0]["path"]
        assert results[0]["source_label"] == "灵台·技能"

    def test_query_no_match(self, index, scope_map):
        index.ensure_built(scope_map)
        results = index.query("不存在的关键词xyz", scope="all", max_results=10)
        assert len(results) == 0

    def test_snippet_extraction(self, index, scope_map):
        index.ensure_built(scope_map)
        results = index.query("方法论", scope="原料", max_results=5)
        assert len(results) >= 1
        assert "方法论" in results[0]["snippet"]

    def test_incremental_update(self, index, scope_map, fts_vault):
        index.ensure_built(scope_map)
        assert index.stats()["total_files"] == 3

        # 新增文件
        (fts_vault / "原料" / "新文件.md").write_text(
            "---\n标题: 新文件\n---\n\n新增内容测试。", encoding="utf-8"
        )
        # 强制过期 TTL
        index._last_check = 0.0
        index.ensure_built(scope_map)
        assert index.stats()["total_files"] == 4

        # 新文件可搜到
        results = index.query("新增内容", scope="原料", max_results=5)
        assert len(results) == 1

    def test_file_deletion(self, index, scope_map, fts_vault):
        index.ensure_built(scope_map)
        assert index.stats()["total_files"] == 3

        # 删除文件
        os.remove(fts_vault / "原料" / "AI工具.md")
        index._last_check = 0.0
        index.ensure_built(scope_map)
        assert index.stats()["total_files"] == 2

        # 已删文件搜不到
        results = index.query("MCP", scope="all", max_results=5)
        assert len(results) == 0

    def test_ttl_short_circuit(self, index, scope_map):
        index.ensure_built(scope_map)
        # 第二次调用应命中 TTL 短路（< 1ms）
        t0 = time.perf_counter()
        index.ensure_built(scope_map)
        elapsed = (time.perf_counter() - t0) * 1000
        assert elapsed < 5.0  # 应远小于 5ms

    def test_rebuild(self, index, scope_map):
        index.ensure_built(scope_map)
        index.rebuild(scope_map)
        assert index.stats()["total_files"] == 3
