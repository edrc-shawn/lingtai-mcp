# -*- coding: utf-8 -*-
"""
灵台MCP - 主动反思引擎（Reflect Engine）
===========================================
基于 Hindsight 设计，定期主动回顾知识库。

六检完备：
1. 知识缺口    - 原料有但丹房无 ✅
2. 断裂关联    - 语义相似但无双向链接 ✅（依赖 Hebbian 权重）
3. 过时内容    - 长期未更新 ✅
4. 话题偏移    - 近期关注方向变化 ✅
5. 模式涌现    - 多条原料指向同一未提炼主题 ✅
6. 图谱缺口    - Obsidian 图谱不可见/断连 ✅（无行内标签/无wikilink/无外向链接）
"""

import os
import json
import re
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, asdict
from collections import Counter
from itertools import combinations
from logger import get_logger

log = get_logger(__name__)


@dataclass
class Finding:
    """检查发现"""
    type: str
    topic: str
    severity: float  # 0-1
    detail: str
    suggestion: str
    
    def to_dict(self) -> dict:
        return asdict(self)


class ReflectEngine:
    """主动反思引擎"""
    
    def __init__(self, vault_path: str = None):
        if vault_path is None:
            # 优先环境变量，避免调用方 cwd 不在 vault 根时产生假零结果
            self.vault_path = os.environ.get("LINGTAI_VAULT", r".")
        else:
            self.vault_path = vault_path
        
        self.丹房 = Path(self.vault_path) / "丹房"
        self.原料 = Path(self.vault_path) / "原料"
        self.report_path = Path(self.vault_path) / "体检" / "reflect_report.md"
        
        # 防假零：丹房目录不存在说明 vault_path 错误，扫描结果必为 0，显式告警
        if not self.丹房.exists():
            log.warning(f"ReflectEngine: 丹房目录不存在（vault_path={self.vault_path}），"
                        f"所有扫描将返回 0（假零）。请传入正确 vault_path 或设置 LINGTAI_VAULT 环境变量。")
        
        # 尝试加载 Hebbian 权重（用于断裂关联检测）
        self._hebbian = None
        try:
            from hebbian_weights import HebbianWeights
            self._hebbian = HebbianWeights(vault_path)
        except ImportError:
            pass
    
    def reflect(self, depth: str = "standard") -> dict:
        """
        全量反思（五检齐全）
        
        Args:
            depth: 深度（quick/standard/deep）
        
        Returns:
            dict: 反思报告
        """
        findings = []
        
        # 执行六项检查
        findings.extend(self._check_knowledge_gaps())
        findings.extend(self._check_broken_links())
        findings.extend(self._check_stale_content())
        findings.extend(self._check_topic_shift())
        findings.extend(self._check_emerging_patterns())
        findings.extend(self._check_graph_gaps())
        
        # 按严重度排序
        findings.sort(key=lambda f: f.severity, reverse=True)
        
        # 生成报告
        report = {
            "timestamp": datetime.now().isoformat(),
            "depth": depth,
            "findings": [f.to_dict() for f in findings],
            "total_findings": len(findings),
            "check_stats": {
                "knowledge_gaps": sum(1 for f in findings if f.type == "knowledge_gap"),
                "broken_links": sum(1 for f in findings if f.type == "broken_link"),
                "stale_content": sum(1 for f in findings if f.type == "stale_content"),
                "topic_shift": sum(1 for f in findings if f.type == "topic_shift"),
                "emerging_pattern": sum(1 for f in findings if f.type == "emerging_pattern"),
                "graph_gaps": sum(1 for f in findings if f.type == "graph_gap"),
            },
            "high_severity": sum(1 for f in findings if f.severity >= 0.7),
            "medium_severity": sum(1 for f in findings if 0.4 <= f.severity < 0.7),
            "low_severity": sum(1 for f in findings if f.severity < 0.4),
        }
        
        return report
    
    # ─── 检查1：知识缺口 ───
    
    def _check_knowledge_gaps(self) -> List[Finding]:
        """检查知识缺口：原料中有但丹房中未提炼的内容"""
        findings = []
        if not self.原料.exists():
            return findings
        
        # 获取丹房所有页面标题（用于判断是否已提炼）
        danfang_titles = set()
        if self.丹房.exists():
            for f in self.丹房.rglob("*.md"):
                danfang_titles.add(f.stem.lower().replace("-", "").replace(" ", ""))
        
        for raw_file in sorted(self.原料.glob("*.md")):
            try:
                content = raw_file.read_text(encoding="utf-8")
                if "处理状态: 待提炼" in content:
                    title_match = re.search(r'^#\s+(.+)$', content, re.MULTILINE)
                    title = title_match.group(1) if title_match else raw_file.stem
                    title_clean = title.lower().replace("-", "").replace(" ", "")
                    
                    # 跳过已提炼的
                    if title_clean in danfang_titles:
                        continue
                    
                    findings.append(Finding(
                        type="knowledge_gap",
                        topic=title[:40],
                        severity=0.6,
                        detail=f"原料「{title[:40]}」待提炼，丹房无对应条目",
                        suggestion="将原料提炼到丹房对应域"
                    ))
            except:
                pass
        
        return findings[:10]
    
    # ─── 检查2：断裂关联 ───
    
    def _extract_wikilinks(self, content: str) -> set:
        """从页面中提取 [[wikilink]] 列表"""
        return set(re.findall(r'\[\[([^\]]+?)(?:\|[^\]]+)?\]\]', content))
    
    def _extract_keywords(self, content: str) -> set:
        """从页面标题/首段提取关键词（用于语义相似度估算）"""
        words = set()
        # 取标题行
        title_m = re.search(r'^#\s+(.+)$', content, re.MULTILINE)
        if title_m:
            title = title_m.group(1)
            # 提取2-4字中文词
            words.update(re.findall(r'[\u4e00-\u9fff]{2,4}', title))
        # 取首段（前200字）
        body = content[:200]
        words.update(re.findall(r'[\u4e00-\u9fff]{2,4}', body))
        return words
    
    def _check_broken_links(self) -> List[Finding]:
        """检查断裂关联：语义相似但缺少双向链接的页面对"""
        findings = []
        if not self.丹房.exists():
            return findings
        
        # 收集所有页面信息
        pages = {}  # {stem: {"links": set, "keywords": set, "path": Path}}
        for f in self.丹房.rglob("*.md"):
            try:
                content = f.read_text(encoding="utf-8")
                pages[f.stem] = {
                    "links": self._extract_wikilinks(content),
                    "keywords": self._extract_keywords(content),
                    "path": f,
                    "title": re.search(r'^#\s+(.+)$', content, re.MULTILINE).group(1) if re.search(r'^#\s+(.+)$', content, re.MULTILINE) else f.stem,
                }
            except:
                pass
        
        # 如果 Hebbian 可用，基于共现找断裂关联
        co_occur = []
        if self._hebbian:
            try:
                # 获取高共现页面对
                co_occur = self._hebbian.get_top_co_occurrences(top_n=30)
                for page_a, page_b, weight in co_occur:
                    if page_a in pages and page_b in pages:
                        a_links = pages[page_a]["links"]
                        b_links = pages[page_b]["links"]
                        # 检查是否缺少双向链接
                        has_ab = page_b in a_links
                        has_ba = page_a in b_links
                        if not (has_ab and has_ba):
                            direction = "无双向" if not (has_ab or has_ba) else ("单向" if has_ab or has_ba else "无")
                            findings.append(Finding(
                                type="broken_link",
                                topic=f"{pages[page_a]['title'][:20]} ↔ {pages[page_b]['title'][:20]}",
                                severity=min(0.8, 0.4 + weight * 0.4),
                                detail=f"共现权重 {weight:.2f}，{direction}链接",
                                suggestion=f"在相关页面间添加 [[{page_a}]] 和 [[{page_b}]] 双向链接"
                            ))
            except Exception:
                log.debug("suppressed", exc_info=True)
        
        # 基于关键词重叠的兜底检测
        stem_list = list(pages.keys())
        if len(stem_list) >= 2:
            checked_pairs = set()
            for a, b in combinations(stem_list, 2):
                pair_key = tuple(sorted([a, b]))
                if pair_key in checked_pairs:
                    continue
                checked_pairs.add(pair_key)
                
                # 关键词重叠度
                kw_a = pages[a]["keywords"]
                kw_b = pages[b]["keywords"]
                if len(kw_a) > 1 and len(kw_b) > 1:
                    overlap = kw_a & kw_b
                    if len(overlap) >= 2:  # ≥2个同词 = 潜在关联
                        a_links = pages[a]["links"]
                        b_links = pages[b]["links"]
                        has_ab = b in a_links
                        has_ba = a in b_links
                        if not has_ab and not has_ba:
                            # 避免与 Hebbian 结果重复
                            if not self._hebbian or (a, b) not in [(x[0], x[1]) for x in co_occur]:
                                findings.append(Finding(
                                    type="broken_link",
                                    topic=f"{pages[a]['title'][:20]} ↔ {pages[b]['title'][:20]}",
                                    severity=0.4,
                                    detail=f"共享关键词 {len(overlap)} 个（{', '.join(list(overlap)[:3])}），但无链接",
                                    suggestion=f"考虑添加双向链接"
                                ))
        
        return findings[:10]
    
    # ─── 检查2.5：Obsidian 图谱缺口 ───
    
    # 图谱检查白名单：这些目录/文件的页面不需要图谱可见
    GRAPH_EXCLUDE_DIRS = {".meta"}
    GRAPH_EXCLUDE_FILES = {"日志.md"}
    
    def _is_graph_page(self, path: Path) -> bool:
        """判断文件是否为需要图谱可见的内容页"""
        rel = str(path.relative_to(self.丹房)).replace(os.sep, '/')
        for d in self.GRAPH_EXCLUDE_DIRS:
            if f"/{d}/" in rel or rel.startswith(f"{d}/"):
                return False
        if path.name in self.GRAPH_EXCLUDE_FILES:
            return False
        return True
    
    def _check_graph_gaps(self) -> List[Finding]:
        """检查 Obsidian 图谱不可见/断连的丹房页面"""
        findings = []
        if not self.丹房.exists():
            return findings
        
        no_tags = []
        no_links = []
        no_outgoing = []
        
        for f in self.丹房.rglob("*.md"):
            if not self._is_graph_page(f):
                continue
            try:
                content = f.read_text(encoding="utf-8")
            except:
                continue
            
            # 获取标题
            title_match = re.search(r'^#\s+(.+)$', content, re.MULTILINE)
            title = title_match.group(1) if title_match else f.stem
            
            # 检查行内标签
            has_inline_tags = bool(re.search(r'^#\S+(?:\s+#\S+)+', content, re.MULTILINE))
            if not has_inline_tags:
                rel = str(f.relative_to(self.丹房)).replace(os.sep, '/')
                no_tags.append((rel, title))
            
            # 检查 wikilink
            wikilinks = re.findall(r'\[\[([^\]|#]+)', content)
            if not wikilinks:
                rel = str(f.relative_to(self.丹房)).replace(os.sep, '/')
                no_links.append((rel, title))
            else:
                # 检查外向链接（链接到 丹房 域内页面）
                outgoing = [w for w in wikilinks 
                           if any(w.startswith(f'{d:02d}-') for d in range(0, 100))]
                if not outgoing:
                    rel = str(f.relative_to(self.丹房)).replace(os.sep, '/')
                    no_outgoing.append((rel, title))
        
        # 汇总：无行内标签（图谱不可见）
        if no_tags:
            n = len(no_tags)
            findings.append(Finding(
                type="graph_gap",
                topic=f"图谱不可见：{n} 页无行内标签",
                severity=0.5,
                detail=f"这些页只有 frontmatter 标签，Obsidian 知识图谱中不显示节点。含：{', '.join(t for _, t in no_tags[:5])}"
                       + (f" 等" if n > 5 else ""),
                suggestion=f"为 {n} 页补充行内 #标签。前5个：{', '.join(p for p, _ in no_tags[:5])}"
            ))
        
        # 汇总：无 wikilink（完全孤立）
        if no_links:
            n = len(no_links)
            findings.append(Finding(
                type="graph_gap",
                topic=f"图谱孤立：{n} 页无任何 wikilink",
                severity=0.7,
                detail=f"这些页在 Obsidian 图谱中是孤点。含：{', '.join(t for _, t in no_links[:5])}"
                       + (f" 等" if n > 5 else ""),
                suggestion=f"为 {n} 页添加 [[推荐阅读]] 或内联引用。前5个：{', '.join(p for p, _ in no_links[:5])}"
            ))
        
        # 汇总：无外向链接（死胡同）
        if no_outgoing:
            n = len(no_outgoing)
            findings.append(Finding(
                type="graph_gap",
                topic=f"图谱死胡同：{n} 页无外向链接",
                severity=0.3,
                detail=f"这些页被其他页引用，但不引用任何丹房页面——图谱中的死胡同。含：{', '.join(t for _, t in no_outgoing[:5])}"
                       + (f" 等" if n > 5 else ""),
                suggestion=f"为 {n} 页添加 ## 推荐阅读。前5个：{', '.join(p for p, _ in no_outgoing[:5])}"
            ))
        
        return findings
    
    # ─── 检查3：过时内容 ───
    
    def _check_stale_content(self) -> List[Finding]:
        """检查过时内容：长时间未更新/未被引用的页面"""
        findings = []
        if not self.丹房.exists():
            return findings
        
        now = datetime.now()
        stale_days = 90
        
        for f in self.丹房.rglob("*.md"):
            try:
                mtime = datetime.fromtimestamp(f.stat().st_mtime)
                days_since_update = (now - mtime).days
                
                if days_since_update > stale_days:
                    content = f.read_text(encoding="utf-8")
                    title_match = re.search(r'^#\s+(.+)$', content, re.MULTILINE)
                    title = title_match.group(1) if title_match else f.stem
                    
                    # 检查入链数
                    inlink_count = 0
                    for other in self.丹房.rglob("*.md"):
                        if other != f:
                            try:
                                other_content = other.read_text(encoding="utf-8")
                                if f.stem in other_content:
                                    inlink_count += 1
                            except:
                                pass
                    
                    severity = 0.4
                    if inlink_count == 0:
                        severity = 0.7  # 无入链更严重
                        detail = f"已 {days_since_update} 天未更新，且无其他页面引用"
                    elif days_since_update > 180:
                        severity = 0.6
                        detail = f"已 {days_since_update} 天未更新，入链 {inlink_count} 个"
                    else:
                        detail = f"已 {days_since_update} 天未更新，入链 {inlink_count} 个"
                    
                    findings.append(Finding(
                        type="stale_content",
                        topic=title[:40],
                        severity=severity,
                        detail=detail,
                        suggestion="审阅是否仍有效，或归档/删除"
                    ))
            except:
                pass
        
        return findings[:10]
    
    # ─── 检查4：话题偏移 ───
    
    def _check_topic_shift(self) -> List[Finding]:
        """检查话题偏移：近期关注方向是否发生变化"""
        findings = []
        if not self.原料.exists():
            return findings
        
        now = datetime.now()
        recent_cutoff = now - timedelta(days=30)
        old_cutoff = now - timedelta(days=90)
        
        recent_keywords = Counter()
        old_keywords = Counter()
        
        for f in self.原料.glob("*.md"):
            try:
                mtime = datetime.fromtimestamp(f.stat().st_mtime)
                content = f.read_text(encoding="utf-8")
                # 提取关键词（2-4字中文词）
                words = re.findall(r'[\u4e00-\u9fff]{2,4}', content)
                word_set = set(words)
                
                if mtime > recent_cutoff:
                    recent_keywords.update(word_set)
                elif mtime > old_cutoff:
                    old_keywords.update(word_set)
            except:
                pass
        
        # 找近期新增的高频词（旧期没有或很少）
        if recent_keywords and old_keywords:
            recent_top = set(w for w, _ in recent_keywords.most_common(20))
            old_top = set(w for w, _ in old_keywords.most_common(20))
            new_topics = recent_top - old_top
            
            # 过滤掉通用词
            stop_words = {"可以", "这个", "那个", "什么", "如何", "为什么", "没有", "不是", "一个", "我们", "他们", "自己", "可能", "需要", "应该", "就是", "因为", "所以", "但是", "而且", "如果", "虽然"}
            new_topics = new_topics - stop_words
            
            if new_topics:
                top_new = sorted(new_topics, key=lambda w: recent_keywords[w], reverse=True)[:5]
                findings.append(Finding(
                    type="topic_shift",
                    topic="、".join(top_new),
                    severity=0.5,
                    detail=f"近期原料中出现新话题：{', '.join(top_new)}",
                    suggestion="关注这些新方向是否需要建立丹房条目"
                ))
        
        return findings[:5]
    
    # ─── 检查5：模式涌现 ───
    
    def _check_emerging_patterns(self) -> List[Finding]:
        """检查模式涌现：多条原料指向同一未提炼主题"""
        findings = []
        if not self.原料.exists():
            return findings
        
        # 获取丹房页面标题集合（去重用）
        danfang_titles = set()
        if self.丹房.exists():
            for f in self.丹房.rglob("*.md"):
                danfang_titles.add(f.stem.lower().replace("-", "").replace(" ", ""))
        
        # 收集待提炼原料的关键词
        raw_topics = []  # [(title, keywords_set)]
        for raw_file in self.原料.glob("*.md"):
            try:
                content = raw_file.read_text(encoding="utf-8")
                if "处理状态: 待提炼" in content:
                    title_match = re.search(r'^#\s+(.+)$', content, re.MULTILINE)
                    title = title_match.group(1) if title_match else raw_file.stem
                    keywords = set(re.findall(r'[\u4e00-\u9fff]{2,4}', content))
                    # 过滤通用词
                    keywords = keywords - {"可以", "这个", "那个", "什么", "如何", "没有", "不是", "一个", "我们", "他们", "可能", "需要", "应该", "就是", "因为", "所以", "但是", "而且", "如果", "虽然"}
                    if len(keywords) >= 3:
                        raw_topics.append((title, keywords))
            except:
                pass
        
        # 聚类：基于关键词重叠
        clusters = []  # [(core_topic, member_titles, keyword_union)]
        processed = set()
        for i, (ti, ki) in enumerate(raw_topics):
            if i in processed:
                continue
            cluster = [i]
            for j, (tj, kj) in enumerate(raw_topics):
                if i != j and j not in processed:
                    overlap = ki & kj
                    if len(overlap) >= 2:
                        cluster.append(j)
            
            if len(cluster) >= 3:  # ≥3条原料指向同一主题 = 模式涌现
                for idx in cluster:
                    processed.add(idx)
                titles = [raw_topics[idx][0] for idx in cluster]
                all_keywords = set()
                for idx in cluster:
                    all_keywords.update(raw_topics[idx][1])
                
                # 取高频词作为核心主题
                keyword_freq = Counter()
                for idx in cluster:
                    keyword_freq.update(raw_topics[idx][1])
                core_topics = [w for w, _ in keyword_freq.most_common(5)]
                core_topic = " / ".join(core_topics[:3])
                
                # 跳过已提炼的主题
                cluster_key = "".join(core_topics).replace(" ", "").lower()
                if any(dt in cluster_key or cluster_key in dt for dt in danfang_titles):
                    continue
                
                clusters.append((core_topic, titles, all_keywords))
        
        for core_topic, titles, _ in clusters[:5]:
            findings.append(Finding(
                type="emerging_pattern",
                topic=core_topic[:40],
                severity=0.7,
                detail=f"{len(titles)} 条原料指向同一方向：{', '.join(t[:20] for t in titles[:4])}",
                suggestion="考虑新建丹房页面系统提炼此主题"
            ))
        
        return findings[:5]
    
    # ─── 定向反思 ───
    
    def reflect_topic(self, topic: str) -> dict:
        """针对特定主题的定向反思"""
        findings = []
        
        if self.原料.exists():
            for f in self.原料.glob("*.md"):
                try:
                    content = f.read_text(encoding="utf-8")
                    if topic.lower() in content.lower():
                        title_match = re.search(r'^#\s+(.+)$', content, re.MULTILINE)
                        title = title_match.group(1) if title_match else f.stem
                        findings.append(Finding(
                            type="topic_related",
                            topic=title[:40],
                            severity=0.5,
                            detail=f"原料中有关于「{topic}」的内容",
                            suggestion="考虑提炼到丹房"
                        ))
                except:
                    pass
        
        return {
            "topic": topic,
            "findings": [f.to_dict() for f in findings[:10]],
            "total": len(findings),
        }


# 便捷函数
def create_reflect_engine(vault_path: str = None) -> ReflectEngine:
    return ReflectEngine(vault_path)