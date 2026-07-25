# -*- coding: utf-8 -*-
"""
灵台MCP - 感知模块（工具层）
============================
只提供工具，不包含检测逻辑。
检测逻辑由 IDENTITY.md 规则驱动，AI 自己判断。

工具：
- inject: 注入相关知识
- save: 保存新知识（触发观察引擎）
- recommend: 推荐相关页面
- context: 生成上下文
"""

import os, time, sys
import json
import re
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional, Any

# 添加当前目录到路径
sys.path.insert(0, str(Path(__file__).resolve().parent))

from memory_engine import MemoryEngine
from content_registry import ContentRegistry
from tokenjuice import (
    smart_truncate,
    compress_page_simple,
    allocate_inject_budget,
    estimate_tokens as tj_estimate_tokens,
    reset_session_cache,
    extract_blocks,
    select_top_blocks_dynamic,
    decay_factor,
)
from summary_tree import SummaryTreeIndex
from observation_engine import ObservationEngine
from logger import get_logger

log = get_logger(__name__)


# 默认最大注入 token（约 1 个结果 ≈ 150 tokens）
DEFAULT_INJECT_MAX_TOKENS = 800


def _estimate_tokens(text: str) -> int:
    """
    精确估算文本 token 数（差距③）
    
    基于典型 LLM tokenizer 的经验公式：
    - 中文字符：1.3 字/token（比旧版 1.5 更精确）
    - 英文单词：3.8 字母/token
    - 数字/符号：2 字/token
    - 标点/空格：1 token/个
    - 加 5 个 overhead token（JSON 包装等）
    """
    if not text:
        return 0
    total = 0
    # 中文
    cjk = re.findall(r'[\u4e00-\u9fff]', text)
    total += len(cjk) / 1.3
    # ASCII 字母词
    words = re.findall(r'[a-zA-Z]+', text)
    for w in words:
        total += len(w) / 3.8
    # 数字
    nums = re.findall(r'[0-9]+', text)
    for n in nums:
        total += len(n) / 2.0
    # 剩余字符（标点、空格等）
    other = len(text) - len(cjk) - sum(len(w) for w in words) - sum(len(n) for n in nums)
    total += other * 0.8
    return int(total + 5)


INJECT_PRIORITY_KEYWORDS = {
    # core：身份/边界/长期任务
    'core': ['称呼', '叫我', '关系', '边界', '禁区', '不要提',
             '长期', '总是', '固定', '一定要', '永远',
             '我是', '我的身份', '职责'],
    # preference：偏好/习惯
    'preference': ['喜欢', '偏好', '习惯', '常用', '倾向', '更爱',
                   '最好', '希望', '想要', '讨厌', '不喜欢'],
}


def _classify_inject_priority(title: str, summary: str, domain: str) -> str:
    """
    判断注入优先级：core > preference > context
    根据标题/摘要/域中的关键词判断
    """
    text = (title + ' ' + summary + ' ' + domain).lower()
    for kw in INJECT_PRIORITY_KEYWORDS['core']:
        if kw in text:
            return 'core'
    for kw in INJECT_PRIORITY_KEYWORDS['preference']:
        if kw in text:
            return 'preference'
    return 'context'


class PerceptionTools:
    """灵台灵识感知工具"""
    
    def __init__(self, vault_path: str = None, registry=None):
        """
        初始化
        
        Args:
            vault_path: 灵台vault路径
            registry: 共享内容注册表（单例），不传则自建
        """
        if vault_path is None:
            self.vault_path = r"."
        else:
            self.vault_path = vault_path
        
        self.memory = MemoryEngine(self.vault_path)
        self.registry = registry if registry is not None else ContentRegistry(self.vault_path)
        self.raw_dir = Path(self.vault_path) / "原料"
        self.danfang_dir = Path(self.vault_path) / "丹房"
        # Token 优化：进程生命周期内只注入一次全量 registry_context
        self._inject_registry_shown = False
        # 分层摘要树（延迟初始化）
        self._summary_tree = None
    
    def _get_summary_tree(self):
        """延迟获取摘要树实例"""
        if self._summary_tree is None:
            self._summary_tree = SummaryTreeIndex(self.vault_path)
        return self._summary_tree

    def _get_observation_engine(self):
        """延迟获取观察引擎实例"""
        if not hasattr(self, '_observation_engine') or self._observation_engine is None:
            self._observation_engine = ObservationEngine(self.vault_path, self.registry)
        return self._observation_engine
    
    def inject(self, keyword: str, max_tokens: int = None) -> dict:
        """
        注入相关知识（AI调用）—— v5 增强版
        
        新增特性（借鉴 Polaris memoryEngine.ts + requestMemoryPlan.ts）：
        - 锚点感知评分（anchor > keyword > ngram）
        - 优先级分级（core > preference > context）
        - 预算感知注入（max_tokens 裁剪，标记 dropped_budget）
        - 证据链（每个结果附上匹配原因）
        
        Args:
            keyword: 搜索关键词
            max_tokens: 最大注入 token 数（默认 800，设为 None 不限）
        
        Returns:
            dict: 带注入计划的结果
        """
        if max_tokens is None:
            max_tokens = DEFAULT_INJECT_MAX_TOKENS
        
        # 使用带锚点评分的增强查询
        query_result = self.memory.query_with_scoring(keyword)
        results = query_result.get("results", [])
        
        # 提取本次查询的锚点（返回给 agent 作证据链）
        query_anchors = query_result.get('anchors', [])
        
        # 查内容注册表
        registry_context = None
        if not self._inject_registry_shown:
            try:
                reg_stats = self.registry.stats()
                reg_related = 0
                for key, entry in self.registry.dump().get("entries", {}).items():
                    if keyword.lower() in entry.get("type", "").lower():
                        reg_related += 1
                registry_context = {
                    "total_entries": reg_stats.get("total_entries", 0),
                    "total_appearances": reg_stats.get("total_appearances", 0),
                    "by_type": reg_stats.get("by_type", {}),
                    "related_entries": reg_related,
                }
                self._inject_registry_shown = True
            except Exception:
                pass
        
        if not results:
            base_result = {"found": False, "match_type": query_result.get("match_type", "none")}
            if registry_context:
                base_result["registry_context"] = registry_context
            return base_result
        
        # 构建注入候选 —— v7 块级别（Memory Tree 风格）
        # 每页拆为带评分的块，从全局块池子选 Top N
        query_keywords = [keyword] + [a['term'] for a in query_anchors]
        
        # 从所有搜索结果提取块
        all_blocks = []
        block_sources = {}  # page_path -> source info
        for r in results:
            page_path = r.get('path', '')
            page_title = r.get('title', '')
            domain = r.get('domain', '')
            pinji = r.get('pinji', '')
            score = r.get('score', 0)
            
            # 尝试用 summary_tree 的块提取（更精确）
            st = self._get_summary_tree()
            # Normalize path: search returns without .md, cache stores with .md
            cache_path = page_path + '.md' if not page_path.endswith('.md') else page_path
            tree_entry = st._tree.get(cache_path, {})
            full_content = tree_entry.get('full', '')
            
            if full_content:
                # 从缓存页面全文提取块
                blocks = extract_blocks(full_content, page_title, domain, pinji)
            else:
                # 没有缓存全文，用搜索摘要作为单个块
                summary = r.get('summary', '')[:200]
                if not summary:
                    continue
                blocks = [{
                    'id': page_title + ':summary',
                    'section': '摘要',
                    'content': summary,
                    'score': 10 + (3 if pinji == '上品' else 0),
                    'tokens': tj_estimate_tokens(summary),
                    'page_title': page_title,
                    'domain': domain,
                    'pinji': pinji,
                }]
            
            for b in blocks:
                b['source_score'] = score  # 保留搜索评分
            all_blocks.extend(blocks)
            block_sources[page_path] = {
                'title': page_title,
                'pinji': pinji,
                'domain': domain,
                'block_count': len(blocks),
            }
        
        if not all_blocks:
            base_result = {"found": False}
            if registry_context:
                base_result['registry_context'] = registry_context
            return base_result
        
        # 从块池子选 Top N（v8: folding + dynamic scoring）
        vault_path = getattr(self, 'vault_path', None)
        result = select_top_blocks_dynamic(all_blocks, max_tokens, query_keywords, vault_path)
        kept = result['kept']
        folded = result['folded']
        
        # 构建 inject 返回格式
        candidates = []
        for b in kept:
            candidates.append({
                'path': '',
                'title': b['page_title'],
                'summary': b['content'],
                'section': b['section'],
                'pinji': b['pinji'],
                'domain': b['domain'],
                'score': b['score'],
                'inject_priority': 'core' if b['score'] >= 40 else 'preference' if b['score'] >= 20 else 'context',
                'estimated_tokens': b['tokens'],
                'status': b['status'],
                'query_match': b.get('query_match', 0),
                'evidence': {
                    'block_id': b['id'],
                    'score': b['score'],
                    'decay': b.get('decay', 1.0),
                    'score_before_decay': b.get('score_before_decay', b['score']),
                },
            })
        
        # 统计
        total_original_tokens = sum(b['tokens'] for b in all_blocks)
        total_used_tokens = result['used_tokens']
        savings_pct = round((1 - total_used_tokens / max(1, total_original_tokens)) * 100, 1)
        
        # 折叠块摘要
        folded_summary = ''
        if folded:
            by_page = {}
            for f in folded:
                by_page.setdefault(f['page_title'], []).append(f)
            lines = []
            for page, folds in by_page.items():
                items = '; '.join(f"{f['section']}: {f['summary'][:30]}..." for f in folds[:3])
                lines.append(f"  {page}: {items}")
            folded_summary = '\n'.join(lines)
        
        evidence_summary = {
            'query_anchors': [a['term'] for a in query_anchors],
            'method': 'block_level_v8_fold_dynamic',
            'total_pages': len(results),
            'total_blocks': len(all_blocks),
            'total_sources': len(block_sources),
            'kept_blocks': len(kept),
            'folded_blocks': result['folded_count'],
            'budget_max': max_tokens,
            'budget_used': total_used_tokens,
            'compression': {
                'method': 'block_level+fold',
                'total_original_tokens': total_original_tokens,
                'total_compressed_tokens': total_used_tokens,
                'folded_count': result['folded_count'],
                'savings_pct': savings_pct,
            },
            'sources': block_sources,
            'folded_summary': folded_summary,
            'decay_stats': {
                'total_blocks_scored': len(all_blocks),
                'with_decay_data': sum(1 for b in all_blocks if decay_factor(b.get('id', ''), vault_path) < 1.0),
            },
        }
        
        result = {
            'found': True,
            'match_type': query_result.get('match_type', 'exact'),
            'evidence': evidence_summary,
            'results': candidates,
        }
        if registry_context:
            result['registry_context'] = registry_context
        return result
    
    def save(self, content: str, category: str = "", source: str = "对话") -> dict:
        """
        保存新知识到原料目录（AI调用）
        
        Args:
            content: 知识内容
            category: 分类（可选）
            source: 来源（默认：对话）
        
        Returns:
            dict: 保存结果
        """
        # === 注册表 O(1) 查重：内容是否已在灵台出现过 ===
        try:
            existing = self.registry.lookup(content)
            if existing:
                return {
                    "success": True,
                    "dup": True,
                    "method": "registry_sha256",
                    "confidence": 1.0,
                    "match": existing["locations"][0] if existing["locations"] else "unknown",
                    "appearances": existing["appearances"],
                    "message": f"内容已在灵台出现 {existing['appearances']} 次（注册表命中），跳过重复写入",
                }
        except Exception:
            pass  # 注册表异常不阻塞保存流程
        
        # 生成文件名
        now = datetime.now()
        date_str = now.strftime("%Y%m%d-%H%M%S")
        
        # 清理内容作为文件名（台律：文件名禁止弯/直引号）
        title = content[:30].replace("\n", " ").strip()
        title = re.sub(r'[<>:"/\\|?*\u201c\u201d\u0022#]', '', title)  # 移除非法字符 + 引号 + #
        title = re.sub(r'^[#\s]+', '', title).strip()  # 去掉开头的 Markdown 标题标记
        
        filename = f"{title}-{date_str}.md"
        filepath = self.raw_dir / filename
        
        # 自动推断域（通过 query 命中页面的 domain）
        domain = ""
        keyword = content[:50].replace("\n", " ").strip()[:30]
        try:
            qr = self.memory.query(keyword)
            results = qr.get("results", []) if isinstance(qr, dict) else qr
            if results:
                domain = results[0].get("domain", "")
        except Exception:
            pass

        # 生成frontmatter
        # 原料分级（规则判断，不调LLM）
        level = "正常"
        try:
            from raw_classifier import classify as _classify
            level = _classify(content, category)
        except Exception:
            pass
        
        fm = f"""---
处理状态: 待提炼
来源: {source}
处理日期: {now.strftime('%Y-%m-%d')}
提炼分级: {level}
"""
        if domain:
            fm += f"域: {domain}\n"
        fm += "---\n"
        
        # 写入文件
        try:
            self.raw_dir.mkdir(parents=True, exist_ok=True)
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(fm + "\n" + content)
            
            # 注册到内容注册表
            rel_path = str(filepath.relative_to(Path(self.vault_path))).replace("\\", "/")
            self.registry.register(content, location=rel_path, module="perception", content_type="raw_material")
            
            # 生成观察反馈消息
            obs_result = None
            feedback = None
            try:
                obs_result = self._get_observation_engine().on_save(content, category=category, source=source)
            except Exception:
                log.debug("observation on_save suppressed", exc_info=True)
            
            if obs_result and obs_result.get("action") == "created":
                feedback = f"灵识观察：已归纳新观察「{obs_result.get('topic', '')}」（置信度: {obs_result.get('confidence', 0):.0%}）"
            elif obs_result and obs_result.get("action") == "updated":
                feedback = f"灵识观察：已更新观察「{obs_result.get('topic', '')}」（现有{obs_result.get('facts_count', 0)}条事实）"
            elif obs_result and obs_result.get("action") == "accumulating":
                facts_count = obs_result.get('facts_count', 0)
                threshold = obs_result.get('threshold', 3)
                if facts_count >= threshold - 1:  # 接近阈值时提示
                    feedback = f"灵识观察：「{obs_result.get('topic', '')}」已有{facts_count}条事实，即将归纳（需{threshold}条）"
            
            return {
                "success": True,
                "path": str(filepath.relative_to(Path(self.vault_path))),
                "filename": filename,
                "observation": obs_result,
                "observation_feedback": feedback,
            }
        except Exception as e:
            return {
                "success": False,
                "error": str(e),
            }
    
    def recommend(self, current_topic: str, max_results: int = 5) -> dict:
        """
        推荐相关页面（AI调用）
        
        Args:
            current_topic: 当前话题
            max_results: 最大结果数
        
        Returns:
            dict: 推荐结果（按品级降序）
        """
        # 提取关键词
        keywords = self._extract_keywords(current_topic)
        
        # 搜索相关页面
        all_results = []
        for keyword in keywords[:3]:
            query_result = self.memory.query(keyword)
            # memory.query() 返回 dict，需要取 "results" 字段
            results = query_result.get("results", [])
            all_results.extend(results)
        
        # 去重
        seen_paths = set()
        unique_results = []
        for r in all_results:
            if r["path"] not in seen_paths:
                seen_paths.add(r["path"])
                unique_results.append(r)
        
        if not unique_results:
            return {"found": False}
        
        # 按品级排序（台律：上品优先）
        pinji_order = {"上品": 0, "中品": 1, "下品": 2, "": 3}
        unique_results.sort(key=lambda r: pinji_order.get(r.get("pinji", ""), 3))
        
        return {
            "found": True,
            "recommendations": [
                {
                    "path": r["path"],
                    "title": r["title"],
                    "summary": r.get("summary", "")[:100],
                    "pinji": r.get("pinji", ""),  # 台律：附带品级标签
                }
                for r in unique_results[:max_results]
            ],
        }
    
    def context(self) -> dict:
        """
        生成会话上下文（AI调用）
        融合 hook-session-greeting 和 hook-pre-compact-summary 功能
        
        Returns:
            dict: 上下文摘要
        """
        import os
        from datetime import datetime, timedelta
        
        stats = self.memory.get_stats()
        
        # 1. 知识库概览（原有功能）
        overview = {
            "total_pages": stats["total_pages"],
            "total_links": stats["total_links"],
            "domains": stats["by_domain"],
        }
        
        # 2. 待办概要（来自 hook-session-greeting）— 带 TTL 缓存（30s）
        # 避免每次 context_load 都扫描 1264 原料文件
        pending_count = getattr(self, '_pending_count_cached', None)
        pending_ts = getattr(self, '_pending_cache_ts', 0)
        if pending_count is None or (time.time() - pending_ts) > 30:
            pending_dir = Path(self.vault_path) / "原料"
            pending_count = 0
            if pending_dir.exists():
                for f in pending_dir.rglob("*.md"):
                    try:
                        content = f.read_text(encoding="utf-8")
                        if "处理状态: 待提炼" in content or "状态: 待提炼" in content:
                            pending_count += 1
                    except:
                        pass
            self._pending_count_cached = pending_count
            self._pending_cache_ts = time.time()
        
        # 3. 最近更新（来自 hook-session-greeting）
        recent_pages = sorted(
            self.memory.pages,
            key=lambda p: p.get("date", ""),
            reverse=True
        )[:5]
        
        # 4. 核心页面（高入链）
        hub_pages = sorted(
            self.memory.pages,
            key=lambda p: len(p.get("linked_from", [])),
            reverse=True
        )[:5]
        
        # 5. 生成问候语（来自 hook-session-greeting）
        now = datetime.now()
        hour = now.hour
        if hour < 6:
            period = "凌晨"
        elif hour < 9:
            period = "早上"
        elif hour < 12:
            period = "上午"
        elif hour < 14:
            period = "中午"
        elif hour < 18:
            period = "下午"
        elif hour < 20:
            period = "傍晚"
        else:
            period = "晚上"
        weekday = ['星期一','星期二','星期三','星期四','星期五','星期六','星期日'][now.weekday()]
        greeting = f"今天是 {now.strftime('%Y年%m月%d日')}，{weekday}，{period}好。"
        
        return {
            "greeting": greeting,
            "overview": overview,
            "pending_count": pending_count,
            "recent_pages": [
                {"path": p["path"], "title": p["title"], "date": p.get("date", "")}
                for p in recent_pages
            ],
            "hub_pages": [
                {"path": p["path"], "title": p["title"], "backlinks": len(p.get("linked_from", []))}
                for p in hub_pages
            ],
            "message": f"知识库有 {stats['total_pages']} 个页面，{pending_count} 篇待提炼原料。" if pending_count > 0 else f"知识库有 {stats['total_pages']} 个页面，无待提炼原料。",
        }
    
    def _extract_keywords(self, text: str) -> List[str]:
        """提取关键词"""
        keywords = []
        
        # 中文关键词（2-4字）
        chinese_words = re.findall(r'[\u4e00-\u9fff]{2,4}', text)
        keywords.extend(chinese_words[:10])
        
        # 英文关键词
        english_words = re.findall(r'[a-zA-Z]{3,}', text)
        keywords.extend([w.lower() for w in english_words[:5]])
        
        # 去重
        seen = set()
        unique_keywords = []
        for kw in keywords:
            if kw not in seen:
                seen.add(kw)
                unique_keywords.append(kw)
        
        return unique_keywords[:10]


# 便捷函数
def create_perception_tools(vault_path: str = None) -> PerceptionTools:
    """创建感知工具实例"""
    return PerceptionTools(vault_path)
