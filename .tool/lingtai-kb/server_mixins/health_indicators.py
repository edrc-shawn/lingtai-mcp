# -*- coding: utf-8 -*-
"""系统健康指标 — 轻量仪表盘（Phase 1a）

提供 system_health_indicators 计算函数，供每日检/巡更调用。
不驱动行为，只驱动提醒和建议。
"""

import os
import json
from datetime import datetime, timedelta
from pathlib import Path


def compute_indicators(vault_path: str) -> dict:
    """计算系统健康度指标。

    Args:
        vault_path: 知识库根目录

    Returns:
        dict: {
            "ingestion_backlog": 待提炼原料数,
            "knowledge_coverage": 知识图节点覆盖率,
            "tool_failure_rate_7d": 近7日工具失败率,
            "tool_health": {工具名: {calls, failures, retries, success_rate}},
            "last_session_gap_days": 距上次会话天数,
            "timestamp": 计算时间,
        }
    """
    now = datetime.now()
    vault = Path(vault_path) if not isinstance(vault_path, Path) else vault_path

    return {
        "ingestion_backlog": _count_pending_raw(vault),
        "tool_health": _aggregate_tool_health(vault, now),
        "last_session_gap_days": _last_session_gap(vault, now),
        "timestamp": now.isoformat(),
    }


def _count_pending_raw(vault: Path) -> int:
    """统计待提炼原料量（处理状态 != 已提炼 的原料文件数）"""
    raw_dir = vault / "原料"
    if not raw_dir.is_dir():
        return 0
    count = 0
    for f in raw_dir.rglob("*.md"):
        try:
            content = f.read_text(encoding="utf-8", errors="ignore")
            if "处理状态: 已提炼" not in content:
                count += 1
        except Exception:
            count += 1
    return count


def _aggregate_tool_health(vault: Path, now: datetime) -> dict:
    """从 tool_sessions.jsonl 聚合近7日工具健康度。"""
    log_path = vault / ".tool" / "lingtai-kb" / "logs" / "tool_sessions.jsonl"
    if not log_path.is_file():
        return {}

    cutoff = now - timedelta(days=7)
    stats = {}  # {tool_name: {"calls": 0, "failures": 0, "retries": 0, "errors": []}}

    try:
        with open(log_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue

                ts_str = entry.get("timestamp", "")
                try:
                    ts = datetime.fromisoformat(ts_str)
                except (ValueError, TypeError):
                    continue
                if ts < cutoff:
                    continue

                outcome = entry.get("outcome", "success")
                retry = entry.get("retry_count", 0)
                for tc in entry.get("tool_calls", []):
                    name = tc.get("name", "unknown")
                    if name not in stats:
                        stats[name] = {"calls": 0, "failures": 0, "retries": 0}
                    stats[name]["calls"] += 1
                    if outcome in ("failure", "error"):
                        stats[name]["failures"] += 1
                    if retry > 0:
                        stats[name]["retries"] += retry
    except OSError:
        return {}

    # 计算成功率
    result = {}
    for name, s in stats.items():
        success_rate = round((s["calls"] - s["failures"]) / max(s["calls"], 1) * 100, 1)
        result[name] = {
            "calls": s["calls"],
            "failures": s["failures"],
            "retries": s["retries"],
            "success_rate": success_rate,
        }
    return result


def _last_session_gap(vault: Path, now: datetime) -> float:
    """距上次工具调用的天数"""
    log_path = vault / ".tool" / "lingtai-kb" / "logs" / "tool_sessions.jsonl"
    if not log_path.is_file():
        return 0.0

    latest = None
    try:
        with open(log_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    ts = datetime.fromisoformat(entry.get("timestamp", ""))
                    if latest is None or ts > latest:
                        latest = ts
                except (json.JSONDecodeError, ValueError, TypeError):
                    continue
    except OSError:
        return 0.0

    if latest is None:
        return 0.0
    return round((now - latest).total_seconds() / 86400, 1)