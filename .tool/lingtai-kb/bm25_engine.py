# -*- coding: utf-8 -*-
"""
灵台丹房检索引擎：BM25 + 向量 + RRF 融合
==========================================
替代 score_relevance 中的 Jaccard 打分，引入标准 BM25（IDF + length norm）
和 bge-small-zh 向量检索，通过 RRF 融合排序。

架构：
- BM25Index：从 MemoryEngine.pages 构建倒排索引 + IDF 统计
- vector_search_pages：查询→丹房页向量相似度（复用 danfang_embeddings.json）
- rrf_fuse：Reciprocal Rank Fusion 合并多路排序

用法：
    from bm25_engine import BM25Index, vector_search_pages, rrf_fuse
    idx = BM25Index(pages)
    bm25_ranked = idx.search("知识管理", top_k=20)
    vec_ranked = vector_search_pages("知识管理", vault_path, top_k=20)
    fused = rrf_fuse([bm25_ranked, vec_ranked], k=60)
"""
__all__ = ["BM25Index", "tokenize_bm25", "vector_search_pages", "rrf_fuse", "hybrid_search"]

import json
import math
import os
import re
from pathlib import Path
from typing import Optional

from logger import get_logger

log = get_logger(__name__)

# ─── 分词 ─────────────────────────────────────────

# 停用词（高频无意义词，降低 IDF 噪声）
_STOPWORDS = frozenset(
    "的 了 是 在 我 有 和 就 不 人 都 一 一个 上 也 很 到 说 要 去 你 会 着 没有 看 好 "
    "自己 这 他 她 它 们 那 些 什么 怎么 如何 可以 因为 所以 但是 而且 或者 如果 虽然 "
    "the a an is are was were be been being in on at to for of with by from as".split()
)

# CJK 连续序列
_CJK_SEQ_RE = re.compile(r"[\u4e00-\u9fff]+")
# ASCII 词（2+ 字符）
_ASCII_RE = re.compile(r"[a-z0-9_][a-z0-9_.-]{1,}")


def tokenize_bm25(text: str) -> list[str]:
    """BM25 分词：CJK bigram + trigram + ASCII 整词。

    与 memory_engine._tokenize_recall_terms 保持一致的粒度，
    但去重（BM25 需要词频，不去重会丢失 TF 信息）。
    """
    norm = text.lower()
    tokens: list[str] = []

    # ASCII 词
    tokens.extend(_ASCII_RE.findall(norm))

    # CJK n-gram (2-3)
    for seq in _CJK_SEQ_RE.findall(norm):
        for n in (2, 3):
            for i in range(len(seq) - n + 1):
                tokens.append(seq[i:i + n])

    # 过滤停用词
    return [t for t in tokens if t not in _STOPWORDS]


# ─── BM25 索引 ────────────────────────────────────


class BM25Index:
    """Okapi BM25 索引（k1=1.5, b=0.75）。

    从 MemoryEngine.pages 构建，每个 page 的索引文本 = title + summary + tags。
    支持增量重建（检测 pages 列表变化）。
    """

    K1 = 1.5
    B = 0.75

    def __init__(self, pages: list[dict]):
        self._pages = pages
        self._page_count = len(pages)
        self._build()

    def _build(self):
        """构建倒排索引 + IDF + 文档长度。"""
        self._doc_freqs: dict[str, int] = {}   # term → 出现在多少文档中
        self._doc_lens: list[int] = []          # 每篇文档的 token 数
        self._tf: list[dict[str, int]] = []     # 每篇文档的 term → count
        self._paths: list[str] = []             # 每篇文档的 path

        total_len = 0
        for page in self._pages:
            text = " ".join([
                page.get("title", ""),
                page.get("summary", ""),
                " ".join(page.get("tags", [])),
            ])
            tokens = tokenize_bm25(text)
            self._doc_lens.append(len(tokens))
            self._paths.append(page.get("path", ""))
            total_len += len(tokens)

            # 词频
            tf: dict[str, int] = {}
            for t in tokens:
                tf[t] = tf.get(t, 0) + 1
            self._tf.append(tf)

            # 文档频率（每 term 只计一次）
            for t in set(tokens):
                self._doc_freqs[t] = self._doc_freqs.get(t, 0) + 1

        self._avgdl = total_len / max(len(self._pages), 1)
        self._N = len(self._pages)

        # 预计算 IDF
        self._idf: dict[str, float] = {}
        for term, df in self._doc_freqs.items():
            self._idf[term] = math.log((self._N - df + 0.5) / (df + 0.5) + 1.0)

        log.debug("bm25 index built", extra={
            "pages": self._N, "vocab": len(self._idf), "avgdl": round(self._avgdl, 1)
        })

    def search(self, query: str, top_k: int = 20) -> list[dict]:
        """BM25 排序检索。

        Returns:
            [{path, score, rank}], rank 从 1 开始
        """
        q_tokens = tokenize_bm25(query)
        if not q_tokens:
            return []

        scores: list[float] = [0.0] * self._N
        for term in q_tokens:
            idf = self._idf.get(term)
            if idf is None:
                continue
            for i in range(self._N):
                tf = self._tf[i].get(term, 0)
                if tf == 0:
                    continue
                dl = self._doc_lens[i]
                numer = tf * (self.K1 + 1)
                denom = tf + self.K1 * (1 - self.B + self.B * dl / self._avgdl)
                scores[i] += idf * numer / denom

        # 取 top-K（跳过零分）
        indexed = [(s, i) for i, s in enumerate(scores) if s > 0]
        indexed.sort(reverse=True)
        results = []
        for rank, (score, i) in enumerate(indexed[:top_k], 1):
            results.append({
                "path": self._paths[i],
                "score": round(score, 4),
                "rank": rank,
            })
        return results

    @property
    def stats(self) -> dict:
        return {
            "pages": self._N,
            "vocab_size": len(self._idf),
            "avg_doc_len": round(self._avgdl, 1),
        }


# ─── 向量检索（丹房页） ───────────────────────────


def vector_search_pages(
    query: str,
    vault_path: str,
    top_k: int = 20,
    threshold: float = 0.35,
    _cache: dict = {},
) -> list[dict]:
    """查询→丹房页向量相似度检索。

    复用 concept_collision 已构建的 danfang_embeddings.json 缓存。
    模型不可用时返回空列表（优雅降级）。

    Returns:
        [{path, score, rank}], rank 从 1 开始
    """
    # 加载嵌入缓存（进程内单例）
    if "embeddings" not in _cache:
        emb_path = os.path.join(vault_path, ".tool", "lingtai-kb", "data", "danfang_embeddings.json")
        if not os.path.exists(emb_path):
            _cache["embeddings"] = {}
            return []
        try:
            with open(emb_path, "r", encoding="utf-8") as f:
                _cache["embeddings"] = json.load(f)
        except Exception:
            log.warning("failed to load danfang embeddings", exc_info=True)
            _cache["embeddings"] = {}

    embeddings = _cache["embeddings"]
    if not embeddings:
        return []

    # 编码查询
    query_vec = _encode_query(query)
    if query_vec is None:
        return []

    # 计算余弦相似度
    scored = []
    for path, entry in embeddings.items():
        vec = entry.get("vec") if isinstance(entry, dict) else entry
        if not vec:
            continue
        sim = _cosine_sim(query_vec, vec)
        if sim >= threshold:
            scored.append((sim, path))

    scored.sort(reverse=True)
    results = []
    for rank, (sim, path) in enumerate(scored[:top_k], 1):
        results.append({
            "path": path,
            "score": round(sim, 4),
            "rank": rank,
        })
    return results


def _encode_query(query: str) -> Optional[list]:
    """用 bge-small-zh 编码查询（带 BGE 查询前缀）。"""
    try:
        from memory_bank.semantic_retriever import _get_model, BGE_QUERY_PREFIX
        model = _get_model()
        if model is None:
            return None
        vec = model.encode([BGE_QUERY_PREFIX + query], show_progress_bar=False)
        return vec[0].tolist() if vec is not None and len(vec) > 0 else None
    except Exception:
        log.debug("vector encode failed", exc_info=True)
        return None


def _cosine_sim(a: list, b: list) -> float:
    """余弦相似度（纯 Python，211 页规模足够快）。"""
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


# ─── RRF 融合 ─────────────────────────────────────


def rrf_fuse(ranked_lists: list[list[dict]], k: int | list[int] = 60) -> list[dict]:
    """Reciprocal Rank Fusion 合并多路排序。

    score(d) = Σ 1/(k_i + rank_i(d))

    Args:
        ranked_lists: 多路排序结果，每路是 [{path, score, rank}]
        k: RRF 常数（默认 60，越大越平滑）。传 int 给所有路统一值，
           传 list[int] 给每路单独指定 k（长度须与 ranked_lists 一致）

    Returns:
        融合后排序 [{path, rrf_score, sources: [来源索引]}]
    """
    if isinstance(k, int):
        ks = [k] * len(ranked_lists)
    else:
        ks = k
    fused: dict[str, dict] = {}  # path → {rrf_score, sources}
    for list_idx, ranked in enumerate(ranked_lists):
        k_i = ks[list_idx] if list_idx < len(ks) else 60
        for item in ranked:
            path = item["path"]
            rank = item["rank"]
            rrf_contrib = 1.0 / (k_i + rank)
            if path not in fused:
                fused[path] = {"path": path, "rrf_score": 0.0, "sources": []}
            fused[path]["rrf_score"] += rrf_contrib
            fused[path]["sources"].append(list_idx)

    # 按 RRF 分数降序
    results = sorted(fused.values(), key=lambda x: -x["rrf_score"])
    for i, r in enumerate(results, 1):
        r["rank"] = i
        r["rrf_score"] = round(r["rrf_score"], 6)
    return results


# ─── 混合检索入口 ─────────────────────────────────


def hybrid_search(
    query: str,
    pages: list[dict] | None = None,
    vault_path: str = "",
    top_k: int = 15,
    bm25_weight: float = 1.0,
    vector_weight: float = 1.0,
    bm25_idx: BM25Index | None = None,
) -> list[dict]:
    """BM25 + 向量 + RRF 混合检索。

    Args:
        query: 查询关键词
        pages: MemoryEngine.pages 列表（bm25_idx 为 None 时用于构建索引）
        vault_path: vault 根路径
        top_k: 最终返回条数
        bm25_weight: BM25 路权重（RRF 贡献乘数）
        vector_weight: 向量路权重
        bm25_idx: 预构建的 BM25Index（传此参数可跳过索引重建，pages 参数仍须传供降级用）

    Returns:
        [{path, rrf_score, bm25_score, vector_score, rank, sources}]
    """
    # BM25 路
    if bm25_idx is not None:
        bm25_results = bm25_idx.search(query, top_k=top_k * 2)
    elif pages:
        bm25_idx = BM25Index(pages)
        bm25_results = bm25_idx.search(query, top_k=top_k * 2)
    else:
        bm25_results = []

    # 向量路
    vec_results = vector_search_pages(query, vault_path, top_k=top_k * 2)

    # RRF 融合
    fused = rrf_fuse([bm25_results, vec_results], k=60)

    # 附加原始分数（便于调试和后续加权）
    bm25_map = {r["path"]: r["score"] for r in bm25_results}
    vec_map = {r["path"]: r["score"] for r in vec_results}
    for item in fused:
        item["bm25_score"] = bm25_map.get(item["path"], 0.0)
        item["vector_score"] = vec_map.get(item["path"], 0.0)

    return fused[:top_k]
