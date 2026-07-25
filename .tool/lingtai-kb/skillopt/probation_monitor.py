# -*- coding: utf-8 -*-
"""
probation_monitor.py — 7天观察期 + 自动撤回

职责：
  - 标记新规则的 probation 状态
  - 累计观察期内的误触发次数
  - 误触发 ≥ 2 次 → 自动降级 ⚪ 并记录到 blacklist
  - 7天后 → 转正（去掉 probation 标记）

数据源：
  - observation_engine 的事实积累（判断是否误触发）
  - perception_stats 的命中率统计（判断规则是否活跃）
"""

from typing import Any
from datetime import date, timedelta


class ProbationMonitor:
    """新规则观察期管理。"""

    PROBATION_DAYS = 7
    MAX_MISTRIGGERS = 2

    def check(self, rule: dict, history: list[dict]) -> dict:
        """
        检查规则在观察期内的状态。

        Args:
            rule: 已采纳的规则（含 probation 标记和采纳日期）
            history: 观察期内的触发历史

        Returns:
            dict: 含状态更新的规则（可降级 ⚪ 或转正 ✅）
        """
        result = rule.copy()
        adopted_date = rule.get("adopted_at")
        if not adopted_date:
            return result

        days_since = (date.today() - date.fromisoformat(adopted_date.split("T")[0])).days
        mistriggers = self._count_mistriggers(rule["rule_id"], history)

        if days_since >= self.PROBATION_DAYS:
            result["status"] = "active"
            result.pop("probation", None)
        elif mistriggers >= self.MAX_MISTRIGGERS:
            result["status"] = "demoted"
            result["level"] = "⚪"
        else:
            result["status"] = "probation"
            result["mistriggers"] = mistriggers

        return result

    def _count_mistriggers(self, rule_id: str, history: list[dict]) -> int:
        """统计指定规则的误触发次数。"""
        # TODO: 对接 observation_engine 的实际判断逻辑
        return 0
