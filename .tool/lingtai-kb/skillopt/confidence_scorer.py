# -*- coding: utf-8 -*-
"""
confidence_scorer.py — 自信定级（V2）职责：
  对已回验的候选规则做自信评分，按规则类型分档设阈值。

V2 改进：
  - 分级阈值：不同类型的规则不同 🟢 线
    - tool_sequence: 🟢 ≥ 0.60（重复调用风险低，可直接采纳）
    - dead_end:      🟢 ≥ 0.75（需更高的确定度再自动采纳）
    - cooccurrence:  🟢 ≥ 0.70
    - knowledge_gap: 🟢 ≥ 0.80
  - 频度提升：同类型规则 if sample_count ≥ 3 且正向率高 → 自信度 +0.05
  - 历史 reject 罚分在 replay_validator 中处理，此处不再重复

公式（V2）：
  基础自信 = 正向率 × 0.6 + (1 - 风险率) × 0.4
  频度校正 = 若 sample_count ≥ 3 → +0.05（说明数据可信）
  最终自信 = 基础自信 + 频度校正

TODO:
  - 从 changelog/日志中提取 adopt 确认记录做正向反馈
"""

import os

# 按类型分档阈值
TYPE_THRESHOLDS = {
    "tool_sequence": {"🟢": 0.60, "🟡": 0.40, "⚪": 0.00},
    "dead_end":      {"🟢": 0.75, "🟡": 0.50, "⚪": 0.00},
    "cooccurrence":  {"🟢": 0.70, "🟡": 0.45, "⚪": 0.00},
    "knowledge_gap": {"🟢": 0.80, "🟡": 0.55, "⚪": 0.00},
}

DEFAULT_THRESHOLDS = {"🟢": 0.75, "🟡": 0.50, "⚪": 0.00}

# 频度校正
SAMPLE_CONFIDENCE_BOOST = 0.05
SAMPLE_MIN_COUNT = 3


class ConfidenceScorer:
    """对已验证的候选规则做自信定级。"""

    def score(self, validated: list[dict]) -> list[dict]:
        """
        对已验证规则打分并定级。

        Args:
            validated: replay_validator 输出（已包含 reject 罚分）

        Returns:
            list[dict]: 带有 confidence 和 level 的规则列表
        """
        scored = []
        for v in validated:
            ptype = v.get("pattern_type", "")
            thresholds = TYPE_THRESHOLDS.get(ptype, DEFAULT_THRESHOLDS)

            base = self._calculate(v)
            boost = self._sample_boost(v)

            confidence = round(base + boost, 2)
            confidence = max(0.0, min(1.0, confidence))
            level = self._level(confidence, thresholds)

            v["confidence"] = confidence
            v["level"] = level
            v["confidence_breakdown"] = {
                "base": round(base, 2),
                "sample_boost": boost,
            }
            scored.append(v)

        scored.sort(key=lambda x: x["confidence"], reverse=True)
        return scored

    def _calculate(self, rule: dict) -> float:
        """基础自信评分：正向率 × 0.6 + (1 - 风险率) × 0.4"""
        pos = rule.get("positive_rate", 0.0)
        risk = rule.get("risk_rate", 0.0)
        return pos * 0.6 + (1 - risk) * 0.4

    def _sample_boost(self, rule: dict) -> float:
        """样本量校正：有足够历史匹配数据时 +0.05。"""
        sample_count = rule.get("sample_count", 0)
        pos_rate = rule.get("positive_rate", 0.0)
        if sample_count >= SAMPLE_MIN_COUNT and pos_rate >= 0.6:
            return SAMPLE_CONFIDENCE_BOOST
        return 0.0

    def _level(self, confidence: float, thresholds: dict) -> str:
        """按类型阈值定级。"""
        if confidence >= thresholds["🟢"]:
            return "🟢"
        elif confidence >= thresholds["🟡"]:
            return "🟡"
        return "⚪"
