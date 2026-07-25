# -*- coding: utf-8 -*-
"""
raw_derive.py — 灵台原料自动推导引擎
====================================

零 LLM 消耗，纯规则管线。对单条原料自动推导：
  标题 / 摘要 / 置信度 / 品级建议 / 去重指纹 / 来源分类 / 关联检测

借鉴：
  - Afu (LearnPrompt/afu-llm-todo) deriveInboxCandidate — 多级 fallback + 扣分式置信度
  - 灵台 DedupEngine — SHA-256 / SimHash / Levenshtein 三层去重

用法：
    from raw_derive import derive_raw_candidate, batch_derive

    info = derive_raw_candidate("原料/含人量.md")
    # → {title, excerpt, confidence, grade_suggestion, ...}

    stats = batch_derive("原料/", limit=50)
    # → {total, refined, candidates: [...], summary: {...}}
"""

import glob
import hashlib
import json
import os
import re
import sys
from datetime import datetime
from typing import Optional


# ── 路径 ──────────────────────────────────────────────────────────
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_VAULT = os.environ.get("LINGTAI_VAULT",
                         os.path.normpath(os.path.join(_THIS_DIR, "..", "..")))


# ── 停用词（用于标题去重，来自 Afu）+ 灵台补充 ────────────────
_STOP_WORDS = frozenset({
    '的', '了', '是', '在', '有', '和', '与', '从', '为', '把', '被',
    '对', '让', '能', '也', '都', '就', '到', '中', '上', '下', '来',
    '去', '会', '要', '这', '那', '个', '着', '过', '一', '不', '我',
    '你', '他', '她', '它', '们', '而', '但', '或', '若', '虽', '因',
    '所', '以', '之', '其', '该', '此', '每', '各',
})


# ── 域关键词映射（用于建议目标域） ─────────────────────────────
_DOMAIN_KEYWORDS = {
    "00-思考与认知": ["认知", "思考", "判断", "框架", "思维", "心智", "含人量",
                     "O与π", "追问", "独立思考", "元认知", "认知升级"],
    "01-内容创作": ["写作", "公众号", "小红书", "抖音", "选题", "内容创作",
                     "标题", "配图", "发布", "脚本", "视频", "文案"],
    "02-成长与日常": ["成长", "日常", "习惯", "自律", "时间管理", "效率",
                       "复盘", "反思", "自我"],
    "03-社会观察": ["社会", "群体", "公共", "舆论", "文化", "现象", "世代",
                     "少年", "哲学", "教育", "焦虑"],
    "04-身体与健康": ["健康", "运动", "饮食", "睡眠", "身体", "健身", "断食",
                       "营养", "蛋白质"],
    "05-哲学与思想": ["哲学", "存在", "自由", "意志", "意义", "道德", "伦理",
                       "庄子", "道德经", "佛", "禅", "道"],
    "06-商业与投资": ["商业", "投资", "创业", "副业", "变现", "赚钱", "流量",
                       "IP", "品牌", "营销", "商业模式"],
    "07-工具与AI": ["AI", "人工智能", "工具", "MCP", "Agent", "LLM", "记忆",
                     "知识库", "Obsidian", "编程", "代码", "Git"],
    "08-教育": ["教育", "学习", "考试", "课程", "知识", "读书", "阅读",
                 "理解", "方法", "训练"],
    "99-一人公司": ["一人公司", "自媒体", "IP打造", "个人品牌", "自由职业",
                     "solopreneur", "独立开发"],
}


# ── 辅助函数 ──────────────────────────────────────────────────────

def _try_read(path: str, max_bytes: int = 20000) -> Optional[str]:
    """尝试读取文件，自动检测 GBK/UTF-8 编码。
    
    返回 None 表示读取失败（文件不存在/二进制/编码全失败）。
    """
    if not os.path.isfile(path):
        return None
    raw = open(path, 'rb').read()
    # 先试 UTF-8
    for enc in ('utf-8', 'gbk', 'gb18030', 'gb2312'):
        try:
            return raw.decode(enc)[:max_bytes]
        except (UnicodeDecodeError, LookupError):
            continue
    # 兜底：忽略错误
    return raw.decode('utf-8', errors='ignore')[:max_bytes]


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """解析 frontmatter，返回 (fields_dict, body)。
    
    兼容：
      - 无 frontmatter 的文件
      - 字段名含中文（如 `处理状态: 已提炼`）
      - 含数组值的字段（tags: [a, b, c]）
    """
    m = re.match(r'^---\s*\n(.*?)\n(?:---|\.\.\.)', text, re.DOTALL)
    if not m:
        return {}, text.strip()

    fm = {}
    body = text[m.end():].strip()
    lines = m.group(1).split('\n')

    i = 0
    while i < len(lines):
        line = lines[i]
        # 匹配 `key: value` 或 `key:`（数组开始）
        kv = re.match(r'^([\w\u4e00-\u9fff\-]+):\s*(.*)', line)
        if not kv:
            i += 1
            continue
        key = kv.group(1).strip()
        val = kv.group(2).strip()

        # 数组值（缩进列表）
        if val == '' and i + 1 < len(lines) and re.match(r'^\s+-\s+', lines[i + 1]):
            items = []
            i += 1
            while i < len(lines) and re.match(r'^\s+-\s+', lines[i]):
                items.append(re.sub(r'^\s+-\s+', '', lines[i]).strip().strip('"\'“”'))
                i += 1
            fm[key] = items
            continue

        # 普通值
        fm[key] = val.strip('"\'“”')
        i += 1

    return fm, body


def _title_keywords(title: str) -> set[str]:
    """中文单字切分 + 去停用词（来自 Afu titleKeywords）。"""
    if not title:
        return set()
    # 去标点，按空格分割
    cleaned = re.sub(r'[，。！？、：；「」【】《》()（）\[\]\/\\,.!?;:\-_\s]', ' ', title)
    tokens = cleaned.split()
    result = set()
    for t in tokens:
        if re.search(r'[\u4e00-\u9fff]', t):
            # 中文：按单字切分
            for ch in t:
                if ch not in _STOP_WORDS:
                    result.add(ch.lower())
        else:
            # 非中文：整词保留
            if t.lower() not in _STOP_WORDS:
                result.add(t.lower())
    return result


def _keyword_overlap(a: str, b: str) -> float:
    """标题关键词重叠率（0~1），≥0.5 视为重复。"""
    ka, kb = _title_keywords(a), _title_keywords(b)
    if not ka or not kb:
        return 0.0
    shared = len(ka & kb)
    return shared / min(len(ka), len(kb))


def _detect_source(fm: dict, name: str) -> str:
    """检测原料来源类型。"""
    source = _fm_get(fm, '来源')
    if source:
        if 'IMA' in source or 'IMA笔记' in source:
            return 'ima'
        if '公众号' in source:
            return 'wechat'
        return 'other'
    if name.startswith('IMA_'):
        return 'ima'
    if name.startswith('对话'):
        return 'conversation'
    return 'unknown'


def _suggest_domain(title: str, excerpt: str, source: str) -> Optional[str]:
    """基于关键词匹配建议目标域。
    
    优先级：
      1. source 已知 + 标题含工程关键词 → 07-工具与AI
      2. 关键词匹配
      3. IMA 笔记兜底 → 07-工具与AI
    """
    text = f"{title} {excerpt}"
    
    # 优先：工程相关 IMA 笔记 → 07-工具与AI
    eng_kw = ['灵台', '灵识', 'MCP', '工程', '代码', '工具', 'Agent', 'AI', 'skillopt', 'Harness']
    if source == 'ima' and any(kw in title for kw in eng_kw):
        return '07-工具与AI'
    
    # 关键词匹配（稀有度加权：高频词权重低，避免通用词偏向 07）
    domain_map = _DOMAIN_KEYWORDS
    # 预计算每个词出现在几个域中
    _kw_domain_count = {}
    for kw_list in domain_map.values():
        for kw in kw_list:
            _kw_domain_count[kw] = _kw_domain_count.get(kw, 0) + 1
    
    scores = []
    for domain, keywords in domain_map.items():
        score = sum(
            1.0 / max(_kw_domain_count.get(kw, 1), 1)
            for kw in keywords if kw in text
        )
        if score > 0:
            scores.append((score, domain))
    if scores:
        scores.sort(reverse=True)
        return scores[0][1]
    
    # IMA 笔记兜底 → 07-工具与AI
    if source == 'ima':
        return '07-工具与AI'
    return None


def _estimate_chinese_ratio(text: str) -> float:
    """估算中文字符占比。"""
    if not text:
        return 0.0
    cn = len(re.findall(r'[\u4e00-\u9fff]', text))
    return cn / max(len(text), 1)


def _fm_get(fm: dict, key: str, default: str = '') -> str:
    """安全从 frontmatter 获取字符串值，兼容 list 类型。"""
    val = fm.get(key, default)
    if isinstance(val, list):
        return ' '.join(str(v) for v in val)
    return str(val).strip() if val else ''


def _is_generic_title(title: str) -> bool:
    """判断是否为通用/无意义标题。"""
    if not title or not title.strip():
        return True
    t = title.strip()
    if re.match(r'^(untitled|readme|无标题|未命名)$', t, re.IGNORECASE):
        return True
    if len(t) < 2:
        return True
    return False


# ── 主推导函数 ───────────────────────────────────────────────────

def derive_raw_candidate(
    file_path: str,
    vault_root: str = None,
    dedup_hashes: dict = None,
) -> dict:
    """零 LLM 推导单条原料的元数据。
    
    Args:
        file_path: 原料文件的绝对路径。
        vault_root: 灵台仓库根目录（用于计算相对路径）。
        dedup_hashes: 可选，外部传入的 {sha256: [paths]} 去重索引。
                      不传则跳过 L1 去重检查。
    
    Returns:
        dict 包含以下字段：
        
        基础信息
          path: str          — 相对 vault_root 的路径
          name: str          — 文件名
          size: int          — 文件字节数
          error: str|None    — 读取失败时的错误信息
        
        内容推导
          title: str         — 4 级 fallback 后的标题
          excerpt: str       — 前 3 行有意义文本，≤180 字
          source: str        — 来源分类 (ima/wechat/conversation/unknown)
          has_title_fm: bool — frontmatter 是否有标题字段
          has_date: bool     — frontmatter 是否有日期字段
          has_source_fm: bool — frontmatter 是否有来源字段
          is_generic_title: bool — 标题是否通用/无意义
        
        提炼状态
          is_refined: bool           — 是否已提炼（检查处理状态/状态字段）
          refine_date: str|None      — 处理日期
          refine_grade: str|None     — 已有提炼分级
          refine_summary: str|None   — 已有提炼摘要
          status_field: str|None     — 哪个字段标记了状态（'处理状态'/'状态'）
        
        置信度评估
          confidence: 'high'|'medium'|'low'
          reasons: list[str]         — 扣分原因（空=无扣分）
          score: int                 — 原始扣分（0=high, 1=medium, ≥2=low）
        
        品级建议（仅当 is_refined=False 时有效）
          grade_suggestion: '快速'|'正常'|'完整'
          grade_reason: str          — 建议理由
        
        去重信息
          dedup: dict|null           — {sha256, title_overlap, ...}
        
        关联检测
          backlinks: list[str]       — 回链目标
          suggested_domain: str|None — 建议的丹房域
        
        正文统计
          body_stats: dict           — {lines, chars, cn_ratio}
    """
    # ── 读取 ─────────────────────────────────────────────────
    text = _try_read(file_path)
    if text is None:
        return {
            "path": file_path,
            "name": os.path.basename(file_path),
            "error": "文件不存在或无法读取",
            "confidence": "low",
            "reasons": ["文件不存在"],
        }

    name = os.path.basename(file_path)
    size = os.path.getsize(file_path)
    vault_root = vault_root or _VAULT
    rel_path = os.path.relpath(file_path, vault_root).replace('\\', '/')

    # ── 解析 frontmatter ────────────────────────────────────
    fm, body = _parse_frontmatter(text)
    body = body.strip()

    # ── 1. 标题提取（4 级 fallback，来自 Afu）───────────────
    raw_title = (
        _fm_get(fm, '标题') or
        _fm_get(fm, 'title') or
        _fm_get(fm, 'description') or
        re.sub(r'\.md$', '', name) or
        ''
    )
    # 清理标题（去掉多余空格、引号）
    title = re.sub(r'\s+', ' ', raw_title).strip().strip('"\'"”“')
    has_title_fm = bool(_fm_get(fm, '标题') or _fm_get(fm, 'title'))
    is_generic = _is_generic_title(title)
    if is_generic and has_title_fm:
        # frontmatter 有标题但无意义，降级到文件名
        fallback = re.sub(r'\.md$', '', name)
        if not _is_generic_title(fallback):
            title = fallback

    # ── 2. 摘要提取（去掉格式后取前 3 行，来自 Afu）─────────
    excerpt = ''
    meaningful = []
    for line in body.split('\n'):
        cleaned = re.sub(r'^#+\s+', '', line).strip()
        cleaned = re.sub(r'^>\s?', '', cleaned).strip()
        cleaned = re.sub(r'!\[\[.*?\]\]', '', cleaned)
        cleaned = re.sub(r'\[\[(.*?)\]\]', r'\1', cleaned)
        cleaned = re.sub(r'\s+', ' ', cleaned).strip()
        if not cleaned or len(cleaned) < 8:
            continue
        # 跳过纯分隔线
        if re.match(r'^[*\-—]{3,}$', cleaned):
            continue
        # 跳过来源信息行（IMA 笔记常见）
        if re.match(r'^来源[：:]', cleaned):
            continue
        # 跳过与 frontmatter 标题重复的行
        if title and (cleaned == title or cleaned.startswith(title[:20]) or title.startswith(cleaned[:20])):
            continue
        meaningful.append(cleaned)
    excerpt = ' '.join(meaningful[:3])[:180]

    # ── 3. 提炼状态检测 ────────────────────────────────────
    # 兼容 `处理状态: 已提炼` / `状态: 已提炼` / `refine_status: done`
    # 修复：扫描全文所有 frontmatter 块（兼容双重 frontmatter 损坏文件），
    #       并把「已跳过」等终态纳入，避免把已处理的料误报为 pending。
    status_field = None
    is_refined = False
    is_skipped = False
    is_terminal = False
    refine_date = None
    refine_grade = None
    refine_summary = None

    _REFINED_VALUES = ('已提炼', 'processed', 'done', 'refined')
    _SKIPPED_VALUES = ('已跳过', 'skipped', 'ignored', '放弃', '废弃', 'duplicate', '已覆盖')

    # 先取第一个 frontmatter 块（_parse_frontmatter 的结果）
    _status_hits = []
    for sf in ('处理状态', '状态', 'refine_status'):
        v = _fm_get(fm, sf)
        if v:
            _status_hits.append((sf, v))
    # 再扫描全文所有 `key: value` 形态的状态字段（含双重 frontmatter 的后续块）
    for m in re.finditer(r'^(处理状态|状态|refine_status)\s*[:：]\s*(.+)$', text, re.MULTILINE):
        _status_hits.append((m.group(1), m.group(2).strip()))

    for sf, val in _status_hits:
        if val in _REFINED_VALUES:
            is_refined = True
            status_field = status_field or sf
            refine_date = _fm_get(fm, '处理日期') or _fm_get(fm, 'refine_date')
            refine_grade = _fm_get(fm, '提炼分级') or _fm_get(fm, 'refine_grade')
            refine_summary = _fm_get(fm, '提炼摘要') or _fm_get(fm, 'refine_summary')
        elif val in _SKIPPED_VALUES:
            is_skipped = True
            status_field = status_field or sf
    is_terminal = is_refined or is_skipped

    # ── 4. 来源检测 ─────────────────────────────────────────
    source = _detect_source(fm, name)
    has_source_fm = bool(_fm_get(fm, '来源'))
    has_date = bool(_fm_get(fm, '日期') or _fm_get(fm, 'date'))

    # ── 5. 置信度评估（扣分制，来自 Afu + 灵台增强）─────────
    reasons = []
    
    # 5a. 正文信息太少
    body_text = re.sub(r'^#.*$', '', body, flags=re.MULTILINE).strip()
    body_len = len(body_text)
    if body_len < 200:
        reasons.append(f"正文信息太少（{body_len} 字符）")
    elif body_len < 500:
        reasons.append(f"正文偏短（{body_len} 字符）")
    
    # 5b. 标题通用/无意义
    if is_generic:
        reasons.append("标题需要人工确认")
    
    # 5d. 正文中文字符占比过低（纯英文/代码）
    cn_ratio = _estimate_chinese_ratio(body)
    if cn_ratio < 0.1 and body_len > 100:
        reasons.append("正文中文字符占比过低")

    # 计算置信度
    score = len(reasons)
    if score == 0:
        confidence = 'high'
    elif score == 1:
        confidence = 'medium'
    else:
        confidence = 'low'

    # ── 6. 品级建议（仅未处理时有效）─────────────────────────
    grade_suggestion = None
    grade_reason = ''
    if not is_terminal:
        if size > 20480:  # > 20KB
            grade_suggestion = '完整'
            grade_reason = f"文件较大（{size//1024}KB），需综合提炼"
        elif confidence == 'high' and size < 5120:
            grade_suggestion = '快速'
            grade_reason = f"信息完整（{size//1024}KB），可快速提炼"
        elif confidence == 'low':
            grade_suggestion = '待补充'
            grade_reason = f"信息不足（{reasons[0] if reasons else '未知'}），建议补充后再提炼"
        else:
            grade_suggestion = '正常'
            grade_reason = f"中等体量（{size//1024}KB）"

    # ── 7. 去重检查 ─────────────────────────────────────────
    dedup_result = None
    if dedup_hashes is not None and not is_terminal:
        body_sha = hashlib.sha256(body.encode('utf-8', errors='replace')).hexdigest()
        if body_sha in dedup_hashes:
            dup_paths = dedup_hashes[body_sha]
            dedup_result = {
                "matched": True,
                "type": "sha256_exact",
                "duplicates": dup_paths,
            }

    # ── 8. 关联检测 ─────────────────────────────────────────
    backlinks = []
    bl_raw = fm.get('回链')
    if isinstance(bl_raw, list):
        bl_text = ' '.join(bl_raw)
    else:
        bl_text = (bl_raw or '').strip()
    if bl_text:
        links = re.findall(r'\[\[(.*?)\]\]', bl_text)
        backlinks = [l.strip() for l in links]

    # 建议目标域
    suggested_domain = _suggest_domain(title, excerpt, source)

    # ── 9. 正文统计 ─────────────────────────────────────────
    body_stats = {
        "lines": len(body.split('\n')),
        "chars": len(body),
        "cn_ratio": round(cn_ratio, 3),
        "kb": round(size / 1024, 1),
    }

    # ── 组装返回 ────────────────────────────────────────────
    result = {
        "path": rel_path,
        "name": name,
        "size": size,
        "error": None,

        "title": title,
        "excerpt": excerpt,
        "source": source,
        "has_title_fm": has_title_fm,
        "has_date": has_date,
        "has_source_fm": has_source_fm,
        "is_generic_title": is_generic,

        "is_refined": is_refined,
        "is_skipped": is_skipped,
        "is_terminal": is_terminal,
        "refine_date": refine_date,
        "refine_grade": refine_grade,
        "refine_summary": refine_summary,
        "status_field": status_field,

        "confidence": confidence,
        "reasons": reasons,
        "score": score,

        "backlinks": backlinks,
        "suggested_domain": suggested_domain,

        "body_stats": body_stats,
    }

    if not is_terminal:
        result["dedup"] = dedup_result
        result["grade_suggestion"] = grade_suggestion
        result["grade_reason"] = grade_reason

    return result


# ── 批量扫描 ─────────────────────────────────────────────────────

def batch_derive(
    raw_dir: str = None,
    limit: int = 200,
    vault_root: str = None,
    dedup_hashes: dict = None,
    skip_refined: bool = True,
    sort_by: str = "newest",
) -> dict:
    """批量扫描原料目录，返回推导结果 + 统计摘要。
    
    Args:
        raw_dir: 原料目录绝对路径。默认 <vault_root>/原料/
        limit: 最大返回数。
        vault_root: 灵台仓库根目录。
        dedup_hashes: 外部去重索引。
        skip_refined: True=跳过已提炼的，False=全部返回。
        sort_by: "newest"（默认，创建时间倒序，新料优先）| "oldest"（创建时间升序，旧料优先）。
    
    Returns:
        {total, refined, pending, candidates: [...], summary: {...}}
    """
    vault_root = vault_root or _VAULT
    raw_dir = raw_dir or os.path.join(vault_root, '原料')

    if not os.path.isdir(raw_dir):
        return {"error": f"目录不存在: {raw_dir}"}

    # 扫描所有 .md 文件（递归子目录），按创建时间排序
    sort_key = os.path.getctime
    files = sorted(
        glob.glob(os.path.join(raw_dir, '**', '*.md'), recursive=True),
        key=sort_key,
        reverse=(sort_by == "newest"),
    )

    total = len(files)
    # 排除目录索引文件
    files = [f for f in files if os.path.basename(f) != '_index.md']
    candidates = []
    counts = {"refined": 0, "pending": 0, "error": 0}

    for f in files[:limit]:
        info = derive_raw_candidate(f, vault_root, dedup_hashes)
        if info.get("error"):
            counts["error"] += 1
        elif info.get("is_terminal"):
            counts["refined"] += 1
        else:
            counts["pending"] += 1
        
        if skip_refined and info.get("is_terminal"):
            continue
        candidates.append(info)

    # 统计摘要
    summary = {
        "total_raw": total,
        "total_refined": counts["refined"],
        "total_pending": counts["pending"],
        "in_limit": len(candidates),
        "by_confidence": {},
        "by_grade": {},
        "by_source": {},
        "no_title": 0,
        "no_source": 0,
        "generic_title": 0,
    }
    for c in candidates:
        conf = c.get("confidence", "unknown")
        summary["by_confidence"][conf] = summary["by_confidence"].get(conf, 0) + 1
        g = c.get("grade_suggestion")
        if g:
            summary["by_grade"][g] = summary["by_grade"].get(g, 0) + 1
        s = c.get("source", "unknown")
        summary["by_source"][s] = summary["by_source"].get(s, 0) + 1
        if not c.get("has_title_fm"):
            summary["no_title"] += 1
        if not c.get("has_source_fm"):
            summary["no_source"] += 1
        if c.get("is_generic_title"):
            summary["generic_title"] += 1

    return {
        "total": total,
        "refined": counts["refined"],
        "pending": counts["pending"],
        "errors": counts["error"],
        "candidates": candidates,
        "summary": summary,
    }


def is_raw_terminal(file_path: str) -> bool:
    """轻量判定原料是否已处理（已提炼/已跳过等终态）。

    扫描全文所有 frontmatter 块的 `处理状态`/`状态`/`refine_status` 字段，
    兼容双重 frontmatter 损坏文件。供 refine_status 全量统计复用，避免重复解析。
    """
    text = _try_read(file_path)
    if not text:
        return False
    _REFINED = ('已提炼', 'processed', 'done', 'refined')
    _SKIPPED = ('已跳过', 'skipped', 'ignored', '放弃', '废弃', 'duplicate')
    for m in re.finditer(r'^(处理状态|状态|refine_status)\s*[:：]\s*(.+)$', text, re.MULTILINE):
        v = m.group(2).strip()
        if v in _REFINED or v in _SKIPPED:
            return True
    return False


# ── 自检 ────────────────────────────────────────────────────────

if __name__ == '__main__':
    # 命令行测试
    args = sys.argv[1:]
    if args:
        path = args[0]
        info = derive_raw_candidate(path)
        import json
        print(json.dumps(info, ensure_ascii=False, indent=2))
    else:
        # 批量扫描
        stats = batch_derive(limit=10)
        print(json.dumps(stats.get("summary", {}), ensure_ascii=False, indent=2))
        print(f"\n--- 共 {stats['total']} 条，展示 {len(stats['candidates'])} 条 ---")
        for c in stats["candidates"][:5]:
            print(f"  [{c['confidence']}] {c['title'][:40]} → {c.get('grade_suggestion', '已提炼')}")