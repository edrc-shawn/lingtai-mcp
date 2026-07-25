# -*- coding: utf-8 -*-
"""外部工具推荐器 mixin — 扫描外部参考目录的 SKILL.md，推荐最匹配的外部 Skill/工具"""
import os
import re
import json
from pathlib import Path
from datetime import datetime
from decorators import tool


class ExternalToolRecommenderMixin:
    """外部工具推荐器

    三层桥接策略中的「路由桥」层：
    灵台收到问题 → 丹房无法回答 → 扫描外部参考目录 → 推荐最匹配的外部 Skill/工具
    """

    # ── 缓存 ──
    _ext_skill_cache = None
    _ext_skill_cache_mtime = 0
    _ext_skill_cache_ttl = 300  # 5 分钟缓存

    # ── 匹配权重 ──
    _WEIGHT_NAME_EXACT = 10       # 名称精确匹配
    _WEIGHT_NAME_CONTAINS = 8     # 名称包含查询词（或反之）
    _WEIGHT_NAME_WORD = 5         # 名称中每个匹配词
    _WEIGHT_DESC_FULL = 4         # 描述包含完整查询词
    _WEIGHT_DESC_WORD = 2         # 描述中每个匹配词
    _WEIGHT_TRIGGER_WORD = 3      # 触发词匹配

    # ── 触发词提取模式 ──
    _TRIGGER_PATTERNS = [
        re.compile(r'触发[词条件]*[：:]\s*(.+?)(?:[。，\n]|$)', re.IGNORECASE),
        re.compile(r'trigger[s]?\s*[：:]\s*(.+?)(?:[。，\n]|$)', re.IGNORECASE),
        re.compile(r'适用场景[：:]\s*(.+?)(?:[。，\n]|$)', re.IGNORECASE),
        re.compile(r'use\s*(?:when|case[s]?)\s*[：:]\s*(.+?)(?:[。，\n]|$)', re.IGNORECASE),
    ]

    # ═══════════════════════════════════════════════════════════
    #  核心：扫描所有 SKILL.md
    # ═══════════════════════════════════════════════════════════

    def _scan_external_skills(self, force_refresh: bool = False) -> list:
        """扫描外部参考目录下所有 SKILL.md 文件，解析 frontmatter

        性能优化：仅读取文件前 3000 字节（足够解析 frontmatter），
        首次扫描后缓存 5 分钟。建议在服务器启动时调用 _warm_external_skill_cache() 预热。

        Returns:
            list[dict]: [{name, description, repo, path, trigger_words}]
        """
        vault = getattr(self, "vault_path", os.environ.get("LINGTAI_VAULT", r"."))
        ext_dir = os.path.normpath(os.path.join(vault, "..", "外部参考和skills"))

        # 缓存检查
        if not force_refresh and self._ext_skill_cache is not None:
            try:
                dir_mtime = os.path.getmtime(ext_dir)
                if dir_mtime == self._ext_skill_cache_mtime:
                    age = (datetime.now() - datetime.fromtimestamp(self._ext_skill_cache_mtime)).total_seconds()
                    if age < self._ext_skill_cache_ttl:
                        return self._ext_skill_cache
            except OSError:
                pass

        if not os.path.isdir(ext_dir):
            return []

        skills = []
        for root, dirs, files in os.walk(ext_dir):
            # 跳过隐藏目录和 .git / node_modules
            dirs[:] = [d for d in dirs if not d.startswith(".") and d != "node_modules"]
            for fname in files:
                if fname != "SKILL.md":
                    continue
                fpath = os.path.join(root, fname)
                try:
                    # 仅读前 3000 字节——足够解析 frontmatter
                    with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
                        content = f.read(3000)
                except Exception:
                    continue

                # 解析 frontmatter
                fm = self._parse_skill_frontmatter(content)
                name = fm.get("name", "").strip()
                description = fm.get("description", "").strip()

                # 如果 frontmatter 没有 name，尝试从目录名推断
                if not name:
                    parent_dir = os.path.basename(root)
                    name = fm.get("标题", parent_dir).strip()

                # 确定所属仓库
                rel_root = os.path.relpath(root, ext_dir)
                repo = rel_root.split(os.sep)[0] if rel_root else "unknown"

                # 提取触发词
                trigger_words = self._extract_trigger_words(content, description)

                # 相对路径
                try:
                    rel_path = os.path.relpath(fpath, vault).replace("\\", "/")
                except ValueError:
                    # 跨盘符时用绝对路径
                    rel_path = fpath.replace("\\", "/")

                if name or description:
                    skills.append({
                        "name": name,
                        "description": description,
                        "repo": repo,
                        "path": rel_path,
                        "trigger_words": trigger_words,
                    })

        # 更新缓存
        self._ext_skill_cache = skills
        try:
            self._ext_skill_cache_mtime = os.path.getmtime(ext_dir)
        except OSError:
            self._ext_skill_cache_mtime = 0

        return skills

    def _warm_external_skill_cache(self):
        """预热缓存——建议在服务器启动时调用，避免首次查询等待"""
        try:
            self._scan_external_skills(force_refresh=True)
        except Exception:
            pass  # 预热失败不影响正常使用

    def _parse_skill_frontmatter(self, content: str) -> dict:
        """解析 SKILL.md 的 frontmatter（兼容多种格式）

        支持：
        - 标准 YAML frontmatter（--- ... ---），含多行字符串（| 和 >）
        - 非标准格式（正文中的 name/description 行）
        """
        fm = {}

        # 尝试标准 YAML frontmatter
        if content.startswith("---"):
            end_idx = content.find("---", 3)
            if end_idx > 0:
                fm_block = content[3:end_idx]
                fm = self._parse_yaml_like(fm_block)

        # 如果 frontmatter 没找到 name/description，尝试从正文提取
        if not fm.get("name"):
            for pattern in [
                r'^name\s*[：:]\s*(.+?)$',
                r'^#\s*(.+?)$',
            ]:
                m = re.search(pattern, content, re.MULTILINE)
                if m:
                    candidate = m.group(1).strip()
                    if len(candidate) < 50:
                        fm["name"] = candidate
                        break

        if not fm.get("description"):
            m = re.search(r'^description\s*[：:]\s*(.+?)$', content, re.MULTILINE)
            if m:
                fm["description"] = m.group(1).strip()

        return fm

    def _parse_yaml_like(self, text: str) -> dict:
        """解析类 YAML 文本，支持多行字符串（| 和 >）

        不依赖 PyYAML，仅提取 name/description/标题/title 字段。
        处理 YAML block scalar：|（literal，保留换行）和 >（folded，折叠换行为空格）
        """
        result = {}
        lines = text.split("\n")
        i = 0
        while i < len(lines):
            line = lines[i]
            stripped = line.strip()

            # 跳过空行和注释
            if not stripped or stripped.startswith("#"):
                i += 1
                continue

            # 检查是否是 key: value 行
            if ":" in stripped:
                key, _, val = stripped.partition(":")
                key = key.strip().lower()
                val = val.strip()

                if key in ("name", "description", "title", "标题"):

                    # 检测 YAML 多行字符串标记
                    if val in ("|", "|-", "|+", ">", ">-", ">+"):
                        # 多行字符串：收集后续缩进行
                        block_lines = []
                        i += 1
                        # 确定基础缩进（第一行非空行的缩进）
                        base_indent = None
                        while i < len(lines):
                            next_line = lines[i]
                            if next_line.strip() == "":
                                # 空行：literal(|) 保留，folded(>) 跳过
                                if val.startswith("|"):
                                    block_lines.append("")
                                i += 1
                                continue
                            # 检查缩进
                            indent = len(next_line) - len(next_line.lstrip())
                            if base_indent is None:
                                base_indent = indent
                            if indent < base_indent and next_line.strip():
                                # 缩进减少 → 块结束
                                break
                            block_lines.append(next_line.strip())
                            i += 1

                        # 合并
                        if val.startswith(">"):
                            # folded: 用空格连接
                            result[key] = " ".join(block_lines)
                        else:
                            # literal: 用换行连接
                            result[key] = "\n".join(block_lines)
                        continue  # 已经推进了 i
                    else:
                        # 简单值：去除引号
                        val = val.strip('"').strip("'")
                        if val:
                            result[key] = val

            i += 1

        return result

    def _extract_trigger_words(self, content: str, description: str) -> list:
        """从 SKILL.md 中提取触发词/触发条件"""
        triggers = []

        # 从 description 中提取（通常是"当用户...时使用"模式）
        for pat in [r'当用户(?:想要|需要|提到|询问)?(.+?)(?:时|的时候)', r'适用[于场景]?[：:]?\s*(.+?)(?:[。\n]|$)']:
            for m in re.finditer(pat, description):
                triggers.append(m.group(1).strip())

        # 从正文中提取触发词
        for pat in self._TRIGGER_PATTERNS:
            for m in pat.finditer(content):
                trig = m.group(1).strip()
                if trig and len(trig) < 100:
                    triggers.append(trig)

        return triggers

    # ═══════════════════════════════════════════════════════════
    #  匹配与排序
    # ═══════════════════════════════════════════════════════════

    def _score_skill(self, skill: dict, keyword: str, kw_words: set) -> tuple:
        """对单个 Skill 打分

        Returns:
            (score, reasons[])
        """
        score = 0
        reasons = []

        name_lower = skill["name"].lower()
        desc_lower = skill["description"].lower()
        kw_lower = keyword.lower().strip()

        # 1. 名称精确匹配
        if kw_lower == name_lower:
            score += self._WEIGHT_NAME_EXACT
            reasons.append(f"名称精确匹配: {skill['name']}")

        # 2. 名称包含查询词（或反之）
        elif kw_lower in name_lower:
            score += self._WEIGHT_NAME_CONTAINS
            reasons.append(f"查询词包含在名称中: {skill['name']}")
        elif name_lower in kw_lower:
            score += self._WEIGHT_NAME_CONTAINS
            reasons.append(f"名称包含在查询中: {skill['name']}")

        # 3. 名称关键词匹配
        name_word_matches = sum(1 for w in kw_words if w in name_lower)
        if name_word_matches > 0:
            score += name_word_matches * self._WEIGHT_NAME_WORD
            reasons.append(f"名称匹配 {name_word_matches} 个关键词")

        # 4. 描述包含完整查询词
        if kw_lower in desc_lower:
            score += self._WEIGHT_DESC_FULL
            reasons.append("描述包含完整查询词")

        # 5. 描述关键词匹配
        desc_word_matches = sum(1 for w in kw_words if len(w) >= 2 and w in desc_lower)
        if desc_word_matches > 0:
            score += desc_word_matches * self._WEIGHT_DESC_WORD
            reasons.append(f"描述匹配 {desc_word_matches} 个关键词")

        # 6. 触发词匹配
        for tw in skill.get("trigger_words", []):
            if kw_lower in tw.lower() or any(w in tw.lower() for w in kw_words if len(w) >= 2):
                score += self._WEIGHT_TRIGGER_WORD
                reasons.append(f"触发词匹配: {tw[:50]}")
                break  # 每个 Skill 只加一次触发词分

        return score, reasons

    # ═══════════════════════════════════════════════════════════
    #  MCP 工具
    # ═══════════════════════════════════════════════════════════

    @tool(readonly=True, write=False, category="system", system=False)
    def external_tool_recommend(self, keyword: str, max_results: int = 5,
                                 include_low_score: bool = False) -> dict:
        """外部工具推荐器——扫描外部参考目录的 SKILL.md，推荐最匹配的外部 Skill/工具

        灵台收到问题后，先判断丹房能否回答；不能则调用此工具扫描外部参考目录，
        推荐最匹配的外部 Skill/工具。

        Args:
            keyword: 用户查询关键词（中文或英文）
            max_results: 最大推荐数（默认 5）
            include_low_score: 是否包含低分匹配（默认 False，仅返回有意义的匹配）

        Returns:
            dict: {
                keyword: str,
                total_skills_scanned: int,
                total_repos: int,
                recommendations: [{name, repo, description, path, score, match_reasons, suggested_action}],
                fallback_note: str (当无匹配时)
            }
        """
        if not keyword or not keyword.strip():
            return {
                "keyword": keyword,
                "total_skills_scanned": 0,
                "total_repos": 0,
                "recommendations": [],
                "error": "关键词为空",
            }

        skills = self._scan_external_skills()
        if not skills:
            return {
                "keyword": keyword,
                "total_skills_scanned": 0,
                "total_repos": 0,
                "recommendations": [],
                "fallback_note": "外部参考目录为空或不可访问",
            }

        kw_lower = keyword.lower().strip()
        kw_words = set(w for w in kw_lower.split() if len(w) >= 2)

        # 打分
        scored = []
        for skill in skills:
            score, reasons = self._score_skill(skill, keyword, kw_words)
            if score > 0 or include_low_score:
                scored.append({
                    "name": skill["name"],
                    "repo": skill["repo"],
                    "description": skill["description"][:200],
                    "path": skill["path"],
                    "score": score,
                    "match_reasons": reasons,
                    "suggested_action": (
                        f"在 {skill['path']} 中查看完整 Skill 定义。"
                        f"若为 dbskill 下的 Skill，可用 dbskill 的 /use 命令加载。"
                    ),
                })

        # 排序
        scored.sort(key=lambda x: x["score"], reverse=True)

        # 统计仓库数
        repos = set(s["repo"] for s in skills)

        top = scored[:max_results]

        result = {
            "keyword": keyword,
            "total_skills_scanned": len(skills),
            "total_repos": len(repos),
            "recommendations": top,
        }

        if not top:
            result["fallback_note"] = (
                f"在 {len(skills)} 个外部 Skill（覆盖 {len(repos)} 个仓库）中未找到匹配。"
                f"建议：换更具体的关键词重试，或直接用 fulltext_search 搜外部参考目录全文。"
            )

        return result

    # ═══════════════════════════════════════════════════════════
    #  供 knowledge.py L2.5 层调用的内部接口
    # ═══════════════════════════════════════════════════════════

    def _recommend_external_tools_for_query(self, keyword: str, max_results: int = 5) -> list:
        """内部接口：供 knowledge_search 的 L2.5 层调用

        Returns:
            list[dict]: 推荐结果列表（已排序），无结果时返回空列表
        """
        result = self.external_tool_recommend(keyword=keyword, max_results=max_results,
                                               include_low_score=False)
        return result.get("recommendations", [])