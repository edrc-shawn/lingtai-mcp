# -*- coding: utf-8 -*-
"""
灵识 memory_bank - 衰减调度器
============================
按类型差异化衰减：偏好慢/事实极慢/会话快/行为中
集成到SkillOpt的03:00周期
"""

import json
from pathlib import Path
from datetime import datetime, timedelta
from typing import List

from .confidence import Memory, DECAY_POLICIES, resolve_decay_policy, DECAY_STREAK_THRESHOLD, DECAY_ARCHIVE_THRESHOLD


class DecayScheduler:
    """衰减调度器"""

    def __init__(self, bank):
        """
        Args:
            bank: MemoryBank 实例
        """
        self.bank = bank
        self.log_path = Path(__file__).parent / "data" / "decay_log.jsonl"

    def run(self) -> dict:
        """
        执行衰减调度

        Returns:
            dict: 衰减结果 {decayed, deprecated, archived, details}
        """
        results = {"decayed": 0, "deprecated": 0, "archived": 0, "details": []}

        for m in self.bank.memories:
            # 同时处理 active 与 deprecated：deprecated 持续衰减直至归档（spec 8.2 遗忘）
            if m.status not in ("active", "deprecated"):
                continue

            # 硬保护：用户纠正/指令/偏好/教训 永不归档，防止高价值记忆被误伤。
            # 背景：user_correction/user_directive 若配错策略仍会丢；lesson 仅享 0.5× 减速
            # 仍可能在连续低置信周期被归档（如 mem_1d36a1c2f115 已归档的测试教训）。
            # 这类记忆应走「毕业管道」沉淀进丹房，而非被衰减丢弃。
            PROTECTED_SOURCES = {"user_correction", "user_directive"}
            PROTECTED_TAGS = {"lesson", "preference", "user_correction", "user_directive"}
            if m.source in PROTECTED_SOURCES or (set(m.tags or []) & PROTECTED_TAGS):
                continue  # 跳过衰减，永不变更状态/置信度

            # 解析衰减策略：expiry_policy 合法则用，否则按 memory_type 映射
            policy_key = resolve_decay_policy(m.memory_type, m.expiry_policy)
            daily_decay = DECAY_POLICIES[policy_key]["daily_decay"]

            if daily_decay <= 0:
                continue

            # 计算距上次验证的天数
            try:
                last_verified = datetime.fromisoformat(m.last_verified)
                days_since = (datetime.now() - last_verified).days
            except (ValueError, TypeError):
                days_since = 1

            if days_since <= 0:
                continue

            # 计算衰减量
            decay_amount = daily_decay * days_since
            # 教训保护：lesson 标签衰减减半
            if "lesson" in (m.tags or []):
                decay_amount *= 0.5
            old_confidence = m.current_confidence
            m.current_confidence = max(0.0, round(m.current_confidence - decay_amount, 4))

            # 连续低置信度计数（spec 8.2 衰减到期）
            if m.current_confidence < DECAY_STREAK_THRESHOLD:
                m.decay_streak += 1
            else:
                m.decay_streak = 0

            # 判断降级/归档
            if m.decay_streak >= 3 or m.current_confidence < DECAY_ARCHIVE_THRESHOLD:
                m.status = "archived"
                results["archived"] += 1
                results["details"].append({
                    "id": m.id, "content": m.content[:30],
                    "from": old_confidence, "to": m.current_confidence,
                    "action": "archived", "days": days_since,
                    "reason": "decay_streak>=3" if m.decay_streak >= 3 else f"conf<{DECAY_ARCHIVE_THRESHOLD}",
                })
            elif m.current_confidence < DECAY_STREAK_THRESHOLD:
                m.status = "deprecated"
                results["deprecated"] += 1
                results["details"].append({
                    "id": m.id, "content": m.content[:30],
                    "from": old_confidence, "to": m.current_confidence,
                    "action": "deprecated", "days": days_since,
                })
            else:
                results["decayed"] += 1

        self.bank._save()
        self._log(results)
        return results

    def run_pending_promotion(self) -> dict:
        """
        晋升调度：pending记忆中被引用≥3次的晋升为active

        Returns:
            dict: 晋升结果
        """
        promoted = 0
        for m in self.bank.memories:
            if m.status != "pending":
                continue
            if m.evidence_count >= 3:
                m.status = "active"
                promoted += 1
                self.bank._audit("auto_promote", m.id, f"evidence={m.evidence_count}")

        self.bank._save()
        return {"promoted": promoted}

    def cleanup_stale_pending(self, max_days: int = 30) -> dict:
        """
        清理超时pending记忆

        Args:
            max_days: 最大保留天数

        Returns:
            dict: 清理结果
        """
        cleaned = 0
        cutoff = datetime.now() - timedelta(days=max_days)

        for m in self.bank.memories:
            if m.status != "pending":
                continue
            try:
                created = datetime.fromisoformat(m.created_at)
                if created < cutoff:
                    m.status = "archived"
                    cleaned += 1
                    self.bank._audit("auto_cleanup", m.id, f"超过{max_days}天未晋升")
            except (ValueError, TypeError):
                continue

        self.bank._save()
        return {"cleaned": cleaned}

    def _log(self, results: dict):
        entry = {
            "timestamp": datetime.now().isoformat(),
            "decayed": results["decayed"],
            "deprecated": results["deprecated"],
            "archived": results["archived"],
        }
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def get_decay_log(self, days: int = 7) -> list:
        """读取衰减日志"""
        if not self.log_path.exists():
            return []
        cutoff = datetime.now() - timedelta(days=days)
        entries = []
        for line in self.log_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
                ts = datetime.fromisoformat(entry["timestamp"])
                if ts > cutoff:
                    entries.append(entry)
            except Exception:
                continue
        return entries
