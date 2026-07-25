# -*- coding: utf-8 -*-
"""LLM推理 mixin"""

class LLMMixin:
    def perception_stats(self, period: str = "summary") -> dict:
        """感知命中率统计"""
        if period == "daily":
            return {"daily_stats": self.perception_stats_monitor.get_daily_stats(7)}
        else:
            return self.perception_stats_monitor.get_summary()

    def analyze_text(self, text: str) -> dict:
        """LLM文本分析（调用DeepSeek）"""
        return self.reasoning.analyze(text)

    def summarize_text(self, text: str, max_length: int = 200) -> str:
        """LLM文章总结（调用DeepSeek）"""
        return self.reasoning.summarize(text, max_length)

    def extract_insights(self, text: str) -> dict:
        """LLM洞察提取（调用DeepSeek）"""
        return self.reasoning.extract_insights(text)
