# -*- coding: utf-8 -*-
"""
灵台知识库 MCP Server v2
========================
模块化架构：按域拆分为 10 个 mixin，统一由 LingtaiMCPServer 继承。

与旧 mcp_server.py 完全兼容——所有工具名和接口签名不变。
"""
import os, sys, json, time, threading
from pathlib import Path

# 添加当前目录到路径
sys.path.insert(0, str(Path(__file__).resolve().parent))

from memory_engine import MemoryEngine
from auto_edge import AutoEdge
from perception import PerceptionTools
from kar_fusion import KARFusion
from perception_stats import PerceptionStats
from reasoning_engine import ReasoningEngine
from reflect_engine import ReflectEngine
from user_profile import UserProfile
from memory_bank import MemoryBank
from content_registry import ContentRegistry
from observation_engine import ObservationEngine
from check_point_engine import CheckPointEngine

from server_mixins.knowledge import KnowledgeMixin
from server_mixins.perception import PerceptionMixin
from server_mixins.perception_page import PageMixin
from server_mixins.perception_refine import RefineMixin
from server_mixins.observation import ObservationMixin
from server_mixins.user_profile import UserProfileMixin
from server_mixins.memory_bank import MemoryBankMixin
from server_mixins.kar import KARMixin
from server_mixins.llm import LLMMixin
from server_mixins.skillopt import SkillOptMixin
from server_mixins.system import SystemMixin
from server_mixins.output import OutputMixin
from server_mixins.check_point import CheckPointMixin
from agent_recommender import AgentRecommender
from degradation import check_mode
from tool_latency import ToolLatencyMonitor

from server_mixins.macros import MacroMixin
from server_mixins.external_tool_recommender import ExternalToolRecommenderMixin
from decorators import tool
from logger import get_logger

log = get_logger(__name__)

class LingtaiMCPServer(KnowledgeMixin, PerceptionMixin, PageMixin, RefineMixin,
                       ObservationMixin,
                       UserProfileMixin, MemoryBankMixin, KARMixin,
                       LLMMixin, SkillOptMixin, SystemMixin,
                       CheckPointMixin, OutputMixin, MacroMixin,
                       ExternalToolRecommenderMixin):
    """灵台知识库 MCP Server"""

    def __init__(self, vault_path: str = None):
        _vault = vault_path or os.environ.get("LINGTAI_VAULT", r".")
        self.vault_path = _vault

        self.memory = MemoryEngine(_vault)
        self.knowledge = self.memory
        self.registry = ContentRegistry(_vault)
        self.auto_edge = AutoEdge(_vault)
        self.perception = PerceptionTools(_vault, registry=self.registry)
        self.kar = KARFusion(_vault, memory=self.memory, auto_edge=self.auto_edge)
        self.perception_stats_monitor = PerceptionStats()
        self.user_profile = UserProfile(_vault)
        self.memory_bank = MemoryBank(_vault, registry=self.registry)
        self.observation = ObservationEngine(_vault)
        self.check_point_engine = CheckPointEngine(_vault)

        # ═══ 惰性加载引擎 ═══
        self._reasoning = None
        self._reflect_engine = None
        self._agent_recommender = None
        self._skillopt_loaded = False
        self._context_cache = None
        self._context_loaded = False
        # 端标识
        self.client = "unknown"
        self.client_version = None
        self.operator = None

        # 工具延迟监控
        self.tool_latency = ToolLatencyMonitor()

        # 后台预热外部 Skill 缓存（避免首次查询等待 30s）
        threading.Thread(target=self._warm_external_skill_cache, daemon=True).start()

        # 异步预热原料索引（性能优化 v3）：后台线程提前构建 _ensure_raw_index，
        # 使首次 search_raw 从 ~1.5s 降到 ~6ms。
        # 最坏情况（search_raw 是绝对首调用且索引未就绪）：search_raw 内部 lazy 兜底。
        try:
            self._raw_index_warmup_thread = threading.Thread(
                target=self.memory._ensure_raw_index, daemon=True
            )
            self._raw_index_warmup_thread.start()
        except Exception:
            self._raw_index_warmup_thread = None

        # 异步预热语义检索模型 + 预缓存全部记忆 embedding：
        # 后台线程提前加载 bge-small-zh-v1.5，不阻塞启动。
        # lingshi_inject/memory_search 首次调用时检查就绪状态，
        # 未就绪则跳过语义搜索，使用纯关键词模式。
        self._semantic_model_ready = False
        try:
            threading.Thread(target=self._warmup_semantic_model, daemon=True).start()
        except Exception:
            log.debug("suppressed", exc_info=True)

    # ═══ 惰性加载属性 ═══
    @property
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

    def set_client(self, name, version=None):
        """由 router 在 MCP initialize 时调用，写入当前调用端标识。"""
        if name:
            self.client = str(name)
        if version:
            self.client_version = str(version)

    def _warmup_semantic_model(self):
        """异步预热语义检索模型 + 预缓存全部记忆 embedding。
        完成后设置 _semantic_model_ready = True。"""
        try:
            from memory_bank.semantic_retriever import preload_model, ensure_cached
            loaded = preload_model()
            if loaded:
                # 预缓存全部活跃记忆 embedding，避免首次 lingshi_inject on-the-fly 计算
                all_active = self.memory_bank.query(keyword="", status="active", min_confidence=0.0)
                if all_active:
                    ensure_cached(all_active)
            self._semantic_model_ready = True
        except Exception as e:
            log.warning("warmup semantic model error", extra={"error": str(e)})
            self._semantic_model_ready = False

    def _detect_cross_end(self) -> dict:
        """轻量跨端活动检测：从 tool_sessions.jsonl 取调用次数+时间 +
        episodic.jsonl 取详细摘要。失败静默返回空 dict。"""
        import json, os
        current = getattr(self, 'client', 'unknown')
        others = {}

        # ── 源1: tool_sessions.jsonl（计数+时间+简单摘要）──
        logs = os.path.join(self.vault_path, ".tool", "lingtai-kb", "logs", "tool_sessions.jsonl")
        if os.path.isfile(logs):
            try:
                size = os.path.getsize(logs)
                chunk = min(size, 4096)
                with open(logs, 'r', encoding='utf-8') as f:
                    f.seek(max(0, size - chunk))
                    lines = f.read().splitlines()
                for line in lines[-60:]:
                    if not line.strip():
                        continue
                    try:
                        e = json.loads(line)
                        cli = e.get("client", "")
                        if not cli or cli == current:
                            continue
                        ts = e.get("timestamp", "")
                        if cli not in others:
                            others[cli] = {"sessions": 0, "last_active": ts, "summaries": []}
                        others[cli]["sessions"] += 1
                        if ts > others[cli]["last_active"]:
                            others[cli]["last_active"] = ts
                    except json.JSONDecodeError:
                        continue
            except Exception:
                log.debug("suppressed", exc_info=True)

        # ── 源2: episodic.jsonl（详细摘要，按 client 字段匹配）──
        epi = os.path.join(self.vault_path, ".tool", "lingtai-kb", "memory_bank", "data", "episodic.jsonl")
        if os.path.isfile(epi):
            try:
                size = os.path.getsize(epi)
                chunk = min(size, 4096)
                with open(epi, 'r', encoding='utf-8') as f:
                    f.seek(max(0, size - chunk))
                    lines = f.read().splitlines()
                for line in lines[-30:]:
                    if not line.strip():
                        continue
                    try:
                        e = json.loads(line)
                        cli = e.get("client", "")
                        if not cli or cli == current:
                            continue
                        sm = (e.get("summary") or "")[:80]
                        ts = e.get("timestamp", "")
                        if cli not in others:
                            others[cli] = {"sessions": 0, "last_active": ts, "summaries": []}
                            others[cli]["sessions"] = 0  # 源自 episodic，不计入 sessions 数
                        if sm and len(others[cli]["summaries"]) < 3:
                            others[cli]["summaries"].append(sm)
                    except json.JSONDecodeError:
                        continue
            except Exception:
                log.debug("suppressed", exc_info=True)

        if not others:
            return {}
        for cli, info in others.items():
            info["alert"] = f"{cli} 最近有 {info['sessions']} 次工具调用，最后活动于 {info['last_active'][:16]}"
        return others
    def ensure_context(self, client_capabilities=None, detail="detailed", client_id=None, operator=None, ambient=False) -> dict:
        """懒加载上下文——首次调任何感知工具时静默触发，结果缓存供 context_load 快速返回。
        client_capabilities: 可选能力清单（握手声明），传入时强制重算以纳入能力集。
        ambient: 是否注入『主动推送』块(topics_of_interest + knowledge_pulse)。
                默认 False = 精简(每会话必注入的 context_load 不含这两块，
                约省 1.2K 字符)；True = 按需获取(灵识想感知知识库动态/热点时显式请求)。

        v8 优化：warm-start——首次成功加载后将 _context_cache 落盘到
        .tool/lingtai-kb/cache/context_cache.json（带源文件 mtime 失效戳），下次进程启动
        首次 context_load 时若源未变更则直接加载，跳过昂贵的首次全量计算
        （原痛点：首次加载耗时过长导致 MCP 插件超时断连）。"""
        need_full = (client_capabilities is not None) or (detail and detail != "detailed")

        # 显式端标识覆盖（context_load 的 client_id / operator 参数）
        if client_id:
            self.set_client(client_id)
        if operator:
            self.operator = operator

        # 已加载且无需全量 → 直接返回缓存（确保带当前端标识，覆盖同会话换端场景）
        if self._context_loaded and not need_full:
            self._context_cache["client"] = self.client
            # 跨端检测（动态注入，不污染缓存本体）
            ce = self._detect_cross_end()
            if ce:
                result = dict(self._context_cache)
                result["cross_end"] = ce
                if ambient:
                    self._inject_ambient(result, True)
                return result
            if ambient:
                # 按需副本：缓存本体保持 lean，绝不污染 self._context_cache
                result = dict(self._context_cache)
                self._inject_ambient(result, True)
                return result
            return self._context_cache

        # 需要带能力集/裁剪重算 → 清除缓存标志，强制 context() 走全量
        if self._context_loaded and need_full:
            self._context_loaded = False
            self._context_cache = None

        # 冷路径（首次 或 刚清除）：尝试 warm-start 落盘缓存
        if not self._context_loaded:
            warm = self._load_context_cache()
            if warm is not None:
                mode = check_mode(self.vault_path)
                if "layers" in warm:
                    warm["layers"]["mode"] = mode
                else:
                    warm["layers"] = {"mode": mode}
                # 重新注入运行时能力集（warm 缓存不含上次的 capabilities）
                warm["capabilities"] = self._resolve_capabilities(client_capabilities)
                # scene 必须动态重算：warm 缓存里的 scene 是上次会话落盘的，
                # 跨端/跨场景切换时会过时，导致 context_load 的 scene 分支与
                # memory_write/search 的默认 branch 错位。重算仅依赖工作印记
                # + 教训文本（已在 warm 中），纯 CPU 毫秒级，无额外 I/O。
                try:
                    _lyr = warm.get("layers", {})
                    _wi = _lyr.get("工作印记") or {}
                    _les = _lyr.get("灵识记忆", {}).get("lessons", [])
                    warm["scene"] = self._detect_current_scene({}, _les, _wi, {})
                except Exception:
                    log.debug("suppressed", exc_info=True)
                warm["client"] = self.client
                # 缓存本体保持 lean：先剥离 ambient 块(warm 缓存可能带旧版)
                self._inject_ambient(warm, False)
                self._context_cache = warm
                self._context_loaded = True
                if not need_full:
                    self._write_heartbeat()
                    self._context_cache["pending_consumed"] = self._pending_consumed
                    ce = self._detect_cross_end()
                    if ambient:
                        # 按需副本：注入 ambient 块但不污染缓存本体
                        result = dict(warm)
                        self._inject_ambient(result, True)
                        if ce:
                            result["cross_end"] = ce
                        return result
                    if ce:
                        result = dict(warm)
                        result["cross_end"] = ce
                        return result
                    return warm
                # need_full：warm 已注入基础数据，但仍需 context() 带能力重算
                self._context_loaded = False
                self._context_cache = None

        # 全量计算（首次冷启动 / 源已变更 / 带能力重算）
        log.info("lazy-loading context (first tool call)")
        try:
            raw = self.context(client_capabilities=client_capabilities)
            # 注入降级模式标记
            mode = check_mode(self.vault_path)
            if "layers" in raw:
                raw["layers"]["mode"] = mode
            else:
                raw["layers"] = {"mode": mode}
            raw["client"] = self.client
            self._context_cache = raw
            self._context_loaded = True
            # 仅当首次成功加载全量（无能力声明、detailed）时落盘，供下次 warm-start
            # raw 此时为 lean(未注入 ambient)，落盘缓存保持精简
            if client_capabilities is None and (not detail or detail == "detailed"):
                self._save_context_cache(raw)
            self._write_heartbeat()
            self._context_cache["pending_consumed"] = self._pending_consumed
            ce = self._detect_cross_end()
            if ambient:
                # 按需副本：注入 ambient 块但不污染缓存本体与落盘
                result = dict(raw)
                self._inject_ambient(result, True)
                if ce:
                    result["cross_end"] = ce
                return result
            if ce:
                result = dict(raw)
                result["cross_end"] = ce
                return result
            return raw
        except Exception as e:
            log.error("context() crashed", extra={"error": str(e)}, exc_info=True)
            sys.stderr.flush()
            # 回退空 dict，不让 server 崩溃
            fallback = {"greeting": "", "message": "context 加载失败", "scene": "通用", "layers": {}}
            fallback["client"] = self.client
            # 错误路径也尝试跨端检测
            ce = self._detect_cross_end()
            if ce:
                fallback["cross_end"] = ce
            self._context_cache = fallback
            self._context_loaded = True
            return fallback

    # ─── 自动心跳 + 补跑工作印记（context_load 首次调用时触发）───
    def _write_heartbeat(self):
        """自动写心跳 + 消费积压工作印记——每次新会话首次 context_load 触发，不依赖 AI 自觉。
        巡更 MCP 断线时积压的工作印记通过 pending_imprints.jsonl 在此补跑写入。"""
        # 1. 心跳
        try:
            from datetime import datetime
            self.memory_bank.write(
                content=f"工作印记：[auto] 客户端:{self.client} 活跃时间:{datetime.now().strftime('%m-%d %H:%M')}",
                source_type="mcp",
                tags=["协作者-工作印记", "heartbeat"],
                branch="通用",
            )
        except Exception:
            log.debug("suppressed", exc_info=True)
        # 2. 消费积压工作印记（独立异常处理，不因消费失败丢心跳）
        self._pending_consumed = self._consume_pending_imprints()

    def _consume_pending_imprints(self) -> dict:
        """消费 pending_imprints.jsonl 中的积压工作印记，逐条写入 memory_bank。
        写入成功则删除对应条目，失败则保留（下次重试）。

        Returns:
            dict: {"consumed": N, "failed": M, "details": [...]}
        """
        pending_path = os.path.join(self.vault_path, ".tool", "lingtai-kb", "cache", "pending_imprints.jsonl")
        result = {"consumed": 0, "failed": 0, "details": []}
        if not os.path.exists(pending_path):
            return result
        try:
            with open(pending_path, "r", encoding="utf-8") as f:
                lines = f.readlines()
            remaining = []
            for line in lines:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    self.memory_bank.write(
                        content=entry.get("content", ""),
                        source_type=entry.get("source_type", "巡更"),
                        tags=entry.get("tags", []),
                        branch=entry.get("branch", "通用"),
                    )
                    result["consumed"] += 1
                    result["details"].append({"status": "ok", "content": entry.get("content", "")[:80]})
                except Exception as e:
                    result["failed"] += 1
                    result["details"].append({"status": "error", "error": str(e)[:120], "content": entry.get("content", "")[:80]})
                    remaining.append(line + "\n")
            if remaining:
                with open(pending_path, "w", encoding="utf-8") as f:
                    f.writelines(remaining)
            else:
                os.remove(pending_path)
        except Exception as e:
            result["failed"] = len(lines) if 'lines' in dir() else 0
            result["details"].append({"status": "fatal", "error": str(e)[:120]})
        return result

    # ─── topics_of_interest（知识库热点预计算，辅助 AI 决策是否调 inject）───
    def _compute_topics_of_interest(self) -> dict:
        """从 index.json 统计知识库热点域和活跃页

        扩展 v2：增加 today_new（今日新增）+ recent_updated（48h内更新页）

        Returns:
            dict: {"top_domains": [...], "hot_pages": [...], "domain_stats": {...},
                   "today_new": [...], "recent_updated": [...]}
            出错时返回空结构。
        """
        try:
            pages = getattr(self.memory, 'pages', None) or []
            if not pages:
                return {"top_domains": [], "hot_pages": [], "domain_stats": {},
                        "today_new": [], "recent_updated": []}

            from datetime import datetime, timedelta
            today = datetime.now().strftime("%Y-%m-%d")
            two_days_ago = (datetime.now() - timedelta(hours=48)).strftime("%Y-%m-%d")

            # 按域聚合
            domain_counts = {}
            for p in pages:
                d = p.get("domain", "未分类")
                domain_counts[d] = domain_counts.get(d, 0) + 1

            top_domains = sorted(domain_counts.items(), key=lambda x: -x[1])[:5]
            all_domains = sorted(domain_counts.items(), key=lambda x: -x[1])

            # 按 date 取最近活跃页（取 Top 5，无 date 的排最后）
            dated = [p for p in pages if p.get("date")]
            dated.sort(key=lambda p: p.get("date", ""), reverse=True)
            hot_pages = [
                {"title": p.get("title", ""), "domain": p.get("domain", ""),
                 "date": p.get("date", "")}
                for p in dated[:5]
            ]

            # 今日新增
            today_new = [
                {"title": p.get("title", ""), "domain": p.get("domain", "")}
                for p in dated if p.get("date", "") == today
            ]

            # 48h内更新（排除今日新增，避免重复）
            recent_updated = [
                {"title": p.get("title", ""), "domain": p.get("domain", ""),
                 "date": p.get("date", "")}
                for p in dated
                if two_days_ago <= p.get("date", "") < today
            ][:5]

            return {
                "top_domains": [
                    {"domain": d, "page_count": c} for d, c in top_domains
                ],
                "hot_pages": hot_pages,
                "domain_stats": {d: c for d, c in all_domains},
                "today_new": today_new,
                "recent_updated": recent_updated,
            }
        except Exception:
            return {"top_domains": [], "hot_pages": [], "domain_stats": {},
                    "today_new": [], "recent_updated": []}

    def _compute_knowledge_pulse(self) -> dict:
        """知识库脉搏：轻量摘要供灵识感知知识库近期动态

        返回今日新增页数+标题、48h更新页数、全局热度前5域。
        纯轻量计算，复用已缓存的 pages 数据（O(n)）。
        失败时回退空结构。
        """
        try:
            toi = self._compute_topics_of_interest()
            today_new_count = len(toi.get("today_new", []))
            recent_count = len(toi.get("recent_updated", []))
            return {
                "today_new": toi.get("today_new", []),
                "today_new_count": today_new_count,
                "recent_updated": toi.get("recent_updated", []),
                "recent_updated_count": recent_count,
                "top_domains": toi.get("top_domains", []),
                "summary": {
                    "new_today": today_new_count,
                    "updated_recently": recent_count,
                    "total_domains": len(toi.get("domain_stats", {})),
                }
            }
        except Exception:
            return {"today_new": [], "today_new_count": 0,
                    "recent_updated": [], "recent_updated_count": 0,
                    "top_domains": [], "summary": {}}

    # ─── ambient 块按需注入/剥离（topics_of_interest + knowledge_pulse）───
    def _inject_ambient(self, ctx: dict, ambient: bool):
        """统一控制『主动推送』块(topics_of_interest + knowledge_pulse)的注入与剥离。

        ambient=True  → 计算并注入(供灵识感知知识库动态/热点，按需显式获取)
        ambient=False → 剥离(保证默认 context_load 精简，缓存本体始终 lean)
        任何路径调用本方法都不会留下半成品字段。
        """
        if ambient:
            ctx["topics_of_interest"] = self._compute_topics_of_interest()
            ctx["knowledge_pulse"] = self._compute_knowledge_pulse()
        else:
            ctx.pop("topics_of_interest", None)
            ctx.pop("knowledge_pulse", None)

    # ─── context 落盘缓存（warm-start）───
    def _context_cache_path(self) -> str:
        return os.path.join(os.path.dirname(os.path.abspath(__file__)), "cache", "context_cache.json")

    def _context_cache_stamp(self) -> float:
        """失效键：context 依赖的关键源文件最大 mtime。任一源变更 → 缓存失效重算。"""
        vault = self.vault_path
        sources = [
            os.path.join(vault, "丹房", ".meta", "index.json"),
            os.path.join(vault, "画像", "履历.md"),
            os.path.join(vault, "画像", "心性.md"),
            os.path.join(vault, "画像", "我是谁.md"),
            os.path.join(vault, ".tool", "lingtai-kb", "memory_bank", "data", "memories.json"),
            os.path.join(vault, "丹房", "00-思考与认知", "协作者约束集.md"),
            os.path.join(vault, ".workbuddy", "memory", "MEMORY.md"),
        ]
        stamps = []
        for s in sources:
            try:
                stamps.append(os.path.getmtime(s))
            except OSError:
                pass
        return round(max(stamps), 3) if stamps else 0.0

    def _load_context_cache(self):
        import json as _json
        try:
            path = self._context_cache_path()
            if not os.path.isfile(path):
                return None
            with open(path, "r", encoding="utf-8") as f:
                blob = _json.load(f)
            if blob.get("version_stamp") != self._context_cache_stamp():
                return None  # 源已变更 → 丢弃，走全量重算
            ctx = blob.get("context")
            if not isinstance(ctx, dict):
                return None
            return ctx
        except Exception:
            return None

    def _save_context_cache(self, ctx: dict):
        import json as _json
        try:
            path = self._context_cache_path()
            os.makedirs(os.path.dirname(path), exist_ok=True)
            blob = {"version_stamp": self._context_cache_stamp(), "context": ctx}
            # 临时写 + 原子 rename，避免半截文件损坏
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                _json.dump(blob, f, ensure_ascii=False)
            os.replace(tmp, path)
        except Exception:
            log.debug("suppressed", exc_info=True)

    def _ensure_skillopt(self):
        """延迟加载 skillopt 引擎（首次调用 skillopt_* 工具时触发）"""
        if self._skillopt_loaded:
            return
        from skillopt.evolve_engine import EvolveEngine
        from skillopt.stager import Stager
        from skillopt.pattern_detector import PatternDetector
        from skillopt.confidence_scorer import ConfidenceScorer
        self.skillopt_engine = EvolveEngine(self.vault_path)
        self.skillopt_stager = Stager()
        self.skillopt_detector = PatternDetector()
        self.skillopt_scorer = ConfidenceScorer()
        self._skillopt_loaded = True
    @tool(readonly=True, write=False, category="agent", system=False)
    def agent_recommend(self, top_n: int = 3) -> dict:
        """根据当前上下文推荐最匹配的技能/工具（基于任务语义匹配）。
        场景：不确定该用哪个技能模板、想要智能推荐时。
        区别：列出全部可用技能用 agent_skills；反馈推荐结果用 agent_feedback。"""
        return self.agent_recommender.recommend(top_n=top_n)
    @tool(readonly=False, write=True, category="agent", system=False)
    def agent_feedback(self, skill_id: str, action: str = "used", mode: str = "", note: str = "") -> dict:
        """反馈"AI推荐了某个技能，我用了或拒了"（优化后续推荐准确率）。
        场景：回应 agent_recommend 的推荐结果后，告知系统采纳/拒绝。
        区别：用户纠正/确认偏好用 user_feedback；对记忆条目做采纳/否决用 memory_feedback。"""
        return self.agent_recommender.record_feedback(skill_id=skill_id, action=action, mode=mode, note=note)
    @tool(readonly=True, write=False, category="agent", system=False)
    def agent_skills(self, mode: str = "") -> dict:
        """列出所有注册技能模板（含模板库+外部参考）。
        场景：想浏览全部可用技能、查找某个技能是否存在时。
        区别：要智能推荐最匹配的用 agent_recommend；列出 MCP 工具 SOP 用 system_sop。"""
        return self.agent_recommender.list_all_skills(mode=mode or None)
    @tool(readonly=True, write=False, category="system", system=True)
    def cross_end_activity(self, hours: int = 24, limit: int = 20) -> dict:
        """跨端活跃概览——聚合 logs/tool_sessions.jsonl，按端(client)分组展示近期活动，
        支撑多端并发协同推进的可视化。每条记录已由 session_tracker 打上端标识。"""
        import json as _json
        from datetime import datetime, timedelta
        log_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs", "tool_sessions.jsonl")
        if not os.path.isfile(log_path):
            return {"ok": True, "ends": [], "message": "无会话记录", "current_client": self.client}
        cutoff = datetime.now() - timedelta(hours=hours)
        ends = {}
        try:
            with open(log_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = _json.loads(line)
                    except Exception:
                        continue
                    ts = rec.get("timestamp", "")
                    try:
                        dt = datetime.fromisoformat(ts)
                    except Exception:
                        dt = None
                    if dt is None or dt < cutoff:
                        continue
                    client = rec.get("client") or "unknown"
                    agg = ends.setdefault(client, {"client": client, "sessions": 0, "calls": 0,
                                                  "last_active": ts, "_last_dt": dt, "tools": {}})
                    agg["sessions"] += 1
                    for c in rec.get("tool_calls", []):
                        t = c.get("name", "?")
                        agg["tools"][t] = agg["tools"].get(t, 0) + 1
                    agg["calls"] = sum(agg["tools"].values())
                    if dt > agg["_last_dt"]:
                        agg["_last_dt"] = dt
                        agg["last_active"] = ts
        except OSError:
            return {"ok": False, "error": "read_failed", "current_client": self.client}
        result = []
        for agg in ends.values():
            agg.pop("_last_dt", None)
            top = sorted(agg["tools"].items(), key=lambda x: -x[1])[:5]
            agg["top_tools"] = [{"tool": t, "count": n} for t, n in top]
            del agg["tools"]
            result.append(agg)
        result.sort(key=lambda x: x.get("last_active", ""), reverse=True)
        return {"ok": True, "window_hours": hours, "ends": result[:limit], "current_client": self.client}
