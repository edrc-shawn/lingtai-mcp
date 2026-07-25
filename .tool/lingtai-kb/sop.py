# -*- coding: utf-8 -*-
"""
灵识工具 SOP 按需披露
=====================
34个工具的详细使用指南，按需加载而非一次性全量暴露。
减少不必要的token消耗。
"""

# 工具SOP字典：key=工具名, value=详细使用指南
TOOL_SOPS = {
    "query": """
## query - 关键词查询

**用途**：查询灵识知识库，支持n-gram回退和图扩散。

**参数**：
- keyword: 搜索关键词（必须）
- use_ngram_fallback: 是否启用n-gram回退（默认true）
- hops: 图扩散跳数（默认3）

**返回**：{"results": [...], "match_type": "exact/ngram/none", "keyword": "..."}

**匹配类型**：
- exact: 精确匹配标题
- ngram: 字符3-gram模糊匹配
- none: 无匹配（降级到图扩散）

**使用时机**：用户提问时，先调inject，再调query补充
""",

    "inject": """
## inject - 知识注入

**用途**：根据关键词检索知识并注入当前上下文。

**参数**：
- keyword: 关键词（必须）

**返回**：{"found": true/false, "results": [...], "total": N}

**规则**：规则①触发时调用。用户提问→inject→返回知识→融入回复
""",

    "save": """
## save - 保存知识

**用途**：保存新知识到原料目录。

**参数**：
- content: 内容（必须）
- category: 分类（可选）
- source: 来源（默认"对话"）

**返回**：{"success": true, "path": "...", "observation_feedback": "..."}

**规则**：规则②触发时调用。用户提供事实→save→提示用户
""",

    "mem_write": """
## mem_write - 写入记忆银行

**用途**：写入记忆到记忆银行（6级信源分级+冲突检测）。

**参数**：
- content: 记忆内容（必须）
- source: user_correction(0.9)/user_repeated(0.8)/ai_reasoning(0.5)/user_stated(0.4)/hebbian(0.2)/external(0.1)
- tags: 标签列表（可选）

**使用时机**：用户透露偏好/习惯/特征时调用。与save的区别：save写知识，mem_write写画像。
""",

    "mem_query": """
## mem_query - 查询记忆银行

**用途**：查询记忆银行中的记忆。

**参数**：
- keyword: 关键词过滤（可选）
- min_confidence: 最低置信度（默认0.0）

**返回**：{"results": [...], "stats": {...}}

**使用时机**：需要了解用户历史偏好或事实时调用。
""",

    "deep_analysis": """
## deep_analysis - 横纵分析法深度研究

**用途**：对产品/公司/概念/人物进行深度研究。

**参数**：
- topic: 研究主题（必须）

**返回**：分析框架（纵向/横向/交汇）+ 灵识内部数据 + Tavily外部搜索建议

**报告结构**：一句话定义→纵向分析(6000-15000字)→横向分析(3000-10000字)→横纵交汇洞察(1500-3000字)

**使用时机**：用户说"研究一下XX"、"帮我分析XX"时调用。
""",

    "recommend_resources": """
## recommend_resources - 知识缺口推荐

**用途**：检测未提炼原料 + Tavily搜索外部资源补全方向。

**参数**：
- topic: 指定主题（可选，默认扫描全部待提炼原料）

**返回**：{"pending_count": N, "recommendations": [...]}
""",
}


def get_sop(tool_name: str) -> dict:
    """获取工具SOP"""
    sop = TOOL_SOPS.get(tool_name)
    if sop:
        return {"tool": tool_name, "sop": sop.strip()}
    return {"tool": tool_name, "error": f"未找到 {tool_name} 的SOP，请用工具本身的description了解功能"}


def list_tools_with_sop() -> list:
    """列出有SOP的工具"""
    return list(TOOL_SOPS.keys())
