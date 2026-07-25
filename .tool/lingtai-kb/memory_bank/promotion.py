# -*- coding: utf-8 -*-
"""
灵识 memory_bank - 晋升引擎
============================
暂存→正式：从pending到active的决策引擎。
依赖审计数据（audit.py）判断引用次数。
"""

import json
from pathlib import Path
from datetime import datetime, timedelta
from typing import List


class PromotionEngine:
    """晋升引擎"""

    def __init__(self, bank):
        """
        Args:
            bank: MemoryBank 实例
        """
        self.bank = bank
        self.log_path = Path(__file__).parent / "data" / "promotion_log.jsonl"

    def run(self, min_evidence: int = 3, max_pending_days: int = 7) -> dict:
        """
        执行晋升调度

        Args:
            min_evidence: 最少证据次数
            max_pending_days: pending最长保留天数

        Returns:
            dict: 晋升结果
        """
        promoted = []
        cleaned = []
        kept = []

        for m in self.bank.memories:
            if m.status != "pending":
                continue

            # 检查证据次数
            if m.evidence_count >= min_evidence:
                m.status = "active"
                promoted.append({"id": m.id, "content": m.content[:30], "evidence": m.evidence_count})
                self.bank._audit("promote", m.id, f"evidence={m.evidence_count}")

            # 检查超时
            elif self._is_stale(m, max_pending_days):
                m.status = "archived"
                cleaned.append({"id": m.id, "content": m.content[:30]})
                self.bank._audit("cleanup", m.id, f"超过{max_pending_days}天未晋升")

            else:
                kept.append(m.id)

        self.bank._save()
        self._log(promoted, cleaned)

        return {
            "promoted": len(promoted),
            "cleaned": len(cleaned),
            "kept": len(kept),
            "details": promoted + cleaned,
        }

    def _is_stale(self, memory, max_days: int) -> bool:
        """判断pending记忆是否超时"""
        try:
            created = datetime.fromisoformat(memory.created_at)
            return (datetime.now() - created).days > max_days
        except (ValueError, TypeError):
            return True

    def get_candidates(self, min_evidence: int = 3) -> list:
        """获取即将晋升的候选记忆"""
        candidates = []
        for m in self.bank.memories:
            if m.status != "pending":
                continue
            if m.evidence_count >= min_evidence:
                candidates.append({
                    "id": m.id,
                    "content": m.content,
                    "confidence": m.current_confidence,
                    "evidence": m.evidence_count,
                    "source": m.source,
                    "created_at": m.created_at,
                })
        return candidates

    def _log(self, promoted: list, cleaned: list):
        entry = {
            "timestamp": datetime.now().isoformat(),
            "promoted": len(promoted),
            "cleaned": len(cleaned),
            "promoted_ids": [p["id"] for p in promoted],
            "cleaned_ids": [c["id"] for c in cleaned],
        }
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def promote_from_observations(self, vault_path: str = None,
                                     min_confidence: float = 0.7,
                                     min_facts: int = 5) -> dict:
        """
        将观察层中高质量观察晋升到记忆银行。
        统一入口：巡更只需调用一次。
        """
        import re, json
        if vault_path is None:
            vault_path = r"."

        obs_path = Path(vault_path) / ".tool" / "lingtai-kb" / "observation" / "observations.json"
        if not obs_path.exists():
            return {"error": "observations.json not found"}

        with open(obs_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        observations = data.get("observations", [])

        # 要跳过的泛词主题
        skip_topics = {"处理状态", "已提炼", "处理日期", "回链", "待提炼", "来源", "对话", "技术", "日常", "临时"}

        promoted = []
        skipped = {"noise": 0, "already": 0, "quality": 0, "confidence": 0, "facts": 0}

        for obs in observations:
            topic = obs.get("topic", "")
            confidence = obs.get("confidence", 0)
            facts = obs.get("facts", [])
            summary = obs.get("summary", "")

            if obs.get("promoted", False):
                skipped["already"] += 1
                continue

            # 噪声过滤
            is_noise = any(kw in topic for kw in skip_topics)
            cleaned = re.sub(r'[\u4e00-\u9fff]', '', topic)
            if is_noise or len(cleaned) >= len(topic) * 0.5:
                skipped["noise"] += 1
                continue

            if confidence < min_confidence:
                skipped["confidence"] += 1
                continue
            if len(facts) < min_facts:
                skipped["facts"] += 1
                continue
            if len(facts) > 100:
                skipped["quality"] += 1
                continue

            content_parts = [f"观察主题: {topic}"]
            if summary:
                content_parts.append(f"摘要: {summary[:200]}")
            for fact in facts[:3]:
                fc = fact.get("content", "")[:100]
                if fc:
                    content_parts.append(f"- {fc}")
            content = "\n".join(content_parts)

            result = self.bank.write(
                content=content,
                source_type="ai_reasoning",
                tags=["promoted_from_obs", topic.split(":")[0] if ":" in topic else "observation"],
            )

            if result.get("success"):
                mid = result.get("id", "")
                boost = confidence - 0.5
                if boost > 0:
                    self.bank.update_confidence(mid, boost)

            obs["promoted"] = True
            obs["promoted_at"] = datetime.now().isoformat()
            obs["memory_id"] = result.get("id", "")

            promoted.append({
                "topic": topic[:40],
                "confidence": confidence,
                "facts": len(facts),
                "memory_id": result.get("id", ""),
            })

        data["updated_at"] = datetime.now().isoformat()
        with open(obs_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        return {
            "total_observations": len(observations),
            "promoted": len(promoted),
            "skipped": skipped,
            "promotions": promoted[:10],
        }

    def run_all(self, vault_path: str = None) -> dict:
        """统一晋升入口：pending→active + 观察→记忆"""
        internal = self.run()
        obs = self.promote_from_observations(vault_path=vault_path)
        return {"pending_promotion": internal, "obs_promotion": obs}

    def get_promotion_log(self, days: int = 7) -> list:
        """读取晋升日志"""
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
