# -*- coding: utf-8 -*-
"""
灵台MCP - 灵台集成模块
=======================
基于 index.json 的灵识集成模块，替代原有的独立数据库方案。

功能：
- 从 index.json 查询知识
- 分析链接关系
- 发现潜在关联
- 记录Token消耗
"""

import os
import sys
import json
from pathlib import Path
from datetime import datetime

# 添加当前目录到路径
sys.path.insert(0, str(Path(__file__).resolve().parent))

from memory_engine import MemoryEngine
from auto_edge import AutoEdge
from token_monitor import TokenMonitor


class LingtaiIntegration:
    """灵台灵识集成模块 V2"""
    
    def __init__(self, vault_path: str = None):
        """
        初始化集成模块
        
        Args:
            vault_path: 灵台vault路径
        """
        if vault_path is None:
            self.vault_path = r"."
        else:
            self.vault_path = vault_path
        
        # 数据目录
        self.data_dir = os.path.join(self.vault_path, ".meta")
        os.makedirs(self.data_dir, exist_ok=True)
        
        # 初始化灵识模块
        self.memory = MemoryEngine(self.vault_path)
        self.auto_edge = AutoEdge(self.vault_path)
        self.token_monitor = TokenMonitor(self.data_dir)
    
    def refresh(self):
        """刷新数据（重新加载 index.json）"""
        self.memory.refresh()
        self.auto_edge.refresh()
    
    def query_from_dantang(self, keyword: str) -> dict:
        """
        从丹房查询知识
        
        Args:
            keyword: 搜索关键词
        
        Returns:
            dict: 查询结果
        """
        print(f"🔍 灵识查询: {keyword}")
        
        # 1. 直接查询
        direct_results = self.memory.query(keyword)
        print(f"   → 直接匹配: {len(direct_results)} 条")
        
        # 2. 图扩散搜索
        graph_results = self.memory.search_graph(keyword, hops=2)
        print(f"   → 关联知识: {len(graph_results)} 条")
        
        # 3. 记录Token消耗
        self.token_monitor.record_usage(
            action="query",
            model="hunyuan-turbos",
            input_tokens=len(keyword) * 2,
            output_tokens=100,
            saved_tokens=50
        )
        
        return {
            "direct_matches": direct_results,
            "related_knowledge": graph_results,
            "total_found": len(direct_results) + len(graph_results)
        }
    
    def analyze_page_links(self, page_path: str) -> dict:
        """
        分析页面链接关系
        
        Args:
            page_path: 页面路径
        
        Returns:
            dict: 链接分析结果
        """
        # 获取相关页面
        related = self.memory.get_related_pages(page_path)
        
        # 获取潜在关联
        potential = self.auto_edge.find潜在关联(page_path)
        
        # 获取链接建议
        suggestions = self.auto_edge.get_link_suggestions(page_path)
        
        return {
            "page": page_path,
            "related_count": len(related),
            "related_pages": [{"path": p["path"], "title": p["title"]} for p in related],
            "potential_count": len(potential),
            "potential_pages": [{"path": p["path"], "title": p["title"]} for p in potential],
            "suggestions": suggestions,
        }
    
    def detect_page_relation(self, path_a: str, path_b: str) -> dict:
        """
        检测两个页面的关系
        
        Args:
            path_a: 页面A路径
            path_b: 页面B路径
        
        Returns:
            dict: 关系检测结果
        """
        relation = self.auto_edge.detect_relation(path_a, path_b)
        
        return {
            "page_a": path_a,
            "page_b": path_b,
            "relation": relation,
        }
    
    def get_link_analysis(self) -> dict:
        """获取全库链接分析"""
        return self.auto_edge.analyze_links()
    
    def get_lingtai_mcp_stats(self) -> dict:
        """获取灵识统计信息"""
        memory_stats = self.memory.get_stats()
        token_stats = self.token_monitor.get_savings()
        
        return {
            "memory": memory_stats,
            "tokens": token_stats,
        }
    
    def search_content(self, keyword: str) -> dict:
        """
        搜索页面内容（需要读取.md文件）
        
        Args:
            keyword: 搜索关键词
        
        Returns:
            dict: 搜索结果
        """
        print(f"📄 内容搜索: {keyword}")
        
        # 搜索摘要
        summary_results = self.memory.search_by_summary(keyword)
        print(f"   → 摘要匹配: {len(summary_results)} 条")
        
        # 搜索内容
        content_results = self.memory.search_by_content(keyword)
        print(f"   → 内容匹配: {len(content_results)} 条")
        
        # 合并去重
        all_paths = set()
        all_results = []
        
        for p in summary_results + content_results:
            if p["path"] not in all_paths:
                all_paths.add(p["path"])
                all_results.append(p)
        
        return {
            "keyword": keyword,
            "summary_matches": len(summary_results),
            "content_matches": len(content_results),
            "total_matches": len(all_results),
            "results": all_results[:20],  # 限制返回数量
        }


def create_lingtai_integration(vault_path: str = None) -> LingtaiIntegration:
    """创建集成实例"""
    return LingtaiIntegration(vault_path)


# 命令行接口
if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="灵台灵识集成工具 V2")
    subparsers = parser.add_subparsers(dest="command", help="可用命令")
    
    # query 命令
    query_parser = subparsers.add_parser("query", help="查询知识")
    query_parser.add_argument("keyword", help="搜索关键词")
    
    # analyze 命令
    analyze_parser = subparsers.add_parser("analyze", help="分析页面链接")
    analyze_parser.add_argument("path", help="页面路径")
    
    # relation 命令
    relation_parser = subparsers.add_parser("relation", help="检测页面关系")
    relation_parser.add_argument("path_a", help="页面A路径")
    relation_parser.add_argument("path_b", help="页面B路径")
    
    # links 命令
    links_parser = subparsers.add_parser("links", help="全库链接分析")
    
    # stats 命令
    stats_parser = subparsers.add_parser("stats", help="查看统计")
    
    # search 命令
    search_parser = subparsers.add_parser("search", help="内容搜索")
    search_parser.add_argument("keyword", help="搜索关键词")
    
    args = parser.parse_args()
    
    integration = LingtaiIntegration()
    
    if args.command == "query":
        result = integration.query_from_dantang(args.keyword)
        print(f"\n找到 {result['total_found']} 条相关知识")
        for item in result['direct_matches'][:5]:
            print(f"  - {item['title']}: {item['summary'][:60]}...")
    
    elif args.command == "analyze":
        result = integration.analyze_page_links(args.path)
        print(f"\n页面链接分析: {args.path}")
        print(f"  相关页面: {result['related_count']}")
        print(f"  潜在关联: {result['potential_count']}")
        print(f"  链接建议: {len(result['suggestions'])}")
    
    elif args.command == "relation":
        result = integration.detect_page_relation(args.path_a, args.path_b)
        print(f"\n页面关系: {result['relation']}")
    
    elif args.command == "links":
        result = integration.get_link_analysis()
        print(f"\n全库链接分析:")
        print(f"  总页面: {result['total_pages']}")
        print(f"  总链接: {result['total_links']}")
        print(f"  孤立页面: {result['isolated_count']}")
        print(f"  枢纽页面: {len(result['hub_pages'])}")
    
    elif args.command == "stats":
        result = integration.get_lingtai_mcp_stats()
        print(f"\n灵识统计:")
        print(json.dumps(result, ensure_ascii=False, indent=2))
    
    elif args.command == "search":
        result = integration.search_content(args.keyword)
        print(f"\n搜索结果: {result['total_matches']} 条")
        for item in result['results'][:5]:
            print(f"  - {item['title']}: {item['summary'][:60]}...")
    
    else:
        parser.print_help()
