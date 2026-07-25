# -*- coding: utf-8 -*-
"""问题消解漏斗 — 借鉴 dbskill 消解漏斗，在检索前检测问题是否需要重新定义"""

import re
from typing import Dict, List, Optional, Tuple

# 消解模式： (正则, 信号类型, 消解建议, 严重程度)
# 严重程度： high=强烈建议消解, medium=可能需消解, low=轻微提示
DISSOLVE_PATTERNS: List[Tuple[re.Pattern, str, str, str]] = [
    # --- 缺少基线（how-to without baseline）---
    (re.compile(r"怎么(提高|做好|优化|增加|减少|改善|提升)(.{0,20})"),
     "missing_baseline",
     "你现在的状态是什么？目标是什么？没有基线数据，'提高'只是方向不是问题。",
     "high"),

    # --- 隐藏假设（should implies hidden assumption）---
    (re.compile(r"(应该|要不要|需不需要|该不该)怎么.{0,20}"),
     "hidden_assumption",
     "你确定需要做这件事吗？'应该怎么'的前提是'必须做'——先确认前提。",
     "high"),

    # --- 确认偏误（why X not Y — 前提可能不成立）---
    (re.compile(r"为什么(.*)(不|没|没有)(.{0,10})"),
     "confirmation_bias",
     "你确定「{0}不{2}」这个观察是准确的吗？可能是观察角度或数据范围的问题。",
     "medium"),

    # --- 比较问题缺上下文（which is better without context）---
    (re.compile(r"(哪个|哪一种|哪种).*(好|更好|最好|合适|适合)"),
     "missing_context",
     "对谁好？在什么场景下？没有上下文的'哪个好'是无法回答的。",
     "high"),

    # --- 问题太短（too vague）---
    (re.compile(r"^.{1,4}$"),
     "too_short",
     "能再具体一点吗？这个词太宽泛了——你想从这个概念中获得什么？",
     "medium"),

    # --- 灵台元问题（meta questions about the system itself）---
    (re.compile(r"灵台(应该|要不要|怎么|如何|该不该)"),
     "lingtai_meta",
     "灵台元问题需要先明确：你想验证什么假设？如果只是探索，可以先说'我也不知道具体方向，帮我看看有哪些可能'。",
     "medium"),

    # --- 商业化问题（commercialization without product maturity）---
    (re.compile(r"(怎么|如何|怎样)(变现|赚钱|商业化|盈利|收费)"),
     "premature_commercialization",
     "商业化之前先确认：产品/服务在什么阶段？用户验证过吗？如果还在探索期，商业化可能是个伪问题。",
     "high"),

    # --- 对比选择（X vs Y）---
    (re.compile(r"(.{2,10})\s*(和|跟|与|vs\.?)\s*(.{2,10})\s*(哪个|怎么选|选哪个|区别|对比)"),
     "comparison_choice",
     "对比之前先明确：你选这个是为了什么目的？两种方案可能各有适用场景，取决于你的约束条件。",
     "medium"),

    # --- 单概念泛问（vague concept query）---
    (re.compile(r"^(什么是|讲讲|说说|解释一下|介绍)(.{2,})"),
     "vague_concept",
     "你想从哪个角度了解这个概念？定义、应用、争议、还是和他人的对比？",
     "low"),

    # --- 情感/意见类问题（opinion without context）---
    (re.compile(r"(你觉得|你认为|你怎么看|怎么看|什么看法)"),
     "opinion_without_context",
     "我可以给分析，但需要先知道：你关心的是哪个维度？技术可行性、社会影响、还是个人实操？",
     "low"),
]


def dissolve_question(question: str) -> Dict:
    """消解漏斗：检测问题是否需要重新定义

    借鉴 dbskill 消解漏斗设计：75% 的问题在检索前被消解掉。
    本函数纯规则匹配，不调用 LLM，轻量高效。

    Args:
        question: 用户问题原文

    Returns:
        dict: {
            needs_dissolve: bool — 是否建议消解
            signals: list — 触发的消解信号 [{type, severity, suggestion}]
            suggested_action: str — 建议下一步操作
            dissolved: bool — 是否强烈建议消解（有 high 信号）
        }
    """
    question_stripped = question.strip()
    signals = []

    for pattern, signal_type, suggestion, severity in DISSOLVE_PATTERNS:
        match = pattern.search(question_stripped)
        if match:
            # 填充 suggestion 中的占位符 {0}, {1}, {2}...
            try:
                groups = match.groups()
                filled = suggestion.format(*groups)
            except (IndexError, ValueError):
                filled = suggestion
            signals.append({
                "type": signal_type,
                "severity": severity,
                "suggestion": filled,
                "matched_text": match.group(0)[:40],
            })

    needs_dissolve = len(signals) > 0
    has_high = any(s["severity"] == "high" for s in signals)
    has_medium = any(s["severity"] == "medium" for s in signals)

    # 建议操作
    if has_high:
        suggested_action = "强烈建议消解——先和用户确认问题前提，再决定是否检索"
    elif has_medium:
        suggested_action = "建议消解——问题可能有隐含假设，确认后再检索更高效"
    elif signals:
        suggested_action = "可选消解——问题基本清晰，但补充上下文会得到更精准的结果"
    else:
        suggested_action = "无需消解——问题清晰，可直接检索"

    return {
        "needs_dissolve": needs_dissolve,
        "signals": signals,
        "signal_count": len(signals),
        "has_high": has_high,
        "suggested_action": suggested_action,
        "dissolved": has_high,  # 有 high 信号 = 强烈建议消解
    }