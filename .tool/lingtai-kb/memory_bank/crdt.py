# -*- coding: utf-8 -*-
"""
CRDT 无锁合并 — 版本向量 + MV-Register 合并
=============================================
每条 entry 带版本向量（dot），多端并发写入自动合并，不丢失数据。

核心概念：
- 版本向量（VersionVector）：{end_id: counter}，每个端独立递增
- 每个 entry 存储写入时的完整版本向量（dot）
- 合并时比较版本向量：因果支配→被支配方标记 superseded；并发→双方保留

集成方式：见 bank.py 的 _write_unlocked() 中 CRDT 注入逻辑。
"""

import json
from pathlib import Path
from typing import Dict, List


class VersionVector:
    """
    版本向量操作：{end_id: counter}
    
    每个端独立递增自己的计数器。比较两个版本向量判断因果/并发关系。
    """

    @staticmethod
    def compare(a: Dict[str, int], b: Dict[str, int]) -> str:
        """
        比较两个版本向量的因果关系。

        Args:
            a: 版本向量 A
            b: 版本向量 B

        Returns:
            "before": A ⪯ B（A 在 B 之前，B 支配 A）
            "after":  B ⪯ A（B 在 A 之前，A 支配 B）
            "equal":  相等
            "concurrent": 并发（互不支配）
        """
        if a == b:
            return "equal"

        all_keys = set(a.keys()) | set(b.keys())

        # 检查 A ⪯ B: A 的所有键值 <= B 的对应键值，且至少一个严格小于
        a_le_b = True
        a_lt_b = False
        for k in all_keys:
            av = a.get(k, 0)
            bv = b.get(k, 0)
            if av > bv:
                a_le_b = False
            elif av < bv:
                a_lt_b = True

        if a_le_b and a_lt_b:
            return "before"  # A ⪯ B, A ≠ B

        # 检查 B ⪯ A
        b_le_a = True
        b_lt_a = False
        for k in all_keys:
            av = a.get(k, 0)
            bv = b.get(k, 0)
            if bv > av:
                b_le_a = False
            elif bv < av:
                b_lt_a = True

        if b_le_a and b_lt_a:
            return "after"   # B ⪯ A, A ≠ B

        return "concurrent"

    @staticmethod
    def merge(a: Dict[str, int], b: Dict[str, int]) -> Dict[str, int]:
        """合并两个版本向量：取每个键的最大值"""
        keys = set(a.keys()) | set(b.keys())
        return {k: max(a.get(k, 0), b.get(k, 0)) for k in keys}


class CrdtState:
    """
    CRDT 状态追踪器。
    
    每个端有独立的版本向量，持久化到 data_dir/crdt/ 下。
    每次写入递增本端计数器，生成新的版本向量给 entry 使用。
    """

    def __init__(self, end_id: str, data_dir: str = None):
        self.end_id = end_id
        self.data_dir = Path(data_dir) if data_dir else Path.cwd()
        self._state_path = self.data_dir / "crdt" / f"{end_id}.json"
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        self.vector: Dict[str, int] = self._load()

    def _load(self) -> Dict[str, int]:
        if self._state_path.exists():
            try:
                return json.loads(self._state_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                pass
        return {}

    def _save(self):
        self._state_path.write_text(
            json.dumps(self.vector, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def increment(self) -> Dict[str, int]:
        """
        递增本端计数器，返回新的版本向量快照。
        
        Returns:
            Dict[str, int]: 递增后的完整版本向量（副本）
        """
        current = self.vector.get(self.end_id, 0)
        self.vector[self.end_id] = current + 1
        self._save()
        return dict(self.vector)

    def merge_received(self, received: Dict[str, int]):
        """
        合并收到的对端版本向量（用于跨端同步）。
        
        Args:
            received: 对端传来的版本向量
        """
        old = dict(self.vector)
        self.vector = VersionVector.merge(self.vector, received)
        if self.vector != old:
            self._save()

    def sync_from_peers(self):
        """
        扫描 crdt/ 目录下所有其他端的状态文件，合并版本向量。
        
        确保本端知道其他端的最新写入，后续写入时 dot 包含完整因果信息。
        """
        crdt_dir = self.data_dir / "crdt"
        if not crdt_dir.exists():
            return
        old = dict(self.vector)
        for f in sorted(crdt_dir.iterdir()):
            if f.suffix != ".json":
                continue
            peer_id = f.stem
            if peer_id == self.end_id:
                continue
            try:
                peer_vector = json.loads(f.read_text(encoding="utf-8"))
                self.vector = VersionVector.merge(self.vector, peer_vector)
            except (json.JSONDecodeError, OSError):
                continue
        if self.vector != old:
            self._save()


def crdt_merge_entries(
    existing_entries: List[dict],
    new_entry: dict,
    max_entries: int = 50,
) -> List[dict]:
    """
    CRDT 合并两条 entries 数组。

    核心规则：
    - 新 entry 与已有 entries 逐条比较版本向量（dot）
    - 因果支配：被支配的标记为 superseded（新支配旧，或旧支配新）
    - 并发：两条都保留，不标记 superseded
    - 无 CRDT 字段的旧 entry：保守保留（退化安全）

    Args:
        existing_entries: 现有 entries 列表
        new_entry: 新写入的 entry
        max_entries: entries 上限

    Returns:
        List[dict]: 合并后的 entries 列表
    """
    if not existing_entries:
        return [new_entry]

    new_dot = new_entry.get("crdt", {}).get("dot", {})
    result = []
    new_superseded = False

    for existing in existing_entries:
        if existing.get("status") == "superseded":
            # 已 superseded 的 entry 不再参与比较
            result.append(existing)
            continue

        existing_dot = existing.get("crdt", {}).get("dot", {})

        # 任一缺失 CRDT 字段 → 退化：保守保留双方
        if not new_dot or not existing_dot:
            result.append(existing)
            continue

        comparison = VersionVector.compare(new_dot, existing_dot)

        if comparison == "before":
            # 新 entry 在旧 entry 之前 → 旧支配新
            new_superseded = True
            result.append(existing)
        elif comparison == "after":
            # 新 entry 在旧 entry 之后 → 新支配旧
            existing["status"] = "superseded"
            result.append(existing)
        elif comparison == "equal":
            # 相同 dot 但不同 origin → 视为并发，双方保留
            # 相同 dot 且相同 origin → 重复写入，取置信度高的
            new_origin = new_entry.get("crdt", {}).get("origin", "")
            existing_origin = existing.get("crdt", {}).get("origin", "")
            if new_origin and existing_origin and new_origin != existing_origin:
                # 不同 origin → 并发保留
                result.append(existing)
            else:
                # 相同 origin → 新支配旧
                existing["status"] = "superseded"
                result.append(existing)
        else:  # concurrent
            # 并发 → 双方都保留
            result.append(existing)

    if not new_superseded:
        result.append(new_entry)

    # 限幅：保留最新的 max_entries 条
    if len(result) > max_entries:
        result = result[-max_entries:]

    return result