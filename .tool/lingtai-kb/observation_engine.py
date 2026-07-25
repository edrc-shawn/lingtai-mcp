# -*- coding: utf-8 -*-
"""
灵台MCP - 自动归纳层（Observation Engine）
===========================================
基于 Hindsight 设计，save 后自动总结模式。

功能：
- save 后自动提取主题/关键词
- 与已有观察匹配（增量更新）
- 积累阈值后创建新观察
- 持久化存储观察
"""

import os
import json
import re
import hashlib
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional, Any

from content_registry import ContentRegistry
from logger import get_logger


log = get_logger(__name__)

class Observation:
    """观察条目"""
    
    def __init__(self, topic: str, facts: List[dict] = None, confidence: float = 0.5):
        self.topic = topic
        self.facts = facts or []
        self.confidence = confidence
        self.created_at = datetime.now().isoformat()
        self.updated_at = datetime.now().isoformat()
        self.summary = ""
        self.status = "active"  # active | dormant（不删，仅标记）
    
    MAX_FACTS = 30  # 每条观察最多保留30条事实，超出淘汰最旧（防无限膨胀）

    def add_fact(self, content: str, source: str):
        """添加事实（自动去重 + 上限淘汰）"""
        # 去重：检查最后5条事实是否相同
        for fact in self.facts[-5:]:
            if fact["content"] == content:
                return  # 重复，跳过
        self.facts.append({
            "content": content,
            "source": source,
            "added_at": datetime.now().isoformat(),
        })
        # 超出上限时淘汰最旧事实（FIFO）
        if len(self.facts) > self.MAX_FACTS:
            self.facts = self.facts[-self.MAX_FACTS:]
        self.updated_at = datetime.now().isoformat()
        # 更新置信度（事实越多，置信度越高）
        self.confidence = self._compute_confidence()
    
    def _compute_confidence(self) -> float:
        """计算置信度：数量 + 来源多样性 + 内容质量"""
        base = 0.3 + len(self.facts) * 0.1
        sources = set(f["source"] for f in self.facts)
        diversity_bonus = 0.05 if len(sources) >= 2 else 0.0
        avg_len = sum(len(f["content"]) for f in self.facts) / max(len(self.facts), 1)
        quality_bonus = 0.05 if avg_len >= 20 else 0.0
        return min(1.0, base + diversity_bonus + quality_bonus)
    
    def needs_update(self) -> bool:
        """是否需要重新归纳"""
        return len(self.facts) >= 3 and not self.summary
    
    def to_dict(self) -> dict:
        """转换为字典"""
        return {
            "topic": self.topic,
            "facts": self.facts,
            "confidence": self.confidence,
            "summary": self.summary,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "status": self.status,
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> 'Observation':
        """从字典创建"""
        obs = cls(
            topic=data["topic"],
            facts=data.get("facts", []),
            confidence=data.get("confidence", 0.5),
        )
        obs.summary = data.get("summary", "")
        obs.created_at = data.get("created_at", datetime.now().isoformat())
        obs.updated_at = data.get("updated_at", datetime.now().isoformat())
        obs.status = data.get("status", "active")
        return obs


class ObservationEngine:
    """自动归纳引擎"""
    
    def __init__(self, vault_path: str = None, registry=None):
        """
        初始化
        
        Args:
            vault_path: 灵台vault路径
            registry: 共享内容注册表（单例），不传则自建
        """
        if vault_path is None:
            self.vault_path = r"."
        else:
            self.vault_path = vault_path
        
        # 存储路径
        self.store_dir = Path(__file__).parent / "observation"
        self.store_dir.mkdir(exist_ok=True)
        self.store_path = self.store_dir / "observations.json"
        
        # 配置
        self.threshold = 3  # 积累3条相关事实后归纳（原2→调高减少pending堆积）
        self.similarity_threshold = 0.2  # 与已有观察匹配的阈值
        self.pending_max_age_days = 30  # pending超过30天未达标则裁剪
        self.stale_single_fact_days = 7  # 单事实pending超过7天即清理（防僵尸堆积）
        
        # 加载观察
        self.observations = self._load_observations()
        self.pending = self._load_pending()
        self.archive = self._load_archive()  # 冷观察归档（保留备查，不自动加载到主列表）
        
        # 启动时裁剪超龄pending
        self._cleanup_stale_pending()
        
        # 内容注册表（有限集成：仅注册最终归纳的观察事实）
        self.registry = registry if registry is not None else ContentRegistry(vault_path)
    
    def _load_observations(self) -> List[Observation]:
        """加载已有观察"""
        if self.store_path.exists():
            try:
                with open(self.store_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                return [Observation.from_dict(obs) for obs in data.get("observations", [])]
            except Exception:
                log.debug("suppressed", exc_info=True)
        return []
    
    def _load_archive(self) -> list:
        """加载冷观察归档"""
        archive_path = self.store_dir / "observations_archive.json"
        if archive_path.exists():
            try:
                with open(archive_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                return [Observation.from_dict(obs) for obs in data.get("observations", [])]
            except Exception:
                log.debug("suppressed", exc_info=True)
        return []
    
    def _save_archive(self):
        """保存冷观察归档"""
        archive_path = self.store_dir / "observations_archive.json"
        data = {
            "observations": [obs.to_dict() for obs in self.archive],
            "updated_at": datetime.now().isoformat(),
        }
        with open(archive_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    
    def _cleanup_stale_pending(self):
        """裁剪超龄pending（超过pending_max_age_days未达threshold的积累槽）"""
        from datetime import datetime, timedelta
        cutoff = datetime.now() - timedelta(days=self.pending_max_age_days)
        single_cutoff = datetime.now() - timedelta(days=self.stale_single_fact_days)
        stale_topics = []
        for topic, slot in self.pending.items():
            facts = slot.get("facts", [])
            created = slot.get("created_at", "")
            # 低事实僵尸提前清理：仅1条事实且超过7天
            if len(facts) < 2 and created:
                try:
                    created_dt = datetime.fromisoformat(created)
                    if created_dt < single_cutoff:
                        stale_topics.append(topic)
                        continue
                except (ValueError, TypeError):
                    stale_topics.append(topic)
                    continue
            # 常规超龄裁剪
            if created:
                try:
                    created_dt = datetime.fromisoformat(created)
                    if created_dt < cutoff:
                        stale_topics.append(topic)
                except (ValueError, TypeError):
                    stale_topics.append(topic)
        for t in stale_topics:
            del self.pending[t]
        if stale_topics:
            self._save_pending()
    
    def _load_pending(self) -> Dict[str, List[dict]]:
        """加载待归纳的积累槽"""
        pending_path = self.store_dir / "pending.json"
        if pending_path.exists():
            try:
                with open(pending_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                log.debug("suppressed", exc_info=True)
        return {}
    
    def _save_observations(self, force: bool = False):
        """保存观察（合并写：11MB 整库重写改为窗口内合并一次落盘）"""
        data = {
            "observations": [obs.to_dict() for obs in self.observations],
            "updated_at": datetime.now().isoformat(),
        }
        from coalesced_json import coalesced_dump
        coalesced_dump(self.store_path, data, indent=2, force=force)

    def _save_pending(self, force: bool = False):
        """保存待归纳（合并写）"""
        pending_path = self.store_dir / "pending.json"
        from coalesced_json import coalesced_dump
        coalesced_dump(pending_path, self.pending, indent=2, force=force)
    
    def _extract_topic(self, content: str, category: str = "") -> str:
        """提取主题"""
        keywords = []
        
        # 中文关键词（2-4字，跳过常见停用词）
        stop_words = {"的", "了", "在", "是", "我", "有", "和", "就", "不", "人", "都", "一", "一个", "上", "也", "很", "到", "说", "要", "去", "你", "会", "着", "没有", "看", "好", "自己", "这"}
        chinese_words = re.findall(r'[\u4e00-\u9fff]{2,4}', content)
        for word in chinese_words:
            if word not in stop_words:
                keywords.append(word)
        
        # 英文关键词
        english_words = re.findall(r'[a-zA-Z]{3,}', content)
        keywords.extend([w.lower() for w in english_words[:3]])
        
        # 使用前3个关键词作为主题
        topic = " ".join(keywords[:3]) if keywords else content[:20]
        
        if category:
            topic = f"{category}:{topic}"
        
        return topic
    
    def _calculate_similarity(self, text1: str, text2: str) -> float:
        """计算文本相似度"""
        words1 = set(text1.lower().split())
        words2 = set(text2.lower().split())
        
        if not words1 or not words2:
            return 0.0
        
        intersection = len(words1 & words2)
        union = len(words1 | words2)
        
        return intersection / union if union > 0 else 0.0
    
    def _find_matched_observation(self, topic: str) -> Optional[Observation]:
        """查找匹配的已有观察"""
        for obs in self.observations:
            similarity = self._calculate_similarity(topic, obs.topic)
            if similarity >= self.similarity_threshold:
                return obs
        return None
    
    def _get_or_create_pending(self, topic: str) -> dict:
        """获取或创建积累槽"""
        if topic not in self.pending:
            self.pending[topic] = {
                "topic": topic,
                "facts": [],
                "created_at": datetime.now().isoformat(),
            }
        return self.pending[topic]
    
    def _归纳(self, pending: dict) -> Observation:
        """归纳积累槽为观察"""
        obs = Observation(topic=pending["topic"])
        for fact in pending.get("facts", []):
            obs.add_fact(fact["content"], fact.get("source", ""))
        
        # 简单归纳：合并所有事实作为摘要
        if obs.facts:
            contents = [f["content"] for f in obs.facts]
            obs.summary = "；".join(contents[:3])
        
        # 注册归纳后的事实到内容注册表
        for fact in obs.facts:
            try:
                self.registry.register(
                    fact["content"],
                    location=f"observation:{obs.topic}",
                    module="observation",
                    content_type="observation",
                )
            except Exception:
                log.debug("suppressed", exc_info=True)
        
        return obs
    
    def _re归纳(self, observation: Observation):
        """重新归纳观察"""
        if observation.facts:
            contents = [f["content"] for f in observation.facts]
            observation.summary = "；".join(contents[:3])
            observation.updated_at = datetime.now().isoformat()
    
    def on_save(self, content: str, category: str = "", source: str = "") -> dict:
        """
        save 后调用，触发自动归纳
        原料同步来源不参与归纳（单次导入，不会积累到 threshold 阈值）
        
        Args:
            content: 保存的内容
            category: 分类
            source: 来源
        
        Returns:
            dict: 归纳结果
        """
        # 原料同步来源不参与观察归纳（单次导入，不会形成3次重复）
        if source and source.startswith("原料同步"):
            return {"action": "skipped", "reason": "raw_material_sync", "source": source}
        
        # 1. 提取主题
        topic = self._extract_topic(content, category)
        
        # 2. 查找匹配的已有观察
        matched = self._find_matched_observation(topic)
        
        if matched:
            # 3a. 已有相关观察 → 增量更新
            matched.add_fact(content, source)
            # 注册新事实到内容注册表
            try:
                self.registry.register(
                    content,
                    location=f"observation:{matched.topic}",
                    module="observation",
                    content_type="observation",
                )
            except Exception:
                log.debug("suppressed", exc_info=True)
            if matched.needs_update():
                self._re归纳(matched)
            self._save_observations()
            return {
                "action": "updated",
                "topic": matched.topic,
                "confidence": matched.confidence,
                "facts_count": len(matched.facts),
            }
        else:
            # 3b. 无匹配 → 新开一个积累槽
            pending = self._get_or_create_pending(topic)
            pending["facts"].append({
                "content": content,
                "source": source,
                "added_at": datetime.now().isoformat(),
            })
            
            # 检查是否达到阈值
            if len(pending["facts"]) >= self.threshold:
                new_obs = self._归纳(pending)
                self.observations.append(new_obs)
                del self.pending[topic]
                self._save_observations()
                self._save_pending()
                return {
                    "action": "created",
                    "topic": new_obs.topic,
                    "confidence": new_obs.confidence,
                    "facts_count": len(new_obs.facts),
                }
            else:
                self._save_pending()
                return {
                    "action": "accumulating",
                    "topic": topic,
                    "facts_count": len(pending["facts"]),
                    "threshold": self.threshold,
                }
    
    def query(self, keyword: str) -> List[dict]:
        """
        查询观察
        
        Args:
            keyword: 搜索关键词
        
        Returns:
            list: 匹配的观察列表
        """
        results = []
        keyword_lower = keyword.lower()
        
        for obs in self.observations:
            if keyword_lower in obs.topic.lower() or keyword_lower in obs.summary.lower():
                results.append(obs.to_dict())
        
        return results
    
    def archive_cold(self, min_confidence: float = 0.4, max_age_days: int = 90) -> dict:
        """
        归档冷观察：将长期未更新且置信度低的观察移出主文件
        
        Args:
            min_confidence: 置信度低于此值视为冷观察
            max_age_days: 超过此天数视为冷观察
        
        Returns:
            dict: 归档统计
        """
        from datetime import datetime, timedelta
        now = datetime.now()
        cutoff = now - timedelta(days=max_age_days)
        
        cold = []
        warm = []
        for obs in self.observations:
            try:
                updated = datetime.fromisoformat(obs.updated_at)
            except (ValueError, TypeError):
                try:
                    updated = datetime.fromisoformat(obs.created_at)
                except (ValueError, TypeError):
                    updated = now
            
            is_cold = (obs.confidence < min_confidence and updated < cutoff)
            is_cold = is_cold or (len(obs.facts) <= 1 and updated < cutoff)
            
            if is_cold:
                cold.append(obs)
            else:
                warm.append(obs)
        
        if cold:
            self.archive.extend(cold)
            self.observations = warm
            self._save_observations()
            self._save_archive()
        
        return {
            "total_before": len(cold) + len(warm),
            "archived_count": len(cold),
            "remaining": len(warm),
            "archive_total": len(self.archive),
            "min_confidence": min_confidence,
            "max_age_days": max_age_days,
        }
    
    def get_stats(self) -> dict:
        """获取统计信息"""
        active_count = sum(1 for o in self.observations if o.status == "active")
        return {
            "total_observations": len(self.observations),
            "active_count": active_count,
            "dormant_count": len(self.observations) - active_count,
            "total_pending": sum(len(facts) for facts in self.pending.values()),
            "pending_topics": len(self.pending),
            "avg_confidence": sum(obs.confidence for obs in self.observations) / max(len(self.observations), 1),
            "archive_total": len(self.archive),
            "threshold": self.threshold,
        }
    
    def decay(self, decay_days: int = 30, daily_rate: float = 0.005,
              dormant_days: int = 90, dormant_confidence: float = 0.3) -> dict:
        """
        时序衰减 + 休眠标记：降低长期未更新观察的置信度，低置信度陈旧观察标记 status=dormant。
        不删数据，不影响存储。休眠观察在检索中优先级降低但不消失。
        """
        from datetime import datetime, timedelta
        now = datetime.now()
        cutoff = now - timedelta(days=decay_days)
        dormant_cutoff = now - timedelta(days=dormant_days)
        
        decayed = 0
        marked_dormant = 0
        total_before = 0.0
        total_after = 0.0
        
        for obs in self.observations:
            if obs.updated_at is None:
                total_before += obs.confidence
                total_after += obs.confidence
                continue
            try:
                updated = datetime.fromisoformat(obs.updated_at)
            except (ValueError, TypeError):
                updated = datetime.fromisoformat(obs.created_at)
            
            old_conf = obs.confidence
            total_before += old_conf
            
            # 衰减
            if updated < cutoff:
                days_stale = (cutoff - updated).days
                decay = daily_rate * days_stale
                obs.confidence = max(0.1, obs.confidence - decay)
                obs.updated_at = now.isoformat()
                decayed += 1
            
            total_after += obs.confidence
            
            # 休眠标记：置信度低于阈值 + 超过天数未更新 → status=dormant，不删
            if obs.status == "active" and obs.confidence < dormant_confidence and updated < dormant_cutoff:
                obs.status = "dormant"
                marked_dormant += 1
        
        if decayed > 0 or marked_dormant > 0:
            self._save_observations()
        
        active_count = sum(1 for o in self.observations if o.status == "active")
        return {
            "total_observations": len(self.observations),
            "active_count": active_count,
            "dormant_count": len(self.observations) - active_count,
            "decayed_count": decayed,
            "marked_dormant": marked_dormant,
            "avg_confidence_before": round(total_before / max(len(self.observations), 1), 4),
            "avg_confidence_after": round(total_after / max(len(self.observations), 1), 4),
        }

    # ═══════════════════════════════════════════════════════════
    #  决策信号检测（联动内观）
    # ═══════════════════════════════════════════════════════════

    def check_decision_signals(self) -> dict:
        """
        扫描所有 pending 决策，匹配触发重评条件。
        由每日内观调用，输出待重评决策清单。
        
        v2 优化：索引驱动——从 index.json 直接获取含决策记录的页面列表，
        不再 glob 全库扫描 .md 文件。
        """
        import re
        import json
        
        vault = self.vault_path
        triggered = []
        total_pending = 0
        
        # v2: 从 index.json 获取含决策记录的页面
        index_path = os.path.join(vault, "丹房", ".meta", "index.json")
        decision_pages = []
        if os.path.isfile(index_path):
            try:
                with open(index_path, 'r', encoding='utf-8') as f:
                    index = json.load(f)
                decision_pages = [p for p in index.get('pages', []) if p.get('has_decisions', False)]
            except Exception:
                # 降级：回退到 glob 扫描（兼容旧索引）
                pass
        
        # 降级：旧索引无 has_decisions 字段
        if not decision_pages:
            import glob as _glob
            danfang = os.path.join(vault, "丹房")
            for md_path in _glob.glob(os.path.join(danfang, "**", "*.md"), recursive=True):
                if ".meta" in md_path or ".workbuddy" in md_path:
                    continue
                try:
                    with open(md_path, 'r', encoding='utf-8', errors='ignore') as f:
                        if "## 决策记录" in f.read():
                            page_name = os.path.relpath(md_path, vault).replace("\\", "/")
                            decision_pages.append({"path": page_name})
                except Exception:
                    continue
        
        # 处理每个决策页面
        for page_info in decision_pages:
            page_path = page_info.get('path', '')
            md_path = os.path.join(vault, page_path + '.md') if not page_path.endswith('.md') else os.path.join(vault, page_path)
            
            try:
                with open(md_path, 'r', encoding='utf-8', errors='ignore') as f:
                    content = f.read()
            except Exception:
                continue
            
            if "## 决策记录" not in content:
                continue
            
            # 按 ### 分割决策块
            decision_section = content.split("## 决策记录", 1)[1]
            # 截到下一个 ## 或文件末尾
            next_h2 = re.search(r'\n## [^#]', decision_section)
            if next_h2:
                decision_section = decision_section[:next_h2.start()]
            
            blocks = re.split(r'\n### ', decision_section)
            for block in blocks:
                if "[pending]" not in block:
                    continue
                
                total_pending += 1
                # 提取标题行
                header_line = block.split('\n')[0] if block.split('\n') else ""
                date_match = re.match(r'(\d{4}-\d{2}-\d{2})\s+(.+)', header_line)
                date_str = date_match.group(1) if date_match else ""
                title = date_match.group(2).replace('[pending]','').strip() if date_match else ""
                
                # 从表格中提取字段（兼容有/无 ** 加粗，自动去尾部 |）
                def _extract_field(text, field_name):
                    m = re.search(rf'\|\s*(?:\*\*)?{field_name}(?:\*\*)?\s*\|\s*(.+?)\s*(?:\||\n|\Z)', text)
                    return m.group(1).strip().rstrip('|').strip() if m else ""
                
                decision_text = _extract_field(block, '决策')
                assumption_text = _extract_field(block, '假设')
                trigger_text = _extract_field(block, '触发重评')
                
                # 检测信号
                signal_hit = self._match_decision_signal(trigger_text, title, date_str)
                
                triggered.append({
                    "date": date_str,
                    "title": title,
                    "decision": decision_text[:80],
                    "assumption": assumption_text[:80],
                    "trigger": trigger_text,
                    "signal_detected": signal_hit,
                    "page": os.path.relpath(md_path, vault).replace("\\", "/"),
                })
        
        return {
            "total_pending": total_pending,
            "signals_triggered": sum(1 for d in triggered if d["signal_detected"]),
            "decisions": triggered,
            "_mode": "index" if decision_pages and isinstance(decision_pages[0], dict) and not decision_pages[0].get('_fallback') else "fallback",
        }
    
    def _get_recent_oplog(self, days: int = 7) -> List[dict]:
        """读取最近 N 天的操作日志"""
        oplog_path = os.path.join(self.vault_path, "丹房", ".meta", "oplog.jsonl")
        if not os.path.isfile(oplog_path):
            return []

        cutoff = datetime.now().timestamp() - days * 86400
        entries = []
        try:
            with open(oplog_path, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                        ts = entry.get('t', '')
                        # 解析时间戳 (ISO 8601)
                        if 'T' in ts:
                            try:
                                dt = datetime.fromisoformat(ts)
                                if dt.timestamp() >= cutoff:
                                    entries.append(entry)
                            except ValueError:
                                pass
                    except json.JSONDecodeError:
                        continue
        except Exception:
            log.debug("suppressed", exc_info=True)
        return entries

    def _extract_keywords(self, text: str) -> List[str]:
        """从触发条件文本中提取有意义的信号关键词（2字以上中文词、英文术语）"""
        if not text:
            return []
        # 中文词（2字以上）
        cn_words = re.findall(r'[\u4e00-\u9fff]{2,}', text)
        # 英文术语（字母开头，含数字/下划线）
        en_terms = re.findall(r'[a-zA-Z][a-zA-Z0-9_-]{2,}', text)
        # 数字模式（含 ≥ ≤ 比较）
        num_patterns = re.findall(r'[≥≤><=]\s*\d+', text)
        # 过滤停用词
        stop_words = {'这个', '那个', '什么', '时候', '怎么', '可以', '一个', '没有', '不是', '就是', '如果', '是否', '已经', '应该'}
        keywords = [w for w in cn_words + en_terms + num_patterns if w not in stop_words]
        return keywords

    def _match_decision_signal(self, trigger_text: str, title: str, date_str: str) -> bool:
        """
        匹配单个触发信号——扫描近期操作日志，检测触发条件是否出现。
        """
        if not trigger_text:
            return False

        tl = trigger_text.lower()

        # 1. 时间基线检测：决策创建超过N天未复审
        time_match = re.search(r'(\d+)\s*天', trigger_text)
        if time_match:
            try:
                n_days = int(time_match.group(1))
                d = datetime.strptime(date_str, "%Y-%m-%d")
                if (datetime.now() - d).days >= n_days:
                    return True
            except (ValueError, AttributeError):
                pass

        # 2. 错误/失败模式：扫描近期 oplog 中是否出现触发关键词的匹配
        #    （如触发条件含 "失败" "错误" "≥N"，检查日志中对应事件）
        keywords = self._extract_keywords(trigger_text)
        if not keywords:
            return False

        # 判断是否为日志匹配型触发条件
        is_log_trigger = any(k in tl for k in ['失败', '错误', '漏', '连续', '超过', '达到', '变更'])
        if not is_log_trigger:
            # 纯时间型已在上面处理，无其他模式→不触发
            return False

        # 读取近期日志
        recent = self._get_recent_oplog(7)
        if not recent:
            return False

        # 3. 逐条匹配：触发条件关键词 vs 日志摘要
        for entry in recent:
            summary = entry.get('summary', '')
            entry_type = entry.get('type', '')
            for kw in keywords:
                if kw in summary or kw in entry_type:
                    return True

        # 4. 特殊："漏" 模式——检查是否该做的事没做
        if '漏' in tl:
            # 从触发条件中推断应该出现的操作类型
            expected_ops = []
            if 'sys_search_logs' in trigger_text or '日志回溯' in trigger_text:
                expected_ops = ['sys_search_logs', 'system_investigate']
            if expected_ops:
                # 统计近期含问答但无搜索调用的对话比例
                dialog_ops = [e for e in recent if e.get('mode') == 'dialog']
                search_ops = [e for e in recent if any(op in e.get('summary','') for op in expected_ops)]
                if len(dialog_ops) > 3 and len(search_ops) == 0:
                    return True

        return False


# 便捷函数
def create_observation_engine(vault_path: str = None) -> ObservationEngine:
    """创建观察引擎实例"""
    return ObservationEngine(vault_path)


if __name__ == "__main__":
    # 测试
    engine = ObservationEngine()
    
    print("观察引擎测试")
    print("=" * 50)
    
    # 模拟 save 操作
    test_cases = [
        ("Python代码要简洁，避免过度封装", "技术", "对话"),
        ("用户批评了某段代码的if嵌套太深", "技术", "对话"),
        ("用户推荐了PEP 8风格指南", "技术", "对话"),
    ]
    
    for content, category, source in test_cases:
        print(f"\n保存: {content}")
        result = engine.on_save(content, category, source)
        print(f"  结果: {result}")
    
    # 查询观察
    print("\n查询观察:")
    results = engine.query("Python")
    print(f"  找到 {len(results)} 条观察")
    for obs in results:
        print(f"  - {obs['topic']}: {obs['summary'][:50]}...")
    
    # 统计
    stats = engine.get_stats()
    print(f"\n统计: {stats}")
    
    print("\n✅ 测试完成")