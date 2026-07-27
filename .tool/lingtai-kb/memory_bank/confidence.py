# -*- coding: utf-8 -*-
"""
灵识 memory_bank - 信源分级引擎
==============================
6级信源分级，每级有不同的初始置信度。
业界唯一：灵识的6级信源分级设计。
"""

import json
from pathlib import Path
from datetime import datetime
from typing import Optional
from dataclasses import dataclass, asdict, field


# 6级信源定义
SOURCE_LEVELS = {
    "user_correction": {"confidence": 0.9, "label": "用户明确纠正", "description": "用户主动说'不对，是这样的'"},
    "user_directive":  {"confidence": 0.8, "label": "用户明确指令", "description": "用户直接说'存这个'"},
    "user_repeated":    {"confidence": 0.8, "label": "用户重复行为", "description": "同一行为出现3次以上"},
    "ai_reasoning":     {"confidence": 0.5, "label": "AI推理结论", "description": "需要后续验证"},
    "mcp":              {"confidence": 0.5, "label": "MCP工具自省", "description": "AI 调试中实测验证的行为规范，与 ai_reasoning 同级；仍需 1 次 adopt 到 0.6 门槛转 active"},
    "user_stated":      {"confidence": 0.4, "label": "单次用户陈述", "description": "可能是一时兴起"},
    "hebbian":          {"confidence": 0.2, "label": "共现权重自增", "description": "Hebbian自动涨，可能巧合"},
    "external":         {"confidence": 0.1, "label": "外部数据摄入", "description": "原料未消化"},
}

# 按类型差异化衰减策略
DECAY_POLICIES = {
    "user_preference": {"speed": "slow",    "daily_decay": 0.001, "description": "用户偏好"},
    "fact_knowledge":  {"speed": "vslow",   "daily_decay": 0.0005, "description": "事实知识"},
    "session_state":   {"speed": "fast",    "daily_decay": 0.05, "description": "会话状态"},
    "behavior_pattern": {"speed": "medium", "daily_decay": 0.005, "description": "行为模式"},
    "hebbian_weight":  {"speed": "on_demand", "daily_decay": 0.0, "description": "共现权重"},
    "session_scope":   {"speed": "instant",  "daily_decay": 0.9, "description": "会话级印记——单次decay直落归档"},
}

# 记忆类型 → 衰减策略（修复 expiry_policy 默认 "slow_decay" 不在键表导致全部回落 session_state 的问题）
MEMORY_TYPE_DECAY = {
    "working":   "session_state",    # 暂存/工作区：转瞬即逝，衰减快
    "episodic":  "behavior_pattern", # 事件流：中等
    "semantic":  "fact_knowledge",   # 事实/偏好：极慢，长期保留
    "procedural": "behavior_pattern",# 技能/流程：中等
    "snapshot":  "fact_knowledge",   # 上下文快照：极慢，长期保留（用户主动存档）
}

# 衰减到期阈值：连续 N 个周期置信度低于此值 → 归档（spec 8.2 衰减到期）
# 注意：此阈值须与写入地板（未知信源默认 0.3）强错开，
# 否则未知信源记忆一旦 active 即触发记过→归档（"一转正就下岗"bug）
DECAY_STREAK_THRESHOLD = 0.15
DECAY_STREAK_LIMIT = 3
# 立即归档线（置信度低于此值直接归档，与写入地板 0.3 强错开）
DECAY_ARCHIVE_THRESHOLD = 0.05

# 场景分支关键词（bank.py + perception.py 共用，2026-07-07 校准）
SCENE_KEYWORDS = {
    "work":    ["项目", "甲方", "工作", "同事", "任务", "截止", "交付", "客户", "KPI", "公司",
                "灵台", "MCP", "Phase", "记忆银行", "巡更", "skillopt", "代码", "管道", "patrol",
                "自动化", "AGENTS", "协作者层", "lint", "memory_decay", "memory_bank", "router",
                "mcp_server", "台律", "巡检", "部署"],
    "life":    ["家", "家人", "朋友", "周末", "健康", "运动", "旅行", "吃饭", "睡觉", "购物", "孩子", "父母"],
    "creation":["选题", "配图", "公众号", "小红书", "抖音", "内容", "创作", "粉丝", "发布", "文案", "视频",
                "耳东", "少年哲学家", "投喂", "选题池", "B站", "作品"],
    "thinking":["哲学", "追问", "O与π", "含人量", "认知", "思考", "框架", "存在", "意义", "自由", "本质", "维度", "判断力"],
}

def detect_scene(content: str) -> str:
    """统一场景检测——bank 写入和 context 加载共用"""
    content_lower = content.lower()
    scores = {}
    for scene, keywords in SCENE_KEYWORDS.items():
        scores[scene] = sum(kw.lower() in content_lower for kw in keywords)
    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else "通用"


def resolve_decay_policy(memory_type: str, expiry_policy: str) -> str:
    """解析衰减策略键：expiry_policy 若已是合法键则直接用，否则按 memory_type 映射。

    修复历史数据 expiry_policy="slow_decay"（不在 DECAY_POLICIES 键表）全部回落
    session_state(0.05/天) 导致所有记忆约 18 天被归档的问题。
    """
    if expiry_policy in DECAY_POLICIES:
        return expiry_policy
    return MEMORY_TYPE_DECAY.get(memory_type, "fact_knowledge")


@dataclass
class Memory:
    """记忆对象（v2: entries 模型）"""
    id: str
    content: str
    source: str                          # 信源类型
    source_confidence: float             # 信源基准置信度
    current_confidence: float            # 当前置信度
    status: str = "active"               # active / pending / deprecated / archived
    evidence_count: int = 1              # 累计证据次数
    branch_id: str = "通用"              # 分叉ID
    conflicts_with: list = field(default_factory=list)
    created_at: str = ""
    last_verified: str = ""
    expiry_policy: str = "slow_decay"    # 衰减策略（写入时按 memory_type 解析为 DECAY_POLICIES 键）
    decay_streak: int = 0                # 连续低置信度周期计数（衰减到期归档用）
    tags: list = field(default_factory=list)
    # v2 新增字段（entries 模型）
    memory_type: str = "semantic"        # working / episodic / semantic / procedural
    entries: list = field(default_factory=list)  # [{"content", "confidence", "timestamp", "source", "status"}, ...]
    schema_version: int = 1              # 1=legacy, 2=entries
    # v3 新增字段（跨域协同：记忆↔知识 受控桥 + 毕业生命周期）
    knowledge_links: list = field(default_factory=list)  # [{"page": "丹房/...", "set_at": "iso"}, ...] 记忆→知识单向
    graduation_candidate: bool = False   # 是否标记为「该沉淀为知识」候选
    graduation_marked_at: str = ""      # 标记候选时间（毕业延迟起点）
    graduated_at: str = ""               # 首次建立知识链接时间（毕业延迟终点）
    # v4 新增字段（与会话/时间戳上下文，支撑日期归一化）
    context: dict = field(default_factory=dict)  # {"session_timestamp": "2023-05-06", ...}
    # v5 新增字段（预期消费者——定向投递）
    expected_consumer: str = ""  # 预期消费者名称，如 "reasonix" / "workbuddy" / "写作"]

    def __post_init__(self):
        if not self.created_at:
            self.created_at = datetime.now().isoformat()
        if not self.last_verified:
            self.last_verified = datetime.now().isoformat()
        # v1→v2 懒升级：第一次访问时，如果 entries 为空但 content 有值，创建初始 entry
        if self.schema_version < 2 and self.content and not self.entries:
            self.entries = [{
                "content": self.content,
                "confidence": self.current_confidence,
                "source": self.source,
                "timestamp": self.created_at,
                "status": "active",
            }]
            self.schema_version = 2

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Memory":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


class ConfidenceEngine:
    """信源分级引擎"""

    def __init__(self):
        pass

    def classify_source(self, source_type: str) -> float:
        """根据信源类型返回初始置信度"""
        level = SOURCE_LEVELS.get(source_type)
        if level:
            return level["confidence"]
        return 0.3  # 默认未知信源

    def detect_source_type(self, content: str, context: dict = None) -> str:
        """
        自动检测信源类型

        Args:
            content: 内容文本
            context: 上下文（如 user_corrected, repeat_count 等）

        Returns:
            str: 信源类型
        """
        if not context:
            context = {}

        # 用户明确纠正
        if context.get("user_corrected"):
            return "user_correction"

        # 用户明确指令
        if context.get("source") == "user_directive":
            return "user_directive"

        # 用户重复行为
        if context.get("repeat_count", 0) >= 3:
            return "user_repeated"

        # AI推理
        if context.get("source") == "ai":
            return "ai_reasoning"

        # 外部数据
        if context.get("source") in ["原料", "tavily", "web"]:
            return "external"

        # Hebbian共现
        if context.get("source") == "hebbian":
            return "hebbian"

        # 默认：单次用户陈述
        return "user_stated"

    def compute_confidence(self, source_type: str, context: dict = None) -> float:
        """计算初始置信度"""
        base = self.classify_source(source_type)
        # 可根据上下文微调（未来扩展）
        return round(base, 2)

    def get_decay_rate(self, memory_type: str) -> float:
        """获取记忆类型的日衰减率"""
        policy = DECAY_POLICIES.get(memory_type, DECAY_POLICIES["session_state"])
        return policy["daily_decay"]

    def get_source_levels(self) -> dict:
        """返回所有信源级别定义"""
        return SOURCE_LEVELS

    def get_decay_policies(self) -> dict:
        """返回所有衰减策略"""
        return DECAY_POLICIES
