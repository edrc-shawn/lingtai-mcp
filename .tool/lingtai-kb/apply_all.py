# -*- coding: utf-8 -*-
"""灵台 MCP 重构 — Phase 1+2+4 合一修补脚本

在 git checkout 恢复的原始文件上，一次性应用：
- Phase 1: @tool 装饰器
- Phase 2: M1-M9 合并
- Phase 4: 惰性加载引擎

用法: python apply_all.py
"""
import os, re, ast

BASE = os.path.dirname(os.path.abspath(__file__))
MIXIN_DIR = os.path.join(BASE, "server_mixins")

# ═══════════════════════════════════════════════
# PART A: @tool 装饰器 (Phase 1)
# ═══════════════════════════════════════════════

TOOL_TO_METHOD = {
    "knowledge_search": "query", "knowledge_explore": "explore_topic",
    "knowledge_inject": "inject", "raw_save": "save",
    "knowledge_gaps": "gaps", "knowledge_digest": "digest",
    "ingest_ripple": "ingest_ripple", "context_load": "ensure_context",
    "cross_end_activity": "cross_end_activity", "lingshi_inject": "lingshi_inject",
    "memory_write": "mem_write", "memory_search": "mem_query",
    "memory_stats": "mem_stats", "memory_consolidate": "mem_consolidate",
    "memory_feedback": "mem_feedback", "memory_merge": "mem_merge",
    "memory_decay": "mem_decay", "memory_scan_conflicts": "mem_scan_conflicts",
    "memory_archive": "mem_archive", "memory_link": "mem_link",
    "memory_lifecycle": "mem_lifecycle",
    "user_push": "memory_push", "user_feedback": "user_feedback",
    "observation_list": "observations", "observation_stats": "observation_stats",
    "observation_rule_health": "sentinel", "observation_reflect": "reflect",
    "page_create": "create_page", "page_update": "update_page",
    "page_append_section": "append_section", "page_read": "read_page",
    "page_add_link": "add_link", "page_link_suggest": "link_suggest",
    "page_history": "page_history",
    "refine_mark": "refine_mark", "refine_status": "refine_status",
    "refine_list_sources": "refine_list_sources", "refine_all_status": "refine_all_status",
    "refine_quick": "refine_quick",
    "raw_derive": "raw_derive", "raw_derive_batch": "raw_derive_batch",
    "knowledge_stats": "stats", "knowledge_domains": "domains",
    "knowledge_pages": "pages", "knowledge_search_evidence": "search_evidence",
    "knowledge_compound": "compound", "knowledge_heatmap": "heatmap",
    "concept_collide": "concept_collide", "lifecycle_scan": "lifecycle_scan",
    "system_sop": "sop", "system_refresh_index": "refresh_index",
    "sys_reload": "reload", "web_search": "web_search",
    "fulltext_search": "fulltext_search", "system_search_logs": "search_logs",
    "system_health": "system_health", "system_restart": "restart",
    "system_token": "token", "system_check_status": "check_status",
    "system_registry_scan": "registry_scan", "vector_index_status": "vector_index_status",
    "domain_visibility": "domain_visibility",
    "health_inspect": "health_inspect", "health_ledger": "health_ledger",
    "episodic_recent": "episodic_recent", "episodic_search": "episodic_search",
    "knowledge_recall": "knowledge_recall", "session_end": "session_end",
    "auto_evolve": "auto_evolve", "topic_match": "topic_match",
    "get_macro_stats": "get_macro_stats",
    "output_list": "output_list", "output_publish": "output_publish",
    "skill_list": "skill_list",
    "agent_recommend": "agent_recommend", "agent_feedback": "agent_feedback",
    "agent_skills": "agent_skills",
    "skillopt_dryrun": "skillopt_dryrun", "skillopt_run": "skillopt_run",
    "skillopt_status": "skillopt_status", "skillopt_adopt": "skillopt_adopt",
    "skillopt_reject": "skillopt_reject", "skillopt_log": "skillopt_log",
}

# Build reverse map: method_name → tool_name
METHOD_TO_TOOL = {v: k for k, v in TOOL_TO_METHOD.items()}

# Tool categories
TOOL_CATEGORY = {}
for tn in TOOL_TO_METHOD:
    if tn.startswith("knowledge_"): TOOL_CATEGORY[tn] = "knowledge"
    elif tn.startswith("page_"): TOOL_CATEGORY[tn] = "page"
    elif tn.startswith("refine_"): TOOL_CATEGORY[tn] = "refine"
    elif tn.startswith("raw_"): TOOL_CATEGORY[tn] = "raw"
    elif tn.startswith("memory_"): TOOL_CATEGORY[tn] = "memory"
    elif tn.startswith("observation_"): TOOL_CATEGORY[tn] = "observation"
    elif tn.startswith("lingshi_"): TOOL_CATEGORY[tn] = "lingshi"
    elif tn.startswith("user_"): TOOL_CATEGORY[tn] = "user"
    elif tn.startswith("health_"): TOOL_CATEGORY[tn] = "health"
    elif tn.startswith("system_") or tn.startswith("sys_") or tn in ("context_load", "cross_end_activity", "fulltext_search", "web_search", "episodic_recent", "episodic_search"): TOOL_CATEGORY[tn] = "system"
    elif tn in ("knowledge_recall", "session_end", "refine_quick", "auto_evolve", "topic_match", "get_macro_stats"): TOOL_CATEGORY[tn] = "macro"
    elif tn.startswith("skillopt_"): TOOL_CATEGORY[tn] = "pipeline"
    elif tn.startswith("output_"): TOOL_CATEGORY[tn] = "output"
    elif tn.startswith("agent_"): TOOL_CATEGORY[tn] = "agent"
    elif tn in ("concept_collide", "lifecycle_scan"): TOOL_CATEGORY[tn] = "concept"
    else: TOOL_CATEGORY[tn] = "general"

# System tools (pipeline-only, never exposed)
SYSTEM_TOOLS = {
    "skillopt_dryrun", "skillopt_run", "skillopt_status",
    "skillopt_adopt", "skillopt_reject", "skillopt_log",
    "memory_decay", "auto_evolve", "system_token",
    "system_restart", "get_macro_stats",
    # Phase 2 merged tools (system=True, alias redirects)
    "knowledge_stats", "knowledge_domains", "knowledge_pages",
    "refine_list_sources", "refine_all_status",
    "raw_derive_batch",
    "memory_lifecycle", "memory_merge", "memory_archive",
    "observation_list", "observation_stats", "observation_rule_health",
    "system_health", "system_check_status", "system_search_logs",
}

# Readonly tools  
READONLY_TOOLS = {
    "knowledge_search", "knowledge_explore", "knowledge_inject",
    "knowledge_search_evidence", "knowledge_compound", "knowledge_heatmap",
    "concept_collide", "lifecycle_scan", "ingest_ripple", "knowledge_stats",
    "knowledge_domains", "knowledge_pages", "knowledge_gaps", "knowledge_digest",
    "context_load", "cross_end_activity",
    "observation_list", "observation_stats", "observation_rule_health", "observation_reflect",
    "memory_search", "memory_stats", "memory_scan_conflicts", "memory_consolidate", "memory_lifecycle",
    "page_read", "page_link_suggest", "page_history",
    "refine_status", "refine_list_sources", "refine_all_status",
    "raw_derive", "raw_derive_batch",
    "user_push", "user_feedback",
    "system_sop", "system_search_logs", "fulltext_search", "web_search",
    "health_inspect", "system_health", "episodic_recent", "episodic_search",
    "knowledge_recall", "topic_match",
    "output_list", "skill_list", "agent_recommend", "agent_skills",
    "skillopt_dryrun", "skillopt_status", "skillopt_log",
    "vector_index_status", "domain_visibility",
    "knowledge_overview", "observation_dashboard",
}


def method_to_file():
    """建立 方法名 → 文件名 映射"""
    m2f = {}
    for fname in os.listdir(MIXIN_DIR):
        if not fname.endswith(".py") or fname in ("__init__.py", "shared.py", "_fix_phase2.py", "_split_perception.py", "page_manager.py", "refine.py"):
            continue
        path = os.path.join(MIXIN_DIR, fname)
        with open(path, "r", encoding="utf-8") as f:
            try:
                tree = ast.parse(f.read())
            except SyntaxError:
                continue
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and not node.name.startswith("_"):
                m2f[node.name] = fname
    # server.py methods
    for m in ("ensure_context", "cross_end_activity", "agent_recommend", "agent_feedback", "agent_skills"):
        m2f[m] = "server.py"
    return m2f


def apply_decorators():
    """Phase 1: 给所有方法加 @tool 装饰器"""
    m2f = method_to_file()
    file_methods = {}
    for tool_name, method_name in sorted(TOOL_TO_METHOD.items()):
        fname = m2f.get(method_name)
        if not fname:
            continue
        readonly = tool_name in READONLY_TOOLS
        write = not readonly
        category = TOOL_CATEGORY.get(tool_name, "general")
        system = tool_name in SYSTEM_TOOLS
        
        nm = ""
        if tool_name != method_name:
            nm = f', name="{tool_name}"'
        decorator = f'    @tool(readonly={readonly}, write={write}, category="{category}", system={system}{nm})'
        file_methods.setdefault(fname, []).append((method_name, decorator))

    for fname, methods in file_methods.items():
        if fname == "server.py":
            fpath = os.path.join(BASE, "server.py")
        else:
            fpath = os.path.join(MIXIN_DIR, fname)
        
        with open(fpath, "r", encoding="utf-8") as f:
            lines = f.readlines()

        # Insert decorators before def lines (avoiding duplicates)
        method_set = {m[0] for m in methods}
        new_lines = []
        i = 0
        while i < len(lines):
            line = lines[i]
            # Skip existing @tool decorators
            if line.strip().startswith("@tool(") or (line.strip() == "" and i+1 < len(lines) and lines[i+1].strip().startswith("@tool(")):
                if line.strip().startswith("@tool("):
                    i += 1
                    continue
                elif i+1 < len(lines) and lines[i+1].strip().startswith("@tool("):
                    i += 2
                    continue
            # Check if next line starts a decorated method
            m = re.match(r'\s+def (\w+)', line)
            if m and m.group(1) in method_set:
                for mn, deco in methods:
                    if mn == m.group(1):
                        new_lines.append(deco + "\n")
                        break
            new_lines.append(line)
            i += 1

        # Ensure import
        has_import = any("from decorators import tool" in l for l in new_lines)
        if not has_import:
            last_import = 0
            for j, l in enumerate(new_lines):
                if l.startswith("import ") or l.startswith("from "):
                    last_import = j
            new_lines.insert(last_import + 1, "from decorators import tool\n")

        with open(fpath, "w", encoding="utf-8") as f:
            f.writelines(new_lines)
        print(f"  ✅ decorators: {fname} ({len(methods)} methods)")


# ═══════════════════════════════════════════════
# PART B: Phase 2 merges
# ═══════════════════════════════════════════════

def apply_merges():
    """Apply M1-M9 merge changes to specific files"""
    changes = 0
    
    # M5: memory_stats + lifecycle
    mb = os.path.join(MIXIN_DIR, "memory_bank.py")
    with open(mb, "r", encoding="utf-8") as f:
        c = f.read()
    old = '''    @tool(readonly=True, write=False, category="memory", system=False)
    def mem_stats(self) -> dict:
        """记忆银行统计"""
        return self.memory_bank.stats()'''
    new = '''    @tool(readonly=True, write=False, category="memory", system=False)
    def mem_stats(self) -> dict:
        """记忆银行统计（含跨域生命周期）"""
        stats = self.memory_bank.stats()
        try:
            stats["lifecycle"] = self.memory_bank.lifecycle_stats()
        except Exception:
            stats["lifecycle"] = {"error": "lifecycle_stats 不可用"}
        return stats'''
    if old in c:
        c = c.replace(old, new); changes += 1
    
    # M6: memory_feedback expanded
    old = '''    def mem_feedback(self, memory_id: str, action: str) -> dict:
        """用户反馈（采纳/否定）
        adopt = 强验证闭环'''
    if old in c:
        new_fb = '''    def mem_feedback(self, memory_id: str, action: str, target_branch: str = "通用", reason: str = "obsolete") -> dict:
        """用户反馈 M6: adopt(采纳) / reject(否定) / merge(跨分支合并) / archive(标记废弃)"""
        if action == "adopt":
            return self.memory_bank.evidence_increment(memory_id, boost=0.1)
        elif action == "reject":
            return self.memory_bank.update_confidence(memory_id, -0.3)
        elif action == "merge":
            return self.memory_bank.merge(memory_id, target_branch)
        elif action == "archive":
            return self.memory_bank.archive(memory_id)
        return {"success": False, "error": "action must be adopt/reject/merge/archive"}

    @tool(readonly=True, write=True, category="memory", system=True, name="memory_merge")
    def mem_merge(self, memory_id: str, target_branch: str = "通用") -> dict:
        """M6: 已合并到 memory_feedback(action="merge")"""

    @tool(readonly=True, write=True, category="memory", system=True, name="memory_archive")
    def mem_archive(self, memory_id: str, reason: str = "obsolete") -> dict:
        """M6: 已合并到 memory_feedback(action="archive")"""'''
        # Need to find the exact old text including the full method
        idx = c.find(old)
        if idx >= 0:
            end_idx = c.find("\n    @tool(", idx + len(old))
            if end_idx < 0:
                end_idx = c.find("\n    def mem_link", idx + len(old))
            if end_idx < 0:
                end_idx = c.find("\n    def mem_lifecycle", idx + len(old))
            old_full = c[idx:end_idx]
            c = c.replace(old_full, new_fb); changes += 1

    with open(mb, "w", encoding="utf-8") as f:
        f.write(c)
    print(f"  ✅ memory_bank.py: {changes} changes")

    # M9: fulltext_search scope="日志"
    sysf = os.path.join(MIXIN_DIR, "system.py")
    with open(sysf, "r", encoding="utf-8") as f:
        c = f.read()
    old = '''            "外部参考": ("技能/外部参考", "灵台·外部参考"),
        }'''
    new = '''            "外部参考": ("技能/外部参考", "灵台·外部参考"),
            "日志": (".tool/lingtai-kb/logs", "灵台·日志"),  # M9
        }'''
    if old in c:
        c = c.replace(old, new); changes += 1
    
    with open(sysf, "w", encoding="utf-8") as f:
        f.write(c)
    print(f"  ✅ system.py: scope=日志 added")

    # M1: knowledge_overview
    kf = os.path.join(MIXIN_DIR, "knowledge.py")
    with open(kf, "r", encoding="utf-8") as f:
        c = f.read()
    old = '''    @tool(readonly=True, write=False, category="knowledge", system=False)
    def stats(self) -> dict:
        """
        获取知识库统计
        
        Returns:
            dict: 统计信息
        """'''
    new = '''    @tool(readonly=True, write=False, category="knowledge", system=False)
    def knowledge_overview(self, mode: str = "stats", domain: str = "", limit: int = 50) -> dict:
        """知识库总览。M1: mode=stats(统计)/domains(域列表)/pages(页面列表)"""
        if mode == "domains":
            return self.domains()
        if mode == "pages":
            return self.pages(domain=domain or None, limit=limit)
        return self.stats()

    @tool(readonly=True, write=False, category="knowledge", system=True, name="knowledge_stats")
    def stats(self) -> dict:
        """M1: 已合并到 knowledge_overview"""'''
    if old in c:
        c = c.replace(old, new); changes += 1
    
    old = '''    @tool(readonly=True, write=False, category="knowledge", system=False)
    def domains(self) -> dict:
        """
        获取域列表
        
        Returns:
            dict: 域列表和页面数
        """'''
    new = '''    @tool(readonly=True, write=False, category="knowledge", system=True, name="knowledge_domains")
    def domains(self) -> dict:
        """M1: 已合并到 knowledge_overview(mode="domains")"""'''
    if old in c:
        c = c.replace(old, new); changes += 1
    
    with open(kf, "w", encoding="utf-8") as f:
        f.write(c)
    print(f"  ✅ knowledge.py: knowledge_overview added")

    # M7: observation_dashboard
    obf = os.path.join(MIXIN_DIR, "observation.py")
    with open(obf, "r", encoding="utf-8") as f:
        c = f.read()
    
    # Add dashboard at top of class
    old = '''class ObservationMixin:
    @tool(readonly=True, write=False, category="observation", system=False)
    def observations(self, keyword: str = "", limit: int = 20) -> dict:
        """
        查询自动归纳出的观察'''
    new = '''class ObservationMixin:
    @tool(readonly=True, write=False, category="observation", system=False)
    def obs_dashboard(self) -> dict:
        """观察总览。M7: stats + rules 一步获取"""
        try:
            stats = self.observation.get_stats()
        except Exception:
            stats = {"error": "stats 不可用"}
        try:
            rules = self.perception_stats_monitor.get_monitoring_report()
        except Exception:
            rules = {"error": "rules 不可用"}
        return {"stats": stats, "rules": rules}

    @tool(readonly=True, write=False, category="observation", system=True, name="observation_list")
    def observations(self, keyword: str = "", limit: int = 20) -> dict:
        """M7: 已合并到 observation_dashboard"""'''
    if old in c:
        c = c.replace(old, new); changes += 1
    
    # observation_stats → system=True  
    old = '''    @tool(readonly=True, write=False, category="observation", system=False)
    def observation_stats(self) -> dict:
        """
        观察层统计信息
        
        Returns:
            dict: 统计
        """
        return self.observation.get_stats()'''
    new = '''    @tool(readonly=True, write=False, category="observation", system=True, name="observation_stats")
    def observation_stats(self) -> dict:
        """M7: 已合并到 observation_dashboard"""'''
    if old in c:
        c = c.replace(old, new); changes += 1
    
    # sentinel → system=True
    old = '''    @tool(readonly=True, write=False, category="observation", system=False)
    def sentinel(self) -> dict:
        """
        感知规则监控报告（Sentinel）。检查各规则的健康状态和违规情况
        
        Returns:
            dict: 监控报告（含健康状态、违规列表、统计摘要）
        """
        return self.perception_stats_monitor.get_monitoring_report()'''
    new = '''    @tool(readonly=True, write=False, category="observation", system=True, name="observation_rule_health")
    def sentinel(self) -> dict:
        """M7: 已合并到 observation_dashboard"""'''
    if old in c:
        c = c.replace(old, new); changes += 1
    
    with open(obf, "w", encoding="utf-8") as f:
        f.write(c)
    print(f"  ✅ observation.py: dashboard added")


def apply_m3_m4():
    """M3+M4: refine_status expand, raw_derive expand"""
    pf = os.path.join(MIXIN_DIR, "perception.py")
    with open(pf, "r", encoding="utf-8") as f:
        c = f.read()
    
    # M3: refine_status
    old = '''    @tool(readonly=True, write=False, category="refine", system=False)
    def refine_status(self, raw_path: str) -> dict:
        """查某条原料的提炼状态"""
        import os
        rmap = self._read_refine_map()
        entry = rmap.get(raw_path)
        if entry:
            return {"refined": True, **entry}
        # miss 时回填 frontmatter 检查
        abs_path = os.path.join(self.vault_path, raw_path.replace('/', os.sep))
        if not os.path.exists(abs_path):
            return {"refined": False, "error": f"文件不存在: {raw_path}"}
        with open(abs_path, 'r', encoding='utf-8') as f:
            content = f.read()
        if content.startswith('---'):
            parts = content.split('---', 2)
            if len(parts) >= 3:
                fm = parts[1]
                if '处理状态: 已提炼' in fm or '处理状态:已提炼' in fm:
                    return {"refined": True, "source": "frontmatter", "note": "旧格式，建议迁移"}
        return {"refined": False}'''
    new = '''    @tool(readonly=True, write=False, category="refine", system=False, name="refine_status")
    def refine_status(self, raw_path: str = "", mode: str = "single", target: str = "", domain: str = "") -> dict:
        """提炼状态。M3: mode=single(单篇)/sources(某页的原料)/all(全量统计)"""
        if mode == "sources":
            rmap = self._read_refine_map()
            sources = [k for k, v in rmap.items() if v.get('target', '').rstrip('.md') == target.rstrip('.md')]
            return {"target": target, "sources": sources, "count": len(sources)}
        if mode == "all":
            import os
            rmap = self._read_refine_map()
            total_refined = len(rmap)
            raw_dir = os.path.join(self.vault_path, '原料')
            total_raw = 0
            if os.path.isdir(raw_dir):
                for f in os.listdir(raw_dir):
                    if f.endswith('.md'):
                        total_raw += 1
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
        with open(abs_path, 'r', encoding='utf-8') as fh:
            fc = fh.read()
        if fc.startswith('---'):
            parts = fc.split('---', 2)
            if len(parts) >= 3:
                fm = parts[1]
                if '处理状态: 已提炼' in fm or '处理状态:已提炼' in fm:
                    return {"refined": True, "source": "frontmatter", "note": "旧格式，建议迁移"}
        return {"refined": False}'''
    if old in c:
        c = c.replace(old, new); print("  ✅ M3: refine_status")

    # M3: system=True markers
    for method in ("refine_list_sources", "refine_all_status"):
        c = c.replace(f'system=False)\n    def {method}(', f'system=True, name="{method}")\n    def {method}(')
    print("  ✅ M3: system markers")

    # M4: raw_derive
    old = '''    @tool(readonly=True, write=False, category="raw", system=False)
    def raw_derive(self, raw_path: str) -> dict:
        """零 LLM 推导单条原料元数据。"""
        import os
        from raw_derive import derive_raw_candidate
        full_path = os.path.join(self.vault_path, raw_path)
        return derive_raw_candidate(full_path, vault_root=self.vault_path)'''
    new = '''    @tool(readonly=True, write=False, category="raw", system=False, name="raw_derive")
    def raw_derive(self, raw_path: str = "", mode: str = "single", limit: int = 200, skip_refined: bool = True, sort_by: str = "newest") -> dict:
        """零 LLM 推导原料元数据。M4: mode=single(单篇) / batch(批量扫描)"""
        if mode == "batch":
            from raw_derive import batch_derive
            return batch_derive(limit=limit, vault_root=self.vault_path, skip_refined=skip_refined, sort_by=sort_by)
        import os
        from raw_derive import derive_raw_candidate
        full_path = os.path.join(self.vault_path, raw_path)
        return derive_raw_candidate(full_path, vault_root=self.vault_path)'''
    if old in c:
        c = c.replace(old, new); print("  ✅ M4: raw_derive")
    c = c.replace(f'system=False)\n    def raw_derive_batch(', f'system=True, name="raw_derive_batch")\n    def raw_derive_batch(')
    print("  ✅ M4: raw_derive_batch → system=True")

    with open(pf, "w", encoding="utf-8") as f:
        f.write(c)


def apply_m8():
    """M8: health_inspect absorb system_health + check_status"""
    sysf = os.path.join(MIXIN_DIR, "system.py")
    with open(sysf, "r", encoding="utf-8") as f:
        c = f.read()
    
    # Find health_inspect's end and add M8 data
    old = '''        data["summary"]["overall_score"] = f"{score}/100 ({grade})"
        '''
    new = '''        data["summary"]["overall_score"] = f"{score}/100 ({grade})"

        # M8: 吸收 system_health + system_check_status
        try:
            import os as _os
            raw_dir = _os.path.join(self.vault_path, '原料')
            raw_count = 0; pending_raw = 0
            if _os.path.isdir(raw_dir):
                for f in _os.listdir(raw_dir):
                    if f.endswith('.md'):
                        raw_count += 1
                        try:
                            with open(_os.path.join(raw_dir, f), 'r', encoding='utf-8') as fh:
                                fc = fh.read(500)
                                if '处理状态' not in fc or '已提炼' not in fc:
                                    pending_raw += 1
                        except Exception:
                            pass
            data["system_health"] = {"total_raw": raw_count, "pending_raw": pending_raw}
        except Exception:
            data["system_health"] = {"error": "不可用"}
        try:
            import subprocess
            repo = os.path.dirname(self.vault_path)
            result = subprocess.run(['git', 'status', '--short'], capture_output=True, text=True, cwd=repo, encoding='utf-8', errors='ignore')
            dirty = result.stdout.strip()
            data["git_status"] = {"has_changes": bool(dirty), "changes": dirty.split('\\\\n') if dirty else []}
        except Exception:
            data["git_status"] = {"has_changes": False, "error": "git 不可用"}

        self.__dict__["_health_inspect_cache"] = {"_ts": datetime.now(), "data": data}
        return data
        '''
    if old in c:
        c = c.replace(old, new); print("  ✅ M8: health_inspect enhanced")
    
    # System markers
    c = c.replace(f'system=False)\n    def check_status(', f'system=True, name="system_check_status")\n    def check_status(')
    c = c.replace(f'system=False)\n    def system_health(', f'system=True, name="system_health")\n    def system_health(')
    print("  ✅ M8: system_health/check_status → system=True")

    with open(sysf, "w", encoding="utf-8") as f:
        f.write(c)


# ═══════════════════════════════════════════════
# PART C: Server.py lazy-load (Phase 4.3)
# ═══════════════════════════════════════════════

def apply_lazy_load():
    sp = os.path.join(BASE, "server.py")
    with open(sp, "r", encoding="utf-8") as f:
        c = f.read()
    
    # Remove eager loading of reasoning, reflect_engine, agent_recommender
    c = c.replace("        self.reasoning = ReasoningEngine()\n", "")
    c = c.replace("        self.reflect_engine = ReflectEngine(_vault)\n", "")
    c = c.replace("        self.agent_recommender = AgentRecommender(_vault)\n", "")
    
    # Add lazy properties after __init__
    old = """        self.operator = None

        # 异步预热原料索引"""
    new = """        self.operator = None

        # ═══ 惰性加载属性 ═══
        self._reasoning = None
        self._reflect_engine = None
        self._agent_recommender = None

        # 异步预热原料索引"""
    
    # Add @property accessors before warmup threads
    old2 = """        # 异步预热原料索引（性能优化 v3）：后台线程提前构建 _ensure_raw_index，"""
    new2 = """    @property
    def reasoning(self):
        if self._reasoning is None:
            self._reasoning = ReasoningEngine()
        return self._reasoning

    @property
    def reflect_engine(self):
        if self._reflect_engine is None:
            self._reflect_engine = ReflectEngine(self.vault_path)
        return self._reflect_engine

    @property
    def agent_recommender(self):
        if self._agent_recommender is None:
            self._agent_recommender = AgentRecommender(self.vault_path)
        return self._agent_recommender

        # 异步预热原料索引（性能优化 v3）：后台线程提前构建 _ensure_raw_index，"""
    
    if old in c:
        c = c.replace(old, new)
    if old2 in c:
        c = c.replace(old2, new2)
    
    with open(sp, "w", encoding="utf-8") as f:
        f.write(c)
    print("  ✅ server.py: lazy-load engines")


# ═══════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════

if __name__ == "__main__":
    print("=== Phase 1: Decorators ===")
    apply_decorators()
    
    print("\n=== Phase 2: Merges ===")
    apply_merges()
    apply_m3_m4()
    apply_m8()
    
    print("\n=== Phase 4: Lazy Load ===")
    apply_lazy_load()
    
    print("\n=== Verify ===")
    import subprocess, sys
    r = subprocess.run([sys.executable, os.path.join(BASE, "router.py"), "--test"],
                       capture_output=True, text=True, timeout=30)
    first = r.stdout.strip().split('\n')[0] if r.stdout else "NO OUTPUT"
    count = len([l for l in r.stdout.split('\n') if l.strip() and not l.startswith('MCP')]) - 1
    print(f"  {first} ({count} tools)")
    if r.returncode != 0:
        print(f"  STDERR: {r.stderr[:500]}")
    
    print("\n✅ All applied. Restart MCP to activate.")
