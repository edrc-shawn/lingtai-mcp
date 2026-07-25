# -*- coding: utf-8 -*-
"""
灵台MCP - 记忆引擎模块 V2
===========================
基于灵台 index.json 的记忆引擎，替代原有的独立数据库。

功能：
- 从 index.json 读取知识图谱
- 图扩散搜索（利用 linked_from 和 links_to）
- 智能查询
- 统计分析
- 缓存预热
- 并发查询
"""

import json
import os
import re
import sys
import threading
import hashlib
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional, Tuple, Any
from concurrent.futures import ThreadPoolExecutor, as_completed
from hebbian_weights import HebbianWeights
from logger import get_logger


log = get_logger(__name__)

class MemoryEngine:
    """灵台灵识记忆引擎 V2 - 基于 index.json"""
    
    def __init__(self, vault_path: str = None):
        """
        初始化记忆引擎
        
        Args:
            vault_path: 灵台vault路径
        """
        if vault_path is None:
            self.vault_path = r"."
        else:
            self.vault_path = vault_path
        
        # index.json 路径
        self.index_path = os.path.join(self.vault_path, "丹房", ".meta", "index.json")

        # 跨进程新鲜度 mtime 占位（真实值在第 73 行附近覆盖；先占位避免重建线程竞态）
        self._index_mtime = 0.0
        
        # 缓存目录
        self.cache_dir = Path(__file__).parent / ".cache"
        self.cache_dir.mkdir(exist_ok=True)
        
        # 内存缓存
        self._query_cache = {}
        self._cache_ttl = 3600  # 1小时过期
        
        # 加载数据
        self.data = self._load_index()
        self.pages = self.data.get("pages", [])
        
        # 构建快速查找映射
        self._build_maps()
        
        # 加载持久化缓存
        self._load_persistent_cache()
        
        # 命中计数（检索即学习）
        self._hit_counts = {}
        self._hit_counts_path = self.cache_dir / "hit_counts.json"
        self._load_hit_counts()
        self._page_activity = {}  # {path: last_active_iso}
        self._page_activity_path = self.cache_dir / "page_activity.json"
        self._load_page_activity()
        
        # Hebbian 动态权重
        self.hebbian = HebbianWeights(self.vault_path)

        # 跨进程新鲜度：记录 index.json 加载时的 mtime，查询前比对
        self._index_mtime = os.path.getmtime(self.index_path) if os.path.exists(self.index_path) else 0.0

        # 原料常驻内存索引（性能优化 v2）：避免 search_raw 每次全量扫描 原料 目录
        self._raw_index = None
        self._raw_index_mtime = 0.0
        self._raw_index_lock = threading.Lock()
    
    def _read_index_json(self) -> dict:
        """只读取 index.json（快速路径，不触发重建）"""
        if os.path.exists(self.index_path):
            try:
                with open(self.index_path, "r", encoding="utf-8-sig") as f:
                    return json.load(f)
            except (json.JSONDecodeError, OSError) as e:
                log.warning("index.json load failed", extra={"error": str(e)})
        return {"pages": [], "_stats": {}}

    def _index_needs_rebuild(self) -> bool:
        """判断是否有页面比 index.json 新（需重建）。仅做 stat 扫描，不重建。"""
        if not os.path.isfile(self.index_path):
            return True
        index_mtime = os.path.getmtime(self.index_path)
        for d in ["丹房", "入门", "作品"]:
            scan_root = os.path.join(self.vault_path, d)
            if not os.path.isdir(scan_root):
                continue
            for root, dirs, files in os.walk(scan_root):
                if any(p2.startswith('.') for p2 in root.split(os.sep) if p2):
                    continue
                for f in files:
                    if not f.endswith('.md'):
                        continue
                    fpath = os.path.join(root, f)
                    try:
                        if os.path.getmtime(fpath) > index_mtime:
                            return True
                    except OSError:
                        continue
        return False

    def _rebuild_index_async(self):
        """后台线程重建 index.json，绝不阻塞查询路径（原 120s 阻塞改为异步）。"""
        build_script = os.path.normpath(os.path.join(
            os.path.dirname(os.path.dirname(__file__)), "..", "scripts", "build_index.py"))
        if not os.path.isfile(build_script):
            return

        def _run():
            try:
                import subprocess
                subprocess.run([sys.executable, build_script], capture_output=True, timeout=120)
            except Exception:
                log.debug("suppressed", exc_info=True)
            # 重建完成后若 index.json 比当前新，热重载（不阻塞前台）
            try:
                cur = os.path.getmtime(self.index_path)
            except OSError:
                return
            if cur > self._index_mtime:
                self.data = self._read_index_json()
                self.pages = self.data.get("pages", [])
                self._build_maps()
                self._query_cache.clear()
                self._index_mtime = cur

        t = threading.Thread(target=_run, daemon=True)
        t.start()

    def _load_index(self) -> dict:
        """加载 index.json（冷启动/显式刷新用）。若过期，重建改为后台线程，不阻塞。"""
        data = self._read_index_json()
        if self._index_needs_rebuild():
            self._rebuild_index_async()
        return data
    
    def _build_maps(self):
        """构建快速查找映射 + 倒排索引 + 预计算特征（性能优化）"""
        # path -> page
        self.path_map = {p["path"]: p for p in self.pages}
        
        # filename -> page
        self.name_map = {p["filename"]: p for p in self.pages}
        
        # tag -> [pages]
        self.tag_map = {}
        for p in self.pages:
            for tag in p.get("tags", []):
                if tag not in self.tag_map:
                    self.tag_map[tag] = []
                self.tag_map[tag].append(p)
        
        # domain -> [pages]
        self.domain_map = {}
        for p in self.pages:
            domain = p.get("domain", "")
            if not domain:
                path = p.get("path", "")
                parts = path.split('/')
                if len(parts) >= 3 and parts[0] == '丹房':
                    domain = parts[1]
            if domain:
                if domain not in self.domain_map:
                    self.domain_map[domain] = []
                self.domain_map[domain].append(p)
        
        # ─── 性能优化：关键词倒排索引（加速 _exact_match）───
        # keyword_lower → set(page_paths)
        self._keyword_index = {}
        # 预计算每页的关键词集合（加速 _calculate_page_weight）
        self._page_keywords = {}  # path → frozenset of lowercase keywords
        
        import re as _re
        for p in self.pages:
            path = p["path"]
            # 收集该页所有可搜索文本
            title = p.get("title", "")
            summary = p.get("summary", "")[:200]  # 只取前200字，匹配权重计算
            tags_text = " ".join(p.get("tags", []))
            domain = p.get("domain", "")
            search_text = f"{title} {summary} {tags_text} {domain}".lower()
            
            # 提取关键词：2-4字中文 + 2+字母英文
            keywords = set(
                _re.findall(r'[\u4e00-\u9fff]{2,4}|[a-z0-9]{2,}', search_text)
            )
            self._page_keywords[path] = frozenset(keywords)
            
            # 倒排索引：每个关键词 → 包含该词的页面
            for kw in keywords:
                if kw not in self._keyword_index:
                    self._keyword_index[kw] = set()
                self._keyword_index[kw].add(path)
    
    # ==================== 持久化缓存 ====================
    
    def _get_cache_key(self, text: str, operation: str) -> str:
        """生成缓存键"""
        content = f"{operation}:{text}"
        return hashlib.md5(content.encode()).hexdigest()
    
    def _load_persistent_cache(self):
        """加载持久化缓存"""
        cache_file = self.cache_dir / "query_cache.json"
        if cache_file.exists():
            try:
                with open(cache_file, "r", encoding="utf-8") as f:
                    self._query_cache = json.load(f)
            except Exception:
                self._query_cache = {}
    
    def _save_persistent_cache(self):
        """保存持久化缓存"""
        cache_file = self.cache_dir / "query_cache.json"
        try:
            with open(cache_file, "w", encoding="utf-8") as f:
                json.dump(self._query_cache, f, ensure_ascii=False)
        except Exception:
            log.debug("suppressed", exc_info=True)
    
    def _get_cached(self, cache_key: str) -> Optional[dict]:
        """获取缓存"""
        cached = self._query_cache.get(cache_key)
        if cached:
            # 检查是否过期
            if datetime.now().timestamp() - cached.get("timestamp", 0) < self._cache_ttl:
                return cached.get("result")
        return None
    
    def _set_cached(self, cache_key: str, result: dict):
        """设置缓存"""
        self._query_cache[cache_key] = {
            "result": result,
            "timestamp": datetime.now().timestamp()
        }
        # 定期保存（每10次操作）
        if len(self._query_cache) % 10 == 0:
            self._save_persistent_cache()
    
    def _load_page_activity(self):
        """加载页面活跃时间"""
        if self._page_activity_path.exists():
            try:
                with open(self._page_activity_path, "r", encoding="utf-8") as f:
                    self._page_activity = json.load(f)
            except Exception:
                self._page_activity = {}

    def _save_page_activity(self):
        """保存页面活跃时间"""
        try:
            with open(self._page_activity_path, "w", encoding="utf-8") as f:
                json.dump(self._page_activity, f, ensure_ascii=False)
        except Exception:
            log.debug("suppressed", exc_info=True)

    def record_page_access(self, path: str):
        """记录页面访问（活跃时间+命中计数）"""
        from datetime import datetime
        self._page_activity[path] = datetime.now().isoformat(timespec="seconds")
        if len(self._page_activity) % 20 == 0:
            self._save_page_activity()

    def get_cold_pages(self, days: int = 30, min_backlinks: int = 3, max_results: int = 10) -> list:
        """
        获取低活跃页面：超过 days 天未访问 且 入链 ≤ min_backlinks 的下品/中品页
        """
        from datetime import datetime, timedelta
        from dateutil import parser as dateparser
        cutoff = datetime.now() - timedelta(days=days)
        cold = []
        for page in self.pages:
            path = page.get("path", "")
            last_active_str = self._page_activity.get(path, "")
            if last_active_str:
                try:
                    last_dt = dateparser.parse(last_active_str)
                    if last_dt > cutoff:
                        continue  # 最近活跃过
                except Exception:
                    log.debug("suppressed", exc_info=True)
            pinji = page.get("pinji", "")
            backlinks = len(page.get("linked_from", []))
            if backlinks <= min_backlinks and pinji in ("下品", "中品", ""):
                cold.append({
                    "path": path,
                    "title": page.get("title", ""),
                    "pinji": pinji,
                    "backlinks": backlinks,
                })
        cold.sort(key=lambda p: (p["backlinks"], p["pinji"]))
        return cold[:max_results]

    def _load_hit_counts(self):
        """加载命中计数"""
        if self._hit_counts_path.exists():
            try:
                with open(self._hit_counts_path, "r", encoding="utf-8") as f:
                    self._hit_counts = json.load(f)
            except Exception:
                self._hit_counts = {}
    
    def _save_hit_counts(self):
        """保存命中计数"""
        try:
            with open(self._hit_counts_path, "w", encoding="utf-8") as f:
                json.dump(self._hit_counts, f, ensure_ascii=False)
        except Exception:
            log.debug("suppressed", exc_info=True)
    
    def clear_cache(self):
        """清空缓存"""
        self._query_cache = {}
        cache_file = self.cache_dir / "query_cache.json"
        if cache_file.exists():
            cache_file.unlink()
    
    def get_cache_stats(self) -> dict:
        """获取缓存统计"""
        cache_file = self.cache_dir / "query_cache.json"
        cache_size = 0
        if cache_file.exists():
            cache_size = cache_file.stat().st_size
        
        return {
            "memory_entries": len(self._query_cache),
            "disk_size": cache_size,
            "cache_dir": str(self.cache_dir),
        }
    
    def _ensure_fresh(self):
        """跨进程新鲜度：index.json 变化则热重载（快速），仅当页面比索引新时后台重建。
        
        性能优化：原实现在查询路径同步触发 _load_index → subprocess 全量重建（最多 120s 阻塞）。
        现改为：立即用现有 index.json 热重载（毫秒级），重建放到后台线程，查询永不卡顿。
        """
        try:
            current_mtime = os.path.getmtime(self.index_path)
        except OSError:
            return  # index.json 不存在，跳过
        if current_mtime > self._index_mtime:
            self.data = self._read_index_json()
            self.pages = self.data.get("pages", [])
            self._build_maps()
            self._query_cache.clear()
            self._index_mtime = current_mtime
            if self._index_needs_rebuild():
                self._rebuild_index_async()
    
    def refresh(self):
        """刷新数据（重新加载 index.json）；重建在后台线程，不阻塞。"""
        self.data = self._load_index()
        self.pages = self.data.get("pages", [])
        self._build_maps()
        # 清空查询缓存（索引变了，旧缓存结果可能不准确）
        self._query_cache.clear()
        # 更新 mtime 标记
        try:
            self._index_mtime = os.path.getmtime(self.index_path)
        except OSError:
            self._index_mtime = 0.0
    
    # ==================== 缓存预热 ====================
    
    def warmup_cache(self, keywords: List[str] = None):
        """
        缓存预热：预加载常用查询结果
        
        Args:
            keywords: 要预热的关键词列表（默认：高频页面标题）
        """
        if keywords is None:
            # 预热高频页面
            keywords = []
            for p in self.pages[:20]:  # 前20个页面
                title = p.get("title", "")
                if title:
                    keywords.append(title)
        
        # 预热查询
        for keyword in keywords[:10]:  # 限制10个
            self.query(keyword, use_ngram_fallback=False)
    
    def get_page_stats(self) -> dict:
        """获取页面统计（用于缓存预热决策）"""
        return {
            "total_pages": len(self.pages),
            "total_links": sum(len(p.get("links_to", [])) for p in self.pages),
            "core_pages": sum(1 for p in self.pages if p.get("is_core")),
            "hub_pages": sorted(
                [(p["title"], len(p.get("linked_from", []))) for p in self.pages],
                key=lambda x: x[1],
                reverse=True
            )[:10],
        }
    
    # ==================== 并发查询 ====================
    
    def parallel_query(self, keywords: List[str], max_workers: int = 4) -> Dict[str, dict]:
        """
        并发查询多个关键词
        
        Args:
            keywords: 关键词列表
            max_workers: 最大并发数
        
        Returns:
            dict: 关键词到结果的映射
        """
        results = {}
        
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            # 提交所有查询任务
            future_to_keyword = {
                executor.submit(self.query, keyword): keyword
                for keyword in keywords
            }
            
            # 收集结果
            for future in as_completed(future_to_keyword):
                keyword = future_to_keyword[future]
                try:
                    result = future.result()
                    results[keyword] = result
                except Exception as e:
                    results[keyword] = {"error": str(e)}
        
        return results
    
    def parallel_search_graph(self, keywords: List[str], hops: int = 3, max_workers: int = 4) -> Dict[str, list]:
        """
        并发图扩散搜索
        
        Args:
            keywords: 关键词列表
            hops: 扩散跳数
            max_workers: 最大并发数
        
        Returns:
            dict: 关键词到结果的映射
        """
        results = {}
        
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            # 提交所有搜索任务
            future_to_keyword = {
                executor.submit(self.search_graph, keyword, hops): keyword
                for keyword in keywords
            }
            
            # 收集结果
            for future in as_completed(future_to_keyword):
                keyword = future_to_keyword[future]
                try:
                    result = future.result()
                    results[keyword] = result
                except Exception as e:
                    results[keyword] = []
        
        return results
    
    def query(self, keyword: str, use_ngram_fallback: bool = True) -> dict:
        """
        查询知识（支持n-gram回退 + 缓存）
        
        Args:
            keyword: 搜索关键词
            use_ngram_fallback: 是否启用n-gram回退
        
        Returns:
            dict: 查询结果，包含匹配类型
        """
        # 跨进程新鲜度：其他进程可能已更新索引
        self._ensure_fresh()
        # 检查缓存
        cache_key = self._get_cache_key(keyword, "query")
        cached = self._get_cached(cache_key)
        if cached:
            return cached
        
        # 第一步：精确匹配
        exact_results = self._exact_match(keyword)
        
        if exact_results:
            result = {
                "results": exact_results,
                "match_type": "exact",
                "keyword": keyword,
            }
            self._set_cached(cache_key, result)
            return result
        
        # 第二步：n-gram回退（如果启用）
        if use_ngram_fallback:
            ngram_results = self._ngram_match(keyword)
            
            if ngram_results:
                result = {
                    "results": ngram_results,
                    "match_type": "ngram",
                    "keyword": keyword,
                    "ngrams": self._generate_ngrams(keyword),
                }
                self._set_cached(cache_key, result)
                return result
        
        # 无结果
        result = {
            "results": [],
            "match_type": "none",
            "keyword": keyword,
        }
        self._set_cached(cache_key, result)
        return result
    
    def _exact_match(self, keyword: str) -> list:
        """精确匹配（倒排索引 O(1) 加速）"""
        keyword_lower = keyword.lower()
        results = []
        seen = set()
        
        # 策略1：倒排索引快速剪枝（如果命中率<全量，则只扫描候选集）
        if hasattr(self, '_keyword_index') and self._keyword_index:
            # 尝试用关键词在倒排索引中查找候选页面
            kw_words = set(re.findall(r'[\u4e00-\u9fff]{2,4}|[a-z0-9]{2,}', keyword_lower))
            candidates = set()
            for kw in kw_words:
                if kw in self._keyword_index:
                    candidates.update(self._keyword_index[kw])
            
            # 如果候选集占全量 < 70%，用倒排索引加速；否则直接全量扫描更快
            total_pages = len(self.pages)
            if candidates and len(candidates) < total_pages * 0.7:
                for path in candidates:
                    page = self.path_map.get(path)
                    if not page or path in seen:
                        continue
                    if self._page_matches_keyword(page, keyword_lower):
                        seen.add(path)
                        results.append(page)
                return results
        
        # 策略2：全量扫描（keyword 太短或候选集太大时用）
        for page in self.pages:
            path = page["path"]
            if path in seen:
                continue
            if self._page_matches_keyword(page, keyword_lower):
                seen.add(path)
                results.append(page)
        
        return results
    
    def _page_matches_keyword(self, page: dict, keyword_lower: str) -> bool:
        """单页关键词匹配（抽出复用，支持两路调用）"""
        # 标题匹配
        title = page.get("_tl") or page.get("title", "").lower()
        if keyword_lower in title:
            return True
        
        # 摘要匹配
        summary = page.get("_sl") or page.get("summary", "").lower()
        if keyword_lower in summary:
            return True
        
        # 标签匹配
        tags = page.get("_tl2") or [t.lower() for t in page.get("tags", [])]
        for tag in tags:
            if keyword_lower in tag:
                return True
        
        return False
    
    def _generate_ngrams(self, text: str, n: int = 3) -> list:
        """生成字符级n-gram"""
        ngrams = []
        text = text.lower()
        
        for i in range(len(text) - n + 1):
            ngram = text[i:i+n]
            if ngram.strip():  # 忽略纯空格n-gram
                ngrams.append(ngram)
        
        return ngrams
    
    def _ngram_match(self, keyword: str, n: int = 3) -> list:
        """
        n-gram模糊匹配（优先使用预计算 ngram，无预计算则实时生成）
        """
        keyword_ngrams = set(self._generate_ngrams(keyword, n))
        
        if not keyword_ngrams:
            return []
        
        results = []
        seen_paths = set()
        
        for page in self.pages:
            if page["path"] in seen_paths:
                continue
            
            # 优先使用预计算的 3-gram
            page_ngrams = page.get("_ng3")
            if page_ngrams:
                page_ngrams = set(page_ngrams)
            else:
                # 实时生成（兼容旧索引）
                page_text = (
                    page.get("title", "") + " " +
                    page.get("summary", "") + " " +
                    " ".join(page.get("tags", []))
                ).lower()
                page_ngrams = set(self._generate_ngrams(page_text, n))
            
            if not page_ngrams:
                continue
            
            overlap = len(keyword_ngrams & page_ngrams)
            overlap_ratio = overlap / len(keyword_ngrams)
            
            if overlap_ratio >= 0.3:
                seen_paths.add(page["path"])
                results.append(page)
        
        return results
    
    def search_graph(self, keyword: str, hops: int = 3, weighted: bool = True, start_pages: list = None) -> list:
        """
        图扩散搜索（支持加权扩散 + 结果缓存）
        
        Args:
            keyword: 起始关键词
            hops: 扩散跳数（默认3，最大3）
            weighted: 是否启用加权扩散（默认开启，核心页面权重更高）
            start_pages: 预查询的结果（避免重复查询）
        
        Returns:
            list: 关联的页面列表（按权重排序）
        """
        # 跨进程新鲜度
        self._ensure_fresh()
        # 缓存检查（关键词 + hops 的组合缓存）
        cache_key = self._get_cache_key(f"{keyword}|h={hops}", "graph")
        cached = self._get_cached(cache_key)
        if cached:
            return cached
        
        # 限制最大跳数
        hops = min(hops, 3)
        
        # 先找到匹配的页面（支持外部传入避免重复查询）
        if start_pages is None:
            query_result = self.query(keyword)
            start_pages = query_result.get("results", [])
        
        if not start_pages:
            self._set_cached(cache_key, [])
            return []
        
        # BFS扩散（带权重），限制最大结果数防止超时
        MAX_RESULTS = 50
        visited = set()
        result_pages = []
        queue = [(page, 0, 1.0) for page in start_pages]
        
        while queue and len(result_pages) < MAX_RESULTS:
            current_page, current_hop, current_weight = queue.pop(0)
            
            page_path = current_page["path"]
            if page_path in visited:
                continue
            if current_hop > hops:
                continue
            
            visited.add(page_path)
            
            page_weight = self._calculate_page_weight(current_page, start_pages[0]["path"], keyword) if weighted else 1.0
            final_weight = current_weight * page_weight
            
            result_pages.append({
                **current_page,
                "weight": round(final_weight, 3),
                "hop": current_hop,
            })
            
            if current_hop < hops:
                for link in current_page.get("links_to", []):
                    if link in self.path_map and link not in visited:
                        queue.append((self.path_map[link], current_hop + 1, final_weight))
                
                for link in current_page.get("linked_from", []):
                    if link in self.path_map and link not in visited:
                        queue.append((self.path_map[link], current_hop + 1, final_weight))
        
        if weighted:
            result_pages.sort(key=lambda x: x.get("weight", 0), reverse=True)
        
        # Hebbian权重记录：批量写入，避免190次独立IO
        top_pages = result_pages[:20]
        if top_pages:
            hebbian_pairs = []
            for i, page_a in enumerate(top_pages):
                for page_b in top_pages[i+1:]:
                    hebbian_pairs.append((page_a["path"], page_b["path"]))
            self.hebbian.on_query_batch(hebbian_pairs)
        
        self._set_cached(cache_key, result_pages)
        return result_pages
    
    def _calculate_page_weight(self, page: dict, query_page: str = None, keyword: str = "") -> float:
        """
        计算页面权重（优化版：预计算关键词集，避免重复分词）
        
        权重因素：
        - 核心页面（⚡标记）：权重 ×1.5
        - 高入链页面：权重 ×1.2
        - 最近更新：权重 ×1.1
        - Hebbian 边权重：与查询页面的共现频率
        - 关键词相关性：使用预计算的 _page_keywords 加速
        """
        weight = 1.0
        
        # 核心页面
        if page.get("is_core"):
            weight *= 1.5
        
        # 高入链
        backlinks = len(page.get("linked_from", []))
        if backlinks > 10:
            weight *= 1.2
        elif backlinks > 5:
            weight *= 1.1
        
        # 最近更新（30天内）
        date_str = page.get("date", "")
        if date_str:
            try:
                page_date = datetime.strptime(date_str, "%Y-%m-%d")
                if (datetime.now() - page_date).days < 30:
                    weight *= 1.1
            except:
                pass
        
        # Hebbian 边权重
        if query_page:
            hebbian_weight = self.hebbian.get_weight(query_page, page["path"])
            if hebbian_weight > 0.5:
                weight *= (1.0 + hebbian_weight * 0.5)
        
        # 关键词相关性：使用预计算的关键词集（避免每节点重复分词）
        if keyword and hasattr(self, '_page_keywords'):
            kw_lower = keyword.lower()
            kw_words = set(re.findall(r'[\u4e00-\u9fff]{2,4}|[a-z0-9]{2,}', kw_lower))
            page_kws = self._page_keywords.get(page["path"], frozenset())
            overlap = len(kw_words & page_kws)
            if overlap > 0:
                # 匹配度：overlap/query_words → 转换为 1.0~1.5 权重加成
                ratio = overlap / max(len(kw_words), 1)
                relevance = 1.0 + min(ratio * 2.0, 0.5)  # 上限 +50%
                weight *= relevance
        
        return weight
    
    def hot_register_page(self, path: str, title: str, domain: str, summary: str = "",
                           tags: list = None, pinji: str = "下品"):
        """立即将新页注册到内存索引，不等 index.json 重建。
        使新页在 knowledge_search 中即刻可搜索。
        """
        from datetime import datetime
        page = {
            "path": f"丹房/{domain}/{title}.md",
            "filename": f"{title}.md",
            "title": title,
            "domain": domain,
            "tags": tags or [],
            "pinji": pinji,
            "date": datetime.now().strftime("%Y-%m-%d"),
            "summary": summary[:200] if summary else "",
            "links_to": [],
            "linked_from": [],
            "status": "",
            "type": "page",
            "is_core": False,
            "is_gate": False,
        }
        self.pages.append(page)
        self.path_map[page["path"]] = page
        self.name_map[page["filename"]] = page
        dom = domain or (path.split("/")[1] if "/" in path else "")
        if dom:
            self.domain_map.setdefault(dom, []).append(page)

    def get_page_by_path(self, path: str) -> Optional[dict]:
        """根据路径获取页面（检索即学习：每次命中计数+1，记录活跃时间）"""
        page = self.path_map.get(path)
        if page:
            self._hit_counts[path] = self._hit_counts.get(path, 0) + 1
            if self._hit_counts[path] % 10 == 0:
                self._save_hit_counts()
            self.record_page_access(path)
        return page
    
    def get_page_by_name(self, name: str) -> Optional[dict]:
        """根据文件名获取页面"""
        return self.name_map.get(name)
    
    def get_pages_by_tag(self, tag: str) -> list:
        """根据标签获取页面"""
        return self.tag_map.get(tag, [])
    
    def get_pages_by_domain(self, domain: str) -> list:
        """根据域名获取页面"""
        return self.domain_map.get(domain, [])
    
    # ═══════════════════════════════════════════════════════════
    #  原料搜索（问知第二层回退：丹房→原料→联网）
    # ═══════════════════════════════════════════════════════════
    
    def _ensure_raw_index(self):
        """懒加载原料常驻内存索引（含文件名 + 标题）。原料目录 mtime 变化则失效重建。
        仅首次访问支付一次全量扫描成本（~1.5s/1211 文件），之后所有 search_raw 零磁盘 IO。"""
        raw_dir = os.path.join(self.vault_path, "原料")
        if not os.path.isdir(raw_dir):
            return None
        try:
            cur_mtime = os.path.getmtime(raw_dir)
        except OSError:
            cur_mtime = 0.0
        if self._raw_index is not None and cur_mtime <= self._raw_index_mtime:
            return self._raw_index
        with self._raw_index_lock:
            try:
                cur_mtime2 = os.path.getmtime(raw_dir)
            except OSError:
                cur_mtime2 = 0.0
            if self._raw_index is not None and cur_mtime2 <= self._raw_index_mtime:
                return self._raw_index
            entries = []
            try:
                for fname in os.listdir(raw_dir):
                    if not fname.endswith('.md'):
                        continue
                    fpath = os.path.join(raw_dir, fname)
                    if not os.path.isfile(fpath):
                        continue
                    title = ""
                    try:
                        with open(fpath, 'r', encoding='utf-8', errors='ignore') as f:
                            first_block = f.read(600)
                        title = self._parse_raw_frontmatter(first_block).get('标题', '').lower()
                    except Exception:
                        log.debug("suppressed", exc_info=True)
                    entries.append({
                        "fname": fname,
                        "fpath": fpath,
                        "fname_lower": fname.lower(),
                        "title_lower": title,
                    })
            except OSError:
                return self._raw_index
            self._raw_index = entries
            self._raw_index_mtime = cur_mtime2
            return entries

    def search_raw(self, keyword: str, max_results: int = 10, min_score: float = 0.5) -> dict:
        """
        搜索原料目录（未提炼的外部输入），含置信度打分。
        
        性能优化 v2：候选收集改用常驻内存索引（_ensure_raw_index，文件名+标题），
        纯内存遍历、零磁盘 IO；仅在最后对少量候选逐个开读 frontmatter 打分，
        彻底消除原实现每次调用对全部原料文件（~1211）的 listdir + 逐文件开读。
        
        Args:
            keyword: 搜索关键词
            max_results: 最大返回数
            min_score: 最低置信度阈值（低于此分的排除）
        
        Returns:
            dict: {results: [...], source: "raw", keyword: ...}
        """
        raw_dir = os.path.join(self.vault_path, "原料")
        if not os.path.isdir(raw_dir):
            return {"results": [], "source": "raw", "keyword": keyword}
        
        keyword_lower = keyword.lower()
        kw_words = set(re.findall(r'[\u4e00-\u9fff]{2,4}|[a-z0-9]{2,}', keyword_lower))
        
        # 候选收集：用常驻内存索引（O(entries) 内存遍历，零磁盘 IO）
        entries = self._ensure_raw_index()
        candidates = []  # (fname, fpath, base_score)

        if entries:
            for e in entries:
                fname_lower = e["fname_lower"]
                if keyword_lower in fname_lower:
                    candidates.append((e["fname"], e["fpath"], 3.0))
                elif any(w in fname_lower for w in kw_words):
                    candidates.append((e["fname"], e["fpath"], 1.5))
            # 文件名匹配 < 5 条 → 加扫标题
            if len(candidates) < 5:
                for e in entries:
                    if any(c[0] == e["fname"] for c in candidates):
                        continue
                    title_lower = e["title_lower"]
                    if keyword_lower in title_lower or any(w in title_lower for w in kw_words):
                        candidates.append((e["fname"], e["fpath"], 1.0))
                        if len(candidates) >= max_results * 2:
                            break
        else:
            # 索引不可用（异常兜底）→ 降级为原 listdir 逻辑
            try:
                for fname in os.listdir(raw_dir):
                    fpath = os.path.join(raw_dir, fname)
                    if not fname.endswith('.md') or not os.path.isfile(fpath):
                        continue
                    fname_lower = fname.lower()
                    if keyword_lower in fname_lower:
                        candidates.append((fname, fpath, 3.0))
                    elif any(w in fname_lower for w in kw_words):
                        candidates.append((fname, fpath, 1.5))
            except OSError:
                return {"results": [], "source": "raw", "keyword": keyword}
            if len(candidates) < 5:
                for fname in os.listdir(raw_dir):
                    if len(candidates) >= max_results * 2:
                        break
                    fpath = os.path.join(raw_dir, fname)
                    if not fname.endswith('.md') or not os.path.isfile(fpath):
                        continue
                    if any(c[0] == fname for c in candidates):
                        continue
                    try:
                        with open(fpath, 'r', encoding='utf-8', errors='ignore') as f:
                            first_block = f.read(600)
                        fm = self._parse_raw_frontmatter(first_block)
                        title = fm.get('标题', '').lower()
                        if keyword_lower in title or any(w in title for w in kw_words):
                            candidates.append((fname, fpath, 1.0))
                            if len(candidates) >= max_results * 2:
                                break
                    except Exception:
                        continue
        
        if not candidates:
            return {"results": [], "source": "raw", "keyword": keyword}
        
        # ─── 置信度打分：只读少量候选的 frontmatter ───
        from datetime import datetime as _dt
        now = _dt.now()
        results = []
        
        for fname, fpath, base_score in candidates:
            score = base_score
            proc_status = ""
            proc_level = ""
            digest = ""
            
            try:
                with open(fpath, 'r', encoding='utf-8', errors='ignore') as f:
                    first_block = f.read(600)
                fm = self._parse_raw_frontmatter(first_block)
                
                proc_status = fm.get('处理状态', '')
                proc_level = fm.get('提炼分级', '')
                digest = fm.get('提炼摘要', '')
                title = fm.get('标题', fname.replace('.md', ''))
                date_str = fm.get('日期', '')
                
                # ── 处理状态置信度调整 ──
                if proc_status == '待提炼':
                    if proc_level == '完整':
                        score += 2.0   # 高质量待提炼 → 最可信
                    else:
                        score += 1.0   # 待提炼但未分级
                elif proc_status == '已跳过':
                    score += 0.3       # 低质量 → 微降
                elif proc_status == '已提炼':
                    score -= 2.0       # 已入丹房 → 大幅降权（避免重复）
                
                # ── 附加信号 ──
                if digest:
                    score += 0.5       # 有提炼摘要 = 已人工审阅过
                if date_str:
                    try:
                        d = _dt.strptime(date_str[:10], '%Y-%m-%d')
                        if (now - d).days < 30:
                            score += 0.5   # 30天内 → 新鲜加分
                    except ValueError:
                        pass
                if fm.get('favorite') == 'true':
                    score += 0.3       # 用户收藏
                    
            except Exception:
                title = fname.replace('.md', '')
            
            if score < min_score:
                continue
            
            results.append({
                "path": f"原料/{fname}",
                "title": title,
                "summary": digest or fname[:100],
                "source": "raw",
                "score": round(score, 1),
                "status": proc_status or "未标记",
                "level": proc_level or "",
            })
        
        # 按得分排序
        results.sort(key=lambda x: -x["score"])
        return {
            "results": results[:max_results],
            "source": "raw",
            "keyword": keyword,
        }
    
    def _parse_raw_frontmatter(self, text: str) -> dict:
        """解析原料文件 frontmatter（只读前600字节，快速提取）"""
        fm = {}
        m = re.match(r'^---\s*\n(.*?)\n(?:---|\.\.\.)', text, re.DOTALL)
        if m:
            for line in m.group(1).split('\n'):
                if ':' in line:
                    k, v = line.split(':', 1)
                    fm[k.strip()] = v.strip().strip('"\'')
        return fm
    
    def get_hit_count(self, path: str) -> int:
        """获取页面命中计数"""
        return self._hit_counts.get(path, 0)
    
    def get_core_pages(self) -> list:
        """获取核心页面"""
        return [p for p in self.pages if p.get("is_core")]
    
    def get_gate_pages(self) -> list:
        """获取门控页面"""
        return [p for p in self.pages if p.get("is_gate")]
    
    def get_related_pages(self, path: str, max_results: int = 10) -> list:
        """
        获取相关页面（基于链接关系）
        
        Args:
            path: 页面路径
            max_results: 最大结果数
        
        Returns:
            list: 相关页面列表
        """
        page = self.get_page_by_path(path)
        if not page:
            return []
        
        related = set()
        
        # 出链
        for link in page.get("links_to", []):
            if link != path:
                related.add(link)
        
        # 入链
        for link in page.get("linked_from", []):
            if link != path:
                related.add(link)
        
        # 转换为页面对象并限制数量
        result = []
        for link in list(related)[:max_results]:
            if link in self.path_map:
                result.append(self.path_map[link])
        
        return result
    
    # ─── 锚点提取系统（借鉴 Polaris memoryRecallAnchors.ts 理念） ───

    RELATION_ANCHORS = frozenset([
        '妈妈', '我妈', '老妈', '母亲', '爸爸', '我爸', '老爸', '父亲',
        '老师', '姐姐', '妹妹', '哥哥', '弟弟', '伴侣',
        '男朋友', '女朋友', '老婆', '老公', '妻子', '丈夫',
        '儿子', '女儿', '爷爷', '奶奶', '外公', '外婆'
    ])

    MODEL_ANCHORS = frozenset([
        'openai', 'chatgpt', 'gpt', 'claude', 'deepseek',
        'gemini', 'kimi', 'qwen', '豆包', '通义',
        'nova', 'polaris', 'pharos', 'cursor', 'copilot',
        'midjourney', 'sora', 'glm', 'minimax',
        'lingtai', '灵台', 'obsidian', 'notion'
    ])

    TECH_ANCHORS = frozenset([
        '向量', '索引', '记忆', '模型', '摘要', '召回',
        '嵌入', 'embedding', 'token', '上下文', 'prompt',
        '知识图', '图谱', '链接', '节点', '权重', '算法',
        'api', 'mcp', '数据库', '缓存', '并发', '异步'
    ])

    CJK_STOPWORDS = frozenset([
        '我', '你', '他', '她', '它', '们', '咱',
        '我们', '你们', '他们', '她们', '它们',
        '这', '那', '这个', '那个', '这些', '那些',
        '谁', '啥', '么', '个', '的', '了', '呢', '吗', '吧',
        '啊', '呀', '哦', '哈', '和', '与', '及', '或',
        '在', '是', '有', '就', '都', '也', '还', '很',
        '更', '最', '要', '会', '能', '把', '被', '让',
        '给', '对', '从', '到', '上', '下', '里',
    ])

    def _is_stopword(self, term: str) -> bool:
        """检查是否为停用词"""
        return term.strip().lower() in self.CJK_STOPWORDS

    def _cjk_ngrams(self, text: str, min_n: int = 2, max_n: int = 4) -> list:
        """CJK n-gram 切词（2-4字），跳过含停用词的组合"""
        sequences = re.findall(r'[\u4e00-\u9fff]+', text)
        terms = []
        for seq in sequences:
            for n in range(min_n, min(max_n + 1, len(seq) + 1)):
                for i in range(len(seq) - n + 1):
                    term = seq[i:i+n]
                    if any(self._is_stopword(c) for c in term):
                        continue
                    terms.append(term)
        return terms

    def _tokenize_recall_terms(self, text: str) -> list:
        """分词：CJK n-gram + ASCII 词，去重去停用词"""
        norm = text.lower()
        ascii_terms = re.findall(r'[a-z0-9_][a-z0-9_.-]{1,}', norm)
        cjk_terms = self._cjk_ngrams(norm)
        seen = set()
        result = []
        for t in ascii_terms + cjk_terms:
            if t not in seen and not self._is_stopword(t):
                seen.add(t)
                result.append(t)
        return result

    def extract_anchors(self, text: str) -> list:
        """
        锚点提取：从文本中提取高价值术语（Polaris 风格）

        返回 [{term, weight, source}]，按权重降序
        """
        norm = text.lower()
        anchors = {}

        def _add(term, weight, source):
            existing = anchors.get(term)
            if not existing or weight > existing['weight']:
                anchors[term] = {'term': term, 'weight': weight, 'source': source}

        # 1. 预设锚点：关系词 → 权重6
        for a in self.RELATION_ANCHORS:
            if a in norm:
                _add(a, 6, 'preset')

        # 2. 预设锚点：模型/产品 → 权重5
        for a in self.MODEL_ANCHORS:
            if a in norm:
                _add(a, 5, 'preset')

        # 3. 预设锚点：技术术语 → 权重3
        for a in self.TECH_ANCHORS:
            if a in norm:
                _add(a, 3, 'preset')

        # 4. 形状锚点：大写/含数字的 ASCII 词 → 权重4
        ascii_terms = re.findall(r'[A-Za-z][A-Za-z0-9_.-]{2,}', text)
        for term in ascii_terms:
            t = term.lower()
            if t not in anchors and (term[0].isupper() or re.search(r'[0-9]', term)):
                if t in self.MODEL_ANCHORS:
                    continue
                _add(t, 4, 'shape')

        # 5. 语料锚点：跨域高频词 → 权重 2.5~4.5
        tokens = self._tokenize_recall_terms(text)
        for t in tokens:
            if t in anchors:
                continue
            count = 0
            seen_domains = set()
            for page in self.pages:
                pt = (page.get('title', '') + ' ' + page.get('summary', '')).lower()
                if t in pt:
                    count += 1
                    d = page.get('domain', '')
                    if d:
                        seen_domains.add(d)
            if count >= 2:
                w = 2.5 + min(len(seen_domains), 3) * 0.5
                _add(t, min(w, 4.5), 'corpus')

        return sorted(anchors.values(), key=lambda a: -a['weight'])

    def score_relevance(self, query: str, page: dict) -> dict:
        """
        多维度相关度评分（Polaris 风格 memoryRetrievalIndex.ts）

        返回 {
            score, matched_anchors, matched_keywords,
            match_kind: exact|anchor|keyword|none
        }
        """
        q_lower = query.lower()
        q_terms = set(self._tokenize_recall_terms(query))
        p_text = (page.get('title', '') + ' ' +
                  page.get('summary', '') + ' ' +
                  ' '.join(page.get('tags', []))).lower()

        # ① 精确短语匹配 → +2
        exact = 2.0 if q_lower in p_text else 0.0

        # ①-b 标题额外加权：查询词出现在标题中时再加 +1.5
        title_lower = page.get('title', '').lower()
        title_bonus = 1.5 if q_lower in title_lower else 0.0

        # ② 锚点匹配 → 累加权重（仅用子串匹配，省去 p_anchors 全量分析）
        q_anchors = self.extract_anchors(query)
        matched = [a for a in q_anchors if a['term'] in p_text]
        anchor_pts = sum(a['weight'] for a in matched)

        # ③ 关键词重叠（Jaccard-like）
        p_terms = set(self._tokenize_recall_terms(p_text))
        overlap = len(q_terms & p_terms)
        overlap_pts = (overlap / (len(q_terms) ** 0.5 * max(len(p_terms), 1) ** 0.5)
                       if overlap else 0.0)

        # ④ 新鲜度
        recency = 0.0
        try:
            d = page.get('date', '')
            if d:
                pd = datetime.strptime(d, '%Y-%m-%d')
                days = (datetime.now() - pd).days
                recency = 0.5 * max(0, 90 - days) / 90
        except Exception:
            log.debug("suppressed", exc_info=True)

        # ⑤ 检索即学习：历史命中次数微量奖励（每200次+0.15，上限0.15）
        path = page.get('path', '')
        hit_count = self._hit_counts.get(path, 0)
        hit_bonus = min(hit_count / 200.0, 0.15)

        total = exact + title_bonus + anchor_pts + overlap_pts + recency + hit_bonus
        kind = 'exact' if exact > 0 else \
               'anchor' if matched else \
               'keyword' if overlap > 0 else 'none'

        return {
            'score': round(total, 4),
            'matched_anchors': [a['term'] for a in matched],
            'matched_keywords': list(q_terms & p_terms),
            'match_kind': kind,
        }

    def query_with_scoring(self, keyword: str, max_results: int = 10) -> dict:
        """
        带锚点评分的增强查询
        
        返回 {results, match_type, scores, anchors, keyword}
        """
        base = self.query(keyword)
        results = base.get('results', [])
        if not results:
            return {**base, 'scores': [], 'anchors': []}

        # 对每个结果评分
        scored = []
        for page in results:
            sr = self.score_relevance(keyword, page)
            scored.append({
                'path': page['path'],
                'title': page['title'],
                'summary': page.get('summary', '')[:200],
                'pinji': page.get('pinji', ''),
                'domain': page.get('domain', ''),
                'date': page.get('date', ''),
                'backlinks': len(page.get('linked_from', [])),
                'score': sr['score'],
                'match_kind': sr['match_kind'],
                'matched_anchors': sr['matched_anchors'],
                'matched_keywords': sr['matched_keywords'],
            })

        # 按分数降序
        scored.sort(key=lambda r: -r['score'])

        return {
            'results': scored[:max_results],
            'match_type': scored[0]['match_kind'] if scored else base.get('match_type', 'none'),
            'scores': [r['score'] for r in scored[:max_results]],
            'anchors': self.extract_anchors(keyword)[:5],
            'keyword': keyword,
        }

    def get_stats(self) -> dict:
        """获取统计信息"""
        stats = self.data.get("_stats", {})
        
        # 补充实时统计
        total_links = sum(len(p.get("links_to", [])) for p in self.pages)
        total_backlinks = sum(len(p.get("linked_from", [])) for p in self.pages)
        
        return {
            "total_pages": stats.get("total_pages", len(self.pages)),
            "core_pages": stats.get("core_pages", 0),
            "gate_pages": stats.get("gate_pages", 0),
            "isolated_pages": stats.get("isolated_pages", 0),
            "deadend_pages": stats.get("deadend_pages", 0),
            "total_links": total_links,
            "total_backlinks": total_backlinks,
            "by_domain": stats.get("by_domain", {}),
            "by_pinji": stats.get("by_pinji", {}),
            "by_status": stats.get("by_status", {}),
        }
    
    def search_by_summary(self, keyword: str) -> list:
        """在摘要中搜索"""
        self._ensure_fresh()
        results = []
        keyword_lower = keyword.lower()
        
        for page in self.pages:
            summary = page.get("summary", "").lower()
            if keyword_lower in summary:
                results.append(page)
        
        return results
    
    def search_by_content(self, keyword: str) -> list:
        """
        搜索页面内容（优先从索引 content 字段，无索引时回退到磁盘读取）
        """
        self._ensure_fresh()
        results = []
        keyword_lower = keyword.lower()
        
        for page in self.pages:
            # 优先从索引的 content 字段读取（避免磁盘 IO）
            content = page.get("content")
            if content:
                if keyword_lower in content.lower():
                    results.append(page)
                continue
            
            # 回退到磁盘读取（兼容旧索引）
            file_path = os.path.join(self.vault_path, page["path"] + ".md")
            if not os.path.exists(file_path):
                continue
            
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    disk_content = f.read().lower()
                
                if keyword_lower in disk_content:
                    results.append(page)
            except:
                pass
        
        return results


def create_engine(vault_path: str = None) -> MemoryEngine:
    """创建记忆引擎实例"""
    return MemoryEngine(vault_path)


