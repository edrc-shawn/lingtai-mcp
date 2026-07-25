# -*- coding: utf-8 -*-
"""
evolve_engine.py — 进化调度

设计：harvest → mine → replay → consolidate → stage

Sleep 周期（由 WorkBuddy 每日 03:00 调度）：
  1. harvest   — 从 observation_engine 收割新增观察
                从 hebbian_weights 获取近期共现变化
                从 search_logs 获取近期会话摘要
  2. mine      — pattern_detector 对收割数据进行模式挖掘
  3. replay    — replay_validator 对候选规则做历史回验
  4. consolidate — confidence_scorer 做自信定级
  5. stage     — stager 写入 staged/ 目录

独立 run() 入口，也可按阶段运行（用于调试/手动触发）。
"""

import os
import json
import re as _re
import sys
from datetime import datetime, date
from typing import Optional

VAULT_PATH = os.environ.get("LINGTAI_VAULT", r".")
SKILLOPT_DIR = os.path.join(os.path.dirname(__file__))

# 🟢 自动采纳护栏：🟢 规则需连续 N 次 run 都出现为候选，才自动写入感知规则.md。
# 否则仅暂存 staged/ 待人类晨间 review。防止单次 harvest 异常（如某次会话疯狂重复调用）
# 把低价值/错误规则在无人值守时固化进活文件。护栏计数文件：adoption_guard.json
ADOPT_MIN_CONSECUTIVE = 3
GUARD_PATH = os.path.join(SKILLOPT_DIR, "adoption_guard.json")

# 🟢 自动采纳类型白名单：仅"有失败/缺口后果"的模式类型可走护栏自动采纳；
# 纯频率类（tool_sequence 合并重复调用）即使 🟢 也只留 staged/ 待人类 review，不自动写入活文件。
# 信号质量排序：knowledge_gap(知识缺口) > dead_end(交互卡顿) > cooccurrence(强共现) >> tool_sequence(纯频率)
AUTO_ADOPT_TYPES = {"dead_end", "knowledge_gap", "cooccurrence"}

# 留出集比例：按时间把会话切分为训练集(旧)/留出集(新)，回验只用留出集，避免"同份数据挖了又验"的循环自证
VAL_RATIO = 0.3

# 阶段级错误 → 人类可操作建议（mcp-design G3：错误要教育性，给下一步）。
# 睡眠进化是无人值守跑的，错误日志是给人早晨 review 看的，必须说明"出了什么、怎么修"。
_ERROR_HINTS = {
    "mine_pattern_error": "模式检测器异常：检查 PatternDetector/RuleCandidate 是否导入成功、raw_data 结构是否变化。本次跳过模式泳道，不影响教训泳道。",
    "write_lessons_error": "记忆银行导入失败：检查 memory_bank 模块路径与 vault_path 是否正确、.tool/lingtai-kb 是否已加入 sys.path。教训未写入。",
    "auto_adopt_error": "规则写入感知规则.md 失败：检查感知规则.md 是否被占用/权限、Rxx 编号是否冲突、_is_valid 去重是否误判。本次规则未采纳，留 staged 待手动。",
    "mem_decay_error": "记忆银行衰减失败：检查 memory_bank/decay 模块导入与 memory 数据目录读写权限。衰减本轮未执行，下夜会重试。",
}


def _split_holdout(sessions: list, val_ratio: float = VAL_RATIO):
    """
    按时间戳把会话切分为训练集(旧)与留出集(新)。

    留出集用于回验候选规则（验证"在挖掘时没见过的新会话里，这条规律是否还成立"），
    避免原版 gate 批评的"在同一份数据上既挖掘又验证"的循环自证。

    无时间戳的条目归入训练集（不参与留出）。时间戳不足 5 条时不切分（留出集为空，回验退回全量）。
    """
    ts_sessions = [s for s in sessions if isinstance(s, dict) and s.get("timestamp")]
    no_ts = [s for s in sessions if not (isinstance(s, dict) and s.get("timestamp"))]
    if len(ts_sessions) < 5:
        return ts_sessions + no_ts, []
    ts_sessions.sort(key=lambda s: str(s.get("timestamp", "")))
    n_val = max(1, int(len(ts_sessions) * val_ratio))
    val = ts_sessions[-n_val:]
    train = ts_sessions[:-n_val] + no_ts
    return train, val


def _ts_gap(ts1: str, ts2: str) -> int | None:
    """计算两个 ISO 时间戳的秒数差。任一解析失败返回 None。"""
    try:
        t1 = datetime.fromisoformat(ts1)
        t2 = datetime.fromisoformat(ts2)
        return abs(int((t2 - t1).total_seconds()))
    except (ValueError, TypeError):
        return None


# 动态导入子模块，避免启动时循环依赖
def _import_module(module_name, class_name):
    try:
        mod = __import__(f"skillopt.{module_name}", fromlist=[class_name])
        return getattr(mod, class_name)
    except (ImportError, AttributeError):
        return None

# ── 阶段枚举 ──────────────────────────────────────────────
HARVEST = "harvest"
RECALL = "recall"
MINE = "mine"
WRITE_LESSONS = "write_lessons"
REPLAY = "replay"
DREAM = "dream"
GATE = "gate"
COLLIDE = "collide"
CONSOLIDATE = "consolidate"
STAGE = "stage"
ALL_PHASES = [HARVEST, RECALL, MINE, WRITE_LESSONS, REPLAY, DREAM, CONSOLIDATE, GATE, COLLIDE, STAGE]


class EvolveEngine:
    """进化调度引擎：协调五个阶段，产出 staged 规则。"""

    def __init__(self, vault_path: str = VAULT_PATH):
        self.vault_path = vault_path
        self.log: list[dict] = []
        self.raw_data: dict = {}
        self.patterns: list[dict] = []
        self.candidates: list[dict] = []
        self.validated: list[dict] = []
        self.scored: list[dict] = []
        self.lesson_candidates: list[dict] = []
        self.success_templates: list[dict] = []

        # 动态实例化子模块
        PatternDetector = _import_module("pattern_detector", "PatternDetector")
        self.pattern_detector = PatternDetector() if PatternDetector else None

        RuleCandidate = _import_module("rule_candidate", "RuleCandidate")
        self.rule_candidate = RuleCandidate() if RuleCandidate else None

        ReplayValidator = _import_module("replay_validator", "ReplayValidator")
        self.replay_validator = ReplayValidator() if ReplayValidator else None

        ConfidenceScorer = _import_module("confidence_scorer", "ConfidenceScorer")
        self.confidence_scorer = ConfidenceScorer() if ConfidenceScorer else None

        Stager = _import_module("stager", "Stager")
        self.stager = Stager() if Stager else None

    # ── 错误日志助手（G3：错误要教育性，给下一步） ──────────
    def _log_err(self, event: str, exc, extra: dict = None):
        """记录阶段级错误，并附人类可操作的"下一步建议"（见模块级 _ERROR_HINTS）。"""
        entry = {"event": event, "error": str(exc)}
        if event in _ERROR_HINTS:
            entry["action"] = _ERROR_HINTS[event]
        if extra:
            entry.update(extra)
        self.log.append(entry)

    # ── 公共入口 ──────────────────────────────────────────

    def run(self, phases: Optional[list[str]] = None) -> dict:
        """
        运行进化轮次。

        Args:
            phases: 指定阶段列表，默认执行全部 5 阶段。

        Returns:
            dict: 本轮进化摘要
        """
        if phases is None:
            phases = ALL_PHASES

        today = date.today().isoformat()
        self.log.append({"event": "run_start", "phases": phases, "date": today})

        if HARVEST in phases:
            self._harvest()

        if RECALL in phases:
            self._recall()

        if MINE in phases:
            self._mine()

        if WRITE_LESSONS in phases:
            self._write_lessons()

        if REPLAY in phases:
            self._replay()

        if DREAM in phases:
            self._dream()

        if CONSOLIDATE in phases:
            self._consolidate()

        if GATE in phases:
            self._gate()

        if COLLIDE in phases:
            self._collide()

        if STAGE in phases:
            self._stage()

        # memory_bank 衰减调度（与 SkillOpt 同周期）
        self._mem_decay()

        self.log.append({"event": "run_end", "date": today})

        return self._summary()

    def dry_run(self) -> dict:
        """预览：仅执行 harvest + mine + write_lessons，不 stage。"""
        return self.run(phases=[HARVEST, MINE, WRITE_LESSONS])

    # ── 各阶段 ────────────────────────────────────────────

    def _harvest(self):
        """
        收割阶段：
        - 收集灵识统计数据
        - 读取观察层近期产出
        - 读取日志中的工具调用记录
        """
        self.log.append({"event": "harvest_start"})

        observations = []
        hebbian_changes = []
        session_summaries = []
        gaps = []

        # ━━ 1. 从 observation_engine 存储读取 ━━━━━━━━━━━━━━━━━━━━
        obs_dir = os.path.join(VAULT_PATH, ".tool", "lingtai-kb", "observation")
        obs_store = os.path.join(obs_dir, "observations.json")
        pending_store = os.path.join(obs_dir, "pending.json")

        # 1a. 读取已归纳的观察
        if os.path.exists(obs_store):
            try:
                with open(obs_store, "r", encoding="utf-8") as f:
                    data = json.load(f)
                for obs in data.get("observations", []):
                    observations.append({
                        "source": "observation_engine",
                        "topic": obs.get("topic", ""),
                        "facts": obs.get("facts", []),
                        "confidence": obs.get("confidence", 0.0),
                        "summary": obs.get("summary", ""),
                        "updated_at": obs.get("updated_at", ""),
                    })
            except (json.JSONDecodeError, OSError, IOError):
                pass

        # 1b. 读取待处理积累槽
        if os.path.exists(pending_store):
            try:
                with open(pending_store, "r", encoding="utf-8") as f:
                    pending = json.load(f)
                for topic, slot in pending.items():
                    observations.append({
                        "source": "observation_engine_pending",
                        "topic": slot.get("topic", topic),
                        "facts": slot.get("facts", []),
                        "confidence": 0.3,
                        "summary": "",
                        "updated_at": slot.get("created_at", ""),
                    })
            except (json.JSONDecodeError, OSError, IOError):
                pass

        # 1c. 尝试用 MCP 接口查 Hebbian 统计（stats 目录不存在时跳过）
        stats_dir = os.path.join(VAULT_PATH, ".tool", "lingtai-kb", "stats")
        if os.path.isdir(stats_dir):
            for sf in sorted(os.listdir(stats_dir), reverse=True)[:3]:
                sf_path = os.path.join(stats_dir, sf)
                try:
                    with open(sf_path, "r", encoding="utf-8") as f:
                        stats = json.load(f)
                        if isinstance(stats, dict):
                            heb = stats.get("hebbian_changes", [])
                            if isinstance(heb, list):
                                hebbian_changes.extend(heb)
                except (json.JSONDecodeError, OSError, IOError):
                    continue

        # 1d. 读取灵识日志（logs/ 目录）
        logs_dir = os.path.join(VAULT_PATH, ".tool", "lingtai-kb", "logs")
        if os.path.isdir(logs_dir):
            log_files = sorted(os.listdir(logs_dir), reverse=True)[:5]
            for lf in log_files:
                lf_path = os.path.join(logs_dir, lf)
                if lf_path.endswith(".jsonl"):
                    try:
                        with open(lf_path, "r", encoding="utf-8") as f:
                            for line in f:
                                try:
                                    entry = json.loads(line.strip())
                                    if "tavily" in str(entry).lower():
                                        continue
                                    session_summaries.append(entry)
                                except json.JSONDecodeError:
                                    continue
                    except (OSError, IOError):
                        continue

        # 1e. 按时间窗口聚合工具调用（tool_sessions.jsonl 每条只记 1 次调用，
        #      session_id 是 uuid4 随机，不反映真实会话边界。
        #      以 5 分钟窗口聚合为虚拟会话，供 pattern_detector 挖序列。）
        SESSION_WINDOW_SEC = 300  # 5 分钟
        tool_entries = [e for e in session_summaries
                        if isinstance(e, dict) and e.get("tool_calls")]
        non_tool_entries = [e for e in session_summaries
                            if not (isinstance(e, dict) and e.get("tool_calls"))]
        if tool_entries:
            # 按时间戳排序
            tool_entries.sort(key=lambda e: str(e.get("timestamp", "")))
            aggregated = []
            current = None
            for e in tool_entries:
                ts = e.get("timestamp", "")
                if current is None:
                    current = dict(e)
                    current["_entries"] = [e]
                    continue
                # 检查时间差
                prev_ts = current.get("timestamp", "")
                gap = _ts_gap(prev_ts, ts)
                if gap is not None and gap <= SESSION_WINDOW_SEC:
                    # 合并：追加 tool_calls，更新 summary
                    current["tool_calls"].extend(e.get("tool_calls", []))
                    current["total_data_chars"] = current.get("total_data_chars", 0) + e.get("total_data_chars", 0)
                    current["timestamp"] = ts  # 取最新时间
                    current["summary"] = f"{len(current['tool_calls'])} 次工具调用（聚合）"
                    current["_entries"].append(e)
                else:
                    # 新会话
                    aggregated.append(current)
                    current = dict(e)
                    current["_entries"] = [e]
            if current:
                aggregated.append(current)
            # 清理临时字段
            for a in aggregated:
                del a["_entries"]
            session_summaries = aggregated + non_tool_entries

        # ━━ 2. 从画像中读取纠正记录 ━━━━━━━━━━━━━━━━━━━
        corrections = []
        profile_path = os.path.join(VAULT_PATH, ".tool", "lingtai-kb", "profile", "user_profile.json")
        if os.path.exists(profile_path):
            try:
                with open(profile_path, "r", encoding="utf-8") as f:
                    profile = json.load(f)
                corrections = profile.get("corrections", [])
            except (json.JSONDecodeError, OSError, IOError):
                pass

        # 3. 读取对账缺口
        duizhang_path = os.path.join(VAULT_PATH, "体检", "系统", "对账.md")
        if os.path.exists(duizhang_path):
            try:
                with open(duizhang_path, "r", encoding="utf-8") as f:
                    content = f.read()
                # 提取 ⏳ 状态缺口
                import re as _re
                gap_lines = _re.findall(r"\|[^|]*⏳[^|]*\|", content)
                for gl in gap_lines:
                    parts = [p.strip() for p in gl.split("|") if p.strip()]
                    if len(parts) >= 2:
                        gaps.append({
                            "source": "对账.md",
                            "description": parts[1],
                            "detail": gl.strip(),
                        })
            except OSError:
                pass

        # 4. 读取搜索统计（search_stats.jsonl，由 _log_search_stats 写入）
        search_stats_path = os.path.join(VAULT_PATH, ".tool", "lingtai-kb", "logs", "search_stats.jsonl")
        search_stats = []
        if os.path.exists(search_stats_path):
            try:
                with open(search_stats_path, "r", encoding="utf-8") as f:
                    for line in f:
                        try:
                            entry = json.loads(line.strip())
                            search_stats.append(entry)
                        except json.JSONDecodeError:
                            continue
                # 只取最近 200 条
                search_stats = search_stats[-200:]
            except (OSError, IOError):
                pass

        # ━━ 留出集切分（P2）：按时间把会话拆 train/val，回验只用留出集，避免循环自证 ━━
        holdout_all = session_summaries[:50]
        train_sessions, val_sessions = _split_holdout(holdout_all, VAL_RATIO)

        self.raw_data = {
            "observations": observations,
            "hebbian_changes": hebbian_changes,
            "session_summaries": train_sessions,   # 挖掘用训练集（旧数据）
            "val_sessions": val_sessions,           # 回验用留出集（新数据，挖掘时未见）
            "search_stats": search_stats,           # 搜索模式统计（供 pattern_detector 分析模式偏好）
            "gaps": gaps,
            "corrections": corrections,
            "harvested_at": datetime.now().isoformat(),
        }

        self.log.append({
            "event": "harvest_end",
            "observations": len(observations),
            "hebbian": len(hebbian_changes),
            "sessions": len(holdout_all),
            "train_sessions": len(train_sessions),
            "val_sessions": len(val_sessions),
            "search_stats": len(search_stats),
            "gaps": len(gaps),
            "corrections": len(corrections),
        })

    def _recall(self, k: int = 20):
        """Associative recall — 从记忆银行召回与当前 session 相关的历史记忆，加入训练池。

        Jaccard token 重叠匹配，与微软 SkillOpt-Sleep 的 recall_similar() 相同思路。
        召回的条目以 recalled_memory 源类型注入 observations，供 _mine 阶段处理。

        Args:
            k: 最大召回数（默认 20，微软实验最优值）
        """
        self.log.append({"event": "recall_start", "k": k})
        sessions = self.raw_data.get("session_summaries", [])
        if not sessions:
            self.log.append({"event": "recall_skip", "reason": "no sessions"})
            return

        # 读取记忆银行
        mem_path = os.path.join(VAULT_PATH, ".tool", "lingtai-kb", "memory_bank", "data", "memories.json")
        if not os.path.exists(mem_path):
            self.log.append({"event": "recall_skip", "reason": "no memory bank"})
            return

        try:
            with open(mem_path, "r", encoding="utf-8") as f:
                memories = json.load(f)
        except (json.JSONDecodeError, OSError):
            self.log.append({"event": "recall_error", "reason": "read failed"})
            return

        # 只取 active + pending，跳过 archived
        active_mem = [m for m in memories if m.get("status") in ("active", "pending")]
        if not active_mem:
            self.log.append({"event": "recall_skip", "reason": "no active memories"})
            return

        # 提取 session 关键词
        def _tokens(text: str) -> set:
            return {w.lower() for w in _re.findall(r"[a-z0-9\u4e00-\u9fff]{2,}", text or "")}

        session_tokens = []
        for s in sessions:
            combined = " ".join([
                s.get("intent", ""),
                s.get("summary", ""),
                s.get("tool_name", ""),
                s.get("content", ""),
            ])
            session_tokens.append(_tokens(combined))

        # Jaccard 匹配：每个 session vs 每条记忆
        scored = []
        for m in active_mem:
            mt = _tokens(m.get("content", "") + " " + " ".join(m.get("tags", [])))
            if not mt:
                continue
            for st in session_tokens:
                union = mt | st
                if not union:
                    continue
                sim = len(mt & st) / len(union)
                if sim > 0.0:
                    scored.append((sim, m))

        # 去重取 top-k
        seen_ids = set()
        scored.sort(key=lambda x: -x[0])
        recalled = []
        for sim, m in scored:
            mid = m.get("id", "")
            if mid in seen_ids:
                continue
            seen_ids.add(mid)
            recalled.append({
                "source": "recalled_memory",
                "topic": m.get("content", "")[:80],
                "facts": m.get("tags", []),
                "confidence": m.get("current_confidence", 0.0),
                "summary": m.get("content", "")[:200],
                "recall_similarity": round(sim, 3),
                "updated_at": m.get("last_verified", ""),
            })
            if len(recalled) >= k:
                break

        if recalled:
            self.raw_data.setdefault("observations", []).extend(recalled)

        self.log.append({
            "event": "recall_end",
            "recalled": len(recalled),
            "candidates": len(scored),
        })

    def _mine(self):
        """
        挖掘阶段：
        - pattern_detector 对 raw_data 进行模式检测
        - rule_candidate 从模式生成候选规则
        - 从纠正记录中提取教训模式
        """
        self.log.append({"event": "mine_start"})

        # ━━ 模式泳道：从行为数据挖工具调用模式 → 候选规则 ━━━━━━━━━━━━━━━━━━━━━━
        # 与教训泳道（_mine_corrections）正交：一个管"交互习惯"，一个管"人"。
        # 数据源：harvest 已从 logs/tool_sessions.jsonl 等收割 session_summaries。
        self.patterns = []
        self.candidates = []
        if self.pattern_detector and self.rule_candidate:
            try:
                self.patterns = self.pattern_detector.detect(self.raw_data)
                self.candidates = self.rule_candidate.generate(self.patterns)
            except Exception as e:
                self._log_err("mine_pattern_error", e)
                self.patterns = []
                self.candidates = []

        # ━━ 教训泳道：从纠正记录中挖掘教训模式 ━━━━━━━━━━━━━━━━━━━━━━
        corrections = self.raw_data.get("corrections", [])
        self.lesson_candidates = self._mine_corrections(corrections)

        # ━━ 正向模式泳道（泳道 3）：从成功会话中挖掘操作模板 ━━━━━━━━━━━━
        # 与模式泳道（"别这么做"的感知规则）正交，关注"这么做有效"的正向模板。
        # 灵感：GStack skillify 从成功 /scrape 中提取可重复 SOP。
        self.success_templates = self._mine_success_patterns(self.raw_data)

        self.log.append({
            "event": "mine_end",
            "patterns": len(self.patterns),
            "candidates": len(self.candidates),
            "lesson_candidates": len(self.lesson_candidates),
            "success_templates": len(self.success_templates),
        })

    def _mine_corrections(self, corrections: list[dict]) -> list[dict]:
        """
        从纠正记录中挖掘教训模式。

        思路：
        - 按主题相似性分组（同关键词 → 同主题）
        - 同一主题被纠正 ≥2 次 → 教训模式（高置信度）
        - 单次纠正 → 教训候选（低置信度）
        - 确认信号 (confirmed) → 跳过
        """
        if not corrections:
            return []

        # 提取主题关键词（从 what 字段中提取核心名词）
        import re
        def _topic_key(what: str) -> str:
            """从纠正主题中提取关键词用于分组。"""
            if not what:
                return ""
            what = what.strip()
            # 移除后缀区分词（效率/优化/规则等）
            for suffix in ["效率", "优化", "规则", "风格", "规范"]:
                if what.endswith(suffix):
                    return what[:-len(suffix)].strip()
            return what

        # 按主题分组
        groups: dict[str, list[dict]] = {}
        for c in corrections:
            what = c.get("what", "")
            correction = c.get("correction", "")
            # 跳过确认信号
            if correction == "(confirmed)" or not what:
                continue
            key = _topic_key(what)
            if key not in groups:
                groups[key] = []
            groups[key].append(c)

        lessons = []
        for topic, entries in groups.items():
            if len(entries) == 0:
                continue
            # 取最近一条纠正内容作为教训描述
            sorted_entries = sorted(entries, key=lambda e: e.get("timestamp", ""), reverse=True)
            latest = sorted_entries[0]
            correction_text = latest.get("correction", "")

            # 频率 = 同一主题被纠正的次数
            freq = len(entries)
            # 置信度：重复纠正 ≥2 次 → 高置信度；单次 → 低置信度
            if freq >= 2:
                confidence = 0.6 + min(freq * 0.1, 0.3)  # 2次=0.7, 3次=0.8, 4次=0.9
            else:
                confidence = 0.4

            lessons.append({
                "type": "correction_pattern",
                "topic": topic,
                "what": latest.get("what", ""),
                "correction": correction_text,
                "frequency": freq,
                "confidence": round(confidence, 2),
                "sources": [e.get("timestamp", "") for e in sorted_entries],
                "description": f"用户在「{topic}」上纠正了 {freq} 次：{correction_text[:60]}",
            })

        return lessons

    def _mine_success_patterns(self, raw_data: dict) -> list[dict]:
        """从成功会话中挖掘正向操作模板（泳道 3）。

        与模式泳道（感知规则：别这么做）和教训泳道（纠正记录）正交。
        正向泳道聚焦"这么做有效"，从正常完结的工具调用中提取可复用操作模式。

        灵感：GStack skillify 从成功的 /scrape 中提取可重复 SOP，
        灵台版从成功的工具调用序列中提取操作模板。
        """
        session_summaries = raw_data.get("session_summaries", [])
        if not session_summaries:
            return []

        # 1. 提取成功调用的工具名序列
        tool_names = []
        for entry in session_summaries:
            if not isinstance(entry, dict):
                continue
            tool = entry.get("tool") or entry.get("tool_name") or ""
            outcome = entry.get("outcome", "success")
            if tool and outcome == "success":
                tool_names.append(tool)

        if not tool_names:
            return []

        # 2. 统计工具使用频率
        from collections import Counter
        tool_counts = Counter(tool_names)
        total = len(tool_names)

        # 3. 提取频繁工具对的共现关系（ad-hoc 统计）
        # 只关心出现 ≥3 次的工具
        frequent_tools = {t for t, c in tool_counts.most_common(10) if c >= 3}

        templates = []
        for tool, count in tool_counts.most_common(8):
            if count < 3:
                continue

            # 置信度：使用频率 × 占比
            ratio = count / total if total > 0 else 0
            confidence = round(min(0.4 + ratio * 0.4, 0.85), 2)

            templates.append({
                "type": "success_pattern",
                "pattern_type": "high_frequency_tool",
                "tool_name": tool,
                "frequency": count,
                "total_calls": total,
                "ratio": round(ratio, 3),
                "confidence": confidence,
                "description": f"工具「{tool}」成功调用 {count} 次（占比 {ratio:.0%}），为高频操作工具",
                "suggestion": f"使用 {tool} 前无需额外前置，该工具成功率较高",
            })

        # 4. 如果有足够数据，识别工具链模式
        # 用滑动窗口看连续工具调用（忽略单条调用）
        if len(frequent_tools) >= 2:
            from collections import defaultdict
            chain_counts = defaultdict(int)
            for i in range(len(tool_names) - 1):
                a, b = tool_names[i], tool_names[i + 1]
                if a in frequent_tools and b in frequent_tools and a != b:
                    pair = (a, b)
                    chain_counts[pair] += 1

            for (a, b), count in sorted(chain_counts.items(), key=lambda x: -x[1]):
                if count < 2:
                    continue
                templates.append({
                    "type": "success_pattern",
                    "pattern_type": "tool_chain",
                    "tool_chain": [a, b],
                    "frequency": count,
                    "confidence": round(min(0.3 + count * 0.08, 0.8), 2),
                    "description": f"工具链 {a} → {b} 出现 {count} 次，为习惯性操作序列",
                    "suggestion": f"在 {a} 之后可优先考虑 {b}，两者常连续使用",
                })

        return templates

    def _write_lessons(self):
        """
        写教训阶段：
        - 将 lesson_candidates 写入 memory_bank
        - MemoryBank.write() 自动处理合并/去重/置信度晋升
        """
        self.log.append({"event": "write_lessons_start"})

        if not self.lesson_candidates:
            self.log.append({"event": "write_lessons_end", "written": 0, "boosted": 0})
            return

        try:
            sys.path.insert(0, os.path.join(SKILLOPT_DIR, ".."))
            from memory_bank import MemoryBank
            bank = MemoryBank(VAULT_PATH)
        except ImportError as e:
            self._log_err("write_lessons_error", e)
            return

        written = 0
        boosted = 0

        for lesson in self.lesson_candidates:
            topic = lesson.get("topic", "")
            correction = lesson.get("correction", "")
            freq = lesson.get("frequency", 1)

            # 构造记忆内容
            content = f"用户纠正——{topic}：{correction}"

            # 写入 memory_bank（自动检测同类 → 合并/增强；无同类 → 新建）
            result = bank.write(
                content=content,
                source_type="user_correction",
                tags=["lesson", f"topic:{topic}"],
                branch="通用",
            )

            if result.get("success"):
                if result.get("dup") or result.get("merge_action") in ("merge", "noop"):
                    boosted += 1
                else:
                    written += 1

        self.log.append({
            "event": "write_lessons_end",
            "written": written,
            "boosted": boosted,
        })

    def _replay(self):
        """
        重放阶段：
        - replay_validator 对候选规则在历史数据中回验
        """
        self.log.append({"event": "replay_start"})

        if self.replay_validator and self.candidates:
            # 回验只用留出集（新数据），避免与挖掘同源导致的循环自证；留出集为空时退回训练集
            history = self.raw_data.get("val_sessions") or self.raw_data.get("session_summaries", [])
            self.validated = self.replay_validator.validate(self.candidates, history)
        else:
            self.validated = self.candidates if self.candidates else []

        self.log.append({
            "event": "replay_end",
            "validated": len(self.validated),
            "validated_on": "val" if self.raw_data.get("val_sessions") else "train_fallback",
        })

    def _dream(self, rollouts: int = 5):
        """Dream rollouts — 多视角对比验证，提升规则稳健性。

        对每个已验证的候选规则，在 recalled_memories 和 val_sessions 上做 K 次
        子集采样验证，检验规则性能的稳定性。一致性高 → 加分；忽好忽坏 → 减分。
        与微软 SkillOpt-Sleep 的 dream_rollouts 思路一致。

        Args:
            rollouts: 子集采样数（默认 5）
        """
        self.log.append({"event": "dream_start", "rollouts": rollouts})
        if not self.validated:
            self.log.append({"event": "dream_skip", "reason": "no validated rules"})
            return

        # 收集可供交叉验证的数据源（recalled_memories + val_sessions）
        cross_data = []
        for obs in self.raw_data.get("observations", []):
            if obs.get("source") == "recalled_memory":
                cross_data.append(obs.get("summary", ""))
        for vs in self.raw_data.get("val_sessions", []):
            cross_data.append(
                vs.get("intent", "") + " " + vs.get("summary", "") + " " + vs.get("content", "")
            )
        cross_data = [c for c in cross_data if c.strip()]
        if not cross_data:
            self.log.append({"event": "dream_skip", "reason": "no cross-validation data"})
            return

        import random
        rng = random.Random(42)

        for v in self.validated:
            ptype = v.get("pattern_type", "")
            desc = v.get("description", v.get("source_pattern", ""))
            desc_tokens = {w.lower() for w in _re.findall(r"[a-z0-9\u4e00-\u9fff]{2,}", desc)}

            if not desc_tokens:
                continue

            # K 次子集采样验证
            hits = 0
            for _ in range(max(rollouts, 1)):
                sample = rng.sample(cross_data, min(len(cross_data), max(3, len(cross_data) // 2)))
                # 检查规则描述是否能在子集中找到匹配信号
                matched = False
                for s in sample:
                    st = {w.lower() for w in _re.findall(r"[a-z0-9\u4e00-\u9fff]{2,}", s)}
                    overlap = len(desc_tokens & st)
                    if overlap >= 2:  # 至少 2 个关键词重叠
                        matched = True
                        break
                if matched:
                    hits += 1

            consistency = hits / max(rollouts, 1)
            v["dream_consistency"] = round(consistency, 2)
            v["dream_rollouts"] = rollouts

            # 一致性调整：高稳加分，高波动减分
            if consistency >= 0.8:
                current_pos = v.get("positive_rate", 0.0)
                v["positive_rate"] = round(min(1.0, current_pos + 0.03), 2)
            elif consistency <= 0.3:
                current_pos = v.get("positive_rate", 0.0)
                v["positive_rate"] = round(max(0.0, current_pos - 0.05), 2)

        avg_c = sum(v.get("dream_consistency", 0) for v in self.validated) / max(len(self.validated), 1)
        boosted = sum(1 for v in self.validated if v.get("dream_consistency", 0) >= 0.8)
        penalized = sum(1 for v in self.validated if v.get("dream_consistency", 0) <= 0.3)

        self.log.append({
            "event": "dream_end",
            "rollouts": rollouts,
            "cross_data_size": len(cross_data),
            "avg_consistency": round(avg_c, 2),
            "boosted": boosted,
            "penalized": penalized,
        })

    def _consolidate(self):
        """
        固化阶段：
        - confidence_scorer 对已验证规则做自信定级
        """
        self.log.append({"event": "consolidate_start"})

        if self.confidence_scorer and self.validated:
            self.scored = self.confidence_scorer.score(self.validated)
        else:
            self.scored = []

        self.log.append({
            "event": "consolidate_end",
            "scored": len(self.scored),
        })

    def _gate(self):
        """验证门控 — 在 scored 规则进入 stage 前做最终裁决。

        对每条规则综合评估：
        - confidence（来自 confidence_scorer）
        - dream_consistency（来自 dream rollouts，如有）

        只有 gate_score ≥ 类型阈值且 confidence ≥ 🟢 线的规则进入 stage。
        未通过的不删除，标记 gate="rejected" 和理由，保留在 scored 中供日志记录。
        """
        self.log.append({"event": "gate_start"})
        if not self.scored:
            self.log.append({"event": "gate_skip", "reason": "no scored rules"})
            return

        passed = 0
        rejected = 0
        for s in self.scored:
            conf = s.get("confidence", 0)
            dc = s.get("dream_consistency", 1.0)  # 无 dream 数据时默认 1.0
            ptype = s.get("pattern_type", "")
            thresholds = {
                "tool_sequence": {"min_conf": 0.60, "min_gate": 0.50},
                "dead_end": {"min_conf": 0.75, "min_gate": 0.60},
                "cooccurrence": {"min_conf": 0.70, "min_gate": 0.55},
                "knowledge_gap": {"min_conf": 0.80, "min_gate": 0.65},
            }.get(ptype, {"min_conf": 0.75, "min_gate": 0.55})

            gate_score = round(conf * dc, 2)
            level = s.get("level", "⚪")

            if level == "🟢" and gate_score >= thresholds["min_gate"]:
                s["gate"] = "passed"
                s["gate_score"] = gate_score
                passed += 1
            else:
                s["gate"] = "rejected"
                reasons = []
                if level != "🟢":
                    reasons.append(f"level={level}")
                if gate_score < thresholds["min_gate"]:
                    reasons.append(f"gate_score={gate_score}<{thresholds['min_gate']}")
                s["gate_reason"] = "; ".join(reasons)
                s["gate_score"] = gate_score
                rejected += 1

        self.log.append({
            "event": "gate_end",
            "passed": passed,
            "rejected": rejected,
        })

    def _collide(self, top_n: int = 10):
        """高温自由联想 — 跨 session 关键词碰撞，挖掘意外关联。

        对应人脑 REM 期的高温连接：对 session_summaries 做跨工具/跨主题的
        关键词 Jaccard 匹配，在「似像非像」区间（0.2-0.5）产出碰撞对。
        低于 0.2 是噪音，高于 0.5 是重复。
        """
        self.log.append({"event": "collide_start", "top_n": top_n})
        sessions = self.raw_data.get("session_summaries", [])
        if len(sessions) < 4:
            self.log.append({"event": "collide_skip", "reason": "too few sessions"})
            return

        # 为每条 session 提取关键词 + 工具名（作为"域"标签）
        items = []
        for s in sessions:
            combined = " ".join([
                s.get("intent", ""), s.get("summary", ""),
                s.get("tool_name", ""), s.get("content", ""),
            ])
            tokens = {w.lower() for w in _re.findall(r"[a-z0-9\u4e00-\u9fff]{3,}", combined)}
            if len(tokens) >= 3:
                items.append({
                    "tokens": tokens,
                    "label": (s.get("tool_name", "") or s.get("intent", "") or "")[:40],
                })

        # 跨 session Jaccard 碰撞：只比较不同 label 的对
        pairs = []
        for i in range(len(items)):
            for j in range(i + 1, len(items)):
                if items[i]["label"] == items[j]["label"]:
                    continue  # 同工具/同主题的跳过
                a, b = items[i]["tokens"], items[j]["tokens"]
                union = a | b
                if not union:
                    continue
                sim = len(a & b) / len(union)
                if 0.2 <= sim <= 0.5:  # "似像非像"区间
                    pairs.append({
                        "sim": round(sim, 3),
                        "a_label": items[i]["label"],
                        "b_label": items[j]["label"],
                        "common": list(a & b)[:5],
                    })

        pairs.sort(key=lambda x: -x["sim"])
        top_pairs = pairs[:top_n]

        if top_pairs:
            # 将碰撞对以 observation 形式注入，供下一轮进化参考
            for p in top_pairs:
                self.raw_data.setdefault("observations", []).append({
                    "source": "collision",
                    "topic": f"{p['a_label']} ↔ {p['b_label']}",
                    "facts": p["common"],
                    "confidence": round(p["sim"] * 0.8, 2),
                    "summary": f"高温关联 ({p['sim']}): {p['a_label']} 与 {p['b_label']} 共享关键词 {p['common']}",
                    "updated_at": "",
                })

        self.log.append({
            "event": "collide_end",
            "pairs_found": len(pairs),
            "top_pairs": len(top_pairs),
        })

    def _load_guard(self) -> dict:
        """读取 🟢 采纳护栏计数（source_pattern → 连续出现次数）。"""
        try:
            with open(GUARD_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError):
            return {}

    def _save_guard(self, guard: dict):
        """持久化 🟢 采纳护栏计数。"""
        try:
            with open(GUARD_PATH, "w", encoding="utf-8") as f:
                json.dump(guard, f, ensure_ascii=False, indent=2)
        except OSError:
            pass

    def _stage(self):
        """
        暂存阶段：
        - stager 将自信 ≥ 0.60 的规则写入 staged/{date}/
        - 🟢 规则分两类：
          · 白名单类型（AUTO_ADOPT_TYPES：dead_end/knowledge_gap/cooccurrence）走护栏，
            需连续 ADOPT_MIN_CONSECUTIVE 次 run 都出现为候选，才自动写入感知规则.md
          · 其余类型（如纯频率类 tool_sequence）即使 🟢 也只留 staged/ 待人类 review，不自动写入
        - 🟡 规则：留 staged/ 待人类审核
        - 不改任何现有文件（除自动采纳写入感知规则.md，且受护栏约束）
        """
        self.log.append({"event": "stage_start"})

        written = []
        auto_adopted = []
        if self.stager and self.scored:
            # 仅 gate=passed 的规则进入 stage（未通过 gate 的规则不写入文件，但保留在 scored 中供日志）
            gate_passed = [r for r in self.scored if r.get("gate") == "passed"]
            if not gate_passed:
                # 如果 scored 非空但 gate 全拒，检查是否忘了跑 _gate()
                all_scored = len(self.scored)
                all_gated = sum(1 for r in self.scored if r.get("gate"))
                if all_scored > 0 and all_gated == 0:
                    # gate 未运行 → 兼容旧流程，放行所有 🟢
                    gate_passed = [r for r in self.scored if r.get("level") == "🟢"]
                # 无 🟢 且无 gated → 无规则可 stage
            written = self.stager.write(gate_passed)

        # ── 🟢 自动采纳护栏 ─────────────────────────────────
        # 仅 AUTO_ADOPT_TYPES 类型可走护栏自动采纳；其余 🟢 直接留 staged 待人类 review。
        # 走护栏的 🟢 需连续 N 次 run 都出现为候选才写入感知规则.md；连续计数存 adoption_guard.json；
        # 本次未以 🟢 出现的 source 计数归零（连续要求）。
        guard = self._load_guard()
        seen_green = set()
        # 只有 gate=passed 的 🟢 规则才进入自动采纳流程
        gate_passed_green = [r for r in self.scored if r.get("level") == "🟢" and r.get("gate") == "passed"]
        # 非白名单类型：永远只留 staged，不计入护栏（held_for_review 直接 +1）
        auto_eligible = [
            r for r in gate_passed_green
            if r.get("pattern_type", "") in AUTO_ADOPT_TYPES and r.get("source_pattern")
        ]
        held = len(gate_passed_green) - len(auto_eligible)

        for rule in auto_eligible:
            src = rule["source_pattern"]
            seen_green.add(src)
            guard[src] = guard.get(src, 0) + 1
            if guard.get(src, 0) >= ADOPT_MIN_CONSECUTIVE:
                if self._auto_adopt(rule):
                    auto_adopted.append(rule["rule_id"])
                    guard.pop(src, None)  # 已采纳，停止追踪（dedup 防重复候选）
            # 未达阈值的 🟢 规则：留在 staged/ 由人类 review，不自动写入活文件

        # 连续要求：本次未以 🟢 出现的 source 计数归零
        guard = {src: cnt for src, cnt in guard.items() if src in seen_green and cnt > 0}
        self._save_guard(guard)

        held += len(auto_eligible) - len(auto_adopted)
        self.log.append({
            "event": "stage_end",
            "written": len(written),
            "green_total": len(gate_passed_green),
            "auto_eligible": len(auto_eligible),
            "auto_adopted": len(auto_adopted),
            "held_for_review": held,
            "files": written,
        })

        # 追加 changelog 中的进化轮次信息
        changelog_path = os.path.join(SKILLOPT_DIR, "changelog.md")
        try:
            summary = self._summary()
            with open(changelog_path, "a", encoding="utf-8") as f:
                f.write(f"\n## {date.today().isoformat()}\n\n")
                f.write(f"- 收割: {summary['harvested']} 条观察\n")
                f.write(f"- 模式: {summary.get('patterns', 0)} 个\n")
                f.write(f"- 候选: {summary['candidates']} 条规则\n")
                f.write(f"- 已验证: {summary['validated']} 条\n")
                f.write(f"- 已评分: {summary['scored']} 条\n")
                f.write(f"- 已暂存: {summary.get('staged', 0)} 条\n")
                f.write(f"- 教训写入: {summary.get('lessons_written', 0)} 条\n")
                f.write(f"- 教训增强: {summary.get('lessons_boosted', 0)} 条\n\n")
        except OSError:
            pass

        # 更新人类仪表盘（丹房页）
        self._update_dashboard()

    def _auto_adopt(self, rule: dict) -> bool:
        """
        自动采纳 🟢 规则：写入感知规则.md + 清理 staged。

        Returns:
            bool: 是否成功采纳
        """
        try:
            rule_id = rule.get("rule_id", "")
            trigger = rule.get("trigger", "")
            action = rule.get("action", "")
            ptype = rule.get("pattern_type", "")
            confidence = rule.get("confidence", 0)
            freq = rule.get("frequency", 1)
            source = rule.get("source_pattern", "")
            today = date.today().isoformat()
            level_str = "🟢" if rule.get("level") == "🟢" else "🟡"

            # 1. 写入感知规则.md
            rules_path = os.path.join(VAULT_PATH, "感知规则.md")
            entry = f"""
### {rule_id} — {trigger}

- **触发条件**：{trigger}
- **建议动作**：{action}
- **来源**：{source}（{today} 累计 {freq} 次）
- **自信度**：{confidence} {level_str}
"""
            # 追加历史采纳记录表格行
            hist_line = f"| {today} | {rule_id} | {ptype} | {confidence} {level_str} | {source} | auto-adopt |\n"

            if os.path.exists(rules_path):
                # 追加到规则列表末尾（在"历史采纳记录"表格前）
                with open(rules_path, "r", encoding="utf-8") as f:
                    content = f.read()

                # 找到表格插入点
                table_marker = "## 📖 历史采纳记录"
                new_rule_marker = "## 📋 规则一览"
                new_entry = f"\n### {rule_id} — {trigger}\n\n- **触发条件**：{trigger}\n- **建议动作**：{action}\n- **来源**：{source}（{today} 累计 {freq} 次）\n- **自信度**：{confidence} {level_str}\n"

                if table_marker in content:
                    # 在表格前插入规则 + 表格行
                    parts = content.split(table_marker)
                    parts[0] += new_entry
                    # 在表格中插入新行
                    table_section = parts[1]
                    first_line_end = table_section.find("\n", table_section.find("|---")) + 1
                    table_body = table_section[:first_line_end] + hist_line + table_section[first_line_end:]
                    content = parts[0] + table_marker + table_body
                elif new_rule_marker in content:
                    # 还没有表格，追加在规则一览后
                    parts = content.split(new_rule_marker)
                    content = parts[0] + new_rule_marker + new_entry + "\n\n" + table_marker + "\n\n| 日期 | 规则 | 类型 | 自信度 | 来源 | 操作 |\n|:---|:---|:---|---:|:---|:---:|\n" + hist_line + parts[1]
                else:
                    # 追加到最后
                    content += new_entry
                    content += f"\n{table_marker}\n\n| 日期 | 规则 | 类型 | 自信度 | 来源 | 操作 |\n|:---|:---|:---|---:|:---|:---:|\n{hist_line}"

                with open(rules_path, "w", encoding="utf-8") as f:
                    f.write(content)
            else:
                # 文件不存在，创建新文件
                frontmatter = f"""---
标题: 感知规则（skillopt 自动采纳）
日期: {today}
类型: 规则
职责: skillopt 每日进化自动写入的感知规则
状态: 活跃
---

# 感知规则

> 由灵识·skillopt 每日 03:00 自动进化生成。所有规则已通过自信度验证。
> 每日新增规则由 AI 在对话时自动加载并应用。

## 📋 规则一览

### {rule_id} — {trigger}

- **触发条件**：{trigger}
- **建议动作**：{action}
- **来源**：{source}（{today} 累计 {freq} 次）
- **自信度**：{confidence} {level_str}

## 📖 历史采纳记录

| 日期 | 规则 | 类型 | 自信度 | 来源 | 操作 |
|:---|:---|:---|---:|:---|:---:|
{hist_line}
"""
                with open(rules_path, "w", encoding="utf-8") as f:
                    f.write(frontmatter)

            # 2. 清理 staged 中的对应文件（best-effort：删除失败不影响采纳结果）
            #    注意：某些沙箱环境 os.remove 被包装为 fail-closed（回收站不可用时拒绝删除），
            #    此时清理失败不应回滚已写入的规则。
            staged_dir = os.path.join(SKILLOPT_DIR, "staged", today)
            if os.path.isdir(staged_dir):
                for fn in os.listdir(staged_dir):
                    if fn.startswith(rule_id):
                        try:
                            os.remove(os.path.join(staged_dir, fn))
                        except OSError:
                            pass
                        break

            self.log.append({"event": "auto_adopt", "rule_id": rule_id, "pattern_type": ptype})
            return True

        except Exception as e:
            self._log_err("auto_adopt_error", e, {"rule_id": rule.get("rule_id", "")})
            return False

    def _mem_decay(self):
        """memory_bank 衰减调度：按类型差异化衰减 + 晋升 + 清理"""
        try:
            sys.path.insert(0, os.path.join(SKILLOPT_DIR, ".."))
            from memory_bank import MemoryBank
            from memory_bank.decay import DecayScheduler

            bank = MemoryBank(VAULT_PATH)
            decay = DecayScheduler(bank)

            # 衰减
            decay_result = decay.run()

            # 晋升
            promote_result = decay.run_pending_promotion()

            # 清理超时pending
            cleanup_result = decay.cleanup_stale_pending(max_days=7)

            self.log.append({
                "event": "mem_decay",
                "decayed": decay_result.get("decayed", 0),
                "deprecated": decay_result.get("deprecated", 0),
                "promoted": promote_result.get("promoted", 0),
                "cleaned": cleanup_result.get("cleaned", 0),
            })
        except Exception as e:
            self._log_err("mem_decay_error", e)

    def _update_dashboard(self):
        """更新体检面板——只写运行状态和统计，规则内容引用 [[感知规则]]（单一真源）。"""
        dashboard_path = os.path.join(
            VAULT_PATH,
            "体检",
            "灵识-skillopt状态.md",
        )
        summary = self._summary()
        today = date.today().isoformat()

        staged_count = summary.get("staged", 0)
        recommended = summary.get("recommended", 0)

        # 统计已采纳规则数
        adopted_count = 0
        rules_path = os.path.join(VAULT_PATH, "感知规则.md")
        try:
            with open(rules_path, "r", encoding="utf-8") as f:
                for line in f:
                    if line.strip().startswith("### R"):
                        adopted_count += 1
        except OSError:
            pass

        blacklist_count = 0
        bl_path = os.path.join(SKILLOPT_DIR, "blacklist.json")
        try:
            with open(bl_path, "r", encoding="utf-8") as f:
                bl = json.load(f)
                blacklist_count = len(bl.get("rejected", []))
        except (OSError, json.JSONDecodeError):
            pass

        content = f"""---
标题: 灵识-skillopt状态
日期: {today}
品级: 中品
类型: 体检
职责: 可读面板
状态: 活跃
---

# 灵识·skillopt 状态面板

> skillopt 是灵识的睡眠自进化引擎，每日 03:00 自动收割 → 挖模式 → 回验 → 定级 → 暂存。
> **已采纳的规则** → [[感知规则]]（单一真源，自动写入）。本页只显示运行状态。

## 当前状态（{today}）

```
🚀 版本:   0.3.0（行为数据驱动）
⏰ 调度:   Daily @ 03:00（WorkBuddy 自动化）
🏗️ Staged:  {staged_count} 条
⬛ 已拒绝:  {blacklist_count} 条
🟢 已采纳:  {adopted_count} 条 → [[感知规则]]
🟡 待审核:   {summary.get('review', 0)} 条
```

## ⏳ Staged 规则（待审核）
"""

        stager = None
        try:
            stager_mod = __import__("skillopt.stager", fromlist=["Stager"])
            stager = stager_mod.Stager()
        except (ImportError, AttributeError):
            pass

        staged_files = []
        if stager:
            staged_files = stager.read()
            if not staged_files:
                staged_files = []

        if not staged_files:
            content += "\n暂无。所有 staged 规则已被采纳或驳回。\n"
        else:
            content += "\n| 编号 | 触发条件 | 自信度 | 来源 |\n|:---|:---|---:|:---|\n"
            for sf in staged_files:
                fn = sf.get("filename", "")
                fc = sf.get("content", "")
                c = str(sf.get("confidence", "?"))
                # 从 content 中提取触发条件（第一行）
                trigger = "—"
                for line in fc.split("\n"):
                    if "触发条件" in line:
                        trigger = line.split("：", 1)[-1].strip() if "：" in line else line.split(":", 1)[-1].strip()
                        break
                content += f"| {fn[:10]} | {trigger[:50]} | {c} | skillopt |\n"

        content += "\n\n## 🔗 关联\n\n"
        content += "- [[感知规则]] — 已采纳规则（单一真源）\n"
        content += "- [[灵识-skillopt设计]] — 引擎设计文档\n"

        try:
            with open(dashboard_path, "w", encoding="utf-8") as f:
                f.write(content)
        except OSError:
            pass

    def _summary(self) -> dict:
        """返回本轮进化摘要（用于简报卡片）。"""
        # 从 log 中提取自动采纳数量
        auto_adopted = 0
        for log_entry in self.log:
            if log_entry.get("event") == "stage_end":
                auto_adopted = log_entry.get("auto_adopted", 0)
                break

        # 从 log 中提取 write_lessons 结果
        lessons_written = 0
        lessons_boosted = 0
        for log_entry in self.log:
            if log_entry.get("event") == "write_lessons_end":
                lessons_written = log_entry.get("written", 0)
                lessons_boosted = log_entry.get("boosted", 0)
                break

        total_scored = len(self.scored)
        return {
            "date": date.today().isoformat(),
            "harvested": len(self.raw_data.get("observations", [])),
            "sessions": len(self.raw_data.get("session_summaries", [])),
            "val_sessions": len(self.raw_data.get("val_sessions", [])),
            "patterns": len(self.patterns),
            "candidates": len(self.candidates),
            "validated": len(self.validated),
            "scored": total_scored,
            "recommended": len([s for s in self.scored if s.get("level") == "🟢"]),
            "review": len([s for s in self.scored if s.get("level") == "🟡"]),
            "auto_adopted": auto_adopted,
            "staged": total_scored - auto_adopted,
            "lessons_written": lessons_written,
            "lessons_boosted": lessons_boosted,
            "log": self.log,
        }
