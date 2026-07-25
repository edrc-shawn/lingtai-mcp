# -*- coding: utf-8 -*-
"""
灵台MCP - 知识管理增强模块
===========================
基于灵台 index.json 的知识引擎，提供查询、图扩散搜索、链接分析等功能。

主要模块：
- MemoryEngine: 记忆引擎（基于index.json的知识查询和图扩散搜索）
- AutoEdge: 链接分析（发现潜在关联和链接建议）
- ReasoningEngine: 推理引擎（数据分析、文章总结、LLM增强）
- LLMReasoning: LLM推理引擎（智能分析、总结、洞察提取）
- TokenMonitor: Token监测（消耗统计、费用估算）
- LingtaiIntegration: 灵台集成模块（统一入口）
"""

try:
    from .memory_engine import MemoryEngine, create_engine
    from .auto_edge import AutoEdge, create_auto_edge
    from .reasoning_engine import ReasoningEngine, create_reasoning_engine
    from .llm_reasoning import LLMReasoning, create_llm_reasoning
    from .token_monitor import TokenMonitor, create_token_monitor
except ImportError:
    from memory_engine import MemoryEngine, create_engine
    from auto_edge import AutoEdge, create_auto_edge
    from reasoning_engine import ReasoningEngine, create_reasoning_engine
    from llm_reasoning import LLMReasoning, create_llm_reasoning
    from token_monitor import TokenMonitor, create_token_monitor

try:
    from .lingtai_integration import LingtaiIntegration, create_lingtai_integration
except ImportError:
    from lingtai_integration import LingtaiIntegration, create_lingtai_integration

__version__ = "2.1.0"
__author__ = "灵台用户"

__all__ = [
    "MemoryEngine",
    "AutoEdge",
    "ReasoningEngine",
    "LLMReasoning",
    "TokenMonitor",
    "LingtaiIntegration",
    "create_engine",
    "create_auto_edge",
    "create_reasoning_engine",
    "create_llm_reasoning",
    "create_token_monitor",
    "create_lingtai_integration",
]


def create_lingtai_mcp(vault_path: str = None):
    """
    创建灵识实例（便捷函数）
    
    Args:
        vault_path: 灵台vault路径
    
    Returns:
        dict: 包含所有模块实例的字典
    """
    return {
        "memory": create_engine(vault_path),
        "auto_edge": create_auto_edge(vault_path),
        "reasoning": create_reasoning_engine(use_llm=True),
        "llm": create_llm_reasoning(),
        "token_monitor": create_token_monitor(),
        "integration": create_lingtai_integration(vault_path),
    }
