# -*- coding: utf-8 -*-
"""CheckPoint mixin — 将 CheckPoint 引擎注册为 MCP 可调方法"""
from .shared import VAULT_PATH
from check_point_engine import CheckPointEngine


class CheckPointMixin:
    """CheckPoint 验证混合类"""
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.check_point_engine = CheckPointEngine(VAULT_PATH)
    
    def check_point(self, scope: str = "all") -> dict:
        """
        运行 CheckPoint 检查
        
        Args:
            scope: "all" | "rule5" | "index" | "patrol"
            
        Returns:
            结构化检查报告
        """
        return self.check_point_engine.run_checks(scope=scope)