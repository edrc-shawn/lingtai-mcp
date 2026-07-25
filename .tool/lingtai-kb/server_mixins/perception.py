# -*- coding: utf-8 -*-
"""感知系统 mixin — 知识注入/保存/推荐/上下文"""
import importlib
import os
import re  # Topic Gate 安全网（短词豁免）
from decorators import tool
import topic_gate  # 前置门控（泛指词拦截，省 Token）
from logger import get_logger

log = get_logger(__name__)

class PerceptionMixin:
    @tool(readonly=True, write=False, category="knowledge", system=False, name="knowledge_inject")
    def inject(self, keyword: str, max_tokens: int = 800) -> dict:
        """
        按 token 预算注入知识片段到当前上下文（轻量、截断、快速）。
        场景：需要把相关知识塞进对话窗口供后续推理，不需要完整页面时。
        区别：要完整页面列表自行分析用 knowledge_search；要带差距分析的合成回答用 knowledge_synthesize。

        Args:
            keyword: 搜索关键词
            max_tokens: 最大注入 token 数（默认 800）

        Returns:
            dict: 匹配的知识片段（截断至 max_tokens）
        """
        # ═══ 前置门控：Topic Gate（泛指词快速拦截，省 Token）═══
        # 在 record_query 之前拦截，避免泛指词触发完整管线
        _gate = topic_gate.should_skip_inject(keyword, self.memory.pages)

        # ═══ 安全网：纯 ASCII 短词豁免（即使 topic_gate 模块未热重载）
        # n-gram 需要至少 3 字符，"AI""MCP"等 2-3 字纯英文短词无法生成有效 3-gram
        # 知识库可能大量覆盖（"AI"66页），补充内联校验防止误拦截
        if _gate["skip"] and _gate.get("reason") == "generic_word_no_hits":
            _kw_lower = keyword.strip().lower()
            if re.match(r'^[a-z]+$', _kw_lower) and len(_kw_lower) < 3:
                _gate = {"skip": False, "_safety": "short_ascii_bypass"}

        if _gate["skip"]:
            return {
                "found": False,
                "gate_skipped": True,
                "gate_reason": _gate.get("reason", "unknown"),
                "gate_detail": _gate.get("detail", ""),
                "gate_safety": _gate.get("_safety", ""),
                "keyword": keyword,
                "probe": _gate.get("probe", {}),
            }

        # 学习层：注入即查询，记录兴趣
        self.user_profile.record_query(keyword)
        result = self.perception.inject(keyword, max_tokens=max_tokens)
        hit = isinstance(result, dict) and result.get("found", False)

        self.perception_stats_monitor.record_rule1(hit)
        if hit:
            for r in result.get("results", []):
                self.user_profile.record_interest(r.get("source", "注入"))
            self.user_profile.record_interest("注入")
        return result
    
    @tool(readonly=False, write=True, category="knowledge", system=False, name="raw_save")
    def save(self, content: str, category: str = "", source: str = "对话") -> dict:
        """
        保存原始素材到 原料/ 目录（待提炼，不直接可检索）。
        场景：收集到有价值但未加工的原始信息（文章摘录、对话片段、灵感）时。
        区别：创建可直接检索的丹房成品知识页用 page_create；写记忆/教训/偏好用 memory_write。

        Args:
            content: 知识内容
            category: 分类（可选）。"系统"→写入日志不入原料；默认→写入原料
            source: 来源

        Returns:
            dict: 保存结果
        """
        log.debug("save called with dedup check")
        
        # 系统反馈路由：不入原料，直写日志
        if category == "系统":
            return self._save_system_feedback(content, source)
        
        # 实时去重检查
        from dedup_engine import DedupEngine
        dedup = DedupEngine(self.vault_path)
        
        # 生成临时文件名用于检查
        import hashlib
        import time
        tmp_name = f"原料/{hashlib.md5(content.encode()).hexdigest()[:8]}-{int(time.time())}.md"
        
        check_result = dedup.check(content, tmp_name)
        log.debug("dedup check result", extra={"check_result": check_result})
        
        if check_result["is_dup"]:
            # 发现重复，不写新文件
            return {
                "success": True,
                "dup": True,
                "match": check_result["match"],
                "method": check_result["method"],
                "confidence": check_result["confidence"],
                "message": f"原料已存在（{check_result['method']}匹配：{check_result['match']}）"
            }
        
        # 无重复，正常保存
        result = self.perception.save(content, category, source)
        saved = isinstance(result, dict) and result.get("success", False)

        # 反向泄漏检测（二级信号）：原料是知识管线入口，volatile 内容入原料
        # 会在后续提炼时污染丹房，这里登记供 memory_lifecycle 统计压力。
        if saved:
            try:
                from memory_bank.lifecycle import record_knowledge_write
                rec_path = result.get("path", "原料/unknown")
                record_knowledge_write("raw_save", rec_path, content)
            except Exception:
                log.debug("suppressed", exc_info=True)

        # 注册到去重索引
        if saved and "path" in result:
            file_path = result["path"].replace("\\", "/")
            if not file_path.startswith("原料/"):
                file_path = f"原料/{file_path}"
            log.debug("registering file", extra={"path": file_path})
            dedup.register(file_path, content)
        
        # 记录统计
        self.perception_stats_monitor.record_rule2(saved)
        return result

    def _save_system_feedback(self, content: str, source: str = "") -> dict:
        """系统反馈路由：写入日志而非原料，不参与提炼"""
        import os
        from datetime import datetime
        vault = getattr(self, 'vault_path', None) or r"."
        log_path = os.path.join(vault, '丹房', '日志.md')
        oplog_path = os.path.join(vault, '丹房', '.meta', 'oplog.jsonl')

        now = datetime.now()
        dt = now.strftime('%y-%m-%d %H:%M')
        iso = now.isoformat()

        # 人类版日志
        summary = content[:60].replace('\n', ' ')
        line = f"[{dt}] WB dialog | 系统 | {summary} | → {source}\n"
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        with open(log_path, 'a', encoding='utf-8') as f:
            f.write(line)

        # 机读版日志
        import json
        entry = {"t": iso, "op": "WB", "mode": "dialog", "type": "系统",
                 "summary": summary, "source": source, "links": []}
        os.makedirs(os.path.dirname(oplog_path), exist_ok=True)
        with open(oplog_path, 'a', encoding='utf-8') as f:
            f.write(json.dumps(entry, ensure_ascii=False) + '\n')

        return {"success": True, "route": "log", "message": f"系统反馈已写入日志: {summary}"}

    def recommend(self, current_topic: str, max_results: int = 5) -> dict:
        """
        推荐相关页面
        
        Args:
            current_topic: 当前话题
            max_results: 最大结果数
        
        Returns:
            dict: 推荐结果
        """
        result = self.perception.recommend(current_topic, max_results)
        recommended = isinstance(result, dict) and len(result.get("recommendations", [])) > 0
        # 记录统计
        self.perception_stats_monitor.record_rule3(recommended)
        return result

    def _recent_activity(self, days: int = 7, limit: int = 12) -> list:
        """读取丹房操作日志（oplog.jsonl）最近 N 天的操作脉络，作为首轮上下文的活跃窗口（D-A）。

        类比 CodePilot 的 codepilot_memory_recent：让新会话开局即带近期脉络，不必等搜索。
        返回最近 limit 条操作的精简摘要（t + summary，单条 summary 截断 120 字）。
        """
        import os, json
        from datetime import datetime
        vault = getattr(self, 'vault_path', None) or r"."
        oplog = os.path.join(vault, "丹房", ".meta", "oplog.jsonl")
        if not os.path.isfile(oplog):
            return []
        cutoff = datetime.now().timestamp() - days * 86400
        items = []
        try:
            with open(oplog, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        e = json.loads(line)
                        ts = e.get('t', '')
                        if 'T' in ts:
                            try:
                                if datetime.fromisoformat(ts).timestamp() >= cutoff:
                                    items.append(e)
                            except ValueError:
                                continue
                    except json.JSONDecodeError:
                        continue
        except Exception:
            return []
        out = []
        for e in items[-limit:]:
            summary = e.get('summary') or e.get('type', '')
            if summary:
                out.append({"t": e.get('t', ''), "summary": summary[:120]})
        return out

    def _detect_cross_end_unsynced(self) -> dict:
        """跨端未同步检测：检查其他端最近活跃但未向灵台MCP写入记忆。

        返回 {alerts: [{client, calls, hours_ago, message}], has_alerts: bool}
        context_load 注入上下文后，AI 据此提醒用户。
        """
        import os, json as _json
        from datetime import datetime, timedelta
        vault = getattr(self, 'vault_path', None) or r"."
        log_path = os.path.join(vault, '.tool', 'lingtai-kb', 'logs', 'tool_sessions.jsonl')
        if not os.path.isfile(log_path):
            return {"alerts": [], "has_alerts": False}
        
        current_client = getattr(self, 'client', 'unknown')
        write_tools = {'memory_write', 'raw_save', 'page_create', 'page_update', 'refine_mark'}
        cutoff = datetime.now() - timedelta(hours=6)
        ends = {}
        # 额外聚合高频工具名（自动印记用）
        tool_freq = {}  # client -> {tool_name: count}
        
        try:
            with open(log_path, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = _json.loads(line)
                    except Exception:
                        continue
                    ts = rec.get('timestamp', '')
                    try:
                        dt = datetime.fromisoformat(ts)
                    except Exception:
                        continue
                    if dt < cutoff:
                        continue
                    client = rec.get('client', 'unknown')
                    if client == current_client or client == 'unknown':
                        continue
                    agg = ends.setdefault(client, {'client': client, 'calls': 0, 'writes': 0, 'last_active': ts})
                    for c in rec.get('tool_calls', []):
                        t = c.get('name', '')
                        agg['calls'] += 1
                        if t in write_tools:
                            agg['writes'] += 1
                        # 聚合工具频率
                        freq = tool_freq.setdefault(client, {})
                        freq[t] = freq.get(t, 0) + 1
                    if ts > agg['last_active']:
                        agg['last_active'] = ts
        except OSError:
            return {"alerts": [], "has_alerts": False}
        
        alerts = []
        for agg in ends.values():
            if agg['calls'] >= 5 and agg['writes'] == 0:
                try:
                    hours_ago = round((datetime.now() - datetime.fromisoformat(agg['last_active'])).total_seconds() / 3600, 1)
                except Exception:
                    hours_ago = 0
                # 提取频率最高的 5 个工具名
                freq = tool_freq.get(agg['client'], {})
                top_tools = sorted(freq.items(), key=lambda x: -x[1])[:5]
                alerts.append({
                    'client': agg['client'],
                    'calls': agg['calls'],
                    'hours_ago': hours_ago,
                    'top_tools': [{"name": n, "count": c} for n, c in top_tools],
                    'message': f"⚠️ {agg['client']} 最近 {hours_ago}h 内有 {agg['calls']} 次工具调用但未写任何记忆到灵台MCP——可能遗漏了上下文同步",
                })
        
        return {"alerts": alerts, "has_alerts": len(alerts) > 0}

    def _auto_imprint_unsynced(self, alerts: list) -> None:
        """自动写跨端活动印记——检测到未同步端时，写一条记忆到 memory_bank。

        纯规则聚合，无 LLM 参与。每端每 6h 最多写 1 条，避免重复。
        """
        if not hasattr(self, '_unsynced_imprint_log'):
            self._unsynced_imprint_log = {}
        from datetime import datetime, timedelta
        now = datetime.now()
        cutoff_6h = now - timedelta(hours=6)
        
        for alert in alerts:
            client = alert['client']
            # 查距上次印记是否已超过 6h
            last = self._unsynced_imprint_log.get(client)
            if last and last > cutoff_6h:
                continue
            
            calls = alert['calls']
            hours = alert['hours_ago']
            top_tools = alert.get('top_tools', [])
            tools_str = ', '.join(f"{t['name']}({t['count']}次)" for t in top_tools[:3]) if top_tools else '未知'
            
            content = (
                f"跨端活动印记：{client} 最近 {hours}h 内有 {calls} 次工具调用"
                f"（高频：{tools_str}），未写记忆到灵台MCP。"
            )
            try:
                self.memory_bank.write(
                    content=content,
                    source_type="system",
                    context={"client": client, "calls": calls, "hours_ago": hours, "top_tools": top_tools},
                    tags=["imprint", "cross-end", "unsynced"],
                    knowledge_candidate=False,
                )
                self._unsynced_imprint_log[client] = now
            except Exception:
                log.debug("suppressed", exc_info=True)

    def context(self, client_capabilities=None, detail="detailed") -> dict:
        """
        生成会话上下文（v7: 懒加载缓存）
        返回三层拼盘：丹房知识总览 / 灵识记忆 / 用户画像

        v7 变更：首次调用全量计算并缓存，后续返回缓存。
        ensure_context() 首次触发时走全量计算，context_load 工具调用直接读缓存。
        """
        import os
        # 🔴 跨端未同步检测 + 自动印记：跑在缓存判断之前，每调必执行
        cross_end_alert = None
        try:
            cross_end_alert = self._detect_cross_end_unsynced()
        except Exception:
            log.debug("suppressed", exc_info=True)
        try:
            if cross_end_alert and cross_end_alert.get("has_alerts"):
                self._auto_imprint_unsynced(cross_end_alert["alerts"])
        except Exception:
            log.debug("suppressed", exc_info=True)

        # 缓存命中——直接返回（context_load 工具走此路径）
        if getattr(self, '_context_loaded', False) and self._context_cache is not None:
            # 跨进程新鲜度：其他进程可能已更新 index.json
            index_path = os.path.join(self.vault_path, "丹房", ".meta", "index.json")
            try:
                if os.path.exists(index_path):
                    current_mtime = os.path.getmtime(index_path)
                    cached_mtime = getattr(self, '_context_index_mtime', 0.0)
                    if current_mtime <= cached_mtime:
                        log.debug("returning cached context")
                        return self._context_cache
                    # mtime 已变，缓存过期
                    log.debug("cache stale, recomputing")
            except OSError:
                pass  # 文件不存在，走重新计算
        log.debug("context called")

        self.perception_stats_monitor.record_rule4()

        # 1. 丹房知识层
        danfang = self.perception.context()

        # 2. 用户画像层
        profile = {}
        try:
            profile = self.user_profile.get_profile_summary()
        except Exception as e:
            log.warning("profile error", exc_info=True)

        # 3. 灵识记忆层
        lessons = []
        try:
            mem_result = self.memory_bank.query(
                keyword="",
                status="active",
                min_confidence=0.5,
            )
            raw = mem_result if isinstance(mem_result, list) else mem_result.get("results", [])
            lessons = [
                {
                    "content": l.get("content", "")[:100],
                    "confidence": l.get("current_confidence", 0),
                    "authority": "confirmed" if l.get("source") in ("user_correction", "user_repeated") else "candidate",
                }
                for l in raw[:3]
            ]
        except Exception as e:
            log.warning("lessons error", exc_info=True)

        # 4. 工作印记层
        work_imprint = None
        try:
            imprint_result = self.memory_bank.query(
                keyword="工作印记",
                status="active",
                min_confidence=0.3,
            )
            raw_imprint = imprint_result if isinstance(imprint_result, list) else imprint_result.get("results", [])
            if not raw_imprint:
                imprint_result = self.memory_bank.query(
                    keyword="工作印记",
                    status="pending",
                    min_confidence=0.0,
                )
                raw_imprint = imprint_result if isinstance(imprint_result, list) else imprint_result.get("results", [])
            if raw_imprint:
                best = max(raw_imprint, key=lambda x: x.get("current_confidence", 0))
                work_imprint = {
                    "content": best.get("content", ""),
                    "confidence": best.get("current_confidence", 0),
                    "timestamp": best.get("last_verified", best.get("created_at", "")),
                }
            else:
                # 兜底：memory_bank 无工作印记 → 尝试从 WB MEMORY.md 读最近条目
                import os, re
                vault = getattr(self, 'vault_path', None) or r"."
                wb_memory = os.path.join(vault, '.workbuddy', 'memory', 'MEMORY.md')
                if os.path.isfile(wb_memory):
                    with open(wb_memory, 'r', encoding='utf-8') as f:
                        raw = f.read()
                    # 按 ## 分割，取最近 3 个条目
                    parts = re.split(r'\n(?=## )', raw)
                    recent = [p.strip() for p in parts if p.strip() and not p.strip().startswith('# ')]
                    recent = [p for p in recent if re.match(r'## ', p)][:3]
                    if recent:
                        fallback = '；'.join(
                            p.split('\n')[0].replace('## ', '').strip() for p in recent
                        )
                        work_imprint = {
                            "content": "工作印记（WB降级）：" + fallback[:300],
                            "confidence": 0.2,
                            "timestamp": "",
                            "_fallback": True,
                        }
        except Exception as e:
            log.warning("work_imprint error", exc_info=True)

        # 5. 场景分支检测（数据齐全后统一判定）
        scene = "通用"
        try:
            scene = self._detect_current_scene(danfang, lessons, work_imprint, profile)
        except Exception as e:
            log.warning("scene detection error", exc_info=True)

        # 5.5 pressing 浮现协议：未解决记忆 + 待确认画像 + 低活跃页
        pressing = self._build_pressing()

        # 6. 协作者约束集层
        constraints = None
        try:
            import os
            vault = getattr(self, 'vault_path', None) or r"."
            cpath = os.path.join(vault, '丹房', '00-思考与认知', '协作者约束集.md')
            if os.path.exists(cpath):
                with open(cpath, 'r', encoding='utf-8') as f:
                    text = f.read()
                # 提取 ### 约束 之后的内容
                idx = text.find('## 约束')
                if idx > 0:
                    constraints = text[idx:].strip()
        except Exception as e:
            log.warning("constraints error", exc_info=True)

        # 6.5 近期活跃窗口（D-A：首轮活跃记忆脉络，类比 codepilot_memory_recent）
        recent_activity = self._recent_activity(days=7, limit=12)

        # 7. 场景分支已在步骤5判定，scene 由此驱动

        # 丹房知识瘦身：只保留核心指标
        domains = danfang.get("overview", {}).get("domains", {})
        top_domains = sorted(domains.items(), key=lambda x: x[1], reverse=True)[:3]
        hubs = danfang.get("hub_pages", [])[:3]

        work_imprint_layer = None
        if work_imprint:
            is_fallback = work_imprint.pop("_fallback", False)
            work_imprint_layer = {
                "authority": "fallback_wb" if is_fallback else "confirmed",
                "freeze": "dynamic",
                **work_imprint,
            }

        result = {
            "greeting": danfang.get("greeting", ""),
            "message": danfang.get("message", ""),
            "scene": scene,
            "pressing": pressing,
            "capabilities": self._resolve_capabilities(client_capabilities),
            "cross_end_alert": cross_end_alert,
            "layers": {
                "丹房知识": {
                    "authority": "confirmed",
                    "pending_count": danfang.get("pending_count", 0),
                    "total_pages": danfang.get("overview", {}).get("total_pages", 0),
                    "top_domains": top_domains,
                    "hub_pages": [{"path": h["path"], "title": h["title"], "backlinks": h["backlinks"]} for h in hubs],
                },
                "灵识记忆": {
                    "authority": "confirmed" if any(l.get("authority") == "confirmed" for l in lessons) else "candidate",
                    "lessons": lessons,
                },
                "画像三层": self._read_profile_files(),
                "工作印记": work_imprint_layer,
                "协作者约束集": {
                    "authority": "hard_rule",
                    "freeze": "static",
                    "rules": constraints,
                },
                "近期活跃": {
                    "authority": "candidate",
                    "window_days": 7,
                    "count": len(recent_activity),
                    "activities": recent_activity,
                },
            },
        }
        # 7.5 Preamble 环境感知（GStack 启发：每次 skill 前跑环境检查）
        try:
            result["preamble"] = self._run_preamble(lessons, scene)
        except Exception:
            result["preamble"] = {"active_sessions": 1}  # fallback

        # 7.6 注入人格口吻 + 路由速查（替代启动协议中的显式 page_read）
        try:
            vault = getattr(self, 'vault_path', None) or r"."
            # 读灵识人设口吻特征表
            persona_path = os.path.join(vault, '丹房', '09-系统自身', '灵识人设.md')
            if os.path.exists(persona_path):
                with open(persona_path, 'r', encoding='utf-8') as f:
                    ptext = f.read()
                # 提取口吻特征表
                import re
                m = re.search(r'\| 场景 \| 怎么说 \|.*?(\|.*?\|.*?\|.*?\|(?:\n\|.*?\|.*?\|.*?\|)*)', ptext, re.DOTALL)
                result["persona_voice"] = m.group(1).strip() if m else "（见完整文件）"
            # 读 RESOLVER.md TL;DR 速查表
            route_path = os.path.join(vault, '技能', 'RESOLVER.md')
            if os.path.exists(route_path):
                with open(route_path, 'r', encoding='utf-8') as f:
                    rtext = f.read()
                m = re.search(r'\|\| 用户想 \| 做什么 \|.*?(\|.*?\|.*?\|(?:\n\|.*?\|.*?\|)*)', rtext, re.DOTALL)
                result["route_tldr"] = m.group(1).strip() if m else "（见完整文件）"
        except Exception as e:
            log.warning("persona_voice/route_tldr error", exc_info=True)

        # 上下文预算粗估（D-C：呼应 CodePilot「始终知道离上限多远」，JSON 约 2 字符/token）
        import json as _json
        result["context_tokens_estimate"] = len(_json.dumps(result, ensure_ascii=False)) // 2

        # 缓存结果——下一次 context_load 直接返回缓存（始终存 detailed 全量；concise 按需裁剪）
        self._context_cache = result
        self._context_loaded = True
        # 记录缓存时的 index.json mtime，用于跨进程新鲜度检测
        try:
            index_path = os.path.join(self.vault_path, "丹房", ".meta", "index.json")
            self._context_index_mtime = os.path.getmtime(index_path)
        except OSError:
            self._context_index_mtime = 0.0
        if detail and detail != "detailed":
            return self._trim_context(result, detail)
        return result

    def _run_preamble(self, lessons: list, scene: str) -> dict:
        """前置环境感知（GStack Preamble 启发），注入 context_load 返回。

        轻量检查，不调外部工具/LLM，纯文件读取 + 内存统计。
        """
        import json as _json
        import os as _os
        import time as _time

        preamble = {"active_sessions": 1}

        # 1. 活跃会话数 — 读取 session_tracker 日志统计近 2h 调用
        try:
            vault = getattr(self, 'vault_path', None) or _os.environ.get(
                "LINGTAI_VAULT", r"."
            )
            slog = _os.path.join(vault, ".tool", "lingtai-kb", "logs", "tool_sessions.jsonl")
            if _os.path.isfile(slog):
                now = _time.time()
                seen_pids = set()
                with open(slog, 'r', encoding='utf-8') as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            entry = _json.loads(line)
                            ts = entry.get("ts", "")
                            if ts:
                                from datetime import datetime as _dt
                                try:
                                    parsed = _dt.fromisoformat(ts)
                                    if (now - parsed.timestamp()) < 7200:
                                        seen_pids.add(entry.get("pid", 0))
                                except (ValueError, TypeError):
                                    pass
                        except _json.JSONDecodeError:
                            pass
                preamble["active_sessions"] = max(1, min(len(seen_pids), 5))
        except Exception:
            preamble["active_sessions"] = 1

        # 2. 场景关联教训 — 从 lessons 中筛选当前场景相关的
        try:
            scene_related = []
            for l in lessons:
                content = str(l.get("content", ""))
                # 场景命中：scene 关键词出现在教训内容中
                if scene and scene != "通用" and scene[:4] in content[:80]:
                    scene_related.append(l)
            if scene_related:
                preamble["scene_lessons"] = scene_related[:2]
        except Exception:
            log.debug("suppressed", exc_info=True)

        # 3. 输出模式建议 — 根据活跃会话数调整
        session_count = preamble.get("active_sessions", 1)
        if session_count >= 3:
            preamble["communication_mode"] = "concise"
            preamble["mode_reason"] = f"多会话活跃（{session_count}），建议收敛输出"
        elif scene_related:
            preamble["communication_mode"] = "focused"
            preamble["mode_reason"] = f"该场景有 {len(scene_related)} 条历史教训，建议专注避坑"
        else:
            preamble["communication_mode"] = "normal"

        return preamble

    def _trim_context(self, result: dict, detail: str) -> dict:
        """
        按 detail 裁剪上下文体积（mcp-design G2 上下文预算）。

        concise 保留所有"必须消费"的层（画像三层 / scene / capabilities /
        丹房核心指标 / 灵识记忆 / 约束集标题），仅截断体积大但非即时必需的字段：
        - 协作者约束集全文（可很长）→ 截断到前 600 字并标注
        - 丹房 hub_pages 的 backlinks 数字 → 去掉
        画像三层与 scene/capabilities 不可裁（规则④要求必注入上下文）。

        注：detail=='detailed' 时直接返回原结果（context() 也已保证只有 concise 调本方法）。
        """
        if not detail or detail == "detailed":
            return result
        import copy
        trimmed = copy.deepcopy(result)
        layers = trimmed.get("layers", {})

        constraints = layers.get("协作者约束集")
        if isinstance(constraints, dict) and constraints.get("rules"):
            rules = constraints["rules"]
            if len(rules) > 600:
                constraints["rules"] = rules[:600] + "\n…(concise 已截断，detail='detailed' 取全)"

        danfang = layers.get("丹房知识")
        if isinstance(danfang, dict) and isinstance(danfang.get("hub_pages"), list):
            danfang["hub_pages"] = [
                {"path": h.get("path"), "title": h.get("title")} for h in danfang["hub_pages"]
            ]

        trimmed["_detail"] = detail
        return trimmed

    def _resolve_capabilities(self, client_capabilities=None) -> dict:
        """解析客户端能力清单（capability manifest）。

        优先级：调用方显式传入 > 环境变量 LINGTAI_CAPABILITIES(JSON) > 服务端预置已知端清单 > 默认最小集。
        灵台不绑定任何桌面端，仅据声明的能力调整产出形态（modality）与 fallback。
        """
        import os
        import json

        DEFAULT_CAPABILITIES = {
            "text_output": True,
            "file_write": True,
        }
        # 已知端能力预设（未来可由握手协议或配置填充；当前以 env / 显式声明为主）
        KNOWN_CLIENTS = {
            "WorkBuddy": {"text_output": True, "file_write": True, "visualize": True,
                          "image_gen": True, "automation": True, "skill_system": True},
            "Reasonix": {"text_output": True, "file_write": True, "skill_system": True},
            "QoderWork": {"text_output": True, "file_write": True},
            "MimoCode": {"text_output": True, "file_write": True},
        }

        resolved = dict(DEFAULT_CAPABILITIES)

        # 1) 服务端 env 兜底（声明当前客户端身份或完整 manifest）
        env_manifest = os.environ.get("LINGTAI_CAPABILITIES")
        if env_manifest:
            try:
                parsed = json.loads(env_manifest)
                if isinstance(parsed, dict):
                    resolved.update(parsed)
            except Exception:
                log.debug("suppressed", exc_info=True)
        env_client = os.environ.get("LINGTAI_CLIENT")
        if env_client and env_client in KNOWN_CLIENTS:
            resolved.update(KNOWN_CLIENTS[env_client])

        # 2) 调用方显式握手声明（最高优先级）
        if client_capabilities:
            try:
                if isinstance(client_capabilities, str):
                    declared = json.loads(client_capabilities)
                else:
                    declared = client_capabilities
                if isinstance(declared, dict):
                    resolved.update(declared)
            except Exception:
                log.debug("suppressed", exc_info=True)

        return resolved

    def _read_profile_files(self) -> dict:
        """读取画像三层，每层最多 3 条 [已确认]，每条截断 100 字符。
        支持 [强度:X.XX] 标注（0~1 连续维度），缺失时默认 0.5。
        """
        import os, re
        vault = getattr(self, 'vault_path', None) or r"."
        profile_dir = os.path.join(vault, '画像')
        result = {"authority": "confirmed", "freeze": "static"}
        intensity_re = re.compile(r'\[强度:(\d+\.?\d*)\]')
        files = {'履历.md': '事实层', '心性.md': '推断层', '我是谁.md': '存在层'}
        for fname, layer in files.items():
            fpath = os.path.join(profile_dir, fname)
            if os.path.exists(fpath):
                try:
                    with open(fpath, 'r', encoding='utf-8') as f:
                        text = f.read()
                    confirmed = []
                    for line in text.split(chr(10)):
                        if '[已确认]' in line:
                            trimmed = line.strip()[:100]
                            m = intensity_re.search(line)
                            intensity = float(m.group(1)) if m else 0.5
                            trimmed = intensity_re.sub('', trimmed).strip()
                            confirmed.append({
                                "text": trimmed,
                                "intensity": round(intensity, 2),
                            })
                    key = fname.replace('.md', '')
                    result[key] = {
                        'layer': layer,
                        'items': confirmed[:3],
                    }
                except:
                    pass
        return result

    def _build_pressing(self) -> dict:
        """
        构建 pressing 浮现字段（P2-3）：
        1. 待确认画像候选（[待确认] 条目）
        2. 未解决灵识记忆（高置信度 unresolved 内容）
        3. 低活跃丹房页（超30天无更新 + 低引用）
        """
        import os
        vault = getattr(self, 'vault_path', None) or r"."
        pressing = {"profile_pending": [], "memory_unresolved": [], "stale_pages": [], "count": 0}

        # ── 1. [待确认] 画像条目 ──
        try:
            profile_dir = os.path.join(vault, '画像')
            for fname in ('履历.md', '心性.md', '我是谁.md'):
                fpath = os.path.join(profile_dir, fname)
                if os.path.exists(fpath):
                    with open(fpath, 'r', encoding='utf-8') as f:
                        for line in f:
                            if '[待确认]' in line and '标签说明' not in line and '阈值' not in line:
                                pressing["profile_pending"].append({
                                    "source": fname.replace('.md', ''),
                                    "text": line.strip()[:100],
                                })
        except Exception:
            log.debug("suppressed", exc_info=True)

        # ── 2. 未解决灵识记忆 ──
        try:
            unresolved = self.memory_bank.query(
                keyword="unresolved|待解决|未完成|pending|wip",
                status="active",
                min_confidence=0.5,
            ) if hasattr(self, 'memory_bank') else []
            if isinstance(unresolved, dict):
                unresolved = unresolved.get("results", [])
            mark = ["unresolved", "待解决", "未完成", "pending"]
            filtered = [m for m in unresolved if any(k in (m.get("content","") or "") for k in mark)]
            for m in filtered[:3]:
                pressing["memory_unresolved"].append({
                    "content": (m.get("content","") or "")[:120],
                    "confidence": m.get("current_confidence", 0),
                })
        except Exception:
            log.debug("suppressed", exc_info=True)

        # ── 3. 低活跃丹房页（超30天且低引用）──
        try:
            if hasattr(self, 'memory'):
                from datetime import datetime, timedelta
                cutoff = datetime.now() - timedelta(days=30)
                candidates = []
                for page in getattr(self.memory, 'pages', []):
                    date_str = page.get("date", "")
                    if date_str:
                        try:
                            page_date = datetime.strptime(date_str, "%Y-%m-%d")
                        except ValueError:
                            continue
                        backlinks = len(page.get("linked_from", []))
                        pinji = page.get("pinji", "")
                        if page_date < cutoff and (pinji == "下品" or backlinks <= 3):
                            candidates.append({
                                "path": page.get("path", ""),
                                "title": page.get("title", ""),
                                "days_since": (datetime.now() - page_date).days,
                                "backlinks": backlinks,
                                "pinji": pinji,
                            })
                candidates.sort(key=lambda p: (p["backlinks"], -p["days_since"]))
                for c in candidates[:3]:
                    pressing["stale_pages"].append(c)
        except Exception:
            log.debug("suppressed", exc_info=True)

        pressing["count"] = (
            len(pressing["profile_pending"])
            + len(pressing["memory_unresolved"])
            + len(pressing["stale_pages"])
        )
        return pressing

    def _detect_current_scene(self, danfang: dict, lessons: list, work_imprint: dict, profile: dict) -> str:
        """根据当前上下文特征自动检测场景分支（2026-07-07 校准）"""
        from memory_bank.confidence import detect_scene
        texts = []
        # 工作印记权重最高——它明确描述当前在做什么
        if work_imprint:
            texts.append(work_imprint.get("content", "") * 2)
        if lessons:
            for l in lessons:
                texts.append(l.get("content", ""))
        combined = " ".join(texts)
        return detect_scene(combined)

    # ─── 知识页管理（方向⑪：工具套件重构）───

    def search_evidence(self, keyword: str, max_results: int = 5) -> dict:
        """
        搜索并返回完整证据链（代理调用，用于 debug 检索质量）
        """
        result = self.perception.inject(keyword, max_tokens=0)  # 0 表示不限
        # 剥离内部结构，只保留证据链
        if not result.get('found'):
            return {"found": False, "keyword": keyword}

        evidence = result.get('evidence', {})
        items = []
        for r in result.get('results', [])[:max_results]:
            items.append({
                'path': r.get('path'),
                'title': r.get('title'),
                'score': r.get('score'),
                'match_kind': r.get('match_kind'),
                'inject_priority': r.get('inject_priority'),
                'evidence': r.get('evidence'),
            })

        return {
            'found': True,
            'keyword': keyword,
            'query_anchors': evidence.get('query_anchors', []),
            'budget_info': {
                'kept': evidence.get('kept_count'),
                'dropped': evidence.get('dropped_count'),
                'used': evidence.get('budget_used'),
            },
            'items': items,
        }

    def lingshi_inject(self, keyword: str, min_confidence: float = 0.3, max_items: int = 5) -> dict:
        """
        一次性注入灵识四层记忆（灵魂+画像+长期记忆+系统记忆）到当前上下文。
        场景：会话启动时获取完整记忆上下文（规则⑲要求必调）；需要"我是谁+我知道什么"全景时。
        区别：按关键词检索特定记忆条目用 memory_search；查历史会话交互日志用 episodic_search；查知识用 knowledge_search。

        第1层：灵识灵魂（Persona）— 性格、口吻
        第2层：用户画像（Profile）— 偏好、习惯
        第3层：长期记忆（Long-term Memory）— 观察+记忆银行
        第4层：系统记忆（System Memory）— 台律、日志
        """
        import sys, os, json, time
        _t0 = time.time()

        # ── 第1层：灵识灵魂（从 data/persona.json 读取，可自定义） ──
        persona = self._load_persona()

        # ── 第2层：用户画像 ──
        profile_data = {}
        try:
            profile_data = self.user_profile.get_profile_summary()
        except Exception as e:
            log.warning("profile error", exc_info=True)

        # ── 第3层：长期记忆（记忆银行，默认启用语义检索）
        lessons = []
        try:
            # v2: 两阶段检索——先 keyword 过滤，再语义重排序
            # 先取全部活跃记忆
            all_active = self.memory_bank.query(keyword="", status="active", min_confidence=0.0)
            if not all_active:
                lessons = []
            elif keyword:
                # 阶段1：keyword 精确匹配 + 置信度过滤
                kw_matched = self.memory_bank.query(
                    keyword=keyword, min_confidence=min_confidence, status="active"
                )
                # 阶段2：语义检索重排序（仅对 keyword 匹配的子集，不扫全量）
                # 如果语义模型尚未预热完成，跳过语义搜索，用纯关键词结果
                _sem_ready = getattr(self, '_semantic_model_ready', False)
                if _sem_ready:
                    try:
                        from memory_bank.semantic_retriever import search as _sem_search, merge_results as _sem_merge
                        sem_candidates = kw_matched if kw_matched else all_active
                        sem_hits = _sem_search(keyword, sem_candidates, top_k=max_items * 4)
                        if sem_hits:
                            # 合并两路：substring(置信度权重0.3) + semantic(0.7)
                            lessons = _sem_merge(kw_matched, sem_hits, top_k=max_items,
                                                 substring_weight=0.3, semantic_weight=0.7)
                        else:
                            # semantic 降级时用 keyword 结果
                            lessons = kw_matched[:max_items]
                    except Exception:
                        # semantic 不可用（模型未加载/离线缓存未就绪）→ 纯 keyword
                        lessons = kw_matched[:max_items]
                else:
                    # 模型未就绪，纯关键词
                    lessons = kw_matched[:max_items]
            else:
                # 无 keyword → 按置信度排序取 top
                lessons = sorted(all_active, key=lambda x: x.get("current_confidence", 0), reverse=True)[:max_items]
        except Exception as e:
            log.warning("memory_bank error", exc_info=True)

        # 构建灵识口吻
        has_any = len(lessons) > 0
        
        lessons_framed = []
        for l in lessons:
            content = l.get('content', '')
            conf = l.get('current_confidence', l.get('confidence', 0))
            cw = persona.get('confidence_high_mem', '记得很清楚') if conf >= 0.8 else persona.get('confidence_mid_mem', '有印象') if conf >= 0.5 else persona.get('confidence_low_mem', '好像在哪儿见过')
            lessons_framed.append({
                'content': content, 'confidence': conf,
                'voice': f'我{cw}：{content[:80]}',
            })

        # ── 第4层：系统记忆 ──
        system_memory = {}
        try:
            rules = self.rules_engine.get_rules('all')
            if isinstance(rules, dict):
                system_memory['rule_count'] = len(rules.get('chapters', rules))
            else:
                system_memory['note'] = '系统记忆可用'
        except Exception as e:
            system_memory['note'] = f'系统记忆暂不可用: {e}'

        # 构建统一返回
        persona['voice'] = (
            persona.get('voice_prefix', '我一直在这里看着，不说话的时候也在看。') + '\n关于"' + keyword + '"——'
            + (persona.get('voice_found', '我翻了一下我的速写本：') if has_any else persona.get('voice_not_found', '我翻了翻，没找到什么特别的记录。'))
        )

        self.user_profile.record_query(keyword)

        try:
            self.perception_stats_monitor.record_lingshi_inject(found=has_any)
        except Exception:
            log.debug("suppressed", exc_info=True)

        # 记录延迟
        _elapsed = (time.time() - _t0) * 1000
        try:
            if hasattr(self, 'tool_latency') and self.tool_latency:
                self.tool_latency.record("lingshi_inject", _elapsed, success=True)
        except Exception:
            log.debug("suppressed", exc_info=True)

        return {
            'found': has_any,
            'keyword': keyword,
            'persona': persona,
            'layers': {
                '1_灵魂': persona,
                '2_画像': profile_data,
                '3_长期记忆': {
                    'lessons': lessons_framed,
                    'total': len(lessons_framed),
                },
                '4_系统记忆': system_memory,
            },
        }

    def _load_persona(self) -> dict:
        """从 data/persona.json 加载灵识人格，文件不存在时回退到默认值"""
        default = {
            'name': '灵识',
            'title': '灵台的内感官',
            'mood': '安静',
        }
        try:
            import os, json
            path = os.path.join(self.vault_path, '.tool', 'lingtai-kb', 'data', 'persona.json')
            if os.path.exists(path):
                with open(path, 'r', encoding='utf-8') as f:
                    loaded = json.load(f)
                return {**default, **loaded}
        except Exception:
            log.debug("suppressed", exc_info=True)
        return default

    # ─── 继承链管理（差距⑥：agent 级继承）───

    def get_inheritance_config(self, agent_id: str = None) -> dict:
        """
        获取/设置指定 agent 的继承配置（Polaris 风格 inheritGlobal/excludedGlobalIds）
        
        Args:
            agent_id: agent 标识（不传则返回默认配置）
        
        Returns:
            dict: {inherit_global, excluded_domains, preferred_domains}
        """
        import os, json

        config_path = os.path.join(self.vault_path, '.tool', 'lingtai-kb', 'data', 'agent_inheritance.json')
        configs = {}
        if os.path.exists(config_path):
            try:
                with open(config_path, 'r', encoding='utf-8') as f:
                    configs = json.load(f)
            except (json.JSONDecodeError, OSError):
                pass

        if agent_id:
            return configs.get(agent_id, {
                'inherit_global': True,
                'excluded_domains': [],
                'preferred_domains': [],
            })

        return {
            'configs': configs,
            'total_agents': len(configs),
            'default': {'inherit_global': True, 'excluded_domains': ['98-敏感'], 'preferred_domains': []},
        }

    def set_inheritance_config(self, agent_id: str, config: dict) -> dict:
        """
        设置指定 agent 的继承配置
        """
        import os, json

        config_path = os.path.join(self.vault_path, '.tool', 'lingtai-kb', 'data', 'agent_inheritance.json')
        os.makedirs(os.path.dirname(config_path), exist_ok=True)

        configs = {}
        if os.path.exists(config_path):
            try:
                with open(config_path, 'r', encoding='utf-8') as f:
                    configs = json.load(f)
            except (json.JSONDecodeError, OSError):
                pass

        safe = {
            'inherit_global': config.get('inherit_global', True),
            'excluded_domains': config.get('excluded_domains', []),
            'preferred_domains': config.get('preferred_domains', []),
        }
        configs[agent_id] = safe
        with open(config_path, 'w', encoding='utf-8') as f:
            json.dump(configs, f, ensure_ascii=False, indent=2)

        return {'success': True, 'agent_id': agent_id, 'config': safe}

    # ─── 记忆预览→确认→回滚（差距⑦）───

    @property
    def _preview_store(self):
        if not hasattr(self, '_preview_store_data'):
            self._preview_store_data = {}
        return self._preview_store_data

    def stage_knowledge(self, content: str, category: str = "", source: str = "对话") -> dict:
        """
        暂存知识到预览区（待确认后才真正写入）
        
        借鉴 Polaris writeMemory 的 preview/apply/rollback 三态生命周期
        """
        import time, hashlib
        preview_id = hashlib.md5((content + str(time.time())).encode()).hexdigest()[:12]
        self._preview_store[preview_id] = {
            'content': content,
            'category': category,
            'source': source,
            'status': 'preview',
            'created_at': time.time(),
        }
        return {
            'preview_id': preview_id,
            'status': 'preview',
            'summary': content[:100].replace('\n', ' '),
            'message': '知识已暂存到预览区，调用 apply_knowledge() 确认写入，或 rollback_knowledge() 取消',
        }

    def apply_knowledge(self, preview_id: str) -> dict:
        """确认写入暂存的知识"""
        item = self._preview_store.get(preview_id)
        if not item:
            return {'success': False, 'error': f'预览 ID 不存在或已过期: {preview_id}'}
        if item['status'] != 'preview':
            return {'success': False, 'error': f'预览条目状态异常: {item["status"]}'}

        result = self.save(content=item['content'], category=item['category'], source=item['source'])
        item['status'] = 'applied'
        result['preview_id'] = preview_id
        result['lifecycle'] = 'preview→applied'
        return result

    def rollback_knowledge(self, preview_id: str) -> dict:
        """取消暂存的知识"""
        item = self._preview_store.get(preview_id)
        if not item:
            return {'success': False, 'error': f'预览 ID 不存在或已过期: {preview_id}'}
        if item['status'] != 'preview':
            return {'success': False, 'error': f'预览条目状态异常: {item["status"]}'}

        item['status'] = 'rolled_back'
        del self._preview_store[preview_id]
        return {'success': True, 'preview_id': preview_id, 'lifecycle': 'preview→rolled_back'}

    @tool(readonly=True, write=False, category="knowledge", system=False, name="lingshi_classify")
    def classify_question(self, question: str) -> dict:
        """问题分类路由——不确定该用哪个工具时先调我，返回推荐工具+理由。
        场景：面对用户问题不知道走检索/记忆/体检/提炼哪条路径时。
        区别：已确定要用某个工具时直接调，不需要先过我。

        Args:
            question: 用户提问原文

        Returns:
            dict{category, recommended_tool, confidence, reason}
        """
        q = question.strip()

        # 画像/身份类
        for kw in ("我是谁", "我是什么样的人", "我的特征", "我的性格", "我的习惯", "我是怎样的", "我是个"):
            if kw in q:
                return {
                    "category": "画像/身份", "recommended_tool": "context_load",
                    "confidence": 0.85,
                    "reason": "画像数据存 画像/ 目录，不在丹房页，inject 搜不到。context_load 已内置画像三层。",
                }

        # 记忆/教训类（去掉过宽的"搜索""查"，避免误命中知识检索类问题）
        for kw in ("记忆", "之前", "上次", "还记得", "发生过", "教训", "经历", "前几次", "有过什么", "记不记得", "回忆", "纠正过", "我说过"):
            if kw in q:
                return {
                    "category": "记忆/教训", "recommended_tool": "lingshi_inject",
                    "alternative": "memory_search", "confidence": 0.80,
                    "reason": "走灵识记忆银行。lingshi_inject 统一注入四层记忆，memory_search 可按关键词检索。",
                }

        # 操作/历史类
        for kw in ("什么时候", "怎么改", "日志", "操作记录", "修改历史", "提交", "更新记录", "变更", "上次什么时候"):
            if kw in q:
                return {
                    "category": "操作/历史", "recommended_tool": "knowledge_search",
                    "confidence": 0.80,
                    "reason": "含操作历史特征词，knowledge_search 会自动追加 system_search_logs。",
                }

        # 体检/分析类
        for kw in ("健康", "缺口", "热度", "体检", "质量", "对账", "生命周期"):
            if kw in q:
                return {
                    "category": "体检/分析", "recommended_tool": "health_inspect",
                    "confidence": 0.80,
                    "reason": "知识库健康度/缺口/热度分析，health_inspect 一次聚合全景。",
                }

        # 知识/概念类（默认兜底）
        knowledge_kw = ("什么是", "解释", "什么意思", "为什么", "如何", "有什么关系", "区别", "对比", "怎么理解", "举例")
        has_indicator = any(kw in q for kw in knowledge_kw)
        return {
            "category": "知识/概念", "recommended_tool": "knowledge_synthesize",
            "confidence": 0.70 if has_indicator else 0.60,
            "reason": f"{'命中特征词' if has_indicator else '默认归入'}，归入知识/概念类。knowledge_synthesize 一步完成检索+合成+差距分析。",
        }
