# -*- coding: utf-8 -*-
"""
灵台 MCP Server — 工具 smoke test
=================================
每个工具至少一个"正常路径不崩"测试。
不验证业务正确性，只验证：调用不抛异常 + 返回 dict。
"""
import pytest


# ═══ 知识检索域 ═══

class TestKnowledgeTools:
    def test_knowledge_search(self, server):
        result = server.query(keyword="测试")
        assert isinstance(result, dict)

    def test_knowledge_search_empty(self, server):
        result = server.query(keyword="")
        assert isinstance(result, dict)

    def test_knowledge_stats(self, server):
        result = server.stats()
        assert isinstance(result, dict)

    def test_knowledge_explore_graph(self, server):
        result = server.graph(page_path="丹房/00-思考与认知/测试页面.md", hops=1)
        assert isinstance(result, dict)

    def test_knowledge_explore_related(self, server):
        result = server.related(page_path="丹房/00-思考与认知/测试页面.md", max_results=3)
        assert isinstance(result, dict)

    def test_fulltext_search(self, server):
        result = server.fulltext_search(keyword="测试", scope="all")
        assert isinstance(result, dict)


# ═══ 页面操作域 ═══

class TestPageTools:
    def test_page_read(self, server):
        result = server.read_page(path="丹房/00-思考与认知/测试页面.md")
        assert isinstance(result, dict)

    def test_page_read_not_found(self, server):
        result = server.read_page(path="丹房/不存在.md")
        assert isinstance(result, dict)

    def test_page_history(self, server):
        result = server.page_history(page_path="丹房/00-思考与认知/测试页面.md")
        assert isinstance(result, dict)


# ═══ 记忆银行域 ═══

class TestMemoryTools:
    def test_memory_write_and_search(self, server):
        # 写入
        w = server.mem_write(content="pytest smoke test 记忆", source="ai_reasoning", tags=["test"])
        assert isinstance(w, dict)
        # 搜索
        s = server.mem_query(keyword="smoke test")
        assert isinstance(s, dict)

    def test_memory_consolidate(self, server):
        result = server.mem_consolidate()
        assert isinstance(result, dict)


# ═══ 原料/提炼域 ═══

class TestRefineTools:
    def test_refine_status(self, server):
        try:
            result = server.refine_status(raw_path="原料/测试原料.md")
            assert isinstance(result, dict)
        except (FileNotFoundError, OSError):
            pytest.skip("refine_status 需要 git 环境")

    def test_raw_derive(self, server):
        result = server.raw_derive(raw_path="原料/测试原料.md")
        assert isinstance(result, dict)


# ═══ 观察/健康域 ═══

class TestObservationTools:
    def test_observation_reflect(self, server):
        result = server.reflect()
        assert isinstance(result, dict)

    def test_health_inspect(self, server):
        result = server.health_inspect()
        assert isinstance(result, dict)


# ═══ 系统域 ═══

class TestSystemTools:
    def test_system_sop(self, server):
        result = server.sop()
        assert isinstance(result, dict)

    def test_lingshi_classify(self, server):
        result = server.classify_question(question="什么是灵台？")
        assert isinstance(result, dict)


# ═══ 宏工具域 ═══

class TestMacroTools:
    def test_session_end(self, server):
        result = server.session_end(session_start="2026-07-24T08:00")
        assert isinstance(result, dict)


# ═══ 返回值契约测试 ═══

class TestReturnContract:
    """验证 _wrap 后所有工具返回 {ok: bool, ...} 格式"""

    def test_wrap_read_tool(self, tool_map):
        handler = tool_map.get("knowledge_stats")
        if handler:
            result = handler({})
            assert "ok" in result, f"knowledge_stats 缺少 ok 字段: {list(result.keys())}"

    def test_wrap_write_tool(self, tool_map):
        handler = tool_map.get("memory_write")
        if handler:
            result = handler({"content": "contract test", "source": "ai_reasoning"})
            assert "ok" in result, f"memory_write 缺少 ok 字段: {list(result.keys())}"
