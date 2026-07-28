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

import hashlib
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
_COLLISION_CACHE_FILENAME = "danfang_collisions.json"


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


def _compute_signature(pages: List[dict]) -> str:
    """计算所有页面的内容签名，用于碰撞结果缓存。

    基于每页的 path + content_hash 生成 SHA256 签名。
    任一页新增/修改/删除 → 签名变化 → 自动触发重算。
    """
    items = sorted(
        f"{p.get('path','')}:{p.get('content_hash') or p.get('body_hash') or ''}"
        for p in pages
    )
    return hashlib.sha256("|".join(items).encode("utf-8")).hexdigest()


def _load_collision_cache(vault_path: str) -> dict:
    """加载碰撞结果缓存。"""
    cache_path = Path(vault_path) / ".tool" / "lingtai-kb" / "data" / _COLLISION_CACHE_FILENAME
    if cache_path.exists():
        try:
            return json.loads(cache_path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _save_collision_cache(vault_path: str, cache: dict):
    """持久化碰撞结果缓存。"""
    cache_dir = Path(vault_path) / ".tool" / "lingtai-kb" / "data"
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path = cache_dir / _COLLISION_CACHE_FILENAME
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

    # ── 碰撞结果缓存检测（按页面签名）──
    signature = _compute_signature(pages)
    collision_cache = _load_collision_cache(vault_path)
    cached = collision_cache.get(signature)
    if cached is not None:
        # 构建 path → page 映射（用于 title 查询）
        path_to_page = {p.get("path", ""): p for p in pages}
        all_pairs = cached.get("all_pairs", [])
        all_dups = cached.get("all_duplicates", [])

        # 按 domain_filter 过滤
        if domain_filter:
            all_pairs = [c for c in all_pairs if domain_filter in (c.get("domain_a", ""), c.get("domain_b", ""))]
            all_dups = [c for c in all_dups if domain_filter in (c.get("domain_a", ""), c.get("domain_b", ""))]

        # 按相似度范围过滤（缓存可能存的是 0.6-0.75，调用可能更窄）
        all_pairs = [c for c in all_pairs if min_sim <= c.get("similarity", 0) <= max_sim]
        all_dups = [c for c in all_dups if min_sim <= c.get("similarity", 0) <= max_sim]

        # 取 top_n 并生成理由
        true_collisions = all_pairs[:top_n]
        duplicates = all_dups[:top_n]
        for c in true_collisions:
            if "reason" not in c:
                c["reason"] = _generate_reason(c)

        log.info("concept_collide cache HIT", extra={"signature": signature[:12], "pairs": len(all_pairs), "dups": len(all_dups)})
        return {
            "total_pages": len(pages),
            "pages_embedded": cached.get("pages_embedded", 0),
            "pairs_evaluated": cached.get("pairs_evaluated", 0),
            "collisions": true_collisions,
            "duplicates": duplicates,
            "stats": {
                "min_similarity": min_sim,
                "max_similarity": max_sim,
                "top_n": top_n,
                "domain_filter": domain_filter or "全部",
                "domain_skip_same": domain_skip_same,
                "duplicates_filtered": len(duplicates),
                "cached": True,
            },
            "_cache_info": {"signature": signature[:12], "hit": True},
        }

    # ── 缓存未命中：全量计算 ──
    log.info("concept_collide cache MISS, computing...", extra={"pages": len(pages)})

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

    # ── 保存全量碰撞结果到缓存（top_n 切片前）──
    collision_cache[signature] = {
        "all_pairs": true_collisions,
        "all_duplicates": duplicates,
        "pages_embedded": len(vectors),
        "pairs_evaluated": all_pairs_evaluated,
    }
    _save_collision_cache(vault_path, collision_cache)

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
        "_cache_info": {"signature": signature[:12], "hit": False},
    }


def _extract_concepts(page: dict) -> list:
    """从页面索引条目中提取概念标签。

    支持两种格式：
    - list: tags: ["概念A", "概念B"]（index.json 标准格式）
    - str: tags: "概念A, 概念B"（frontmatter 原始格式）
    """
    tags_raw = page.get("tags", [])
    if not tags_raw:
        return []

    # 如果已经是 list，直接用
    if isinstance(tags_raw, list):
        candidates = tags_raw
    elif isinstance(tags_raw, str):
        tags_str = tags_raw.strip()
        if tags_str.startswith("[") and tags_str.endswith("]"):
            tags_str = tags_str[1:-1]
        candidates = [t.strip().strip("'").strip('"') for t in tags_str.split(",")]
    else:
        return []

    # 过滤掉占位符和无意义标签
    _skip = {"提炼", "概念", "方法", "工具", "认知", "AI", "系统", "日志", "写作", "哲学", "设计", "创作", "商业", "教育", "健康", "成长"}
    return [c for c in candidates if c and c not in _skip]


def _extract_all_concepts(pages: List[dict]) -> dict:
    """从所有丹房页提取概念 → 页面映射。

    Returns:
        dict: {concept_name: {pages: [path, ...], domains: set, count: int}}
    """
    concept_map = {}
    for p in pages:
        path = p.get("path", "")
        domain = p.get("domain", "未知")
        concepts = _extract_concepts(p)
        for c in concepts:
            if c not in concept_map:
                concept_map[c] = {"pages": [], "domains": set(), "count": 0}
            concept_map[c]["pages"].append(path)
            concept_map[c]["domains"].add(domain)
            concept_map[c]["count"] += 1
    return concept_map


def concept_collide_pages(
    vault_path: str,
    pages: List[dict],
    top_n: int = _TOP_N_DEFAULT,
    min_sim: float = _MIN_SIM,
    max_sim: float = _MAX_SIM,
    min_pages: int = 2,
) -> dict:
    """概念级碰撞：基于标签字段做概念间的语义相似度匹配。

    与页面级 collide() 的区别：
    - 操作粒度：概念（标签）而非页面（summary）
    - 输出：概念对 + 各自关联的页面
    - 过滤：只碰撞至少出现在 min_pages 个页面中的概念

    流程：
    1. 从所有页面 frontmatter 提取标签
    2. 过滤低频概念（< min_pages 页）
    3. 对概念文本做嵌入
    4. 跨域概念对余弦相似度匹配
    5. 返回概念对 + 关联页面

    Returns:
        dict: {
            "total_concepts": int,
            "concepts_embedded": int,
            "collisions": [{concept_a, concept_b, sim, pages_a, pages_b, domains_a, domains_b}, ...],
            "concept_map": {concept: {pages, domains, count}},
            "stats": {...}
        }
    """
    model = _get_model()
    if model is None:
        return {"total_concepts": 0, "concepts_embedded": 0, "collisions": [], "concept_map": {}, "stats": {"error": "模型不可用"}}

    # 1. 提取概念
    concept_map = _extract_all_concepts(pages)
    log.info(f"概念提取：{len(concept_map)} 个唯一概念")

    # 2. 过滤低频概念
    qualified = {c: info for c, info in concept_map.items() if info["count"] >= min_pages}
    log.info(f"概念过滤（≥{min_pages}页）：{len(qualified)}/{len(concept_map)}")

    if len(qualified) < 2:
        return {"total_concepts": len(concept_map), "concepts_embedded": 0, "collisions": [], "concept_map": concept_map, "stats": {"error": "合格概念不足 2 个"}}

    # 3. 嵌入概念文本
    concept_names = list(qualified.keys())
    vectors = model.encode(concept_names, show_progress_bar=False)
    if vectors is None:
        return {"total_concepts": len(concept_map), "concepts_embedded": 0, "collisions": [], "concept_map": concept_map, "stats": {"error": "嵌入失败"}}

    # 4. 概念对碰撞
    collisions = []
    pairs_evaluated = 0
    for i in range(len(concept_names)):
        for j in range(i + 1, len(concept_names)):
            ci = concept_names[i]
            cj = concept_names[j]
            info_i = qualified[ci]
            info_j = qualified[cj]

            # 跳过同域概念对（如果两个概念的所有页面都在同一域）
            if info_i["domains"] == info_j["domains"] and len(info_i["domains"]) == 1:
                continue

            sim = _cosine_sim(vectors[i], vectors[j])
            pairs_evaluated += 1
            if min_sim <= sim <= max_sim:
                collisions.append({
                    "concept_a": ci,
                    "concept_b": cj,
                    "similarity": round(sim, 4),
                    "pages_a": info_i["pages"],
                    "pages_b": info_j["pages"],
                    "domains_a": list(info_i["domains"]),
                    "domains_b": list(info_j["domains"]),
                    "count_a": info_i["count"],
                    "count_b": info_j["count"],
                    "reason": f"概念碰撞：{ci} × {cj}（相似度 {sim:.3f}，分别出现在 {info_i['count']}/{info_j['count']} 页）",
                })

    collisions.sort(key=lambda x: x["similarity"], reverse=True)
    collisions = collisions[:top_n]

    return {
        "total_concepts": len(concept_map),
        "concepts_embedded": len(qualified),
        "pairs_evaluated": pairs_evaluated,
        "collisions": collisions,
        "concept_map": {c: {"pages": info["pages"], "domains": list(info["domains"]), "count": info["count"]} for c, info in qualified.items()},
        "stats": {
            "min_similarity": min_sim,
            "max_similarity": max_sim,
            "top_n": top_n,
            "min_pages": min_pages,
            "concepts_filtered": len(qualified),
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


def raw_coldness_scan(
    vault_path: str,
    stale_days: int = 60,
    max_results: int = 30,
) -> dict:
    """原料冷度扫描——检测长期未提炼的原料，落实规则 16b（定期减负）。

    冷度评分维度：
    1. 年龄分：距「日期」frontmatter 的天数 / 365（归一化 0-1）
    2. 状态乘数：未标注 ×2.0 / 待提炼 ×1.5 / 已提炼 ×0.3
    3. 无回链乘数：无「回链」字段 ×1.3
    4. 短内容乘数：< 200 字 ×1.2（碎片原料，价值低）

    Args:
        vault_path: vault 根路径
        stale_days: 至少冷于此天数才纳入扫描（默认 60 天）
        max_results: 最多返回条数

    Returns:
        dict: {
            "candidates": [{filename, title, coldness, age_days, status, has_backlink, reason}],
            "stats": {total_raw, processed, unprocessed, no_status, candidates}
        }
    """
    import os, re
    from datetime import datetime, timezone

    raw_dir = os.path.join(vault_path, "原料")
    if not os.path.isdir(raw_dir):
        return {"error": "原料目录不存在", "candidates": [], "stats": {}}

    now = datetime.now(timezone.utc)
    files = [f for f in os.listdir(raw_dir) if f.endswith('.md')]

    stats = {
        "total_raw": len(files),
        "processed": 0,
        "unprocessed": 0,
        "no_status": 0,
        "candidates": 0,
        "oldest_age_days": 0,
    }

    # 辅助：从正文提取「创建于：YYYY-MM-DD」日期
    BODY_DATE_RE = re.compile(r'创建于[：:]\s*(\d{4}-\d{2}-\d{2})')
    # 辅助：frontmatter 字段提取
    FM_RE = re.compile(r'^([\w\u4e00-\u9fff]+)\s*[:：]\s*(.+)', re.MULTILINE)

    candidates = []

    for fname in files:
        fpath = os.path.join(raw_dir, fname)
        try:
            with open(fpath, 'r', encoding='utf-8-sig') as fp:
                content = fp.read()
        except Exception:
            continue

        # 解析 frontmatter（取第一个 --- 块）
        fm = {}
        fm_match = re.match(r'^---\s*\n(.*?)\n---', content, re.DOTALL)
        if fm_match:
            for m in FM_RE.finditer(fm_match.group(1)):
                fm[m.group(1).strip()] = m.group(2).strip()

        # ── 年龄计算 ──
        date_str = fm.get('日期', '') or fm.get('date', '')
        if not date_str:
            # 回退到正文「创建于」日期
            body_m = BODY_DATE_RE.search(content)
            if body_m:
                date_str = body_m.group(1)
        if not date_str:
            # 回退到文件 mtime
            try:
                mtime = os.path.getmtime(fpath)
                date_dt = datetime.fromtimestamp(mtime, tz=timezone.utc)
                age_days = (now - date_dt).days
            except OSError:
                age_days = 0
        else:
            try:
                date_dt = datetime.strptime(date_str[:10], '%Y-%m-%d').replace(tzinfo=timezone.utc)
                age_days = (now - date_dt).days
            except ValueError:
                age_days = 0

        # ── 状态判定 ──
        status_raw = fm.get('处理状态', '') or fm.get('状态', '')
        is_refined = '已提炼' in status_raw
        is_unprocessed = '待提炼' in status_raw
        has_status = bool(status_raw)

        if is_refined:
            stats["processed"] += 1
            status = "已提炼"
            status_mult = 0.3
        elif is_unprocessed:
            stats["unprocessed"] += 1
            status = "待提炼"
            status_mult = 1.5
        else:
            stats["no_status"] += 1
            status = "未标注"
            status_mult = 2.0

        if age_days < stale_days:
            continue  # 不够冷，跳过

        # ── 回链检测 ──
        has_backlink = bool(fm.get('回链', ''))

        # ── 内容长度 ──
        body_start = content.find('---\n', content.find('---') + 3)
        body_text = content[body_start + 4:] if body_start >= 0 else content
        word_count = len(re.sub(r'\s+', '', body_text))
        short_mult = 1.2 if word_count < 200 else 1.0

        # ── 冷度评分 ──
        age_score = min(age_days / 365, 1.0)  # 0-1，超过 1 年封顶
        coldness = round(age_score * status_mult * (1.3 if not has_backlink else 1.0) * short_mult, 3)

        title = fm.get('标题', '') or fname.replace('.md', '')

        candidates.append({
            "filename": fname,
            "title": title,
            "coldness": coldness,
            "age_days": age_days,
            "status": status,
            "has_backlink": has_backlink,
            "word_count": word_count,
            "reason": f"冷度 {coldness:.2f} | {status} | {age_days}天 | {'有回链' if has_backlink else '无回链'} | {word_count}字",
        })

        if age_days > stats["oldest_age_days"]:
            stats["oldest_age_days"] = age_days

    # 按冷度降序排列
    candidates.sort(key=lambda x: -x["coldness"])
    candidates = candidates[:max_results]
    stats["candidates"] = len(candidates)

    return {
        "candidates": candidates,
        "stats": stats,
        "config": {
            "stale_days": stale_days,
            "max_results": max_results,
            "mode": "raw_coldness",
        },
    }