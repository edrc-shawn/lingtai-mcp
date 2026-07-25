# -*- coding: utf-8 -*-
"""
召回引擎（差距②：4 种召回候选）
================================
借鉴 Polaris requestSemanticRecallPlan.ts 理念。

从丹房知识库中生成 4 种类型的召回候选：
1. exact_match: 精确匹配（原 query_with_scoring 结果）
2. anchor_match: 锚点匹配（权重≥3 的锚点命中）
3. graph_diffusion: 图扩散（跨域关联）
4. recent_tail: 近期更新的页面

每种候选带 score/match_kind/label/evidence 信息。
"""

import re
from datetime import datetime
from typing import Dict, List, Optional


def user_domain_boost(candidates: list, active_domains: list = None) -> list:
    """
    神经调质乘数：用户活跃域内的候选评分 ×1.1（+10%）。
    零 LLM 成本——纯启发式规则，从 user_profile 获取活跃域即可。
    """
    if not active_domains:
        return candidates
    
    boosted = []
    for c in candidates:
        path = c.get('path', '')
        domain = ''
        parts = path.split('/')
        for part in parts:
            if re.match(r'^\d{2}-', part):
                domain = part
                break
        
        if domain in active_domains:
            c = dict(c)
            c['score'] = round(c['score'] * 1.1, 2)
            c['evidence'] = dict(c.get('evidence', {}))
            c['evidence']['user_domain_boost'] = domain
        boosted.append(c)
    
    return boosted


def compute_retention_score(memory_engine, candidate: dict) -> float:
    """
    统一保留评分（人脑 FSFM 遗忘曲线的工程化版本）。
    
    将分散的因子整合为单一 retention_score，用于最终排序：
      retention = base_score × (1 + backlinks_bonus + pinji_bonus)
    
    零 LLM 成本——纯启发式规则。
    """
    base = candidate.get('score', 0)
    path = candidate.get('path', '')
    page = memory_engine.get_page_by_path(path) if path else None
    if not page:
        return base
    
    # 入链奖励：每 10 个入链 +0.1，上限 0.3
    backlinks = len(page.get('linked_from', []))
    backlinks_bonus = min(backlinks / 20.0, 0.3)
    
    # 品级奖励：上品 +0.2，中品 +0.1
    pinji = page.get('pinji', '')
    pinji_bonus = 0.2 if pinji == '上品' else (0.1 if pinji == '中品' else 0.0)
    
    retention = round(base * (1 + backlinks_bonus + pinji_bonus), 2)
    return retention


def build_recall_candidates(memory_engine, keyword: str, max_total: int = 10,
                           active_domains: list = None) -> dict:
    """
    生成多类型召回候选（Polaris 风格）
    
    Returns:
        dict: {
            candidates: [{kind, label, score, match_kind, path, title, evidence}, ...],
            strategies: {exact_match: N, anchor_match: N, graph_diffusion: N, recent_tail: N},
        }
    """
    candidates = []
    seen_paths = set()

    # 1. exact_match: 锚点评分查询
    scored = memory_engine.query_with_scoring(keyword, max_results=5)
    for r in scored.get('results', []):
        p = r.get('path', '')
        if p not in seen_paths:
            seen_paths.add(p)
            candidates.append({
                'kind': 'exact_match',
                'path': p,
                'title': r.get('title', ''),
                'score': r.get('score', 0),
                'match_kind': r.get('match_kind', 'keyword'),
                'evidence': {
                    'matched_anchors': r.get('matched_anchors', []),
                    'matched_keywords': r.get('matched_keywords', []),
                },
            })

    # 2. anchor_match: 锚点匹配但未被精确命中
    anchors = memory_engine.extract_anchors(keyword)
    strong_anchors = [a for a in anchors if a['weight'] >= 3]
    for anchor in strong_anchors[:3]:
        for page in memory_engine.pages:
            p = page.get('path', '')
            if p in seen_paths:
                continue
            text = (page.get('title', '') + ' ' + page.get('summary', '')).lower()
            if anchor['term'] in text:
                seen_paths.add(p)
                candidates.append({
                    'kind': 'anchor_match',
                    'path': p,
                    'title': page.get('title', ''),
                    'score': anchor['weight'],
                    'match_kind': 'anchor',
                    'evidence': {'matched_anchors': [anchor['term']], 'anchor_weight': anchor['weight']},
                })

    # 3. graph_diffusion: 从已命中页面出发图扩散
    start_paths = [c['path'] for c in candidates[:3]]
    for start in start_paths:
        page = memory_engine.get_page_by_path(start)
        if not page:
            continue
        related = memory_engine.get_related_pages(start, max_results=5)
        for rel in related:
            p = rel.get('path', '')
            if p not in seen_paths:
                seen_paths.add(p)
                candidates.append({
                    'kind': 'graph_diffusion',
                    'path': p,
                    'title': rel.get('title', ''),
                    'score': len(rel.get('linked_from', [])) * 0.5,
                    'match_kind': 'graph',
                    'evidence': {'from': start, 'backlinks': len(rel.get('linked_from', []))},
                })

    # 4. recent_tail: 近期更新的高入链页面
    now = datetime.now()
    pages_sorted = sorted(memory_engine.pages, key=lambda p: p.get('date', ''), reverse=True)[:10]
    for page in pages_sorted:
        p = page.get('path', '')
        if p in seen_paths:
            continue
        date_str = page.get('date', '')
        if date_str:
            try:
                page_date = datetime.strptime(date_str, '%Y-%m-%d')
                days_old = (now - page_date).days
                if days_old > 30:
                    continue
            except ValueError:
                continue
        backlinks = len(page.get('linked_from', []))
        if backlinks < 2:
            continue
        seen_paths.add(p)
        candidates.append({
            'kind': 'recent_tail',
            'path': p,
            'title': page.get('title', ''),
            'score': backlinks * 0.3,
            'match_kind': 'recent',
            'evidence': {'days_old': days_old, 'backlinks': backlinks},
        })

    # 神经调质乘数：用户活跃域评分提升（零 LLM 成本）
    if active_domains:
        candidates = user_domain_boost(candidates, active_domains)

    # 统一保留评分：整合入链、品级因子（人脑 FSFM 工程化，零 LLM 成本）
    for c in candidates:
        c['retention_score'] = compute_retention_score(memory_engine, c)

    # 排序：按保留评分降序（原 score 保留在 evidence 中备查）
    candidates.sort(key=lambda c: -c.get('retention_score', c['score']))

    # 统计各策略
    strategies = {}
    for c in candidates:
        strategies[c['kind']] = strategies.get(c['kind'], 0) + 1

    return {
        'candidates': candidates[:max_total],
        'strategies': strategies,
        'total_candidates': len(candidates),
    }


if __name__ == '__main__':
    import sys, os
    sys.path.insert(0, os.path.dirname(__file__))
    from memory_engine import MemoryEngine
    engine = MemoryEngine()
    result = build_recall_candidates(engine, '灵台')
    print(f"strategies: {result['strategies']}")
    for c in result['candidates'][:5]:
        print(f"  [{c['kind']}] {c['title']} score={c['score']}")