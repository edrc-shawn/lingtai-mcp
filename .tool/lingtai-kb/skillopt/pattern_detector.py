# -*- coding: utf-8 -*-
"""
pattern_detector.py — 模式检测（行为数据驱动）

从收割的会话数据（logs/tool_sessions.jsonl 等）中检测可归纳的工具调用模式：
  - tool_sequence：高频连续工具调用对（跨会话累计 ≥3 次）
  - dead_end：会话末步查询中断 / ≥3 次连续查询无产出
  - cooccurrence：Hebbian 共现变化（需 stats 数据支持）
  - knowledge_gap：对账表缺口

由 evolve_engine._mine() 调用，与 _mine_corrections（教训泳道）正交并行——双泳道互不干扰。
"""

import re
from collections import Counter


FORBIDDEN_PATTERNS = [
    "直接查询已有页面",
    "Git 提交",
    "读取文件内容",
]

# 产出类工具：调用即表示会话有产出，不视为死胡同
PRODUCT_TOOLS = frozenset({
    "page_create", "page_update", "page_append_section",
    "page_add_link", "page_compress",
    "refine_quick", "refine_mark",
    "memory_write", "memory_feedback", "memory_link",
    "memory_graduate",
    "user_push", "user_feedback",
    "output_publish", "raw_save", "agent_feedback",
    "session_end", "context_load",
    "sys_reload", "system_refresh_index",
    "skillopt_adopt", "skillopt_reject", "skillopt_run",
    "system_registry_scan",
})

# 查询类工具：只读搜索/查询，不产生持久化效果
QUERY_TOOLS = frozenset({
    "knowledge_search", "knowledge_synthesize", "knowledge_inject",
    "knowledge_stats", "knowledge_gaps", "knowledge_heatmap",
    "knowledge_digest", "knowledge_explore", "knowledge_compound",
    "knowledge_quality_check", "knowledge_overview",
    "page_read", "page_history", "page_link_suggest",
    "memory_search", "memory_stats", "memory_consolidate",
    "memory_decay", "lingshi_inject",
    "fulltext_search", "web_search", "episodic_search",
    "episodic_recent", "observation_reflect", "observation_stats",
    "health_inspect", "health_ledger", "lifecycle_scan",
    "vector_index_status", "system_sop", "refine_status",
    "raw_derive", "raw_derive_batch",
    "skillopt_status", "skillopt_log", "skillopt_dryrun",
    "agent_skills", "agent_recommend",
    "cross_end_activity", "skill_list",
    "nightly_enrich", "topic_match", "concept_collide",
    "ingest_ripple", "domain_visibility",
    "test_direct",
})


class PatternDetector:
    """从收割数据中检测可归纳模式。"""

    def detect(self, raw_data: dict) -> list[dict]:
        """
        检测模式。

        Args:
            raw_data: from evolve_engine._harvest()
                      含 observations, hebbian_changes, session_summaries

        Returns:
            list[dict]: 检测到的模式列表
        """
        patterns = []

        # 1. 从 session_summaries 中检测重复工具调用序列
        sessions = raw_data.get("session_summaries", [])
        if sessions:
            sequences = self._extract_sequences(sessions)
            patterns.extend(self._rank_sequences(sequences))

        # 2. 从 session_summaries 中检测死胡同
        if sessions:
            patterns.extend(self._detect_dead_ends(sessions))

        # 3. 从 hebbian_changes 中检测高频共现对
        hebbian = raw_data.get("hebbian_changes", [])
        if hebbian:
            patterns.extend(self._extract_cooccurrence(hebbian))

        # 4. 检测缺口相关模式
        gaps = raw_data.get("gaps", [])
        if gaps:
            patterns.append({
                "pattern_id": "gap_detected",
                "type": "knowledge_gap",
                "sequence": [],
                "frequency": len(gaps),
                "weight": 0.7,
                "description": f"知识缺口: {len(gaps)} 条（来自对账表）",
                "detail": gaps,
            })

        return patterns

    def _extract_sequences(self, sessions: list[dict]) -> list[list[str]]:
        """从会话摘要中提取工具调用序列。"""
        sequences = []
        for s in sessions:
            calls = s.get("tool_calls", [])
            if len(calls) >= 2:
                sequences.append([c.get("name", "") for c in calls if c.get("name")])
        return sequences

    def _rank_sequences(self, sequences: list[list[str]], top_k: int = 5) -> list[dict]:
        """对序列聚类并排序。"""
        pair_counts = Counter()
        for seq in sequences:
            for i in range(len(seq) - 1):
                pair = f"{seq[i]}→{seq[i+1]}"
                pair_counts[pair] += 1

        patterns = []
        for pair, count in pair_counts.most_common(top_k):
            if count < 3:  # 至少出现 3 次才值得关注（2 次可能是巧合）
                continue
            tools = pair.split("→")
            patterns.append({
                "pattern_id": f"tool_seq_{tools[0]}_{tools[1]}",
                "type": "tool_sequence",
                "sequence": tools,
                "frequency": count,
                "weight": min(count / 10, 1.0),
                "description": f"工具调用序列: {tools[0]} → {tools[1]}（出现 {count} 次）",
            })

        return patterns

    def _extract_cooccurrence(self, hebbian_changes: list[dict]) -> list[dict]:
        """从 Hebbian 权重变化中提取共现模式。"""
        patterns = []
        for h in hebbian_changes:
            pair = h.get("pair", "")
            weight = h.get("delta", 0)
            if abs(weight) > 0.3:
                sources = pair.split("↔")
                patterns.append({
                    "pattern_id": f"hebbian_{sources[0]}_{sources[1]}" if len(sources) == 2 else f"hebbian_{pair}",
                    "type": "cooccurrence",
                    "sequence": sources if len(sources) == 2 else [pair],
                    "frequency": 1,
                    "weight": abs(weight),
                    "delta": weight,
                    "description": f"共现变化: {pair} (Δ={weight:.2f})",
                })
        return patterns

    def _detect_dead_ends(self, sessions: list[dict]) -> list[dict]:
        """
        检测死胡同模式——V3 用当前工具名精确匹配。

        Tier 1 — 末步是查询工具（会话中断在查询上，无产出）
        Tier 2 — ≥3 次连续查询调用且无产出
        """
        dead_ends = []
        for s in sessions:
            calls = s.get("tool_calls", [])
            call_names = [c.get("name", "") for c in calls if c.get("name")]
            if not call_names:
                continue

            # 有产出类工具 → 不是死胡同
            if any(n in PRODUCT_TOOLS for n in call_names):
                continue

            # 单次查询是正常 Q&A，不是死胡同
            if len(call_names) < 2:
                continue

            last_call = call_names[-1]

            # Tier 1: 末步是查询 → 会话中断在查询上
            if last_call in QUERY_TOOLS:
                dead_ends.append({
                    "pattern_id": f"deadend_{last_call}",
                    "type": "dead_end",
                    "sequence": [last_call],
                    "frequency": 1,
                    "weight": 0.65,
                    "description": f"死胡同: 末步查询中断（末步: {last_call}）",
                })
                continue

            # Tier 2: ≥3 次连续查询 → 深度搜索无产出
            consecutive_queries = 0
            for n in call_names:
                if n in QUERY_TOOLS:
                    consecutive_queries += 1
                else:
                    consecutive_queries = 0
                if consecutive_queries >= 3:
                    dead_ends.append({
                        "pattern_id": f"deadend_{last_call}",
                        "type": "dead_end",
                        "sequence": [last_call],
                        "frequency": 1,
                        "weight": 0.6,
                        "description": f"死胡同: 连续{consecutive_queries}次查询无产出（末步: {last_call}）",
                    })
                    break

        return dead_ends
