# -*- coding: utf-8 -*-
"""
stager.py — Stage 管理

职责：
  1. write / read / list / purge staged 规则
  2. 写入 staged/{date}/ 目录，不改任何现有文件
  3. 每条规则为一个独立 .md 文件，便于人类直接阅读

安全性：
  - stage 操作不改感知规则.md、AGENTS.md 等任何活跃文件
  - 只有 /skillopt adopt 命令才会写入感知规则.md（并先备份）
"""

import os
import json
from datetime import date as Date

SKILLOPT_DIR = os.path.dirname(__file__)
STAGED_DIR = os.path.join(SKILLOPT_DIR, "staged")
CHANGELOG_PATH = os.path.join(SKILLOPT_DIR, "changelog.md")


class Stager:
    """Stage 管理。"""

    def write(self, scored: list[dict], target_date: str = None) -> list[str]:
        """
        将 scored 中自信 ≥ 0.60 的规则写入 staged/{date}/。

        Args:
            scored: confidence_scorer 输出
            target_date: 日期字符串（默认今天）

        Returns:
            list[str]: 写入的文件路径列表
        """
        if target_date is None:
            target_date = Date.today().isoformat()

        # 只暂存 🟢 和 🟡
        to_stage = [r for r in scored if r.get("level") in ("🟢", "🟡")]

        if not to_stage:
            return []

        day_dir = os.path.join(STAGED_DIR, target_date)
        os.makedirs(day_dir, exist_ok=True)

        written = []
        for rule in to_stage:
            filename = f"{rule['rule_id']}_{rule.get('trigger', 'unknown')}.md"
            filepath = os.path.join(day_dir, filename)
            content = self._format_rule_md(rule)
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(content)
            written.append(filepath)
            self._append_changelog(rule, target_date)

        return written

    def read(self, target_date: str = None) -> list[dict]:
        """读取 staged 规则列表。"""
        if target_date is None:
            target_date = Date.today().isoformat()

        day_dir = os.path.join(STAGED_DIR, target_date)
        if not os.path.isdir(day_dir):
            return []

        rules = []
        for fname in sorted(os.listdir(day_dir)):
            if fname.endswith(".md"):
                path = os.path.join(day_dir, fname)
                with open(path, "r", encoding="utf-8") as f:
                    content = f.read()
                rules.append({"path": path, "content": content, "filename": fname})
        return rules

    def read_summary(self, target_date: str = None) -> list[dict]:
        """读取 staged 规则摘要（仅 frontmatter，不含 content 正文）。"""
        if target_date is None:
            target_date = Date.today().isoformat()

        day_dir = os.path.join(STAGED_DIR, target_date)
        if not os.path.isdir(day_dir):
            return []

        import re
        rules = []
        for fname in sorted(os.listdir(day_dir)):
            if fname.endswith(".md"):
                path = os.path.join(day_dir, fname)
                with open(path, "r", encoding="utf-8") as f:
                    content = f.read()
                # 解析 frontmatter
                fm = {}
                m = re.match(r'^---\s*\n(.*?)\n---', content, re.DOTALL)
                if m:
                    for line in m.group(1).split('\n'):
                        if ':' in line:
                            k, v = line.split(':', 1)
                            fm[k.strip()] = v.strip()
                rules.append({
                    "path": path,
                    "filename": fname,
                    "rule_id": fm.get("rule_id", ""),
                    "level": fm.get("level", ""),
                    "confidence": float(fm.get("confidence", 0)),
                })
        return rules

    def purge(self, days: int = 30) -> int:
        """清理超过 N 天的 staged 目录。"""
        from datetime import timedelta
        cutoff = Date.today() - timedelta(days=days)
        removed = 0
        for entry in os.listdir(STAGED_DIR):
            entry_path = os.path.join(STAGED_DIR, entry)
            if os.path.isdir(entry_path):
                try:
                    entry_date = Date.fromisoformat(entry)
                    if entry_date < cutoff:
                        import shutil
                        shutil.rmtree(entry_path)
                        removed += 1
                except ValueError:
                    continue
        return removed

    def _format_rule_md(self, rule: dict) -> str:
        """将规则转为可读 Markdown 文件。"""
        return (
            f"---\n"
            f"rule_id: {rule.get('rule_id', '')}\n"
            f"level: {rule.get('level', '')}\n"
            f"confidence: {rule.get('confidence', 0)}\n"
            f"pattern_type: {rule.get('pattern_type', '')}\n"
            f"tool_name: {rule.get('tool_name', '')}\n"
            f"source_pattern: {rule.get('source_pattern', '')}\n"
            f"description: {rule.get('description', '')}\n"
            f"---\n\n"
            f"## {rule.get('description', '')}\n\n"
            f"**触发条件**：{rule.get('trigger', '')}\n\n"
            f"**建议动作**：{rule.get('action', '')}\n\n"
            f"**来源模式**：{rule.get('source_pattern', '')}\n"
        )

    def _append_changelog(self, rule: dict, date_str: str):
        """追加到 changelog.md。"""
        line = f"- {date_str} [{rule.get('level', '')}] {rule['rule_id']}: {rule.get('description', '')}\n"
        with open(CHANGELOG_PATH, "a", encoding="utf-8") as f:
            f.write(line)

    def _parse_frontmatter(self, content: str) -> dict:
        """解析 staged .md 的 frontmatter，返回扁平字段字典（split(':',1) 容忍值内含冒号）。"""
        import re
        fm = {}
        m = re.match(r'^---\s*\n(.*?)\n---', content, re.DOTALL)
        if not m:
            return fm
        for line in m.group(1).split('\n'):
            if ':' in line:
                k, v = line.split(':', 1)
                fm[k.strip()] = v.strip()
        return fm

    def read_all(self) -> list[dict]:
        """读取所有日期目录下的 staged 规则（历史 + 今天），按日期 + 文件名排序。

        修复盲区：原 read()/read_summary() 默认只扫「今天」目录，
        导致历史日期候选对所有 MCP 工具隐形（看不见 / 采纳不了 / 拒绝不掉）。
        status/adopt/reject 现改用本方法，历史候选得以回收。
        """
        if not os.path.isdir(STAGED_DIR):
            return []
        rules = []
        for day in sorted(os.listdir(STAGED_DIR)):
            day_dir = os.path.join(STAGED_DIR, day)
            if not os.path.isdir(day_dir):
                continue
            for fname in sorted(os.listdir(day_dir)):
                if fname.endswith(".md"):
                    path = os.path.join(day_dir, fname)
                    with open(path, "r", encoding="utf-8") as f:
                        content = f.read()
                    fm = self._parse_frontmatter(content)
                    rules.append({
                        "path": path,
                        "content": content,
                        "filename": fname,
                        "level": fm.get("level", ""),
                        "confidence": float(fm.get("confidence", 0)),
                    })
        return rules

    def read_summary_all(self) -> list[dict]:
        """read_all 的摘要版（仅 frontmatter，不含 content 正文）。"""
        return [
            {
                "path": r["path"],
                "filename": r["filename"],
                "rule_id": self._parse_frontmatter(r["content"]).get("rule_id", ""),
                "level": r["level"],
                "confidence": r["confidence"],
            }
            for r in self.read_all()
        ]
