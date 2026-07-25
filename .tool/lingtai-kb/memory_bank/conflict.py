# -*- coding: utf-8 -*-
"""
灵识 memory_bank - 冲突检测与分叉管理
=====================================
核心原则：新旧冲突不覆盖，分叉并存。
旧信息保留，新信息另起一条，各自带时间戳和置信度。
"""

from typing import List, Optional, Tuple
from .confidence import Memory


class ConflictDetector:
    """冲突检测引擎"""

    def __init__(self):
        pass

    def detect(self, new_memory: Memory, existing: List[Memory]) -> List[dict]:
        """
        检测新记忆与现有记忆的冲突

        Args:
            new_memory: 新记忆
            existing: 现有记忆列表

        Returns:
            List[dict]: 冲突列表 [{memory_id, old_content, old_confidence, similarity}]
        """
        conflicts = []
        for m in existing:
            if m.id == new_memory.id:
                continue
            sim = self._semantic_similarity(new_memory.content, m.content)
            if sim > 0.5:  # 相似度阈值
                conflicts.append({
                    "memory_id": m.id,
                    "old_content": m.content,
                    "old_confidence": m.current_confidence,
                    "old_status": m.status,
                    "similarity": round(sim, 3),
                })
        return conflicts

    def resolve(self, new_memory: Memory, conflict: dict, existing: List[Memory]) -> dict:
        """
        冲突解决策略

        规则：
        1. 新信息置信度明显更高（差>0.2）→ 新记忆写入，旧记忆标记deprecated
        2. 两者接近 → 新记忆写入新branch，标记conflict
        3. 新信息置信度低 → 新记忆写入pending，触发审计

        Returns:
            dict: 解决方案 {action, reason, branch_id}
        """
        old_conf = conflict["old_confidence"]
        new_conf = new_memory.source_confidence
        diff = new_conf - old_conf

        if diff > 0.2:
            # 新信息明显更可信 → 替换旧的
            return {
                "action": "replace",
                "reason": f"新置信度({new_conf})显著高于旧({old_conf})",
                "branch_id": "通用",
                "deprecate_old": conflict["memory_id"],
            }
        elif diff > -0.2:
            # 两者接近 → 分叉并存
            branch_id = f"branch_{new_memory.id[:8]}"
            new_memory.branch_id = branch_id
            new_memory.conflicts_with = [conflict["memory_id"]]
            return {
                "action": "fork",
                "reason": f"新旧置信度接近({new_conf} vs {old_conf})，分叉并存",
                "branch_id": branch_id,
            }
        else:
            # 新信息更不可信 → 暂存观察
            return {
                "action": "pending",
                "reason": f"新置信度({new_conf})低于旧({old_conf})，进入暂存区",
                "branch_id": "pending",
            }

    def _semantic_similarity(self, a: str, b: str) -> float:
        """
        简化版语义相似度：基于字符重叠率
        （Phase 1用简化版，Phase 2可替换为embedding）
        """
        if not a or not b:
            return 0.0
        a_chars = set(a)
        b_chars = set(b)
        if not a_chars or not b_chars:
            return 0.0
        intersection = a_chars & b_chars
        union = a_chars | b_chars
        return len(intersection) / len(union)

    def find_all_conflicts(self, memories: List[Memory]) -> List[dict]:
        """扫描全部记忆，找出所有冲突对"""
        conflicts = []
        seen = set()
        for i, m1 in enumerate(memories):
            if m1.status in ("archived", "deprecated"):
                continue
            for m2 in memories[i + 1:]:
                if m2.status in ("archived", "deprecated"):
                    continue
                if m1.id == m2.id:
                    continue
                pair = tuple(sorted([m1.id, m2.id]))
                if pair in seen:
                    continue
                sim = self._semantic_similarity(m1.content, m2.content)
                if sim > 0.5:
                    seen.add(pair)
                    conflicts.append({
                        "a": m1.id,
                        "a_content": m1.content,
                        "a_confidence": m1.current_confidence,
                        "b": m2.id,
                        "b_content": m2.content,
                        "b_confidence": m2.current_confidence,
                        "similarity": round(sim, 3),
                    })
        return conflicts
