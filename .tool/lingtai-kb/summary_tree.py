# -*- coding: utf-8 -*-
"""
灵识 分层摘要树
===============
OpenHuman Memory Tree 启发：知识页预计算多级摘要，
注入时按 token 预算选级，避免硬截断。

五级摘要（第 5 级由 LLM 补充）：
  tldr   (~50 tokens)  标题 + 一句话
  brief  (~200 tokens)  摘要 + 要点
  normal (~500 tokens)  完整摘要 + 核心章节
  full   (不限)         全文
#   llm    (不限)         LLM 生成摘要（每日检批量补充）

存储：.cache/summary_tree.json（增量更新，页变才重新计算）
"""

import json
import re
import os
from logger import get_logger

log = get_logger(__name__)
from pathlib import Path
from datetime import datetime
from typing import Dict, Optional, List

from tokenjuice import estimate_tokens, smart_truncate

# 各级别的 token 预算上限
LEVEL_BUDGET = {
    'tldr': 50,
    'brief': 200,
    'normal': 500,
    'full': None,  # 不限
}

# 优先级保留的章节标题关键字
PRIORITY_SECTIONS = ['要点', '摘要', '总结', '核心', '架构', '设计', '用法']


def parse_frontmatter(content: str) -> Dict:
    """解析 Markdown frontmatter，返回元数据和正文"""
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


def extract_section(body: str, section_title: str) -> Optional[str]:
    """从正文中提取指定章节的内容"""
    # 匹配 ## 章节标题
    pattern = rf'^##\s*{re.escape(section_title)}\s*$(.*?)(?=^##|\Z)'
    match = re.search(pattern, body, re.MULTILINE | re.DOTALL)
    if match:
        return match.group(1).strip()
    return None


def extract_key_points(body: str) -> List[str]:
    """提取正文中的列表项（- xxx 或 1. xxx）作为关键点"""
    points = []
    for line in body.split('\n'):
        line = line.strip()
        if line.startswith('- ') or line.startswith('* '):
            points.append(line[2:].strip())
        elif re.match(r'^\d+\.\s', line):
            points.append(re.sub(r'^\d+\.\s', '', line).strip())
    return points[:10]  # 最多 10 条


def extract_section_headings(body: str) -> List[str]:
    """提取二级标题（##）"""
    headings = re.findall(r'^##\s+(.+)$', body, re.MULTILINE)
    return [h.strip() for h in headings if h.strip()]


def compute_page_summary(content: str) -> Dict[str, str]:
    """
    计算单页的四级摘要。
    
    Args:
        content: 页面完整内容（Markdown）
    
    Returns:
        dict: {tldr, brief, normal, full}
    """
    parsed = parse_frontmatter(content)
    metadata = parsed['metadata']
    body = parsed['body']
    
    title = metadata.get('标题', metadata.get('title', ''))
    summary = metadata.get('摘要', metadata.get('summary', ''))
    
    # 如果 frontmatter 没有摘要字段，从正文 ## 摘要 章节提取
    if not summary:
        summary_section = extract_section(body, '摘要')
        if summary_section:
            summary = summary_section.strip()[:300]
    
    # --- tldr: 标题 + 一句话 ---
    if summary:
        # 取摘要的第一句
        first_sent = summary.split('。')[0].split('，')[0]
        tldr = f"{title}：{first_sent}" if title else first_sent
    else:
        tldr = title if title else body[:50]
    tldr = tldr[:100]  # 防溢出
    
    # --- brief: 摘要 + 要点列表 ---
    brief_parts = []
    if summary:
        brief_parts.append(summary[:200])
    
    points_section = extract_section(body, '要点')
    if points_section:
        brief_parts.append(points_section[:300])
    else:
        # 没有要点章节就提取列表项
        points = extract_key_points(body)
        if points:
            brief_parts.append('\n'.join(f'- {p}' for p in points[:5]))
    
    brief = '\n\n'.join(brief_parts)
    if estimate_tokens(brief) > LEVEL_BUDGET['brief']:
        brief = smart_truncate(brief, LEVEL_BUDGET['brief'])
    
    # --- normal: 摘要 + 要点 + 核心章节 ---
    normal_parts = []
    if summary:
        normal_parts.append(summary)
    if points_section:
        normal_parts.append(f"## 要点\n{points_section}")
    
    # 提取优先级高的章节
    headings = extract_section_headings(body)
    for heading in headings:
        for priority in PRIORITY_SECTIONS:
            if priority in heading and heading not in ['要点']:  # 要点已包含
                section_content = extract_section(body, heading)
                if section_content and len(section_content) > 20:
                    normal_parts.append(f"## {heading}\n{section_content[:400]}")
                    break
    
    normal = '\n\n'.join(normal_parts)
    if estimate_tokens(normal) > LEVEL_BUDGET['normal']:
        normal = smart_truncate(normal, LEVEL_BUDGET['normal'])
    
    # --- full: 全文 ---
    full = content
    
    return {
        'tldr': tldr,
        'brief': brief,
        'normal': normal,
        'full': full,
    }


class SummaryTreeIndex:
    """
    摘要树索引——预计算所有知识页的各级摘要，增量更新。
    
    缓存文件：.cache/summary_tree.json
    """
    
    def __init__(self, vault_path: str = None):
        if vault_path is None:
            vault_path = r"."
        self.vault_path = Path(vault_path)
        self.cache_path = self.vault_path / ".tool" / "lingtai-kb" / ".cache" / "summary_tree.json"
        self._tree: Dict[str, Dict[str, str]] = {}
        self._load()
    
    def _load(self):
        """从缓存加载"""
        if self.cache_path.exists():
            try:
                self._tree = json.loads(self.cache_path.read_text(encoding="utf-8"))
            except Exception:
                self._tree = {}
    
    def _save(self):
        """保存缓存"""
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.cache_path.write_text(
            json.dumps(self._tree, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )
    
    def get_level(self, page_path: str, level: str = 'normal') -> Optional[str]:
        """
        获取指定页面的指定级别摘要。
        
        Args:
            page_path: 页面路径（如 丹房/07-工具与AI/xxx）
            level: tldr / brief / normal / full
        
        Returns:
            str 或 None（页面不存在时）
        """
        entry = self._tree.get(page_path)
        if entry:
            return entry.get(level)
        return None
    
    def get_best_level(self, page_path: str, max_tokens: int) -> Optional[str]:
        """
        根据 token 预算选择最合适的摘要级别。
        
        Args:
            page_path: 页面路径
            max_tokens: token 预算
        
        Returns:
            str: 最合适的级别对应的内容，None 表示页面不在索引中
        """
        entry = self._tree.get(page_path)
        if not entry:
            return None
        
        # 从低到高逐级尝试，选预算内能容纳的最高级别
        best = 'tldr'
        for level in ['tldr', 'brief', 'normal', 'full']:
            text = entry.get(level, '')
            if not text:
                continue
            tok = estimate_tokens(text)
            if tok <= max_tokens:
                best = level
        
        result = entry.get(best, '')
        if estimate_tokens(result) > max_tokens:
            result = smart_truncate(result, max_tokens)
        # 确保至少返回标题
        if not result.strip():
            title = (entry.get("tldr", "") or entry.get("brief", "") or "").split(chr(10))[0][:50]
            result = title or "（摘要不可用）"
        return result
    
    def pages_needing_llm(self, max_pages: int = 20) -> List[str]:
        """返回需要 LLM 补充摘要的页面列表（按日期旧的优先）"""
        needs = []
        for path, entry in self._tree.items():
            if not entry.get('llm', '').strip():
                needs.append(path)
        # 优先补核心页：按 normal 摘要长度倒序（内容多的优先）
        def sort_key(p):
            e = self._tree.get(p, {})
            return -(len(e.get('normal', '')) or 0)
        needs.sort(key=sort_key)
        return needs[:max_pages]
    
    def mark_llm_done(self, page_path: str, llm_summary: str):
        """标记页面已补 LLM 摘要"""
        if page_path in self._tree:
            self._tree[page_path]['''llm'''] = llm_summary
            self._save()
    
    def index_page(self, page_path: str, content: str) -> Dict[str, str]:
        """
        索引一个页面——计算各级摘要并缓存。
        
        Args:
            page_path: 页面路径
            content: 页面完整内容
        
        Returns:
            dict: 各级摘要
        """
        summaries = compute_page_summary(content)
        self._tree[page_path] = summaries
        return summaries
    
    def index_all_pages(self, force: bool = False) -> int:
        """
        索引所有知识页。
        
        Args:
            force: True=强制重新索引全部，False=仅索引新增/变更页
        
        Returns:
            int: 索引的页数
        """
        danfang = self.vault_path / "丹房"
        if not danfang.exists():
            return 0
        
        count = 0
        for md_file in sorted(danfang.rglob("*.md")):
            rel_path = str(md_file.relative_to(self.vault_path)).replace("\\", "/")
            
            if not force and rel_path in self._tree:
                continue  # 已索引且不需要强制刷新
            
            try:
                content = md_file.read_text(encoding="utf-8")
                self.index_page(rel_path, content)
                count += 1
            except Exception as e:
                log.warning("summary_tree 索引失败 %s: %s", rel_path, e)
        
        if count > 0:
            self._save()
        
        return count
    
    def stats(self) -> Dict:
        """统计信息"""
        levels = {'tldr': 0, 'brief': 0, 'normal': 0, 'full': 0}
        for entry in self._tree.values():
            for level in levels:
                if entry.get(level):
                    levels[level] += 1
        return {
            'total_pages': len(self._tree),
            'by_level': levels,
            'cache_path': str(self.cache_path),
        }
