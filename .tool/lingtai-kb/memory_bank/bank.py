# -*- coding: utf-8 -*-
"""
灵识 memory_bank - 记忆库核心
============================
CRUD + 分叉管理 + 冲突检测 + 证据晋升
"""

import os
import json
import hashlib
import re
import sys
from pathlib import Path
from datetime import datetime, timedelta
from typing import List, Optional

from .confidence import ConfidenceEngine, Memory, resolve_decay_policy, detect_scene, DECAY_STREAK_THRESHOLD, DECAY_ARCHIVE_THRESHOLD
from .conflict import ConflictDetector
from .audit import AuditLog
from .merge_policy import (
    MergeStrategy, MemoryType,
    detect_memory_type, get_default_strategy,
    make_entry, project_entries, apply_strategy,
    DEFAULT_MAX_ENTRIES,
)

from content_registry import ContentRegistry, content_hash, mem_id_from_hash
from logger import get_logger

log = get_logger(__name__)

# 记忆银行上限保护
MAX_MEMORIES = 500  # 超过此值时自动归档最旧的 pending 记忆


class MemoryBank:
    """记忆库核心"""

    def __init__(self, vault_path: str = None, registry=None, data_dir: str = None):
        """
        Args:
            vault_path: vault 根路径（用于 ContentRegistry 与路径校验）
            registry:  可注入的 ContentRegistry（测试隔离用）
            data_dir:  记忆库数据目录。⚠️ 测试隔离时务必传入临时目录——
                       传入后 memories_path/audit_path 全部基于它重算，
                       不会再触碰真实 data/ 下的文件。（旧写法「事后覆盖 mb.data_dir」
                       无效，因为 memories_path 在构造时已缓存——以此为准。）
        """
        if vault_path is None:
            vault_path = r"."
        self.vault_path = vault_path
        if data_dir is None:
            self.data_dir = Path(__file__).parent / "data"
        else:
            self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.memories_path = self.data_dir / "memories.json"
        self.audit_path = self.data_dir / "audit.jsonl"
        self.confidence = ConfidenceEngine()
        self.conflict = ConflictDetector()
        self.audit_log = AuditLog(data_dir=str(self.data_dir))
        self.registry = registry if registry is not None else ContentRegistry(vault_path)
        self.memories: List[Memory] = self._load()
        self._id_index: dict = {}  # {memory_id: Memory} O(1) 检索
        self._keyword_index: dict = {}  # {word: set(memory_ids)} 倒排索引
        self._rebuild_index()

    def _audit(self, action: str, memory_id: str, detail: str = ""):
        self.audit_log.record(action, memory_id, detail)

    def _load(self) -> List[Memory]:
        # 优先从 SQLite 加载
        from .memory_sqlite import MemoryStore
        self._store = MemoryStore(str(self.data_dir))
        db_path = self.data_dir / "memories.db"
        if db_path.exists():
            try:
                rows = self._store.load_all()
                if rows:
                    return [Memory.from_dict(m) for m in rows]
            except Exception:
                log.warning("sqlite load failed, fallback to json", exc_info=True)

        # SQLite 为空或不存在：从 JSON 迁移
        if self.memories_path.exists():
            try:
                data = json.loads(self.memories_path.read_text(encoding="utf-8"))
                memories = [Memory.from_dict(m) for m in data]
                # 自动迁移到 SQLite
                if memories:
                    self._store.save_many([m.to_dict() for m in memories])
                    log.info("auto-migrated json→sqlite", extra={"count": len(memories)})
                return memories
            except Exception:
                pass
        # ⚠️ 防误伤：detect 是否其他 data 文件还在但 memories.json 丢失/损坏
        _leak = self.data_dir / "leak_ledger.jsonl"
        _decay = self.data_dir / "decay_log.jsonl"
        if _leak.exists() or _decay.exists():
            log.warning("memories.json 不存在或已损坏 — 记忆银行已重置为空，可通过 git checkout 恢复: git checkout -- .tool/lingtai-kb/memory_bank/data/memories.json (leak_ledger=%s, decay_log=%s)", _leak.exists(), _decay.exists())
        return []

    def _save(self, force: bool = False):
        # SQLite write-through（事务安全，替代 coalesced_json 的 0.5s 窗口）
        data = [m.to_dict() for m in self.memories]
        try:
            self._store.save_many(data)
        except Exception:
            log.warning("sqlite save failed, fallback to json", exc_info=True)
            from coalesced_json import coalesced_dump
            coalesced_dump(self.memories_path, data, indent=2, force=force)

        # 定期导出 JSON（git 备份，每 50 次保存或 force 时）
        if not hasattr(self, "_save_count"):
            self._save_count = 0
        self._save_count += 1
        if force or self._save_count % 50 == 0:
            try:
                self._store.export_json(str(self.memories_path))
            except Exception:
                log.debug("json export skipped", exc_info=True)

        # 上限保护：超过 MAX_MEMORIES 时自动归档最旧的 pending 记忆
        if len(self.memories) > MAX_MEMORIES:
            to_prune = len(self.memories) - MAX_MEMORIES
            pending = sorted(
                [m for m in self.memories if m.status == "pending"],
                key=lambda m: (m.last_verified or ""),
            )
            for m in pending[:to_prune]:
                m.status = "archived"
            if pending[:to_prune]:
                self._rebuild_index()

    def _rebuild_index(self):
        """重建 _id_index + _keyword_index（_load 后、或 memories 被外部修改后调用）"""
        self._id_index = {m.id: m for m in self.memories}
        self._keyword_index = {}
        for m in self.memories:
            self._index_keywords(m)

    def _index_put(self, memory):
        """写入新记忆时同步更新索引（id 索引 + 关键词倒排索引）"""
        self._id_index[memory.id] = memory
        self._index_keywords(memory)

    @staticmethod
    def _extract_keywords(text: str) -> set:
        """从文本中提取索引关键词：≥3 字英文词 + 中文单字"""
        words = set()
        # 英文词（≥3 字母，全小写）
        for w in re.findall(r'[a-zA-Z]{3,}', text):
            words.add(w.lower())
        # 中文单字（去除常见虚词）
        _stop_zh = {"的", "了", "在", "是", "我", "有", "和", "就", "不", "人", "都", "一",
                    "个", "上", "也", "很", "到", "说", "要", "去", "你", "会", "着",
                    "看", "这", "那", "它", "他", "她", "们", "与", "但", "而", "或",
                    "又", "再", "才", "没", "被", "把", "对", "从", "以", "为", "能",
                    "做", "让", "给", "向", "过", "中", "大", "小", "多", "少", "好",
                    "可", "所", "如", "之", "其"}
        for c in text:
            if '\u4e00' <= c <= '\u9fff' and c not in _stop_zh:
                words.add(c)
        return words

    def _index_keywords(self, memory: Memory):
        """将记忆的内容拆词后写入倒排索引"""
        words = self._extract_keywords(memory.content)
        # 也索引 normalized_at_write 字段（若存在）
        ctx = memory.context or {}
        normalized = ctx.get("normalized_at_write", "")
        if normalized and normalized != memory.content:
            words |= self._extract_keywords(normalized)
        for w in words:
            self._keyword_index.setdefault(w, set()).add(memory.id)

    def _gen_id(self, content: str) -> str:
        return mem_id_from_hash(content)

    def _is_similar(self, a: str, b: str) -> float:
        """轻量文本相似度（重叠2-gram），0-1"""
        def extract(s):
            """提取所有2-gram中文字词 + 英文词"""
            words = set()
            # 2-gram 滑动窗口
            chars = [c for c in s if '\u4e00' <= c <= '\u9fff']
            for i in range(len(chars) - 1):
                words.add(chars[i] + chars[i+1])
            # 英文词（≥3字母）
            words.update(re.findall(r'[a-zA-Z]{3,}', s.lower()))
            return words
        wa = extract(a)
        wb = extract(b)
        if not wa or not wb:
            return 0.0
        intersection = wa & wb
        union = wa | wb
        return len(intersection) / len(union)

    # === CRUD ===

    def write(self, content: str, source_type: str, context: dict = None, tags: list = None,
               branch: str = "", knowledge_candidate: bool = False, why: str = "",
               expected_consumer: str = "", expiry_policy: str = None) -> dict:
        """
        写入记忆（含冲突检测 + 内容注册表 + merge_policy + 场景分支）

        Args:
            content: 记忆内容
            source_type: 信源类型
            context: 上下文
            tags: 标签
            branch: 场景分支（空=自动检测）
            knowledge_candidate: 是否标记为「该沉淀为知识」候选（毕业延迟起点）
            why: 为什么记住——记录原因/背景/意图，不参与检索打分

        Returns:
            dict: 写入结果
        """
        confidence = self.confidence.compute_confidence(source_type, context)
        mem_id = self._gen_id(content)
        # 毕业候选：显式参数 或 tag 命中
        is_candidate = knowledge_candidate or ("knowledge_candidate" in (tags or []))
        _now = datetime.now().isoformat()

        # branch 默认通用，显式传参可覆盖（自动检测保留但不默认启用）
        if not branch:
            branch = "通用"

        # === 精确重复阻断：查内容注册表 ===
        existing = self.registry.lookup(content)
        if existing and "memory" in existing.get("modules", []):
            for loc in existing["locations"]:
                m = self._id_index.get(loc)
                if m:
                    old_conf = m.current_confidence
                    m.current_confidence = max(old_conf, confidence)
                    if tags:
                        existing_tags = set(m.tags or [])
                        existing_tags.update(tags)
                        m.tags = list(existing_tags)
                    m.last_verified = datetime.now().isoformat()
                    self._save()
                    self._audit("write_duplicate_merged", m.id, f"confidence={old_conf}={m.current_confidence}")
                    return {"success": True, "id": m.id, "confidence": m.current_confidence, "status": m.status, "dup": True, "message": "已有相同内容的记忆，已合并置信度/标签"}

        # === v2 写入：记忆类型检测 + entries 存储 ===
        memory_type = detect_memory_type(content)
        strategy = get_default_strategy(memory_type)
        new_entry = make_entry(content, confidence, source_type, why=why)

        # 找同 topic 的已有记忆（按记忆类型分阈值）
        existing_mem = None
        merge_threshold = 0.5 if memory_type.value == "episodic" else 0.3
        for m in self.memories:
            if m.status in ("active", "pending") and m.id != mem_id:
                sim = self._is_similar(m.content, content)
                if sim > merge_threshold:
                    existing_mem = m
                    break

        if existing_mem:
            existing_entries = existing_mem.entries or [{
                "content": existing_mem.content,
                "confidence": existing_mem.current_confidence,
                "source": existing_mem.source,
                "timestamp": existing_mem.created_at,
                "status": "active",
            }]
            outcome = apply_strategy(strategy, existing_entries, new_entry, max_entries=DEFAULT_MAX_ENTRIES)

            if outcome["action"] == "noop":
                existing_mem.last_verified = datetime.now().isoformat()
                if tags:
                    existing_tags = set(existing_mem.tags or [])
                    existing_tags.update(tags)
                    existing_mem.tags = list(existing_tags)
                self._save()
                self._audit("write_merge_noop", existing_mem.id, outcome["reason"])
                return {"success": True, "id": existing_mem.id, "confidence": existing_mem.current_confidence, "status": existing_mem.status, "merge_action": "noop", "message": f"合并策略跳过写入（{outcome['reason']}）"}

            if outcome["action"] == "reject":
                self._audit("write_merge_reject", existing_mem.id, outcome["reason"])
                return {"success": False, "id": existing_mem.id, "merge_action": "reject", "reason": outcome["reason"], "message": f"合并策略拒绝写入（{outcome['reason']}）"}

            existing_mem.entries = outcome["entries"]
            proj = project_entries(outcome["entries"])
            existing_mem.content = proj["content"]
            existing_mem.current_confidence = proj["confidence"]
            existing_mem.memory_type = memory_type.value
            existing_mem.branch_id = branch
            existing_mem.last_verified = datetime.now().isoformat()
            # 毕业候选标记（仅首次）
            if is_candidate:
                existing_mem.graduation_candidate = True
                if not existing_mem.graduation_marked_at:
                    existing_mem.graduation_marked_at = _now
            # 预期消费者：合并时以新写入为准（覆盖旧值）
            if expected_consumer and not existing_mem.expected_consumer:
                existing_mem.expected_consumer = expected_consumer
            # 上下文合并：传入的 context 存到记忆上（不覆盖已有）
            if context and not existing_mem.context:
                existing_mem.context = context
            elif context and isinstance(existing_mem.context, dict):
                # 补充已有 context 中缺失的键
                for k, v in context.items():
                    if k not in existing_mem.context:
                        existing_mem.context[k] = v
            if tags:
                existing_tags = set(existing_mem.tags or [])
                existing_tags.update(tags)
                existing_mem.tags = list(existing_tags)
            self._save()
            self._audit("write_merge", existing_mem.id, f"strategy={strategy.value}, {outcome['reason']}")
            return {"success": True, "id": existing_mem.id, "confidence": existing_mem.current_confidence, "status": existing_mem.status, "merge_action": outcome["action"], "merge_strategy": strategy.value, "message": f"记忆已合并（{strategy.value}），当前{len(outcome['entries'])}条entries"}

        # 没有同 topic 记忆 → 创建新记忆
        memory = Memory(
            id=mem_id, content=content, source=source_type,
            source_confidence=confidence, current_confidence=confidence,
            status="active" if (confidence >= 0.6 or expiry_policy == "session_scope") else "pending",
            tags=tags or [],
            memory_type=memory_type.value,
            branch_id=branch,
            expiry_policy=expiry_policy if expiry_policy is not None else resolve_decay_policy(memory_type.value, "slow_decay"),
            entries=[new_entry],
            schema_version=2,
            graduation_candidate=is_candidate,
            graduation_marked_at=_now if is_candidate else "",
            context=context if context else {},
            expected_consumer=expected_consumer,
        )
        self.memories.append(memory)
        self._index_put(memory)
        self._save()
        self._audit("write", memory.id, f"confidence={confidence}, type={memory_type.value}")
        self.registry.register(content, location=mem_id, module="memory_bank", content_type="memory")
        return {"success": True, "id": memory.id, "confidence": confidence, "status": memory.status, "memory_type": memory_type.value, "merge_strategy": strategy.value, "graduation_candidate": memory.graduation_candidate, "graduation_marked_at": memory.graduation_marked_at}

    def query(self, keyword: str = None, status: str = "active", min_confidence: float = 0.0,
                  branch: str = "", include_archived: bool = False,
                  consumer: str = "") -> List[dict]:
        """
        查询记忆（支持场景分支过滤 + 归档包含 + 关键词相关性排序 + 消费者过滤）

        Args:
            keyword: 关键词过滤（可选）
            status: 状态过滤。空串=全部非归档（配合 include_archived 控制）
            min_confidence: 最低置信度
            branch: 场景分支过滤。空=搜索全部
            include_archived: 是否包含已归档
            consumer: 预期消费者过滤。空=搜索全部

        Returns:
            List[dict]: 匹配的记忆列表（按相关性+置信度降序）
        """
        results = []
        # ── Step 1: 候选集预过滤（关键词倒排索引加速）──
        candidates = self.memories
        kw_lower = None
        if keyword:
            kw_lower = keyword.lower()
            kw_words = self._extract_keywords(kw_lower)
            if kw_words:
                candidate_ids = set()
                for w in kw_words:
                    ids = self._keyword_index.get(w)
                    if ids:
                        candidate_ids |= ids
                if candidate_ids:
                    candidates = [self._id_index[mid] for mid in candidate_ids
                                  if mid in self._id_index]

        for m in candidates:
            if status:
                if not include_archived:
                    if m.status == "archived":
                        continue
                    if m.status != status:
                        continue
                else:
                    if m.status not in (status, "archived"):
                        continue
            elif not include_archived:
                if m.status == "archived":
                    continue

            if m.current_confidence < min_confidence:
                continue
            if keyword:
                if kw_lower not in m.content.lower():
                    # 同时检查 normalized_at_write 字段（写时归一化的日期可能不在原始 content 中）
                    ctx = m.context or {}
                    normalized = ctx.get("normalized_at_write", "")
                    if not normalized or kw_lower not in normalized.lower():
                        continue
            if branch:
                if m.branch_id not in (branch, "通用"):
                    continue
            if consumer:
                if m.expected_consumer and m.expected_consumer != consumer:
                    continue
            d = m.to_dict()
            # 命中原因（供 AI 理解为什么这条被召回）
            d["matched_reason"] = self._compute_matched_reason(m, kw_lower)
            results.append(d)

        # 排序 + 相关性评分：有关键词则按词频相关性降序，无关键词按置信度
        # 同时为每项计算 relevance_score（0~1 范围，与置信度分开）供上游判断可答性
        if keyword:
            kw_lower = keyword.lower()
            _kw_tags = set(w.strip().lower() for w in kw_lower.split() if len(w.strip()) > 1)
            for r in results:
                text = r.get("content", "") or ""
                entries = r.get("entries") or []
                freq = text.lower().count(kw_lower)
                for e in entries:
                    if isinstance(e, dict):
                        ec = (e.get("content") or "")
                        freq += ec.lower().count(kw_lower)
                norm = 1 + 0.08 * len(text)
                conf = r.get("current_confidence", 0)
                entry_bonus = 1 + 0.15 * min(len(entries), 8)
                r["relevance_score"] = round(min((freq / norm) * conf * entry_bonus, 1.0), 4)

                # ── 时间衰减因子：近期更新过的记忆加分 ──
                lv = r.get("last_verified") or r.get("created_at", "")
                if lv:
                    try:
                        from datetime import datetime
                        dt = datetime.fromisoformat(lv) if isinstance(lv, str) else datetime.now()
                        days = (datetime.now() - dt).total_seconds() / 86400.0
                        recency = 1.0 + 0.3 * (2.71828 ** (-days / 14.0))  # 14天半衰期
                        r["relevance_score"] = round(min(r["relevance_score"] * recency, 1.0), 4)
                        r["_recency_days"] = round(days, 1)
                    except Exception:
                        pass

                # ── 标签匹配加分：查询词命中标签则补 0.1 ──
                mem_tags = set((t or "").lower() for t in (r.get("tags") or []))
                tag_overlap = _kw_tags & mem_tags
                if tag_overlap:
                    r["relevance_score"] = round(min(r["relevance_score"] + 0.05 * len(tag_overlap), 1.0), 4)

            results.sort(key=lambda x: x["relevance_score"], reverse=True)
        else:
            for r in results:
                r["relevance_score"] = round(r.get("current_confidence", 0), 4)
            results.sort(key=lambda x: x["current_confidence"], reverse=True)
        return results

    @staticmethod
    def _compute_matched_reason(memory, kw_lower: str = None) -> str:
        """计算命中原因，用于搜索结果中的 matched_reason 字段"""
        if not kw_lower:
            return "confidence_based"
        content = (memory.content or "").lower()
        if kw_lower in content:
            return "substring_match"
        ctx = memory.context or {}
        normalized = ctx.get("normalized_at_write", "")
        if normalized and kw_lower in normalized.lower():
            return "date_normalized_match"
        return "keyword_index_match"

    def merge(self, memory_id: str, target_branch: str = "通用") -> dict:
        """跨分支 merge — O(1) 哈希索引"""
        m = self._id_index.get(memory_id)
        if not m:
            return {"success": False, "error": "not found"}
        if m.branch_id == target_branch:
            return {"success": True, "id": memory_id, "message": "已在目标分支，无需 merge"}
        # 在目标分支创建一个新 entry（追加内容 + 标记来源分支）
        new_entry = make_entry(
            content=m.content,
            confidence=m.current_confidence,
            source=m.source,
        )
        new_entry["branch_origin"] = m.branch_id
        m.entries = m.entries or []
        m.entries.append(new_entry)
        m.branch_id = target_branch
        self._save()
        self._audit("merge", memory_id, f"{m.branch_id}→{target_branch}")
        return {"success": True, "id": memory_id, "branch": target_branch, "message": f"记忆已从 {m.branch_id} merge 到 {target_branch}"}

    # === 场景检测 ===

    def _detect_scene(self, content: str) -> str:
        """自动检测场景分支（合并到 confidence.detect_scene）"""
        return detect_scene(content)

    def get(self, memory_id: str) -> Optional[dict]:
        """获取单条记忆 — O(1) 哈希索引"""
        m = self._id_index.get(memory_id)
        return m.to_dict() if m else None

    def update_confidence(self, memory_id: str, delta: float) -> dict:
        """更新置信度 — O(1) 哈希索引"""
        m = self._id_index.get(memory_id)
        if not m:
            return {"success": False, "error": "not found"}
        old_status = m.status
        m.current_confidence = max(0.0, min(1.0, m.current_confidence + delta))
        m.last_verified = datetime.now().isoformat()
        # 状态回弹：置信度跨阈值时自动升降（与衰减记过线一致，已与写入地板错开）
        if m.current_confidence >= DECAY_STREAK_THRESHOLD and m.status == "deprecated":
            m.status = "active"
            m.decay_streak = 0
        elif m.current_confidence < DECAY_STREAK_THRESHOLD and m.status == "active":
            m.status = "deprecated"

        # 采纳时累加证据，触发教训毕业
        graduated = None
        is_adopt = delta > 0
        is_lesson = "lesson" in (m.tags or [])
        if is_adopt and is_lesson:
            m.evidence_count += 1
            if m.evidence_count >= 3:
                graduated = self._graduate_lesson(m)

        self._save()
        self._audit("update_confidence", memory_id, f"delta={delta}, new={m.current_confidence}, status={old_status}→{m.status}")
        result = {"success": True, "new_confidence": m.current_confidence, "status": m.status}
        if graduated:
            result["graduated"] = graduated
        return result

    def _graduate_lesson(self, m) -> dict:
        """教训毕业：写入原料目录（待提炼）→ 归档 → 日志

        v2: 不再写入 AGENTS.md。毕业教训写入 原料/教训/ 作为 .md 文件，
        由提炼管线扫描处理，自动进入丹房知识库。
        """
        import os, re
        from datetime import datetime
        vault = getattr(self, 'vault_path', os.environ.get("LINGTAI_VAULT", r"."))
        raw_dir = os.path.join(vault, "原料", "教训")
        log_path = os.path.join(vault, "丹房", "日志.md")

        summary = m.content[:80].replace('\n', ' ')

        # 写原料文件：原料/教训/{slug}.md
        try:
            os.makedirs(raw_dir, exist_ok=True)
            # 从 content 第一行提取文件名
            first_line = m.content.split('\n')[0].strip()[:40]
            slug = re.sub(r'[^\w\u4e00-\u9fff]', '_', first_line) if first_line else f"lesson_{m.id}"
            slug = slug[:50]
            raw_path = os.path.join(raw_dir, f"{slug}.md")
            # 写 frontmatter + body
            with open(raw_path, 'w', encoding='utf-8') as f:
                f.write("---\n")
                f.write(f"标题: 教训·{first_line}\n")
                f.write(f"来源: memory_bank\n")
                f.write(f"记忆ID: {m.id}\n")
                f.write(f"置信度: {m.current_confidence:.2f}\n")
                f.write(f"证据计数: {m.evidence_count}\n")
                f.write(f"信源: {m.source}\n")
                f.write(f"毕业时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n")
                f.write("状态: 待提炼\n")
                f.write("---\n\n")
                f.write(m.content)
                f.write("\n\n> 由记忆银行毕业自动写入，待提炼为丹房知识\n")
        except Exception as e:
            log.warning("graduate raw write error: %s", e)
            return {"error": str(e)}

        m.status = "archived"
        self._audit("graduate", m.id, f"evidence={m.evidence_count}")

        try:
            os.makedirs(os.path.dirname(log_path), exist_ok=True)
            now = datetime.now().strftime('%y-%m-%d %H:%M')
            tags = ",".join(m.tags) if m.tags else ""
            with open(log_path, 'a', encoding='utf-8') as f:
                f.write(f"[{now}] WB auto | 教训毕业 | {summary} | → 原料/教训/{slug}.md | tags={tags}\n")
        except:
            pass
        return {"summary": summary, "evidence": m.evidence_count, "raw_path": raw_path}

    def evidence_increment(self, memory_id: str, boost: float = 0.1) -> dict:
        """证据计数+1 并轻度提升置信度（验证闭环核心）— O(1) 哈希索引
        语义：被明确采纳/验证 = 证据+1 + 置信度微升 + 刷新验证时间；
        pending 累计达 3 次自动转 active。解决"只减不增"与"pending 永不通不过晋升"。
        """
        m = self._id_index.get(memory_id)
        if not m:
            return {"success": False, "error": "not found"}
        m.evidence_count += 1
        m.current_confidence = min(0.95, round(m.current_confidence + boost, 4))
        m.last_verified = datetime.now().isoformat()
        if m.evidence_count >= 3 and m.status == "pending":
            m.status = "active"
            self._audit("promote", memory_id, f"evidence={m.evidence_count}")
        self._save()
        return {"success": True, "evidence_count": m.evidence_count, "status": m.status, "confidence": m.current_confidence}

    def deprecate(self, memory_id: str, reason: str = "") -> dict:
        """废弃记忆 — O(1) 哈希索引"""
        m = self._id_index.get(memory_id)
        if not m:
            return {"success": False, "error": "not found"}
        m.status = "deprecated"
        self._save()
        self._audit("deprecate", memory_id, reason)
        return {"success": True}

    def archive(self, memory_id: str) -> dict:
        """归档记忆 — O(1) 哈希索引"""
        m = self._id_index.get(memory_id)
        if not m:
            return {"success": False, "error": "not found"}
        m.status = "archived"
        self._save()
        self._audit("archive", memory_id)
        return {"success": True}

    # === 跨域协同：记忆↔知识 受控桥 ===

    def _resolve_danfang_path(self, page: str):
        """校验并归一化丹房页路径，返回绝对路径（不存在返回 None）"""
        p = page.strip()
        if not p.endswith(".md"):
            p = p + ".md"
        if not p.startswith("丹房/"):
            p = "丹房/" + p
        abs_p = os.path.join(self.vault_path, p.replace("/", os.sep))
        return abs_p if os.path.isfile(abs_p) else None

    def set_knowledge_link(self, memory_id: str, links, mode: str = "add") -> dict:
        """建立记忆→知识的受控 wikilink（单向桥，不污染知识图）。

        Args:
            memory_id: 记忆 ID
            links: 目标丹房页路径（str 或 list），如 "丹房/07-工具与AI/AI Agent 记忆系统方案对比"
            mode: "add"（默认，去重追加）/ "replace"（替换）
        Returns:
            dict: 操作结果，含最终 knowledge_links 与 graduated_at
        """
        m = self._id_index.get(memory_id)
        if not m:
            return {"success": False, "error": "not found"}
        if isinstance(links, str):
            links = [links]
        valid, invalid = [], []
        for p in links:
            abs_p = self._resolve_danfang_path(p)
            if abs_p:
                valid.append({"page": p, "set_at": datetime.now().isoformat()})
            else:
                invalid.append(p)
        if not valid:
            return {"success": False, "error": "no_valid_danfang_page", "invalid": invalid}

        if mode == "replace":
            m.knowledge_links = valid
        else:  # add（去重）
            existing_pages = {l["page"] for l in (m.knowledge_links or [])}
            for v in valid:
                if v["page"] not in existing_pages:
                    m.knowledge_links.append(v)

        # 首次建立链接 = 毕业（consolidation 落点），记终点时间
        if m.knowledge_links and not m.graduated_at:
            m.graduated_at = datetime.now().isoformat()
            m.graduation_candidate = True
            if not m.graduation_marked_at:
                m.graduation_marked_at = m.created_at

        self._save()
        self._audit("knowledge_link", memory_id, f"mode={mode}, n={len(m.knowledge_links)}")
        return {
            "success": True,
            "memory_id": memory_id,
            "knowledge_links": m.knowledge_links,
            "graduated_at": m.graduated_at,
            "invalid": invalid,
        }

    def lifecycle_stats(self) -> dict:
        """跨域生命周期统计：毕业延迟 + 反向泄漏率。

        - 毕业延迟：graduation_candidate 标记 → 首次 knowledge_link 的时延分布。
        - 反向泄漏率：知识写入中易变物占比（来自 leak_ledger.jsonl）。
        """
        from memory_bank.lifecycle import read_ledger

        candidates = [m for m in self.memories if m.graduation_candidate]
        graduated = [m for m in candidates if m.knowledge_links]
        pending = [m for m in candidates if not m.knowledge_links]

        latencies = []
        for m in graduated:
            set_ats = [l.get("set_at") for l in m.knowledge_links if l.get("set_at")]
            if not set_ats:
                continue
            first_set = min(set_ats)
            start = m.graduation_marked_at or m.created_at
            try:
                d0 = datetime.fromisoformat(start)
                d1 = datetime.fromisoformat(first_set)
                latencies.append((d1 - d0).total_seconds() / 86400.0)
            except Exception:
                pass

        avg_lat = round(sum(latencies) / len(latencies), 2) if latencies else None
        median_lat = round(sorted(latencies)[len(latencies) // 2], 2) if latencies else None
        pending_examples = [
            {"id": m.id, "content": m.content[:50], "marked_at": m.graduation_marked_at}
            for m in pending[:5]
        ]

        leak = read_ledger(0)
        # 丹房直写口径（仅 page_create）才是严格意义的「漏进丹房」
        bt = leak.get("by_tool", {})
        pc = bt.get("page_create", {})
        rate_danfang = round(pc["volatile"] / pc["total"], 4) if pc.get("total") else 0.0
        leak["rate_danfang"] = rate_danfang
        return {
            "graduation": {
                "candidates": len(candidates),
                "graduated": len(graduated),
                "pending": len(pending),
                "avg_latency_days": avg_lat,
                "median_latency_days": median_lat,
                "pending_examples": pending_examples,
            },
            "reverse_leak": leak,
        }

    # === 衰减 ===

    def decay(self, observation_ref=None) -> dict:
        """衰减调度：遍历active记忆，按类型衰减置信度。可选联动观察层衰减。
        连续衰减周期跟踪：置信度连续 N 次低于阈值则归档。"""
        decayed = 0
        deprecated = 0
        archived = 0
        for m in self.memories:
            # 同时处理 active 与 deprecated：deprecated 持续衰减直至归档
            # 也处理 pending 中 session_scope 的记忆（短期印记不走 pending 通道）
            if m.status not in ("active", "deprecated") and not (m.status == "pending" and m.expiry_policy == "session_scope"):
                continue
            decay_rate = self.confidence.get_decay_rate(
                resolve_decay_policy(m.memory_type, m.expiry_policy)
            )
            if decay_rate > 0:
                # 教训保护：lesson 标签衰减减半
                if "lesson" in (m.tags or []):
                    decay_rate *= 0.5
                m.current_confidence -= decay_rate
                m.current_confidence = round(m.current_confidence, 4)
                # 连续低置信度归档（spec 8.2 衰减到期）
                if m.current_confidence < DECAY_STREAK_THRESHOLD:
                    m.decay_streak += 1
                else:
                    m.decay_streak = 0
                if m.decay_streak >= 3 or m.current_confidence < DECAY_ARCHIVE_THRESHOLD:
                    m.status = "archived"
                    archived += 1
                elif m.current_confidence < DECAY_STREAK_THRESHOLD:
                    m.status = "deprecated"
                    deprecated += 1
                else:
                    decayed += 1
        self._save()
        result = {"decayed": decayed, "deprecated": deprecated, "archived": archived}
        if observation_ref is not None:
            try:
                obs_result = observation_ref.decay()
                result["observation_decay"] = obs_result
            except Exception as e:
                result["observation_decay_error"] = str(e)
        return result

    # === 统计 ===

    def scan_conflicts(self) -> dict:
        """扫描全部记忆中的冲突对"""
        active = [m for m in self.memories if m.status in ("active", "pending")]
        conflicts = self.conflict.find_all_conflicts(active)
        return {"count": len(conflicts), "conflicts": conflicts}

    def stats(self) -> dict:
        """记忆库统计"""
        active = sum(1 for m in self.memories if m.status == "active")
        pending = sum(1 for m in self.memories if m.status == "pending")
        deprecated = sum(1 for m in self.memories if m.status == "deprecated")
        archived = sum(1 for m in self.memories if m.status == "archived")
        avg_confidence = 0
        if active > 0:
            avg_confidence = round(
                sum(m.current_confidence for m in self.memories if m.status == "active") / active, 2
            )
        # 分支分布
        branch_dist = {}
        for m in self.memories:
            branch_dist[m.branch_id] = branch_dist.get(m.branch_id, 0) + 1
        return {
            "total": len(self.memories),
            "active": active,
            "pending": pending,
            "deprecated": deprecated,
            "archived": archived,
            "avg_confidence": avg_confidence,
            "branch_distribution": branch_dist,
        }
