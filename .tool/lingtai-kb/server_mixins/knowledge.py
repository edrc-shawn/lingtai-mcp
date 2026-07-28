# -*- coding: utf-8 -*-
"""知识库查询 mixin — 核心检索+图扩散（v2: 日志回溯自动追加）"""
import os
import re
import sys
from pathlib import Path
from . import concept_collision
from decorators import tool

# ═══ 搜索缓存（TTL LRU，无外部依赖）═══
import time
from collections import OrderedDict


class _SearchCache:
    """knowledge_search 结果缓存，仅缓存丹房命中。
    
    - TTL: 300 秒（5 分钟）
    - 容量: 128 条
    - 淘汰策略: LRU
    - 线程安全: 通过 GIL 保证（CPython）
    """
    def __init__(self, maxsize: int = 128, ttl: int = 300):
        self.maxsize = maxsize
        self.ttl = ttl
        self._cache: OrderedDict = OrderedDict()

    def _make_key(self, keyword: str, category: str, hops: int, related_limit: int, mode: str = "standard") -> tuple:
        return (keyword, category, hops, related_limit, mode)

    def get(self, keyword: str, category: str, hops: int, related_limit: int, mode: str = "standard"):
        key = self._make_key(keyword, category, hops, related_limit, mode)
        if key not in self._cache:
            return None
        entry = self._cache[key]
        if time.time() - entry['time'] > self.ttl:
            del self._cache[key]
            return None
        self._cache.move_to_end(key)  # LRU 提升
        return entry['value']

    def set(self, keyword: str, category: str, hops: int, related_limit: int, mode: str = "standard", value: dict = None):
        key = self._make_key(keyword, category, hops, related_limit, mode)
        self._cache[key] = {'value': value, 'time': time.time()}
        while len(self._cache) > self.maxsize:
            self._cache.popitem(last=False)

    def clear(self):
        self._cache.clear()

    @property
    def size(self) -> int:
        return len(self._cache)


# 全局缓存实例（进程内共享）
_search_cache = _SearchCache()

# 操作历史类问题的特征关键词（精确化）
# 已移除 '历史''变化''变更''改动''之前' 等泛化词——
# 它们在普通知识查询（"历史文化""什么变化"）中高频误触发全量日志扫描，
# 而真正的操作历史问题由 '上次/什么时候/何时/怎么改/谁/日志/操作记录' 等精确词覆盖。
_HISTORY_KEYWORDS = frozenset([
    '上次', '什么时候', '怎么改', '什么时候改',
    '操作记录', '日志', '之前怎么', '何时', '何人', '谁', '做了什么',
    'what changed', 'when did', 'how did', 'what happened',
])


def _is_operation_history(keyword: str) -> bool:
    """检测关键词是否指向操作历史类问题"""
    kw = keyword.lower().strip()
    for hk in _HISTORY_KEYWORDS:
        if hk in kw:
            return True
    return False


from .question_dissolve import dissolve_question
from logger import get_logger

# 简繁归一 + 同义词映射（提升 knowledge_search 召回，借鉴 llmwiki alias resolution）
_TRAD_TO_SIMP = {
    '靈':'灵','臺':'台','網':'网','學':'学','體':'体','識':'识','來':'来','關':'关',
    '實':'实','與':'与','進':'进','應':'应','對':'对','說':'说','語':'语','變':'变',
    '書':'书','問':'问','題':'题','義':'义','產':'产','內':'内','處':'处','開':'开',
    '發':'发','們':'们','長':'长','門':'门','見':'见','覺':'觉','連':'连','參':'参',
    '觀':'观','統':'统','紀':'纪','終':'终','經':'经','過':'过','這':'这','還':'还',
    '會':'会','後':'后','點':'点','維':'维','證':'证','雜':'杂','頁':'页','頻':'频',
    '強':'强','錢':'钱','東':'东','車':'车','馬':'马','鳥':'鸟','魚':'鱼','時':'时',
    '間':'间','個':'个','從':'从','兩':'两','雲':'云','電':'电','腦':'脑','話':'话',
    '認':'认','報':'报','類':'类','總':'总','動':'动','態':'态','庫':'库','氣':'气',
    '歲':'岁','歷':'历','爐':'炉','燒':'烧','煙':'烟','熱':'热','愛':'爱','爾':'尔',
    '當':'当','國':'国','圖':'图','團':'团','圓':'圆','場':'场','塊':'块','壞':'坏',
    '復':'复','備':'备','專':'专','嚴':'严','豐':'丰','測':'测','減':'减','溫':'温',
    '濕':'湿','環':'环','護':'护','礎':'础','禮':'礼','積':'积','範':'范','編':'编',
    '練':'练','職':'职','聯':'联','腳':'脚','興':'兴','舉':'举','薦':'荐','藝':'艺',
    '號':'号','蟲':'虫','補':'补','視':'视','詞':'词','評':'评','讀':'读','負':'负',
    '費':'费','質':'质','運':'运','遊':'游','達':'达','遠':'远','遷':'迁','醫':'医',
    '鐵':'铁','錯':'错','闆':'板','階':'阶','際':'际','陸':'陆','陳':'陈','陰':'阴',
    '陽':'阳','離':'离','難':'难','項':'项','順':'顺','領':'领','頭':'头','風':'风',
    '飛':'飞','飲':'饮','養':'养','劃':'划',
}
_SYNONYM_MAP = {
    # 领域同义词（谨慎，避免误命中）
    '大模型': 'LLM',
    '大語言模型': 'LLM',
    '大型語言模型': 'LLM',
}


def _normalize_alias(keyword: str) -> str:
    """简繁归一 + 同义词映射，提升 knowledge_search 召回（借鉴 llmwiki alias resolution）

    先整词同义词替换（避免破坏复合词），再逐字简繁归一。
    返回归一后的查询词；映射表为空时原样返回，不引入副作用。
    """
    if not keyword:
        return keyword
    kw = keyword.strip()
    if kw in _SYNONYM_MAP:
        return _SYNONYM_MAP[kw]
    return ''.join(_TRAD_TO_SIMP.get(ch, ch) for ch in kw)


# ═══ P3：证据契约 ═══
# 每个搜索结果携带证据类型标签 + create_safety 信号，
# 避免调用者凭模糊分数判断是否该创建新页面。

EVIDENCE_TYPES = {
    "exact_title_match": "查询词完整匹配页面标题",
    "high_vector_match": "向量相似度高或关键词精确命中（score≥2.0）",
    "keyword_exact": "关键词精确匹配正文（score>0且<2.0）",
    "weak_semantic": "语义关联较弱（score≤0）",
    "graph_relation": "通过知识图谱关系关联",
}


def _classify_evidence(keyword: str, page: dict, relevance: dict) -> list:
    """推断一条搜索结果的证据类型

    Args:
        keyword: 查询词（已归一化）
        page: 页面元数据 dict
        relevance: score_relevance() 返回的评分结果

    Returns:
        list[str]: 证据类型标签列表（按优先级降序）
    """
    kw_lower = keyword.lower().strip()
    title_lower = page.get("title", "").lower().strip()
    score = relevance.get("score", 0)
    match_kind = relevance.get("match_kind", "none")

    evidence = []

    # 精确标题匹配（最高优先级）
    if kw_lower == title_lower or kw_lower == title_lower.replace("：", ":"):
        evidence.append("exact_title_match")

    # 高相关度
    if match_kind in ("exact", "anchor", "keyword") or score >= 2.0:
        evidence.append("high_vector_match")
    elif score > 0:
        evidence.append("keyword_exact")
    else:
        evidence.append("weak_semantic")

    return evidence


def _determine_create_safety(evidence: list, keyword: str, title: str) -> str:
    """根据证据类型判断创建新页的安全性

    Returns:
        "exists":   页面已存在（同标题/强匹配），应拒绝创建
        "probable": 很可能存在，建议检查后创建
        "unknown":  自由创建
    """
    if "exact_title_match" in evidence:
        return "exists"
    if "high_vector_match" in evidence:
        kw_lower = keyword.lower().strip()
        if kw_lower in title.lower():
            return "exists"
        return "probable"
    if "keyword_exact" in evidence:
        kw_lower = keyword.lower().strip()
        if kw_lower in title.lower():
            return "probable"
    return "unknown"


def _append_log_results(result: dict, logs: dict) -> dict:
    """将日志搜索结果附加到知识搜索结果中"""
    if logs and logs.get("total_matches", 0) > 0:
        result["_log_auto_appended"] = True
        result["_log_match_count"] = logs["total_matches"]
        result["_log_results"] = [
            {"source": r["source"], "content": r.get("content", "")[:200]}
            for r in logs.get("results", [])[:5]
        ]
    else:
        result["_log_auto_appended"] = False
        result["_log_match_count"] = 0
    return result


def _bridge_arbitrate(result: dict, memories: list) -> dict:
    """知识←→记忆双轴裁决（替代 _append_memory_hits 的一刀切记忆覆盖）

    按 AGENTS-appendix.md 规则⑤双轴裁决表实现：
    丹房品级 × 记忆置信度 → 自动断胜出方。
    丹房精确命中 + 高置信(>=0.8)时跳过记忆搜索（省 I/O）。

    注意：result 必须已含 _meta_confidence（由 _compute_meta_confidence 先行计算）。
    """
    # 阶段1：知识质量判断
    meta_conf = result.get("_meta_confidence", 0)
    results = result.get("results", [])
    evidence = results[0].get("evidence", []) if results else []
    grade = results[0].get("grade", "下品") if results else "下品"
    is_exact = "exact_title_match" in evidence

    # 精确命中+高置信 → 跳过记忆，零开销
    if meta_conf >= 0.8 and is_exact:
        result["memory_hits"] = []
        result["_bridge_note"] = ""
        result["_bridge_winner"] = "knowledge"
        result["_bridge_reason"] = "丹房精确命中（高置信），跳过记忆搜索"
        return result

    # 阶段2：有记忆命中 → 按双轴表裁决
    if memories:
        best_mem = max(memories, key=lambda m: m.get("current_confidence", 0))
        mem_conf = best_mem.get("current_confidence", 0)

        table = {"上品": 0.95, "中品": 0.70, "下品": 0.50}
        threshold = table.get(grade, 0.70)

        if mem_conf >= threshold:
            winner = "memory"
        else:
            winner = "knowledge"

        result["memory_hits"] = [
            {
                "memory_id": m.get("id", ""),
                "content": m.get("content", "")[:200],
                "confidence": round(m.get("current_confidence", 0), 2),
                "tags": m.get("tags", []),
                "branch": m.get("branch", ""),
            }
            for m in memories[:5]
        ]
        result["_bridge_note"] = (
            f"知识←→记忆桥接·双轴裁决：{grade}知识(conf={meta_conf}) "
            f"vs 记忆(conf={mem_conf:.2f}) → {winner}胜出"
        )
        result["_bridge_winner"] = winner
        result["_bridge_reason"] = (
            f"{grade}知识 vs 记忆(conf={mem_conf:.2f})，"
            f"{'记忆胜出（超阈值' + str(threshold) + '）' if winner == 'memory' else '知识胜出（未达阈值' + str(threshold) + '）'}"
        )
    else:
        result["memory_hits"] = []
        result["_bridge_note"] = ""
        result["_bridge_winner"] = "knowledge"
        result["_bridge_reason"] = "无相关记忆命中"

    return result


def _log_search_stats(result: dict) -> None:
    """记录本次搜索的模式+结果数到 search_stats.jsonl，供 skillopt harvest 消费"""
    import os, json
    from datetime import datetime
    try:
        vault = os.environ.get("LINGTAI_VAULT", r".")
        path = os.path.join(vault, ".tool", "lingtai-kb", "logs", "search_stats.jsonl")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        entry = {
            "timestamp": datetime.now().isoformat(),
            "keyword": result.get("keyword", "")[:30],
            "fallback_source": result.get("fallback_source", "none"),
            "direct_matches": result.get("direct_matches", 0),
            "related_knowledge": result.get("related_knowledge", 0),
            "total_results": result.get("direct_matches", 0) + result.get("related_knowledge", 0),
        }
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        log.debug("suppressed", exc_info=True)


def _compute_meta_confidence(result: dict) -> dict:
    """计算并附加检索质量自评（_meta_confidence），运行时计算。
    
    评估维度：
    - fallback_source 深度（丹房 > 原料 > 联网 > 全文 > 无结果）
    - 结果数量
    - 丹房源的额外加分
    """
    source_depth = {
        "danfang": 1.0, "raw": 0.7, "raw+web": 0.6,
        "external_ref": 0.6, "web": 0.5, "fulltext": 0.4, "none": 0.0,
    }
    fallback = result.get("fallback_source", "none")
    base = source_depth.get(fallback, 0.0)
    
    direct = result.get("direct_matches", 0)
    related = result.get("related_knowledge", 0)
    total = direct + related
    
    # 结果数量加分：0=0, 1-2=+0.1, 3-5=+0.2, 6+=+0.25
    quantity_bonus = min(0.25, total * 0.05)
    
    # 丹房源且有结果 → 额外加分
    danfang_bonus = 0.15 if (fallback == "danfang" and direct > 0) else 0.0
    
    overall = min(1.0, base + quantity_bonus + danfang_bonus)
    result["_meta_confidence"] = round(overall, 2)
    # 降级标记：非丹房源即为降级结果，客户端可据此判断是否重试或提示用户
    result["degraded"] = (fallback != "danfang")

    # 记录搜索日志（供 skillopt harvest 消费）
    _log_search_stats(result)
    return result

log = get_logger(__name__)

class KnowledgeMixin:
    @tool(readonly=True, write=False, category="knowledge", system=False, name="knowledge_search")
    def query(self, keyword: str, hops: int = 2, category: str = "", related_limit: int = 5, mode: str = "standard") -> dict:
        """
        关键词检索丹房知识页（含图扩散关联）。四层回退：丹房→原料→联网→全文。
        场景：有明确关键词、需要原始页面列表自行判断时；查操作历史/日志时（自动追加日志检索）。
        区别：要合成回答用 knowledge_synthesize；要注入上下文片段用 knowledge_inject；搜非丹房目录用 fulltext_search；不确定关键词想逛知识网络用 knowledge_explore。

        Args:
            keyword: 搜索关键词
            hops: 图扩散跳数（默认2）
            category: 域分类筛选（如"00-思考与认知"，可选）
            related_limit: 图扩散关联结果返回上限（默认5，设0则不返回related）
            mode: "standard"（完整管线，默认）| "quick"（仅丹房直接匹配，跳过图扩散和记忆桥接）

        Returns:
            dict: 查询结果，含 fallback_source 字段标记数据来源
        """
        # 简繁/同义词归一，提升召回（借鉴 llmwiki alias resolution）
        keyword = _normalize_alias(keyword)

        # 缓存命中检查（仅非操作历史类问题）
        if not _is_operation_history(keyword):
            cached = _search_cache.get(keyword, category, hops, related_limit, mode)
            if cached is not None:
                return cached

        # 学习层：记录查询 + 推导域兴趣
        self.user_profile.record_query(keyword)

        # 条件分支预检：操作历史类问题
        _log_needed = _is_operation_history(keyword)

        # ═══ 四路并行预检：操作日志(条件) + 记忆银行 + 丹房布尔检索 + BM25向量混合 ═══
        from concurrent.futures import ThreadPoolExecutor
        _log_search_result = None
        _memory_matches = []
        query_result = {}
        _hybrid_results = []
        with ThreadPoolExecutor(max_workers=4) as pool:
            f_logs = pool.submit(
                self.search_logs, keyword=keyword, days=30
            ) if _log_needed else None
            f_mem = pool.submit(self._search_memory_bank, keyword)
            f_danfang = pool.submit(self.memory.query, keyword)
            f_hybrid = pool.submit(self._hybrid_danfang_search, keyword)

            if f_logs:
                try:
                    _log_search_result = f_logs.result()
                except Exception:
                    _log_search_result = {"total_matches": 0}
            _memory_matches = f_mem.result()
            query_result = f_danfang.result()
            try:
                _hybrid_results = f_hybrid.result()
            except Exception:
                log.debug("hybrid search suppressed", exc_info=True)

        direct_results = query_result.get("results", []) if isinstance(query_result, dict) else query_result

        # ═══ RRF 融合：布尔匹配 + BM25 + 向量 ═══
        if _hybrid_results:
            direct_results = self._rrf_merge(keyword, direct_results, _hybrid_results)
        
        if direct_results:
            # 丹房命中
            for r in direct_results:
                d = r.get("domain", "")
                if d:
                    self.user_profile.record_interest(d)

            if category:
                direct_results = [r for r in direct_results if r.get("domain", "") == category]
            
            # 排序：有 RRF 融合时保持融合序；否则用 score_relevance（兼容旧路径）
            if not _hybrid_results:
                direct_results.sort(
                    key=lambda p: -self.memory.score_relevance(keyword, p)['score'] * (
                        1.15 if '编译真理' in p.get("summary", "") or '编译真理' in p.get("title", "") else 1.0
                    )
                )

            # ═══ quick 模式：跳过图扩散、证据契约、记忆桥接，直接返回 ═══
            if mode == "quick":
                quick_results = [
                    {
                        "path": p["path"],
                        "title": p["title"],
                        "summary": p.get("summary", "")[:100],
                        "domain": p.get("domain", ""),
                        "tags": p.get("tags", []),
                        "source_label": "灵台·丹房",
                    }
                    for p in direct_results[:10]
                ]
                result = {
                    "keyword": keyword,
                    "fallback_source": "danfang",
                    "mode": "quick",
                    "direct_matches": len(direct_results),
                    "related_knowledge": 0,
                    "results": quick_results,
                    "related": [],
                }
                _search_cache.set(keyword, category, hops, related_limit, mode, result)
                return result

            # ═══ standard 模式：完整图扩散 + 证据契约 + 记忆桥接 ═══
            graph_results = self.memory.search_graph(keyword, hops=hops, start_pages=direct_results)
            self.perception_stats_monitor.record_rule5(True)

            # 证据契约：为每条结果附加证据标签 + create_safety + last_updated
            def _enrich_result(p: dict, is_related: bool = False) -> dict:
                rel = self.memory.score_relevance(keyword, p)
                ev = _classify_evidence(keyword, p, rel)
                if is_related:
                    ev.append("graph_relation")
                return {
                    "path": p["path"],
                    "title": p["title"],
                    "summary": p.get("summary", "")[:100],
                    "domain": p.get("domain", ""),
                    "tags": p.get("tags", []),
                    "source_label": "灵台·丹房",
                    "evidence": ev,
                    "create_safety": _determine_create_safety(ev, keyword, p.get("title", "")),
                    "last_updated": p.get("date", ""),
                }

            enriched_direct = [_enrich_result(p) for p in direct_results[:10]]
            enriched_related = [_enrich_result(p, is_related=True) for p in graph_results[:related_limit]] if related_limit > 0 else []

            result = _bridge_arbitrate(_compute_meta_confidence(_append_log_results({
                "keyword": keyword,
                "fallback_source": "danfang",
                "degraded_reason": "",  # 首层命中，无降级
                "direct_matches": len(direct_results),
                "related_knowledge": len(graph_results),
                "results": enriched_direct,
                "related": enriched_related,
            }, _log_search_result)), _memory_matches)
            # 丹房命中 → 写入缓存
            _search_cache.set(keyword, category, hops, related_limit, mode, result)
            return result
        
        # ═══ 第二层：原料回退 ═══
        raw_result = self.memory.search_raw(keyword)
        raw_matches = raw_result.get("results", [])
        if raw_matches:
            self.perception_stats_monitor.record_rule5(True)
            top_score = raw_matches[0].get("score", 0)
            raw_results = [
                {
                    "path": r["path"],
                    "title": r["title"],
                    "summary": r.get("summary", "")[:100],
                    "domain": "原料",
                    "tags": [],
                    "score": r.get("score", 0),
                    "status": r.get("status", ""),
                    "source_label": "灵台·原料",
                    "evidence": ["keyword_exact"] if keyword.lower() in r.get("title", "").lower() else ["weak_semantic"],
                    "create_safety": "unknown",
                    "last_updated": r.get("date", ""),
                }
                for r in raw_matches[:10]
            ]
            
            # 置信度足够（最高分 ≥ 4.0）→ 纯原料返回
            if top_score >= 4.0:
                return _bridge_arbitrate(_compute_meta_confidence(_append_log_results({
                    "keyword": keyword,
                    "fallback_source": "raw",
                    "degraded_reason": "丹房层无匹配，降级到原料层",
                    "direct_matches": len(raw_matches),
                    "related_knowledge": 0,
                    "results": raw_results,
                    "related": [],
                }, _log_search_result)), _memory_matches)
            
            # 置信度不足 → 先查外部参考，再联网补充
            try:
                # 智能推荐器：解析 SKILL.md frontmatter，按名称+描述+触发词多维匹配排序
                ext_recs = self._recommend_external_tools_for_query(keyword=keyword, max_results=5)
                if ext_recs:
                    self.perception_stats_monitor.record_rule5(True)
                    return _bridge_arbitrate(_compute_meta_confidence(_append_log_results({
                        "keyword": keyword,
                        "fallback_source": "external_ref",
                        "degraded_reason": f"丹房层无匹配，原料层置信度不足，降级到外部参考（推荐 {len(ext_recs)} 个匹配 Skill）",
                        "direct_matches": len(ext_recs),
                        "related_knowledge": 0,
                        "results": [
                            {
                                "path": r["path"],
                                "title": f"{r['name']} ({r['repo']})",
                                "summary": f"[匹配 {r['score']}分] {r['description'][:80]}",
                                "domain": "外部参考",
                                "tags": [],
                                "source_label": f"灵台·外部参考·{r['repo']}",
                                "evidence": ["keyword_exact"],
                                "create_safety": "unknown",
                                "last_updated": "",
                            }
                            for r in ext_recs
                        ],
                        "related": [],
                        "recommendations": ext_recs,
                    }, _log_search_result)), _memory_matches)
            except Exception:
                log.debug("suppressed", exc_info=True)
            
            # 外部参考也无 → 联网补充（带超时保护，10s 上限）
            try:
                from concurrent.futures import ThreadPoolExecutor
                with ThreadPoolExecutor(max_workers=1) as pool:
                    web_future = pool.submit(self.web_search, keyword=keyword, max_results=5)
                    web_result = web_future.result(timeout=10)
                web_items = web_result.get("results", [])
                if web_items:
                    web_label = web_result.get("source", "").split(" (")[0] if " (" in web_result.get("source", "") else "联网搜索"
                    raw_results.append({
                        "path": "",
                        "title": "── 以下为联网补充 ──",
                        "summary": "",
                        "domain": "",
                        "tags": [],
                        "score": 0,
                        "status": "divider",
                        "source_label": "",
                    })
                    for r in web_items[:5]:
                        raw_results.append({
                            "path": r.get("url", ""),
                            "title": r.get("title", ""),
                            "summary": r.get("content", "")[:100],
                            "domain": "联网搜索",
                            "tags": [],
                            "score": 0,
                            "status": "web",
                            "source_label": web_label,
                        })
                    return _bridge_arbitrate(_compute_meta_confidence(_append_log_results({
                        "keyword": keyword,
                        "fallback_source": "raw+web",
                        "degraded_reason": "丹房+外部参考无匹配，原料层置信度不足，补充联网搜索",
                        "direct_matches": len(raw_matches) + len(web_items),
                        "related_knowledge": 0,
                        "results": raw_results,
                        "related": [],
                        "web_source": web_result.get("source", ""),
                    }, _log_search_result)), _memory_matches)
            except Exception:
                log.debug("suppressed", exc_info=True)
            
            # 联网失败 → 只返原料（虽然低置信度）
            return _bridge_arbitrate(_compute_meta_confidence(_append_log_results({
                "keyword": keyword,
                "fallback_source": "raw",
                "degraded_reason": "丹房+外部参考无匹配，原料层置信度不足，联网搜索失败",
                "direct_matches": len(raw_matches),
                "related_knowledge": 0,
                "results": raw_results,
                "related": [],
            }, _log_search_result)), _memory_matches)
        
        # ═══ L2.5：外部参考回退（智能推荐器：多维匹配+排序）═══
        try:
            ext_recs = self._recommend_external_tools_for_query(keyword=keyword, max_results=5)
            if ext_recs:
                self.perception_stats_monitor.record_rule5(True)
                return _bridge_arbitrate(_compute_meta_confidence(_append_log_results({
                    "keyword": keyword,
                    "fallback_source": "external_ref",
                    "degraded_reason": f"丹房+原料层均无匹配，降级到外部参考（推荐 {len(ext_recs)} 个匹配 Skill）",
                    "direct_matches": len(ext_recs),
                    "related_knowledge": 0,
                    "results": [
                        {
                            "path": r["path"],
                            "title": f"{r['name']} ({r['repo']})",
                            "summary": f"[匹配 {r['score']}分] {r['description'][:80]}",
                            "domain": "外部参考",
                            "tags": [],
                            "source_label": f"灵台·外部参考·{r['repo']}",
                            "evidence": ["keyword_exact"],
                            "create_safety": "unknown",
                            "last_updated": "",
                        }
                        for r in ext_recs
                    ],
                    "related": [],
                    "recommendations": ext_recs,
                }, _log_search_result)), _memory_matches)
        except Exception:
            log.debug("suppressed", exc_info=True)
        
        # ═══ 第三层：联网回退（AnySearch → Tavily，10s 超时）═══
        try:
            from concurrent.futures import ThreadPoolExecutor, TimeoutError
            with ThreadPoolExecutor(max_workers=1) as pool:
                web_future = pool.submit(self.web_search, keyword=keyword, max_results=5)
                web_result = web_future.result(timeout=10)
            if web_result.get("results"):
                web_label = web_result.get("source", "").split(" (")[0] if " (" in web_result.get("source", "") else "联网搜索"
                self.perception_stats_monitor.record_rule5(True)
                return _bridge_arbitrate(_compute_meta_confidence(_append_log_results({
                    "keyword": keyword,
                    "fallback_source": "web",
                    "degraded_reason": "丹房+原料+外部参考均无匹配，降级到联网搜索",
                    "direct_matches": len(web_result["results"]),
                    "related_knowledge": 0,
                    "results": [
                        {
                            "path": r.get("url", ""),
                            "title": r.get("title", ""),
                            "summary": r.get("content", "")[:100],
                            "domain": "联网搜索",
                            "tags": [],
                            "source_label": web_label,
                            "evidence": ["keyword_exact"],
                            "create_safety": "unknown",
                            "last_updated": "",
                        }
                        for r in web_result["results"][:10]
                    ],
                    "related": [],
                    "web_source": web_result.get("source", ""),
                }, _log_search_result)), _memory_matches)
        except Exception:
            log.debug("suppressed", exc_info=True)
        
        # ═══ 第四层：非丹房资产全文搜索（fulltext_search）═══
        try:
            ft_result = self.fulltext_search(keyword=keyword, scope="all", max_results=5)
            if ft_result.get("found"):
                self.perception_stats_monitor.record_rule5(True)
                return _bridge_arbitrate(_compute_meta_confidence(_append_log_results({
                    "keyword": keyword,
                    "fallback_source": "fulltext",
                    "degraded_reason": "丹房+原料+外部参考+联网均无匹配，降级到全文搜索",
                    "direct_matches": ft_result["total_matches"],
                    "related_knowledge": 0,
                    "results": [
                        {
                            "path": r.get("path", ""),
                            "title": r.get("snippet", "")[:80],
                            "summary": r.get("snippet", "")[:100],
                            "domain": r.get("source_label", "非丹房资产"),
                            "tags": [],
                            "source_label": r.get("source_label", "灵台·外部"),
                            "evidence": ["keyword_exact"],
                            "create_safety": "unknown",
                            "last_updated": "",
                        }
                        for r in ft_result.get("results", [])[:10]
                    ],
                    "related": [],
                }, _log_search_result)), _memory_matches)
        except Exception:
            log.debug("suppressed", exc_info=True)
        
        # 四层均为空
        self.perception_stats_monitor.record_rule5(False)
        return _bridge_arbitrate(_compute_meta_confidence(_append_log_results({
            "keyword": keyword,
            "fallback_source": "none",
            "degraded_reason": "四层全空，知识库未涵盖",
            "direct_matches": 0,
            "related_knowledge": 0,
            "results": [],
            "related": [],
        }, _log_search_result)), _memory_matches)

    @tool(readonly=True, write=False, category="knowledge", system=False, name="question_dissolve")
    def question_dissolve(self, question: str) -> dict:
        """
        消解漏斗 — 在知识检索前检测问题是否需要重新定义

        借鉴 dbskill 消解漏斗设计：75% 的问题在检索前被消解掉。
        本工具纯规则匹配，不调用 LLM，轻量高效。
        返回消解信号（missing_baseline/hidden_assumption/confirmation_bias/
        missing_context/too_short/premature_commercialization 等），
        以及建议操作。

        使用方式：在 knowledge_search 之前调用，若 dissolved=True
        则先和用户确认问题前提，再决定是否检索。

        Args:
            question: 用户问题原文

        Returns:
            dict: {needs_dissolve, signals, signal_count, has_high, suggested_action, dissolved}
        """
        return dissolve_question(question)

    @tool(readonly=True, write=False, category="knowledge", system=False, name="knowledge_synthesize")
    def synthesize(self, keyword: str, detail: str = "standard") -> dict:
        """
        知识合成——检索+生成带引用的合成回答+差距分析。
        场景：需要知识库"回答一个问题"而非"给我页面列表"时；知识类/分析类问题首选。
        区别：要原始页面列表自行分析用 knowledge_search；只需注入片段到上下文用 knowledge_inject。

        knowledge_search 返回页面列表由你自己读；knowledge_synthesize
        返回一篇带引用的合成正文，并诚实标注知识库未覆盖的方面、
        过时信息、矛盾点。

        Args:
            keyword: 问题/搜索关键词
            detail: 详细程度（quick=仅合成命中结果 / standard=加图扩散）

        Returns:
            dict: {clarification, synthesis, gaps, citations, outdated, contradictions, confidence, fallback_source, suggested_next}
                  clarification 为 null 时表示问题清晰无歧义；
                  非 null 时包含 is_ambiguous/interpretations/recommended
                  suggested_next 为延伸方向列表 [{type, label, target, why}]
        """
        from .synthesis_prompt import build_synthesis_prompt
        import json, os

        # Step 1: 检索（复用现有四层回退）
        hops = 1 if detail == "quick" else 2
        search_result = self.query(keyword=keyword, hops=hops)

        fallback_source = search_result.get("fallback_source", "none")
        direct_results = search_result.get("results", [])
        related_results = search_result.get("related", [])
        all_results = direct_results + related_results

        # 无结果 → 直接返回
        if not all_results:
            return {
                "keyword": keyword,
                "clarification": None,
                "synthesis": "知识库未涵盖该主题。",
                "gaps": [{"aspect": keyword, "severity": "high"}],
                "citations": [],
                "outdated": [],
                "contradictions": [],
                "confidence": "none",
                "fallback_source": fallback_source,
                "suggested_next": [],
                "_note": "无检索结果，建议尝试联网搜索或调整关键词",
            }

        # Step 2: 直接读文件获取页面正文（最多前 5 页）
        page_contents = []
        for r in all_results[:5]:
            page_path = r.get("path", "")
            if not page_path:
                continue
            # 去掉可能含的 .md 后缀
            clean_path = page_path.replace('.md', '')
            abs_path = os.path.join(self.vault_path, (clean_path + '.md').replace("/", os.sep))
            if not os.path.exists(abs_path):
                # path 本身可能已含 .md
                abs_path = os.path.join(self.vault_path, page_path.replace("/", os.sep))
            content = "(文件不存在)"
            if os.path.exists(abs_path):
                try:
                    with open(abs_path, 'r', encoding='utf-8') as f:
                        raw = f.read()
                    # 跳过 frontmatter
                    if raw.startswith('---'):
                        parts = raw.split('---', 2)
                        if len(parts) >= 3:
                            raw = parts[2]
                    content = raw[:4000]
                except Exception as e:
                    content = f"(读取失败: {e})"
            page_contents.append({
                "path": clean_path,
                "title": r.get("title", page_path.split("/")[-1]),
                "content": content,
            })

        # Step 3: 组装 prompt 并调 LLM 合成
        prompt = build_synthesis_prompt(keyword, search_result, page_contents)

        llm_text = ""
        try:
            # 尝试缓存 LLMReasoning 实例（避免每次新建的初始化开销）
            if not hasattr(self, '_synthesize_llm') or self._synthesize_llm is None:
                from llm_reasoning import LLMReasoning
                self._synthesize_llm = LLMReasoning()
            llm_text = self._synthesize_llm._call_llm(prompt, max_tokens=2048, action="synthesize")
        except Exception as e:
            return {
                "keyword": keyword,
                "clarification": None,
                "synthesis": f"LLM 合成失败：{e}。请直接查看 knowledge_search 原始结果。",
                "gaps": [],
                "citations": [],
                "outdated": [],
                "contradictions": [],
                "confidence": "error",
                "fallback_source": fallback_source,
                "suggested_next": [],
                "_error": f"{type(e).__name__}: {e}",
            }

        # Step 4: 解析 JSON 输出
        try:
            # 尝试从 markdown 代码块中提取 JSON
            cleaned = llm_text.strip()
            if "```json" in cleaned:
                cleaned = cleaned.split("```json")[1].split("```")[0].strip()
            elif "```" in cleaned:
                cleaned = cleaned.split("```")[1].split("```")[0].strip()
            parsed = json.loads(cleaned)
        except (json.JSONDecodeError, IndexError):
            # JSON 解析失败，将 LLM 原始输出作为 synthesis
            return {
                "keyword": keyword,
                "clarification": None,
                "synthesis": llm_text,
                "gaps": [],
                "citations": [],
                "outdated": [],
                "contradictions": [],
                "confidence": "unparsed",
                "fallback_source": fallback_source,
                "suggested_next": [],
            }

        # Step 5: 组装返回
        return {
            "keyword": keyword,
            "clarification": parsed.get("clarification", None),
            "synthesis": parsed.get("synthesis", llm_text),
            "gaps": parsed.get("gaps", []),
            "citations": parsed.get("citations", []),
            "outdated": parsed.get("outdated", []),
            "contradictions": parsed.get("contradictions", []),
            "confidence": parsed.get("confidence", "medium"),
            "fallback_source": fallback_source,
            "suggested_next": parsed.get("suggested_next", []),
            "source_stats": {
                "direct_matches": search_result.get("direct_matches", 0),
                "related_knowledge": search_result.get("related_knowledge", 0),
            },
        }

    def _search_hook_summaries(self, keyword: str) -> list:
        """搜索钩子摘要卡片（近期记忆）"""
        vault = os.environ.get("LINGTAI_VAULT", r".")
        summaries_dir = os.path.join(vault, ".tool", "hook-summaries")
        if not os.path.isdir(summaries_dir):
            return []

        from datetime import datetime, timedelta
    
        results = []
        now = datetime.now()
        keyword_lower = keyword.lower()

        for fname in os.listdir(summaries_dir):
            fpath = os.path.join(summaries_dir, fname)
            if not fname.endswith('.md') or not os.path.isfile(fpath):
                continue

            # 只读最近 2 天
            try:
                date_part = fname.replace('.md', '')[:10]
                fdate = datetime.strptime(date_part, '%Y-%m-%d')
                if (now - fdate) > timedelta(days=2):
                    continue
            except ValueError:
                continue

            with open(fpath, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()

            # 关键词匹配
            if keyword_lower not in content.lower():
                continue

            lines = content.strip().split('\n')
            title = fname.replace('.md', '')
            summary = content[:200].replace('\n', ' ').strip()

            results.append({
                "path": f".tool/hook-summaries/{fname}",
                "title": title,
                "summary": summary,
                "date": date_part,
            })

        return results
    
    def search(self, keyword: str, search_content: bool = False) -> dict:
        """
        搜索页面内容
        
        Args:
            keyword: 搜索关键词
            search_content: 是否搜索页面内容（较慢）
        
        Returns:
            dict: 搜索结果
        """
        # 搜索摘要
        summary_results = self.memory.search_by_summary(keyword)
        
        # 搜索内容
        content_results = []
        if search_content:
            content_results = self.memory.search_by_content(keyword)
        
        # 合并去重
        all_paths = set()
        all_results = []
        
        for p in summary_results + content_results:
            if p["path"] not in all_paths:
                all_paths.add(p["path"])
                all_results.append(p)
        
        # 记录统计（规则5）
        completed = len(all_results) > 0
        self.perception_stats_monitor.record_rule5(completed)
        
        return {
            "keyword": keyword,
            "summary_matches": len(summary_results),
            "content_matches": len(content_results),
            "total_matches": len(all_results),
            "results": [
                {
                    "path": p["path"],
                    "title": p["title"],
                    "summary": p.get("summary", "")[:100],
                }
                for p in all_results[:20]
            ],
        }
    
    def analyze(self, page_path: str) -> dict:
        """
        分析页面链接
        
        Args:
            page_path: 页面路径（如 "丹房/00-思考与认知/含人量"）
        
        Returns:
            dict: 分析结果
        """
        # 获取相关页面
        related = self.memory.get_related_pages(page_path)
        
        # 获取潜在关联
        potential = self.auto_edge.find潜在关联(page_path)
        
        # 获取链接建议
        suggestions = self.auto_edge.get_link_suggestions(page_path)
        
        return {
            "page": page_path,
            "related_count": len(related),
            "related_pages": [
                {"path": p["path"], "title": p["title"]}
                for p in related[:10]
            ],
            "potential_count": len(potential),
            "potential_pages": [
                {"path": p["path"], "title": p["title"]}
                for p in potential[:10]
            ],
            "suggestions": suggestions[:5],
        }
    
    def related(self, page_path: str, max_results: int = 10) -> dict:
        """
        获取相关页面
        
        Args:
            page_path: 页面路径
            max_results: 最大结果数
        
        Returns:
            dict: 相关页面列表
        """
        related = self.memory.get_related_pages(page_path, max_results)
        
        return {
            "page": page_path,
            "count": len(related),
            "related": [
                {
                    "path": p["path"],
                    "title": p["title"],
                    "summary": p.get("summary", "")[:100],
                    "backlinks": len(p.get("linked_from", [])),
                }
                for p in related
            ],
        }
    
    @tool(readonly=True, write=False, category="knowledge", system=False, name="knowledge_stats")
    def stats(self) -> dict:
        """
        获取知识库统计
        
        Returns:
            dict: 统计信息
        """
        memory_stats = self.memory.get_stats()
        edge_analysis = self.auto_edge.analyze_links()
        
        return {
            "total_pages": memory_stats["total_pages"],
            "total_links": memory_stats["total_links"],
            "core_pages": memory_stats["core_pages"],
            "gate_pages": memory_stats["gate_pages"],
            "isolated_pages": memory_stats["isolated_pages"],
            "domains": memory_stats["by_domain"],
            "pinji": memory_stats["by_pinji"],
            "hub_pages": edge_analysis["hub_pages"][:5],
        }
    
    @tool(readonly=True, write=False, category="knowledge", system=True, name="knowledge_domains")
    def domains(self) -> dict:
        """
        获取域列表
        
        Returns:
            dict: 域列表和页面数
        """
        stats = self.memory.get_stats()
        
        return {
            "domains": [
                {"name": name, "count": count}
                for name, count in stats["by_domain"].items()
            ],
            "total": len(stats["by_domain"]),
        }
    
    @tool(readonly=False, write=True, category="system", system=False, name="system_refresh_index")
    def refresh_index(self, mode: str = "quick") -> dict:
        """
        增量重建丹房索引并刷新内存
        
        Args:
            mode: quick=增量（默认） / full=全量重建
        
        Returns:
            dict: 重建结果
        """
        import subprocess, time
        t0 = time.time()
        
        build_script = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "..", "scripts", "build_index.py"
        )
        build_script = os.path.normpath(build_script)
        
        if not os.path.isfile(build_script):
            return {"status": "error", "message": f"build_index.py not found: {build_script}"}
        
        cmd = [sys.executable, build_script]
        if mode == "full":
            cmd.append("--full")
        
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        
        try:
            result = subprocess.run(cmd, capture_output=True,
                                    timeout=120, env=env)
            stdout = result.stdout.decode('utf-8', errors='replace')
            stderr = result.stderr.decode('utf-8', errors='replace')
            elapsed = time.time() - t0
        except subprocess.TimeoutExpired:
            return {"status": "error", "message": "build_index.py timed out (120s)"}
        
        if result.returncode != 0:
            return {
                "status": "error",
                "message": f"build_index.py exited with code {result.returncode}",
                "stderr": stderr[:500],
            }
        
        # 刷新内存中的索引数据
        self.memory.refresh()
        
        # 清除旧查询缓存
        if hasattr(self.memory, '_query_cache'):
            self.memory._query_cache = {}
        
        elapsed = time.time() - t0
        output_lines = [l for l in stdout.strip().split('\n') if l.strip()]

        # 解析 schema 验收结果
        schema_violations = 0
        schema_total = 0
        for line in output_lines:
            m = re.match(r'⚠️\s+[Ss]chema 验收: (\d+) 条违规 / (\d+) 页', line)
            if m:
                schema_violations = int(m.group(1))
                schema_total = int(m.group(2))
            m2 = re.match(r'✅\s+[Ss]chema 验收: (\d+) 页全部通过', line)
            if m2:
                schema_total = int(m2.group(1))

        return {
            "status": "ok",
            "mode": mode,
            "elapsed": f"{elapsed:.1f}s",
            "schema_audit": {
                "pages_checked": schema_total or "?",
                "violations": schema_violations,
                "clean": schema_violations == 0,
            },
            "build_output": output_lines[-5:],
        }
    
    @tool(readonly=True, write=False, category="knowledge", system=True, name="knowledge_pages")
    def pages(self, domain: str = None, limit: int = 50) -> dict:
        """
        获取页面列表
        
        Args:
            domain: 域名（可选，不传则返回所有）
            limit: 最大返回数
        
        Returns:
            dict: 页面列表
        """
        if domain:
            pages = self.memory.get_pages_by_domain(domain)
        else:
            pages = self.memory.pages
        
        return {
            "domain": domain or "all",
            "count": len(pages),
            "pages": [
                {
                    "path": p["path"],
                    "title": p["title"],
                    "domain": p.get("domain", ""),
                    "pinji": p.get("pinji", ""),
                    "backlinks": len(p.get("linked_from", [])),
                }
                for p in pages[:limit]
            ],
        }
    
    def hebbian_stats(self) -> dict:
        """
        Hebbian 动态权重统计，查看共现边的权重分布
        
        Returns:
            dict: 权重统计
        """
        stats = self.memory.hebbian.get_stats()
        stats["decay_days"] = self.memory.hebbian.decay_days
        return stats

    @tool(readonly=True, write=False, category="knowledge", system=False, name="knowledge_compound")
    def compound(self, top_n: int = 20, min_weight: float = 0.0) -> dict:
        """
        知识复利：查看共现权重最高的知识边。反复被一起查询的页面边权越滚越大。
        
        Args:
            top_n: 返回条数（默认20）
            min_weight: 最低权重过滤（默认0，取全部）
        
        Returns:
            dict: 共现边列表 + 统计
        """
        edges = self.memory.hebbian.get_top_co_occurrences(top_n=top_n * 2)
        if min_weight > 0:
            edges = [(s, t, w) for s, t, w in edges if w >= min_weight]
        edges = edges[:top_n]
        return {
            "total_edges": len(self.memory.hebbian.weights),
            "returned": len(edges),
            "min_weight": min_weight,
            "edges": [
                {
                    "source": s.split("/")[-1].replace(".md", ""),
                    "target": t.split("/")[-1].replace(".md", ""),
                    "weight": round(w, 3),
                }
                for s, t, w in edges
            ],
            "stats": self.memory.hebbian.get_stats(),
        }

    @tool(readonly=True, write=False, category="knowledge", system=False, name="knowledge_heatmap")
    def heatmap(self, domain: str = None, top_n: int = 20) -> dict:
        """
        知识热度：查看页面被查询和引用的活跃度
        
        Args:
            domain: 域筛选（可选）
            top_n: 返回条数
        
        Returns:
            dict: 页面热度列表
        """
        pages = self.memory.pages
        if domain:
            pages = [p for p in pages if p.get("domain", "") == domain]
        
        scored = []
        for p in pages:
            backlinks = len(p.get("linked_from", []))
            links = len(p.get("links_to", []))
            # 热度分 = 入链 + 出链（简单加权）
            heat = backlinks + links
            scored.append({
                "path": p["path"],
                "title": p.get("title", ""),
                "domain": p.get("domain", ""),
                "pinji": p.get("pinji", ""),
                "backlinks": backlinks,
                "outlinks": links,
                "heat_score": heat,
            })
        
        scored.sort(key=lambda x: x["heat_score"], reverse=True)
        return {
            "domain": domain or "all",
            "total_pages": len(scored),
            "returned": min(top_n, len(scored)),
            "pages": scored[:top_n],
        }

    @tool(readonly=True, write=False, category="knowledge", system=True, name="knowledge_digest")
    def digest(self, hours: int = 48, max_results: int = 10, scope: str = "recent") -> dict:
        """
        知识消化协议（P3-4）——扫描丹房页，检测：
        1. 孤立新页（新创建但无入链）
        2. 断链（引用了不存在的页面）
        3. 近重复（高度相似的内容，建议互链或合并）

        Args:
            hours: 变动窗口（scope=recent 时生效，默认 48h）
            max_results: 每类最多返回数
            scope: "recent"（仅近期变动，默认）
                   "full"（全量扫描所有页）

        参考 Ombre Brain dream 协议。
        """
        import subprocess
        from datetime import datetime, timedelta
        vault = getattr(self, 'vault_path', None) or r"."
        since = (datetime.now() - timedelta(hours=hours)).strftime('%Y-%m-%dT%H:%M:%S')

        # 1. 获取近期修改的丹房页
        recent_paths = set()
        try:
            result = subprocess.run(
                ['git', 'log', '--since', since, '--name-only', '--pretty=format:', '--', '丹房/'],
                capture_output=True, text=True, timeout=10, cwd=vault)
            for line in result.stdout.strip().split('\n'):
                line = line.strip()
                if line.startswith('丹房/') and line.endswith('.md') and '/.' not in line:
                    recent_paths.add(line)
        except Exception:
            log.debug("suppressed", exc_info=True)
        # 也扫描文件 mtime
        try:
            import os
            cutoff = datetime.now().timestamp() - hours * 3600
            for root, dirs, files in os.walk(os.path.join(vault, '丹房')):
                dirs[:] = [d for d in dirs if not d.startswith('.')]
                for f in files:
                    if not f.endswith('.md'):
                        continue
                    fpath = os.path.join(root, f)
                    if os.path.getmtime(fpath) > cutoff:
                        rel = os.path.relpath(fpath, vault).replace('\\', '/')
                        recent_paths.add(rel)
        except Exception:
            log.debug("suppressed", exc_info=True)

        all_pages = getattr(self.memory, 'pages', []) if hasattr(self, 'memory') else []

        # scope="full" 时覆盖为全量页
        if scope == "full" and all_pages:
            recent_paths = {p.get("path", "") for p in all_pages if p.get("path", "")}

        # 2. 孤立新页
        isolated = []
        try:
            for page in all_pages:
                p = page.get("path", "")
                if p not in recent_paths:
                    continue
                if len(page.get("linked_from", [])) == 0:
                    isolated.append({
                        "path": p, "title": page.get("title", ""),
                        "domain": page.get("domain", ""),
                        "type": "孤立新页",
                        "suggestion": "需建立入链", })
        except Exception:
            log.debug("suppressed", exc_info=True)

        # 3. 断链
        broken_links = []
        try:
            existing_paths = {p.get("path", "") for p in all_pages}
            for page in all_pages:
                p = page.get("path", "")
                if p not in recent_paths:
                    continue
                for target in page.get("links_to", []):
                    tp = target if target.endswith('.md') else f"{target}.md"
                    if tp.startswith('丹房/') and tp not in existing_paths:
                        broken_links.append({
                            "source": p, "target": tp,
                            "title": page.get("title", ""),
                            "type": "断链", "suggestion": "目标页不存在", })
        except Exception:
            log.debug("suppressed", exc_info=True)

        # 4. 近重复
        near_dups = []
        try:
            by_domain = {}
            for page in all_pages:
                d = page.get("domain", "")
                if d:
                    by_domain.setdefault(d, []).append(page)
            for domain, pages in by_domain.items():
                titles = [(p.get("path", ""), p.get("title", "")) for p in pages
                          if p.get("path", "") in recent_paths]
                for i, (p1, t1) in enumerate(titles):
                    for p2, t2 in titles[i+1:]:
                        w1 = set(t1.lower().replace('：', ':').split())
                        w2 = set(t2.lower().replace('：', ':').split())
                        if len(w1) > 1 and len(w2) > 1:
                            j = len(w1 & w2) / len(w1 | w2)
                            if j > 0.5:
                                near_dups.append({
                                    "path_a": p1, "title_a": t1,
                                    "path_b": p2, "title_b": t2,
                                    "domain": domain, "similarity": round(j, 2),
                                    "type": "近重复",
                                    "suggestion": "内容高度重叠，建议合并或互链", })
        except Exception:
            log.debug("suppressed", exc_info=True)

        total = len(isolated) + len(broken_links) + len(near_dups)
        return {
            "digest_window_hours": hours,
            "recently_modified": len(recent_paths),
            "total_issues": total,
            "findings": {
                "isolated_pages": isolated[:max_results],
                "broken_links": broken_links[:max_results],
                "near_duplicates": near_dups[:max_results],
            },
            "summary": {"isolated": len(isolated), "broken_links": len(broken_links), "near_duplicates": len(near_dups)},
            "note": f"{'全量扫描' if scope == 'full' else f'最近 {hours}h 变动'}的 {len(recent_paths)} 页中，发现 {total} 个待消化项" if total
                    else f"{'全量扫描' if scope == 'full' else f'最近 {hours}h 变动'}的 {len(recent_paths)} 页无待消化项",
            "scope": scope,
        }

    @tool(readonly=True, write=False, category="concept", system=False)
    def lifecycle_scan(self, stale_days: int = 30, min_backlinks: int = 3, mode: str = "page") -> dict:
        """知识生命周期扫描——检测可降级/可清理的候选。

        mode="page"（默认）：丹房页面降级扫描（下品+少入链+陈旧）
        mode="raw"：原料冷度扫描（长期未提炼+无回链，对齐规则 16b）
        mode="both"：同时返回页面和原料两个维度

        Args:
            stale_days: 陈旧阈值（天，page 模式默认 30，raw 模式默认 60）
            min_backlinks: 最低入链数（仅 page 模式）
            mode: 扫描模式

        Returns:
            dict: 候选列表
        """
        if mode == "raw":
            return concept_collision.raw_coldness_scan(
                vault_path=self.vault_path,
                stale_days=stale_days if stale_days != 30 else 60,
                max_results=30,
            )
        elif mode == "both":
            page_result = concept_collision.lifecycle_scan(
                vault_path=self.vault_path,
                pages=self.memory.pages,
                stale_days=stale_days,
                min_backlinks=min_backlinks,
            )
            raw_result = concept_collision.raw_coldness_scan(
                vault_path=self.vault_path,
                stale_days=60,
                max_results=20,
            )
            return {
                "page_scan": page_result,
                "raw_scan": raw_result,
                "mode": "both",
            }
        return concept_collision.lifecycle_scan(
            vault_path=self.vault_path,
            pages=self.memory.pages,
            stale_days=stale_days,
            min_backlinks=min_backlinks,
        )

    @tool(readonly=True, write=False, category="concept", system=False)
    def concept_collide(
        self,
        top_n: int = 20,
        min_similarity: float = 0.6,
        max_similarity: float = 0.75,
        domain_filter: str = "",
        mode: str = "page",
        min_pages: int = 2,
    ) -> dict:
        """
        概念碰撞——跨域语义相似度检测，在 0.6-0.75 区间产出意外关联。

        mode="page"（默认）：页面级碰撞，基于丹房页 summary 嵌入。
        mode="concept"：概念级碰撞，基于 frontmatter 标签字段提取概念后嵌入。

        基于 bge-small-zh-v1.5 做语义嵌入，只比较跨域对，
        筛选「有关联但视角不同」的语义巧合——低于 0.6 是噪音，高于 0.75 是重复。
        纯只读扫描，不写任何数据。由灵识逐条判断后执行补链或提炼。

        Args:
            top_n: 返回碰撞对数量（默认 20）
            min_similarity: 最低相似度（默认 0.6）
            max_similarity: 最高相似度（默认 0.75）
            domain_filter: 限返回包含该域的碰撞对（可选）
            mode: 碰撞模式，"page"（页面级）或 "concept"（概念级）
            min_pages: 概念模式下，最低出现页数过滤（默认 2）

        Returns:
            dict: 碰撞结果。概念模式额外返回 concept_map
        """
        pages = self.memory.pages
        vault = self.vault_path

        if mode == "concept":
            return concept_collision.concept_collide_pages(
                vault_path=vault,
                pages=pages,
                top_n=top_n,
                min_sim=min_similarity,
                max_sim=max_similarity,
                min_pages=min_pages,
            )

        return concept_collision.collide(
            vault_path=vault,
            pages=pages,
            top_n=top_n,
            min_sim=min_similarity,
            max_sim=max_similarity,
            domain_filter=domain_filter,
        )

    # [已删除] concept_collide_apply + _save_collision_as_raw — 无差别补链无判断力

    @tool(readonly=True, write=False, category="page", system=False, name="page_link_suggest")
    def link_suggest(self, page_path: str, max_results: int = 10) -> dict:
        """
        自动链接建议：基于标签重叠+同域+关键词相似度推荐潜在链接
        
        Args:
            page_path: 页面路径（如 "丹房/00-思考与认知/含人量"）
            max_results: 最大建议数
        
        Returns:
            dict: 链接建议列表
        """
        candidates = self.auto_edge.find潜在关联(page_path, max_results=max_results)
        suggestions = self.auto_edge.get_link_suggestions(page_path, max_suggestions=max_results)

        return {
            "page": page_path,
            "candidates": [
                {
                    "path": p["path"],
                    "title": p.get("title", ""),
                    "domain": p.get("domain", ""),
                    "summary": p.get("summary", "")[:100] if p.get("summary") else "",
                }
                for p in candidates
            ],
            "suggestions": suggestions[:max_results],
        }

    @tool(readonly=True, write=False, category="general", system=False)
    def ingest_ripple(self, new_page: str, max_results: int = 10, auto_generate: bool = False) -> dict:
        """
        波及分析（Karpathy Ingest）——给定新丹房页，分析哪些已有页应更新交叉引用
        
        Args:
            new_page: 新丹房页路径（如 丹房/07-工具与AI/MemPalace AI记忆系统解析）
            max_results: 最大波及页数
        
        Returns:
            dict: 波及分析结果，含 impacted_pages 列表
        """
        import re, os, math
        
        # 清除 .md 后缀（path_map 的 key 不含 .md）
        lookup_path = new_page.replace('.md', '')
        
        # 1. 获取新页元数据
        page = self.memory.get_page_by_path(lookup_path)
        if not page:
            return {
                "source_page": new_page,
                "error": f"页面不存在: {new_page}",
                "ripple_count": 0,
                "impacted_pages": [],
            }
        
        source_title = page.get("title", "")
        source_tags = page.get("tags", [])
        source_domain = page.get("domain", "")
        source_path = page.get("path", new_page)
        
        # 2. 收集候选页（三种来源合并去重）
        candidates = {}  # path -> {"page": page, "score": float, "reasons": [str]}
        
        def _add_candidate(cand_path, score_inc, reason):
            """添加或累加候选页"""
            if cand_path == source_path:
                return
            if cand_path not in candidates:
                page_obj = self.memory.get_page_by_path(cand_path)
                if not page_obj:
                    return
                candidates[cand_path] = {"page": page_obj, "score": 0.0, "reasons": []}
            candidates[cand_path]["score"] += score_inc
            if reason not in candidates[cand_path]["reasons"]:
                candidates[cand_path]["reasons"].append(reason)
        
        # 2a. 标签重叠（宽泛标签降权：覆盖页数越多，权重越低）
        for tag in source_tags:
            tag_pages = self.memory.get_pages_by_tag(tag)
            tag_count = len(tag_pages) if isinstance(tag_pages, (list, tuple)) else 0
            # 权重 = 0.35 / sqrt(覆盖页数)，覆盖 1 页得 0.35，50 页得 ~0.05
            tag_weight = round(0.35 / (math.sqrt(tag_count) if tag_count > 0 else 1), 2)
            for tp in tag_pages:
                tp_path = tp.get("path") if isinstance(tp, dict) else tp
                _add_candidate(tp_path, tag_weight, f"标签重叠: #{tag}")
        
        # 2b. 同域（但不同标签的页，给低分）
        if source_domain:
            domain_pages = self.memory.get_pages_by_domain(source_domain)
            for dp in domain_pages:
                dp_path = dp.get("path") if isinstance(dp, dict) else dp
                # 避免过度加权：同域但已有标签重叠的不再加分
                if dp_path in candidates:
                    if "同域" not in " ".join(candidates[dp_path]["reasons"]):
                        candidates[dp_path]["score"] += 0.15
                        candidates[dp_path]["reasons"].append(f"同域: {source_domain}")
                else:
                    _add_candidate(dp_path, 0.15, f"同域: {source_domain}")
        
        # 2c. 链接相关（出链入链，最高权重）
        related = self.memory.get_related_pages(source_path, max_results=20)
        for rp in related:
            rp_path = rp.get("path") if isinstance(rp, dict) else ""
            if rp_path:
                score_boost = 0.25 if rp_path in candidates else 0.5
                _add_candidate(rp_path, score_boost, "链接相关")

        # 2d. 语义相似度匹配（复用 concept_collide 的嵌入缓存）
        try:
            _embed_cache_path = os.path.join(self.vault_path, ".tool", "lingtai-kb", "data", "danfang_embeddings.json")
            if os.path.isfile(_embed_cache_path):
                import json as _json
                with open(_embed_cache_path, "r", encoding="utf-8") as _f:
                    _embed_cache = _json.load(_f)
                _src_vec_data = _embed_cache.get(source_path)
                if _src_vec_data and isinstance(_src_vec_data, dict) and "vec" in _src_vec_data:
                    import numpy as _np
                    _src_vec = _np.array(_src_vec_data["vec"])
                    for _epath, _eentry in _embed_cache.items():
                        if _epath == source_path:
                            continue
                        if not isinstance(_eentry, dict) or "vec" not in _eentry:
                            continue
                        # 检查是否跨域
                        _ep = self.memory.get_page_by_path(_epath)
                        if _ep and _ep.get("domain", "") == source_domain:
                            continue  # 同域跳过（已有标签/同域匹配）
                        _tgt_vec = _np.array(_eentry["vec"])
                        _norm = (_np.linalg.norm(_src_vec) * _np.linalg.norm(_tgt_vec))
                        if _norm == 0:
                            continue
                        _sim = float(_np.dot(_src_vec, _tgt_vec) / _norm)
                        if 0.5 <= _sim <= 0.75:
                            _score = round(_sim * 0.35, 2)  # 相似度权重
                            _add_candidate(_epath, _score, f"语义相似(sim={_sim:.2f})")
        except Exception:
            log.debug("suppressed", exc_info=True)
        
        # 3. 评分调整：多标签命中叠加
        for cpath, cdata in candidates.items():
            tag_hits = sum(1 for t in source_tags if t in cdata["reasons"])
            if tag_hits > 1:
                cdata["score"] += (tag_hits - 1) * 0.2  # 每多一个标签 +0.2
        
        # 4. 排序
        scored = sorted(candidates.values(), key=lambda x: x["score"], reverse=True)
        
        # 5. 读取候选页，识别章节标题
        impacted = []
        for cdata in scored[:max_results]:
            cp = cdata["page"]
            cp_path = cp.get("path", "")
            cp_title = cp.get("title", "")
            cp_domain = cp.get("domain", "")
            
            # 读页面前 500 字符找章节标题
            suggested_section = ""
            section_match = ""
            try:
                cp_abs = os.path.join(self.vault_path, (cp_path + '.md').replace("/", os.sep))
                if not os.path.exists(cp_abs):
                    # 有时 path 本身已含 .md（如 raw 原料路径）
                    cp_abs = os.path.join(self.vault_path, cp_path.replace("/", os.sep))
                if os.path.exists(cp_abs):
                    with open(cp_abs, 'r', encoding='utf-8') as f:
                        preview = f.read(2000)
                    # 提取 ## 章节标题
                    sections = re.findall(r'^##\s+(.+)$', preview, re.MULTILINE)
                    if sections:
                        # 选第一个非空章节作为建议插入点
                        suggested_section = "## " + sections[0]
                        section_match = "章节标题模糊匹配"
                        # 如果有多个章节且标题与源页主题接近，选更相关的
                        for sec in sections:
                            src_words = set(re.findall(r'[\u4e00-\u9fff]{2,}', source_title))
                            sec_words = set(re.findall(r'[\u4e00-\u9fff]{2,}', sec))
                            if src_words & sec_words:
                                suggested_section = "## " + sec
                                section_match = "章节标题语义匹配"
                                break
            except Exception:
                log.debug("suppressed", exc_info=True)
            
            impacted.append({
                "page_path": cp_path,
                "title": cp_title,
                "domain": cp_domain,
                "relevance": round(cdata["score"], 2),
                "match_reason": " + ".join(cdata["reasons"]),
                "suggested_section": suggested_section,
                "section_match": section_match,
            })
        

        # 6. 若 auto_generate=True，为每个波及页生成引用文本
        if auto_generate:
            for item in impacted:
                ref_lines = []
                ref_lines.append(f'> [!tip] 关联：[[{source_title}]]')
                
                # 根据匹配原因生成不同风格的引用
                reasons = item.get("match_reason", "")
                if "标签重叠" in reasons:
                    ref_lines.append(f'> 本文与 [[{source_title}]] 共享标签，在记忆系统设计上形成互补视角。')
                elif "同域" in reasons:
                    ref_lines.append(f'> 同域参照：[[{source_title}]] 提供了{source_domain}领域的另一种实现路径。')
                elif "链接相关" in reasons:
                    ref_lines.append(f'> 关联阅读：[[{source_title}]] 与本页在知识图谱中已有链接，建议补充交叉引用。')
                else:
                    ref_lines.append(f'> 参见：[[{source_title}]] 与本主题相关。')
                
                item["generated_ref"] = '\n'.join(ref_lines)
        
        return {
            "source_page": source_path,
            "source_title": source_title,
            "source_domain": source_domain,
            "source_tags": source_tags,
            "ripple_count": len(impacted),
            "impacted_pages": impacted,
        }

    @tool(readonly=True, write=False, category="page", system=False)
    def page_history(self, page_path: str, days: int = 30, max_results: int = 20) -> dict:
        """
        页面版本追溯：从操作日志中提取指定页面的修改历史
        
        Args:
            page_path: 页面路径（如 "丹房/00-思考与认知/含人量"）
            days: 回溯天数
            max_results: 最大返回条数
        
        Returns:
            dict: 修改历史时间线
        """
        import os, json
        from datetime import datetime, timedelta

        oplog_path = os.path.join(self.vault_path, "丹房", ".meta", "oplog.jsonl")
        if not os.path.exists(oplog_path):
            return {"page": page_path, "error": "oplog.jsonl not found", "entries": []}

        cutoff = datetime.now() - timedelta(days=days)
        # oplog 时间戳含 +08:00 时区，确保 cutoff 也是 offset-aware
        if cutoff.tzinfo is None:
            from datetime import timezone
            cutoff = cutoff.replace(tzinfo=timezone.utc).astimezone()
        entries = []
        path_key = page_path.replace("丹房/", "").replace(".md", "")
        page_name = path_key.split("/")[-1] if "/" in path_key else path_key

        with open(oplog_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue

                ts_str = entry.get("t", "")
                try:
                    ts = datetime.fromisoformat(ts_str)
                except (ValueError, TypeError):
                    continue
                if ts < cutoff:
                    continue

                links = entry.get("links", [])
                if not links:
                    continue
                matched = any(page_name in l or path_key in l for l in links)
                if not matched:
                    continue

                entries.append({
                    "timestamp": ts_str,
                    "operator": entry.get("op", ""),
                    "type": entry.get("type", ""),
                    "summary": entry.get("summary", ""),
                    "links": links,
                })

        entries.sort(key=lambda x: x["timestamp"], reverse=True)
        return {
            "page": page_path,
            "days": days,
            "total_entries": len(entries),
            "entries": entries[:max_results],
        }

    def graph(self, page_path: str, hops: int = 3, weighted: bool = True) -> dict:
        """
        从某页面出发的图扩散（支持加权）
        
        Args:
            page_path: 起始页面路径
            hops: 扩散跳数（默认3，最大3）
            weighted: 是否启用加权扩散（默认开启）
        
        Returns:
            dict: 扩散结果
        """
        # 限制最大跳数
        hops = min(hops, 3)
        
        # 获取起始页面
        start_page = self.memory.get_page_by_path(page_path)
        if not start_page:
            return {"found": False, "error": f"页面不存在: {page_path}"}
        
        # BFS扩散（带权重 + 节点上限防退化）
        max_nodes = 100
        visited = set()
        result_nodes = []
        queue = [(start_page, 0, 1.0)]
        
        while queue and len(result_nodes) < max_nodes:
            current_page, current_hop, current_weight = queue.pop(0)
            
            if current_page["path"] in visited:
                continue
            if current_hop > hops:
                continue
            
            visited.add(current_page["path"])
            
            # 计算页面权重
            page_weight = self.memory._calculate_page_weight(current_page) if weighted else 1.0
            final_weight = current_weight * page_weight
            
            result_nodes.append({
                "path": current_page["path"],
                "title": current_page["title"],
                "hop": current_hop,
                "weight": round(final_weight, 3),
                "summary": current_page.get("summary", "")[:100],
            })
            
            # 找到关联页面
            for link in current_page.get("links_to", []):
                neighbor = self.memory.get_page_by_path(link)
                if neighbor and neighbor["path"] not in visited:
                    queue.append((neighbor, current_hop + 1, final_weight))
            
            # 找到被引用页面
            for link in current_page.get("linked_from", []):
                neighbor = self.memory.get_page_by_path(link)
                if neighbor and neighbor["path"] not in visited:
                    queue.append((neighbor, current_hop + 1, final_weight))
        
        # 按权重排序（加权模式）
        if weighted:
            result_nodes.sort(key=lambda x: x.get("weight", 0), reverse=True)
        
        return {
            "found": True,
            "start": page_path,
            "hops": hops,
            "weighted": weighted,
            "total_nodes": len(result_nodes),
            "nodes": result_nodes,
        }

    def _search_memory_bank(self, keyword: str) -> list:
        """
        桥接层：查询记忆银行，使 knowledge_search 返回结果附带相关记忆。
        这是知识←→记忆双向桥接的反向（知识→记忆）方向。

        Args:
            keyword: 搜索关键词

        Returns:
            list: 匹配的记忆条目列表（空列表 = 无匹配或不可用）
        """
        try:
            if not hasattr(self, 'memory_bank') or self.memory_bank is None:
                return []
            return self.memory_bank.query(
                keyword=keyword,
                status="active",
                min_confidence=0.3,
                include_archived=False,
                audit_source="knowledge_bridge",
            )
        except Exception:
            return []

    # ═══ BM25 + 向量混合检索 ═══════════════════════

    def _hybrid_danfang_search(self, keyword: str, top_k: int = 15) -> list[dict]:
        """BM25 + 向量 RRF 混合检索丹房页。

        Returns:
            [{path, rrf_score, bm25_score, vector_score, rank}] 或空列表
        """
        try:
            from bm25_engine import BM25Index, hybrid_search
            pages = self.memory.pages
            if not pages:
                return []

            # 缓存 BM25Index：page 数量未变时复用已有索引
            if not hasattr(self, '_bm25_cache') or self._bm25_cache[0] != len(pages):
                self._bm25_cache = (len(pages), BM25Index(pages))
            bm25_idx = self._bm25_cache[1]

            return hybrid_search(
                query=keyword,
                pages=pages,
                vault_path=self.vault_path,
                top_k=top_k,
                bm25_idx=bm25_idx,
            )
        except Exception:
            log.debug("hybrid_danfang_search failed", exc_info=True)
            return []

    def _rrf_merge(self, keyword: str, boolean_results: list[dict], hybrid_results: list[dict]) -> list[dict]:
        """将布尔匹配结果与 BM25+向量混合结果用 RRF 融合。

        策略：
        - 布尔匹配（精确/anchor/keyword）作为第一路（已按 score_relevance 排序）
        - BM25+向量混合作为第二路（已按 RRF 排序）
        - 最终用 RRF 融合两路，布尔匹配路权重略高（k 更小 = 排名差异更敏感）

        Returns:
            融合后的 page dict 列表（保持原有字段），按 RRF 分数降序
        """
        from bm25_engine import rrf_fuse

        if not boolean_results and not hybrid_results:
            return []

        # 构建布尔匹配路的 ranked list
        bool_ranked = [
            {"path": p.get("path", ""), "score": 0, "rank": i + 1}
            for i, p in enumerate(boolean_results)
        ]

        # hybrid_results 已经是 [{path, rrf_score, rank}] 格式
        hybrid_ranked = [
            {"path": r["path"], "score": r.get("rrf_score", 0), "rank": r["rank"]}
            for r in hybrid_results
        ]

        # RRF 融合（布尔路 k=40 权重更高，混合路 k=60 更平滑）
        fused = rrf_fuse([bool_ranked, hybrid_ranked], k=[40, 60])

        # 将融合结果映射回 page dict
        path_to_page = {}
        for p in boolean_results:
            path_to_page[p.get("path", "")] = p
        # 补充 hybrid 中有但 boolean 中没有的页面（提升召回）
        for r in hybrid_results:
            path = r["path"]
            if path not in path_to_page:
                page = self.memory.path_map.get(path)
                if page:
                    path_to_page[path] = page

        merged = []
        for item in fused:
            page = path_to_page.get(item["path"])
            if page:
                merged.append(page)

        return merged
