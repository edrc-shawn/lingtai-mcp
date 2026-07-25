# -*- coding: utf-8 -*-
"""
lingshi-chunks MCP adapter — 将结构化索引能力封装为 MCP 工具。

当前状态：CLI 先行阶段，此文件是 MCP 适配器骨架。
当结构化索引的提取和检索质量经过 CLI 验证后，
可将此 adapter 注册到现有的 lingshi MCP Server。

与现有 lingshi 的差异：
  lingshi（现有）：index.json → 文件级检索 → 返回整页
  lingshi-chunks（新）：chunks/ → 语义原子级检索 → 返回高密度片段

用法：
    # 独立 MCP server（未来）
    python mcp_adapter.py

    # 或者从主 mcp_server.py 中导入：
    from mcp_adapter import ChunksTools
    tools = ChunksTools(vault_path)
    results = tools.search_chunks("递归")
"""

import json
import os
import sys
from pathlib import Path
from typing import List, Optional

sys.path.insert(0, str(Path(__file__).parent))
from core import StructuredIndex


class ChunksTools:
    """结构化索引的 MCP 工具集（待封装为 FastMCP 或 stdio MCP）。"""

    def __init__(self, vault_path: str):
        self.si = StructuredIndex(vault_path)
        self.si.ensure_dirs()

    def search_chunks(self, query: str, top_k: int = 10, domain: str = "") -> dict:
        """搜索结构化 chunk —— 替代 lingshi 的 inject/query 的增强版。

        Args:
            query: 搜索关键词
            top_k: 最大返回条数
            domain: 域过滤（可选）

        Returns:
            {"ok": true, "results": [...], "total": N}
        """
        filters = {}
        if domain:
            filters["domain"] = domain

        results = self.si.search(query, top_k=top_k, **filters)
        return {
            "ok": True,
            "results": results,
            "total": len(results),
        }

    def extract_chunks(self, md_path: str) -> dict:
        """从一篇丹房页提取结构化 chunk。

        Args:
            md_path: 相对 vault 的丹房页路径

        Returns:
            {"ok": true, "chunks": N, "path": md_path}
        """
        count = self.si.extract(md_path)
        return {
            "ok": True,
            "chunks": count,
            "path": md_path,
        }

    def reindex_all(self) -> dict:
        """全量重建结构化索引。

        Returns:
            {"ok": true, "total_chunks": N}
        """
        total = self.si.reindex_all()
        return {
            "ok": True,
            "total_chunks": total,
        }

    def chunk_stats(self) -> dict:
        """结构化索引统计。"""
        stats = self.si.stats()
        return {
            "ok": True,
            **stats,
        }

    def get_chunk_context(self, query: str, top_k: int = 5) -> dict:
        """为 LLM 组装检索上下文（带覆盖度警告）。

        这是消费端最核心的工具——LLM 需要的不是原始搜索结果，
        而是可直接注入 prompt 的上下文块。

        Args:
            query: 用户原始查询
            top_k: 最大上下文块数

        Returns:
            {
                "ok": true,
                "context": "## 检索到的相关知识\\n...",
                "total_chunks": N,
                "coverage_warning": null 或 "缺失部分：..."
            }
        """
        results = self.si.search(query, top_k=top_k)

        if not results:
            return {
                "ok": True,
                "context": "未检索到相关知识。",
                "total_chunks": 0,
                "coverage_warning": None,
            }

        context_parts = ["## 检索到的相关知识\n"]
        for r in results:
            context_parts.append(
                f"### [{r.get('chunk','')}]\n"
                f"域：{r.get('domain','')}\n"
                f"---\n"
                f"{r.get('content','')}\n"
            )

        # 简单覆盖度警告（后续基于查询意图分解做更精确的检测）
        coverage_warning = None
        if len(results) < 3:
            coverage_warning = (
                f"⚠️ 检索结果较少（{len(results)}条），"
                f"知识覆盖可能不完整。建议补充关键词后重试。"
            )

        return {
            "ok": True,
            "context": "\n".join(context_parts),
            "total_chunks": len(results),
            "coverage_warning": coverage_warning,
        }


# ── 独立 MCP Server 入口（待接入 FastMCP 框架） ──

def main():
    """待实现：注册为 stdio MCP server。"""
    print("lingshi-chunks MCP Server")
    print("当前为 adapter 骨架，请通过 CLI 验证后再封装为 MCP 协议。")
    print()
    print("用法：")
    print("  python cli.py extract <path>")
    print("  python cli.py search <query>")
    print("  python cli.py reindex")
    print("  python cli.py stats")


if __name__ == "__main__":
    main()