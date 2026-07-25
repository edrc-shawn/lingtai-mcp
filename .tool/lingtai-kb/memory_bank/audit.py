# -*- coding: utf-8 -*-
"""
灵识 memory_bank - 审计日志
============================
记录每次读写和用户反馈，供晋升引擎和衰减调度器使用。
最独立的模块，可先上。
"""

import json
from pathlib import Path
from datetime import datetime, timedelta
from collections import Counter, defaultdict


class AuditLog:
    """记忆审计日志"""

    def __init__(self, data_dir: str = None):
        """
        Args:
            data_dir: 数据目录。None=默认 __file__.parent/data；
                      传入则基于它定位 audit.jsonl（测试隔离用，与 MemoryBank.data_dir 一致）。
        """
        if data_dir is None:
            self.log_path = Path(__file__).parent / "data" / "audit.jsonl"
        else:
            self.log_path = Path(data_dir) / "audit.jsonl"

    def record(self, action: str, memory_id: str, detail: str = "", user_feedback: str = None):
        """
        记录审计事件

        Args:
            action: 操作类型（write/update_confidence/evidence_increment/query_hit/query_miss/user_feedback）
            memory_id: 记忆ID
            detail: 附加信息
            user_feedback: 用户反馈（adopt/reject）
        """
        entry = {
            "timestamp": datetime.now().isoformat(),
            "action": action,
            "memory_id": memory_id,
            "detail": detail,
        }
        if user_feedback:
            entry["user_feedback"] = user_feedback

        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def get_entries(self, memory_id: str = None, action: str = None, days: int = 7) -> list:
        """
        获取审计条目

        Args:
            memory_id: 按记忆ID过滤
            action: 按操作类型过滤
            days: 最近N天

        Returns:
            list: 审计条目列表
        """
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
                if ts < cutoff:
                    continue
                if memory_id and entry.get("memory_id") != memory_id:
                    continue
                if action and entry.get("action") != action:
                    continue
                entries.append(entry)
            except Exception:
                continue

        return entries

    def get_memory_references(self, memory_id: str, days: int = 7) -> int:
        """获取记忆被引用的次数（query_hit次数）"""
        entries = self.get_entries(memory_id=memory_id, action="query_hit", days=days)
        return len(entries)

    def get_feedback_summary(self, memory_id: str, days: int = 30) -> dict:
        """获取用户反馈汇总"""
        entries = self.get_entries(memory_id=memory_id, action="user_feedback", days=days)
        adopt = sum(1 for e in entries if e.get("user_feedback") == "adopt")
        reject = sum(1 for e in entries if e.get("user_feedback") == "reject")
        return {"adopt": adopt, "reject": reject, "total": adopt + reject}

    def get_stats(self, days: int = 7) -> dict:
        """审计统计"""
        entries = self.get_entries(days=days)
        action_counts = Counter(e.get("action", "unknown") for e in entries)
        memory_counts = Counter(e.get("memory_id", "unknown") for e in entries)
        return {
            "total_entries": len(entries),
            "by_action": dict(action_counts.most_common(10)),
            "most_active_memories": dict(memory_counts.most_common(5)),
        }

    def cleanup(self, keep_days: int = 30):
        """清理旧审计日志"""
        if not self.log_path.exists():
            return 0

        cutoff = datetime.now() - timedelta(days=keep_days)
        kept = []
        removed = 0

        for line in self.log_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
                ts = datetime.fromisoformat(entry["timestamp"])
                if ts >= cutoff:
                    kept.append(line)
                else:
                    removed += 1
            except Exception:
                kept.append(line)

        self.log_path.write_text("\n".join(kept) + "\n" if kept else "", encoding="utf-8")
        return removed
