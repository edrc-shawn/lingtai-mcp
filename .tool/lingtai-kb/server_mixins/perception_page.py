# -*- coding: utf-8 -*-
"""页面操作 mixin — 丹房知识页的创建/更新/追加/压缩/读取/链接"""
import os
import re
import json
import subprocess
from datetime import datetime

from decorators import tool
from logger import get_logger


log = get_logger(__name__)

class PageMixin:
    def _log_and_commit(self, log_type, summary, links, commit_msg):
        """写工具自动追加日志.md + oplog.jsonl + git commit（§5.1-5.3 的落地保障）"""
        import os, json, subprocess
        from datetime import datetime
        now = datetime.now()
        vault = self.vault_path
        repo = os.path.dirname(vault)
        ts = now.strftime('%y-%m-%d %H:%M')
        iso_ts = now.strftime('%Y-%m-%dT%H:%M:00+08:00')
        log_path = os.path.join(vault, '丹房', '日志.md')
        links_str = ', '.join(links)
        log_line = f'\n[{ts}] MCP | {log_type} | {summary} | → {links_str}\n'
        try:
            with open(log_path, 'a', encoding='utf-8') as f:
                f.write(log_line)
        except Exception:
            log.debug("suppressed", exc_info=True)
        oplog_path = os.path.join(vault, '丹房', '.meta', 'oplog.jsonl')
        entry = {"t": iso_ts, "op": "MCP", "mode": "auto", "model": None, "type": log_type, "summary": summary, "links": links}
        try:
            os.makedirs(os.path.dirname(oplog_path), exist_ok=True)
            with open(oplog_path, 'a', encoding='utf-8') as f:
                f.write(json.dumps(entry, ensure_ascii=False) + '\n')
        except Exception:
            log.debug("suppressed", exc_info=True)
        try:
            # 暂存 ①：git add -u 处理已跟踪文件的修改（日志.md / oplog.jsonl / 索引等）
            subprocess.run(f'cd "{repo}" && git add -u',
                           shell=True, capture_output=True, text=True, timeout=30)
            # 暂存 ②：显式 git add 本操作涉及的页面路径。
            # 新建丹房页是 untracked，git add -u 不会暂存它，必须显式加入，
            # 否则新建页会漏进提交（历史 bug：945cd554 仅含元数据、丹房页游离工作区）。
            # 保留 -u 的"不扫描全量 untracked"意图，仅补显式路径，避免误加运行时脏文件。
            vault_rel = os.path.basename(os.path.normpath(vault))
            for lp in (links or []):
                rel = str(lp).replace('\\', '/').strip().lstrip('/')
                if not rel:
                    continue
                full_rel = rel if rel.startswith(vault_rel) else f"{vault_rel}/{rel}"
                try:
                    subprocess.run(f'cd "{repo}" && git add "{full_rel}"',
                                   shell=True, capture_output=True, text=True, timeout=30)
                except Exception:
                    log.debug("suppressed", exc_info=True)
            subprocess.run(f'cd "{repo}" && git commit -m "{commit_msg}"',
                           shell=True, capture_output=True, text=True, timeout=30)
        except Exception:
            log.debug("suppressed", exc_info=True)

    def _check_grade_quota(self) -> list:
        """
        品级配额检查：上品 ≤30，hub页(backlinks>50) ≤10
        返回配额警告列表，空列表表示无违规
        """
        warnings = []
        try:
            stats = self.memory.get_stats() if hasattr(self, 'memory') else {}
            by_pinji = stats.get("by_pinji", {})
            shangpin = by_pinji.get("上品", 0)
            if shangpin >= 30:
                warnings.append(f"上品页已达 {shangpin} 页（上限30），新页面若被升品将拥挤。建议先清理/降品老旧上品页")

            page_stats = self.memory.get_page_stats() if hasattr(self, 'memory') else {}
            hub_pages = page_stats.get("hub_pages", [])
            hub_over_limit = sum(1 for _, bl in hub_pages if bl > 50)
            if hub_over_limit >= 10:
                warnings.append(f"枢纽页（backlinks>50）已达 {hub_over_limit} 页（上限10），过度集中。建议审视是否需要拆解或优化链接拓扑")
        except Exception:
            log.debug("suppressed", exc_info=True)
        return warnings

    @tool(readonly=False, write=True, category="page", system=False, name="page_create")
    def create_page(self, title: str, content: str, domain: str = "07-工具与AI", tags: list = None) -> dict:
        """
        在 丹房/ 下创建成品知识页（即时可检索、含 frontmatter 自动生成）。
        场景：提炼完成、内容已结构化、可直接入库时；agent 自主沉淀知识时。
        区别：存未加工原始素材用 raw_save（写入原料/，需后续提炼）；修改已有页用 page_update。

        Args:
            title: 页面标题（同时作为文件名）
            content: Markdown 正文
            domain: 所属域（如"00-思考与认知"，默认"07-工具与AI"）
            tags: 标签列表（可选）
        """
        import os, re
        from datetime import datetime

        safe_title = re.sub(r'[<>:"/\\|?*#]', '', title).strip()
        if not safe_title:
            return {"success": False, "error": "标题不能为空或全为非法字符"}

        now = datetime.now()
        date_str = now.strftime('%Y-%m-%d')
        page_path = f"丹房/{domain}/{safe_title}.md"
        abs_path = os.path.join(self.vault_path, page_path.replace('/', os.sep))

        if os.path.exists(abs_path):
            return {"success": False, "error": f"知识点「{safe_title}」已存在于 {domain} 域", "path": page_path}

        # 构建 frontmatter
        tags_str = ', '.join(tags) if tags else ''
        fm = f"""---
标题: {safe_title}
创建日期: {date_str}
更新日期: {date_str}
品级: 下品{f'''
标签: [{tags_str}]''' if tags_str else ''}
---
"""
        # 正文：直接使用 AI 提供的完整 Markdown 内容（含 H1 标题、行内标签、章节）。
        # 仅当内容未以 H1 标题开头时，自动补一个与 frontmatter 标题一致的标题，
        # 避免旧逻辑自动注入 ## 摘要 / ## 要点 与已有结构重复造成重复块。
        if content and not content.lstrip().startswith('# '):
            body = f"# {safe_title}\n\n{content}"
        else:
            body = content or ''

        # 简洁骨架：H1 + 行内标签 + 正文（无两区结构，两区由 page_compress 按需引入）
        if '## 编译真理' not in body and '## 时间线' not in body:
            lines = body.split('\n', 1)
            h1 = lines[0] if lines[0].lstrip().startswith('# ') else f"# {safe_title}"
            rest = lines[1] if len(lines) > 1 else ''
            # 行内标签（从 tags 同步到正文，与 frontmatter 一致）
            tag_line = ' '.join(f'#{t}' for t in (tags or [])) if tags else ''
            nl = '\n\n'
            body = f"""{h1}{f'{nl}{tag_line}' if tag_line else ''}

{rest.strip()}

"""
        full = fm + body
        os.makedirs(os.path.dirname(abs_path), exist_ok=True)
        with open(abs_path, 'w', encoding='utf-8') as f:
            f.write(full)

        # 写入持久性保证：立即注册到 memory engine，不等 index.json 重建
        # 新页即刻在 knowledge_search 中可搜索（P2-4）
        try:
            if hasattr(self, 'memory'):
                self.memory.hot_register_page(
                    path=page_path, title=safe_title,
                    domain=domain, summary=content[:200] if content else "",
                    tags=tags or [], pinji="下品",
                )
        except Exception:
            log.debug("suppressed", exc_info=True)

        # 反向泄漏检测：登记知识写入，标记易变物（气话/一次性/未定）
        volatile_warning = None
        try:
            from memory_bank.lifecycle import record_knowledge_write
            rec = record_knowledge_write("page_create", page_path, content)
            if rec.get("volatile"):
                volatile_warning = f"反向泄漏预警：内容被判为易变物（{rec.get('reason')}），建议勿长期留存为知识"
        except Exception:
            log.debug("suppressed", exc_info=True)

        self._log_and_commit(
            log_type="创建",
            summary=f"新建知识点「{safe_title}」在 {domain} 域",
            links=[page_path],
            commit_msg=f"feat: {safe_title}（{domain}）",
        )

        # 品级配额检查（上品限30 / hub页限10）
        quota_warnings = self._check_grade_quota()

        result = {
            "success": True,
            "path": page_path,
            "title": safe_title,
            "domain": domain,
            "message": f"已在 {domain} 域创建知识点「{safe_title}」",
            "volatile_warning": volatile_warning,
        }
        if quota_warnings:
            result["quota_warnings"] = quota_warnings
        return result

    @tool(readonly=False, write=True, category="page", system=False, name="page_update")
    def update_page(self, path: str, content: str = None, append: bool = True,
                    timeline_mode: bool = False, update_compiled_truth: bool = False) -> dict:
        """
        整页级更新（追加到末尾或替换全文）。
        场景：大段改写、整页替换、时间线区追加条目时。
        区别：只在某个 ## 章节下精准插入内容用 page_append_section；创建新页用 page_create。

        Args:
            path: 知识页路径
            content: 要更新的正文
            append: True=追加 / False=替换
            timeline_mode: 启用编译真理时间线模式（append=True 时生效）
                页面有两区结构 → 追加到时间线区
                页面无两区结构 → 回退到原 append 行为
            update_compiled_truth: 同时更新编译真理区（替换该区内容）
        """
        import os

        # 确保有 .md 后缀
        if not path.endswith('.md'):
            path = path + '.md'
        abs_path = os.path.join(self.vault_path, path.replace('/', os.sep))

        if not os.path.exists(abs_path):
            return {"success": False, "error": f"知识页不存在: {path}"}

        with open(abs_path, 'r', encoding='utf-8') as f:
            full = f.read()

        # 分离 frontmatter 和正文
        if full.startswith('---'):
            parts = full.split('---', 2)
            if len(parts) >= 3:
                fm = parts[1]
                body = parts[2]
            else:
                fm, body = '', full
        else:
            fm, body = '', full

        if content is None:
            return {"success": False, "error": "content 参数不能为空"}

        # 编译真理两区检测：检查页面是否有 ## 编译真理 和 ## 时间线
        has_two_zone = '## 编译真理' in body and '## 时间线' in body

        if append and timeline_mode and has_two_zone:
            # 时间线模式：追加到时间线区（在最新记录前插入）
            import re as _re
            from datetime import datetime as _dt
            _today = _dt.now().strftime('%Y-%m-%d')
            # 找到时间线区的起始位置
            tl_marker = '## 时间线'
            tl_idx = body.find(tl_marker)
            if tl_idx >= 0:
                # 时间线区内容 = marker 之后的部分
                tl_section = body[tl_idx + len(tl_marker):]
                # 分离开头的引述块（如果有）和实际条目列表
                _rest = tl_section.lstrip('\n')
                _quote = ""
                if _rest.startswith('>'):
                    _qm = _re.match(r'^((?:>.*\n?)+)', _rest)
                    if _qm:
                        _quote = _qm.group(1)
                        _rest = _rest[_qm.end():]
                # 在引述块后、已有条目之前插入新记录
                new_entry = f"\n### {_today}\n{content}\n"
                updated_tl = tl_marker + '\n' + _quote + new_entry + _rest
                new_body = body[:tl_idx] + updated_tl
            else:
                # 理论不会走到这里，但兜底
                new_body = body.rstrip() + '\n\n' + content
        elif append and update_compiled_truth and has_two_zone:
            # 替换编译真理区内容
            ct_marker = '## 编译真理'
            tl_marker = '## 时间线'
            ct_idx = body.find(ct_marker)
            tl_idx = body.find(tl_marker)
            if ct_idx >= 0 and tl_idx > ct_idx:
                # 替换编译真理区（从 marker 到时间线之前）
                before_ct = body[:ct_idx]
                after_ct_to_tl = body[tl_idx:]
                new_body = before_ct + f"{ct_marker}\n\n{content}\n\n" + after_ct_to_tl
            else:
                new_body = body.rstrip() + '\n\n' + content
        elif append:
            new_body = body.rstrip() + '\n\n' + content
        else:
            new_body = content

        # 更新 frontmatter 中的 更新日期 字段
        if fm:
            import re
            from datetime import datetime
            today = datetime.now().strftime('%Y-%m-%d')
            if '更新日期:' in fm:
                fm = re.sub(r'更新日期:\s*\S+', f'更新日期: {today}', fm)
            else:
                fm = re.sub(r'(日期:\s*\S+)', rf'\1\n更新日期: {today}', fm)

        if fm:
            new_full = f"---{fm}---{new_body}"
        else:
            new_full = new_body

        with open(abs_path, 'w', encoding='utf-8') as f:
            f.write(new_full)

        self._log_and_commit(
            log_type="更新",
            summary=f"{'追加' if append else '替换'}内容到 {path}",
            links=[path],
            commit_msg=f"refine: {path}",
        )

        meta = {"success": True, "path": path, "appended": append, "chars_added": len(content)}
        if has_two_zone:
            meta["compiled_truth"] = True
            if timeline_mode:
                meta["timeline_mode"] = True
            if update_compiled_truth:
                meta["compiled_truth_updated"] = True
        return meta

    @tool(readonly=False, write=True, category="page", system=False, name="page_append_section")
    def append_section(self, page: str, section: str, content: str, position: str = "after") -> dict:
        """
        章节级精准插入（定位到指定 ## 标题，不动页面其他部分）。
        场景：只在页面某个章节下补充内容时（如波及分析补引用、某节追加条目）。
        区别：整页追加/替换用 page_update；创建新页用 page_create。

        Args:
            page: 知识页路径
            section: 目标章节标题（如 "## 方案全景对比"，支持 prefix 匹配）
            content: 要追加的 Markdown 内容
            position: "after"=插在章节末尾（默认） / "before"=插在章节之前

        Returns:
            dict: 操作结果
        """
        import os, re
        from datetime import datetime
        
        # 确保有 .md 后缀
        page_path = page
        if not page_path.endswith('.md'):
            page_path = page_path + '.md'
        abs_path = os.path.join(self.vault_path, page_path.replace('/', os.sep))
        
        if not os.path.exists(abs_path):
            return {"success": False, "error": f"知识页不存在: {page}"}
        
        if not content:
            return {"success": False, "error": "content 参数不能为空"}
        
        with open(abs_path, 'r', encoding='utf-8') as f:
            full = f.read()
        
        # 分离 frontmatter 和正文
        if full.startswith('---'):
            parts = full.split('---', 2)
            if len(parts) >= 3:
                fm = parts[1]
                body = parts[2]
            else:
                fm, body = '', full
        else:
            fm, body = '', full
        
        # 在 body 中定位章节
        body_lines = body.split('\n')
        section_lower = section.strip().lower()
        
        # 匹配模式：支持 prefix 匹配（"## 方案全景" → "## 方案全景对比"）
        match_idx = -1
        for i, line in enumerate(body_lines):
            line_stripped = line.strip()
            line_lower = line_stripped.lower()
            if line_lower.startswith(section_lower) or section_lower.startswith(line_lower):
                # 确保是标题行（以 # 开头）
                if line_stripped.startswith('#'):
                    match_idx = i
                    break
        
        if match_idx == -1:
            # 找不到章节 → 降级为 page_update(append=True)
            self.update_page(path=page_path, content=content, append=True)
            return {
                "success": True,
                "path": page_path,
                "section": section,
                "position": "append_fallback",
                "chars_added": len(content),
                "warning": f"未找到章节「{section}」，已降级为全文追加",
            }
        
        # 去重检查：提取该章节的正文范围，看 content 是否已存在
        section_end = len(body_lines)  # 默认到文件末尾
        match_level = len(body_lines[match_idx]) - len(body_lines[match_idx].lstrip('#'))
        for j in range(match_idx + 1, len(body_lines)):
            line = body_lines[j]
            stripped = line.strip()
            if stripped.startswith('#'):
                curr_level = len(line) - len(line.lstrip('#'))
                if curr_level <= match_level:
                    section_end = j
                    break
        section_text = '\n'.join(body_lines[match_idx:section_end])
        if content.strip() in section_text:
            return {
                "success": True,
                "path": page_path,
                "section": section,
                "position": position,
                "chars_added": 0,
                "warning": "内容已存在，跳过追加",
            }
        
        if position == "before":
            # 插在章节标题行之前
            insert_line = match_idx
        else:
            # 插在章节末尾：沿用去重检查已算出的 section_end
            insert_line = section_end
        
        # 插入内容
        content_to_insert = '\n' + content.strip() + '\n'
        body_lines.insert(insert_line, content_to_insert)
        new_body = '\n'.join(body_lines)
        
        # 更新 frontmatter 中的 更新日期
        if fm:
            today = datetime.now().strftime('%Y-%m-%d')
            if '更新日期:' in fm:
                fm = re.sub(r'更新日期:\s*\S+', f'更新日期: {today}', fm)
            else:
                fm = re.sub(r'(日期:\s*\S+)', rf'\1\n更新日期: {today}', fm)
        
        if fm:
            new_full = f"---{fm}---{new_body}"
        else:
            new_full = new_body
        
        with open(abs_path, 'w', encoding='utf-8') as f:
            f.write(new_full)
        
        self._log_and_commit(
            log_type="更新",
            summary=f"精准追加「{section}」→ {page_path}",
            links=[page_path],
            commit_msg=f"refine: {page_path}（追加至 {section}）",
        )
        
        return {
            "success": True,
            "path": page_path,
            "section": section,
            "position": position,
            "chars_added": len(content),
        }

    @tool(readonly=False, write=True, category="page", system=False, name="page_compress")
    def compress_page(self, path: str, dry_run: bool = False) -> dict:
        """
        编译真理压缩——将时间线区的条目合并提炼到编译真理区（P1）

        Args:
            path: 知识页路径
            dry_run: True=只预览不做任何写入

        Returns:
            dict: {success, path, old_entries, new_truth, dry_run, ...}
        """
        import os, re as _re
        from datetime import datetime as _dt

        if not path.endswith('.md'):
            path = path + '.md'
        abs_path = os.path.join(self.vault_path, path.replace('/', os.sep))
        if not os.path.exists(abs_path):
            return {"success": False, "error": f"知识页不存在: {path}"}

        with open(abs_path, 'r', encoding='utf-8') as f:
            full = f.read()

        # 分离 frontmatter 和正文
        if full.startswith('---'):
            parts = full.split('---', 2)
            if len(parts) >= 3:
                fm, body = parts[1], parts[2]
            else:
                fm, body = '', full
        else:
            fm, body = '', full

        # 检测两区结构
        if '## 编译真理' not in body or '## 时间线' not in body:
            return {"success": False, "error": "页面没有编译真理两区结构，无法压缩", "path": path}

        # 提取时间线条目
        tl_marker = '## 时间线'
        tl_idx = body.find(tl_marker)
        tl_section = body[tl_idx + len(tl_marker):]
        # 跳过引述块
        tl_section = _re.sub(r'^[ \t]*>.*(\n|$)*', '', tl_section).strip()
        # 提取 ### 日期 条目
        entries = _re.findall(r'###\s*(\S+)\s*\n(.*?)(?=\n###|\Z)', tl_section, _re.DOTALL)
        if not entries:
            return {"success": False, "error": "时间线区无条目可压缩", "path": path}

        # 保留初始创建以外的条目作为压缩素材
        timeline_entries = [(d, c.strip()) for d, c in entries if '[初始创建]' not in c.strip()]

        # 提取当前编译真理内容
        ct_marker = '## 编译真理'
        ct_idx = body.find(ct_marker)
        ct_end = body.find('\n## ', ct_idx + 1)
        if ct_end < 0 or ct_end > tl_idx:
            ct_end = tl_idx
        current_truth = body[ct_idx + len(ct_marker):ct_end].strip()

        if dry_run:
            return {
                "success": True, "path": path, "dry_run": True,
                "entries_count": len(timeline_entries),
                "entries": [{"date": d, "text": t[:80]} for d, t in timeline_entries],
                "current_truth_preview": current_truth[:200],
            }

        # 调 LLM 合成新编译真理
        today = _dt.now().strftime('%Y-%m-%d')
        merged = '\n\n'.join(f"【{d}】{t}" for d, t in timeline_entries)
        compress_prompt = f"""你是知识压缩引擎。将以下时间线条目综合提炼为一段精炼的编译真理。

当前编译真理：
{current_truth}

时间线待并入条目：
{merged}

要求：
1. 保留关键差异和演进脉络
2. 用结论先行结构
3. 输出纯文字，不要 markdown 标记外的格式
4. 不超过 800 字"""

        try:
            if hasattr(self, '_synthesize_llm'):
                _llm = self._synthesize_llm
            else:
                from llm_reasoning import LLMReasoning
                _llm = LLMReasoning()
                self._synthesize_llm = _llm
            new_truth = _llm._call_llm(compress_prompt, max_tokens=1024, action="compress")
        except Exception as e:
            return {"success": False, "error": f"LLM 调用失败: {e}"}

        # 替换编译真理区
        new_truth_section = f"{ct_marker}\n\n{new_truth.strip()}\n\n"
        new_body = body[:ct_idx] + new_truth_section + body[tl_idx:]

        # 在时间线区追加压缩记录（引述块之后，已有条目之前）
        _tl_after = new_body[new_body.find(tl_marker) + len(tl_marker):]
        _rest_tl = _tl_after.lstrip('\n')
        _tl_quote = ""
        if _rest_tl.startswith('>'):
            _qm = _re.match(r'^((?:>.*\n?)+)', _rest_tl)
            if _qm:
                _tl_quote = _qm.group(1)
                _rest_tl = _rest_tl[_qm.end():]
        tl_entry = f"\n### {today}\n[编译真理合并重写（{len(timeline_entries)} 条时间线条目已合并）]\n"
        updated_tl = tl_marker + '\n' + _tl_quote + tl_entry + _rest_tl
        new_body = new_body[:new_body.find(tl_marker)] + updated_tl

        # 更新 frontmatter
        if fm:
            if '更新日期:' in fm:
                fm = _re.sub(r'更新日期:\s*\S+', f'更新日期: {today}', fm)
            else:
                fm = _re.sub(r'(日期:\s*\S+)', rf'\1\n更新日期: {today}', fm)
            new_full = f"---{fm}---{new_body}"
        else:
            new_full = new_body

        with open(abs_path, 'w', encoding='utf-8') as f:
            f.write(new_full)

        self._log_and_commit(
            log_type="更新",
            summary=f"编译真理压缩: {path}（{len(timeline_entries)} 条条目）",
            links=[path],
            commit_msg=f"refine: {path}（编译真理合并{len(timeline_entries)}条时间线）",
        )

        return {
            "success": True, "path": path, "merged_entries": len(timeline_entries),
            "new_truth": new_truth.strip(),
            "message": f"已合并 {len(timeline_entries)} 条时间线条目到编译真理区",
        }

    @tool(readonly=True, write=False, category="page", system=False, name="page_read")
    def read_page(self, path: str, max_chars: int = 5000) -> dict:
        """
        读取知识页完整内容
        """
        import os
        if not path.endswith('.md'):
            path = path + '.md'
        abs_path = os.path.join(self.vault_path, path.replace('/', os.sep))

        if not os.path.exists(abs_path):
            return {"found": False, "error": f"知识页不存在: {path}"}

        with open(abs_path, 'r', encoding='utf-8') as f:
            content = f.read()

        truncated = len(content) > max_chars
        text = content[:max_chars] + ('\n...（已截断）' if truncated else '')

        return {"found": True, "path": path, "content": text, "char_count": len(content), "truncated": truncated}

    @tool(readonly=False, write=True, category="page", system=False, name="page_add_link")
    def add_link(self, source: str, target: str, label: str = "") -> dict:
        """
        在两个知识页之间建立显式链接（[[wikilink]]）
        """
        import os
        if not source.endswith('.md'):
            source = source + '.md'
        if not target.endswith('.md'):
            target = target + '.md'

        src_path = os.path.join(self.vault_path, source.replace('/', os.sep))
        tgt_path = os.path.join(self.vault_path, target.replace('/', os.sep))

        if not os.path.exists(src_path):
            return {"success": False, "error": f"源页面不存在: {source}"}
        if not os.path.exists(tgt_path):
            return {"success": False, "error": f"目标页面不存在: {target}"}

        # 构建 wikilink 行
        link_text = target.replace('.md', '')
        if label:
            link_line = f"- {label} → [[{link_text}|{label}]]\n"
        else:
            link_line = f"- [[{link_text}]]\n"

        with open(src_path, 'r', encoding='utf-8') as f:
            content = f.read()

        # 检查链接是否已存在
        bare_reference = f"[[{link_text}]]" if not label else f"[[{link_text}|{label}]]"
        if bare_reference in content:
            return {"success": False, "error": f"链接已存在: {source} → {target}", "exists": True}

        # 追加到推荐阅读节（或创建它）
        if '## 推荐阅读' in content:
            content = content.rstrip() + '\n' + link_line
        elif content.endswith('\n'):
            content = content + '## 推荐阅读\n' + link_line
        else:
            content = content + '\n## 推荐阅读\n' + link_line

        # 更新 frontmatter 中的 更新日期 字段
        if content.startswith('---'):
            parts = content.split('---', 2)
            if len(parts) >= 3:
                import re
                from datetime import datetime
                today = datetime.now().strftime('%Y-%m-%d')
                fm_text = parts[1]
                if '更新日期:' in fm_text:
                    fm_text = re.sub(r'更新日期:\s*\S+', f'更新日期: {today}', fm_text)
                else:
                    fm_text = re.sub(r'(日期:\s*\S+)', rf'\1\n更新日期: {today}', fm_text)
                content = f"---{fm_text}---{parts[2]}"

        with open(src_path, 'w', encoding='utf-8') as f:
            f.write(content)

        self._log_and_commit(
            log_type="链接",
            summary=f"建立链接: {source} → {target}",
            links=[source, target],
            commit_msg=f"docs: 链接 {source} → {target}",
        )
        return {"success": True, "source": source, "target": target, "label": label or None}
