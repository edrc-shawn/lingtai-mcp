# -*- coding: utf-8 -*-
"""
灵台内容注册表（Content Registry）
==================================
全局内容指纹系统。所有写入路径（perception / memory_bank / observation） 
写入内容时，同步注册 SHA-256 指纹，形成统一的内容地图。

用途：
- 精确去重：O(1) 查询"这个内容我见过吗"
- 跨层知晓：一个 content_hash 能查它在原料/记忆/观察各层出现的位置
- 变更检测：巡检时比对内容 hash 可检测静默损坏
- 注入增强：perception_inject 先查注册表，命中即知灵识已见过
"""

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Set


# === 规范哈希 ===

def content_hash(content: str) -> str:
    """
    灵台规范内容哈希（SHA-256）
    
    所有模块统一使用此函数生成内容指纹，确保跨层可对标。
    """
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def mem_id_from_hash(content: str) -> str:
    """
    用规范哈希生成记忆银行 ID（取代 MD5[:8]）
    
    格式：mem_<sha256[:12]>
    - 12 hex chars = 48-bit 空间，碰撞概率 << 1%
    - 与规范哈希同源，可跨层关联
    """
    return "mem_" + content_hash(content)[:12]


# === 注册表 ===

class ContentRegistry:
    """内容注册表"""

    def __init__(self, vault_path: str = None):
        if vault_path is None:
            vault_path = r"."
        self.vault_path = vault_path

        # 存储路径
        self.data_dir = Path(__file__).parent / "data" / "content_registry"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.registry_path = self.data_dir / "registry.json"
        self.mtime_cache_path = self.data_dir / "mtime_cache.json"

        # 加载注册表
        self.registry: dict = self._load()
        
        # v2: mtime 缓存（避免每次全量 SHA-256）
        self._mtime_cache: dict = self._load_mtime_cache()

    def _load(self) -> dict:
        """加载注册表"""
        if self.registry_path.exists():
            try:
                data = json.loads(self.registry_path.read_text(encoding="utf-8"))
                # 确保结构完整
                if "entries" in data and "meta" in data:
                    return data
            except (json.JSONDecodeError, OSError, KeyError):
                pass
        return self._empty_registry()

    @staticmethod
    def _empty_registry() -> dict:
        return {
            "entries": {},       # sha256:h -> entry
            "meta": {
                "created_at": datetime.now(timezone.utc).isoformat(),
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "total_entries": 0,
                "version": 1,
            },
        }

    def _save(self, force: bool = False):
        """持久化注册表（合并写：短时间内多次 register 合并为一次落盘）"""
        self.registry["meta"]["updated_at"] = datetime.now(timezone.utc).isoformat()
        self.registry["meta"]["total_entries"] = len(self.registry["entries"])
        from coalesced_json import coalesced_dump
        coalesced_dump(self.registry_path, self.registry, indent=2, force=force)
    
    def _load_mtime_cache(self) -> dict:
        """加载 mtime 缓存（避免每次 SHA-256 全量扫描）"""
        if self.mtime_cache_path.exists():
            try:
                return json.loads(self.mtime_cache_path.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {}
    
    def _save_mtime_cache(self):
        """持久化 mtime 缓存"""
        self.mtime_cache_path.write_text(
            json.dumps(self._mtime_cache, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    # === 查询 ===

    def lookup(self, content: str) -> Optional[dict]:
        """
        查询内容是否已在注册表中
        
        Args:
            content: 要查询的内容
        
        Returns:
            dict or None: {
                "hash": "sha256:abc...",
                "locations": ["原料/xxx.md"],
                "first_seen": "...",
                "last_seen": "...",
                "appearances": 1,
                "modules": ["perception"],
                "type": "raw_material",
            } 或 None
        """
        key = "sha256:" + content_hash(content)
        entry = self.registry["entries"].get(key)
        if entry:
            return {
                "hash": key,
                **entry,
            }
        return None

    def lookup_by_hash(self, hash_hex: str) -> Optional[dict]:
        """
        按哈希值查询（支持完整或前缀匹配）
        
        Args:
            hash_hex: SHA-256 hex 或前缀
        
        Returns:
            dict or None
        """
        # 精确匹配
        key = f"sha256:{hash_hex}"
        if key in self.registry["entries"]:
            return {"hash": key, **self.registry["entries"][key]}

        # 前缀匹配
        if len(hash_hex) >= 8:
            for k, v in self.registry["entries"].items():
                if k.startswith(f"sha256:{hash_hex}"):
                    return {"hash": k, **v}

        return None

    # === 注册 ===

    def register(
        self,
        content: str,
        location: str,
        module: str = "perception",
        content_type: str = "raw_material",
    ) -> dict:
        """
        注册内容到注册表
        
        Args:
            content: 内容全文
            location: 位置标识（如 "原料/xxx.md" 或 "mem_abc123"）
            module: 来源模块（perception | memory_bank | observation）
            content_type: 内容类型（raw_material | memory | observation）
        
        Returns:
            dict: 注册结果
        """
        key = "sha256:" + content_hash(content)
        now = datetime.now(timezone.utc).isoformat()

        if key in self.registry["entries"]:
            # 更新已有条目
            entry = self.registry["entries"][key]
            if location not in entry["locations"]:
                entry["locations"].append(location)
                entry["appearances"] += 1
            if module not in entry["modules"]:
                entry["modules"].append(module)
            entry["last_seen"] = now
            action = "updated"
        else:
            # 创建新条目
            self.registry["entries"][key] = {
                "locations": [location],
                "first_seen": now,
                "last_seen": now,
                "appearances": 1,
                "modules": [module],
                "type": content_type,
            }
            action = "created"

        self._save()
        return {
            "action": action,
            "hash": key,
            "appearances": self.registry["entries"][key]["appearances"],
            "locations": self.registry["entries"][key]["locations"],
        }

    def unregister(self, content: str, location: str) -> bool:
        """
        从注册表中移除一个位置
        
        Args:
            content: 内容
            location: 要移除的位置
        
        Returns:
            bool: 是否成功移除
        """
        key = "sha256:" + content_hash(content)
        if key not in self.registry["entries"]:
            return False

        entry = self.registry["entries"][key]
        if location in entry["locations"]:
            entry["locations"].remove(location)
            entry["appearances"] -= 1
            entry["last_seen"] = datetime.now(timezone.utc).isoformat()

            if entry["appearances"] <= 0 or not entry["locations"]:
                # 无剩余位置 → 删除条目
                del self.registry["entries"][key]
            self._save()
            return True

        return False

    # === 搜索 ===

    def search_by_type(self, content_type: str) -> List[dict]:
        """按内容类型搜索"""
        results = []
        for key, entry in self.registry["entries"].items():
            if entry["type"] == content_type:
                results.append({"hash": key, **entry})
        return results

    def search_by_module(self, module: str) -> List[dict]:
        """按来源模块搜索"""
        results = []
        for key, entry in self.registry["entries"].items():
            if module in entry["modules"]:
                results.append({"hash": key, **entry})
        return results

    # === 维护 ===

    def build_from_scan(self, force: bool = False) -> dict:
        """
        全量扫描灵台，重建注册表
        
        扫描范围：
        - 原料/ 目录（全部原料文件）
        - 记忆银行（memory_bank/data/memories.json）
        - 观察引擎（observation/observations.json）
        
        v2 优化：mtime 增量——跳过 mtime 未变的文件，仅对新/修改文件做 SHA-256。
        
        Args:
            force: 强制重建
        
        Returns:
            dict: {
                "total_entries": 注册表总量，
                "stats": {
                    "raw_material": {"found": N, "newly_registered": M, "skipped": S},
                    ...
                }
            }
        """
        if force:
            self.registry = self._empty_registry()
            self._mtime_cache = {}

        stats = {
            "raw_material": {"found": 0, "newly_registered": 0, "skipped": 0},
            "memory": {"found": 0, "newly_registered": 0},
            "observation": {"found": 0, "newly_registered": 0},
        }

        vault = Path(self.vault_path)

        # 1. 扫描原料目录（v2: mtime 增量）
        raw_dir = vault / "原料"
        if raw_dir.exists():
            for f in raw_dir.glob("*.md"):
                stats["raw_material"]["found"] += 1
                rel = f"原料/{f.name}"
                
                # mtime 检查：跳过未变更文件
                try:
                    current_mtime = f.stat().st_mtime
                    cached_mtime = self._mtime_cache.get(rel, 0)
                    if current_mtime == cached_mtime:
                        stats["raw_material"]["skipped"] += 1
                        continue
                except Exception:
                    pass
                
                try:
                    content = f.read_text(encoding="utf-8")
                    key = "sha256:" + content_hash(content)

                    if key not in self.registry["entries"]:
                        self.registry["entries"][key] = {
                            "locations": [rel],
                            "first_seen": datetime.now(timezone.utc).isoformat(),
                            "last_seen": datetime.now(timezone.utc).isoformat(),
                            "appearances": 1,
                            "modules": ["perception"],
                            "type": "raw_material",
                        }
                        stats["raw_material"]["newly_registered"] += 1
                    
                    # 更新 mtime 缓存
                    try:
                        self._mtime_cache[rel] = current_mtime
                    except Exception:
                        pass
                    
                except Exception:
                    pass

        # 2. 扫描记忆银行
        mem_path = Path(__file__).parent / "memory_bank" / "data" / "memories.json"
        if mem_path.exists():
            try:
                memories = json.loads(mem_path.read_text(encoding="utf-8"))
                for mem in memories:
                    stats["memory"]["found"] += 1
                    content = mem.get("content", "")
                    if content:
                        mem_id = mem.get("id", mem_id_from_hash(content))
                        key = "sha256:" + content_hash(content)

                        if key not in self.registry["entries"]:
                            self.registry["entries"][key] = {
                                "locations": [mem_id],
                                "first_seen": mem.get("created_at", datetime.now(timezone.utc).isoformat()),
                                "last_seen": mem.get("updated_at", datetime.now(timezone.utc).isoformat()),
                                "appearances": 1,
                                "modules": ["memory_bank"],
                                "type": "memory",
                            }
                            stats["memory"]["newly_registered"] += 1
            except Exception:
                pass

        # 3. 扫描观察引擎
        obs_path = Path(__file__).parent / "observation" / "observations.json"
        if obs_path.exists():
            try:
                obs_data = json.loads(obs_path.read_text(encoding="utf-8"))
                for obs_entry in obs_data.get("observations", []):
                    for fact in obs_entry.get("facts", []):
                        stats["observation"]["found"] += 1
                        content = fact.get("content", "")
                        if content:
                            key = "sha256:" + content_hash(content)

                            if key not in self.registry["entries"]:
                                self.registry["entries"][key] = {
                                    "locations": [f"observation:{obs_entry.get('topic', 'unknown')}"],
                                    "first_seen": fact.get("added_at", datetime.now(timezone.utc).isoformat()),
                                    "last_seen": fact.get("added_at", datetime.now(timezone.utc).isoformat()),
                                    "appearances": 1,
                                    "modules": ["observation"],
                                    "type": "observation",
                                }
                                stats["observation"]["newly_registered"] += 1
            except Exception:
                pass

        self._save()
        self._save_mtime_cache()  # v2: 持久化 mtime 缓存
        return {
            "total_entries": len(self.registry["entries"]),
            "stats": stats,
            "_mtime_cached": len(self._mtime_cache),
        }

    def stats(self) -> dict:
        """注册表统计"""
        by_type: Dict[str, int] = {}
        by_module: Dict[str, int] = {}
        total_appearances = 0

        for key, entry in self.registry["entries"].items():
            t = entry["type"]
            by_type[t] = by_type.get(t, 0) + 1

            for mod in entry["modules"]:
                by_module[mod] = by_module.get(mod, 0) + 1

            total_appearances += entry["appearances"]

        return {
            "total_entries": len(self.registry["entries"]),
            "total_appearances": total_appearances,
            "by_type": by_type,
            "by_module": by_module,
            "version": self.registry["meta"]["version"],
            "created_at": self.registry["meta"]["created_at"],
            "updated_at": self.registry["meta"]["updated_at"],
        }

    def dump(self) -> dict:
        """导出完整注册表（用于调试/巡检）"""
        return dict(self.registry)


# === 工厂函数 ===

def create_content_registry(vault_path: str = None) -> ContentRegistry:
    """创建内容注册表实例"""
    return ContentRegistry(vault_path)
