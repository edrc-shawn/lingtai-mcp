# -*- coding: utf-8 -*-
"""
概念碰撞引擎 (Concept Collision Engine)
========================================
基于 bge-small-zh-v1.5 语义嵌入，扫描丹房页全量做跨域余弦相似度匹配，
在 0.6-0.75 区间产出「有关联但视角不同」的意外关联候选。

基础设计灵感来自「知识库系统架构深度解析」原料中的概念碰撞机制，
0.6-0.75 阈值瞄准语义巧合的甜点区——低于 0.6 是噪音，高于 0.75 是重复。

复用 memory_bank/semantic_retriever.py 的 bge-small-zh-v1.5 模型，
独立缓存丹房页嵌入（与记忆银行语义缓存隔离）。

也包含知识生命周期扫描工具（lifecycle_scan），用于检测可降级/可清理的页面。
"""

import json
import os
import numpy as np
from pathlib import Path
from typing import List, Optional, Tuple

from logger import get_logger

log = get_logger(__name__)

# ─── 常量 ───
_MIN_SIM = 0.6       # 最低相似度（低于此 = 噪音）
_MAX_SIM = 0.75      # 最高相似度（高于此 = 重复）
_TOP_N_DEFAULT = 20  # 默认返回条数
_TITLE_OVERLAP_THRESHOLD = 0.5  # 标题 bigram 重叠超过此值视为重复
_CACHE_FILENAME = "danfang_embeddings.json"


def _get_model():
    """懒加载 bge-small-zh-v1.5 模型（复用记忆层的同一实例）。"""
    from memory_bank.semantic_retriever import _get_model as _mem_model
    return _mem_model()


def _cosine_sim(a: np.ndarray, b: np.ndarray) -> float:
    """余弦相似度。"""
    na = np.linalg.norm(a)
    nb = np.linalg.norm(b)
    if na == 0 or nb == 0:
        return 0.0
    return float(np.dot(a, b) / (na * nb))


def _title_overlap(t1: str, t2: str) -> float:
    """计算两个标题的重叠度，用于检测同主题重复。

    两阶段检测：
    1. 子串包含：一方是另一方的子串 → 强重复信号（返回 1.0）
    2. 字符集 Jaccard：适用于标题共享核心词但措辞不同的情况
    """
    if not t1 or not t2:
        return 0.0
    # 1. 子串检查
    if t1 in t2 or t2 in t1:
        return 1.0
    # 2. 字符集 Jaccard
    s1 = set(t1.replace(" ", ""))
    s2 = set(t2.replace(" ", ""))
    if not s1 or not s2:
        return 0.0
    return len(s1 & s2) / len(s1 | s2)


def _load_cache(vault_path: str) -> dict:
    """加载丹房页嵌入缓存。"""
    cache_path = Path(vault_path) / ".tool" / "lingtai-kb" / "data" / _CACHE_FILENAME
    if cache_path.exists():
        try:
            return json.loads(cache_path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _save_cache(vault_path: str, cache: dict):
    """持久化丹房页嵌入缓存。"""
    cache_dir = Path(vault_path) / ".tool" / "lingtai-kb" / "data"
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path = cache_dir / _CACHE_FILENAME
    cache_path.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")


def _extract_page_text(page: dict) -> str:
    """从 page entry 提取适合嵌入的文本。

    优先用 summary（~200 字摘要），回退到 title。
    摘要文本足够捕捉语义主题，且嵌入速度比全量 content 快 25 倍。
    """
    text = (page.get("summary") or "").strip()
    if not text:
        text = (page.get("title") or "").strip()
    return text[:300]


def build_index(vault_path: str, pages: List[dict], batch_size: int = 50) -> dict:
    """为丹房页构建/增量更新嵌入索引。

    对比已有缓存，只嵌入新增或内容变化（by path+content_hash）的页面。
    分批次嵌入避免长耗时，批次进度输出到 stderr。

    Args:
        vault_path: vault 根路径
        pages: MemoryEngine.pages 列表（每项含 path, domain, summary 等）
        batch_size: 每批嵌入页数（默认 50，控制单次耗时 < 20s）

    Returns:
        dict: {path: {vec: [...], hash: "..."}, ...}
    """
    cache = _load_cache(vault_path)
    model = _get_model()
    if model is None:
        # 模型不可用：用空嵌入（0 向量）兜底，碰撞结果空
        return cache

    need_embed = []
    for p in pages:
        path = p.get("path", "")
        if not path:
            continue
        content_hash = p.get("content_hash") or p.get("body_hash") or ""
        cached = cache.get(path, {})
        cached_hash = ""
        if isinstance(cached, dict):
            cached_hash = cached.get("hash", "")
        # 若有缓存且 hash 匹配则跳过
        if cached_hash and cached_hash == content_hash:
            continue
        text = _extract_page_text(p)
        if text:
            need_embed.append((path, text, content_hash))

    if not need_embed:
        return cache

    # 分批嵌入（避免单次耗时过长）
    model = _get_model()
    if model is None:
        return cache

    for batch_start in range(0, len(need_embed), batch_size):
        batch = need_embed[batch_start:batch_start + batch_size]
        texts = [t for _, t, _ in batch]
        vectors = model.encode(texts, show_progress_bar=False)
        if vectors is None:
            continue
        for i, (path, _, ch) in enumerate(batch):
            vec = vectors[i] if i < len(vectors) else None
            if vec is not None:
                cache[path] = {"vec": vec.tolist(), "hash": ch}
        log.info("embedding batch", extra={"batch": batch_start//batch_size + 1, "total": (len(need_embed)-1)//batch_size + 1, "pages": len(batch)})

    _save_cache(vault_path, cache)
    return cache


def collide(
    vault_path: str,
    pages: List[dict],
    top_n: int = _TOP_N_DEFAULT,
    min_sim: float = _MIN_SIM,
    max_sim: float = _MAX_SIM,
    domain_filter: str = "",
    domain_skip_same: bool = True,
) -> dict:
    """执行概念碰撞：跨域语义相似度匹配。

    流程：
    1. 确保嵌入索引已构建
    2. 按域分组，只做跨域对比较（domain_skip_same=True）
    3. 余弦相似度计算，筛选 min_sim ≤ sim ≤ max_sim
    4. 按相似度降序排列取 top_n

    Args:
        vault_path: vault 根路径
        pages: 丹房页列表
        top_n: 返回条数
        min_sim: 最低相似度
        max_sim: 最高相似度
        domain_filter: 限只返回包含该域的碰撞对（空=不限）
        domain_skip_same: 跳过同域对（默认 True）

    Returns:
        dict: {
            "total_pages": int,
            "pairs_evaluated": int,
            "collisions": [{page_a, page_b, sim, domain_a, domain_b, reason}, ...],
            "stats": {min_sim, max_sim, top_n, domain_filter, domain_skip_same}
        }
    """
    cache = build_index(vault_path, pages)

    # 构建 path → page 映射 + 向量查表
    path_to_page = {p.get("path", ""): p for p in pages}
    vectors = {}  # path → np.ndarray
    for path, entry in cache.items():
        if isinstance(entry, dict) and "vec" in entry:
            vectors[path] = np.array(entry["vec"])
        elif isinstance(entry, list):
            vectors[path] = np.array(entry)

    # 按域分组
    domain_groups: dict = {}
    path_domain: dict = {}
    for p in pages:
        path = p.get("path", "")
        domain = p.get("domain", "未知")
        if path in vectors:
            domain_groups.setdefault(domain, []).append(path)
            path_domain[path] = domain

    all_pairs_evaluated = 0
    collisions = []

    domains = list(domain_groups.keys())
    for i in range(len(domains)):
        for j in range(i + 1, len(domains)):
            d_a = domains[i]
            d_b = domains[j]
            if domain_filter and domain_filter not in (d_a, d_b):
                continue
            group_a = domain_groups[d_a]
            group_b = domain_groups[d_b]

            for pa in group_a:
                va = vectors[pa]
                for pb in group_b:
                    vb = vectors[pb]
                    sim = _cosine_sim(va, vb)
                    all_pairs_evaluated += 1
                    if min_sim <= sim <= max_sim:
                        # 构建简短匹配理由
                        title_a = path_to_page.get(pa, {}).get("title", pa.split("/")[-1])
                        title_b = path_to_page.get(pb, {}).get("title", pb.split("/")[-1])
                        collisions.append({
                            "page_a": pa,
                            "page_b": pb,
                            "title_a": title_a,
                            "title_b": title_b,
                            "similarity": round(sim, 4),
                            "domain_a": d_a,
                            "domain_b": d_b,
                        })

    # 按相似度降序排列
    collisions.sort(key=lambda x: x["similarity"], reverse=True)

    # ── 标题去重分离：标题高度重叠的移至 duplicates ──
    true_collisions = []
    duplicates = []
    for c in collisions:
        overlap = _title_overlap(c["title_a"], c["title_b"])
        if overlap >= _TITLE_OVERLAP_THRESHOLD:
            c["title_overlap"] = round(overlap, 4)
            c["reason"] = _generate_duplicate_reason(c)
            duplicates.append(c)
        else:
            true_collisions.append(c)

    true_collisions = true_collisions[:top_n]
    duplicates = duplicates[:top_n]

    # 为每个碰撞对生成简短理由
    for c in true_collisions:
        c["reason"] = _generate_reason(c)

    return {
        "total_pages": len(pages),
        "pages_embedded": len(vectors),
        "pairs_evaluated": all_pairs_evaluated,
        "collisions": true_collisions,
        "duplicates": duplicates,
        "stats": {
            "min_similarity": min_sim,
            "max_similarity": max_sim,
            "top_n": top_n,
            "domain_filter": domain_filter or "全部",
            "domain_skip_same": domain_skip_same,
            "duplicates_filtered": len(duplicates),
        },
    }


def _generate_reason(c: dict) -> str:
    """为碰撞对生成简短匹配理由。

    基于域对组合做模式化描述，让 AI 和人能快速判断是否值得深入。
    """
    da = c["domain_a"]
    db = c["domain_b"]
    # 提取短域名（去掉编号前缀）
    da_short = da.split("-", 1)[-1] if "-" in da else da
    db_short = db.split("-", 1)[-1] if "-" in db else db
    return f"跨域关联：{da_short} × {db_short}，语义相似度 {c['similarity']:.3f}，有关联但视角不同"


def _generate_duplicate_reason(c: dict) -> str:
    """为重复对生成简短理由。"""
    return f"疑似重复：标题重叠度 {c.get('title_overlap', 0):.2f}，建议合并或去重"


def apply_collision(
    vault_path: str,
    collision: dict,
    add_link_func: callable,
    logger=None,
) -> dict:
    """执行单条碰撞结果：自动补链（双向 wikilink）。

    Args:
        vault_path: vault 根路径
        collision: collide() 返回的单个碰撞条目
        add_link_func: 用于添加链接的可调用对象（如 server.add_link）
        logger: 日志器（可选）

    Returns:
        dict: {applied: bool, link_a_to_b: bool, link_b_to_a: bool, error: str}
    """
    result = {"applied": False, "link_a_to_b": False, "link_b_to_a": False, "error": ""}
    try:
        pa = collision["page_a"]
        pb = collision["page_b"]
        sim = collision["similarity"]

        # 双向补链
        label_a = f"概念碰撞({sim:.3f}) → {collision.get('title_b', pb)}"
        label_b = f"概念碰撞({sim:.3f}) → {collision.get('title_a', pa)}"

        add_link_func(pa, pb, label=label_a)
        result["link_a_to_b"] = True

        add_link_func(pb, pa, label=label_b)
        result["link_b_to_a"] = True

        result["applied"] = True
        if logger:
            logger.info(f"[concept_collide] 自动补链: {pa} ↔ {pb} (sim={sim:.3f})")
    except Exception as e:
        result["error"] = str(e)
        if logger:
            logger.error(f"[concept_collide] 补链失败: {e}")
    return result


def lifecycle_scan(
    vault_path: str,
    pages: List[dict],
    stale_days: int = 30,
    min_backlinks: int = 3,
) -> dict:
    """知识生命周期扫描——检测可降级/可清理的页面候选。

    降级条件（同时满足）：
    1. 品级为「下品」（单源/初始内容）
    2. 入链数 < min_backlinks（默认 3）
    3. 超过 stale_days（默认 30 天）未更新

    Args:
        vault_path: vault 根路径
        pages: MemoryEngine.pages 列表
        stale_days: 陈旧阈值（天）
        min_backlinks: 最低入链数

    Returns:
        dict: {
            "candidates": [{path, title, domain, backlinks, pinji, reason, ...}],
            "stats": {total_pages, low_pinji, low_backlinks, stale, candidates}
        }
    """
    import os, time
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    stats = {
        "total_pages": len(pages),
        "low_pinji": 0,
        "low_backlinks": 0,
        "stale": 0,
        "candidates": 0,
    }
    candidates = []

    for p in pages:
        pinji = (p.get("pinji") or "").strip()
        backlinks = len(p.get("linked_from", []))
        path = p.get("path", "")
        domain = p.get("domain", "未知")
        title = p.get("title", path.split("/")[-1])

        # 条件1: 下品
        if pinji != "下品":
            continue
        stats["low_pinji"] += 1

        # 条件2: 入链少
        if backlinks >= min_backlinks:
            continue
        stats["low_backlinks"] += 1

        # 条件3: 超过 stale 天未改
        # 检查文件 mtime
        full_path = os.path.join(vault_path, path + ".md")
        if os.path.exists(full_path):
            mtime = os.path.getmtime(full_path)
            file_mtime = datetime.fromtimestamp(mtime, tz=timezone.utc)
            age_days = (now - file_mtime).days
            if age_days < stale_days:
                continue
        else:
            age_days = -1  # 文件不存在，跳过
            continue
        stats["stale"] += 1

        candidates.append({
            "path": path,
            "title": title,
            "domain": domain,
            "backlinks": backlinks,
            "pinji": pinji,
            "age_days": age_days,
            "reason": f"下品·{pinji} | 入链{backlinks}<{min_backlinks} | {age_days}天未改",
        })

    # 按 age_days 降序排列
    candidates.sort(key=lambda x: -x["age_days"])
    stats["candidates"] = len(candidates)

    return {
        "candidates": candidates,
        "stats": stats,
        "config": {
            "stale_days": stale_days,
            "min_backlinks": min_backlinks,
        },
    }