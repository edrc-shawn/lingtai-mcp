# -*- coding: utf-8 -*-
"""SkillOpt 进化引擎 mixin"""
import os
import json
from datetime import datetime
from decorators import tool

class SkillOptMixin:
    @tool(readonly=True, write=False, category="pipeline", system=False)
    def skillopt_dryrun(self) -> dict:
        """预览本轮进化会产出什么。不暂存不改动。"""
        self._ensure_skillopt()
        summary = self.skillopt_engine.dry_run()
        return {"type": "dry_run", "summary": summary}

    @tool(readonly=False, write=True, category="pipeline", system=False)
    def skillopt_run(self) -> dict:
        """手动触发进化轮次（不等 03:00）。"""
        self._ensure_skillopt()
        summary = self.skillopt_engine.run()
        return {"type": "full_run", "summary": summary}

    @tool(readonly=True, write=False, category="pipeline", system=False)
    def skillopt_status(self) -> dict:
        """查看 staged 规则列表摘要（仅 frontmatter，不含 content 正文）。"""
        self._ensure_skillopt()
        rules = self.skillopt_stager.read_summary_all()
        # 按自信度降序排列
        rules.sort(key=lambda r: r.get("confidence", 0), reverse=True)
        return {"type": "status", "staged_count": len(rules), "rules": rules}

    @tool(readonly=False, write=True, category="pipeline", system=False)
    def skillopt_adopt(self, ids: str = "") -> dict:
        """采纳 staged 规则。ids 为空时采纳全部 🟢。"""
        self._ensure_skillopt()
        rules = self.skillopt_stager.read_all()
        if not rules:
            return {"type": "adopt", "ids": ids, "status": "nothing_staged"}

        if ids:
            targets = [r for r in rules if r["filename"].startswith(ids)]
        else:
            # 全树回收：默认只采纳 🟢，不误采纳待审阅的 🟡/⚪
            targets = [r for r in rules if r.get("level") == "🟢"]

        if not targets:
            return {"type": "adopt", "ids": ids, "status": "no_match", "staged": [r["filename"] for r in rules]}

        rules_path = os.path.join(self.vault_path, "感知规则.md")
        backup = None
        if os.path.exists(rules_path):
            backup = rules_path + ".bak"
            import shutil
            shutil.copy2(rules_path, backup)

        adopted = []
        with open(rules_path, "a", encoding="utf-8") as f:
            for r in targets:
                f.write(r["content"])
                adopted.append(r["filename"])

        for r in targets:
            try:
                os.remove(r["path"])
            except OSError:
                pass

        return {
            "type": "adopt",
            "ids": ids,
            "status": "ok",
            "adopted": adopted,
            "backup": backup,
        }

    @tool(readonly=False, write=True, category="pipeline", system=False)
    def skillopt_reject(self, id: str, reason: str = "") -> dict:
        """拒绝规则 → 记录 blacklist（自动写入匹配字段，使 reject 永久生效）。"""
        self._ensure_skillopt()
        blacklist_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "skillopt", "blacklist.json")
        bl = {"version": 1, "rejected": []}
        if os.path.exists(blacklist_path):
            with open(blacklist_path, "r", encoding="utf-8") as f:
                try:
                    bl = json.load(f)
                except json.JSONDecodeError:
                    bl = {"version": 1, "rejected": []}

        # 删除 staged 文件前，先解析其 frontmatter 提取匹配字段。
        # mine 阶段过滤器按 pattern_type+tool_name 命中；缺字段则黑名单形同虚设（R10 反复重现的根因）。
        matched = {}
        for r in self.skillopt_stager.read_all():
            if r["filename"].startswith(id):
                matched = self._parse_rule_frontmatter(r["content"])
                try:
                    os.remove(r["path"])
                except OSError:
                    pass

        entry = {
            "rule_id": id,
            "reason": reason or "人工拒绝",
            "rejected_at": datetime.now().isoformat(),
        }
        # 仅搬运真实存在的匹配字段，避免写入空字段导致过滤器误判
        for fld in ("pattern_type", "tool_name", "source_pattern", "description"):
            if matched.get(fld):
                entry[fld] = matched[fld]

        bl["rejected"].append(entry)
        bl["updated_at"] = datetime.now().isoformat()

        with open(blacklist_path, "w", encoding="utf-8") as f:
            json.dump(bl, f, ensure_ascii=False, indent=2)

        return {"type": "reject", "id": id, "reason": reason, "matched_fields": matched, "status": "ok"}

    @staticmethod
    def _parse_rule_frontmatter(content: str) -> dict:
        """解析 staged 规则 .md 的 frontmatter，返回扁平字段字典（split(':',1) 容忍值内含冒号）。"""
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

    @tool(readonly=True, write=False, category="pipeline", system=False)
    def skillopt_log(self, days: int = 7) -> dict:
        """查询进化历史。"""
        changelog_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "skillopt", "changelog.md")
        if not os.path.exists(changelog_path):
            return {"type": "log", "days": days, "entries": [], "status": "no_log"}

        from datetime import timedelta, date
        cutoff = date.today() - timedelta(days=days)

        entries = []
        with open(changelog_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                try:
                    date_str = line.split()[1]
                    entry_date = date.fromisoformat(date_str)
                    if entry_date >= cutoff:
                        entries.append(line)
                except (IndexError, ValueError):
                    entries.append(line)

        return {"type": "log", "days": days, "entries": entries, "count": len(entries), "status": "ok"}
