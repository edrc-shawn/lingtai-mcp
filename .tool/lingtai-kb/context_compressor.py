# -*- coding: utf-8 -*-
"""
灵识 context_compressor - 上下文压缩层
=======================================
解决Context Engineering差距1：显式的上下文窗口管理策略

核心功能：
1. 低品级检索结果优先丢弃
2. 历史对话片段替换为摘要
3. 监控token消耗，触发压缩阈值
4. 上下文预算分配（给RAG留多少、给工具返回留多少）
"""

import json
import re
from pathlib import Path
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Tuple


# 配置常量
DEFAULT_MAX_TOKENS = 8000        # 默认上下文窗口上限
COMPRESSION_THRESHOLD = 0.8      # 触发压缩的使用率阈值
AGGRESSIVE_THRESHOLD = 0.95     # 激进压缩阈值
SUMMARY_RATIO = 0.3             # 压缩后摘要占原文比例


class ContextCompressor:
    """上下文压缩引擎"""

    def __init__(self, vault_path: str = None):
        if vault_path is None:
            vault_path = r"."
        self.vault_path = vault_path
        self.config_path = Path(__file__).parent / ".cache" / "compressor_config.json"
        self.config = self._load_config()

    def _load_config(self) -> dict:
        if self.config_path.exists():
            try:
                return json.loads(self.config_path.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {
            "max_tokens": DEFAULT_MAX_TOKENS,
            "compression_threshold": COMPRESSION_THRESHOLD,
            "aggressive_threshold": AGGRESSIVE_THRESHOLD,
        }

    def _save_config(self):
        self.config_path.parent.mkdir(exist_ok=True)
        self.config_path.write_text(
            json.dumps(self.config, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )

    # === 核心压缩方法 ===

    def compress_context(self, context_items: List[dict], budget: int = None) -> dict:
        """
        压缩上下文内容到指定预算内

        Args:
            context_items: 上下文条目列表
                [{"type": "rag/tool/dialog/rule", "content": "...", "priority": 0.0-1.0, "token_count": int}]
            budget: token预算（默认用config.max_tokens）

        Returns:
            dict: {items: 压缩后的条目, dropped: 丢弃的条目, summary: 摘要, stats: 统计}
        """
        if budget is None:
            budget = self.config["max_tokens"]

        total_tokens = sum(item.get("token_count", self._estimate_tokens(item["content"])) for item in context_items)
        stats = {
            "original_count": len(context_items),
            "original_tokens": total_tokens,
            "budget": budget,
            "usage_ratio": round(total_tokens / budget, 3) if budget > 0 else 1.0,
        }

        # 如果没超预算，直接返回
        if total_tokens <= budget:
            return {"items": context_items, "dropped": [], "summary": None, "stats": stats}

        # 超预算，开始压缩
        sorted_items = sorted(context_items, key=lambda x: x.get("priority", 0.5))

        dropped = []
        kept = []
        current_tokens = 0

        for item in sorted_items:
            item_tokens = item.get("token_count", self._estimate_tokens(item["content"]))

            # 规则类上下文永不丢弃
            if item.get("type") == "rule":
                kept.append(item)
                current_tokens += item_tokens
                continue

            # 低优先级先丢
            if current_tokens + item_tokens > budget * 0.9:  # 留10%余量
                dropped.append(item)
            else:
                kept.append(item)
                current_tokens += item_tokens

        # 如果丢了还不够，对最长的条目做摘要
        if current_tokens > budget:
            kept = self._summarize_longest(kept, budget)

        stats["compressed_count"] = len(kept)
        stats["dropped_count"] = len(dropped)
        stats["compressed_tokens"] = sum(
            item.get("token_count", self._estimate_tokens(item["content"])) for item in kept
        )

        return {
            "items": kept,
            "dropped": dropped,
            "summary": self._generate_compression_summary(dropped),
            "stats": stats,
        }

    def prioritize_results(self, results: list, max_tokens: int = None) -> list:
        """
        对检索结果按品级+相关度排序，截断到token预算内

        Args:
            results: 搜索结果列表（来自灵识query/search）
            max_tokens: token预算

        Returns:
            list: 排序+截断后的结果
        """
        if max_tokens is None:
            max_tokens = self.config["max_tokens"] // 2  # RAG最多占一半

        # 按品级排序（上品优先）
        grade_order = {"上品": 0, "中品": 1, "下品": 2, "": 3}
        sorted_results = sorted(results, key=lambda x: grade_order.get(x.get("grade", ""), 3))

        # 截断到预算
        kept = []
        tokens_used = 0
        for r in sorted_results:
            r_tokens = self._estimate_tokens(json.dumps(r, ensure_ascii=False))
            if tokens_used + r_tokens <= max_tokens:
                kept.append(r)
                tokens_used += r_tokens
            else:
                break

        return kept

    def check_token_budget(self, current_tokens: int) -> dict:
        """
        检查当前token使用情况，返回压缩建议

        Returns:
            dict: {level, action, message}
        """
        budget = self.config["max_tokens"]
        ratio = current_tokens / budget if budget > 0 else 1.0

        if ratio < self.config["compression_threshold"]:
            return {"level": "safe", "action": "none", "message": "上下文使用正常", "ratio": ratio}
        elif ratio < self.config["aggressive_threshold"]:
            return {
                "level": "warning",
                "action": "compress",
                "message": f"上下文使用 {ratio:.0%}，建议压缩低优先级内容",
                "ratio": ratio,
            }
        else:
            return {
                "level": "critical",
                "action": "aggressive_compress",
                "message": f"上下文使用 {ratio:.0%}，必须激进压缩",
                "ratio": ratio,
            }

    # === 内部方法 ===

    def _estimate_tokens(self, text: str) -> int:
        """粗略估算token数（中英文混合：约2字符/中文token，4字符/英文token）"""
        if not text:
            return 0
        cn_chars = len(re.findall(r'[\u4e00-\u9fff]', text))
        en_chars = len(text) - cn_chars
        return cn_chars + max(1, en_chars // 4)

    def _summarize_longest(self, items: list, budget: int) -> list:
        """对最长的条目做压缩摘要"""
        # 简化版：直接截断到summary_ratio比例
        compressed = []
        for item in items:
            tokens = item.get("token_count", self._estimate_tokens(item["content"]))
            if tokens > 200:  # 超过200token的条目才压缩
                max_chars = int(len(item["content"]) * SUMMARY_RATIO)
                item = dict(item)  # 不修改原对象
                item["content"] = item["content"][:max_chars] + "..."
                item["compressed"] = True
            compressed.append(item)
        return compressed

    def _generate_compression_summary(self, dropped: list) -> str:
        """生成压缩摘要"""
        if not dropped:
            return ""
        types = {}
        for item in dropped:
            t = item.get("type", "unknown")
            types[t] = types.get(t, 0) + 1
        parts = [f"{t}:{n}" for t, n in types.items()]
        return f"已压缩丢弃 {len(dropped)} 条内容（{', '.join(parts)}）"

    # === 配置 ===

    def set_budget(self, max_tokens: int):
        """设置上下文窗口预算"""
        self.config["max_tokens"] = max_tokens
        self._save_config()

    def get_config(self) -> dict:
        return self.config
