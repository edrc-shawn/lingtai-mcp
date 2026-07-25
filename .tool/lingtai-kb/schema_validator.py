# -*- coding: utf-8 -*-
"""
灵台 Schema 校验器——从 AGENTS.md 的 <!-- schema-rules --> 块解析规则。

硬校验：写入前执行，违规直接拒绝 + 返回 violations
软校验：允许写入，记录 warning 到体检
"""
import re
import os
from typing import List, Dict
from errors import fail, ErrorCode


def _load_rules(vault_path: str) -> dict:
    """解析 AGENTS.md 中的 schema-rules 块（支持多行 YAML 条目）"""
    agents_path = os.path.join(vault_path, "AGENTS.md")
    if not os.path.exists(agents_path):
        return {"hard": [], "soft": []}

    with open(agents_path, "r", encoding="utf-8") as f:
        content = f.read()

    m = re.search(r'<!-- schema-rules\n(.*?)\n-->', content, re.DOTALL)
    if not m:
        return {"hard": [], "soft": []}

    rules_text = m.group(1)

    def _unescape(val: str) -> str:
        """将文件中的 \\\\ 转为 \\（regex 需要单反斜杠）"""
        return val.replace("\\\\", "\\")

    rules: dict = {"hard": [], "soft": []}
    current_section = None
    current_entry = None

    def _flush():
        nonlocal current_entry
        if current_entry is not None and current_section:
            rules[current_section].append(current_entry)
        current_entry = None

    for raw_line in rules_text.split("\n"):
        line = raw_line.rstrip()
        stripped = line.strip()

        if not stripped:
            continue

        # 段落标题：hard: 或 soft: ——先 flush 再切 section
        if stripped in ("hard:", "soft:"):
            _flush()
            current_section = stripped[:-1]
            continue

        # 新条目以 "- " 开头——先 flush 上一个 entry
        if stripped.startswith("- "):
            _flush()
            current_entry = {}
            kv_text = stripped[2:]
            for kv in re.findall(r'(\S+?):\s*(.*)', kv_text):
                current_entry[kv[0]] = _unescape(kv[1].strip().strip('"').strip("'"))
            continue

        # 缩进字段：field: value（4 空格缩进）
        if current_entry is not None and line.startswith("    "):
            for kv in re.findall(r'(\S+?):\s*(.*)', stripped):
                current_entry[kv[0]] = _unescape(kv[1].strip().strip('"').strip("'"))
            continue

    # 收尾最后一个 entry
    _flush()

    return rules


def validate_page_path(path: str, vault_path: str) -> dict:
    """校验丹房页面路径格式"""
    rules = _load_rules(vault_path)
    violations = []

    for rule in rules.get("hard", []):
        if rule.get("type") == "path_pattern":
            if not re.match(rule["match"], path):
                violations.append({
                    "rule": "path_pattern",
                    "message": rule.get("message", f"路径 {path} 不符合格式"),
                    "expected": rule["match"],
                    "actual": path,
                })

    if violations:
        return fail(
            ErrorCode.SCHEMA_VIOLATION,
            f"路径 {path} 违反 {len(violations)} 条硬校验规则",
            data={"violations": violations},
        )
    return {"ok": True, "warnings": []}


def validate_frontmatter(content: str, vault_path: str) -> dict:
    """校验 frontmatter 必填字段"""
    rules = _load_rules(vault_path)
    violations = []
    warnings = []

    # 提取 frontmatter
    fm_match = re.match(r'^---\n(.*?)\n---', content, re.DOTALL)
    if not fm_match:
        violations.append({
            "rule": "frontmatter_missing",
            "message": "页面缺少 YAML frontmatter",
        })
        return fail(
            ErrorCode.SCHEMA_VIOLATION,
            "不存在 frontmatter",
            data={"violations": violations},
        )

    fm_text = fm_match.group(1)
    fm_keys = set(re.findall(r'^(\S+?):', fm_text, re.MULTILINE))

    for rule in rules.get("hard", []):
        if rule.get("type") == "frontmatter_field":
            field = rule["field"]
            if field not in fm_keys:
                violations.append({
                    "rule": "frontmatter_field",
                    "field": field,
                    "message": rule.get("message", f"缺少必填字段 '{field}'"),
                })

    if violations:
        return fail(
            ErrorCode.SCHEMA_VIOLATION,
            f"frontmatter 违反 {len(violations)} 条硬校验规则",
            data={"violations": violations},
        )
    return {"ok": True, "warnings": warnings}


def validate_page_create(title: str, content: str, domain: str, vault_path: str) -> dict:
    """page_create 的综合校验入口"""
    path = f"丹房/{domain}/{title}.md"

    # 路径校验
    r1 = validate_page_path(path, vault_path)
    if not r1.get("ok"):
        return r1

    # frontmatter 校验——拼一个临时 frontmatter
    fm_content = f"---\n标题: {title}\n---\n\n{content}"
    r2 = validate_frontmatter(fm_content, vault_path)
    if not r2.get("ok"):
        return r2

    return {"ok": True, "path": path}


def batch_validate(vault_path: str, scan_dirs: list = None) -> dict:
    """
    批量校验丹房所有页面——路径格式 + frontmatter 完整性。
    供 build_index.py 在索引重建后调用，输出违规报告。

    Returns:
        dict: {total, violations: [{path, rule, message}], warnings: [{path, message}]}
    """
    if scan_dirs is None:
        scan_dirs = ["丹房"]
    violations = []
    warnings = []
    total = 0

    rules = _load_rules(vault_path)
    hard_rules = rules.get("hard", [])

    for scan_dir in scan_dirs:
        scan_root = os.path.join(vault_path, scan_dir)
        if not os.path.isdir(scan_root):
            continue
        for root, dirs, files in os.walk(scan_root):
            # 跳过隐藏目录
            dirs[:] = [d for d in dirs if not d.startswith('.')]
            for fname in files:
                if not fname.endswith('.md'):
                    continue
                fpath = os.path.join(root, fname)
                rel_path = os.path.relpath(fpath, vault_path).replace('\\', '/')

                total += 1

                # path_pattern 校验
                for rule in hard_rules:
                    if rule.get("type") == "path_pattern":
                        if not re.match(rule["match"], rel_path):
                            violations.append({
                                "path": rel_path,
                                "rule": "path_pattern",
                                "message": rule.get("message", f"路径不符合格式"),
                                "expected": rule["match"],
                            })

                # frontmatter_field 校验
                try:
                    with open(fpath, 'r', encoding='utf-8') as f:
                        content = f.read()
                    fm_match = re.match(r'^---\n(.*?)\n---', content, re.DOTALL)
                    if not fm_match:
                        violations.append({
                            "path": rel_path,
                            "rule": "frontmatter_missing",
                            "message": "页面缺少 YAML frontmatter",
                        })
                        continue
                    fm_text = fm_match.group(1)
                    fm_keys = set(re.findall(r'^(\S+?):', fm_text, re.MULTILINE))
                    for rule in hard_rules:
                        if rule.get("type") == "frontmatter_field":
                            if rule["field"] not in fm_keys:
                                violations.append({
                                    "path": rel_path,
                                    "rule": "frontmatter_field",
                                    "field": rule["field"],
                                    "message": rule.get("message", f"缺少必填字段 '{rule['field']}'"),
                                })
                except Exception as e:
                    warnings.append({
                        "path": rel_path,
                        "message": f"读取失败: {e}",
                    })

    return {
        "total": total,
        "violations": violations,
        "violation_count": len(violations),
        "warnings": warnings,
    }