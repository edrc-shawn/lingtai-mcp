# -*- coding: utf-8 -*-
"""
lingshi-chunks — 零台结构化索引模块

从丹房页 Markdown 提取结构化知识块（chunk），文件级 JSON 存储。

模块结构：
  core.py          — Shared logic: StructuredChunk, ChunkStore, NaiveSearch, Extractor
  cli.py           — CLI entry: extract, reindex, search, stats, status, show
  mcp_adapter.py   — MCP protocol wrapper (skeleton, ready for FastMCP)

用法：
  python cli.py reindex                   全量重建
  python cli.py search "递归"             搜索
  python cli.py stats                     统计
"""

__version__ = "0.1.0"