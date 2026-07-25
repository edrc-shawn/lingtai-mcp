# -*- coding: utf-8 -*-
"""
灵台MCP - 自动建边模块 V2
===========================
基于 index.json 的自动建边逻辑，利用已有的 linked_from 和 links_to 数据。

功能：
- 分析现有链接关系
- 发现潜在关联
- 生成关联建议
"""

import json
import os
import re
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional, Tuple, Any


class AutoEdge:
    """灵台灵识自动建边模块 V2 - 基于 index.json"""
    
    def __init__(self, vault_path: str = None):
        """
        初始化自动建边模块
        
        Args:
            vault_path: 灵台vault路径
        """
        if vault_path is None:
            self.vault_path = r"."
        else:
            self.vault_path = vault_path
        
        # index.json 路径
        self.index_path = os.path.join(self.vault_path, "丹房", ".meta", "index.json")
        
        # 加载数据
        self.data = self._load_index()
        self.pages = self.data.get("pages", [])
    
    def _load_index(self) -> dict:
        """加载 index.json"""
        if os.path.exists(self.index_path):
            try:
                with open(self.index_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except (json.JSONDecodeError, OSError) as e:
                print(f"⚠️ 加载 index.json 失败: {e}")
        
        return {"pages": [], "_stats": {}}
    
    def refresh(self):
        """刷新数据"""
        self.data = self._load_index()
        self.pages = self.data.get("pages", [])
    
    def analyze_links(self) -> dict:
        """
        分析链接关系
        
        Returns:
            dict: 链接分析结果
        """
        total_pages = len(self.pages)
        total_links = sum(len(p.get("links_to", [])) for p in self.pages)
        total_backlinks = sum(len(p.get("linked_from", [])) for p in self.pages)
        
        # 找到孤立页面（没有出链和入链）
        isolated = []
        for p in self.pages:
            if not p.get("links_to") and not p.get("linked_from"):
                isolated.append(p["path"])
        
        # 找到死胡同页面（有出链但没有入链）
        deadend = []
        for p in self.pages:
            if p.get("links_to") and not p.get("linked_from"):
                deadend.append(p["path"])
        
        # 找到枢纽页面（入链最多）
        hub_pages = sorted(self.pages, key=lambda p: len(p.get("linked_from", [])), reverse=True)[:10]
        
        return {
            "total_pages": total_pages,
            "total_links": total_links,
            "total_backlinks": total_backlinks,
            "link_density": total_links / total_pages if total_pages > 0 else 0,
            "isolated_count": len(isolated),
            "isolated_pages": isolated,
            "deadend_count": len(deadend),
            "deadend_pages": deadend[:10],
            "hub_pages": [{"path": p["path"], "title": p["title"], "backlinks": len(p.get("linked_from", []))} for p in hub_pages],
        }
    
    def find潜在关联(self, page_path: str, max_results: int = 10) -> list:
        """
        发现潜在关联页面
        
        Args:
            page_path: 页面路径
            max_results: 最大结果数
        
        Returns:
            list: 潜在关联页面列表
        """
        # 找到目标页面
        target_page = None
        for p in self.pages:
            if p["path"] == page_path:
                target_page = p
                break
        
        if not target_page:
            return []
        
        # 计算与其他页面的关联分数
        scores = []
        target_tags = set(target_page.get("tags", []))
        target_domain = target_page.get("domain", "")
        target_links = set(target_page.get("links_to", []))
        target_backlinks = set(target_page.get("linked_from", []))
        
        for p in self.pages:
            if p["path"] == page_path:
                continue
            
            # 已有链接的页面不作为潜在关联
            if p["path"] in target_links or p["path"] in target_backlinks:
                continue
            
            score = 0
            
            # 标签重叠
            page_tags = set(p.get("tags", []))
            tag_overlap = len(target_tags & page_tags)
            score += tag_overlap * 2
            
            # 同域名
            if p.get("domain") == target_domain:
                score += 1
            
            # 内容相似度（基于摘要关键词）
            target_keywords = set(target_page.get("summary", "").split())
            page_keywords = set(p.get("summary", "").split())
            keyword_overlap = len(target_keywords & page_keywords)
            score += keyword_overlap * 0.5
            
            if score > 0:
                scores.append({"page": p, "score": score})
        
        # 按分数排序
        scores.sort(key=lambda x: x["score"], reverse=True)
        
        return [item["page"] for item in scores[:max_results]]
    
    def detect_relation(self, path_a: str, path_b: str) -> str:
        """
        检测两个页面的关系
        
        Args:
            path_a: 页面A路径
            path_b: 页面B路径
        
        Returns:
            str: 关系类型
        """
        # 找到两个页面
        page_a = None
        page_b = None
        
        for p in self.pages:
            if p["path"] == path_a:
                page_a = p
            elif p["path"] == path_b:
                page_b = p
        
        if not page_a or not page_b:
            return "未知"
        
        # 检查直接链接
        if path_b in page_a.get("links_to", []):
            return "A链接到B"
        if path_a in page_b.get("links_to", []):
            return "B链接到A"
        
        # 检查共同链接
        common_links = set(page_a.get("links_to", [])) & set(page_b.get("links_to", []))
        if common_links:
            return f"共同链接到 {len(common_links)} 个页面"
        
        # 检查共同被链接
        common_backlinks = set(page_a.get("linked_from", [])) & set(page_b.get("linked_from", []))
        if common_backlinks:
            return f"被 {len(common_backlinks)} 个页面共同链接"
        
        # 检查标签重叠
        tags_a = set(page_a.get("tags", []))
        tags_b = set(page_b.get("tags", []))
        common_tags = tags_a & tags_b
        if common_tags:
            return f"共享标签: {', '.join(common_tags)}"
        
        # 检查同域名
        if page_a.get("domain") == page_b.get("domain"):
            return f"同域: {page_a.get('domain')}"
        
        return "未知"
    
    def get_link_suggestions(self, page_path: str, max_suggestions: int = 5) -> list:
        """
        获取链接建议
        
        Args:
            page_path: 页面路径
            max_suggestions: 最大建议数
        
        Returns:
            list: 链接建议列表
        """
        # 找到目标页面
        target_page = None
        for p in self.pages:
            if p["path"] == page_path:
                target_page = p
                break
        
        if not target_page:
            return []
        
        suggestions = []
        
        # 建议1: 基于标签关联
        target_tags = set(target_page.get("tags", []))
        for p in self.pages:
            if p["path"] == page_path:
                continue
            if p["path"] in target_page.get("links_to", []):
                continue
            
            page_tags = set(p.get("tags", []))
            common_tags = target_tags & page_tags
            
            if common_tags:
                suggestions.append({
                    "page": p["path"],
                    "title": p["title"],
                    "reason": f"共享标签: {', '.join(common_tags)}",
                    "strength": len(common_tags)
                })
        
        # 建议2: 基于内容相似度（简单关键词匹配）
        target_keywords = set(target_page.get("summary", "").split())
        for p in self.pages:
            if p["path"] == page_path:
                continue
            if p["path"] in target_page.get("links_to", []):
                continue
            
            page_keywords = set(p.get("summary", "").split())
            common_keywords = target_keywords & page_keywords
            
            if len(common_keywords) >= 3:  # 至少3个共同关键词
                suggestions.append({
                    "page": p["path"],
                    "title": p["title"],
                    "reason": f"共享关键词: {', '.join(list(common_keywords)[:3])}",
                    "strength": len(common_keywords)
                })
        
        # 按强度排序并去重
        seen = set()
        unique_suggestions = []
        for s in suggestions:
            if s["page"] not in seen:
                seen.add(s["page"])
                unique_suggestions.append(s)
        
        unique_suggestions.sort(key=lambda x: x["strength"], reverse=True)
        
        return unique_suggestions[:max_suggestions]


def create_auto_edge(vault_path: str = None) -> AutoEdge:
    """创建自动建边实例"""
    return AutoEdge(vault_path)


if __name__ == "__main__":
    # 测试
    auto_edge = AutoEdge()
    
    # 链接分析
    analysis = auto_edge.analyze_links()
    print("链接分析:")
    print(f"  总页面: {analysis['total_pages']}")
    print(f"  总链接: {analysis['total_links']}")
    print(f"  孤立页面: {analysis['isolated_count']}")
    print(f"  枢纽页面: {len(analysis['hub_pages'])}")
    
    # 显示枢纽页面
    print("\n枢纽页面 (Top 5):")
    for hub in analysis['hub_pages'][:5]:
        print(f"  - {hub['title']}: {hub['backlinks']} 个入链")
