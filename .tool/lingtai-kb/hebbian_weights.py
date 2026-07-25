# -*- coding: utf-8 -*-
"""
灵台MCP - Hebbian 动态权重模块
================================
基于 Prism MCP 设计，为知识图谱的边添加动态权重。

核心思想：
- 经常一起被查询的页面，边权重增加
- 长时间未被访问的边，权重衰减
- 权重影响图扩散搜索的排序

功能：
- 记录边的使用频率
- 计算边的权重
- 权重衰减
- 影响图扩散排序
"""

import os
import json
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, List, Optional


class HebbianWeights:
    """Hebbian 动态权重引擎"""
    
    def __init__(self, vault_path: str = None):
        """
        初始化
        
        Args:
            vault_path: 灵台vault路径
        """
        if vault_path is None:
            self.vault_path = r"."
        else:
            self.vault_path = vault_path
        
        # 权重存储路径
        self.store_dir = Path(__file__).parent / "weights"
        self.store_dir.mkdir(exist_ok=True)
        self.weights_path = self.store_dir / "edge_weights.json"
        
        # 配置
        self.decay_days = 30  # 30天衰减
        self.min_weight = 0.1  # 最小权重
        self.max_weight = 2.0  # 最大权重
        self.boost_factor = 0.1  # 每次使用增加的权重
        
        # 加载权重
        self.weights = self._load_weights()
    
    def _load_weights(self) -> dict:
        """加载边权重"""
        if self.weights_path.exists():
            try:
                with open(self.weights_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {}
    
    def _save_weights(self):
        """保存边权重"""
        with open(self.weights_path, "w", encoding="utf-8") as f:
            json.dump(self.weights, f, ensure_ascii=False, indent=2)
    
    def _get_edge_key(self, source: str, target: str) -> str:
        """生成边的唯一键"""
        # 确保顺序一致
        if source > target:
            source, target = target, source
        return f"{source}||{target}"
    
    def on_query(self, source: str, target: str):
        """
        查询时调用，增加边权重
        
        Args:
            source: 起始页面
            target: 目标页面
        """
        edge_key = self._get_edge_key(source, target)
        
        if edge_key in self.weights:
            # 增加权重
            self.weights[edge_key]["weight"] = min(
                self.max_weight,
                self.weights[edge_key]["weight"] + self.boost_factor
            )
            self.weights[edge_key]["last_used"] = datetime.now().isoformat()
            self.weights[edge_key]["use_count"] = self.weights[edge_key].get("use_count", 0) + 1
        else:
            # 创建新边
            self.weights[edge_key] = {
                "source": source,
                "target": target,
                "weight": 0.5,
                "created_at": datetime.now().isoformat(),
                "last_used": datetime.now().isoformat(),
                "use_count": 1,
            }
        
        self._save_weights()
    
    def on_query_batch(self, pairs: list):
        """
        批量记录边权重（优化：一次 save，替代 N 次独立 save）
        
        Args:
            pairs: [(source, target), ...] 边对列表
        """
        now = datetime.now().isoformat()
        for source, target in pairs:
            edge_key = self._get_edge_key(source, target)
            if edge_key in self.weights:
                self.weights[edge_key]["weight"] = min(
                    self.max_weight,
                    self.weights[edge_key]["weight"] + self.boost_factor
                )
                self.weights[edge_key]["last_used"] = now
                self.weights[edge_key]["use_count"] = self.weights[edge_key].get("use_count", 0) + 1
            else:
                self.weights[edge_key] = {
                    "source": source,
                    "target": target,
                    "weight": 0.5,
                    "created_at": now,
                    "last_used": now,
                    "use_count": 1,
                }
        # 批量结束，一次写盘
        self._save_weights()
    
    def get_weight(self, source: str, target: str) -> float:
        """
        获取边的权重
        
        Args:
            source: 起始页面
            target: 目标页面
        
        Returns:
            float: 边的权重
        """
        edge_key = self._get_edge_key(source, target)
        edge = self.weights.get(edge_key, {})
        return edge.get("weight", self.min_weight)
    
    def decay(self):
        """衰减所有边的权重"""
        now = datetime.now()
        
        for edge_key, edge_data in self.weights.items():
            last_used = datetime.fromisoformat(edge_data.get("last_used", now.isoformat()))
            days_since_use = (now - last_used).days
            
            # 衰减因子：30天内完全衰减
            decay_factor = max(0, 1 - days_since_use / self.decay_days)
            
            # 应用衰减
            edge_data["weight"] = max(
                self.min_weight,
                edge_data["weight"] * decay_factor
            )
        
        self._save_weights()
    
    def get_top_co_occurrences(self, top_n: int = 20) -> List[tuple]:
        """
        获取共现权重最高的边列表
        
        Args:
            top_n: 返回条数
        
        Returns:
            List[(source, target, weight)]
        """
        if not self.weights:
            return []
        
        # 按权重降序排列
        sorted_edges = sorted(
            self.weights.items(),
            key=lambda x: x[1].get("weight", 0),
            reverse=True
        )
        
        result = []
        for edge_key, edge_data in sorted_edges[:top_n]:
            source = edge_data["source"]
            target = edge_data["target"]
            weight = edge_data.get("weight", 0)
            result.append((source, target, weight))
        
        return result
    
    def get_stats(self) -> dict:
        """获取权重统计"""
        if not self.weights:
            return {"total_edges": 0, "avg_weight": 0}
        
        weights = [e.get("weight", 0) for e in self.weights.values()]
        use_counts = [e.get("use_count", 0) for e in self.weights.values()]
        
        return {
            "total_edges": len(self.weights),
            "avg_weight": sum(weights) / len(weights) if weights else 0,
            "max_weight": max(weights) if weights else 0,
            "min_weight": min(weights) if weights else 0,
            "total_uses": sum(use_counts),
        }


# 便捷函数
def create_hebbian_weights(vault_path: str = None) -> HebbianWeights:
    """创建 Hebbian 权重实例"""
    return HebbianWeights(vault_path)


if __name__ == "__main__":
    # 测试
    engine = HebbianWeights()
    
    print("Hebbian 动态权重测试")
    print("=" * 50)
    
    # 模拟查询
    test_queries = [
        ("丹房/00-思考与认知/含人量", "丹房/00-思考与认知/追问·O与π"),
        ("丹房/00-思考与认知/含人量", "丹房/00-思考与认知/追问·O与π"),
        ("丹房/00-思考与认知/含人量", "丹房/00-思考与认知/独立思考"),
    ]
    
    for source, target in test_queries:
        print(f"\n查询: {source.split('/')[-1]} → {target.split('/')[-1]}")
        engine.on_query(source, target)
        weight = engine.get_weight(source, target)
        print(f"  权重: {weight:.2f}")
    
    # 统计
    stats = engine.get_stats()
    print(f"\n统计:")
    print(f"  总边数: {stats['total_edges']}")
    print(f"  平均权重: {stats['avg_weight']:.2f}")
    print(f"  最大权重: {stats['max_weight']:.2f}")
    print(f"  总使用次数: {stats['total_uses']}")
    
    # 测试衰减
    print("\n衰减测试:")
    engine.decay()
    stats_after = engine.get_stats()
    print(f"  衰减后平均权重: {stats_after['avg_weight']:.2f}")
    
    print("\n✅ 测试完成")
