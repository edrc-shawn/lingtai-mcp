# -*- coding: utf-8 -*-
"""
灵台MCP - 图谱分析工具模块
============================
基于 Obsidian MCP 设计，扩展知识图谱分析能力。

功能：
- find_clusters: 发现知识聚类
- centrality: 计算页面中心性
- isolated_nodes: 发现孤立节点
"""

import os
import json
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional
from collections import Counter


class GraphAnalysis:
    """图谱分析引擎"""
    
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
        
        # 加载数据
        self.index_path = os.path.join(self.vault_path, "丹房", ".meta", "index.json")
        self.data = self._load_index()
        self.pages = self.data.get("pages", [])
        
        # 构建图
        self.adjacency = self._build_adjacency()
    
    def _load_index(self) -> dict:
        """加载 index.json"""
        if os.path.exists(self.index_path):
            try:
                with open(self.index_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {"pages": []}
    
    def _build_adjacency(self) -> Dict[str, set]:
        """构建邻接表"""
        adj = {}
        for page in self.pages:
            path = page["path"]
            if path not in adj:
                adj[path] = set()
            
            # 出链
            for link in page.get("links_to", []):
                adj[path].add(link)
                if link not in adj:
                    adj[link] = set()
                adj[link].add(path)
        
        return adj
    
    def find_clusters(self, min_cluster_size: int = 3) -> List[dict]:
        """
        发现知识聚类（简单连通分量）
        
        Args:
            min_cluster_size: 最小聚类大小
        
        Returns:
            list: 聚类列表
        """
        visited = set()
        clusters = []
        
        for page in self.pages:
            path = page["path"]
            if path in visited:
                continue
            
            # BFS找连通分量
            cluster = []
            queue = [path]
            
            while queue:
                current = queue.pop(0)
                if current in visited:
                    continue
                
                visited.add(current)
                cluster.append(current)
                
                # 添加邻居
                for neighbor in self.adjacency.get(current, set()):
                    if neighbor not in visited:
                        queue.append(neighbor)
            
            # 过滤小聚类
            if len(cluster) >= min_cluster_size:
                clusters.append({
                    "size": len(cluster),
                    "pages": cluster[:10],  # 只返回前10个
                })
        
        # 按大小排序
        clusters.sort(key=lambda x: x["size"], reverse=True)
        
        return clusters
    
    def centrality(self) -> List[dict]:
        """
        计算页面中心性（度中心性）
        
        Returns:
            list: 按中心性排序的页面列表
        """
        centrality_scores = []
        
        for page in self.pages:
            path = page["path"]
            
            # 度中心性 = 连接数 / 总节点数
            degree = len(self.adjacency.get(path, set()))
            total_nodes = len(self.pages)
            centrality = degree / total_nodes if total_nodes > 0 else 0
            
            centrality_scores.append({
                "path": path,
                "title": page.get("title", ""),
                "degree": degree,
                "centrality": round(centrality, 4),
            })
        
        # 按中心性排序
        centrality_scores.sort(key=lambda x: x["centrality"], reverse=True)
        
        return centrality_scores[:20]  # 返回前20个
    
    def isolated_nodes(self) -> List[dict]:
        """
        发现孤立节点（无连接的页面）
        
        Returns:
            list: 孤立节点列表
        """
        isolated = []
        
        for page in self.pages:
            path = page["path"]
            connections = self.adjacency.get(path, set())
            
            if len(connections) == 0:
                isolated.append({
                    "path": path,
                    "title": page.get("title", ""),
                    "domain": page.get("domain", ""),
                })
        
        return isolated


# 便捷函数
def create_graph_analysis(vault_path: str = None) -> GraphAnalysis:
    """创建图谱分析实例"""
    return GraphAnalysis(vault_path)


if __name__ == "__main__":
    # 测试
    analysis = GraphAnalysis()
    
    print("图谱分析工具测试")
    print("=" * 50)
    
    # 发现聚类
    print("\n1. 发现聚类:")
    clusters = analysis.find_clusters(min_cluster_size=3)
    print(f"   聚类数: {len(clusters)}")
    for i, c in enumerate(clusters[:3]):
        print(f"   聚类{i+1}: {c['size']} 个页面")
    
    # 计算中心性
    print("\n2. 页面中心性 (Top 5):")
    centrality = analysis.centrality()
    for c in centrality[:5]:
        print(f"   {c['title']}: {c['centrality']:.4f} (度={c['degree']})")
    
    # 发现孤立节点
    print("\n3. 孤立节点:")
    isolated = analysis.isolated_nodes()
    print(f"   孤立节点数: {len(isolated)}")
    for node in isolated[:3]:
        print(f"   - {node['title']}")
    
    print("\n✅ 测试完成")
