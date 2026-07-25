# -*- coding: utf-8 -*-
"""
语义检索模块 (Semantic Retriever)
==================================
基于 bge-small-zh-v1.5 本地嵌入模型的记忆向量检索。
复用 .tool/scripts/semantic_scan.py 的 embedding 管线。

设计：
- 懒加载嵌入模型（首次调用时初始化）
- 独立 embedding 缓存（与丹房页的缓存隔开）
- 查询向量 → 与全部记忆向量做余弦相似度 → 返回 Top-K
```
"""
import json
import math
import os
import sys
import numpy as np
from pathlib import Path
from typing import List, Optional
import threading
from logger import get_logger

log = get_logger(__name__)

_MODEL = None
_MODEL_LOCK = threading.Lock()
_HERE = Path(__file__).parent
_CACHE_DIR = _HERE / "data"
_CACHE_PATH = _CACHE_DIR / "semantic_cache.json"
_EMBEDDING_DIM = 512
_MODEL_CACHE_DIR = _HERE / "model_cache"

# 模型缓存路径指向项目本地目录（方案 C）：隔离系统清理风险，始终离线运行
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
os.environ.setdefault("HF_HOME", str(_MODEL_CACHE_DIR))
# 强制离线：模型已通过下载脚本缓存到 HF_HOME，运行时永不联网
# 首次部署需先跑 .tool/scripts/download_semantic_model.py
os.environ["TRANSFORMERS_OFFLINE"] = "1"

# BGE 非对称检索指令前缀：短查询 → 长文档
# 不加前缀时 bge 的 query/doc embedding 处于同一空间但区分度低，
# 加前缀后 query 编码偏向"检索意图"，召回率提升 10-15%
BGE_QUERY_PREFIX = "为这个句子生成表示以用于检索相关文章："


def preload_model():
    """预热加载语义模型，供 server 启动时后台线程调用。
    返回是否加载成功。静默失败不抛异常。"""
    model = _get_model()
    if model is not None:
        try:
            model.encode([BGE_QUERY_PREFIX + "."], show_progress_bar=False)
            return True
        except Exception:
            pass
    return False


def _get_model():
    global _MODEL
    if _MODEL is not None:
        return _MODEL
    if not _MODEL_LOCK.acquire(blocking=False):
        return None  # 另一线程正在加载
    try:
        from sentence_transformers import SentenceTransformer
        _MODEL = SentenceTransformer("BAAI/bge-small-zh-v1.5")
    except Exception as e:
        log.warning("semantic_retriever Model load failed: %s", e)
        _MODEL = None
    finally:
        _MODEL_LOCK.release()
    return _MODEL


def _load_cache() -> dict:
    if _CACHE_PATH.exists():
        try:
            return json.loads(_CACHE_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _save_cache(cache: dict):
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    _CACHE_PATH.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")


def _cosine_sim(a: np.ndarray, b: np.ndarray) -> float:
    norm_a = np.linalg.norm(a)
    norm_b = np.linalg.norm(b)
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return float(np.dot(a, b) / (norm_a * norm_b))


def embed(text: str, is_query: bool = False) -> Optional[np.ndarray]:
    """嵌入单条文本。返回 512-dim ndarray 或 None。
    
    Args:
        text: 输入文本
        is_query: True 时自动加 BGE 查询指令前缀（非对称检索）
    """
    model = _get_model()
    if model is None:
        return None
    try:
        input_text = BGE_QUERY_PREFIX + text if is_query else text
        vec = model.encode([input_text], show_progress_bar=False)[0]
        return vec
    except Exception:
        return None


def embed_many(texts: List[str]) -> Optional[np.ndarray]:
    """批量嵌入文本。返回 (N, 512) ndarray 或 None。"""
    model = _get_model()
    if model is None:
        return None
    if not texts:
        return None
    try:
        return model.encode(texts, show_progress_bar=False)
    except Exception:
        return None


def _build_embed_text(m: dict) -> str:
    """构建语义编码的输入文本：正文 + 标签 + 类型前缀，让 tag 关键词参与语义匹配。
    
    语义检索时标签关键词（如 "lesson"、"topic:回复"）若不在正文中，
    纯 content 嵌入会漏掉这些语义信号。拼接后 bge 能捕获标签和正文的交叉语义。
    """
    content = (m.get("content") or "")[:600]
    tags = m.get("tags") or []
    mtype = m.get("memory_type") or ""
    tag_str = " ".join(str(t) for t in tags[:12])  # 取前12个防溢出
    if tag_str or mtype:
        return f"[{mtype}] [{tag_str}] {content}" if tag_str else f"[{mtype}] {content}"
    return content


def ensure_cached(memories: List[dict]) -> dict:
    """确保给定记忆列表全部有 embedding 缓存。
    
    返回 {memory_id: embedding_vector, ...} 的映射。
    新记忆逐条嵌入并保存到缓存。
    缓存上限 = 当前记忆数（自动清理已删除/归档的旧条目）。
    """
    cache = _load_cache()
    need_embed = []
    for m in memories:
        mid = m.get("id", "")
        if mid not in cache:
            embed_text = _build_embed_text(m)
            need_embed.append((mid, embed_text))

    if need_embed:
        texts = [t for _, t in need_embed]
        vectors = embed_many(texts)
        if vectors is not None:
            for (mid, _), vec in zip(need_embed, vectors):
                cache[mid] = vec.tolist()

    # LRU 清理：移除不在当前记忆列表中的旧条目（防止无限膨胀）
    active_ids = {m.get("id", "") for m in memories}
    stale = [k for k in cache if k not in active_ids]
    if stale:
        for k in stale:
            del cache[k]

    if need_embed or stale:
        _save_cache(cache)

    # 返回 numpy 向量
    result = {}
    for m in memories:
        mid = m.get("id", "")
        vec = cache.get(mid)
        if vec is not None:
            result[mid] = np.array(vec)
    return result


def search(query: str, memories: List[dict], top_k: int = 10) -> List[dict]:
    """语义检索：对查询编码后与所有记忆向量做余弦相似度，返回 top-K（带 semantic_score）。

    返回结果格式（与 MemoryBank.query 兼容）：
    [{"id": ..., "content": ..., "semantic_score": 0.XX, ...}, ...]
    """
    model = _get_model()
    if model is None:
        return []

    qvec = embed(query, is_query=True)
    if qvec is None:
        return []

    vec_map = ensure_cached(memories)
    if not vec_map:
        return []

    scored = []
    for m in memories:
        mid = m.get("id", "")
        vec = vec_map.get(mid)
        if vec is None:
            continue
        sim = _cosine_sim(qvec, vec)
        if sim < 0.4:  # 低阈值过滤
            continue
        result = dict(m)
        result["semantic_score"] = round(sim, 4)
        scored.append(result)

    scored.sort(key=lambda x: -x["semantic_score"])
    return scored[:top_k]


def merge_results(substring_results: List[dict],
                  semantic_results: List[dict],
                  top_k: int = 10,
                  substring_weight: float = 0.3,
                  semantic_weight: float = 0.7) -> List[dict]:
    """两路结果合并：substring（带 relevance_score）+ semantic（带 semantic_score）。

    合并策略：
    - ID 去重：两路结果按 memory_id 归并
    - 最终得分 = substring_relevance × substring_weight + semantic_score × semantic_weight
    - 如果只有一路有结果，另一路 score=0
    - 如果某条 memory 只在一路出现，走该路的分数（不惩罚）
    - 排序后取 top-K
    """
    merged = {}
    for r in substring_results:
        mid = r.get("id", "")
        rel = r.get("relevance_score", 0)
        merged[mid] = {"result": dict(r), "sub_rel": rel, "sem_score": 0.0}

    for r in semantic_results:
        mid = r.get("id", "")
        sem = r.get("semantic_score", 0)
        if mid in merged:
            merged[mid]["sem_score"] = sem
        else:
            d = dict(r)
            d["relevance_score"] = 0.0
            merged[mid] = {"result": d, "sub_rel": 0.0, "sem_score": sem}

    for mid, data in merged.items():
        data["result"]["final_score"] = round(
            data["sub_rel"] * substring_weight + data["sem_score"] * semantic_weight, 4
        )

    final = sorted(merged.values(), key=lambda x: -x["result"]["final_score"])
    return [fv["result"] for fv in final[:top_k]]
