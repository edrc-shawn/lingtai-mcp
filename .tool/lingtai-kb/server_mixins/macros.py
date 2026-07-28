# -*- coding: utf-8 -*-
"""宏工具 mixin — 高频工具组合封装

基于 tool_sessions.jsonl 的数据分析（2026-07-11）：
- knowledge_inject + knowledge_search 是最高频共现对（5/41 sessions）
- knowledge_search → knowledge_search 链 24 次
- 收尾模式（session_end）基于 Hy3 痛点，非数据驱动

每个宏工具返回结构化步骤状态（Saga 模式），而非扁平布尔值。
"""

import json
import os
import time
import hashlib
import subprocess
from datetime import datetime
from memory_bank.episodic import EpisodicMemory
from decorators import tool
from logger import get_logger


log = get_logger(__name__)

class MacroMixin:
    """宏工具 mixin — 封装高频原子工具组合"""

    @property
    def _macro_tracker(self):
        if not hasattr(self, '_macro_tracker_inst'):
            from server_mixins.macro_tracker import MacroUsageTracker
            vault = getattr(self, 'vault_path', None) or r"."
            self._macro_tracker_inst = MacroUsageTracker(vault)
        return self._macro_tracker_inst

    # ─── knowledge_recall: 知识检索宏 ───

    @tool(readonly=True, write=False, category="knowledge", system=True, name="knowledge_recall")
    def knowledge_recall(self, keyword: str, hops: int = 2, max_tokens: int = 800) -> dict:
        """知识检索宏：inject + search 一步完成

        数据支撑：knowledge_inject → knowledge_search 是最高频共现模式（5/41 sessions）。
        knowledge_search 是单工具频次冠军（13/41 sessions）。

        Args:
            keyword: 搜索关键词
            hops: 图扩散跳数（默认 2，传给 knowledge_search）
            max_tokens: inject 最大 token 数（默认 800）

        Returns:
            dict: 结构化步骤状态
        """
        macro_id = self._macro_id("recall", keyword)
        steps = []
        has_error = False

        # 步骤 1: knowledge_inject
        try:
            inject_result = self.inject(keyword=keyword, max_tokens=max_tokens)
            found = inject_result.get("found", False) if isinstance(inject_result, dict) else False
            steps.append({
                "step": "inject",
                "status": "ok",
                "found": found,
                "summary": f"inject({keyword}): {'命中' if found else '未命中'}",
            })
        except Exception as e:
            steps.append({"step": "inject", "status": "error", "error": str(e), "retryable": True})
            has_error = True

        # 步骤 2: knowledge_search（语义搜索 + 图扩散）
        try:
            search_result = self.query(keyword=keyword, hops=hops, category="")
            result_count = 0
            if isinstance(search_result, dict):
                results = search_result.get("results", search_result.get("pages", []))
                result_count = len(results) if isinstance(results, list) else 0
            elif isinstance(search_result, list):
                result_count = len(search_result)
            steps.append({
                "step": "search",
                "status": "ok",
                "result_count": result_count,
                "summary": f"search({keyword}, hops={hops}): {result_count} 条结果",
            })
        except Exception as e:
            steps.append({"step": "search", "status": "error", "error": str(e), "retryable": True})
            has_error = True

        # 构建返回
        overall = "error" if has_error else "ok"
        combined = {}
        if isinstance(inject_result, dict):
            combined["inject"] = inject_result
        if isinstance(search_result, dict):
            combined["search"] = search_result

        result = {
            "macro": "knowledge_recall",
            "macro_id": macro_id,
            "overall": overall,
            "steps": steps,
            "keyword": keyword,
            "combined": combined,
        }
        # 记录使用率
        try:
            client = getattr(self, 'client', '')
            self._macro_tracker.record_macro("knowledge_recall", steps, client=client)
        except Exception:
            log.debug("suppressed", exc_info=True)
        return result

    # ─── refine_quick: 快速提炼宏 ───

    @tool(readonly=False, write=True, category="refine", system=False)
    def refine_quick(
        self,
        raw_paths: list,
        content: str,
        title: str = None,
        target_domain: str = None,
        grade: str = "正常",
        force: bool = True,
        tags: list = None,
    ) -> dict:
        """快速提炼宏：预检→写页→标记→建链 一步完成

        把提炼流程从 7 步压缩到 2 步（AI 只需读原文+写正文）。

        Args:
            raw_paths: 原料路径列表（单篇或多篇同主题）
            content: 丹房页正文（Markdown）
            title: 丹房页标题（默认从 raw_derive 预检提取）
            target_domain: 目标域（默认从 raw_derive 建议）
            grade: 提炼分级，默认"正常"
            force: 跳过去重预检，默认 True
            tags: 自定义标签列表（如 ["概念", "痕迹", "虚壳"]），
                  不传则自动从域+标题推断

        Returns:
            dict: 结构化步骤状态
        """
        import os
        from raw_derive import derive_raw_candidate

        macro_id = self._macro_id("refine_quick", str(raw_paths))
        steps = []
        has_error = False
        created_path = None

        vault = getattr(self, 'vault_path', None) or os.environ.get("LINGTAI_VAULT", r".")
        prechecks = []

        # ── 步骤 1: raw_derive 预检 ──
        try:
            for rp in raw_paths:
                full_path = os.path.join(vault, rp) if not os.path.isabs(rp) else rp
                info = derive_raw_candidate(full_path, vault_root=vault)
                prechecks.append(info)

            # 从第一条原料推断域和标题
            if not target_domain:
                target_domain = prechecks[0].get('suggested_domain') or '07-工具与AI'
            if not title:
                title = prechecks[0].get('title', '未命名')
                # 多篇合并时追加"全景"后缀
                if len(raw_paths) > 1:
                    title = title.rstrip('：:') + '全景'

            # 待补充警告
            grade_warnings = []
            for p in prechecks:
                g = p.get('grade_suggestion', '')
                if g == '待补充':
                    r = p.get('grade_reason', '信息不足')
                    grade_warnings.append(f"「{p.get('title', '')}」→ {r}")

            steps.append({
                "step": "precheck",
                "status": "ok",
                "count": len(prechecks),
                "domain": target_domain,
                "title": title,
                "grades": [p.get('grade_suggestion', grade) for p in prechecks],
                "summary": f"预检 {len(prechecks)} 篇原料 → {target_domain} / {title}",
                "grade_warnings": grade_warnings if grade_warnings else None,
            })
        except Exception as e:
            steps.append({"step": "precheck", "status": "error", "error": str(e), "retryable": True})
            has_error = True

        # 快速路径开关：从 prechecks 或 grade 参数推断
        is_fast = (grade == '快速') or any(p.get('grade_suggestion') == '快速' for p in prechecks if prechecks)

        # ── 步骤 1.5: 证据契约 dedup 预检（force=False 时启用）──
        if not has_error and not force:
            try:
                from server_mixins.knowledge import _classify_evidence, _determine_create_safety
                ks = self.query(keyword=title, hops=1)
                direct_results = ks.get("results", [])
                if direct_results:
                    # 检查最高排名的结果
                    top = direct_results[0]
                    cs = top.get("create_safety", "unknown")
                    if cs == "exists":
                        steps.append({
                            "step": "dedup_check",
                            "status": "blocked",
                            "path": top.get("path", ""),
                            "create_safety": cs,
                            "summary": f"知识库已有同主题页: {top.get('path', '')}（create_safety=exists），拒绝创建",
                        })
                        has_error = True
                    elif cs == "probable":
                        steps.append({
                            "step": "dedup_check",
                            "status": "warning",
                            "path": top.get("path", ""),
                            "create_safety": cs,
                            "summary": f"存在可能重复的页面: {top.get('path', '')}（create_safety=probable），将继续创建但建议检查",
                        })
                    else:
                        steps.append({
                            "step": "dedup_check",
                            "status": "ok",
                            "create_safety": cs,
                            "summary": "无重复页面（create_safety=unknown）",
                        })
                else:
                    steps.append({
                        "step": "dedup_check",
                        "status": "ok",
                        "summary": "无检索结果，自由创建",
                    })
            except Exception as e:
                # dedup 预检失败不阻断创建流程
                steps.append({
                    "step": "dedup_check",
                    "status": "warning",
                    "error": str(e),
                    "summary": f"dedup 预检失败(非致命): {e}",
                })

        # ── 步骤 2: page_create ──
        if not has_error:
            try:
                # 域→主标签映射，替代硬编码的 ["提炼"]
                _DOMAIN_MAIN_TAG = {
                    "00-思考与认知": "概念", "01-内容创作": "方法",
                    "02-成长与日常": "方法", "03-社会观察": "痛点",
                    "04-身体与健康": "方法", "05-哲学与思想": "概念",
                    "06-商业与投资": "方法", "07-工具与AI": "工具",
                    "08-教育": "方法", "99-一人公司": "方法",
                }
                main_tag = _DOMAIN_MAIN_TAG.get(target_domain, "概念")
                # 自定义标签优先，否则从域+标题推断
                if tags:
                    _refined_tags = tags
                else:
                    import re as _re
                    _title_parts = _re.split(r'[：:、，,（）()（）]', title)
                    _sub_tag = _title_parts[0].strip() if _title_parts and len(_title_parts[0].strip()) <= 8 else ''
                    _refined_tags = [main_tag] + ([_sub_tag] if _sub_tag and _sub_tag != main_tag else [])
                if is_fast:
                    # 快速路径：stub 页，不写长正文
                    excerpt = content[:200] if content else '待补充'
                    fast_content = f"> 快速提炼：来源 [[{raw_paths[0]}]]\n\n## 摘要\n\n{excerpt}"
                    create_result = self.create_page(
                        title=title,
                        content=fast_content,
                        domain=target_domain,
                        tags=_refined_tags,
                    )
                else:
                    create_result = self.create_page(
                        title=title,
                        content=content,
                        domain=target_domain,
                        tags=_refined_tags,
                    )
                created_path = create_result.get("path", "") if isinstance(create_result, dict) else ""
                steps.append({
                    "step": "page_create",
                    "status": "ok",
                    "path": created_path,
                    "summary": f"创建丹房页: {created_path}",
                })
            except Exception as e:
                created_path = created_path or getattr(e, 'created_path', '')
                steps.append({"step": "page_create", "status": "error", "error": str(e),
                              "note": "页文件已落盘（写入优先于索引），数据安全不受影响" if created_path else "",
                              "retryable": False})
                has_error = True

        # ── 步骤 3: refine_mark（逐条标记原料） ──
        if not has_error and created_path:
            for i, rp in enumerate(raw_paths):
                try:
                    summary_text = prechecks[i].get('excerpt', '')[:80] if prechecks else ''
                    grade_val = prechecks[i].get('grade_suggestion', grade) if prechecks else grade
                    mark_result = self.refine_mark(
                        raw_path=rp,
                        target=created_path,
                        summary=summary_text or f"提炼→{created_path}",
                        grade=grade_val,
                        force=force,
                    )
                    mark_ok = mark_result.get("success", False) if isinstance(mark_result, dict) else False
                    steps.append({
                        "step": f"refine_mark[{i}]",
                        "status": "ok" if mark_ok else "warn",
                        "raw": rp,
                        "summary": f"标记 {rp}: {'✅' if mark_ok else '⚠️ ' + str(mark_result)}",
                    })
                except Exception as e:
                    steps.append({"step": f"refine_mark[{i}]", "status": "error", "error": str(e), "retryable": True})
                    has_error = True

        # ── 步骤 4: link_suggest → add_link + 原料回链（快速路径跳过）──
        if not has_error and created_path and not is_fast:
            try:
                suggest_result = self.link_suggest(page_path=created_path, max_results=5)
                suggested = []
                if isinstance(suggest_result, dict):
                    links = suggest_result.get("suggestions", suggest_result.get("links", []))
                    for link in links[:3]:
                        target = link if isinstance(link, str) else link.get("path", "")
                        label = link.get("label", "") if isinstance(link, dict) else ""
                        if target:
                            self.add_link(source=created_path, target=target, label=label)
                            suggested.append(target)

                # 原料回链：从 raw_derive 预检结果中提取回链目标
                for p in prechecks:
                    for bl in p.get('backlinks', []):
                        bl_target = bl.replace('[[', '').replace(']]', '').strip()
                        if bl_target and bl_target != title:
                            self.add_link(source=created_path, target=bl_target)
                            suggested.append(bl_target)

                steps.append({
                    "step": "link",
                    "status": "ok",
                    "count": len(suggested),
                    "targets": list(set(suggested)),
                    "summary": f"自动建链 {len(set(suggested))} 条: {list(set(suggested))}",
                })
            except Exception as e:
                steps.append({"step": "link", "status": "warn", "error": str(e), "summary": "建链跳过（非致命）"})

        # ── 步骤 5: action_hint（行动索引建议——方向一落地）──
        # 提炼完成后检测原料堆积，主动建议"干中学"方向
        action_hint = None
        if not has_error and created_path:
            try:
                status = self.refine_status(mode="all")
                pending = status.get("pending", 0)
                if pending >= 5:
                    _DOMAIN_ACTION = {
                        "00-思考与认知": "认知框架文章或深度思考",
                        "01-内容创作": "选题池或作品产出",
                        "02-成长与日常": "行动清单或反思日记",
                        "03-社会观察": "评论文章或社会分析",
                        "04-身体与健康": "健康计划或行动项",
                        "05-哲学与思想": "哲学思辨或思想笔记",
                        "06-商业与投资": "商业模式分析或投资决策",
                        "07-工具与AI": "工具改进或方法论总结",
                        "08-教育": "教育方法或课程设计",
                        "99-一人公司": "业务决策或流程优化",
                    }
                    action_scene = _DOMAIN_ACTION.get(target_domain, "行动项")
                    action_hint = {
                        "pending_total": pending,
                        "domain": target_domain,
                        "suggestion": f"知识库还有 {pending} 篇待提炼原料。刚提炼的「{target_domain}」域知识可考虑转化成{action_scene}——干中学比囤着更有效。",
                    }
                    steps.append({
                        "step": "action_hint",
                        "status": "ok",
                        "summary": action_hint["suggestion"],
                    })
                else:
                    steps.append({
                        "step": "action_hint",
                        "status": "ok",
                        "summary": f"待提炼原料 {pending} 篇，库存健康",
                    })
            except Exception as e:
                steps.append({"step": "action_hint", "status": "warn", "error": str(e), "summary": "行动建议跳过（非致命）"})

        overall = "error" if has_error else "ok"
        result = {
            "macro": "refine_quick",
            "macro_id": macro_id,
            "overall": overall,
            "steps": steps,
            "created_path": created_path,
            "domain": target_domain,
            "title": title,
            "raw_count": len(raw_paths),
            "action_hint": action_hint,
        }
        try:
            client = getattr(self, 'client', '')
            self._macro_tracker.record_macro("refine_quick", steps, client=client)
        except Exception:
            log.debug("suppressed", exc_info=True)
        return result

    # ─── topic_match: 热点匹配工具 ───

    @tool(readonly=True, write=False, category="macro", system=False)
    def topic_match(self, keyword: str, max_results: int = 5) -> dict:
        """热点匹配——外部热点与丹房已有知识的关联分析

        AI 写作或选题前调用，判断丹房是否有相关储备。

        Args:
            keyword: 热点关键词（如 "AI编程 抖音 Web Coding"）
            max_results: 最大返回数

        Returns:
            dict: {hotspot, coverage, matched_pages, matched_raw, existing_topic, suggestion}
        """
        import os, re, glob

        vault = getattr(self, 'vault_path', None) or os.environ.get("LINGTAI_VAULT", r".")
        words = [w for w in re.split(r'[\s,，、]+', keyword) if len(w) >= 2]

        # ── 1. 丹房检索（用已有 query 引擎） ──
        danfang_matches = []
        try:
            # hops=0 精确匹配，不走图扩散
            qr = self.query(keyword=keyword, hops=0)
            results = qr.get("results", []) if isinstance(qr, dict) else (qr if isinstance(qr, list) else [])
            domain_counts = {}
            for r in results:
                title = r.get("title", "")
                path = r.get("path", "")
                domain = r.get("domain", "")
                summary = r.get("summary", "")[:80]
                domain_counts[domain] = domain_counts.get(domain, 0) + 1
                danfang_matches.append({
                    "path": path, "title": title, "domain": domain,
                    "summary": summary, "type": "丹房页",
                })
            # 补充：域名匹配（如 "一人公司" 未命中标题但域名匹配）
            for domain in domain_counts:
                if any(d in domain or domain in d for d in words):
                    # 域名已命中，不用额外标记
                    pass
        except Exception:
            log.debug("suppressed", exc_info=True)

        # ── 2. 原料检索（关键词匹配文件名+frontmatter） ──
        raw_matches = []
        try:
            raw_dir = os.path.join(vault, "原料")
            for f in sorted(glob.glob(os.path.join(raw_dir, "**", "*.md"), recursive=True), key=os.path.getmtime, reverse=True):
                name = os.path.basename(f).lower()
                score = sum(1 for w in words if w.lower() in name)
                if score >= 1:
                    # 检查是否已处理（已提炼/已跳过等终态，兼容双重 frontmatter）
                    try:
                        head = open(f, 'r', encoding='utf-8', errors='ignore').read(4096)
                        is_refined = any(
                            s in head for s in ('已提炼', '已跳过', 'processed', 'done', 'refined', 'skipped', 'ignored', '放弃', '废弃', 'duplicate')
                        )
                    except Exception:
                        is_refined = False
                    raw_matches.append({
                        "name": os.path.basename(f),
                        "score": score,
                        "refined": is_refined,
                        "type": "原料",
                    })
            raw_matches.sort(key=lambda x: -x["score"])
        except Exception:
            log.debug("suppressed", exc_info=True)

        # ── 3. 选题池匹配 ──
        existing_topic = None
        try:
            topic_path = os.path.join(vault, "作品", "选题池.md")
            if os.path.isfile(topic_path):
                text = open(topic_path, 'r', encoding='utf-8', errors='ignore').read()
                for line in text.split('\n'):
                    if '|' in line and re.match(r'\|\s*\d+', line):
                        title = line.split('|')[2].strip() if len(line.split('|')) > 2 else ''
                        if title and any(w.lower() in title.lower() for w in words):
                            existing_topic = title
                            break
        except Exception:
            log.debug("suppressed", exc_info=True)

        # ── 4. 覆盖度判断 ──
        has_danfang = len(danfang_matches) >= 1
        has_raw = len(raw_matches) >= 1
        has_refined_raw = any(r.get("refined") for r in raw_matches)

        if has_danfang:
            coverage = "已覆盖"
            suggestion = "丹房已有相关页，可直接参考已有知识进行写作。"
        elif has_refined_raw:
            coverage = "部分覆盖"
            suggestion = "原料已提炼为丹房页，但关键词匹配未命中。建议 refined_quick 补充此方向的丹房页。"
        elif has_raw:
            coverage = "仅原料"
            suggestion = "有原料未提炼，可 refine_quick 快速提炼。"
        else:
            coverage = "未覆盖"
            suggestion = "丹房尚无相关储备，如需写作需从头调研。"

        return {
            "hotspot": keyword,
            "coverage": coverage,
            "matched_pages": danfang_matches[:max_results],
            "matched_raw": raw_matches[:max_results],
            "existing_topic": existing_topic,
            "suggestion": suggestion,
        }


    # ─── health_check: 健康检查宏 ───

    @tool(readonly=True, write=False, category="macro", system=False)
    def health_check(
        self,
        scope: str = "full",
        stale_days: int = 30,
        min_backlinks: int = 3,
        min_similarity: float = 0.6,
        max_similarity: float = 0.75,
    ) -> dict:
        """健康检查宏：聚合 4 个子体检一步完成

        把「先 health_inspect、再 knowledge_gaps、再 heatmap、再 lifecycle_scan、再 reflect」
        的 5 次独立调用压缩为 1 次原子宏调用。

        Args:
            scope: "full"（全量，默认） / "quick"（仅聚合近期扫描结果）
            stale_days: 陈旧阈值
            min_backlinks: 最低入链数
            min_similarity: 关联检测最低相似度
            max_similarity: 关联检测最高相似度

        Returns:
            dict: 聚合健康报告
        """
        macro_id = self._macro_id("health_check", str(scope))
        steps = []
        has_error = False

        # 步骤 1: knowledge_gaps
        try:
            gaps_result = self.gaps()
            pending = gaps_result.get("pending", 0) if isinstance(gaps_result, dict) else 0
            steps.append({"step": "knowledge_gaps", "status": "ok",
                          "pending_raw": pending,
                          "summary": f"待提炼原料: {pending} 篇"})
        except Exception as e:
            steps.append({"step": "knowledge_gaps", "status": "error", "error": str(e), "retryable": True})
            has_error = True

        # 步骤 2: knowledge_heatmap
        try:
            heatmap_result = self.heatmap(top_n=10)
            top_pages = heatmap_result.get("pages", [])[:5] if isinstance(heatmap_result, dict) else []
            steps.append({"step": "knowledge_heatmap", "status": "ok",
                          "top_hot_pages": [p.get("title", "") for p in top_pages],
                          "summary": f"热度扫描完成: Top {min(5, len(top_pages))} 活跃页"})
        except Exception as e:
            steps.append({"step": "knowledge_heatmap", "status": "warn", "error": str(e),
                          "summary": "热度扫描失败（非致命）"})

        # 步骤 3: lifecycle_scan
        try:
            lifecycle_result = self.lifecycle_scan(stale_days=stale_days, min_backlinks=min_backlinks, mode="both")
            pc = lifecycle_result.get("candidates", lifecycle_result.get("page_scan", {}).get("candidates", [])) if isinstance(lifecycle_result, dict) else []
            rc = lifecycle_result.get("raw_scan", {}).get("cold_raw_candidates", lifecycle_result.get("raw_coldness", [])) if isinstance(lifecycle_result, dict) else []
            steps.append({"step": "lifecycle_scan", "status": "ok",
                          "page_candidates": len(pc), "raw_candidates": len(rc),
                          "summary": f"生命周期扫描: {len(pc)} 可降级页, {len(rc)} 冷原料"})
        except Exception as e:
            steps.append({"step": "lifecycle_scan", "status": "warn", "error": str(e),
                          "summary": "生命周期扫描跳过（非致命）"})

        # 步骤 4: concept_collide
        try:
            cc_result = self.concept_collide(mode="page", top_n=5, min_similarity=min_similarity, max_similarity=max_similarity)
            collisions = cc_result.get("collisions", []) if isinstance(cc_result, dict) else []
            top_hits = [f"{c.get('domain_a','?')}×{c.get('domain_b','?')} ({c.get('similarity',0):.3f})" for c in collisions[:3]]
            steps.append({"step": "concept_collide", "status": "ok",
                          "collisions": len(collisions), "top_hits": top_hits,
                          "summary": f"概念碰撞: {len(collisions)} 对跨域关联" if collisions else "概念碰撞: 无高价值跨域关联"})
        except Exception as e:
            steps.append({"step": "concept_collide", "status": "skipped",
                          "summary": f"概念碰撞暂不可用: {str(e)[:60]}"})

        # 步骤 5: observation_reflect
        try:
            reflect_result = self.reflect()
            findings = sum(len(v) for v in reflect_result.values() if isinstance(v, list)) if isinstance(reflect_result, dict) else 0
            steps.append({"step": "observation_reflect", "status": "ok",
                          "findings": findings,
                          "summary": f"全量反思: 发现 {findings} 个待关注项"})
        except Exception as e:
            steps.append({"step": "observation_reflect", "status": "warn", "error": str(e),
                          "summary": "反思失败（非致命）"})

        overall = "error" if has_error else "ok"
        result = {"macro": "health_check", "macro_id": macro_id, "overall": overall,
                  "scope": scope, "steps": steps,
                  "summary": f"健康检查完成：{sum(1 for s in steps if s['status'] == 'ok')} ok / "
                             f"{sum(1 for s in steps if 'warn' in s['status'] or 'skipped' in s['status'])} warn / "
                             f"{sum(1 for s in steps if s['status'] == 'error')} error"}
        try:
            self._macro_tracker.record_macro("health_check", steps, client=getattr(self, 'client', ''))
        except Exception:
            log.debug("suppressed", exc_info=True)
        return result
    # ─── session_end: 会话收尾宏 ───

    @tool(readonly=False, write=True, category="macro", system=False)
    def session_end(
        self,
        feedback_what: str = "",
        feedback_correction: str = "",
        user_push_key: str = "",
        user_push_value: str = "",
        work_imprint: str = "",
        profile_candidate: str = "",
        session_start: str = "",
        evidence_flags: str = "",
    ) -> dict:
        """会话收尾宏：一次性跑完灵识批量处理五步

        数据支撑：低频但高痛点的操作（Hy3 "结束"问题）。
        覆盖 AGENTS.md §5.8 规则⑥⑨⑬ 的收尾流程。

        Args:
            feedback_what: 用户纠正/确认的内容（传空则跳过）
            feedback_correction: 纠正方向（留空表示确认）
            user_push_key: 偏好推送键（传空则跳过）
            user_push_value: 偏好推送值
            work_imprint: 工作印记内容（传空则自动生成结构化格式）
            profile_candidate: 画像候选内容（传空则跳过）
            session_start: 会话开始时间（ISO 格式，如 "2026-07-20T15:35"，传空则只记录结束时间）
            evidence_flags: 画像证据条目 JSON 字符串（格式：[{"entry":"条目名","flag":"implicit_confirm","reason":"理由"}]，传空则跳过）

        Returns:
            dict: 结构化步骤状态
        """
        macro_id = self._macro_id("session_end", str(time.time()))
        steps = []
        has_error = False

        # 步骤 1: user_feedback（纠正/确认）
        if feedback_what:
            try:
                fb_result = self.user_feedback(what=feedback_what, correction=feedback_correction)
                steps.append({
                    "step": "user_feedback",
                    "status": "ok",
                    "type": fb_result.get("type", "unknown"),
                    "summary": f"反馈: {feedback_what}",
                })
            except Exception as e:
                steps.append({"step": "user_feedback", "status": "error", "error": str(e), "retryable": True})
                has_error = True
        else:
            steps.append({"step": "user_feedback", "status": "skipped", "summary": "无反馈内容"})

        # 步骤 2: user_push（偏好同步）
        if user_push_key and user_push_value:
            try:
                push_result = self.memory_push(key=user_push_key, value=user_push_value)
                action = push_result.get("action", "unknown") if isinstance(push_result, dict) else "ok"
                steps.append({
                    "step": "user_push",
                    "status": "ok",
                    "action": action,
                    "summary": f"偏好: {user_push_key}={user_push_value}",
                })
            except Exception as e:
                steps.append({"step": "user_push", "status": "error", "error": str(e), "retryable": True})
                has_error = True
        else:
            steps.append({"step": "user_push", "status": "skipped", "summary": "无偏好推送"})

        # 步骤 3: raw_save（画像候选存储）
        if profile_candidate:
            try:
                save_result = self.save(content=profile_candidate, category="原料", source="对话")
                saved = save_result.get("success", False) if isinstance(save_result, dict) else False
                dup = save_result.get("dup", False) if isinstance(save_result, dict) else False
                steps.append({
                    "step": "raw_save",
                    "status": "ok",
                    "saved": saved,
                    "dup": dup,
                    "summary": f"画像候选: {'已保存' if saved else '重复' if dup else '失败'}",
                })
            except Exception as e:
                steps.append({"step": "raw_save", "status": "error", "error": str(e), "retryable": True})
                has_error = True
        else:
            steps.append({"step": "raw_save", "status": "skipped", "summary": "无画像候选"})

        # 步骤 4: memory_write（工作印记）
        _step_timing = {"_start": time.time()}
        now = datetime.now()
        imprint_content = work_imprint
        if not imprint_content:
            # 自动生成结构化工作印记
            end_str = now.strftime('%H:%M')
            start_str = session_start[11:16] if len(session_start) >= 16 else session_start if session_start else end_str
            date_str = now.strftime('%Y-%m-%d')
            lines = [f"工作印记：{date_str} {end_str}"]
            lines.append(f"  时间：{start_str}-{end_str}")
            if feedback_what:
                direction = "（确认）" if not feedback_correction else f"（纠正：{feedback_correction}）"
                lines.append(f"  决策：{feedback_what}{direction}")
            if user_push_key and user_push_value:
                lines.append(f"  偏好：{user_push_key}={user_push_value}")
            # 自动扫本次会话的 git commit，嵌
            # ═══ P3: 扫描本轮写操作，嵌入工作印记 ═══
            _write_tools = frozenset({
                'page_create', 'page_update', 'page_append_section',
                'raw_save', 'refine_mark', 'memory_write', 'knowledge_inject',
            })
            _session_records = []
            try:
                from server_mixins.stub_manager import stub_tool_sessions
                records = stub_tool_sessions.read_tail(vault, n=200)
                for rec in records:
                    ts = rec.get("timestamp", "")
                    if session_start and ts < session_start:
                        continue
                    for tc in rec.get("tool_calls", []):
                        tname = tc.get("name", "")
                        if tname in _write_tools:
                            _session_records.append(f"  {tname}: {tc.get('args', {})}")
                if _session_records:
                    lines.append("  本轮写操作：")
                    for r_line in _session_records[:15]:
                        lines.append(r_line[:120])
            except Exception:
                log.debug("suppressed", exc_info=True)
            if session_start:
                try:
                    vault = getattr(self, 'vault_path', None) or r"."
                    # 先检查 vault 自身是否为 git 仓库（独立仓库），再检查父目录（旧指针布局）
                    if os.path.isfile(os.path.join(vault, '.git', 'HEAD')):
                        repo = vault
                    elif os.path.isfile(os.path.join(os.path.dirname(vault), '.git', 'HEAD')):
                        repo = os.path.dirname(vault)
                    else:
                        repo = vault
                    result = subprocess.run(
                        ['git', 'log', '--oneline', f'--after={session_start}'],
                        capture_output=True, text=True, timeout=15, cwd=repo
                    )
                    if result.returncode == 0 and result.stdout.strip():
                        commits = [l.strip() for l in result.stdout.strip().split('\n') if l.strip()]
                        for c in commits[:5]:  # 最多 5 条
                            lines.append(f"  {c}")
                except Exception:
                    log.debug("suppressed", exc_info=True)
            imprint_content = "\n".join(lines)
        _step_timing["mem_write_start"] = time.time()
        try:
            mem_result = self.mem_write(
                content=imprint_content,
                tags=["协作者-工作印记", "session_end"],
                branch="通用",
                # 修复根因：原 session_scope(0.9/天) 使工作印记约 1 天即被归档，
                # 导致 grill-mode/贾维斯人设/RESOLVER 等真架构决策整批丢失。
                # 改为 behavior_pattern(0.005/天) 长衰减，留足毕业管道沉淀窗口。
                expiry_policy="behavior_pattern",
            )
            _step_timing["mem_write_ms"] = round((time.time() - _step_timing["mem_write_start"]) * 1000)
            steps.append({
                "step": "memory_write",
                "status": "ok",
                "summary": f"工作印记已写入: {imprint_content[:60]}",
            })
        except Exception as e:
            steps.append({"step": "memory_write", "status": "error", "error": str(e), "retryable": True})
            has_error = True

        # 步骤 5: L1 情景记忆（每次 session_end 自动写入）
        try:
            vault = getattr(self, 'vault_path', None) or r"."
            _step_timing["episodic_start"] = time.time()
            l1 = EpisodicMemory(vault)
            # 从工作印记内容推导 summary 和 decisions
            imprint_lines = [l.strip() for l in imprint_content.split(':') if l.strip()]
            summary = imprint_content[:200] if len(imprint_content) > 10 else f"会话收尾 at {datetime.now().strftime('%m-%d %H:%M')}"
            decisions = []
            if feedback_what:
                decisions.append(feedback_what)
            _client = getattr(self, 'client', 'unknown')
            l1_result = l1.record(
                summary=summary,
                outcome="productive" if not has_error else "inconclusive",
                events=[
                    {"t": datetime.now().strftime('%H:%M'), "type": "session_end", "content": summary[:100]},
                ],
                decisions=decisions,
                tags=["session_end"],
                client=_client,
            )
            _step_timing["episodic_ms"] = round((time.time() - _step_timing["episodic_start"]) * 1000)
            if l1_result.get("stored"):
                steps.append({"step": "episodic_memory", "status": "ok", "summary": f"L1 情景记忆已写入: {l1_result['session_id']}"})
            else:
                steps.append({"step": "episodic_memory", "status": "warning", "summary": f"L1 写入失败: {l1_result.get('error', 'unknown')}"})
        except Exception as e:
            steps.append({"step": "episodic_memory", "status": "skipped", "summary": f"L1 暂不可用: {str(e)[:60]}"})

        # 步骤 6: concept_collide（跨域概念碰撞——收尾前主动发现意外关联）
        _step_timing["concept_collide"] = round((time.time() - _step_timing["_start"]) * 1000)
        _step_timing["_cc_start"] = time.time()
        try:
            cc_result = self.concept_collide(mode="page", top_n=5, min_similarity=0.65, max_similarity=0.75)
            _step_timing["concept_collide_inner"] = round((time.time() - _step_timing["_cc_start"]) * 1000)
            collisions = cc_result.get("collisions", []) if isinstance(cc_result, dict) else []
            dup_count = len(cc_result.get("duplicates", [])) if isinstance(cc_result, dict) else 0
            if collisions:
                top_hits = []
                for c in collisions[:3]:
                    top_hits.append(f"{c.get('domain_a','?')}×{c.get('domain_b','?')} ({c.get('similarity',0):.3f})")
                steps.append({
                    "step": "concept_collide",
                    "status": "ok",
                    "collisions": len(collisions),
                    "top_hits": top_hits,
                    "duplicates_found": dup_count,
                    "summary": f"概念碰撞: {len(collisions)} 对跨域关联, Top: {', '.join(top_hits)}",
                })
            else:
                steps.append({
                    "step": "concept_collide",
                    "status": "ok",
                    "collisions": 0,
                    "duplicates_found": dup_count,
                    "summary": "概念碰撞: 无高价值跨域关联",
                })
        except Exception as e:
            steps.append({"step": "concept_collide", "status": "skipped", "summary": f"概念碰撞暂不可用: {str(e)[:60]}"})

        # 步骤 7: image_evidence_scan（画像证据链追加）
        if evidence_flags:
            try:
                import json as _json
                entries = _json.loads(evidence_flags) if isinstance(evidence_flags, str) else evidence_flags
                if isinstance(entries, list) and len(entries) > 0:
                    scan_result = self.image_evidence_scan(evidence_entries=entries)
                    steps.append({
                        "step": "image_evidence_scan",
                        "status": "ok" if scan_result.get("fail", 0) == 0 else "warning",
                        "total": scan_result.get("total", 0),
                        "ok": scan_result.get("ok", 0),
                        "fail": scan_result.get("fail", 0),
                        "summary": f"画像证据: {scan_result.get('ok', 0)}/{scan_result.get('total', 0)} 条已写入",
                    })
            except Exception as e:
                steps.append({"step": "image_evidence_scan", "status": "error", "error": str(e), "retryable": True})
                has_error = True
        else:
            steps.append({"step": "image_evidence_scan", "status": "skipped", "summary": "无证据条目"})

        # 记录会话到用户画像（session_count 自增）
        try:
            self.user_profile.record_session()
        except Exception:
            log.debug("suppressed", exc_info=True)

        overall = "error" if has_error else "ok"
        _step_timing["total"] = round((time.time() - _step_timing["_start"]) * 1000)
        result = {
            "macro": "session_end",
            "macro_id": macro_id,
            "overall": overall,
            "steps": steps,
            "_timing_ms": _step_timing,
            "summary": f"收尾完成：{sum(1 for s in steps if s['status'] == 'ok')} ok / "
                       f"{sum(1 for s in steps if s['status'] == 'skipped')} skipped / "
                       f"{sum(1 for s in steps if s['status'] == 'error')} error",
        }
        # 记录使用率
        try:
            client = getattr(self, 'client', '')
            self._macro_tracker.record_macro("session_end", steps, client=client)
        except Exception:
            log.debug("suppressed", exc_info=True)
        return result

    # ─── image_evidence_scan: 画像证据链追加 ───

    def _append_evidence_to_decay(self, entry_name: str, flag_type: str, reason: str, date_str: str) -> tuple:
        """将一条 evidence_flag 追加到 decay.md 证据链

        Args:
            entry_name: 条目名（支持部分匹配，如"追问驱动"匹配"价值排序-⑤ 追问驱动"）
            flag_type: 信号类型（implicit_confirm / subtle_drift / strong_conflict）
            reason: 简短理由
            date_str: 日期字符串（如 "2026-07-20"）

        Returns:
            (bool, str): 是否成功 + 消息
        """
        import os, re
        vault = getattr(self, 'vault_path', None) or os.environ.get("LINGTAI_VAULT", r".")
        decay_path = os.path.join(vault, "画像/.meta/decay.md")
        if not os.path.exists(decay_path):
            return False, "decay.md 不存在"

        with open(decay_path, 'r', encoding='utf-8') as f:
            content = f.read()

        evidence_line = f"  [{date_str}] {flag_type} → {reason}"

        # 在内容中查找匹配的 #### 标题（支持部分匹配）
        section_pattern = re.compile(r'^####\s+(.+)$', re.MULTILINE)
        matched_section = None
        matched_header = None
        for m in section_pattern.finditer(content):
            header_text = m.group(1).strip()
            if entry_name in header_text or header_text in entry_name:
                matched_section = m
                matched_header = header_text
                break

        if not matched_section:
            return False, f"未找到匹配条目: {entry_name}"

        # 定位 "链:" 行
        section_start = matched_section.start()
        next_section = section_pattern.search(content, section_start + 1)
        section_end = next_section.start() if next_section else len(content)
        section = content[section_start:section_end]

        chain_marker = "\n链:"
        chain_idx = section.find(chain_marker)
        if chain_idx == -1:
            # 尝试找 "链："
            chain_marker = "\n链："
            chain_idx = section.find(chain_marker)
        if chain_idx == -1:
            return False, f"条目 {entry_name} 缺少 '链:' 标记"

        # "链:" 行之后的第一个换行位置
        chain_line_end = section.index("\n", chain_idx + len(chain_marker))
        after_chain = section[chain_line_end + 1:]

        # 取 after_chain 的第一行（去掉开头的空白）
        stripped = after_chain.lstrip()
        empty_marker = stripped.startswith("—") or stripped.startswith("--")

        if empty_marker:
            # 替换占位标记
            placeholder_end = chain_line_end + 1 + len(after_chain) - len(stripped) + len(stripped.split("\n")[0])
            new_section = section[:chain_line_end + 1] + "\n" + evidence_line + "\n" + section[placeholder_end:]
        else:
            # 已有证据行，在最后一条证据行后追加
            lines = after_chain.split("\n")
            evidence_count = 0
            for line in lines:
                if line.strip().startswith("["):
                    evidence_count += 1
                else:
                    break
            if evidence_count > 0:
                # 跳过所有证据行
                insert_pos = chain_line_end + 1
                for _ in range(evidence_count):
                    next_nl = section.index("\n", insert_pos + 1)
                    insert_pos = next_nl
                new_section = section[:insert_pos + 1] + "\n" + evidence_line + section[insert_pos + 1:]
            else:
                # 链后无证据行，直接追加
                new_section = section[:chain_line_end + 1] + "\n" + evidence_line + "\n" + after_chain

        content = content[:section_start] + new_section + content[section_start + len(section):]

        with open(decay_path, 'w', encoding='utf-8') as f:
            f.write(content)

        return True, f"已追加 {matched_header}: {flag_type} → {reason}"

    @tool(readonly=False, write=True, category="画像", system=True, name="image_evidence_scan")
    def image_evidence_scan(self, evidence_entries: list, date_str: str = "") -> dict:
        """画像证据扫描：灵识在会话收尾时调用，将当会话的 evidence_flag 追加到 decay.md 证据链。

        灵识在每场对话中自然积累了用户行为画像判断后，调用此工具批量写入。
        每条 entry 的 name 字段支持部分匹配（如"追问驱动"匹配"价值排序-⑤ 追问驱动"）。

        Args:
            evidence_entries: 证据条目列表，每项为 {"entry": "条目名", "flag": "implicit_confirm|subtle_drift|strong_conflict", "reason": "简短理由"}
            date_str: 日期（ISO，如 "2026-07-20"，传空自动取当天）

        Returns:
            dict: 写入结果，含每条的状态
        """
        from datetime import date as dt_date
        date_str = date_str or dt_date.today().isoformat()
        results = []
        ok_count = 0
        fail_count = 0
        for entry in evidence_entries:
            success, msg = self._append_evidence_to_decay(
                entry_name=entry.get("entry", ""),
                flag_type=entry.get("flag", "implicit_confirm"),
                reason=entry.get("reason", ""),
                date_str=date_str,
            )
            results.append({"entry": entry.get("entry", ""), "flag": entry.get("flag", ""), "success": success, "message": msg})
            if success:
                ok_count += 1
            else:
                fail_count += 1
        return {
            "tool": "image_evidence_scan",
            "total": len(evidence_entries),
            "ok": ok_count,
            "fail": fail_count,
            "results": results,
        }

    # ─── 宏使用率查询 ───

    @tool(readonly=False, write=True, category="macro", system=True)
    def get_macro_stats(self, hours: int = 24) -> dict:
        """查询宏工具使用率统计（Phase 3 观察用）

        Args:
            hours: 回溯时间窗口（默认24小时）

        Returns:
            dict: macro_usage / drill_down_rates / macro_coverage / total_macro_calls
        """
        try:
            return self._macro_tracker.get_stats(hours=hours)
        except Exception as e:
            return {"error": str(e), "macro_usage": {}, "drill_down_rates": {}, "macro_coverage": {}}

    # ─── 内部辅助 ───

    def _macro_id(self, prefix: str, seed: str) -> str:
        """生成幂等宏调用 ID"""
        raw = f"{prefix}:{seed}:{time.time()}"
        return f"{prefix}_{hashlib.md5(raw.encode()).hexdigest()[:8]}"

    # ─── auto_evolve: 知识自动演化（扫描→建联→产出刷新候选）───

    @tool(readonly=False, write=True, category="macro", system=True)
    def auto_evolve(self, dry_run: bool = True, budget: int = 5,
                    max_links_per_page: int = 2, do_links: bool = True,
                    do_merge: bool = True, stale_days: int = 30,
                    do_collide: bool = True, max_pages: int = 0) -> dict:
        """知识自动演化：扫描全库 → 自动建立新关联 → 产出内容刷新候选。

        dry_run=True（默认）只扫描预览不落盘，AI 审查后调 dry_run=False 执行。
        - 建联：对孤立/死胡同页程序化补同域链接（复用 auto_edge + add_link）
        - 合并：近重复页（SimHash 汉明距离）自动交叉互链，并报告为合并候选
        - 碰撞：跨域语义相似度 0.6-0.75 概念碰撞（扫描，灵识逐条判断处理）
        - 刷新候选：陈旧页（长期未改但高入链）+ 矛盾页（含 ⚡矛盾 标记），供 agent 用 LLM 增量更新

        Args:
            dry_run: True（默认）只扫描预览不落盘；False 执行写入
            budget: 内容刷新候选上限（默认 5，日预算封顶）
            max_links_per_page: 每页最多补链数（默认 2）
            max_pages: 实际建联页数上限（0=不限，灰度/每日预算用）
            do_links: 是否自动建联
            do_merge: 是否自动交叉互链近重复对
            do_collide: 是否自动概念碰撞检测（默认 True）
            stale_days: 陈旧判定阈值（天，默认 30）

        Returns:
            dict: {ok, dry_run, links_added, merge_linked, refresh_candidates, deferred_refresh, skipped, summary}
        """
        macro_id = self._macro_id("auto_evolve", str(budget))
        import os, re, json
        from datetime import datetime, timedelta
        from dedup_engine import DedupEngine

        vault = getattr(self, 'vault_path', None) or r"."
        pages = getattr(self.auto_edge, 'pages', [])
        if not pages:
            self.auto_edge.refresh()
            pages = self.auto_edge.pages

        page_map = {p.get('path'): p for p in pages}
        now = datetime.now()

        # ── 幂等：今日已演化的页跳过 ──
        state_path = os.path.join(vault, '.tool', 'lingtai-kb', '.auto_evolve_state.json')
        state_path = os.path.normpath(state_path)
        evolved_today = set()
        today_str = now.strftime('%Y-%m-%d')
        try:
            if os.path.exists(state_path):
                with open(state_path, 'r', encoding='utf-8') as f:
                    st = json.load(f)
                if st.get('last_date') == today_str:
                    evolved_today = set(st.get('evolved_pages', []))
        except Exception:
            log.debug("suppressed", exc_info=True)

        def _read_body(path):
            p = path if path.endswith('.md') else path + '.md'
            abs_p = os.path.join(vault, p.replace('/', os.sep))
            if not os.path.exists(abs_p):
                return '', ''
            try:
                txt = open(abs_p, 'r', encoding='utf-8').read()
            except Exception:
                return '', ''
            if txt.startswith('---'):
                parts = txt.split('---', 2)
                fm = parts[1] if len(parts) >= 3 else ''
                body = parts[2] if len(parts) >= 3 else txt
            else:
                fm, body = '', txt
            return fm, body

        def _grade(fm):
            m = re.search(r'品级:\s*(\S+)', fm)
            return m.group(1) if m else ''

        # ── Phase A: 扫描 ──
        link_pairs = []      # (source, target)
        merge_pairs = []     # (low_path, high_path, hamming)
        refresh_raw = []     # {path, reason, priority}

        # A1: 孤立/死胡同 → 补链候选（优先同域）
        for p in pages:
            path = p.get('path', '')
            if path in evolved_today:
                continue
            links_to = p.get('links_to', []) or []
            linked_from = p.get('linked_from', []) or []
            is_isolated = (not links_to and not linked_from)
            is_deadend = (bool(links_to) and not linked_from)
            if not (is_isolated or is_deadend):
                continue
            try:
                sugg = self.auto_edge.get_link_suggestions(path, max_suggestions=max_links_per_page * 2)
            except Exception:
                sugg = []
            added = 0
            for s in sugg:
                tgt = s.get('page', '')
                if not tgt or tgt == path:
                    continue
                if tgt in links_to or tgt in linked_from:
                    continue
                if added >= max_links_per_page:
                    break
                link_pairs.append((path, tgt))
                added += 1

        # A2: 近重复 → 合并候选（SimHash 汉明距离，复用 dedup_engine 算法）
        simhash_cache = {}
        for p in pages:
            path = p.get('path', '')
            _, body = _read_body(path)
            stripped = re.sub(r'\s+', '', body).strip()
            if len(stripped) < 50:
                simhash_cache[path] = (None, 0)
            else:
                simhash_cache[path] = (DedupEngine._simhash(body), len(stripped))
        paths = [pp for pp in simhash_cache if simhash_cache[pp][0] is not None]
        for i in range(len(paths)):
            for j in range(i + 1, len(paths)):
                a, b = paths[i], paths[j]
                if a in evolved_today or b in evolved_today:
                    continue
                dist = DedupEngine._hamming_distance(simhash_cache[a][0], simhash_cache[b][0])
                min_len = min(simhash_cache[a][1], simhash_cache[b][1])
                threshold = 5 if min_len < 200 else 10
                if dist <= threshold:
                    # 低入链页并入高入链页
                    ba = len(page_map.get(a, {}).get('linked_from', []) or [])
                    bb = len(page_map.get(b, {}).get('linked_from', []) or [])
                    low, high = (a, b) if ba <= bb else (b, a)
                    merge_pairs.append((low, high, dist))

        # A3: 刷新候选（陈旧 + 矛盾标记）
        for p in pages:
            path = p.get('path', '')
            if path in evolved_today:
                continue
            fm, body = _read_body(path)
            backlinks = len(p.get('linked_from', []) or [])
            grade = _grade(fm)
            try:
                mtime = os.path.getmtime(os.path.join(vault, (path + '.md').replace('/', os.sep)))
                age_days = (now.timestamp() - mtime) / 86400
            except Exception:
                age_days = 999
            if age_days >= stale_days and backlinks >= 3 and grade != '下品':
                refresh_raw.append({'path': path, 'reason': f'陈旧未更新（约{int(age_days)}天未动，入链{backlinks}）', 'priority': int(age_days)})
            if '⚡矛盾' in body:
                refresh_raw.append({'path': path, 'reason': '含 ⚡矛盾 标记，需更新对立观点', 'priority': 1000})

        # 刷新候选按优先级排序并封顶
        refresh_raw.sort(key=lambda x: -x['priority'])
        refresh_candidates = refresh_raw[:budget]
        deferred = len(refresh_raw) - len(refresh_candidates)

        # A4: 概念碰撞（跨域语义相似度 0.6-0.75）
        collision_pairs = []  # {page_a, page_b, similarity, domain_a, domain_b, title_a, title_b, reason}
        if do_collide:
            try:
                from . import concept_collision
                result = concept_collision.collide(
                    vault_path=vault,
                    pages=pages,
                    top_n=10,
                    min_sim=0.6,
                    max_sim=0.75,
                )
                collision_pairs = result.get("collisions", [])
            except Exception as e:
                log.warning("auto_evolve A4 collision failed", extra={"error": str(e)})

        # ── Phase B/C: 自动落盘（确定性、可逆、git 回滚）──
        links_added = []
        merge_linked = []
        skipped = []
        collision_applied = []
        if not dry_run:
            if do_links:
                pages_touched = set()
                for src, tgt in link_pairs:
                    if max_pages and len(pages_touched) >= max_pages:
                        break
                    try:
                        r = self.add_link(source=src, target=tgt)
                        if r.get('success'):
                            links_added.append({'source': src, 'target': tgt})
                            pages_touched.add(src)
                        elif r.get('exists'):
                            pass
                        else:
                            skipped.append({'type': 'link', 'source': src, 'target': tgt, 'error': r.get('error')})
                    except Exception as e:
                        skipped.append({'type': 'link', 'source': src, 'target': tgt, 'error': str(e)[:80]})
            if do_merge:
                for low, high, d in merge_pairs:
                    try:
                        r = self.add_link(source=low, target=high)
                        if r.get('success'):
                            merge_linked.append({'low': low, 'high': high, 'hamming': d})
                        elif r.get('exists'):
                            pass
                        else:
                            skipped.append({'type': 'merge', 'low': low, 'high': high, 'error': r.get('error')})
                    except Exception as e:
                        skipped.append({'type': 'merge', 'low': low, 'high': high, 'error': str(e)[:80]})
            # B4: [已删除] 自动应用概念碰撞——无差别补链无判断力，由灵识逐条判断后执行
            # 写幂等状态
            try:
                evolved = set(evolved_today)
                for s, _ in link_pairs:
                    evolved.add(s)
                for low, _, _ in merge_pairs:
                    evolved.add(low)
                for c in refresh_candidates:
                    evolved.add(c['path'])
                with open(state_path, 'w', encoding='utf-8') as f:
                    json.dump({'last_date': today_str, 'evolved_pages': list(evolved)}, f, ensure_ascii=False, indent=2)
            except Exception:
                log.debug("suppressed", exc_info=True)

        summary = (f"自动演化：补链 {len(links_added)}(候选{len(link_pairs)}) / "
                   f"合并互链 {len(merge_linked)}(候选{len(merge_pairs)}) / "
                   f"碰撞检测 {len(collision_pairs)} / "
                   f"刷新候选 {len(refresh_candidates)}（封顶{budget}，延迟{deferred}）"
                   + (" [dry-run]" if dry_run else ""))
        return {
            'ok': True,
            'macro': 'auto_evolve',
            'macro_id': macro_id,
            'dry_run': dry_run,
            'link_candidates': link_pairs,
            'merge_candidates': merge_pairs,
            'collision_candidates': collision_pairs,
            'links_added': links_added,
            'merge_linked': merge_linked,
            'collision_applied': collision_applied,
            'refresh_candidates': refresh_candidates,
            'deferred_refresh': deferred,
            'skipped': skipped,
            'summary': summary,
        }