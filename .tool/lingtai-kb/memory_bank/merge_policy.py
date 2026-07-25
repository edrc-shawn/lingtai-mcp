# -*- coding: utf-8 -*-
"""
灵识 merge_policy - 合并策略引擎
=================================
6 种合并策略 + 4 种记忆类型自动分配。
参考 Memoir 的 merge_policy.py 设计。

策略说明：
- APPEND: 追加到 entries 列表（事件流/对话历史）
- REPLACE: 新条目替换旧条目（暂存/工作区）
- CONFIDENCE_GATED: 新置信度 >= 现有才写入（事实/偏好）
- LLM_MERGE: 新老内容由 LLM 合并后写入一条新 entry（技能/流程）
- MERGE_ON_READ: 存储时 APPEND，读取时 LLM 合并
- REJECT: 不写入，返回冲突信息（用户仲裁）
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class MergeStrategy(str, Enum):
    APPEND = "append"
    REPLACE = "replace"
    CONFIDENCE_GATED = "confidence_gated"
    LLM_MERGE = "llm_merge"
    MERGE_ON_READ = "merge_on_read"
    REJECT = "reject"


class MemoryType(str, Enum):
    WORKING = "working"       # 暂存/工作区
    EPISODIC = "episodic"     # 事件流/对话历史
    SEMANTIC = "semantic"     # 事实/偏好/知识
    PROCEDURAL = "procedural" # 技能/流程
    SNAPSHOT = "snapshot"     # 上下文快照（跨会话存档）


# 默认策略映射：每种记忆类型对应的默认合并策略
TYPE_DEFAULT_STRATEGY = {
    MemoryType.WORKING: MergeStrategy.REPLACE,
    MemoryType.EPISODIC: MergeStrategy.APPEND,
    MemoryType.SEMANTIC: MergeStrategy.CONFIDENCE_GATED,
    MemoryType.PROCEDURAL: MergeStrategy.LLM_MERGE,
    MemoryType.SNAPSHOT: MergeStrategy.REPLACE,  # 同名快照覆盖旧版
}

# 关键词 → 记忆类型（自动检测）
KEYWORD_TYPE_MAP = [
    # snapshot 优先匹配（上下文快照标志）
    (["[快照]", "上下文存档", "会话快照", "memory_snapshot", "关键结论", "已否决方向"], MemoryType.SNAPSHOT),
    # semantic 优先匹配（事实/偏好/知识）
    (["截止日期", "项目", "甲方", "偏好", "姓名", "年龄", "学历", "职业", "喜欢", "不喜欢", "地址", "电话", "邮箱"], MemoryType.SEMANTIC),
    # episodic（事件/时间）
    (["今天", "昨天", "刚才", "上次", "发生了", "记得", "之前", "当时", "那天", "星期"], MemoryType.EPISODIC),
    # working（当前任务）
    (["当前任务", "正在做", "下一步", "待办", "进行中", "手头"], MemoryType.WORKING),
    # procedural（技能/方法）
    (["方法是", "我习惯", "步骤", "流程", "技巧", "方式", "做法", "套路"], MemoryType.PROCEDURAL),
]

# MAX_ENTRIES 默认上限
DEFAULT_MAX_ENTRIES = 50


@dataclass
class Entry:
    """单条记忆条目（facet）"""
    content: str
    confidence: float = 0.5
    timestamp: str = ""
    source: str = "user_stated"
    status: str = "active"  # active / superseded


def detect_memory_type(content: str) -> MemoryType:
    """根据内容关键词自动检测记忆类型"""
    for keywords, mtype in KEYWORD_TYPE_MAP:
        for kw in keywords:
            if kw in content:
                return mtype
    return MemoryType.SEMANTIC  # 默认最安全


def get_default_strategy(memory_type: MemoryType) -> MergeStrategy:
    """获取记忆类型的默认合并策略"""
    return TYPE_DEFAULT_STRATEGY.get(memory_type, MergeStrategy.CONFIDENCE_GATED)


def make_entry(content: str, confidence: float = 0.5, source: str = "user_stated",
               timestamp: str = "", why: str = "") -> dict:
    """创建一条新 entry"""
    entry = {
        "content": content,
        "confidence": confidence,
        "source": source,
        "timestamp": timestamp or "",
        "status": "active",
    }
    if why:
        entry["why"] = why
    return entry


def project_entries(entries: list) -> dict:
    """从 entries 列表中投影出置信度最高的 active entry
    
    Returns:
        dict: {content, confidence, source, timestamp}
    """
    if not entries:
        return {"content": "", "confidence": 0.0, "source": "", "timestamp": ""}
    active = [e for e in entries if e.get("status", "active") == "active"]
    if not active:
        active = entries
    # 取置信度最高的
    best = max(active, key=lambda e: e.get("confidence", 0))
    return {
        "content": best.get("content", ""),
        "confidence": best.get("confidence", 0),
        "source": best.get("source", ""),
        "timestamp": best.get("timestamp", ""),
    }


def apply_strategy(strategy: MergeStrategy, existing_entries: list,
                   new_entry: dict, max_entries: int = DEFAULT_MAX_ENTRIES) -> dict:
    """应用合并策略，返回写入指令
    
    Args:
        strategy: 合并策略
        existing_entries: 现有 entries 列表
        new_entry: 新写入的 entry
        max_entries: entries 上限
    
    Returns:
        dict: {action: "write"|"noop"|"reject", entries: list, reason: str}
    """
    if not existing_entries:
        return {"action": "write", "entries": [new_entry], "reason": "first_entry"}

    if strategy == MergeStrategy.APPEND:
        new_entries = existing_entries + [new_entry]
        if len(new_entries) > max_entries:
            new_entries = new_entries[-max_entries:]
        return {"action": "write", "entries": new_entries, "reason": "appended"}

    if strategy == MergeStrategy.REPLACE:
        # 旧条目标记为 superseded
        for e in existing_entries:
            e["status"] = "superseded"
        return {"action": "write", "entries": [new_entry], "reason": "replaced"}

    if strategy == MergeStrategy.CONFIDENCE_GATED:
        # 新置信度 >= 现有最高置信度才写入
        current_best = max((e.get("confidence", 0) for e in existing_entries if e.get("status", "active") == "active"), default=0)
        new_conf = new_entry.get("confidence", 0)
        if new_conf >= current_best:
            # 旧 active 标记为 superseded
            for e in existing_entries:
                if e.get("status", "active") == "active":
                    e["status"] = "superseded"
            new_entries = existing_entries + [new_entry]
            if len(new_entries) > max_entries:
                # 限幅裁旧：保留最新的 max_entries 条
                new_entries = new_entries[-max_entries:]
            return {"action": "write", "entries": new_entries, "reason": f"new_confidence({new_conf}) >= existing({current_best})"}
        return {"action": "noop", "entries": existing_entries, "reason": f"new_confidence({new_conf}) < existing({current_best})"}

    if strategy == MergeStrategy.LLM_MERGE:
        # LLM_MERGE 需要 LLM 合并文本，由调用方在 new_entry 中放入合并后的内容
        # 这里只做存储：替换旧条目
        for e in existing_entries:
            e["status"] = "superseded"
        return {"action": "write", "entries": [new_entry], "reason": "llm_merged"}

    if strategy == MergeStrategy.MERGE_ON_READ:
        # 存储时 APPEND，读取时再合并
        new_entries = existing_entries + [new_entry]
        if len(new_entries) > max_entries:
            new_entries = new_entries[-max_entries:]
        return {"action": "write", "entries": new_entries, "reason": "merge_on_read"}

    if strategy == MergeStrategy.REJECT:
        return {"action": "reject", "entries": existing_entries, "reason": "rejected_by_strategy"}

    return {"action": "write", "entries": existing_entries + [new_entry], "reason": "default_append"}