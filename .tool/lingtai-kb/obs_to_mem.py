# -*- coding: utf-8 -*-
"""
观察→记忆晋升通道（薄包装层）
============================
统一入口在 memory_bank/promotion.py::PromotionEngine.promote_from_observations()。
此文件保留为向后兼容的薄包装，新代码应直接调用 PromotionEngine.run_all()。
"""

from memory_bank import MemoryBank
from memory_bank.promotion import PromotionEngine


def promote(vault_path: str = None, min_confidence: float = 0.7, min_facts: int = 5) -> dict:
    """
    薄包装：使用 PromotionEngine.promote_from_observations()
    保持与旧调用方（巡更脚本等）的兼容性。
    """
    mb = MemoryBank(vault_path)
    engine = PromotionEngine(mb)
    return engine.promote_from_observations(
        vault_path=vault_path,
        min_confidence=min_confidence,
        min_facts=min_facts,
    )