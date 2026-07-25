# -*- coding: utf-8 -*-
"""
rule_candidate.py — 候选规则生成

职责：
  1. 去重：与已存在的感知规则比较，避免生成重复规则
  2. 冲突检测：与现有规则/blacklist 比对，排除冲突项
  3. 生成自然语言规则描述：将 pattern 转为可读的感知规则文本
  4. 硬约束检查：确保不生成禁止类型的规则（不改台律/品级/输出流水线等）
  5. 低频死胡同过滤：同一工具死胡同出现 <3 次不生成（避免模板噪音）

V2 改进：
  - action 按 pattern_type + tool 生成差异化建议，不再笼统模板
  - dead_end 同工具合并（多条归一条，记累计频次）
  - tool_sequence 同工具重复调用 vs 跨工具给出不同建议
"""

import json
import os
import re
from collections import Counter
from typing import Optional

SKILLOPT_DIR = os.path.dirname(__file__)
BLACKLIST_PATH = os.path.join(SKILLOPT_DIR, "blacklist.json")
VAULT_PATH = os.environ.get("LINGTAI_VAULT", r".")
RULES_PATH = os.path.join(VAULT_PATH, "感知规则.md")

# 永不自动生成的规则类型
FORBIDDEN_PATTERNS = [
    "delete_", "modify_knowledge", "alter_tailu",
    "change_grade", "change_output_pipeline",
]

# 同一工具死胡同的最低出现次数，低于此值不生成规则
DEAD_END_MIN_FREQUENCY = 3

# 各工具的收尾建议（精确化 action）
DEAD_END_ACTIONS = {
    "perception_inject": {
        "action": "知识注入后建议接 `kar_unified` 或 `kb_search` 将匹配知识整合到回复，而非仅调 inject 就结束",
        "risk": "high",
    },
    "kb_search": {
        "action": "搜索后建议输出结果摘要或调用 `raw_save` 保存新发现的事实",
        "risk": "medium",
    },
    "kb_query": {
        "action": "查询后建议将匹配知识融入回复，或将查询结果汇总为一句结论输出",
        "risk": "medium",
    },
    "kar_unified": {
        "action": "统一查询后建议将跨域关联结果整合到回复中，或调用 `raw_save` 记录发现",
        "risk": "medium",
    },
    "kar_chain": {
        "action": "链式查询后建议检查是否触发了新发现，有则 `raw_save`，无则输出查询回执",
        "risk": "medium",
    },
    "sys_rules": {
        "action": "查看规则后建议执行对应动作——检查到规则存在就执行，不存在就提供替代方案",
        "risk": "low",
    },
    "sys_sop": {
        "action": "查看 SOP 后建议执行对应操作步骤",
        "risk": "low",
    },
    "mem_query": {
        "action": "记忆查询后建议输出查询结果摘要，如有匹配条目则调用 `memory_write` 记录新关联",
        "risk": "low",
    },
    "user_feedback": {
        "action": "用户反馈后建议根据反馈内容执行后续操作（修正回答 or 确认存储）",
        "risk": "low",
    },
    "obs_list": {
        "action": "查看观察列表后建议选取高置信度观察调用 `raw_save` 存入知识库",
        "risk": "low",
    },
    "obs_perception_stats": {
        "action": "查看统计后建议将关键指标写回会话上下文或生成仪表盘摘要",
        "risk": "low",
    },
    "obs_sentinel": {
        "action": "查看哨兵报告后建议根据异常项执行对应修复步骤",
        "risk": "low",
    },
    "reflect": {
        "action": "反思后建议将反思结论输出为结构化内容或存入原料目录",
        "risk": "low",
    },
    "kb_domains": {
        "action": "查看域列表后建议选取目标域执行深入查询（`kb_pages` 或 `kb_query`）",
        "risk": "low",
    },
    "kb_pages": {
        "action": "查看页面列表后建议选取目标页面读取内容或分析链接关系",
        "risk": "low",
    },
    "sys_token": {
        "action": "查看 Token 统计后建议优化查询策略或精简上下文",
        "risk": "low",
    },
}

# 工具序列建议
TOOL_SEQUENCE_ACTIONS = {
    "stat_to_stat": {
        "action": "连续统计类调用，建议合并为一次 `stats` 调用并将结果分段展示",
        "risk": "low",
    },
    "default": {
        "action": "连续工具调用链路，建议评估是否存在冗余，尝试将串行优化为并行或合并",
        "risk": "low",
    },
}


class RuleCandidate:
    """从检测到的模式生成候选规则。"""

    def __init__(self):
        self.blacklist = self._load_blacklist()
        self.staged_rule_ids = self._load_staged_ids()
        self.adopted_sources = self._load_adopted_sources()

    def _load_blacklist(self) -> list:
        if os.path.exists(BLACKLIST_PATH):
            with open(BLACKLIST_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data.get("rejected", [])
        return []

    def _load_staged_ids(self) -> set:
        """读取当前 staged 目录中的 rule_id，避免重复生成。"""
        staged_dir = os.path.join(SKILLOPT_DIR, "staged")
        if not os.path.isdir(staged_dir):
            return set()
        ids = set()
        for dated_dir in os.listdir(staged_dir):
            ddir = os.path.join(staged_dir, dated_dir)
            if not os.path.isdir(ddir):
                continue
            for fn in os.listdir(ddir):
                if fn.startswith("R") and fn.endswith(".md"):
                    ids.add(fn)
        return ids

    def _load_adopted_sources(self) -> set:
        """读取已采纳规则（感知规则.md）的 source_pattern 集合，避免重复生成已采纳规则。"""
        sources = set()
        if not os.path.exists(RULES_PATH):
            return sources
        try:
            with open(RULES_PATH, "r", encoding="utf-8") as f:
                for line in f:
                    m = re.search(r"来源\**\s*[:：]\s*([A-Za-z0-9_]+)", line)
                    if m:
                        sources.add(m.group(1))
        except OSError:
            pass
        return sources

    def _max_rule_num(self) -> int:
        """已采纳规则 + 已 staged 规则中的最大 R 编号，用于续编，避免编号冲突。"""
        max_n = 0
        # 已采纳（感知规则.md 的 ### Rxx）
        if os.path.exists(RULES_PATH):
            try:
                with open(RULES_PATH, "r", encoding="utf-8") as f:
                    for line in f:
                        m = re.match(r"###\s*R(\d+)", line)
                        if m:
                            max_n = max(max_n, int(m.group(1)))
            except OSError:
                pass
        # 已 staged（staged/<date>/Rxx_*.md）
        staged_dir = os.path.join(SKILLOPT_DIR, "staged")
        if os.path.isdir(staged_dir):
            for ddir in os.listdir(staged_dir):
                sub = os.path.join(staged_dir, ddir)
                if not os.path.isdir(sub):
                    continue
                for fn in os.listdir(sub):
                    m = re.match(r"R(\d+)", fn)
                    if m:
                        max_n = max(max_n, int(m.group(1)))
        return max_n

    def generate(self, patterns: list[dict]) -> list[dict]:
        """从 pattern 列表生成候选规则。"""
        # 预处理：合并同工具死胡同
        patterns = self._merge_dead_ends(patterns)

        candidates = []
        idx = self._max_rule_num()  # 续编：从已采纳/已staged 的最大编号之后开始，避免 R 编号冲突
        for p in patterns:
            if not self._is_valid(p):
                continue
            idx += 1
            candidates.append(self._build_rule(idx, p))

        return candidates

    def _is_valid(self, pattern: dict) -> bool:
        """去重 + 冲突检测 + 硬约束检查 + 低频过滤。"""
        desc = pattern.get("description", "")
        ptype = pattern.get("type", "")
        freq = pattern.get("frequency", 0)
        seq = pattern.get("sequence", [])
        last_call = seq[-1] if seq else ""

        # 硬约束禁止
        for forbid in FORBIDDEN_PATTERNS:
            if forbid in desc:
                return False

        # 同工具连续调用（X→X）永久封杀：已被拒绝 4 次（R01/R02/R10/R11），
        # 每次换工具名就绕过黑名单精确匹配，直接在源头拦截
        if ptype == "tool_sequence" and len(seq) == 2 and seq[0] == seq[1]:
            return False

        # 已采纳规则去重：source_pattern 已在感知规则.md 中 → 跳过（不重复生成）
        if pattern.get("pattern_id", "") in self.adopted_sources:
            return False

        # 死胡同低频过滤：同工具出现 < DEAD_END_MIN_FREQUENCY 不生成
        if ptype == "dead_end" and freq < DEAD_END_MIN_FREQUENCY:
            return False

        # blacklist 匹配：按 pattern_type + 工具名/描述判断
        for rejected in self.blacklist:
            r_desc = rejected.get("description", "")
            if r_desc and r_desc in desc:
                return False
            # 同 source_pattern 已被拒绝
            r_source = rejected.get("source_pattern", "")
            if r_source and r_source in desc:
                return False
            # 新式拒单：按 pattern_type + tool_name 匹配
            r_ptype = rejected.get("pattern_type", "")
            r_tool = rejected.get("tool_name", "")
            if r_ptype and r_tool:
                if r_ptype == ptype and last_call == r_tool:
                    return False

        return True

    def _merge_dead_ends(self, patterns: list[dict]) -> list[dict]:
        """合并同工具死胡同模式：多条相同 last_call 合并为一条，累加 frequency。"""
        dead_ends = [p for p in patterns if p.get("type") == "dead_end"]
        others = [p for p in patterns if p.get("type") != "dead_end"]

        if not dead_ends:
            return patterns

        merged = {}
        for de in dead_ends:
            last_call = de.get("sequence", [""])[-1]
            if last_call not in merged:
                merged[last_call] = dict(de)
                merged[last_call]["frequency"] = 1
                merged[last_call]["pattern_id"] = f"deadend_{last_call}"
            else:
                merged[last_call]["frequency"] += 1

        return others + list(merged.values())

    def _build_rule(self, index: int, pattern: dict) -> dict:
        """将 pattern 转为规则格式，按类型 + 工具生成差异化 action。"""
        ptype = pattern.get("type", "")
        seq = pattern.get("sequence", [])
        freq = pattern.get("frequency", 1)
        last_call = seq[-1] if seq else ""
        first_call = seq[0] if seq else ""

        # rule_key 承载可回验标识：tool_sequence 用完整工具对（含 →），
        # 其余用末步工具名。replay_validator 据此匹配历史 tool_calls 序列。
        if ptype == "tool_sequence" and len(seq) >= 2:
            rule_key = f"{ptype}|{seq[0]}→{seq[1]}"
        else:
            rule_key = f"{ptype}|{last_call or first_call}"

        action_data = {}
        if ptype == "tool_sequence" and len(seq) >= 2:
            # 同工具连续调用已在 _is_valid 硬过滤拦截，此处仅处理跨工具序列
            if any(kw in seq[1] for kw in ("stats", "status")):
                action_data = TOOL_SEQUENCE_ACTIONS["stat_to_stat"]
                trigger = f"用户调用 {seq[0]} 后调用 {seq[1]} 时（出现 {freq} 次）"
            else:
                action_data = TOOL_SEQUENCE_ACTIONS["default"]
                trigger = f"用户连续调用 {seq[0]} → {seq[1]} 时（出现 {freq} 次）"

        elif ptype == "dead_end" and last_call:
            action_data = DEAD_END_ACTIONS.get(last_call, {
                "action": f"调用 {last_call} 后建议检查是否需要执行产出步骤（save/analyze/output）",
                "risk": "medium",
            })
            trigger = f"调用 {last_call} 后无后续产出（累计 {freq} 次）"

        elif ptype == "cooccurrence":
            action_data = {
                "action": f"当同时出现「{'」「'.join(seq)}」时，建议合并加载上下文以减少跨域查询",
                "risk": "low",
            }
            trigger = f"用户涉及「{'」「'.join(seq)}」相关内容时"

        elif ptype == "knowledge_gap":
            action_data = {
                "action": "建议在原料目录补充该方向的结构化采集，或调用 Tavily 搜索外部资料填充缺口",
                "risk": "medium",
            }
            trigger = f"用户反复触及同一缺口方向时"

        else:
            action_data = {"action": pattern.get("action", ""), "risk": "medium"}
            trigger = pattern.get("trigger", "")

        return {
            "rule_id": f"R{index:02d}",
            "rule_key": rule_key,
            "description": pattern.get("description", ""),
            "trigger": trigger,
            "action": action_data.get("action", ""),
            "risk": action_data.get("risk", "medium"),
            "source_pattern": pattern.get("pattern_id", ""),
            "pattern_type": ptype,
            "tool_name": last_call,
            "frequency": freq,
            "weight": pattern.get("weight", 0.5),
            "generated_at": __import__("datetime").datetime.now().isoformat(),
        }
