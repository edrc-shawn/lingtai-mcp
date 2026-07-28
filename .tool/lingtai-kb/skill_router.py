# -*- coding: utf-8 -*-
"""
灵台技能路由引擎（Skill Router）
====================================
将高频工作流封装为可调用的 skill 模板，支撑「技能层抽象」：

- 加载技能/模板/*.skill.md → 解析 frontmatter + body
- 根据用户意图文本匹配最佳 skill
- 返回 skill 的触发条件、上下文清单、工具链、质量门控
"""

import os
import re
import json
from pathlib import Path
from typing import Dict, List, Optional, Any


class SkillRouter:
    """技能路由引擎"""
    
    def __init__(self, vault_path: str = None):
        if vault_path is None:
            self.vault_path = r"."
        else:
            self.vault_path = vault_path
        
        self.skills_dir = Path(self.vault_path) / "技能" / "模板"
        self._skills = None  # 懒加载
    
    def _load_skills(self) -> Dict[str, dict]:
        """加载所有 skill 模板"""
        if self._skills is not None:
            return self._skills
        
        self._skills = {}
        if not self.skills_dir.exists():
            return self._skills
        
        for f in sorted(self.skills_dir.glob("skill.*.md")):
            try:
                content = f.read_text(encoding="utf-8")
                meta = self._parse_frontmatter(content)
                if meta and "skill_id" in meta:
                    self._skills[meta["skill_id"]] = {
                        "file": f.name,
                        "skill_id": meta["skill_id"],
                        "name": meta.get("name", f.stem),
                        "description": meta.get("description", ""),
                        "trigger": meta.get("trigger", ""),
                        "context_load": meta.get("context_load", []),
                    }
            except Exception:
                pass
        
        return self._skills
    
    def _parse_frontmatter(self, content: str) -> Optional[dict]:
        """解析 YAML frontmatter（简易版，不引入yaml库）"""
        m = re.match(r'^---\s*\n(.*?)\n---', content, re.DOTALL)
        if not m:
            return None
        
        meta = {}
        yaml_block = m.group(1)
        for line in yaml_block.split("\n"):
            line = line.strip()
            if ":" in line:
                key, _, value = line.partition(":")
                key = key.strip()
                value = value.strip()
                meta[key] = value
        
        return meta
    
    def list_skills(self) -> List[dict]:
        """列出所有可用 skill"""
        skills = self._load_skills()
        return [
            {
                "skill_id": s["skill_id"],
                "name": s["name"],
                "description": s["description"],
                "trigger": s["trigger"],
            }
            for s in skills.values()
        ]
    
    def get_skill(self, skill_id: str) -> Optional[dict]:
        """获取单个 skill 的完整信息"""
        skills = self._load_skills()
        skill = skills.get(skill_id)
        if not skill:
            return None
        
        # 加载完整内容
        file_path = self.skills_dir / skill["file"]
        try:
            full_content = file_path.read_text(encoding="utf-8")
            # 剥离 frontmatter
            body = re.sub(r'^---.*?---\s*', '', full_content, count=1, flags=re.DOTALL)
            skill["body"] = body.strip()
        except Exception:
            skill["body"] = ""
        
        return skill
    
    def match_intent(self, intent: str) -> Optional[dict]:
        """
        根据用户意图文本匹配最佳 skill
        
        Args:
            intent: 用户意图文本（如"跑一遍每日检"、"总结到灵台"、"帮我查资料"）
        
        Returns:
            匹配的 skill 信息 + 置信度
        """
        skills = self._load_skills()
        intent_lower = intent.lower()
        
        # 关键词匹配规则
        patterns = [
            # daily_patrol
            (r"巡更|每日检|内观|早安|日报|每日任务|跑一遍", "daily_patrol", 0.8),
            (r"自动提炼|原料.*处理|每小时", "daily_patrol", 0.7),
            # refine_archive
            (r"总结到灵台|结束|归档|提炼|保存知识|入库", "refine_archive", 0.9),
            (r"整理.*对话|保存.*内容|知识.*归档", "refine_archive", 0.8),
            # agent_interaction（"记住/记一下"等记忆信号已剥离到 detect_memory_signal，
            # 不再误判为对话/执行意图——信号闭环的关键一步）
            (r"帮我查|搜索.*知识|查.*资料|了解.*概念", "agent_interaction", 0.7),
        ]
        
        best_match = None
        best_score = 0
        
        for pattern, skill_id, score in patterns:
            if re.search(pattern, intent_lower):
                if score > best_score and skill_id in skills:
                    best_score = score
                    best_match = {
                        **skills[skill_id],
                        "confidence": score,
                    }
        
        # 无明确匹配时返回默认（agent_interaction 是默认对话模式）
        if best_match is None and "agent_interaction" in skills:
            best_match = {
                **skills["agent_interaction"],
                "confidence": 0.3,
                "note": "未明确匹配，默认使用对话模式",
            }
        
        return best_match

    def detect_memory_signal(self, text: str) -> dict:
        """
        检测用户消息中的【显式+隐式记忆信号】，返回记忆写入建议——信号闭环入口。

        灵台不缺信源分级（见 confidence.py SOURCE_LEVELS），缺的是把用户自然语言
        解析成 detect_source_type 需要的 context/source_type。本方法填这个空：
          纠正类（"不对/应该是/别再"）      -> user_correction (0.9)
          指令类（"记住/以后/从今往后"）    -> user_directive  (0.8)
          偏好陈述（"我偏好/我喜欢/我习惯"） -> user_stated     (0.4)
          隐式指令（"存到X/下次我直接X"）   -> mcp             (0.5)
          隐式偏好（"方案X好/不做X"）       -> mcp             (0.5)

        Returns:
            dict: is_signal + signal_kind + source_type + confidence_hint +
                  suggested_status + content + suggested_tags + context + note
        """
        t = (text or "").strip()
        if not t:
            return {"is_signal": False, "text": ""}

        correction = re.search(r"不对|不是这样|错了|搞错|应该是|纠正|别再|不要再|以后别|别老是", t)
        directive = re.search(r"记住|记一下|记录一下|存一下|存这个|别忘了|以后记得|从今往后|下次记得|请记得|牢记", t)
        preference = re.search(r"我偏好|我喜欢|我不喜欢|我习惯|我倾向|我一般|我通常|我讨厌", t)
        # 隐式指令：陈述句式但含明确流程/位置/工具变更
        implicit_directive = re.search(
            r"存到|放到|放至|改为|换成|用.+替代|以后都|下次我|下次直接|都存到|都放"
            r"|新建会话|在.+开|不再存|不存", t)
        # 隐式偏好：决策选择或否定式偏好
        implicit_preference = re.search(
            r"方案.{0,15}[好行可以值得]|选.{0,6}[好行]|.+比.+好|不做|不要.+模式"
            r"|别.+盯|不需要.+监工|优先|能不.+尽量", t)

        if correction:
            kind, source_type, conf, ctx = "correction", "user_correction", 0.9, {"user_corrected": True}
        elif directive:
            kind, source_type, conf, ctx = "directive", "user_directive", 0.8, {"source": "user_directive"}
        elif preference:
            kind, source_type, conf, ctx = "preference", "user_stated", 0.4, {"source": "user_directive"}
        elif implicit_directive:
            kind, source_type, conf, ctx = "implicit_directive", "mcp", 0.5, {"source": "ai_inferred"}
        elif implicit_preference:
            kind, source_type, conf, ctx = "implicit_preference", "mcp", 0.5, {"source": "ai_inferred"}
        else:
            return {"is_signal": False, "text": t}

        # 提取要记的实质内容：剥掉触发前缀
        content = t
        m = re.search(r"(?:记住|记一下|记录一下|存一下|存这个|请记得|牢记|以后记得|从今往后|下次记得)[：:\s,，]*", t)
        if m:
            content = (t[m.end():].strip() or t)
        content = content.lstrip("：:\uff0c, 。").strip()

        # 纠正/指令：用户明说即终裁 -> 高置信直接生效；偏好：AI 不敢替人拍板 -> pending 待确认
        suggested_status = "active" if conf >= 0.6 else "pending"

        tags = ["user_signal", "signal:" + kind]
        if kind in ("correction", "preference", "implicit_preference"):
            tags.append("type:knowledge")   # 用户教的 -> knowledge 分流（对齐 AGENTS.md 收尾规则）
        if kind in ("preference", "implicit_preference"):
            tags.append("preference")
        elif "回复" in t or "简洁" in t or "别啰嗦" in t:
            tags.append("reply_style")

        label = {"correction": "纠正", "directive": "指令", "preference": "偏好",
                 "implicit_directive": "隐式指令", "implicit_preference": "隐式偏好"}[kind]
        tail = "高置信直接生效" if suggested_status == "active" else "保持 pending 待用户确认"
        prefix = "显式" if kind in ("correction", "directive", "preference") else ""
        note = (prefix + label + "信号 -> 建议 memory_write(content=..., source_type='"
                + source_type + "', tags=" + str(tags) + ")，" + tail)

        return {
            "is_signal": True,
            "signal_kind": kind,
            "source_type": source_type,
            "confidence_hint": conf,
            "suggested_status": suggested_status,
            "content": content,
            "suggested_tags": tags,
            "context": ctx,
            "note": note,
        }


# 便捷函数
def create_skill_router(vault_path: str = None) -> SkillRouter:
    return SkillRouter(vault_path)