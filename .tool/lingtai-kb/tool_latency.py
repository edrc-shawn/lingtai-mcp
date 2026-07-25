# -*- coding: utf-8 -*-
"""
工具延迟监控 (Tool Latency Monitor)
====================================
轻量级工具调用延迟记录 + 异常检测。

让灵识能自主发现"lingshi_inject 慢了"这类问题，
而不需要用户主动告知。

存储：tool_latency.jsonl（追加写入，滚动保留最近 30 天）
"""
import json
import os
import time
from pathlib import Path
from datetime import datetime, timedelta
from typing import List, Optional


# 慢工具阈值（毫秒）
SLOW_THRESHOLD_MS = 5000
# 保留天数
RETENTION_DAYS = 30
# 异常检测窗口（连续 N 次超阈值视为异常）
ANOMALY_WINDOW = 3


class ToolLatencyMonitor:
    """工具延迟监控"""

    def __init__(self, data_dir: str = None):
        if data_dir is None:
            self.data_dir = Path(__file__).parent / "data"
        else:
            self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.latency_file = self.data_dir / "tool_latency.jsonl"

    def record(self, tool_name: str, duration_ms: float, success: bool = True):
        """记录一次工具调用延迟"""
        entry = {
            "tool": tool_name,
            "duration_ms": round(duration_ms, 2),
            "success": success,
            "ts": datetime.now().isoformat(),
        }
        try:
            with open(self.latency_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception:
            pass  # 静默失败，不阻塞调用

    def get_recent(self, days: int = 7) -> List[dict]:
        """取最近 N 天的延迟记录"""
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()
        records = []
        try:
            if not self.latency_file.exists():
                return records
            with open(self.latency_file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                        if entry.get("ts", "") >= cutoff:
                            records.append(entry)
                    except json.JSONDecodeError:
                        continue
        except Exception:
            pass
        return records

    def get_slow_tools(self, threshold_ms: int = SLOW_THRESHOLD_MS, days: int = 7) -> List[dict]:
        """返回最近 N 天平均延迟超过阈值的工具列表"""
        records = self.get_recent(days=days)
        if not records:
            return []

        # 按工具名聚合
        agg = {}
        for r in records:
            name = r.get("tool", "unknown")
            if name not in agg:
                agg[name] = {"total_ms": 0.0, "count": 0, "recent": []}
            agg[name]["total_ms"] += r.get("duration_ms", 0)
            agg[name]["count"] += 1
            agg[name]["recent"].append(r.get("duration_ms", 0))

        slow = []
        for name, data in agg.items():
            avg_ms = data["total_ms"] / data["count"]
            if avg_ms >= threshold_ms:
                slow.append({
                    "tool": name,
                    "avg_ms": round(avg_ms, 2),
                    "max_ms": round(max(data["recent"]), 2),
                    "call_count": data["count"],
                })
        return sorted(slow, key=lambda x: -x["avg_ms"])

    def detect_anomalies(self, threshold_ms: int = SLOW_THRESHOLD_MS, days: int = 7) -> List[dict]:
        """检测异常：连续 N 次调用都超阈值 → 视为持续性异常"""
        records = self.get_recent(days=days)
        if not records:
            return []

        anomalies = []
        # 按工具分组
        by_tool = {}
        for r in records:
            name = r.get("tool", "unknown")
            by_tool.setdefault(name, []).append(r)

        for name, calls in by_tool.items():
            # 按时间排序
            calls.sort(key=lambda x: x.get("ts", ""))
            # 检查最后 N 次是否都超阈值
            recent = [c for c in calls[-ANOMALY_WINDOW:] if c.get("duration_ms", 0) >= threshold_ms]
            if len(recent) >= ANOMALY_WINDOW:
                avg_last = sum(c.get("duration_ms", 0) for c in recent) / len(recent)
                anomalies.append({
                    "tool": name,
                    "consecutive_slow": len(recent),
                    "avg_last_ms": round(avg_last, 2),
                    "total_calls": len(calls),
                    "severity": "high" if avg_last >= threshold_ms * 3 else "medium",
                })

        return sorted(anomalies, key=lambda x: -x["avg_last_ms"])

    def get_report(self, days: int = 7) -> dict:
        """生成延迟报告（供 health_inspect 集成）"""
        slow = self.get_slow_tools(days=days)
        anomalies = self.detect_anomalies(days=days)
        records = self.get_recent(days=days)

        # 总调用统计
        total_calls = len(records)
        total_errors = sum(1 for r in records if not r.get("success", True))
        avg_all = sum(r.get("duration_ms", 0) for r in records) / total_calls if total_calls else 0

        return {
            "total_calls": total_calls,
            "error_count": total_errors,
            "avg_all_ms": round(avg_all, 2),
            "slow_tools": slow,
            "anomalies": anomalies,
            "status": "anomaly" if anomalies else "warning" if slow else "healthy",
        }

    def cleanup(self):
        """清理超期记录（保留最近 RETENTION_DAYS 天）"""
        try:
            if not self.latency_file.exists():
                return
            cutoff = (datetime.now() - timedelta(days=RETENTION_DAYS)).isoformat()
            kept = []
            with open(self.latency_file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                        if entry.get("ts", "") >= cutoff:
                            kept.append(entry)
                    except json.JSONDecodeError:
                        continue
            # 重写文件
            with open(self.latency_file, "w", encoding="utf-8") as f:
                for entry in kept:
                    f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception:
            pass