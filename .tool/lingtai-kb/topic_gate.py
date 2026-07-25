# -*- coding: utf-8 -*-
"""
Topic Gate — knowledge_inject 前置快速门控

拦截泛指词触发的低价值注入，省下完整管线（4层回退 + 记忆桥接 + 全文搜索）。
门控只依赖 index.json 的 pages 列表（已在 memory_engine 中加载），零外部依赖。

用法：
    from topic_gate import should_skip_inject
    gate = should_skip_inject(keyword, memory_engine.pages)
    if gate["skip"]:
        return {"found": False, "gate_skipped": True, ...}
"""

import sys
import re
_VERSION = "2026-07-17-v3"  # 热重载验证标记

# ═══════════════════════════════════════════════════════════════════
# 泛指词表（约 80 个高频宽泛概念）
# 来源：user_profile.recent_queries 高频低价值词 + 常见对话噪声
# 维护：不需要频繁更新，新增词时确保不覆盖专指场景
# ═══════════════════════════════════════════════════════════════════
_GENERIC_WORDS = frozenset([
    # 抽象概念
    '项目', '配置', '数据', '信息', '内容', '资料', '资源',
    # 方法论
    '方法', '方案', '方式', '形式', '模式', '流程', '策略',
    '思路', '方向', '框架', '模型', '原理', '理论', '概念',
    '本质', '定义', '特性', '属性',
    # 操作类
    '管理', '设计', '分析', '优化', '解决', '处理', '实现',
    '使用', '应用', '操作', '执行', '维护',
    # 评价类
    '对比', '区别', '差异', '相同', '不同', '优缺点', '优劣',
    '总结', '汇总', '概况', '简介',
    # 学习类
    '学习', '笔记', '教程', '指南', '说明', '手册',
    '入门', '进阶', '实践', '案例',
    # 系统类
    '系统', '平台', '工具', '技术', '产品', '服务',
    '功能', '需求', '目标', '结果', '问题', '建议',
    # 时间类
    '趋势', '现状', '背景', '历史', '经过', '过程',
    '步骤', '环节', '阶段', '时期',
    # 高频噪声
    '效率', 'ai', 'llm', '新', '好', '最佳', '常用',
    '如何', '怎么', '什么', '为什么',
    '设置', '参数', '配置项',
])

# ═══════════════════════════════════════════════════════════════════
# 专有短语表（只覆盖正则无法处理的纯中文独特短语）
# 可选优化，不维护也不影响功能正确性
# ═══════════════════════════════════════════════════════════════════
_SPECIFIC_PHRASES = frozenset([
    '三进制哈希', '四维匹配', '知识复利',
    '含人量', '聪明笨',
    'O与π', '粒波',
])

# ═══════════════════════════════════════════════════════════════════
# 专指词检测正则
# ═══════════════════════════════════════════════════════════════════
_SPECIFIC_RE = re.compile(
    r'(?:'
    r'^[A-Z][a-z]+(?:\s[A-Z][a-z]+)*$'          # 英文专有名词
    r'|^[A-Z]{2,6}$'                              # 全大写缩写
    r'|[a-z]+[A-Z]'                               # 小写开头驼峰（小写开头）
    r'|^[A-Z][a-z]+[A-Z]'                         # 大写开头驼峰（大写开头）
    r'|\d+\.\d+'                                  # 版本号
    r'|[A-Za-z].*[\u4e00-\u9fff]'                # 中英文混写
    r'|[\u4e00-\u9fff].*[A-Za-z]'                # 中英文混写（反向）
    r'|[/\\]'                                     # 路径分隔符
    r')'
)


def _is_specific_term(keyword: str) -> bool:
    """判断是否为专指词——技术术语/专有名词/路径/版本号等"""
    kw = keyword.strip()
    if len(kw) < 2:
        return False
    # 先排除纯泛指词（整词匹配，非子串匹配）
    # "AI编程"含"AI"子串但整体是专指，不应排除
    if kw.lower().strip() in _GENERIC_WORDS:
        return False
    # 正则匹配
    if _SPECIFIC_RE.search(kw):
        return True
    # 专有短语表
    if kw in _SPECIFIC_PHRASES:
        return True
    return False
def _is_generic_word(keyword: str) -> bool:
    """判断是否为泛指词——宽泛概念，非专指。

    只检查整词匹配，不做子串/拆词匹配。
    原因：子串匹配会误伤自然语言句子（如"如何提高工作效率"含"如何"子串），
    宁可放过少数泛指词（走完整管线但浪费 Token），不误伤正常查询。
    """
    kw = keyword.strip().lower()
    return kw in _GENERIC_WORDS


def _generate_ngrams(text: str, n: int = 3) -> list:
    """生成字符级 n-gram（与 memory_engine._generate_ngrams 逻辑一致）"""
    ngrams = []
    text = text.lower()
    for i in range(len(text) - n + 1):
        ngram = text[i:i + n]
        if ngram.strip():
            ngrams.append(ngram)
    return ngrams


# 同义词映射（简化版，完整版在 knowledge.py _SYNONYM_MAP）
_SYNONYM_SIMPLE = {
    '大模型': 'llm',
    '人工智能': 'ai',
    'machine learning': 'ai',
    '深度学习': 'ai',
}

def _normalize_alias_simple(keyword: str) -> str:
    """简化的同义词归一（仅用于门控探针，不追求完整覆盖）"""
    kw = keyword.strip().lower()
    if kw in _SYNONYM_SIMPLE:
        return _SYNONYM_SIMPLE[kw]
    return kw


# ═══════════════════════════════════════════════════════════════════
# 快速探针与裁决
# ═══════════════════════════════════════════════════════════════════

GENERIC_PROBE_THRESHOLD = 3  # 泛指词命中 ≥ 3 页才放行


def _quick_probe(keyword: str, pages_index: list) -> dict:
    """
    n-gram 快速探针（只查 index.json 预计算 _ng3，不触发文件 IO）。

    Args:
        keyword: 归一化后的关键词
        pages_index: index.json 的 pages 列表

    Returns:
        {"hit_count": int, "matched_domains": [str]}
    """
    kw_ng3 = set(_generate_ngrams(keyword, 3))
    if not kw_ng3:
        return {"hit_count": 0, "matched_domains": []}

    hit_count = 0
    matched_domains = set()
    seen_paths = set()

    for page in pages_index:
        if page["path"] in seen_paths:
            continue
        page_ng3 = page.get("_ng3")
        if not page_ng3:
            continue
        overlap = len(kw_ng3 & set(page_ng3))
        if overlap / len(kw_ng3) >= 0.3:
            seen_paths.add(page["path"])
            hit_count += 1
            matched_domains.add(page.get("domain", ""))

    return {"hit_count": hit_count, "matched_domains": list(matched_domains)}


def should_skip_inject(keyword: str, pages_index: list) -> dict:
    """
    判断是否应跳过 knowledge_inject。

    Args:
        keyword: 原始查询关键词
        pages_index: index.json 的 pages 列表

    Returns:
        {"skip": False}  → 通过，继续 inject
        {"skip": True, "reason": "...", "detail": "..."}  → 拦截
    """
    if not keyword or len(keyword.strip()) < 2:
        return {"skip": True, "reason": "keyword_too_short",
                "detail": f"关键词 '{keyword}' 过短，跳过", "_v": _VERSION}

    kw = keyword.strip()

    # 阶段 1：专指词 → 直接通过
    if _is_specific_term(kw):
        return {"skip": False, "_v": _VERSION}

    # 阶段 2：泛指词 → n-gram 快速探针
    if _is_generic_word(kw):
        # 归一化后探针
        norm_kw = _normalize_alias_simple(kw)
        probe = _quick_probe(norm_kw, pages_index)

        # 短词豁免：纯 ASCII 且归一化后 < 3 字符时 n-gram 无法生成有效匹配
        # 探针必然返回 0 命中，但知识库可能大量覆盖（如"AI"66页）
        # 注意：中文 2 字词（"设置"）不豁免——中文 2 字已可产生 2-gram 匹配
        if re.match(r'^[a-zA-Z]+$', norm_kw) and len(norm_kw) < 3:
            return {"skip": False}

        if probe["hit_count"] < GENERIC_PROBE_THRESHOLD:
            return {
                "skip": True,
                "reason": "generic_word_no_hits",
                "detail": (
                    f"泛指词 '{kw}' 仅命中 {probe['hit_count']} 页 "
                    f"（阈值 {GENERIC_PROBE_THRESHOLD}），跳过完整管线"
                ),
                "probe": probe,
            }
        # 命中 ≥ 3 页 → 放行
        return {"skip": False}

    # 阶段 3：非专非泛 → 放行（中间地带，不确定就放行）
    return {"skip": False}