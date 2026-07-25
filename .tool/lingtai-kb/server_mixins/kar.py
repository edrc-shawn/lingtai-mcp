# -*- coding: utf-8 -*-
from decorators import tool
"""KAR 融合 mixin（缓存已移至 kar_fusion.py 入口）"""

class KARMixin:
    def unified_query(self, keyword: str, hops: int = 2) -> dict:
        """KAR统一查询：知识+关联+推理"""
        return self.kar.unified_query(keyword, hops=hops)

    def chain_query(self, keywords: list, hops: int = 2) -> dict:
        """KAR链式查询：多关键词串联"""
        return self.kar.chain_query(keywords, hops=hops)

    @tool(readonly=True, write=False, category="knowledge", system=False, name="knowledge_explore")
    def explore_topic(self, topic: str, depth: int = 2) -> dict:
        """KAR主题探索：从主题出发做发散式知识网络探索（多跳关联+跨域桥接）。
        场景：不确定关键词、想"逛"知识图谱发现意外关联时；需要某个主题的全景关联时。
        区别：有明确关键词要精确结果用 knowledge_search；要结论性合成回答用 knowledge_synthesize。

        Args:
            topic: 探索主题
            depth: 探索深度/跳数（默认2）
        """
        if hasattr(self.kar, 'explore_topic'):
            return self.kar.explore_topic(topic, depth)
        # Fallback: 使用 graph 从 topic 出发扩散
        return self.graph(page_path=topic, hops=depth, weighted=True)
