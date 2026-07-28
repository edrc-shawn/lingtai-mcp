# -*- coding: utf-8 -*-
"""巡更·自愈管线 — 8 步闭环（检测→分类→修复/L2建议→复查→报告→归档→回写索引）

对齐 llmwiki self-healing：把灵台巡更 L1(自动修)/L2(建议+确认) 升级为系统化闭环。
安全边界：默认 dry-run（不改文件、不刷索引）；--apply 才对 L1 低风险项动刀。
--suggest：对 L2 问题（死胡同、弯引号文件名）生成 AI 建议，辅助人做最终决策。
"""
import os
import re
import sys
import json
import datetime
import subprocess
from collections import Counter

VAULT = os.environ.get("LINGTAI_VAULT", r"C:\Obsidian仓库\edrc\灵台")
DANFANG = os.path.join(VAULT, "丹房")
HEAL_LOG = os.path.join(VAULT, ".tool", "巡更", "self_heal_log.jsonl")
BUILD_INDEX = os.path.join(VAULT, ".tool", "scripts", "build_index.py")

# 命名禁用字符（台律）：弯引号 U+201C/U+201D + 直引号 U+0022
BANNED_CHARS = ["\u201c", "\u201d", "\u0022"]

# 丹房页路径格式
_SCHEMA_RE = re.compile(r"^丹房/\d{2}-[^/]+/.+\.md$")

# 台律标签（出现在正文中的 #标签）
_TAG_RE = re.compile(r"#[\u4e00-\u9fff\w]+")

# wikilink 模式 [[页名]] 或 [[页名|别名]]
_WIKILINK_RE = re.compile(r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]")


def _scan_danfang_pages():
    """遍历所有丹房 schema 页面，yield (rel_path, abspath, raw_text)"""
    for root, _, files in os.walk(DANFANG):
        for f in files:
            if not f.endswith(".md"):
                continue
            p = os.path.join(root, f)
            rel = os.path.relpath(p, VAULT).replace(os.sep, "/")
            if not _SCHEMA_RE.match(rel):
                continue
            try:
                txt = open(p, "r", encoding="utf-8").read()
            except Exception:
                continue
            yield rel, p, txt


def detect():
    """步1 检测：扫描丹房已知问题（仅 schema 知识页）"""
    findings = []
    for rel, p, txt in _scan_danfang_pages():
        # L1：缺 标题 frontmatter
        fm = re.match(r"^---\n(.*?)\n---", txt, re.S)
        has_title = bool(fm and re.search(r"^标题\s*:", fm.group(1), re.M))
        if not has_title:
            findings.append({"type": "missing_title", "path": rel, "level": "L1", "fixable": True})
        # L2：弯引号/直引号文件名
        fname = os.path.basename(p)
        if any(c in fname for c in BANNED_CHARS):
            findings.append({"type": "banned_quote_filename", "path": rel, "level": "L2", "fixable": False})
    return findings


def detect_l2_deadends():
    """检测 L2 图谱问题。

    Returns: {
        'no_outlink': [(path, tags, domain), ...],  # 出链=0，纯粹不存在 wikilink 的页面
        'no_inlink':  [(path, tags, domain), ...],  # 入链=0，有出链但无人链回（lint 死胡同）
    }
    """
    # 第一遍：收集所有出链
    page_outlinks = {}  # {rel: [target_rel, ...]}
    page_tags = {}
    page_domain = {}
    for rel, p, txt in _scan_danfang_pages():
        body = txt
        fm = re.match(r"^---\n.*?\n---\n?", txt, re.S)
        if fm:
            body = txt[fm.end():]
        outlinks = _WIKILINK_RE.findall(body)
        page_outlinks[rel] = outlinks
        page_tags[rel] = _TAG_RE.findall(txt)
        page_domain[rel] = rel.split("/")[0] if "/" in rel else ""

    # 第二遍：计算入链
    page_inlinks = {rel: 0 for rel in page_outlinks}
    # 构建路径→页名映射
    name_to_paths = {}  # {page_name: [rel_path, ...]}
    for rel in page_outlinks:
        name = os.path.splitext(os.path.basename(rel))[0]
        name_to_paths.setdefault(name, []).append(rel)
    
    for rel, outlinks in page_outlinks.items():
        for target_name in outlinks:
            # 尝试匹配丹房页
            for target_rel in name_to_paths.get(target_name, []):
                page_inlinks[target_rel] = page_inlinks.get(target_rel, 0) + 1

    # 分类
    no_outlink = []
    no_inlink = []
    for rel in page_outlinks:
        out_c = len(page_outlinks[rel])
        in_c = page_inlinks.get(rel, 0)
        tags = page_tags[rel]
        domain = page_domain[rel]
        if out_c == 0:
            no_outlink.append((rel, tags, domain, in_c))
        elif in_c == 0:
            no_inlink.append((rel, tags, domain, out_c))

    return {
        'no_outlink': no_outlink,
        'no_inlink': no_inlink,
    }


def _build_tag_index():
    """构建标签→页面索引（用于 L2 建议引擎）"""
    tag_index = {}  # {tag: [rel_path, ...]}
    domain_pages = {}  # {domain: [rel_path, ...]}
    page_tags = {}  # {rel_path: [tag, ...]}
    
    for rel, p, txt in _scan_danfang_pages():
        tags = _TAG_RE.findall(txt)
        page_tags[rel] = tags
        domain = rel.split("/")[0] if "/" in rel else ""
        domain_pages.setdefault(domain, []).append(rel)
        for t in tags:
            tag_index.setdefault(t, []).append(rel)
    
    return tag_index, domain_pages, page_tags


def suggest_l2_links(deadends):
    """对图谱问题页面生成建议。

    Args:
        deadends: detect_l2_deadends() 的返回值

    Returns: {
        'no_outlink': {path: [(target, score, shared_tags), ...]},
        'no_inlink':  {path: [(source, score, shared_tags), ...]},
    }
    """
    tag_index, domain_pages, page_tags = _build_tag_index()
    result = {'no_outlink': {}, 'no_inlink': {}}

    # 出链=0：建议加出链目标
    for rel, tags, domain, in_c in deadends.get('no_outlink', []):
        result['no_outlink'][rel] = _suggest_outlink_targets(rel, tags, domain, tag_index, domain_pages, page_tags)

    # 入链=0：建议应从此页出链的候选源页（逆向：谁该链我）
    for rel, tags, domain, out_c in deadends.get('no_inlink', []):
        result['no_inlink'][rel] = _suggest_inlink_sources(rel, tags, domain, tag_index, domain_pages, page_tags)

    return result


def _suggest_outlink_targets(rel, tags, domain, tag_index, domain_pages, page_tags):
    """建议出链目标：同域标签重叠度最高的页面"""
    suggestions = _score_by_tags(rel, tags, domain, tag_index, domain_pages, page_tags)
    return suggestions[:5]


def _suggest_inlink_sources(rel, tags, domain, tag_index, domain_pages, page_tags):
    """建议入链源：哪些同域页面应引用本页（逆向匹配）"""
    # 找同域中与本页标签重叠的页面，建议它们链到本页
    suggestions = _score_by_tags(rel, tags, domain, tag_index, domain_pages, page_tags)
    return suggestions[:5]


def _score_by_tags(target_rel, tags, domain, tag_index, domain_pages, page_tags):
    """通用标签评分：找出与 target 标签重叠的页面"""
    if not tags:
        same_domain = [p for p in domain_pages.get(domain, []) if p != target_rel]
        return [(p, 0, []) for p in same_domain[:3]]
    
    scored = Counter()
    for t in tags:
        for p in tag_index.get(t, []):
            if p != target_rel:
                scored[p] += 1
    
    # 优先同域
    same_domain_ranked = [
        (p, s) for p, s in scored.most_common()
        if p.split("/")[0] == domain
    ]
    cross_domain_ranked = [
        (p, s) for p, s in scored.most_common()
        if p.split("/")[0] != domain
    ]
    
    top = same_domain_ranked[:5] + cross_domain_ranked[:3]
    result = []
    for p, s in top[:5]:
        shared = [t for t in tags if t in page_tags.get(p, [])]
        result.append((p, s, shared))
    return result


def classify(findings):
    """步2 分类"""
    return (
        [x for x in findings if x["level"] == "L1"],
        [x for x in findings if x["level"] == "L2"],
    )


def fix_l1(l1):
    """步3 修复：仅 L1 低风险项（补 标题）"""
    fixed = []
    for x in l1:
        p = os.path.join(VAULT, x["path"])
        try:
            txt = open(p, "r", encoding="utf-8").read()
            name = os.path.splitext(os.path.basename(p))[0]
            if txt.startswith("---"):
                m = re.match(r"^---\n(.*?)\n---\n?", txt, re.S)
                if not m:
                    continue
                body = m.group(1).rstrip()
                if not re.search(r"^标题\s*:", body, re.M):
                    body += f"\n标题: {name}"
                txt = "---\n" + body + "\n---\n" + txt[m.end():]
            else:
                txt = f"---\n标题: {name}\n---\n\n" + txt
            open(p, "w", encoding="utf-8").write(txt)
            fixed.append(x["path"])
        except Exception as e:
            x["error"] = str(e)
    return fixed


def recheck():
    """步4 复查"""
    return detect()


def report(findings, fixed, recheck_findings, ts, date_str, l2_suggestions=None):
    """步5 报告"""
    out = os.path.join(VAULT, "体检", "日报", "自愈报告", f"自愈报告_{ts}.md")
    l2 = [x for x in findings if x["level"] == "L2"]
    lines = [
        "---", "标题: 巡更自愈报告", f"日期: {date_str}", "---", "",
        f"# 巡更自愈报告 {date_str}", "",
        f"- 检测问题总数: {len(findings)}",
        f"- L1 自动修复: {len(fixed)}",
        f"- L2 待确认: {len(l2)}",
        f"- 复查剩余: {len(recheck_findings)}", "",
        "## L1 已修复", "",
    ]
    lines += [f"- {p}" for p in fixed] or ["- （无）"]
    
    # L2 部分
    lines += ["", "## L2 待确认（需人工决策）", ""]
    if not l2:
        lines += ["- （无）"]
    else:
        for x in l2:
            lines += [f"- {x['path']} ({x['type']})"]
    
    # L2 建议（--suggest 模式额外输出）
    if l2_suggestions:
        lines += ["", "## L2 建议（AI 预判，仅供参考）", ""]
        
        # 出链=0：建议加出链目标
        no_out = l2_suggestions.get('no_outlink', {})
        if no_out:
            lines += ["### 📤 缺出链 — 建议加到](共{}页）".format(len(no_out)), ""]
            for path, sugs in sorted(no_out.items()):
                page_name = os.path.splitext(os.path.basename(path))[0]
                lines += [f"**{page_name}**"]
                if not sugs:
                    lines += ["  - 无同域候选页面", ""]
                    continue
                for i, item in enumerate(sugs):
                    target, score, shared = item
                    target_name = os.path.splitext(os.path.basename(target))[0]
                    shared_str = "、".join(shared[:3])
                    lines += [f"  {i+1}. [[{target_name}]] — 共享: {shared_str}（重叠度 {score}）"]
                lines += [""]
        
        # 入链=0：建议哪些页应链回本页
        no_in = l2_suggestions.get('no_inlink', {})
        if no_in:
            lines += ["### 📥 缺入链 — 建议以下页引用本页（共{}页）".format(len(no_in)), ""]
            for path, sugs in sorted(no_in.items()):
                page_name = os.path.splitext(os.path.basename(path))[0]
                lines += [f"**{page_name}**"]
                if not sugs:
                    lines += ["  - 无同域候选源页", ""]
                    continue
                for i, item in enumerate(sugs):
                    target, score, shared = item
                    target_name = os.path.splitext(os.path.basename(target))[0]
                    shared_str = "、".join(shared[:3])
                    lines += [f"  {i+1}. [[{target_name}]] 应链入 — 共享: {shared_str}（重叠度 {score}）"]
                lines += [""]
    
    lines += ["", "## 检测明细（全量）", ""]
    for x in findings:
        tag = "L1✓已修" if (x["path"] in fixed) else x["level"]
        lines += [f"- [{tag}] {x['path']} ({x['type']})"]
    
    open(out, "w", encoding="utf-8").write("\n".join(lines))
    return out


def archive(findings, fixed, ts, l2_suggestions=None):
    """步6 归档"""
    rec = {
        "ts": ts,
        "total": len(findings),
        "fixed": fixed,
        "l2_remaining": [x["path"] for x in findings if x["level"] == "L2"],
    }
    if l2_suggestions:
        rec["l2_suggestions"] = {
            key: {k: [(t, s, sr) for t, s, sr in v] for k, v in val.items()}
            for key, val in l2_suggestions.items()
        }
    with open(HEAL_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def rewrite_index():
    """步7 回写索引"""
    if os.path.isfile(BUILD_INDEX):
        try:
            subprocess.run(
                [sys.executable, BUILD_INDEX],
                capture_output=True, timeout=120,
                env={**os.environ, "PYTHONIOENCODING": "utf-8"},
            )
            return True
        except Exception:
            return False
    return False


def main():
    apply = "--apply" in sys.argv
    suggest = "--suggest" in sys.argv
    now = datetime.datetime.now()
    ts = now.strftime("%Y%m%d-%H%M")
    date_str = now.strftime("%Y-%m-%d %H:%M")

    # 步 1: 检测
    print(f"[1/8] 检测…")
    findings = detect()
    
    # --suggest 模式：追加图谱问题检测
    l2_suggestions = None
    no_outlink_count = 0
    no_inlink_count = 0
    if suggest:
        print(f"    --suggest 模式：检测图谱问题…")
        deadend_map = detect_l2_deadends()
        no_outlink_count = len(deadend_map['no_outlink'])
        no_inlink_count = len(deadend_map['no_inlink'])
        # 出链=0 作为 L2 findings
        for rel, tags, domain, in_c in deadend_map['no_outlink']:
            findings.append({
                "type": "no_outlink",
                "path": rel,
                "level": "L2",
                "fixable": False,
                "tags": tags,
                "domain": domain,
            })
        # 入链=0 也作为 L2 findings
        for rel, tags, domain, out_c in deadend_map['no_inlink']:
            findings.append({
                "type": "no_inlink",
                "path": rel,
                "level": "L2",
                "fixable": False,
                "tags": tags,
                "domain": domain,
            })
        print(f"    出链=0: {no_outlink_count} | 入链=0: {no_inlink_count}")
    
    print(f"    发现问题 {len(findings)} 条")
    
    # 步 2: 分类
    l1, l2 = classify(findings)
    extra = f"（出链=0{no_outlink_count}/入链=0{no_inlink_count}）" if (no_outlink_count or no_inlink_count) else ""
    print(f"[2/8] 分类: L1={len(l1)} L2={len(l2)}{extra}")

    # 步 3: 修复 L1 或 dry-run
    fixed = []
    if apply:
        print(f"[3/8] 修复 L1…")
        fixed = fix_l1(l1)
        print(f"    修复 {len(fixed)} 条")
    else:
        print(f"[3/8] 修复 L1… (dry-run，跳过)")

    # 步 3.5: L2 建议（--suggest 模式）
    if suggest and (no_outlink_count > 0 or no_inlink_count > 0):
        print(f"[3.5/8] L2 建议引擎…")
        deadend_map = detect_l2_deadends()
        l2_suggestions = suggest_l2_links(deadend_map)
        no_out = sum(1 for v in l2_suggestions.get('no_outlink', {}).values() if v)
        no_in = sum(1 for v in l2_suggestions.get('no_inlink', {}).values() if v)
        print(f"    出链建议: {no_out}/{no_outlink_count} | 入链建议: {no_in}/{no_inlink_count}")

    # 步 4: 复查
    print(f"[4/8] 复查…")
    recheck_findings = recheck()
    print(f"    剩余 {len(recheck_findings)} 条")

    # 步 5: 报告
    print(f"[5/8] 报告…")
    out = report(findings, fixed, recheck_findings, ts, date_str, l2_suggestions)
    print(f"    {out}")

    # 步 6: 归档
    print(f"[6/8] 归档…")
    archive(findings, fixed, ts, l2_suggestions)

    # 步 7: 回写索引
    if apply:
        print(f"[7/8] 回写索引…")
        ok = rewrite_index()
        print(f"    索引刷新: {'ok' if ok else 'skip'}")
    else:
        print(f"[7/8] 回写索引… (dry-run，跳过)")

    # 步 8: 摘要
    print(f"[8/8] 摘要…")
    print(f"    L1修复: {len(fixed)} | L2待确认: {len(l2)}" +
          (f" | 建议: 出链{sum(1 for v in (l2_suggestions or {}).get('no_outlink',{}).values() if v)}/入链{sum(1 for v in (l2_suggestions or {}).get('no_inlink',{}).values() if v)}" if l2_suggestions else ""))
    
    mode_tag = ""
    if suggest and not apply:
        mode_tag = "（--suggest 建议模式，未改文件）"
    elif apply and suggest:
        mode_tag = "（--apply + --suggest）"
    elif apply:
        mode_tag = "（--apply，已修复 L1）"
    else:
        mode_tag = "（dry-run，未改文件）"
    print(f"✅ 自愈闭环完成 {mode_tag}")


if __name__ == "__main__":
    main()
