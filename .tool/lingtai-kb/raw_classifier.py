# -*- coding: utf-8 -*-
"""
原料分级器（Raw Classifier）
==============================
入库时对原料做快速分级，不调 LLM，纯规则判断。

分级结果写入 frontmatter 的 `提炼分级:` 字段：
- 快速 → 走快速通道（补索引+链接，15秒/篇，不调 LLM）
- 正常 → 现有提炼流程
- 完整 → 完整提炼流程（跨域关联、深度分析）
"""

import re

# ─── 噪声检测（借鉴 Polaris memoryNaturalSourceMessage.ts）───

CONTINUATION_PREFIXES = [
    "上一条回答在中途停住了",
    "上一条回答里的工具调用或代码参数在中途截断了",
    "继续沿着这条消息往下写",
    "继续到达终点",
]

TOOL_PATTERNS = [
    r'```tool_call',
    r'candidateId|messageIds|sourceConversationIds',
    r'result\["content"\]',
    r'memoryId|memoryDocId|toolCallId',
    r'<\|system\|>|<\|user\|>|<\|assistant\|>',
    r'^system:\s',
    r'^assistant:\s$',
]


def is_natural_dialogue(text: str) -> bool:
    """
    判断是否为自然对话文本（Polaris 风格 isNaturalMemorySourceMessage）
    True = 可保留，False = 应过滤
    """
    if not text or not text.strip():
        return False
    norm = text.strip()

    # 1. 续写指令
    for prefix in CONTINUATION_PREFIXES:
        if norm.startswith(prefix):
            return False

    # 2. 工具调用/系统消息特征
    for pattern in TOOL_PATTERNS:
        if re.search(pattern, norm, re.IGNORECASE):
            return False

    # 3. 多行机器文本
    lines = [l.strip() for l in norm.split('\n') if l.strip()]
    if len(lines) > 10:
        bullet_like = sum(1 for l in lines if re.match(r'^[-*•]|\d+[.)]\s', l))
        if bullet_like >= 3:
            return False
        long_strs = len(re.findall(r'[A-Za-z0-9_./:@-]{40,}', norm))
        if long_strs >= 3:
            return False

    # 4. 代码块密集
    code_blocks = len(re.findall(r'```', norm)) // 2
    if code_blocks >= 2:
        return False

    # 5. JSON 格式
    if norm.startswith('{') and norm.endswith('}'):
        try:
            import json
            json.loads(norm)
            return False
        except Exception:
            pass

    return True


def filter_noise(content: str) -> dict:
    """
    噪声过滤步骤（用于 raw_preprocess --step filter_noise）
    
    Args:
        content: 原料/对话全文
    
    Returns:
        dict: {filtered, reason, keep_lines, dropped_lines}
    """
    if is_natural_dialogue(content):
        return {'filtered': False, 'reason': '', 'keep_lines': [content], 'dropped_lines': []}

    lines = content.split('\n')
    keep = [l for l in lines if is_natural_dialogue(l)]
    drop = [l for l in lines if not is_natural_dialogue(l)]

    if keep:
        return {
            'filtered': True,
            'reason': f'已过滤 {len(drop)} 行噪声（工具调用/续写指令/机器文本）',
            'keep_lines': keep,
            'dropped_lines': drop,
        }
    return {
        'filtered': True,
        'reason': '全文被判定为噪声',
        'keep_lines': [],
        'dropped_lines': lines,
    }


def classify(content: str, category: str = "", title_len: int = 0) -> str:
    """
    对原料内容做快速分级
    
    Args:
        content: 原料全文
        category: 传入的分类（可选）
        title_len: 文件名截取长度（用于估算整体长度）
    
    Returns:
        str: "快速" / "正常" / "完整"
    """
    total_len = len(content)
    
    # ── 快速通道判定（任意一条命中即走快速）──
    
    # 1. 内容太短（< 10 字，纯碎片信息）
    if total_len < 10:
        return "快速"
    
    # 2. 模糊表达开头（可能、我觉得、也许）
    fuzzy_starts = ["我觉得", "可能是", "也许是", "可能是", "我想说", "我感觉", "随便记", "备忘"]
    first_line = content.strip().split("\n")[0][:20]
    for kw in fuzzy_starts:
        if first_line.startswith(kw):
            return "快速"
    
    # 3. 无具体事实信号：不含日期、数字、人名关键词
    has_date = bool(re.search(r'\d{4}[-/年]\d{1,2}[-/月]\d{1,2}', content))
    has_number = bool(re.search(r'\d{2,}', content))  # 两位数以上数字
    has_name = bool(re.search(r'[用户]说|作者|项目|公司|版本|v\d+|[A-Z]{2,}', content))  # 大写缩写如PEP8/Python/POST
    if not has_date and not has_number and not has_name and total_len < 100:
        return "快速"
    
    # 4. category 为日常/临时
    if category in ("日常", "临时", "笔记"):
        return "快速"
    
    # ── 完整提炼判定（高价值内容）──
    
    # 1. 长文本 + 多概念
    if total_len >= 500:
        # 检测是否有多个概念（句号/分号分隔的独立观点）
        clauses = re.split(r'[。；\n]', content)
        meaningful_clauses = [c for c in clauses if len(c) >= 10]
        if len(meaningful_clauses) >= 3:
            return "完整"
    
    # 2. 含跨域关联（多个 [[]] wikilink 或跨域关键词）
    has_wikilinks = content.count("[[") >= 2
    if has_wikilinks and total_len >= 200:
        return "完整"
    
    # 3. category 为深度分析类
    if category in ("思考", "认知", "分析", "哲学"):
        if total_len >= 140:
            return "完整"
    
    # ── 默认：正常提炼 ──
    return "正常"


def classify_file(filepath: str) -> str:
    """
    对已保存的原料文件重新分级（用于批量重审）
    
    Args:
        filepath: 原料文件路径
    
    Returns:
        str: 分级结果
    """
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()
    except Exception:
        return "正常"
    
    # 提取 frontmatter 中的分类
    category = ""
    fm_match = re.search(r'^---\n(.*?)\n---', content, re.DOTALL)
    if fm_match:
        fm = fm_match.group(1)
        cat_match = re.search(r'^category:\s*(.+)$', fm, re.MULTILINE)
        if cat_match:
            category = cat_match.group(1).strip()
    
    # 去掉 frontmatter 后的正文
    body = re.sub(r'^---.*?---\s*', '', content, count=1, flags=re.DOTALL)
    
    return classify(body, category)