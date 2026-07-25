# -*- coding: utf-8 -*-
"""
灵识 skillopt — 睡眠自进化引擎
================================
灵感源于微软 SkillOpt-Sleep，让灵识在 03:00 睡眠周期中：
  1. harvest   — 从 observation_engine / hebbian_weights / search_logs 收割数据
  2. mine      — 模式挖掘（工具调用序列聚类 + 共现加权 + 缺口检测）
  3. replay    — 候选规则在历史日志中回放验证
  4. consolidate — 自信定级（正向率 + 风险率）
  5. stage     — 写入 staged/ 目录，只等人类 /skillopt adopt

核心理念：AI 在夜里试错，人类在早晨拍板。不做自动生效。

版本: 0.2.0（管线全通）
"""

__version__ = "0.2.0"
__all__ = [
    "EvolveEngine",
    "PatternDetector",
    "RuleCandidate",
    "ReplayValidator",
    "ConfidenceScorer",
    "Stager",
    "ProbationMonitor",
]
