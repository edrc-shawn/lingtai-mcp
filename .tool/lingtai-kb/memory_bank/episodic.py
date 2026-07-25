# -*- coding: utf-8 -*-
"""L1 情景记忆（Episodic Memory）— Phase 1c

记录 per-session 的原始事件流：会话中发生了什么（who/what/when）。
与工作印记的边界：L1 是原料（原始事件流），工作印记是加工品（curated 洞察）。
L1 自动写入、低门槛；工作印记由每日检判断哪些值得提炼上浮。
"""

import os
import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional


class EpisodicMemory:
    """情景记忆 — 以会话为单位的轻量 JSON 存储。

    每条记录的结构：
    {
        "session_id": "uuid",
        "timestamp": "2026-07-12T10:30:00",
        "summary": "模板填充的会话摘要",
        "outcome": "productive|exploratory|inconclusive",
        "events": [{"t": "...", "type": "...", "content": "..."}],
        "decisions": ["..."],
        "follow_up_items": ["..."],
        "files_changed": ["相对路径"],
        "parent_session": "",
        "tags": ["architecture"],
    }
    """

    def __init__(self, vault_path: str):
        self.vault = Path(vault_path)
        self.data_dir = self.vault / ".tool" / "lingtai-kb" / "memory_bank" / "data"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._store_path = self.data_dir / "episodic.jsonl"

    def record(self, summary: str, outcome: str = "productive",
               events: list = None, decisions: list = None,
               follow_up_items: list = None, files_changed: list = None,
               parent_session: str = "", tags: list = None,
               client: str = "") -> dict:
        """写入一条情景记忆记录。

        Args:
            summary: 会话摘要（先用模板填充，后续可升级为 AI 生成）
            outcome: productive / exploratory / inconclusive
            events: 事件列表 [{"t": "HH:MM", "type": "类型", "content": "内容"}]
            decisions: 决策列表
            follow_up_items: 待办事项
            files_changed: 修改的文件路径列表
            parent_session: 跨会话的叙事线（可选）
            tags: 标签列表
            client: 调用端标识（如 "reasonix" / "connector:custom-mcp:lingtai-kb"）

        Returns:
            dict: {session_id, stored: bool, path: str}
        """
        now = datetime.now()
        session_id = f"ses_{now.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"

        entry = {
            "session_id": session_id,
            "timestamp": now.isoformat(),
            "summary": (summary or "")[:500],
            "outcome": outcome if outcome in ("productive", "exploratory", "inconclusive") else "productive",
            "events": (events or [])[:50],
            "decisions": (decisions or [])[:20],
            "follow_up_items": (follow_up_items or [])[:10],
            "files_changed": (files_changed or [])[:20],
            "parent_session": parent_session or "",
            "tags": (tags or [])[:10],
            "client": (client or "")[:50],
        }

        try:
            with open(self._store_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
                f.flush()
            return {"session_id": session_id, "stored": True, "path": str(self._store_path)}
        except OSError as e:
            return {"session_id": session_id, "stored": False, "error": str(e)}

    def query(self, keyword: str = "", limit: int = 10) -> list:
        """搜索情景记忆（关键词匹配 summary/events/decisions）。"""
        if not self._store_path.is_file():
            return []

        keyword_lower = keyword.lower().strip()
        results = []

        try:
            with open(self._store_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    if not keyword_lower:
                        results.append(entry)
                    else:
                        # 搜索 summary、events content、decisions、tags
                        search_text = (
                            entry.get("summary", "") + " " +
                            " ".join(e.get("content", "") for e in entry.get("events", [])) + " " +
                            " ".join(entry.get("decisions", [])) + " " +
                            " ".join(entry.get("tags", []))
                        ).lower()
                        if keyword_lower in search_text:
                            results.append(entry)
        except OSError:
            return []

        # 按时间倒排，取最新
        results.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
        return results[:limit]

    def get_recent(self, days: int = 7, limit: int = 10) -> list:
        """获取近期情景记忆。"""
        from datetime import timedelta
        cutoff = datetime.now() - timedelta(days=days)
        results = []

        if not self._store_path.is_file():
            return []

        try:
            with open(self._store_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                        ts = datetime.fromisoformat(entry.get("timestamp", ""))
                        if ts >= cutoff:
                            results.append(entry)
                    except (json.JSONDecodeError, ValueError, TypeError):
                        continue
        except OSError:
            return []

        results.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
        return results[:limit]

    def get_follow_ups(self) -> list:
        """获取所有未完成的待办事项（从 follow_up_items 非空且未关闭的记录中提取）。"""
        results = self.get_recent(days=30, limit=50)
        follow_ups = []
        for ses in results:
            items = ses.get("follow_up_items", [])
            if items:
                follow_ups.append({
                    "session_id": ses["session_id"],
                    "timestamp": ses["timestamp"],
                    "items": items,
                })
        return follow_ups[:10]