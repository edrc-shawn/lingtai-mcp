# -*- coding: utf-8 -*-
"""记忆银行 mixin — mem_* 六件套（Phase 1+2: @tool装饰器 + M5+M6合并）"""
from decorators import tool
from logger import get_logger

log = get_logger(__name__)

class MemoryBankMixin:
    @tool(readonly=False, write=True, category="memory", system=False, name="memory_write")
    def mem_write(self, content: str, source: str = "mcp", tags: list = None, branch: str = "",
                   knowledge_candidate: bool = False, context: dict = None, why: str = "",
                   expected_consumer: str = "", expiry_policy: str = None) -> dict:
        """写入记忆到记忆银行（含6级信源分级+冲突检测+场景分支+上下文）

        context 可选，传 {"session_timestamp": "2023-05-06"} 等会话时间戳供日期归一化。
        context.intended_use 可选，记录「这条记忆预计在什么场景下被召回」，不参与检索。
        why 可选，记忆的「为什么记住」——记录原因/背景/意图，不参与检索打分。
        expected_consumer 可选，预期消费者名称（如 "reasonix" / "workbuddy"），
            消费端可据此过滤记忆噪音。

        写入时自动增强：
        - 日期归一化：若 context 含 session_timestamp，将相对日期转绝对日期追加到 content
        - 自动标签：从 content 提取关键词补全 tags（不覆盖手动传入的 tags）
        """
        from datetime import datetime

        # ── 写时日期归一化 ──
        ts = None
        if context and isinstance(context, dict):
            raw_ts = context.get("session_timestamp")
            if raw_ts:
                try:
                    ts = datetime.fromisoformat(raw_ts) if isinstance(raw_ts, str) else raw_ts
                except Exception:
                    log.debug("suppressed", exc_info=True)
        if ts:
            try:
                from memory_bank.date_normalizer import normalize_text
                normalized = normalize_text(content, ts)
                if normalized != content:
                    content = normalized
                    context["normalized_at_write"] = normalized
            except Exception:
                log.debug("suppressed", exc_info=True)

        # ── 自动标签补全 ──
        tags = list(tags or [])
        if content:
            import re as _re
            words = set(w.lower() for w in _re.findall(r'[a-zA-Z]{3,}', content)
                       if w.lower() not in {"the", "and", "for", "was", "has", "had", "not",
                                            "are", "but", "all", "any", "can", "its", "let",
                                            "how", "now", "our", "out", "get", "got", "say",
                                            "she", "her", "him", "his", "you", "did", "use",
                                            "way", "new", "old", "one", "two", "may", "see",
                                            "who", "why", "too", "put", "set", "yet", "own",
                                            "just", "like", "than", "that", "this", "what",
                                            "with", "will", "have", "been", "from", "been",
                                            "some", "them", "into", "been", "each", "also",
                                            "very", "much", "more", "most", "such", "when",
                                            "then", "there", "their", "would", "could",
                                            "should", "about", "which", "where", "after",
                                            "before", "between", "through", "during",
                                            "without", "because", "already", "always",
                                            "really", "actually", "basically", "essentially",
                                            "something", "everything", "nothing", "anything",
                                            "sometime", "sometimes", "somewhere", "everyone",
                                            "everybody", "nobody", "anybody", "whoever",
                                            "whatever", "whenever", "wherever", "however",
                                            "meanwhile", "moreover", "furthermore",
                                            "nevertheless", "notwithstanding", "therefore"})
            if words:
                existing_tags = set(t.lower() for t in tags)
                for w in sorted(words, key=len, reverse=True)[:8]:
                    if w not in existing_tags:
                        tags.append(w)

        return self.memory_bank.write(content, source, tags=tags, branch=branch,
                                      knowledge_candidate=knowledge_candidate, context=context, why=why,
                                      expected_consumer=expected_consumer, expiry_policy=expiry_policy)

    @tool(readonly=True, write=False, category="memory", system=False, name="memory_search")
    def mem_query(self, keyword: str = None, min_confidence: float = 0.0, branch: str = "",
                   include_archived: bool = False, include_pending: bool = True,
                   min_relevance: float = 0.0, normalize_dates: bool = False,
                   semantic: bool = True, consumer: str = "", top_k: int = 20) -> dict:
        """检索记忆银行中的教训/偏好/纠正/决策记录。
        场景：找"之前发生过什么""用户纠正过什么""上次怎么决定的""我的偏好是什么"时。
        区别：找知识概念用 knowledge_search；找会话级交互日志用 episodic_search；一次性注入全部记忆层用 lingshi_inject。

        include_pending=True（默认）→ 查全部（含 pending+active）
        include_pending=False → 仅查 active 记忆（原 MCP 默认行为）
        min_relevance > 0 时：返回结果只包含 relevance_score >= 阈值的项；
                             若无任何结果达阈值，返回 low_confidence: true + 空 results。
        normalize_dates=True 时：对含 context.session_timestamp 的记忆做日期归一化，
                                归一化结果存到 normalized_content 字段。
        semantic=True 时（默认）：在子串检索后追加一轮语义检索（bge-small-zh-v1.5 本地模型），
                         两路结果按权重合并（substring=0.3, semantic=0.7），
                         返回值中 semantic_results 字段列出语义命中的额外条目。
                         语义模型已在启动时后台预热（~30-40s），首调不需要额外等待。
        consumer 可选，按预期消费者过滤（如 "reasonix" / "workbuddy"），空=不过滤。
        """
        status = "" if include_pending else "active"
        results = self.memory_bank.query(keyword=keyword, min_confidence=min_confidence,
                                         branch=branch, include_archived=include_archived,
                                         status=status, consumer=consumer)

        # ── 语义检索（基于 bge-small-zh-v1.5 本地模型）──
        semantic_hits = []
        if semantic and keyword:
            try:
                from memory_bank.semantic_retriever import search, merge_results
                all_mems = self.memory_bank.query(keyword="", status="",
                                                  min_confidence=0.0, branch="",
                                                  include_archived=include_archived)
                semantic_hits = search(keyword, all_mems, top_k=top_k)
                if semantic_hits:
                    results = merge_results(results, semantic_hits, top_k=top_k)
            except Exception as e:
                log.warning("semantic fallback to substring-only", extra={"error": str(e)})

        stats = self.memory_bank.stats()
        low_confidence = False

        # 实体不匹配检测
        if keyword and results:
            import re
            _NON_ENTITY = {"i", "it", "its", "my", "me", "he", "she", "we", "you", "they",
                           "the", "this", "that", "these", "those", "what", "when", "where",
                           "who", "how", "why", "which", "both", "each", "every", "many",
                           "some", "any", "all", "no", "not", "also", "could", "would",
                           "should", "will", "can", "may", "might", "shall", "is", "am",
                           "are", "was", "were", "has", "have", "had", "did", "does", "do",
                           "after", "before", "during", "through", "about", "since", "until",
                           "because", "although", "please", "let", "tell", "ask", "see",
                           "say", "find", "make", "use", "give", "take", "put", "set"}
            entities = [w for w in re.findall(r"\b[A-Z][a-z]+\b", keyword)
                       if w.lower() not in _NON_ENTITY]
            if entities:
                all_content = " ".join(r.get("content", "") for r in results).lower()
                cleaned_entities = [e.lower().rstrip("'s") for e in entities]
                if not any(ce in all_content for ce in cleaned_entities):
                    low_confidence = True

        # 可答性门控
        if min_relevance > 0.0 and results:
            before = len(results)
            results = [r for r in results if r.get("relevance_score", 0) >= min_relevance]
            if not results and before > 0:
                low_confidence = True

        # 日期归一化
        if normalize_dates and results:
            from memory_bank.date_normalizer import normalize_text
            for r in results:
                ctx = r.get("context") or {}
                ts = ctx.get("session_timestamp")
                if ts:
                    try:
                        ref_dt = datetime.fromisoformat(ts) if isinstance(ts, str) else datetime.now()
                    except Exception:
                        ref_dt = datetime.now()
                    r["normalized_content"] = normalize_text(
                        (r.get("content") or ""), ref_dt
                    )

        from datetime import datetime
        if top_k > 0 and len(results) > top_k:
            results = results[:top_k]
        resp = {"results": results, "stats": stats}
        if low_confidence:
            resp["low_confidence"] = True
        if semantic_hits:
            resp["semantic_hits"] = len(semantic_hits)
        return resp

    @tool(readonly=True, write=False, category="memory", system=True, name="memory_stats")
    def mem_stats(self) -> dict:
        """记忆银行统计（含跨域生命周期 M5）"""
        stats = self.memory_bank.stats()
        try:
            stats["lifecycle"] = self.memory_bank.lifecycle_stats()
        except Exception:
            stats["lifecycle"] = {"error": "lifecycle_stats 不可用"}
        return stats

    @tool(readonly=True, write=True, category="memory", system=True, name="memory_decay")
    def mem_decay(self) -> dict:
        """执行衰减调度（完整管线：衰减 + pending 晋升 + 超时 pending 清理）"""
        from memory_bank.decay import DecayScheduler
        decay = DecayScheduler(self.memory_bank)
        result = decay.run()
        promoted = decay.run_pending_promotion()
        cleaned = decay.cleanup_stale_pending()
        return {
            "decayed": result.get("decayed", 0),
            "deprecated": result.get("deprecated", 0),
            "archived": result.get("archived", 0),
            "promoted": promoted.get("promoted", 0),
            "cleaned": cleaned.get("cleaned", 0),
            "details": result.get("details", []),
        }

    @tool(readonly=True, write=False, category="memory", system=True, name="memory_scan_conflicts")
    def mem_scan_conflicts(self) -> dict:
        """扫描记忆银行中的冲突"""
        return self.memory_bank.scan_conflicts()

    @tool(readonly=False, write=True, category="memory", system=False, name="memory_feedback")
    def mem_feedback(self, memory_id: str, action: str, target_branch: str = "通用", reason: str = "obsolete") -> dict:
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

    @tool(readonly=False, write=True, category="memory", system=False, name="memory_link")
    def mem_link(self, memory_id: str, knowledge_pages, mode: str = "add") -> dict:
        """建立记忆→知识的受控 wikilink 桥（单向，不污染知识图）。

        Args:
            memory_id: 记忆 ID
            knowledge_pages: 目标丹房页路径（str 或 list）
            mode: "add"（默认，去重追加）/ "replace"（替换）
        """
        return self.memory_bank.set_knowledge_link(memory_id, knowledge_pages, mode=mode)

    @tool(readonly=True, write=False, category="memory", system=False, name="memory_consolidate")
    def mem_consolidate(self, min_confidence: float = 0.7, max_results: int = 5) -> dict:
        """
        记忆→知识毕业建议（只读扫描，不改写任何数据）。
        扫描记忆银行中 ripe 的待毕业记忆，搜索知识库找出关联页面推荐连接。

        Args:
            min_confidence: 最低置信度（默认0.7）
            max_results: 最大返回条数（默认5）

        Returns:
            dict: {total_candidates, suggestions: [{memory_id, content, confidence,
                   suggested_pages: [{path, title, relevance}], status}]}
        """
        vault = self.vault_path
        candidates = []
        for m in self.memory_bank.memories:
            if m.status == "archived":
                continue
            if m.graduation_candidate and not m.knowledge_links:
                candidates.append(m)
            elif (m.current_confidence >= min_confidence and not m.knowledge_links
                  and m.status == "active"):
                candidates.append(m)

        candidates.sort(key=lambda x: x.current_confidence, reverse=True)
        candidates = candidates[:max_results]

        import os, json
        index_path = os.path.join(vault, "丹房", ".meta", "index.json")
        all_pages = []
        if os.path.exists(index_path):
            try:
                with open(index_path, "r", encoding="utf-8") as f:
                    idx = json.load(f)
                all_pages = idx.get("pages", [])
            except Exception:
                log.debug("suppressed", exc_info=True)

        suggestions = []
        for m in candidates:
            kw = m.content.lower()
            kw_words = [w for w in kw.split() if len(w) > 2]
            scored_pages = []
            for p in all_pages:
                title = (p.get("title") or "").lower()
                summary = (p.get("summary") or "").lower()
                tags = " ".join(p.get("tags") or []).lower()
                text = f"{title} {summary} {tags}"
                score = sum(1 for w in kw_words if w in text)
                if score > 0:
                    scored_pages.append({
                        "path": p.get("path", ""),
                        "title": p.get("title", ""),
                        "relevance": round(score / max(len(kw_words), 1), 2),
                    })
            scored_pages.sort(key=lambda x: x["relevance"], reverse=True)
            suggestions.append({
                "memory_id": m.id,
                "content": m.content[:200],
                "confidence": round(m.current_confidence, 2),
                "tags": m.tags[:5],
                "suggested_pages": scored_pages[:5],
                "status": "candidate" if m.graduation_candidate else "high_confidence",
            })

        return {
            "total_candidates": len(self.memory_bank.memories),
            "ripe_count": len(candidates),
            "graduated_count": sum(1 for m in self.memory_bank.memories
                                   if m.graduation_candidate and m.knowledge_links),
            "pending_candidates": sum(1 for m in self.memory_bank.memories
                                      if m.graduation_candidate and not m.knowledge_links),
            "suggestions": suggestions,
            "action_hint": "确认后调用 memory_link(memory_id, knowledge_pages=[...]) 建立链接",
        }

    @tool(readonly=True, write=False, category="memory", system=False, name="memory_project_snapshot")
    def mem_project_snapshot(self, project: str, consumer: str = "") -> dict:
        """项目级上下文快照——聚合指定项目的所有相关记忆，一步拿到项目全景。

        Args:
            project: 项目名称（匹配记忆标签或内容）
            consumer: 可选，只返回指定消费者的记忆（如 "reasonix" / "workbuddy"）

        Returns:
            dict: {project, memories: [{id, content, confidence, tags, expected_consumer, ...}], stats}
        """
        tag = project.lower().replace(" ", "_")
        results = self.memory_bank.query(keyword=tag, status="", min_confidence=0.0,
                                         include_archived=False, consumer=consumer)
        # 同时搜索内容中包含项目名的记忆
        all_results = self.memory_bank.query(keyword=project, status="", min_confidence=0.0,
                                             include_archived=False, consumer=consumer)
        # 合并去重
        seen = set()
        merged = []
        for r in results + all_results:
            rid = r.get("id", "")
            if rid not in seen:
                seen.add(rid)
                merged.append(r)
        merged.sort(key=lambda x: x.get("current_confidence", 0), reverse=True)
        return {
            "project": project,
            "total": len(merged),
            "active_count": sum(1 for m in merged if m.get("status") == "active"),
            "pending_count": sum(1 for m in merged if m.get("status") == "pending"),
            "memories": [
                {
                    "id": m.get("id", ""),
                    "content": (m.get("content") or "")[:200],
                    "confidence": m.get("current_confidence", 0),
                    "tags": m.get("tags", [])[:8],
                    "status": m.get("status", ""),
                    "expected_consumer": m.get("expected_consumer", ""),
                    "relevance_score": m.get("relevance_score", 0),
                }
                for m in merged[:20]
            ],
            "action_hint": f"查看 {project} 项目全景。用 memory_search(consumer=...) 进一步过滤。",
        }

    @tool(readonly=False, write=True, category="memory", system=False, name="memory_snapshot")
    def mem_save_snapshot(self, title: str, key_conclusions: list = None,
                          rejected_directions: list = None, pending_questions: list = None,
                          recommended_next: str = "", source_skill: str = "",
                          extra_notes: str = "", slug: str = "") -> dict:
        """显式跨会话存档——把当前对话的关键结论、已否决方向、待解问题存为结构化快照。

        借鉴 dbskill 的 save 机制：不依赖 AI 自动记忆，而是用户主动触发存档。
        与 memory_write 的区别：memory_write 是隐式自动记录，memory_snapshot 是显式
        结构化快照，有明确的字段语义，适合跨会话接续。

        Args:
            title: 快照标题（如 "商业模式诊断 2026-07-22"）
            key_conclusions: 已确认的关键结论列表
            rejected_directions: 已否决的方向列表
            pending_questions: 待解决的问题列表
            recommended_next: 推荐下一步（一句话）
            source_skill: 来源 skill（如 "dbs-diagnosis" / "灵识对话"）
            extra_notes: 额外备注
            slug: 项目标识（同一 slug 的快照可形成时间线，同 slug 新快照覆盖旧快照）

        Returns:
            dict: {ok, memory_id, slug, saved_at}
        """
        from datetime import datetime

        saved_at = datetime.now().strftime("%Y-%m-%d %H:%M")
        slug = slug.strip() if slug else "通用"

        # 构建结构化内容
        parts = [f"# [快照] {title}"]
        parts.append(f"slug: {slug}")
        parts.append(f"时间: {saved_at}")
        if source_skill:
            parts.append(f"来源: {source_skill}")

        if key_conclusions:
            parts.append("\n## 关键结论")
            for i, c in enumerate(key_conclusions, 1):
                parts.append(f"{i}. {c}")

        if rejected_directions:
            parts.append("\n## 已否决方向")
            for i, d in enumerate(rejected_directions, 1):
                parts.append(f"{i}. {d}")

        if pending_questions:
            parts.append("\n## 待解决问题")
            for i, q in enumerate(pending_questions, 1):
                parts.append(f"{i}. {q}")

        if recommended_next:
            parts.append(f"\n## 推荐下一步\n{recommended_next}")

        if extra_notes:
            parts.append(f"\n## 备注\n{extra_notes}")

        content = "\n".join(parts)

        # 写入记忆银行，使用 snapshot 类型 + 高置信度
        tags = ["snapshot"]
        if slug and slug != "通用":
            tags.append(slug)

        result = self.memory_bank.write(
            content=content,
            source_type="user_directive",
            tags=tags,
            branch=slug,
            context={"session_timestamp": datetime.now().isoformat()},
            why=f"用户主动存档: {title}",
            expected_consumer="workbuddy",
        )

        return {
            "ok": result.get("success", False),
            "memory_id": result.get("id", ""),
            "slug": slug,
            "title": title,
            "saved_at": saved_at,
            "item_count": {
                "conclusions": len(key_conclusions or []),
                "rejected": len(rejected_directions or []),
                "pending": len(pending_questions or []),
            },
            "restore_hint": "调用 memory_restore(slug=...) 恢复此快照",
        }

    @tool(readonly=True, write=False, category="memory", system=False, name="memory_restore")
    def mem_restore_snapshot(self, slug: str = "", latest_only: bool = True) -> dict:
        """恢复最近的跨会话快照——读取 memory_snapshot 存档的结构化上下文。

        借鉴 dbskill 的 restore 机制：拉出最近一次存档，呈现关键结论和待解问题，
        让后续对话能接上。

        Args:
            slug: 项目标识（空=查全部快照，指定=只查该项目的快照）
            latest_only: 是否只返回最新一条（默认 True）

        Returns:
            dict: {found, slug, snapshots: [{memory_id, title, saved_at, parsed: {...}}]}
        """
        # 查询所有 snapshot 标记的记忆
        raw = self.memory_bank.query(
            keyword="[快照]",
            status="",
            min_confidence=0.0,
            include_archived=False,
        )

        # 过滤 + 排序
        snapshots = []
        for m in raw:
            tags = m.get("tags", [])
            if "snapshot" not in tags:
                continue
            mem_slug = m.get("branch_id") or m.get("branch", "")
            if slug and mem_slug != slug:
                continue
            snapshots.append(m)

        if not snapshots:
            return {
                "found": False,
                "slug": slug or "全部",
                "message": "没有找到存档快照。用 memory_snapshot 创建一个。",
                "snapshots": [],
            }

        # 按时间排序（最新在前）
        snapshots.sort(key=lambda x: x.get("created_at", ""), reverse=True)

        if latest_only:
            snapshots = snapshots[:1]

        parsed_snapshots = []
        for s in snapshots:
            content = s.get("content", "")
            parsed = self._parse_snapshot_content(content)
            parsed_snapshots.append({
                "memory_id": s.get("id", ""),
                "title": parsed.get("title", ""),
                "saved_at": parsed.get("saved_at", s.get("created_at", "")),
                "slug": s.get("branch_id", s.get("branch", "")),
                "confidence": s.get("current_confidence", 0),
                "parsed": parsed,
                "raw_content": content[:500],
            })

        return {
            "found": True,
            "slug": slug or "全部",
            "count": len(parsed_snapshots),
            "snapshots": parsed_snapshots,
            "action_hint": "将 snapshots[0].parsed 注入到 AI 上下文，即可接续上次对话。",
        }

    def _parse_snapshot_content(self, content: str) -> dict:
        """从快照 Markdown 中提取结构化字段"""
        import re
        result = {
            "title": "",
            "slug": "",
            "saved_at": "",
            "source": "",
            "key_conclusions": [],
            "rejected_directions": [],
            "pending_questions": [],
            "recommended_next": "",
            "extra_notes": "",
        }

        # 提取标题
        m = re.search(r'^#\s*\[快照\]\s*(.+?)$', content, re.MULTILINE)
        if m:
            result["title"] = m.group(1).strip()

        # 提取元数据
        for field in ["slug", "时间", "来源"]:
            key = {"时间": "saved_at", "来源": "source"}.get(field, field)
            m = re.search(rf'^{field}:\s*(.+?)$', content, re.MULTILINE)
            if m:
                result[key] = m.group(1).strip()

        # 提取列表段
        sections = {
            "关键结论": "key_conclusions",
            "已否决方向": "rejected_directions",
            "待解决问题": "pending_questions",
        }
        for section_name, field_name in sections.items():
            pattern = rf'##\s*{section_name}\s*\n((?:\d+\.\s*.+\n?)+)'
            m = re.search(pattern, content)
            if m:
                items = re.findall(r'\d+\.\s*(.+)$', m.group(1), re.MULTILINE)
                result[field_name] = [i.strip() for i in items]

        # 提取单行段
        for section_name, field_name in [("推荐下一步", "recommended_next"), ("备注", "extra_notes")]:
            pattern = rf'##\s*{section_name}\s*\n(.+?)(?:\n##|\n\Z|\Z)'
            m = re.search(pattern, content, re.DOTALL)
            if m:
                result[field_name] = m.group(1).strip()

        return result
