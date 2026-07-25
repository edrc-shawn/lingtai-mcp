# -*- coding: utf-8 -*-
"""
灵识 TokenJuice 压缩层
======================
OpenHuman TokenJuice 启发：所有 payload 在接触 LLM 前先压缩。

核心功能：
1. smart_truncate — 智能截断（保留高价值段落，不是硬截断）
2. compress_page — 多级压缩（按预算选择摘要级别）
3. dedup_check — 去重（防止同一会话中重复注入同一内容）
4. estimate_tokens — Token 估算（复用 perception.py 的函数）
"""

import re
import hashlib
from typing import List, Optional, Dict

# Inline helpers (from summary_tree, avoid circular import)
def _parse_frontmatter(content):
    result = {'metadata': {}, 'body': content}
    if content.startswith('---'):
        parts = content.split('---', 2)
        if len(parts) >= 3:
            fm_text = parts[1].strip()
            body = parts[2].strip()
            result['body'] = body
            for line in fm_text.split('\n'):
                line = line.strip()
                if ':' in line:
                    key, val = line.split(':', 1)
                    result['metadata'][key.strip()] = val.strip().strip('"\'')
            return result
    return result

def _extract_section(body, section_title):
    import re
    p = r'^##\s*' + re.escape(section_title) + r'\s*$(.*?)(?=^##|\Z)'
    m = re.search(p, body, re.MULTILINE | re.DOTALL)
    if m:
        return m.group(1).strip()
    return None

def _extract_section_headings(body):
    h = re.findall(r'^##\s+(.+)$', body, re.MULTILINE)
    return [x.strip() for x in h if x.strip()]


# 压缩级别定义的 token 预算阈值
COMPRESSION_LEVELS = {
    'tldr': 50,      # 仅标题 + 一句话摘要
    'brief': 200,    # 关键点概要
    'normal': 500,   # 完整摘要
    'full': 1000,    # 接近全文
}

# 预估 token 数（复用 perception.py 的精确估算）
def estimate_tokens(text: str) -> int:
    """精确估算文本 token 数"""
    if not text:
        return 0
    total = 0
    cjk = re.findall(r'[\u4e00-\u9fff]', text)
    total += len(cjk) / 1.3
    words = re.findall(r'[a-zA-Z]+', text)
    for w in words:
        total += len(w) / 3.8
    nums = re.findall(r'[0-9]+', text)
    for n in nums:
        total += len(n) / 2.0
    remaining = len(text) - len(''.join(cjk)) - len(''.join(words)) - len(''.join(nums))
    total += remaining / 2.0
    return max(1, int(total + 5))


def select_compression_level(max_tokens: int) -> str:
    """根据 token 预算选择压缩级别"""
    for level, threshold in sorted(COMPRESSION_LEVELS.items(), key=lambda x: x[1]):
        if max_tokens <= threshold:
            return level
    return 'full'


def smart_truncate(text: str, max_tokens: int, preserve_sections: Optional[List[str]] = None) -> str:
    """
    智能截断——保留高价值段落，不是硬截断。
    
    策略：
    1. 如果全文在预算内，原样返回
    2. 如果超预算，按段落分割，优先保留：
       - 摘要/要点区域（开头部分）
       - 指定的关键 section（如 preserve_sections=['要点', '总结']）
       - 标记了 ## 或 ### 的标题段落
    3. 最后才截断尾部
    
    Args:
        text: 原文
        max_tokens: token 预算
        preserve_sections: 优先保留的 section 标题关键字
    
    Returns:
        str: 压缩后的文本
    """
    if not text or estimate_tokens(text) <= max_tokens:
        return text
    
    if preserve_sections is None:
        preserve_sections = ['要点', '摘要', '总结', '核心']
    
    # 按空行或标题分割为段落
    lines = text.split('\n')
    paragraphs = []
    current_para = []
    for line in lines:
        if line.startswith('#') and current_para:
            paragraphs.append('\n'.join(current_para))
            current_para = [line]
        elif line.strip() == '' and current_para:
            paragraphs.append('\n'.join(current_para))
            current_para = []
        else:
            current_para.append(line)
    if current_para:
        paragraphs.append('\n'.join(current_para))
    
    # 给每个段落打分
    scored = []
    for p in paragraphs:
        if not p.strip():
            continue
        score = 0
        first_line = p.split('\n')[0]
        if first_line.startswith('#'):
            score += 10
            for section in preserve_sections:
                if section in first_line:
                    score += 20
        if p.startswith('- ') or p.startswith('* ') or re.match(r'^\d+\.', p):
            score += 5
        if p.startswith('```'):
            score -= 5
        if '|' in p and p.strip().startswith('|'):
            score += 3
        if len(p) < 100 and not p.startswith('#'):
            score += 2
        scored.append((p, score))
    
    # 预算内尽量保持原始顺序，优先保留高价值段落
    used_tokens = 0
    result_parts = []
    
    for p, score in scored:
        t = estimate_tokens(p)
        if used_tokens + t <= max_tokens:
            result_parts.append(p)
            used_tokens += t
        elif score >= 15:
            # 高价值段落即使超预算也压缩保留
            ratio = max_tokens / max(1, used_tokens + t)
            truncated = p[:int(len(p) * ratio * 0.8)]
            if len(truncated) > 20:
                result_parts.append(truncated + '\n…（截断）')
            used_tokens = max_tokens
    
    result = '\n\n'.join(result_parts)
    
    if estimate_tokens(result) > max_tokens:
        chars_per_token = len(result) / max(1, estimate_tokens(result))
        max_chars = int(max_tokens * chars_per_token * 0.9)
        result = result[:max_chars] + "\n\n…（内容已截断）"
    
    return result


def dedup_key(content: str) -> str:
    """
    生成去重键——用于判断同一内容是否已注入过。
    
    使用内容的前 100 字 + 长度做 hash，平衡准确性 vs 碰撞率。
    """
    prefix = content.strip()[:100]
    length = len(content)
    raw = f"{prefix}|{length}"
    return hashlib.md5(raw.encode('utf-8')).hexdigest()[:16]


def compress_page_simple(content: str, max_tokens: int) -> Dict:
    """
    单页内容压缩——返回压缩后的文本和元数据。
    
    这是最简实现，后续可扩展到 LLM 生成摘要。
    
    Args:
        content: 页面全文
        max_tokens: 目标 token 预算
    
    Returns:
        dict: {text, original_tokens, compressed_tokens, savings_pct, level}
    """
    original_tokens = estimate_tokens(content)
    level = select_compression_level(max_tokens)
    
    if original_tokens <= max_tokens:
        return {
            'text': content,
            'original_tokens': original_tokens,
            'compressed_tokens': original_tokens,
            'savings_pct': 0,
            'level': 'full',
            'truncated': False,
        }
    
    compressed = smart_truncate(content, max_tokens)
    compressed_tokens = estimate_tokens(compressed)
    savings_pct = round((1 - compressed_tokens / max(1, original_tokens)) * 100, 1)
    
    return {
        'text': compressed,
        'original_tokens': original_tokens,
        'compressed_tokens': compressed_tokens,
        'savings_pct': savings_pct,
        'level': level,
        'truncated': compressed_tokens < original_tokens,
    }


# 会话级去重缓存（用于防止同一会话中重复注入）
_session_dedup_cache: set = set()

def reset_session_cache():
    """重置会话级去重缓存"""
    _session_dedup_cache.clear()

def check_and_mark_dedup(content: str) -> bool:
    """
    检查去重并标记。
    
    Returns:
        True 如果已存在（重复），False 如果第一次见
    """
    key = dedup_key(content)
    if key in _session_dedup_cache:
        return True  # 重复
    _session_dedup_cache.add(key)
    return False  # 首次


# 注入预算分配器
def allocate_inject_budget(
    candidates: List[Dict],
    max_tokens: int,
    priority_field: str = 'inject_priority',
    priority_order: tuple = ('core', 'preference', 'context'),
) -> List[Dict]:
    """
    预算感知分配——按优先级分配 token 预算，而不是简单截断。
    
    类似 Polaris 的 requestMemoryPlan.ts 策略：
    - core 记忆 → 始终注入
    - preference → 按相关性注入
    - context → 仅在匹配时注入
    
    Args:
        candidates: 候选列表（需含 estimated_tokens 和 inject_priority 字段）
        max_tokens: 总预算
        priority_field: 优先级字段名
        priority_order: 优先级顺序（高→低）
    
    Returns:
        list: 分配后的候选列表（含 status: kept/dropped_budget）
    """
    priority_rank = {p: i for i, p in enumerate(priority_order)}
    
    # 先按优先级分组
    by_priority: Dict[str, list] = {}
    for c in candidates:
        p = c.get(priority_field, 'context')
        by_priority.setdefault(p, []).append(c)
    
    result = []
    used = 0
    dropped = []
    
    # 按优先级从高到低分配
    for priority in priority_order:
        items = by_priority.get(priority, [])
        for c in items:
            tokens = c.get('estimated_tokens', estimate_tokens(c.get('summary', '')))
            if used + tokens <= max_tokens:
                c['status'] = 'kept'
                used += tokens
                result.append(c)
            else:
                c['status'] = 'dropped_budget'
                dropped.append(c)
    
    return result


# === Block extraction + scoring (Memory Tree style) ===

SECTION_IMPORTANCE = {
    '摘要': 30, '要点': 25, '核心': 20, '总结': 20,
    '架构': 18, '设计': 18, '概述': 15, '用法': 15,
    '对比': 12, '功能': 12, '安装': 8, '配置': 8,
    '背景': 8, '推荐阅读': 2, '参考': 2, '附录': 1,
    '日志': 1, '变更': 1,
}

SECTION_KEYWORD_MAP = [
    (['核心','关键','主要','重要'], 20),
    (['架构','设计','原理','机制','模型'], 18),
    (['对比','区别','vs','VS','差异'], 15),
    (['功能','特性','能力','用法','使用'], 12),
    (['安装','部署','配置','设置','环境'], 8),
    (['背景','动机','缘起','目的','目标'], 8),
    (['例子','示例','案例','实战','教程'], 8),
    (['附录','参考','相关','延伸','拓展'], 2),
    (['日志','变更','历史','更新','changelog'], 1),
]


def _section_importance(section_title):
    if section_title in SECTION_IMPORTANCE:
        return SECTION_IMPORTANCE[section_title]
    for keywords, score in SECTION_KEYWORD_MAP:
        for kw in keywords:
            if kw in section_title:
                return score
    return 5


def extract_blocks(content, page_title="", domain="", pinji=""):
    parsed = _parse_frontmatter(content)
    metadata = parsed['metadata']
    body = parsed['body']
    title = page_title or metadata.get('标题', metadata.get('title', ''))
    pinji_score = {'上品': 10, '中品': 5, '下品': 2, '': 3}
    base_score = pinji_score.get(pinji, 3)
    blocks = []

    # 1. Title block
    title_block = '# ' + title
    blocks.append({'id': title + ':title', 'section': 'title', 'content': title_block,
        'score': base_score + 15, 'tokens': estimate_tokens(title_block),
        'page_title': title, 'domain': domain, 'pinji': pinji})

    # 2. Summary block
    summary_section = _extract_section(body, '摘要')
    if summary_section:
        summary_text = summary_section.strip()[:500]
        if summary_text:
            blocks.append({'id': title + ':摘要', 'section': '摘要',
                'content': '## 摘要\n' + summary_text,
                'score': base_score + 30, 'tokens': estimate_tokens(summary_text),
                'page_title': title, 'domain': domain, 'pinji': pinji})

    # 3. Section blocks
    headings = _extract_section_headings(body)
    for heading in headings:
        if heading == chr(25688)+chr(35201):
            continue
        section_content = _extract_section(body, heading)
        if not section_content:
            continue
        section_text = section_content.strip()
        if len(section_text) < 10:
            continue
        if estimate_tokens(section_text) > 3000:
            section_text = smart_truncate(section_text, 3000)
        importance = _section_importance(heading)
        score = base_score + importance
        blocks.append({'id': title + ':' + heading, 'section': heading,
            'content': '## ' + heading + '\n' + section_text,
            'score': score, 'tokens': estimate_tokens(section_text),
            'page_title': title, 'domain': domain, 'pinji': pinji})

    return blocks


def score_block_with_query(block, query_keywords):
    score = block['score']
    content_lower = (block['content'] + ' ' + block['section']).lower()
    match_count = 0
    for kw in query_keywords:
        kw_lower = kw.lower()
        if kw_lower in content_lower:
            match_count += 1
            if kw_lower in block['section'].lower() or kw_lower in block['page_title'].lower():
                score += 3
            else:
                score += 1

    tokens = block['tokens']
    chars = len(block['content'])
    if tokens > 0 and chars > 0:
        density = chars / tokens
        if density > 3.0:
            score += 2

    if tokens > 0:
        efficiency = score / max(1, tokens)
        if efficiency > 0.5:
            score += 3

    result = dict(block)
    result['score'] = score
    result['query_match'] = match_count
    return result


def select_top_blocks(all_blocks, max_tokens, query_keywords=None):
    if not all_blocks:
        return []

    keywords = query_keywords or []
    scored = []
    for block in all_blocks:
        b = score_block_with_query(block, keywords)
        b['status'] = 'dropped'
        scored.append(b)

    scored.sort(key=lambda b: -b['score'])

    selected = []
    used = 0
    for block in scored:
        tokens = block['tokens']
        if used + tokens <= max_tokens:
            block['status'] = 'kept'
            selected.append(block)
            used += tokens

    selected.sort(key=lambda b: (b['page_title'],
        {'title': 0, chr(25688)+chr(35201): 1}.get(b['section'], 99)))
    return selected
# === Folding (collapse low-score blocks into one-liners) ===

def select_top_blocks_fold(all_blocks, max_tokens, query_keywords=None):
    # Memory Tree style: high-score blocks keep detail, low-score blocks
    # are collapsed into section: one-line references instead of dropped.
    # Returns: {'kept': [blocks in full], 'folded': [one-liner refs], 'used_tokens': int}
    if not all_blocks:
        return {'kept': [], 'folded': [], 'used_tokens': 0, 'folded_count': 0}

    keywords = query_keywords or []
    scored = []
    for block in all_blocks:
        b = score_block_with_query(block, keywords)
        scored.append(b)

    scored.sort(key=lambda b: -b['score'])

    kept = []
    folded = []
    used = 0

    for block in scored:
        tokens = block['tokens']
        if used + tokens <= max_tokens:
            block['status'] = 'kept'
            kept.append(block)
            used += tokens
        else:
            section = block.get('section', '参考')
            content = block.get('content', '')
            one_line = content.split('\n')[0].strip()[:80]
            if len(one_line) > 10:
                folded.append({
                    'page_title': block.get('page_title', ''),
                    'section': section,
                    'summary': one_line,
                    'score': block['score'],
                    'tokens': tokens,
                })

    kept.sort(key=lambda b: (b['page_title'],
        {'title': 0, chr(25688)+chr(35201): 1}.get(b['section'], 99)))

    return {'kept': kept, 'folded': folded, 'used_tokens': used, 'folded_count': len(folded)}


# === Dynamic scoring (decay + frequency) ===

import json
from pathlib import Path
from datetime import datetime

_DECAY_FILE = None
_DECAY_DATA = {}


def _decay_path(vault_path=None):
    if vault_path is None:
        vault_path = r'.'
    return Path(vault_path) / '.tool' / 'lingtai-kb' / '.cache' / 'block_decay.json'


def _load_decay(vault_path=None):
    global _DECAY_DATA
    path = _decay_path(vault_path)
    if path.exists():
        try:
            _DECAY_DATA = json.loads(path.read_text(encoding='utf-8'))
        except Exception:
            _DECAY_DATA = {}
    return _DECAY_DATA


def _save_decay(vault_path=None):
    path = _decay_path(vault_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_DECAY_DATA, ensure_ascii=False, indent=2), encoding='utf-8')


def record_injection(block_ids, vault_path=None):
    # Record that these blocks were injected (increment ref count)
    _load_decay(vault_path)
    now = datetime.now().isoformat()
    for bid in block_ids:
        if bid not in _DECAY_DATA:
            _DECAY_DATA[bid] = {'hits': 0, 'first_seen': now}
        _DECAY_DATA[bid]['hits'] = _DECAY_DATA[bid].get('hits', 0) + 1
        _DECAY_DATA[bid]['last_hit'] = now
    _save_decay(vault_path)


def decay_factor(block_id, vault_path=None):
    # Calculate decay factor for a block (0.0-1.0).
    # Recency: 7 days full, then -1.5%/day
    # Frequency: >=3 hits gets bonus (validation signal)
    _load_decay(vault_path)
    entry = _DECAY_DATA.get(block_id)
    if not entry:
        return 1.0

    last_hit_str = entry.get('last_hit')
    if not last_hit_str:
        return 1.0

    try:
        last_hit = datetime.fromisoformat(last_hit_str)
    except Exception:
        return 1.0

    days_since = (datetime.now() - last_hit).days

    # Recency decay
    if days_since <= 7:
        recency = 1.0
    else:
        recency = max(0.3, 1.0 - (days_since - 7) * 0.015)

    # Frequency bonus: up to +30%
    hits = entry.get('hits', 0)
    freq = 1.0 + min(0.3, hits * 0.05)

    return round(recency * freq, 3)


def score_block_with_decay(block, query_keywords=None, vault_path=None):
    # Score a block with query relevance + dynamic decay.
    # final_score = query_score * decay_factor
    scored = score_block_with_query(block, query_keywords or [])
    decay = decay_factor(scored.get('id', ''), vault_path)
    original = scored['score']
    scored['score'] = round(original * decay, 1)
    scored['decay'] = decay
    scored['score_before_decay'] = original
    return scored


def select_top_blocks_dynamic(all_blocks, max_tokens, query_keywords=None, vault_path=None):
    # Dynamic-aware version of select_top_blocks_fold.
    # Uses decay_factor to adjust scores before selection + folding.
    if not all_blocks:
        return {'kept': [], 'folded': [], 'used_tokens': 0, 'folded_count': 0}

    keywords = query_keywords or []
    scored = []
    for block in all_blocks:
        b = score_block_with_decay(block, keywords, vault_path)
        scored.append(b)

    scored.sort(key=lambda b: -b['score'])

    kept = []
    folded = []
    used = 0
    kept_ids = []

    for block in scored:
        tokens = block['tokens']
        if used + tokens <= max_tokens:
            block['status'] = 'kept'
            kept.append(block)
            kept_ids.append(block.get('id', ''))
            used += tokens
        else:
            section = block.get('section', '参考')
            content = block.get('content', '')
            one_line = content.split('\n')[0].strip()[:80]
            if len(one_line) > 10:
                folded.append({
                    'page_title': block.get('page_title', ''),
                    'section': section,
                    'summary': one_line,
                    'score': block['score'],
                    'tokens': tokens,
                    'decay': block.get('decay', 1.0),
                    'score_before_decay': block.get('score_before_decay', block['score']),
                })

    # Record injections for future decay
    record_injection(kept_ids, vault_path)

    kept.sort(key=lambda b: (b['page_title'],
        {'title': 0, chr(25688)+chr(35201): 1}.get(b['section'], 99)))

    return {'kept': kept, 'folded': folded, 'used_tokens': used, 'folded_count': len(folded)}
