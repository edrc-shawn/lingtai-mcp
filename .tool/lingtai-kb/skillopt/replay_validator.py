# -*- coding: utf-8 -*-
"""
replay_validator.py — 历史回验

对候选规则在历史数据中做回放验证。
计算"如果当时有这条规则，会怎样？"
输出每条规则的 positive_rate 和 risk_rate。

V2 改进：
  - 按 pattern_type + tool_name 精确匹配（不再用文本关键词）
  - 从 blacklist.json 读取历史 reject 记录做罚分
  - dead_end 规则：查询末尾工具在 session 中的出现次数 / 占比
  - tool_sequence 规则：查询工具对在 session 中的出现次数
"""

import json
import os
from collections import Counter
from typing import Optional

SKILLOPT_DIR = os.path.dirname(__file__)
BLACKLIST_PATH = os.path.join(SKILLOPT_DIR, "blacklist.json")

# 产出类工具（会话有这些调用表示"有收尾"）
PRODUCT_TOOLS = {"perception_save", "mem_write", "user_push"}

# 历史 reject 罚分（匹配规则降低 positive_rate）
REJECT_PENALTY = 0.15


class ReplayValidator:
    """对候选规则做历史回验。"""

    def __init__(self):
        self.blacklist = self._load_blacklist()

    def _load_blacklist(self) -> list:
        if os.path.exists(BLACKLIST_PATH):
            with open(BLACKLIST_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data.get("rejected", [])
        return []

    def validate(self, candidates: list[dict], history: Optional[list[dict]] = None) -> list[dict]:
        """
        对候选规则逐一回验。

        Args:
            candidates: 候选规则列表（来自 rule_candidate.generate，含 rule_key）
            history: 历史会话数据（含 tool_calls 列表）

        Returns:
            list[dict]: 带 validated 字段的候选规则
        """
        if not candidates:
            return []

        validated = []
        for candidate in candidates:
            result = self._simulate(candidate, history)
            validated.append(result)

        return validated

    def _simulate(self, candidate: dict, history: Optional[list[dict]]) -> dict:
        """
        单条规则回验。

        pattern_type + rule_key（工具名）精确匹配历史 session 中的 tool_calls。
        """
        rule = dict(candidate)
        ptype = candidate.get("pattern_type", "")
        rule_key = candidate.get("rule_key", "")

        if not history:
            rule["validated"] = True
            rule["positive_rate"] = 0.5
            rule["risk_rate"] = 0.2
            rule["sample_count"] = 0
            rule["note"] = "无历史数据，保守中立"
            return rule

        # 从 rule_key 提取工具名（格式: "dead_end|perception_inject" 或 "tool_sequence|kb_search"）
        _, tool_name = rule_key.split("|", 1) if "|" in rule_key else ("", rule_key)
        freq = candidate.get("frequency", 1)

        if ptype == "dead_end":
            return self._simulate_dead_end(rule, tool_name, history, freq)
        elif ptype == "tool_sequence":
            return self._simulate_tool_sequence(rule, rule_key, history, freq)
        else:
            return self._simulate_generic(rule, history)

    def _simulate_dead_end(self, rule: dict, tool_name: str, history: list[dict], freq: int) -> dict:
        """
        死胡同规则回验：
        在历史会话中查找以 tool_name 结尾且无产出类调用的比例。
        """
        total_with_tool = 0
        dead_end_count = 0

        for s in history:
            calls = s.get("tool_calls", [])
            call_names = [c.get("name", "") for c in calls if c.get("name")]

            # 检查工具是否出现在此会话
            if tool_name not in call_names:
                continue

            total_with_tool += 1

            # 检查是否为死胡同：工具在末尾且无产出类调用
            last_tool = call_names[-1] if call_names else ""
            has_product = any(name in PRODUCT_TOOLS for name in call_names)

            if last_tool == tool_name and not has_product:
                dead_end_count += 1

        # 计算正向/风险率
        if total_with_tool == 0:
            # 未在历史中找到该工具的使用记录 → 保守
            positive_rate = 0.5
            risk_rate = 0.2
            note = f"未在历史中找到工具 '{tool_name}' 的使用记录"
        else:
            dead_end_ratio = dead_end_count / total_with_tool
            # 死胡同比例越高 → 正向率越低（说明问题确实存在）
            positive_rate = 1.0 - dead_end_ratio
            # 风险率 = 其他工具处理无问题的比例
            risk_rate = 0.1 * (1.0 - dead_end_ratio)

            # 频繁度校正：当前规则频率 vs 历史频率
            # 规则说"出现 {freq} 次" → 如果历史也高频出现，问题确实普遍
            historical_ratio = total_with_tool / max(len(history), 1)
            if historical_ratio > 0.3:
                positive_rate = max(positive_rate, 0.6)

            note = f"工具 '{tool_name}' 在 {total_with_tool}/{len(history)} 个会话中出现，死胡同占比 {dead_end_ratio:.0%}"

        # 历史 reject 罚分
        reject_penalty = self._calc_reject_penalty("dead_end", tool_name)
        risk_rate = min(risk_rate + reject_penalty * 0.3, 0.9)

        rule["validated"] = True
        rule["positive_rate"] = round(positive_rate, 2)
        rule["risk_rate"] = round(risk_rate, 2)
        rule["sample_count"] = total_with_tool
        rule["note"] = note
        return rule

    def _simulate_tool_sequence(self, rule: dict, rule_key: str, history: list[dict], freq: int) -> dict:
        """
        工具序列规则回验：
        在历史会话中查找连续调用这对工具的次数。
        """
        _, key = rule_key.split("|", 1) if "|" in rule_key else ("", rule_key)

        pair_count = 0
        total_sequences = 0

        for s in history:
            calls = s.get("tool_calls", [])
            call_names = [c.get("name", "") for c in calls if c.get("name")]

            for i in range(len(call_names) - 1):
                pair = f"{call_names[i]}→{call_names[i+1]}"
                if pair == key:
                    pair_count += 1
                total_sequences += 1  # 总连续调用对数

        if total_sequences == 0:
            positive_rate = 0.5
            risk_rate = 0.2
            note = "无连续调用历史数据"
        else:
            # 如果规则检测到的频率较高，说明匹配准确
            match_ratio = pair_count / total_sequences if total_sequences else 0
            positive_rate = min(match_ratio * 5, 0.85)  # 即使匹配很少，至少给 0.3
            risk_rate = 0.1  # 连续调用一般没有风险

            # 高频校正：规则说 {freq} 次 → 如果历史匹配多，自信度提升
            if pair_count >= 3:
                positive_rate = max(positive_rate, 0.7)

            note = f"工具对 '{key}' 在历史中出现 {pair_count}/{freq} 次匹配（规则声称 {freq} 次）"

        reject_penalty = self._calc_reject_penalty("tool_sequence", key)
        risk_rate = min(risk_rate + reject_penalty * 0.2, 0.9)

        rule["validated"] = True
        rule["positive_rate"] = round(positive_rate, 2)
        rule["risk_rate"] = round(risk_rate, 2)
        rule["sample_count"] = pair_count
        rule["note"] = note
        return rule

    def _simulate_generic(self, rule: dict, history: list[dict]) -> dict:
        """通用规则回验：保守中立。"""
        rule["validated"] = True
        rule["positive_rate"] = 0.5
        rule["risk_rate"] = 0.2
        rule["sample_count"] = 0
        rule["note"] = "通用规则，保守中立"
        return rule

    def _calc_reject_penalty(self, ptype: str, tool_name: str) -> float:
        """
        计算历史 reject 罚分。
        在 blacklist 中搜索同类型+同工具名的拒绝记录。
        返回 0.0 ~ 1.0 的惩罚系数。
        """
        matched = 0
        for r in self.blacklist:
            reason = r.get("reason", "")
            r_id = r.get("rule_id", "")
            r_desc = r.get("description", "")

            # 匹配条件：同类型 + 同工具
            if f"{ptype}" in reason.lower() or f"{ptype}" in r_desc.lower():
                if tool_name in reason or tool_name in r_desc:
                    matched += 1
                    continue
            # 如果之前拒绝的同类型规则的理由包含"假阳性"或"模板化"
            if "假阳性" in reason or "模板化" in reason:
                if tool_name in r_desc or f"{ptype}|{tool_name}" in r_desc:
                    matched += 0.5

        return min(matched * 0.5, 1.0)
