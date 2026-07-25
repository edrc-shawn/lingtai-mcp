# -*- coding: utf-8 -*-
"""
对话摘要流水线（方向⑦）
=========================
借鉴 Polaris conversationSummaryMemory.ts 理念。

功能：
- 将原始对话/文本自动摘要化
- 分类：personality（用户身份偏好）/ topic（讨论主题）/ decision（决定共识）
- 生成结构化摘要 frontmatter 字段
- 纯规则判断，不调 LLM
"""

import re
from datetime import datetime
from typing import Dict, List, Optional


# ─── 总结类型检测模式 ───

# personality：身份/偏好信号
PERSONALITY_PATTERNS = {
    'identity': [
        r'(?:我|咱|我们)\s*(?:是|叫|在|做|从事|家住|来自)',
        r'(?:我|咱)\s*(?:的[^，。]*?(?:身份|工作|职业|专业|家乡|年龄))',
        r'(?:称呼|叫我|叫我|叫我)',
        r'(?:我是|我是做|我是学|我住在|我来自)',
    ],
    'preference': [
        r'(?:我|咱)\s*(?:喜欢|偏好|热爱|讨厌|不喜欢|受不了)',
        r'(?:最[^，。]*?(?:喜欢|爱|讨厌|烦))',
        r'(?:更[^，。]*?(?:喜欢|倾向|愿意|习惯))',
        r'(?:习惯|常用|日常)(?:性|地|的)',
        r'(?:推荐|安利|种草|踩雷)',
    ],
    'habit': [
        r'(?:每天|每周|平时|通常|总是|经常|偶尔|很少)',
        r'(?:作息|早睡|熬夜|早起|午休|运动|健身|跑步)',
        r'(?:在(?:读|看|学|做|写|画|练))',
    ],
}

# topic：主题讨论信号
TOPIC_PATTERNS = {
    'technology': [
        r'(?:技术|编程|代码|算法|框架|库|API|SDK|部署|架构)',
        r'(?:Python|Java|Go|Rust|TypeScript|JavaScript|React|Vue|Docker|K8s)',
        r'(?:AI|人工智能|机器(?:学习|智能)|深度(?:学习|神经网络)|大模型|LLM|GPT)',
        r'(?:数据库|缓存|MQ|Redis|MySQL|PostgreSQL|MongoDB)',
    ],
    'philosophy': [
        r'(?:哲学|思考|认知|思维|逻辑|方法论|反思|元认知)',
        r'(?:存在|意义|价值|本质|现象|本原|形而上学)',
        r'(?:追问|批判|怀疑|辩证|归纳|演绎)',
    ],
    'creation': [
        r'(?:创作|写作|写[^，。]*?(?:文章|小说|诗|剧本)|内容|编辑|排版)',
        r'(?:设计|UI|UX|交互|视觉|配色|字体|排版|原型)',
        r'(?:画|绘|插画|动画|建模|渲染|3D)',
    ],
    'business': [
        r'(?:商业|公司|创业|产品|运营|市场|营销|增长)',
        r'(?:融资|投资|营收|利润|成本|ROI|GMV|DAU)',
        r'(?:用户|客户|需求|痛点|场景|竞品|赛道)',
    ],
    'social': [
        r'(?:社会|文化|教育|政策|经济|民生|环境|公益)',
        r'(?:新闻|热点|事件|趋势|讨论|观点|立场)',
    ],
}

# decision：决定/共识信号
DECISION_PATTERNS = [
    r'(?:决定|约定|商量好|说好了|就这么办|同意|赞成|通过)',
    r'(?:下[^，。]*?(?:次|周|月|一步|一个|一次))',
    r'(?:截止|DDL|deadline|交付|提交|完成|上线|发布)',
    r'(?:确认|认可|批准|采纳|落地|推进|执行)',
    r'(?:计划|安排|排期|规划|筹备|准备)',
]

# 代码块特征（检测是否为纯技术输出）
CODE_BLOCK_PATTERN = re.compile(r'```[\s\S]*?```')
# 对话标记
DIALOGUE_MARKERS = re.compile(r'(?:user[：:]|assistant[：:]|我说|他说|她说|你说|AI[：:])', re.IGNORECASE)

# ─── 主摘要引擎 ───

def _has_dialogue_form(text: str) -> bool:
    """检测是否为对话形式"""
    return bool(DIALOGUE_MARKERS.search(text))

def _count_code_blocks(text: str) -> int:
    """统计代码块数量"""
    return len(CODE_BLOCK_PATTERN.findall(text))

def _extract_first_n_lines(text: str, n: int = 5) -> List[str]:
    """提取前 n 行"""
    lines = [l.strip() for l in text.split('\n') if l.strip()]
    return lines[:n]

def _detect_summary_kind(text: str) -> str:
    """
    判断对话/文本的主要总结类型
    
    Returns: 'personality' | 'topic' | 'decision' | 'mixed' | 'general'
    """
    scores = {'personality': 0, 'topic': 0, 'decision': 0}
    
    # personality 检测
    for category, patterns in PERSONALITY_PATTERNS.items():
        for pat in patterns:
            matches = re.findall(pat, text)
            scores['personality'] += len(matches) * (2 if category == 'identity' else 1)
    
    # topic 检测
    for domain, patterns in TOPIC_PATTERNS.items():
        for pat in patterns:
            matches = re.findall(pat, text)
            scores['topic'] += len(matches)
    
    # decision 检测
    for pat in DECISION_PATTERNS:
        matches = re.findall(pat, text)
        scores['decision'] += len(matches) * 2  # 决策信号权重更高
    
    # 确定主类型
    sorted_scores = sorted(scores.items(), key=lambda x: -x[1])
    top_score = sorted_scores[0][1]
    second_score = sorted_scores[1][1] if len(sorted_scores) > 1 else 0
    
    if top_score == 0:
        return 'general'
    
    # 如果两个类型接近，标记为 mixed
    if second_score > 0 and second_score >= top_score * 0.6:
        return 'mixed'
    
    return sorted_scores[0][0]


def _generate_summary_text(text: str, kind: str, max_chars: int = 200) -> str:
    """生成摘要文本（取第一批有信息量的行）"""
    lines = [l.strip() for l in text.split('\n') if l.strip()]
    
    # 跳过纯 frontmatter 行
    cleaned = []
    in_fm = False
    for line in lines:
        if line == '---':
            in_fm = not in_fm
            continue
        if in_fm:
            continue
        if len(line) < 3:
            continue
        cleaned.append(line)
    
    # 取前几个有信息量的行组合成摘要
    summary_lines = []
    chars = 0
    for line in cleaned[:10]:
        if chars + len(line) > max_chars:
            break
        summary_lines.append(line)
        chars += len(line)
    
    return ' | '.join(summary_lines) if summary_lines else text[:max_chars]


def _extract_decisions(text: str, max_items: int = 5) -> List[str]:
    """提取决策/决定事项"""
    decisions = []
    for pat in DECISION_PATTERNS:
        matches = re.findall(pat, text)
        for m in matches[:max_items]:
            # 提取匹配行附近的完整句
            lines = text.split('\n')
            for i, line in enumerate(lines):
                if m in line:
                    sentence = line.strip()[:120]
                    if sentence not in decisions:
                        decisions.append(sentence)
                    break
    return decisions[:max_items]


def _extract_topics(text: str, max_items: int = 5) -> List[str]:
    """提取讨论主题"""
    topics = set()
    for domain, patterns in TOPIC_PATTERNS.items():
        for pat in patterns:
            matches = re.findall(pat, text)
            for m in matches[:3]:
                topics.add(m.lower())
    return sorted(topics)[:max_items]


def build_dialogue_summary(content: str, source: str = '对话') -> dict:
    """
    对原料内容生成结构化摘要（主入口）
    
    借鉴 Polaris conversationSummaryMemory.ts 的 SummaryKind 类型：
    - relational_profile → personality（关系画像）
    - recent_topic → topic（近期主题）
    - 新增 decision（决定共识）
    
    Args:
        content: 原料正文（不含 frontmatter）
        source: 来源标签
    
    Returns:
        dict: 摘要结果
    """
    clean = content.strip()
    if not clean:
        return {'kind': None, 'summary': '', 'decisions': [], 'topics': []}
    
    code_ratio = _count_code_blocks(clean) / max(len(clean), 1) * 1000
    is_dialogue = _has_dialogue_form(clean)
    
    kind = _detect_summary_kind(clean)
    summary_text = _generate_summary_text(clean, kind)
    decisions = _extract_decisions(clean)
    topics = _extract_topics(clean)
    
    # 生成结构化前件
    fm_fields = {}
    if kind:
        fm_fields['summary_kind'] = kind
    if is_dialogue:
        fm_fields['is_dialogue'] = True
    if code_ratio > 5:
        fm_fields['code_ratio'] = round(code_ratio, 1)
    if decisions:
        fm_fields['decisions'] = decisions
    if topics:
        fm_fields['topics'] = topics
    
    return {
        'kind': kind,
        'summary': summary_text,
        'is_dialogue': is_dialogue,
        'decisions': decisions,
        'topics': topics,
        'fm_fields': fm_fields,
    }


def attach_summary_to_frontmatter(existing_fm: str, summary: dict) -> str:
    """
    将摘要结果附加到现有 frontmatter 上
    
    Args:
        existing_fm: 已有的 frontmatter 文本（不含 --- 边界）
        summary: build_dialogue_summary 的返回
    
    Returns:
        str: 增强后的 frontmatter 文本
    """
    if not summary or not summary.get('kind'):
        return existing_fm
    
    fields = summary.get('fm_fields', {})
    additions = []
    
    if 'summary_kind' in fields:
        additions.append(f"总结类型: {fields['summary_kind']}")
    
    if summary.get('summary'):
        # summary 较长，放在最后
        pass
    
    if fields.get('is_dialogue'):
        additions.append("来源格式: 对话")
    
    if fields.get('code_ratio'):
        additions.append(f"代码密度: {fields['code_ratio']}‰")
    
    if fields.get('topics'):
        topics_str = ', '.join(fields['topics'][:5])
        additions.append(f"关键词: {topics_str}")
    
    if fields.get('decisions'):
        for i, d in enumerate(fields['decisions'][:3], 1):
            additions.append(f"决策{i}: {d[:60]}")
    
    if not additions:
        return existing_fm
    
    augmented = existing_fm.rstrip()
    for line in additions:
        if line not in augmented:
            augmented += '\n' + line
    
    return augmented


def enrich_raw_material(filepath: str, content: str) -> dict:
    """
    对原料文件执行完整摘要分析（可直接集成到 raw_preprocess 流程）
    
    Args:
        filepath: 原料文件路径
        content: 文件完整内容（含 frontmatter）
    
    Returns:
        dict: 处理结果
    """
    # 提取 frontmatter 和正文
    body = content
    fm_text = ''
    if content.startswith('---'):
        parts = content.split('---', 2)
        if len(parts) >= 3:
            fm_text = parts[1]
            body = parts[2]
    
    # 如果正文太短，不处理
    if len(body.strip()) < 20:
        return {'file': filepath, 'status': 'skipped', 'reason': '内容过短'}
    
    # 生成摘要
    summary = build_dialogue_summary(body)
    
    if not summary.get('kind'):
        return {'file': filepath, 'status': 'skipped', 'reason': '未检测到可总结内容'}
    
    # 构建新 frontmatter
    new_fm = attach_summary_to_frontmatter(fm_text, summary)
    
    # 如果 frontmatter 有变化，加上摘要文本
    new_content = content
    if new_fm != fm_text:
        if content.startswith('---'):
            parts = content.split('---', 2)
            if len(parts) >= 3:
                new_content = f"---\n{new_fm}---\n{parts[2]}"
    
    return {
        'file': filepath,
        'status': 'summarized',
        'kind': summary['kind'],
        'summary': summary['summary'],
        'topics': summary['topics'],
        'decisions': summary['decisions'],
        'has_changes': new_fm != fm_text,
        'new_content': new_content if new_fm != fm_text else None,
    }


def batch_summarize_raw_materials(raw_dir: str, max_files: int = 20) -> dict:
    """
    批量摘要化原料目录中的文件（增量更新，差距⑤）
    
    借鉴 Polaris conversationSummaryMemory.ts 的分批模式：
    - 按时间排序，每次最多处理 max_files 个
    - 已摘要过的跳过（检测 frontmatter 中是否有 summary_kind 字段）
    - 代码块密集的文本自动剥离
    
    Args:
        raw_dir: 原料目录路径
        max_files: 单次最大处理数
    
    Returns:
        dict: {processed, skipped, errors, results}
    """
    import os
    if not os.path.isdir(raw_dir):
        return {'processed': 0, 'skipped': 0, 'errors': 0, 'results': []}

    files = sorted(
        [f for f in os.listdir(raw_dir) if f.endswith('.md')],
        key=lambda f: os.path.getmtime(os.path.join(raw_dir, f)),
        reverse=True
    )[:max_files]

    processed = 0
    skipped = 0
    errors = 0
    results = []

    for fname in files:
        fpath = os.path.join(raw_dir, fname)
        try:
            with open(fpath, 'r', encoding='utf-8') as f:
                content = f.read()

            # 检查是否已摘要过
            if 'summary_kind:' in content[:500]:
                skipped += 1
                continue

            # 剥离代码块（Polaris 风格）
            body = content
            if content.startswith('---'):
                parts = content.split('---', 2)
                if len(parts) >= 3:
                    body = parts[2]
            code_stripped = re.sub(r'```[\s\S]*?```', '[代码块已略过]', body)

            # 生成摘要
            summary = build_dialogue_summary(code_stripped)
            if not summary.get('kind'):
                skipped += 1
                continue

            # 更新 frontmatter
            if content.startswith('---'):
                parts = content.split('---', 2)
                if len(parts) >= 3:
                    new_fm = attach_summary_to_frontmatter(parts[1], summary)
                    new_content = f"---\n{new_fm}---\n{parts[2]}"
                    with open(fpath, 'w', encoding='utf-8') as f:
                        f.write(new_content)
                    processed += 1
                    results.append({'file': fname, 'kind': summary['kind']})
            else:
                skipped += 1

        except Exception as e:
            errors += 1
            results.append({'file': fname, 'error': str(e)})

    return {
        'processed': processed,
        'skipped': skipped,
        'errors': errors,
        'total': len(files),
        'results': results,
    }


if __name__ == '__main__':
    # 测试
    test_text = """user: 我是个前端开发者，平时喜欢用 React 和 TypeScript
assistant: 理解，你在做前端开发
user: 我决定下个月把项目迁移到 Next.js 上
assistant: 这是个不错的决定"""
    
    result = build_dialogue_summary(test_text)
    print(f"kind: {result['kind']}")
    print(f"summary: {result['summary']}")
    print(f"decisions: {result['decisions']}")
    print(f"topics: {result['topics']}")
