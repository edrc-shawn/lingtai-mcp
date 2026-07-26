# -*- coding: utf-8 -*-
"""巡更·自愈管线 — 7 步闭环（检测→分类→修复→复查→报告→归档→回写索引）

对齐 llmwiki self-healing：把灵台巡更 L1(自动修)/L2(只扫不修) 升级为系统化闭环。
安全边界：默认 dry-run（不改文件、不刷索引）；--apply 才对 L1 低风险项动刀。
"""
import os
import re
import sys
import json
import datetime
import subprocess

VAULT = os.environ.get("LINGTAI_VAULT", r"C:\Obsidian仓库\edrc\灵台")
DANFANG = os.path.join(VAULT, "丹房")
HEAL_LOG = os.path.join(VAULT, ".tool", "巡更", "self_heal_log.jsonl")
BUILD_INDEX = os.path.join(VAULT, ".tool", "scripts", "build_index.py")

# 命名禁用字符（台律）：弯引号 U+201C/U+201D + 直引号 U+0022
BANNED_CHARS = ["\u201c", "\u201d", "\u0022"]


def detect():
    """步1 检测：扫描丹房已知问题（仅 schema 知识页：丹房/{域编号}-{域名}/...）"""
    # AGENTS 工程底线：丹房页路径格式 丹房/{域编号}-{域名}/{页面名}.md
    _SCHEMA_RE = re.compile(r"^丹房/\d{2}-[^/]+/.+\.md$")
    findings = []
    for root, _, files in os.walk(DANFANG):
        for f in files:
            if not f.endswith(".md"):
                continue
            p = os.path.join(root, f)
            rel = os.path.relpath(p, VAULT).replace(os.sep, "/")
            # 仅校验符合 schema 的知识页；.meta/ 机器导出、根级日志等跳过
            if not _SCHEMA_RE.match(rel):
                continue
            try:
                txt = open(p, "r", encoding="utf-8").read()
            except Exception:
                continue
            # L1：缺 标题 frontmatter（低风险，可自动补）
            fm = re.match(r"^---\n(.*?)\n---", txt, re.S)
            has_title = bool(fm and re.search(r"^标题\s*:", fm.group(1), re.M))
            if not has_title:
                findings.append({"type": "missing_title", "path": rel, "level": "L1", "fixable": True})
            # L2：弯引号/直引号文件名（改名会断双链，只报不修）
            if any(c in f for c in BANNED_CHARS):
                findings.append({"type": "banned_quote_filename", "path": rel, "level": "L2", "fixable": False})
    return findings


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


def report(findings, fixed, recheck_findings, ts, date_str):
    """步5 报告"""
    out = os.path.join(VAULT, "体检", "日报", "自愈报告", f"自愈报告_{ts}.md")
    l2 = [x for x in findings if x["level"] == "L2"]
    lines = [
        "---", "标题: 巡更自愈报告", f"日期: {date_str}", "---", "",
        f"# 巡更自愈报告 {date_str}", "",
        f"- 检测问题总数: {len(findings)}",
        f"- L1 自动修复: {len(fixed)}",
        f"- L2 只报不修: {len(l2)}",
        f"- 复查剩余: {len(recheck_findings)}", "",
        "## L1 已修复", "",
    ]
    lines += [f"- {p}" for p in fixed] or ["- （无）"]
    lines += ["", "## L2 待人工（只报不修）", ""]
    lines += [f"- {x['path']} ({x['type']})" for x in l2] or ["- （无）"]
    lines += ["", "## 检测明细（全量）", ""]
    for x in findings:
        tag = "L1✓已修" if (x["path"] in fixed) else x["level"]
        lines += [f"- [{tag}] {x['path']} ({x['type']})"]
    open(out, "w", encoding="utf-8").write("\n".join(lines))
    return out


def archive(findings, fixed, ts):
    """步6 归档"""
    rec = {
        "ts": ts,
        "total": len(findings),
        "fixed": fixed,
        "remaining": [x["path"] for x in findings if x["level"] == "L2"],
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
    now = datetime.datetime.now()
    ts = now.strftime("%Y%m%d-%H%M")
    date_str = now.strftime("%Y-%m-%d %H:%M")
    print(f"[1/7] 检测…")
    findings = detect()
    print(f"    发现问题 {len(findings)} 条")
    l1, l2 = classify(findings)
    print(f"[2/7] 分类: L1={len(l1)} L2={len(l2)}")
    fixed = []
    if apply:
        print(f"[3/7] 修复 L1…")
        fixed = fix_l1(l1)
        print(f"    修复 {len(fixed)} 条")
    else:
        print(f"[3/7] 修复 L1… (dry-run，跳过)")
    print(f"[4/7] 复查…")
    recheck_findings = recheck()
    print(f"    剩余 {len(recheck_findings)} 条")
    print(f"[5/7] 报告…")
    out = report(findings, fixed, recheck_findings, ts, date_str)
    print(f"    {out}")
    print(f"[6/7] 归档…")
    archive(findings, fixed, ts)
    if apply:
        print(f"[7/7] 回写索引…")
        ok = rewrite_index()
        print(f"    索引刷新: {'ok' if ok else 'skip'}")
    else:
        print(f"[7/7] 回写索引… (dry-run，跳过)")
    print("✅ 自愈闭环完成" + ("" if apply else "（dry-run，未改文件）"))


if __name__ == "__main__":
    main()
