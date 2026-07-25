# -*- coding: utf-8 -*-
"""
灵台 Agent 推荐引擎 v2
=====================
三步升级完成版：
  1. 自动扫描技能目录（模板+外部参考）发现新 skill
  2. 反馈吸收器——用/不用自动调匹配度
  3. 画像感知——读心性.md 决策模式预填 skill 参数

用法：
    recommender = AgentRecommender(vault_path)
    result = recommender.recommend()      # TOP 推荐
    result = recommender.feedback(skill_id, action="used")  # 反馈
"""

import json, os, re
from pathlib import Path
from datetime import datetime, timedelta
from collections import defaultdict
from typing import List, Dict, Optional


class AgentRecommender:
    """Agent 推荐引擎 v2"""

    def __init__(self, vault_path: str = None):
        if vault_path is None:
            self.vault_path = r"."
        else:
            self.vault_path = vault_path

        self.oplog_path = Path(self.vault_path) / "丹房" / ".meta" / "oplog.jsonl"
        self.profile_dir = Path(self.vault_path) / "画像"
        self.templates_dir = Path(self.vault_path) / "技能" / "模板"
        self.ext_ref_dir = Path(self.vault_path) / "技能" / "外部参考"
        self._feedback_path = Path(self.vault_path) / ".tool" / "lingtai-kb" / "data" / "agent_feedback.json"

        # 初始模式→skill 映射（硬编码保底）
        self._init_base_map()
        # 自动扫描技能目录（合并进来）
        self._scan_skills()
        # 加载历史反馈
        self._load_feedback()
        # 加载画像感知
        self._load_profile()

    # ─── 1. 基础映射表 ───

    def _init_base_map(self):
        self.pattern_map = {
            "提炼模式": {"skills": {}},
            "修复模式": {"skills": {}},
            "架构迭代": {"skills": {}},
            "配图模式": {"skills": {}},
            "体检阅读": {"skills": {}},
            "画像确认": {"skills": {}},
            "内观模式": {"skills": {}},
        }

        self.type_to_pattern = {
            "提炼": "提炼模式", "补角": "提炼模式", "补强": "提炼模式",
            "配图": "配图模式",
            "修": "修复模式", "修复": "修复模式",
            "功能": "架构迭代", "架构": "架构迭代", "规划": "架构迭代",
            "优化": "架构迭代", "增强": "架构迭代", "清理": "架构迭代",
            "脚本": "架构迭代", "规则": "架构迭代", "维护": "架构迭代",
            "文档": "架构迭代", "沉淀归档": "架构迭代",
            "体检": "体检阅读",
            "画像": "画像确认",
            "内观": "内观模式",
        }

    # ─── 2. 自动扫描技能目录 ───

    def _scan_skills(self):
        """扫描本地模板和外部参考，按名称/keyword 猜测归属模式"""
        # 关键词→模式映射（用于自动归类）
        keyword_mode_map = [
            (["提炼", "refine", "原料", "知识"], "提炼模式"),
            (["debug", "修", "fix", "bug", "修复", "调试"], "修复模式"),
            (["配图", "生图", "插图", "IP", "illustrat", "插画", "漫画", "social-card"], "配图模式"),
            (["配图", "illustration", "card", "social"], "配图模式"),
            (["写作", "write", "内容", "文章", "公众号", "公众号", "khazix", "humanizer"], "提炼模式"),
            (["记忆", "memory", "codebase-memory", "context-compressor"], "架构迭代"),
            (["规范", "规范", "convention", "guide"], "架构迭代"),
            (["任务", "task", "decomposition", "plan"], "架构迭代"),
            (["json-canvas", "obsidian"], "架构迭代"),
            (["体检", "health", "巡更", "检查"], "体检阅读"),
            (["画像", "profile", "用户"], "画像确认"),
        ]

        # 扫描本地模板
        self._scan_dir(self.templates_dir, "本地模板", keyword_mode_map, is_file=True)
        # 扫描外部参考
        self._scan_dir(self.ext_ref_dir, "外部参考", keyword_mode_map, is_file=False)

    def _scan_dir(self, dir_path: Path, source: str, keyword_map: list, is_file: bool = False):
        if not dir_path.exists():
            return

        if is_file:
            items = list(dir_path.glob("*.md"))
        else:
            items = [d for d in dir_path.iterdir() if d.is_dir()]

        for item in items:
            name = item.stem if is_file else item.name
            skill_id = item.name if is_file else item.name

            # 读取前几行找 description
            desc = ""
            read_path = item if is_file else item / "SKILL.md"
            if read_path and read_path.exists():
                try:
                    for line in read_path.read_text(encoding="utf-8").split("\n")[:15]:
                        if "description:" in line or "name:" in line:
                            desc = line.strip()
                except:
                    pass

            # 按关键词猜测归属模式
            matched_modes = []
            for keywords, mode in keyword_map:
                name_lower = name.lower()
                desc_lower = desc.lower()
                for kw in keywords:
                    if kw.lower() in name_lower or kw.lower() in desc_lower:
                        matched_modes.append(mode)
                        break

            # 默认归入"架构迭代"
            if not matched_modes:
                matched_modes.append("架构迭代")

            # 注册到每个匹配的模式
            for mode in set(matched_modes):
                if skill_id not in self.pattern_map.get(mode, {}).get("skills", {}):
                    base_match = 0.5 if mode == "架构迭代" else 0.6
                    self.pattern_map[mode]["skills"][skill_id] = {
                        "match": base_match,
                        "source": source,
                        "desc": desc[:120] if desc else f"自动注册: {name}",
                        "auto_registered": True,
                    }

    # ─── 3. 反馈吸收器 ───

    def _load_feedback(self):
        """加载历史反馈"""
        self._feedback_data = {}
        if self._feedback_path.exists():
            try:
                self._feedback_data = json.loads(self._feedback_path.read_text(encoding="utf-8"))
            except:
                self._feedback_data = {}

    def _save_feedback(self):
        self._feedback_path.parent.mkdir(parents=True, exist_ok=True)
        self._feedback_path.write_text(
            json.dumps(self._feedback_data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def record_feedback(self, skill_id: str, action: str = "used",
                 mode: str = None, note: str = "") -> Dict:
        """
        记录用户对推荐的反馈。

        Args:
            skill_id: 技能 ID
            action: "used"（用了）| "rejected"（不是这个）| "liked"（好评）
            mode: 当时操作模式（可选，留空自动查找）
            note: 备注

        Returns:
            {"success": True, "new_match": 新匹配度}
        """
        if skill_id not in self._feedback_data:
            self._feedback_data[skill_id] = {
                "used": 0, "rejected": 0, "liked": 0,
                "match_adjust": 0.0, "history": [],
            }

        fb = self._feedback_data[skill_id]
        fb[action] += 1
        fb["history"].append({
            "action": action, "mode": mode or "",
            "note": note, "time": datetime.now().isoformat(),
        })

        # 匹配度调整
        adjust = 0.0
        if action == "used":
            adjust = 0.08
        elif action == "liked":
            adjust = 0.12
        elif action == "rejected":
            adjust = -0.15

        fb["match_adjust"] = round(fb.get("match_adjust", 0) + adjust, 2)
        fb["match_adjust"] = max(-0.5, min(0.5, fb["match_adjust"]))

        self._save_feedback()

        # 更新内存中的匹配度
        new_match = self._apply_feedback_to_skill(skill_id)

        return {"success": True, "new_match": round(new_match, 2), "adjust": adjust}

    def _apply_feedback_to_skill(self, skill_id: str) -> float:
        """将反馈调整应用到所有模式中该 skill 的匹配度"""
        fb = self._feedback_data.get(skill_id, {})
        adjust = fb.get("match_adjust", 0)
        affected_modes = []

        for mode_name, mode_data in self.pattern_map.items():
            skills = mode_data.get("skills", {})
            if skill_id in skills:
                base = skills[skill_id].get("_base_match", skills[skill_id].get("match", 0.5))
                if "_base_match" not in skills[skill_id]:
                    skills[skill_id]["_base_match"] = skills[skill_id]["match"]
                skills[skill_id]["match"] = round(base + adjust, 2)
                affected_modes.append(mode_name)

        return (base + adjust) if "base" in dir() else (0.5 + adjust)

    # ─── 4. 画像感知 ───

    def _load_profile(self):
        """读取心性.md 的决策模式，用于 skill 参数预填"""
        self.profile_hints = {}
        profile_path = self.profile_dir / "心性.md"
        if not profile_path.exists():
            return

        try:
            content = profile_path.read_text(encoding="utf-8")
            # 提取"干中学"模式 → 预填"先做再学"
            if "干中学" in content:
                self.profile_hints["learning_style"] = "learning_by_doing"
            # 提取"怀疑驱动验证" → skill 默认开启根因分析
            if "怀疑驱动" in content:
                self.profile_hints["require_root_cause"] = True
            # 提取"MVP" → skill 参数倾向最小可行
            if "MVP" in content:
                self.profile_hints["mvp_mode"] = True
            # 提取"独立" → 倾向独立可用工具
            if "独立" in content and "孤立" not in content:
                self.profile_hints["independent_tool"] = True
        except:
            pass

    # ─── 5. 核心逻辑 ───

    def read_recent_ops(self, count: int = 5) -> List[Dict]:
        """读取最近 N 条操作记录"""
        if not self.oplog_path.exists():
            return []
        records = []
        try:
            with open(self.oplog_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            records.append(json.loads(line))
                        except json.JSONDecodeError:
                            continue
        except:
            return []
        return records[-count:]

    def detect_mode(self, recent_ops: List[Dict] = None) -> Dict:
        """检测当前操作模式"""
        if recent_ops is None:
            recent_ops = self.read_recent_ops()

        if not recent_ops:
            return {"mode": "未知", "confidence": 0, "recent_types": [], "operation_count": 0}

        types = []
        for op in recent_ops:
            t = op.get("type", "")
            if " " in t:
                t = t.split(" ", 1)[-1]
            types.append(t)

        pattern_scores = defaultdict(float)
        total_weight = 0

        for i, t in enumerate(types):
            weight = 1.0 + (i / len(types)) * 0.5
            matched = self.type_to_pattern.get(t)
            if matched:
                pattern_scores[matched] += weight
            total_weight += weight

        if not pattern_scores:
            return {"mode": "未知", "confidence": 0, "recent_types": types, "operation_count": len(types)}

        best_mode = max(pattern_scores, key=pattern_scores.get)
        confidence = pattern_scores[best_mode] / total_weight

        return {
            "mode": best_mode,
            "confidence": round(confidence, 2),
            "recent_types": types,
            "operation_count": len(types),
        }

    def recommend(self, top_n: int = 3) -> Dict:
        """
        生成 skill 推荐（考虑反馈 + 画像感知）

        Returns:
            {
                "mode": "提炼模式",
                "confidence": 0.85,
                "recommendations": [...],
                "profile_hints": {...},
                "total_skills_available": 30,
            }
        """
        recent_ops = self.read_recent_ops()
        mode_info = self.detect_mode(recent_ops)
        mode_name = mode_info["mode"]

        result = {
            "mode": mode_name,
            "confidence": mode_info["confidence"],
            "recommendations": [],
            "recent_ops": mode_info["recent_types"],
            "profile_hints": self.profile_hints,
            "total_skills_available": sum(
                len(m["skills"]) for m in self.pattern_map.values()
            ),
        }

        if mode_name == "未知":
            return result

        # 获取该模式的 skill 列表
        mode_skills = self.pattern_map.get(mode_name, {}).get("skills", {})
        if not mode_skills:
            return result

        # 按匹配度排序
        skill_list = sorted(
            [{"id": sid, **info} for sid, info in mode_skills.items()],
            key=lambda s: -s["match"],
        )

        result["recommendations"] = skill_list[:top_n]
        return result

    def _apply_decay(self, skill_list: list):
        """Phase 4: 降级/升级——根据使用频率调整匹配度"""
        from datetime import datetime
        now = datetime.now()
        for skill in skill_list:
            sid = skill['id']
            fb = self._feedback_data.get(sid, {})
            history = fb.get('history', [])
            if not history:
                skill['match'] = round(skill['match'] - 0.05, 2)
                continue
            last_used = None
            used_count = 0
            rejected_count = 0
            for h in history:
                if h['action'] in ('used', 'liked'):
                    used_count += 1
                    try:
                        t = datetime.fromisoformat(h['time'])
                        if last_used is None or t > last_used:
                            last_used = t
                    except:
                        pass
                elif h['action'] == 'rejected':
                    rejected_count += 1
            if last_used:
                days_since = (now - last_used).days
                if days_since <= 7:
                    boost = min(0.15, 0.03 * used_count)
                    skill['match'] = round(skill['match'] + boost, 2)
                elif days_since >= 30:
                    skill['match'] = round(skill['match'] - 0.1, 2)
            if rejected_count >= 2:
                skill['match'] = round(skill['match'] - 0.15, 2)
            skill['match'] = max(0.05, min(0.99, skill['match']))

    def list_all_skills(self, mode: str = None) -> Dict:
        """列出所有已注册的 skill（按模式分组）"""
        if mode:
            skills = self.pattern_map.get(mode, {}).get("skills", {})
            return {mode: skills}

        result = {}
        for mode_name, mode_data in self.pattern_map.items():
            result[mode_name] = mode_data["skills"]
        return result

    def get_skill_detail(self, skill_id: str) -> Optional[Dict]:
        """获取 skill 文件内容预览"""
        # 本地模板
        for p in [self.templates_dir / skill_id, self.templates_dir / f"{skill_id}.md"]:
            if p.exists():
                lines = p.read_text(encoding="utf-8").split("\n")[:20]
                desc = ""
                for line in lines:
                    if line.startswith("description:"):
                        desc = line.replace("description:", "").strip("> '\"")
                        break
                return {"id": skill_id, "source": "本地模板", "preview": desc[:300]}

        # 外部参考（读 SKILL.md 或 README.md）
        ext_path = self.ext_ref_dir / skill_id
        if ext_path.exists():
            for fname in ["SKILL.md", "README.md", "CLAUDE.md", "AGENTS.md"]:
                f = ext_path / fname
                if f.exists():
                    lines = f.read_text(encoding="utf-8").split("\n")[:20]
                    desc = ""
                    for line in lines:
                        if "description:" in line.lower():
                            desc = line.strip()[:300]
                            break
                    return {"id": skill_id, "source": "外部参考", "preview": desc or f"(已注册外部skill)"}
        return None


# 便捷函数
def create_recommender(vault_path: str = None) -> AgentRecommender:
    return AgentRecommender(vault_path)