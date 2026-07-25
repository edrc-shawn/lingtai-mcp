# -*- coding: utf-8 -*-
"""
灵台MCP - Obsidian Markdown 工具
================================
生成 Obsidian Flavored Markdown 格式内容，供丹房页使用。
这些格式对人类阅读有价值，对LLM无价值。
"""

import re
from typing import List, Optional


class MarkdownUtils:
    """Obsidian Markdown 工具"""
    
    # Callout 类型定义
    CALLOUT_TYPES = {
        "note": "📝 注意",
        "tip": "💡 提示",
        "warning": "⚠️ 警告",
        "info": "ℹ️ 信息",
        "example": "📋 示例",
        "quote": "💬 引用",
        "bug": "🐛 缺陷",
        "danger": "🚨 危险",
        "success": "✅ 成功",
        "failure": "❌ 失败",
        "question": "❓ 问题",
        "abstract": "摘要",
        "todo": "📝 待办",
    }
    
    @staticmethod
    def callout(content: str, callout_type: str = "note", title: str = None, collapsed: bool = False) -> str:
        """
        生成 Callout 格式
        
        Args:
            content: 内容
            callout_type: 类型（note/tip/warning/info等）
            title: 自定义标题（可选）
            collapsed: 是否默认折叠
        
        Returns:
            str: Callout 格式文本
        """
        collapse_char = "-" if collapsed else ""
        title_part = f" {title}" if title else ""
        
        # 处理多行内容
        lines = content.strip().split("\n")
        formatted_lines = [f"> [!{callout_type}]{title_part}"]
        for line in lines:
            formatted_lines.append(f"> {line}")
        
        return "\n".join(formatted_lines)
    
    @staticmethod
    def embed(note_path: str, section: str = None, width: int = None) -> str:
        """
        生成嵌入语法
        
        Args:
            note_path: 笔记路径
            section: 章节（可选）
            width: 宽度（可选）
        
        Returns:
            str: 嵌入语法
        """
        if section:
            embed_str = f"![[{note_path}#{section}]"
        else:
            embed_str = f"![[{note_path}]"
        
        if width:
            embed_str += f"|{width}"
        
        embed_str += "]"
        return embed_str
    
    @staticmethod
    def footnote(text: str, footnote_id: str = None, footnote_content: str = None) -> str:
        """
        生成脚注语法
        
        Args:
            text: 文本
            footnote_id: 脚注ID（可选）
            footnote_content: 脚注内容（可选）
        
        Returns:
            str: 脚注语法
        """
        if footnote_content:
            # 行内脚注
            return f"{text}^[{footnote_content}]"
        elif footnote_id:
            # 引用脚注
            return f"{text}[^{footnote_id}]"
        else:
            # 自动生成ID
            return f"{text}^[脚注内容]"
    
    @staticmethod
    def mermaid_diagram(diagram_type: str, content: str) -> str:
        """
        生成 Mermaid 图表
        
        Args:
            diagram_type: 图表类型（graph/flowchart/sequence等）
            content: 图表内容
        
        Returns:
            str: Mermaid 语法
        """
        return f"```mermaid\n{diagram_type}\n{content}\n```"
    
    @staticmethod
    def highlight(text: str) -> str:
        """
        生成高亮语法
        
        Args:
            text: 要高亮的文本
        
        Returns:
            str: 高亮语法
        """
        return f"=={text}=="
    
    @staticmethod
    def callout_with_embed(content: str, embed_path: str, callout_type: str = "note") -> str:
        """
        生成带嵌入的 Callout
        
        Args:
            content: 内容
            embed_path: 嵌入路径
            callout_type: Callout 类型
        
        Returns:
            str: 带嵌入的 Callout
        """
        embed_str = MarkdownUtils.embed(embed_path)
        return f"> [!{callout_type}]\n> {content}\n>\n> {embed_str}"
    
    @staticmethod
    def callout_with_footnote(content: str, footnote_text: str, callout_type: str = "note") -> str:
        """
        生成带脚注的 Callout
        
        Args:
            content: 内容
            footnote_text: 脚注文本
            callout_type: Callout 类型
        
        Returns:
            str: 带脚注的 Callout
        """
        return f"> [!{callout_type}]\n> {content}\n>\n> ^[{footnote_text}]"
    
    @staticmethod
    def knowledge_card(title: str, summary: str, tags: List[str] = None, related: List[str] = None) -> str:
        """
        生成知识卡片格式
        
        Args:
            title: 标题
            summary: 摘要
            tags: 标签列表
            related: 相关页面列表
        
        Returns:
            str: 知识卡片格式
        """
        lines = []
        lines.append(f"## {title}")
        lines.append("")
        lines.append(f"> [!tip] 核心摘要")
        lines.append(f"> {summary}")
        
        if tags:
            lines.append("")
            lines.append(f"标签: {' '.join(['#' + t for t in tags])}")
        
        if related:
            lines.append("")
            lines.append("相关页面:")
            for r in related:
                lines.append(f"- [[{r}]]")
        
        return "\n".join(lines)
    
    @staticmethod
    def comparison_table(items: List[dict], columns: List[str]) -> str:
        """
        生成对比表格
        
        Args:
            items: 数据列表
            columns: 列名列表
        
        Returns:
            str: Markdown 表格
        """
        if not items:
            return ""
        
        # 表头
        header = "| " + " | ".join(columns) + " |"
        separator = "| " + " | ".join(["---"] * len(columns)) + " |"
        
        # 数据行
        rows = []
        for item in items:
            row = "| " + " | ".join([str(item.get(col, "")) for col in columns]) + " |"
            rows.append(row)
        
        return "\n".join([header, separator] + rows)


# 便捷函数
def create_markdown_utils() -> MarkdownUtils:
    """创建 Markdown 工具实例"""
    return MarkdownUtils()


if __name__ == "__main__":
    # 测试
    utils = MarkdownUtils()
    
    print("Markdown 工具测试")
    print("=" * 50)
    
    # 测试 Callout
    print("\n1. Callout:")
    print(utils.callout("这是重要的信息", "important"))
    print()
    print(utils.callout("这是警告", "warning", title="注意"))
    
    # 测试嵌入
    print("\n2. 嵌入:")
    print(utils.embed("丹房/00-思考与认知/含人量"))
    print(utils.embed("丹房/00-思考与认知/含人量", section="核心概念"))
    
    # 测试脚注
    print("\n3. 脚注:")
    print(utils.footnote("这是文本", footnote_content="这是脚注内容"))
    
    # 测试高亮
    print("\n4. 高亮:")
    print(utils.highlight("重要概念"))
    
    # 测试知识卡片
    print("\n5. 知识卡片:")
    print(utils.knowledge_card(
        "含人量",
        "你在内容、判断、行动中保留的独立思考比例",
        ["概念", "框架"],
        ["追问·O与π", "独立思考"]
    ))
    
    # 测试对比表格
    print("\n6. 对比表格:")
    print(utils.comparison_table(
        [
            {"维度": "手艺", "本质": "可编码", "AI影响": "替代"},
            {"维度": "判断", "本质": "需活过", "AI影响": "放大"},
        ],
        ["维度", "本质", "AI影响"]
    ))
    
    print("\n✅ 测试完成")
