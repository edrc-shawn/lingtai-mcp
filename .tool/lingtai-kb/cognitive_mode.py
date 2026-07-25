# -*- coding: utf-8 -*-
"""
灵台MCP - 认知模式切换模块
============================
基于 MetaCog 设计，支持不同认知模式下的感知规则权重调整。

模式：
- quick: 快速模式，最少API调用
- standard: 标准模式，平衡质量和效率
- deep: 深度模式，最大API调用，最高质量

不同模式下，感知规则的触发阈值和权重不同。
"""

import json
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional


# 模式配置
MODES = {
    "quick": {
        "name": "快速模式",
        "description": "最少API调用，适合快速查询",
        "rules": {
            "rule1_inject": {"threshold": 0.7, "weight": 0.5},  # 高阈值，低权重
            "rule2_learn": {"threshold": 0.8, "weight": 0.3},   # 高阈值，低权重
            "rule3_recommend": {"threshold": 0.6, "weight": 0.4},  # 中阈值，中权重
            "rule4_context": {"enabled": False},  # 禁用
            "rule5_search": {"threshold": 0.5, "weight": 0.3},  # 中阈值，低权重
        },
        "max_api_calls": 5,
    },
    "standard": {
        "name": "标准模式",
        "description": "平衡质量和效率",
        "rules": {
            "rule1_inject": {"threshold": 0.5, "weight": 1.0},  # 中阈值，标准权重
            "rule2_learn": {"threshold": 0.6, "weight": 0.8},   # 中阈值，中权重
            "rule3_recommend": {"threshold": 0.4, "weight": 1.0},  # 中阈值，标准权重
            "rule4_context": {"enabled": True},  # 启用
            "rule5_search": {"threshold": 0.3, "weight": 1.0},  # 低阈值，标准权重
        },
        "max_api_calls": 15,
    },
    "deep": {
        "name": "深度模式",
        "description": "最大API调用，最高质量",
        "rules": {
            "rule1_inject": {"threshold": 0.3, "weight": 1.5},  # 低阈值，高权重
            "rule2_learn": {"threshold": 0.4, "weight": 1.2},   # 低阈值，高权重
            "rule3_recommend": {"threshold": 0.2, "weight": 1.5},  # 低阈值，高权重
            "rule4_context": {"enabled": True},  # 启用
            "rule5_search": {"threshold": 0.2, "weight": 1.5},  # 低阈值，高权重
        },
        "max_api_calls": 50,
    },
}


class CognitiveMode:
    """认知模式管理器"""
    
    def __init__(self):
        self.current_mode = "standard"
        self.mode_history = []
    
    def set_mode(self, mode: str) -> dict:
        """
        设置认知模式
        
        Args:
            mode: 模式名称（quick/standard/deep）
        
        Returns:
            dict: 设置结果
        """
        if mode not in MODES:
            return {"success": False, "error": f"未知模式: {mode}"}
        
        old_mode = self.current_mode
        self.current_mode = mode
        
        # 记录历史
        self.mode_history.append({
            "from": old_mode,
            "to": mode,
            "timestamp": datetime.now().isoformat(),
        })
        
        return {
            "success": True,
            "mode": mode,
            "name": MODES[mode]["name"],
            "description": MODES[mode]["description"],
        }
    
    def get_mode(self) -> dict:
        """获取当前模式"""
        mode_config = MODES[self.current_mode]
        return {
            "mode": self.current_mode,
            "name": mode_config["name"],
            "description": mode_config["description"],
            "rules": mode_config["rules"],
            "max_api_calls": mode_config["max_api_calls"],
        }
    
    def get_mode_config(self, mode: str = None) -> dict:
        """获取模式配置"""
        if mode is None:
            mode = self.current_mode
        return MODES.get(mode, MODES["standard"])
    
    def get_history(self) -> list:
        """获取模式切换历史"""
        return self.mode_history
    
    def suggest_mode(self, task_type: str) -> str:
        """
        根据任务类型建议模式
        
        Args:
            task_type: 任务类型（query/refine/analyze）
        
        Returns:
            str: 建议的模式
        """
        suggestions = {
            "query": "quick",
            "refine": "standard",
            "analyze": "deep",
            "reflect": "deep",
            "save": "quick",
        }
        return suggestions.get(task_type, "standard")


# 便捷函数
def create_cognitive_mode() -> CognitiveMode:
    """创建认知模式实例"""
    return CognitiveMode()


if __name__ == "__main__":
    # 测试
    mode = CognitiveMode()
    
    print("认知模式切换测试")
    print("=" * 50)
    
    # 获取当前模式
    print("\n当前模式:")
    current = mode.get_mode()
    print(f"  模式: {current['mode']}")
    print(f"  名称: {current['name']}")
    print(f"  描述: {current['description']}")
    
    # 切换模式
    print("\n切换到 quick 模式:")
    result = mode.set_mode("quick")
    print(f"  结果: {result['success']}")
    print(f"  新模式: {result['name']}")
    
    # 获取配置
    print("\nquick 模式配置:")
    config = mode.get_mode_config("quick")
    print(f"  规则: {config['rules']}")
    print(f"  最大API调用: {config['max_api_calls']}")
    
    # 建议模式
    print("\n任务建议:")
    print(f"  query → {mode.suggest_mode('query')}")
    print(f"  refine → {mode.suggest_mode('refine')}")
    print(f"  analyze → {mode.suggest_mode('analyze')}")
    
    # 获取历史
    print("\n切换历史:")
    history = mode.get_history()
    for h in history:
        print(f"  {h['from']} → {h['to']} ({h['timestamp']})")
    
    print("\n✅ 测试完成")
