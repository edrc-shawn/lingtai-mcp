# -*- coding: utf-8 -*-
"""
灵识 memory_bank - 跨域生命周期（记忆↔知识协同可观测）
===================================================
1. VolatilityDetector：保守启发式检测「易变物/一次性/气话」内容，
   用于反向泄漏率监控——这类内容漏进丹房会污染知识图。
2. KnowledgeLedger：把每次知识写入（page_create / knowledge_save）登记到
   data/leak_ledger.jsonl，供 memory_lifecycle() 计算反向泄漏率。
"""

import os
import json
import hashlib
from datetime import datetime, timedelta
from pathlib import Path
from typing import Tuple, List, Dict, Optional

# ═══════════════════════════════════════════════════════
# 反向泄漏检测：保守启发式
# 设计原则：宁漏勿误判。只拦明确的易变/情绪/未定/碎片，
# 不拦稳定事实（如截止日期、项目名、决策结论）。
# ═══════════════════════════════════════════════════════

# 情绪/发泄类（气话、吐槽）——明显不该进知识库
_VOLATILE_EMOTION = [
    "气死", "烦死", "烦死", "郁闷", "吐槽", "卧槽", "我靠", "妈的", "去他的",
    "滚", "垃圾", "太烂", "烂透了", "无语", "崩溃", "想哭", "想死",
]
# 未定/犹豫类（尚未成为事实）
_VOLATILE_HEDGE = [
    "可能吧", "也许吧", "应该吧", "大概吧", "说不定", "还没定", "待定",
    "不确定", "等下", "等会", "再说吧", "看情况", "随便", "算了", "管他",
    "估计是", "可能是", "应该是",
]
# 瞬时自我状态（转瞬即逝，非长期事实）
_VOLATILE_SELF_STATE = [
    "我刚", "我现在", "我心情", "我累了", "我困了", "我想睡", "我饿了",
    "我渴了", "我烦", "我生气", "我不开心", "我高兴", "我难过",
]
# 极短碎片阈值（字符数，含标点）
_VOLATILE_MIN_LEN = 6


class VolatilityDetector:
    """保守波动性检测——判定内容是否「易变物」，不该直接落成知识。"""

    @staticmethod
    def is_volatile(content: str) -> Tuple[bool, str]:
        """
        返回 (是否易变, 命中原因)。
        命中任一类即判为易变；否则稳定。
        """
        if not content or not content.strip():
            return True, "empty"

        c = content.strip()

        # 1) 极短碎片（无结构、无信息量）
        if len(c) < _VOLATILE_MIN_LEN:
            return True, "too_short"

        # 2) 疑问句（在问，不在陈述事实）
        if c.endswith("？") or c.endswith("?") or c.endswith("吗") or c.endswith("嘛"):
            # 排除「X 是什么/为什么」式求知问（这种反而值得记成知识缺口）
            if not (c.startswith("为什么") or c.startswith("怎么") or c.startswith("什么是")
                    or c.startswith("如何") or c.startswith("什么是")):
                return True, "question"

        # 3) 情绪/发泄
        for kw in _VOLATILE_EMOTION:
            if kw in c:
                return True, f"emotion:{kw}"

        # 4) 未定/犹豫
        for kw in _VOLATILE_HEDGE:
            if kw in c:
                return True, f"hedge:{kw}"

        # 5) 瞬时自我状态
        for kw in _VOLATILE_SELF_STATE:
            if c.startswith(kw) or f" {kw}" in c:
                return True, f"self_state:{kw}"

        return False, ""


# ═══════════════════════════════════════════════════════
# 知识写入台账（反向泄漏率数据源）
# ═══════════════════════════════════════════════════════

# 泄漏台账路径：默认 data/leak_ledger.jsonl；可通过环境变量 LINGTAI_LEAK_LEDGER
# 重定向（测试隔离），或运行时调用 set_ledger_path() 重定向。
_LEDGER_PATH = os.environ.get(
    "LINGTAI_LEAK_LEDGER",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "leak_ledger.jsonl"),
)


def set_ledger_path(path: str) -> None:
    """
    重定向泄漏台账到指定路径（测试隔离用）。
    传入临时文件路径后，本进程内 record_knowledge_write/read_ledger
    全部走新路径，不影响真实 data/leak_ledger.jsonl。
    """
    global _LEDGER_PATH
    _LEDGER_PATH = os.path.abspath(path)
    os.makedirs(os.path.dirname(_LEDGER_PATH), exist_ok=True)


def _now_iso() -> str:
    return datetime.now().isoformat()


def record_knowledge_write(tool: str, path: str, content: str) -> Dict:
    """
    登记一次知识写入（page_create / knowledge_save），
    跑波动性检测并追加到 leak_ledger.jsonl。

    返回 {volatile, reason, recorded} —— 调用方可据此在返回里加 volatile_warning。
    """
    volatile, reason = VolatilityDetector.is_volatile(content)
    entry = {
        "t": _now_iso(),
        "tool": tool,
        "path": path,
        "volatile": volatile,
        "reason": reason,
        "hash8": hashlib.md5(content.encode("utf-8")).hexdigest()[:8],
    }
    try:
        os.makedirs(os.path.dirname(_LEDGER_PATH), exist_ok=True)
        with open(_LEDGER_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        return {**entry, "recorded": False}
    return {**entry, "recorded": True}


def read_ledger(window_hours: int = 0) -> Dict:
    """
    读取泄漏台账，计算反向泄漏率。

    Args:
        window_hours: 0 = 全量；>0 = 仅统计最近 N 小时。
    Returns:
        {total, volatile, stable, rate, by_tool, recent_volatile: [...]}
    """
    entries: List[Dict] = []
    if os.path.isfile(_LEDGER_PATH):
        try:
            with open(_LEDGER_PATH, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entries.append(json.loads(line))
                    except Exception:
                        continue
        except Exception:
            pass

    cutoff = None
    if window_hours > 0:
        cutoff = datetime.now() - timedelta(hours=window_hours)

    if cutoff is not None:
        filtered = []
        for e in entries:
            ts = e.get("t", "")
            try:
                dt = datetime.fromisoformat(ts)
            except Exception:
                continue
            if dt >= cutoff:
                filtered.append(e)
        entries = filtered

    total = len(entries)
    volatile = sum(1 for e in entries if e.get("volatile"))
    stable = total - volatile
    rate = round(volatile / total, 4) if total else 0.0

    by_tool: Dict[str, Dict] = {}
    for e in entries:
        t = e.get("tool", "unknown")
        slot = by_tool.setdefault(t, {"total": 0, "volatile": 0})
        slot["total"] += 1
        if e.get("volatile"):
            slot["volatile"] += 1

    recent_volatile = [
        {"t": e.get("t"), "tool": e.get("tool"), "path": e.get("path"), "reason": e.get("reason")}
        for e in entries if e.get("volatile")
    ][-10:]

    return {
        "total": total,
        "volatile": volatile,
        "stable": stable,
        "rate": rate,
        "by_tool": by_tool,
        "window_hours": window_hours,
        "recent_volatile": recent_volatile,
    }


if __name__ == "__main__":
    tests = [
        "截止日期是7月15号",
        "气死我了这破需求",
        "可能吧，再看看",
        "用户偏好简洁回复",
        "？",
        "我刚想到一个事",
    ]
    for t in tests:
        print(t, "->", VolatilityDetector.is_volatile(t))
