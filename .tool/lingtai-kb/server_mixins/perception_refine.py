# -*- coding: utf-8 -*-
"""提炼/原料操作 mixin — refine_status / refine_mark / raw_derive 等

从 perception.py 拆分而来，包含：
- refine-map.json 读写（_refine_map_path / _read_refine_map / _write_refine_map）
- 提炼状态查询（refine_status / refine_list_sources / refine_all_status）
- 原料元数据推导（raw_derive / raw_derive_batch）
- 提炼收尾登记（refine_mark）

通过多继承混入主 Server 类，依赖 self.vault_path 等属性。
"""

import glob
import json
import os
import re
from datetime import datetime

from decorators import tool
from logger import get_logger

log = get_logger(__name__)


class RefineMixin:

    def _refine_map_path(self):
        return os.path.join(self.vault_path, '.lingtai', 'refine-map.json')

    def _read_refine_map(self):
        """读取 refine-map.json（走 stub 缓存，避免同一流程中重复解析）"""
        from server_mixins.stub_manager import stub_refine_map
        return stub_refine_map.read(self.vault_path)

    def _write_refine_map(self, data):
        """写入 refine-map.json 并刷新缓存"""
        import json, os
        path = self._refine_map_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
        from server_mixins.stub_manager import stub_refine_map
        stub_refine_map.invalidate()

    @tool(readonly=True, write=False, category="refine", system=False, name="refine_status")
    def refine_status(self, raw_path: str = "", mode: str = "single", target: str = "", domain: str = "") -> dict:
        """提炼状态。M3: mode=single(单篇)/sources(某页的原料)/all(全量统计)"""
        if mode == "sources":
            rmap = self._read_refine_map()
            sources = [k for k, v in rmap.items() if v.get('target', '').rstrip('.md') == target.rstrip('.md')]
            return {"target": target, "sources": sources, "count": len(sources)}
        if mode == "all":
            import os, re, glob
            _TERMINAL = ('已提炼', 'processed', 'done', 'refined', '已跳过', 'skipped', 'ignored', '放弃', '废弃', 'duplicate')
            _re_status = re.compile(r'^(?:处理状态|状态|refine_status)\s*[:：]\s*(.+)$')
            raw_dir = os.path.join(self.vault_path, '原料')
            total_raw = 0
            total_refined = 0
            if os.path.isdir(raw_dir):
                for fp in glob.glob(os.path.join(raw_dir, '**', '*.md'), recursive=True):
                    if os.path.basename(fp) == '_index.md':
                        continue
                    total_raw += 1
                    try:
                        head = open(fp, 'r', encoding='utf-8', errors='ignore').read(4096)
                    except Exception:
                        continue
                    for line in head.splitlines():
                        mm = _re_status.match(line)
                        if mm and mm.group(1).strip() in _TERMINAL:
                            total_refined += 1
                            break
            return {"refined": total_refined, "total_raw": total_raw, "pending": total_raw - total_refined,
                    "coverage": f"{total_refined/total_raw*100:.1f}%" if total_raw > 0 else "N/A"}
        import os
        rmap = self._read_refine_map()
        entry = rmap.get(raw_path)
        if entry:
            return {"refined": True, **entry}
        abs_path = os.path.join(self.vault_path, raw_path.replace('/', os.sep))
        if not os.path.exists(abs_path):
            return {"refined": False, "error": f"文件不存在: {raw_path}"}
        import re
        with open(abs_path, 'r', encoding='utf-8') as fh:
            fc = fh.read()
        _TERMINAL = ('已提炼', 'processed', 'done', 'refined', '已跳过', 'skipped', 'ignored', '放弃', '废弃', 'duplicate')
        for line in fc.splitlines():
            mm = re.match(r'^(?:处理状态|状态|refine_status)\s*[:：]\s*(.+)$', line)
            if mm and mm.group(1).strip() in _TERMINAL:
                return {"refined": True, "source": "frontmatter"}
        return {"refined": False}

    @tool(readonly=True, write=False, category="refine", system=True)
    def refine_list_sources(self, target: str) -> dict:
        """查某个丹房页的所有来源原料"""
        rmap = self._read_refine_map()
        sources = [k for k, v in rmap.items() if v.get('target', '').rstrip('.md') == target.rstrip('.md')]
        return {"target": target, "sources": sources, "count": len(sources)}

    @tool(readonly=True, write=False, category="refine", system=True)
    def refine_all_status(self, domain: str = "") -> dict:
        """原料提炼状态统计（扫描原料 FM 的 处理状态 字段，兼容双重 frontmatter + 已跳过终态）"""
        import os, re, glob
        _TERMINAL = ('已提炼', 'processed', 'done', 'refined', '已跳过', 'skipped', 'ignored', '放弃', '废弃', 'duplicate')
        _re_status = re.compile(r'^(?:处理状态|状态|refine_status)\s*[:：]\s*(.+)$')
        raw_dir = os.path.join(self.vault_path, '原料')
        total_raw = 0
        total_refined = 0
        if os.path.isdir(raw_dir):
            for fp in glob.glob(os.path.join(raw_dir, '**', '*.md'), recursive=True):
                if os.path.basename(fp) == '_index.md':
                    continue
                total_raw += 1
                try:
                    head = open(fp, 'r', encoding='utf-8', errors='ignore').read(4096)
                except Exception:
                    continue
                for line in head.splitlines():
                    mm = _re_status.match(line)
                    if mm and mm.group(1).strip() in _TERMINAL:
                        total_refined += 1
                        break
        return {
            "refined": total_refined,
            "total_raw": total_raw,
            "pending": total_raw - total_refined,
            "coverage": f"{total_refined/total_raw*100:.1f}%" if total_raw > 0 else "N/A"
        }

    @tool(readonly=True, write=False, category="raw", system=False, name="raw_derive")
    def raw_derive(self, raw_path: str = "", mode: str = "single", limit: int = 200, skip_refined: bool = True, sort_by: str = "newest") -> dict:
        """零 LLM 推导原料元数据。M4: mode=single(单篇) / batch(批量扫描)"""
        if mode == "batch":
            from raw_derive import batch_derive
            return batch_derive(limit=limit, vault_root=self.vault_path, skip_refined=skip_refined, sort_by=sort_by)
        import os
        from raw_derive import derive_raw_candidate
        full_path = os.path.join(self.vault_path, raw_path)
        return derive_raw_candidate(full_path, vault_root=self.vault_path)

    @tool(readonly=True, write=False, category="raw", system=True)
    def raw_derive_batch(self, limit: int = 200, skip_refined: bool = True, sort_by: str = "newest") -> dict:
        """批量扫描原料目录，返回推导结果+统计摘要。"""
        from raw_derive import batch_derive
        return batch_derive(limit=limit, vault_root=self.vault_path, skip_refined=skip_refined, sort_by=sort_by)

    @tool(readonly=False, write=True, category="refine", system=False)
    def refine_mark(self, raw_path: str, target: str, summary: str, grade: str = "正常", force: bool = False, operation_type: str = "new") -> dict:
        """
        提炼收尾登记——更新原料FM、追加日志（不做内容写作，不单独 git commit）
        
        Args:
            raw_path: 原料路径（如 原料/xxx.md）
            target: 目标丹房页路径（如 丹房/xx/xxx）
            summary: 提炼摘要
            grade: 提炼分级（快速/正常/完整，默认正常）
            force: 跳过去重预检（默认 False）
            operation_type: 操作类型——"new"=新建页（默认，触发去重）/ "supplement"=补充既有页（自动跳过去重）
        
        Returns:
            dict: 收尾结果
        """
        import os, re
        from datetime import datetime

        vault = self.vault_path
        now = datetime.now()
        date_str = now.strftime('%Y-%m-%d')
        log_prefix = now.strftime('[%y-%m-%d %H:%M]')

        # 1. 更新原料 frontmatter
        raw_abs = os.path.join(vault, raw_path) if not os.path.isabs(raw_path) else raw_path
        if not os.path.isfile(raw_abs):
            return {"success": False, "error": f"原料文件不存在: {raw_path}"}

        # 1a. 去重预检：检查目标域是否已有同主题页（force=True 或 supplement 模式则跳过）
        raw_basename = os.path.splitext(os.path.basename(raw_path))[0]
        if not force and operation_type != "supplement":
            # 清理 IMA 前缀和时间戳后缀
            raw_keywords = re.sub(r'^IMA_', '', raw_basename)
            raw_keywords = re.sub(r'_\d{8}-\d{6}$', '', raw_keywords)
            # 从 target 中提取域名
            domain_match = re.match(r'丹房/([^/]+)/', target)
            if domain_match:
                domain_dir = domain_match.group(1)
                domain_abs = os.path.join(vault, '丹房', domain_dir)
                if os.path.isdir(domain_abs):
                    existing_pages = [f.replace('.md', '') for f in os.listdir(domain_abs) if f.endswith('.md')]
                    # 字符级 2-gram（bigram）重叠去重
                    def _bigrams(s):
                        s_clean = re.sub(r'[\s\-_—]+', '', s)
                        return {s_clean[i:i+2] for i in range(len(s_clean)-1)}
                    raw_bg = _bigrams(raw_keywords)
                    dupes = []
                    for ep in existing_pages:
                        ep_bg = _bigrams(ep)
                        overlap = raw_bg & ep_bg
                        if len(overlap) >= 2 and len(overlap) / max(len(raw_bg), 1) >= 0.15:
                            dupes.append((ep, len(overlap)))
                    dupes.sort(key=lambda x: -x[1])
                    if dupes:
                        dup_names = [d[0] for d in dupes[:5]]
                        log.warning("refine_mark dedup alert", extra={"raw": raw_basename, "domain": domain_dir, "similar": dup_names})
                        return {
                            "success": False, "warning": "duplicate_detected",
                            "raw": raw_path, "target": target,
                            "existing_pages": dup_names,
                            "message": f"目标域「{domain_dir}」已有相似页: {dup_names}。请确认是否追加到已有页，而非新建重页。",
                        }

        with open(raw_abs, 'r', encoding='utf-8') as f:
            raw_content = f.read()

        if raw_content.startswith('---'):
            end = raw_content.find('---', 3)
            if end > 0:
                fm_block = raw_content[3:end].strip()
                body = raw_content[end + 3:]
                # 翻转提炼状态：兼容「状态:」与「处理状态:」两种字段名
                raw_title = os.path.splitext(os.path.basename(raw_path))[0]
                status_field_name = '（无）'
                status_old_val = '（无）'
                if re.search(r'^处理状态:', fm_block, re.MULTILINE):
                    m = re.search(r'^处理状态:\s*(.+)', fm_block, re.MULTILINE)
                    if m: status_old_val = m.group(1).strip()
                    status_field_name = '处理状态'
                    fm_block = re.sub(r'^处理状态:.*', '处理状态: 已提炼', fm_block, flags=re.MULTILINE)
                elif re.search(r'^状态:', fm_block, re.MULTILINE):
                    m = re.search(r'^状态:\s*(.+)', fm_block, re.MULTILINE)
                    if m: status_old_val = m.group(1).strip()
                    status_field_name = '状态'
                    fm_block = re.sub(r'^状态:.*', '状态: 已提炼', fm_block, flags=re.MULTILINE)
                else:
                    fm_block += '\n状态: 已提炼'
                    status_field_name = '（追加）'
                log.info("refine_mark done", extra={"raw": raw_title, "target": target, "status_field": status_field_name})
                if re.search(r'^处理日期:', fm_block, re.MULTILINE):
                    fm_block = re.sub(r'^处理日期:.*', f'处理日期: {date_str}', fm_block, flags=re.MULTILINE)
                else:
                    fm_block += f'\n处理日期: {date_str}'
                if re.search(r'^提炼分级:', fm_block, re.MULTILINE):
                    fm_block = re.sub(r'^提炼分级:.*', f'提炼分级: {grade}', fm_block, flags=re.MULTILINE)
                else:
                    fm_block += f'\n提炼分级: {grade}'
                if re.search(r'^提炼摘要:', fm_block, re.MULTILINE):
                    fm_block = re.sub(r'^提炼摘要:.*', f'提炼摘要: {summary}', fm_block, flags=re.MULTILINE)
                else:
                    fm_block += f'\n提炼摘要: {summary}'
                raw_content = f'---\n{fm_block}\n---\n{body}'
        else:
            # 文件无 frontmatter：创建新 frontmatter
            raw_title = os.path.splitext(os.path.basename(raw_path))[0]
            fm_block = f'处理状态: 已提炼\n处理日期: {date_str}\n提炼分级: {grade}\n提炼摘要: {summary}'
            raw_content = f'---\n{fm_block}\n---\n{raw_content}'
            log.info("refine_mark done (no frontmatter)", extra={"raw": raw_title, "target": target})

        # 追加回链
        backlink = f'→ [[{target}]]'
        if backlink not in raw_content:
            body_idx = raw_content.find('\n---', 3)
            if body_idx > 0:
                body = raw_content[body_idx + 4:]
                raw_content = raw_content[:body_idx + 4] + body.rstrip() + f'\n\n{backlink}\n'

        with open(raw_abs, 'w', encoding='utf-8') as f:
            f.write(raw_content)

        # 同步写 refine-map.json
        rmap = self._read_refine_map()
        from datetime import datetime
        rmap[raw_path] = {
            "target": target,
            "grade": grade,
            "summary": summary,
            "date": datetime.now().strftime('%Y-%m-%d'),
        }
        self._write_refine_map(rmap)

        return {
            "success": True,
            "raw": raw_path,
            "target": target,
            "summary": summary,
            "grade": grade,
            "message": f"原料已标记为已提炼（依赖 page_create/page_update 的 commit 做统一提交）",
        }
