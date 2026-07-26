# -*- coding: utf-8 -*-
"""宏工具使用率跟踪器 — 支撑 Phase 3 观察与迭代

职责：
  1. 记录每次宏调用及其内部步骤详情（补全 session_tracker 的粒度缺失）
  2. 提供宏使用率：宏调用次数 / (宏调用次数 + 对应原子工具调用次数)
  3. 提供下钻率：调了宏之后又在同一会话中调对应原子工具的比率

数据源：
  - macro_usage.jsonl：宏调用记录（由 MacroMixin 每步调用后写入）
  - tool_sessions.jsonl：全量工具调用记录（用于计算原子工具基线）

用法：
  tracker = MacroUsageTracker(vault_path)
  tracker.record_macro(macro_name, step_results, session_id)
  stats = tracker.get_stats(hours=24)  # 观察用
"""
import json
import os
from datetime import datetime, timedelta
from collections import defaultdict

# 宏工具 → 对应原子工具集合（用于下钻检测）
MACRO_ATOMIC_MAP = {
    "knowledge_recall": {"knowledge_inject", "knowledge_search", "kb_search", "kb_query", "kar_unified", "kar_chain"},
    "session_end": {"user_feedback", "user_push", "raw_save", "perception_save", "memory_write", "mem_write"},
    "health_check": {"health_inspect", "knowledge_gaps", "knowledge_heatmap", "lifecycle_scan", "concept_collide", "observation_reflect"},
}

# 原子工具 → 归属宏（反向映射）
ATOMIC_TO_MACRO = {}
for macro, atoms in MACRO_ATOMIC_MAP.items():
    for a in atoms:
        ATOMIC_TO_MACRO.setdefault(a, set()).add(macro)


class MacroUsageTracker:
    """宏工具使用率跟踪器"""

    def __init__(self, vault_path: str):
        self.vault = vault_path
        logs_dir = os.path.join(vault_path, ".tool", "lingtai-kb", "logs")
        os.makedirs(logs_dir, exist_ok=True)
        self.macro_log = os.path.join(logs_dir, "macro_usage.jsonl")
        self.tool_log = os.path.join(logs_dir, "tool_sessions.jsonl")

    # ─── 写入 ───

    def record_macro(self, macro_name: str, step_results: list, session_id: str = "", client: str = ""):
        """记录一次宏工具调用及其内部步骤详情"""
        entry = {
            "macro": macro_name,
            "session_id": session_id,
            "client": client,
            "timestamp": datetime.now().isoformat(),
            "steps": [
                {
                    "step": s.get("step", "?"),
                    "status": s.get("status", "?"),
                    "retryable": s.get("retryable", False),
                    "summary": s.get("summary", ""),
                }
                for s in step_results
            ],
            "overall": "ok" if all(s.get("status") == "ok" for s in step_results) else
                       "error" if any(s.get("status") == "error" for s in step_results) else "partial",
        }
        try:
            with open(self.macro_log, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
                f.flush()
        except OSError:
            pass

    # ─── 查询 ───

    def get_stats(self, hours: int = 24, top_n: int = 5) -> dict:
        """获取宏工具使用率统计

        Returns:
            dict: {
                "macro_usage": {macro_name: call_count},
                "drill_down_rates": {macro_name: rate_0to1},
                "macro_coverage": {macro_name: usage_rate_0to1},
                "total_macro_calls": int,
                "total_atomic_in_macro_scope": int,
            }
        """
        cutoff = datetime.now() - timedelta(hours=hours)

        # 1. 读取宏调用记录
        macro_counts = defaultdict(int)
        macro_sessions = defaultdict(set)
        try:
            with open(self.macro_log, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    ts = entry.get("timestamp", "")
                    try:
                        dt = datetime.fromisoformat(ts)
                    except (ValueError, TypeError):
                        continue
                    if dt < cutoff:
                        continue
                    name = entry.get("macro", "?")
                    macro_counts[name] += 1
                    sid = entry.get("session_id", "")
                    if sid:
                        macro_sessions[name].add(sid)
        except OSError:
            pass  # 文件不存在，静默返回空

        # 2. 读取原子工具调用记录（用于计算下钻率）
        # 下钻：同一 session 内，调了宏之后又调了对应原子工具
        atomic_sessions = defaultdict(set)  # atomic_tool → set of session_ids
        try:
            with open(self.tool_log, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    ts = entry.get("timestamp", "")
                    try:
                        dt = datetime.fromisoformat(ts)
                    except (ValueError, TypeError):
                        continue
                    if dt < cutoff:
                        continue
                    sid = entry.get("session_id", "")
                    if not sid:
                        continue
                    for tc in entry.get("tool_calls", []):
                        tname = tc.get("name", "")
                        if tname in ATOMIC_TO_MACRO:
                            atomic_sessions[tname].add(sid)
        except OSError:
            pass

        # 3. 计算下钻率
        drill_down = {}
        for macro_name, sessions in macro_sessions.items():
            atoms = MACRO_ATOMIC_MAP.get(macro_name, set())
            if not sessions:
                drill_down[macro_name] = 0.0
                continue
            # 任何原子工具在同一 session 中出现 = 下钻
            drill_sessions = set()
            for atom in atoms:
                if atom in atomic_sessions:
                    drill_sessions.update(atomic_sessions[atom] & sessions)
            drill_down[macro_name] = round(len(drill_sessions) / len(sessions), 2)

        # 4. 计算宏使用率（在宏覆盖的原子工具范围内）
        # 使用率 = 宏调用次数 / (宏调用次数 + 对应原子工具直接调用次数)
        # 注意：原子工具可能被多个宏覆盖，这里只做粗略统计
        coverage = {}
        total_atomic = 0
        for macro_name, atoms in MACRO_ATOMIC_MAP.items():
            atomic_count = sum(len(atomic_sessions.get(a, set())) for a in atoms)
            macro_count = macro_counts.get(macro_name, 0)
            total_atomic += atomic_count
            total = macro_count + atomic_count
            coverage[macro_name] = round(macro_count / total, 2) if total > 0 else 0.0

        return {
            "macro_usage": dict(macro_counts),
            "drill_down_rates": drill_down,
            "macro_coverage": coverage,
            "total_macro_calls": sum(macro_counts.values()),
            "total_atomic_in_macro_scope": total_atomic,
            "window_hours": hours,
        }