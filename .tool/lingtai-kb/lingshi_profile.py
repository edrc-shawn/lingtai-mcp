# -*- coding: utf-8 -*-
"""
灵台MCP - 用户画像模块
======================
本地化用户画像，供其他agents学习。

数据源：
- 灵台/.tool/lingtai-kb/profile.json（本地副本）
- ~/.workbuddy/MEMORY.md（WorkBuddy源）
- ~/.workbuddy/IDENTITY.md（系统架构）

功能：
- 读取用户画像
- 更新用户画像
- 导出用户画像
"""

import os
import json
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional, Any


class UserProfile:
    """用户画像管理"""
    
    def __init__(self, vault_path: str = None):
        """
        初始化
        
        Args:
            vault_path: 灵台vault路径
        """
        if vault_path is None:
            self.vault_path = r"."
        else:
            self.vault_path = vault_path
        
        # 本地画像路径
        self.profile_path = Path(__file__).parent / "profile.json"
        
        # WorkBuddy 路径
        self.wb_memory = Path(os.path.expanduser("~")) / ".workbuddy" / "MEMORY.md"
        self.wb_identity = Path(os.path.expanduser("~")) / ".workbuddy" / "IDENTITY.md"
        
        # 加载画像
        self.profile = self._load_profile()
    
    def _load_profile(self) -> dict:
        """加载用户画像"""
        if self.profile_path.exists():
            try:
                with open(self.profile_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        
        # 从 WorkBuddy 同步
        return self._sync_from_workbuddy()
    
    def _sync_from_workbuddy(self) -> dict:
        """从 WorkBuddy 同步用户画像"""
        profile = {
            "name": "",
            "preferences": {},
            "history": [],
            "synced_at": datetime.now().isoformat(),
        }
        
        # 读取 MEMORY.md
        if self.wb_memory.exists():
            try:
                content = self.wb_memory.read_text(encoding="utf-8")
                profile["memory_content"] = content[:2000]  # 截取前2000字符
            except Exception:
                pass
        
        # 读取 IDENTITY.md
        if self.wb_identity.exists():
            try:
                content = self.wb_identity.read_text(encoding="utf-8")
                profile["identity_content"] = content[:2000]
            except Exception:
                pass
        
        # 保存本地副本
        self._save_profile(profile)
        
        return profile
    
    def _save_profile(self, profile: dict):
        """保存用户画像"""
        profile["updated_at"] = datetime.now().isoformat()
        try:
            with open(self.profile_path, "w", encoding="utf-8") as f:
                json.dump(profile, f, ensure_ascii=False, indent=2)
        except Exception:
            pass
    
    def get_profile(self) -> dict:
        """获取用户画像"""
        return self.profile
    
    def update_profile(self, updates: dict):
        """更新用户画像"""
        self.profile.update(updates)
        self._save_profile(self.profile)
    
    def get_summary(self) -> dict:
        """获取用户画像摘要"""
        return {
            "name": self.profile.get("name", "未知用户"),
            "has_memory": bool(self.profile.get("memory_content")),
            "has_identity": bool(self.profile.get("identity_content")),
            "synced_at": self.profile.get("synced_at", "未同步"),
            "push_count": len(self.profile.get("pushes", [])),
        }

    def push(self, key: str, value: str, category: str = "general", source: str = "mcp") -> dict:
        """
        推送记忆到画像（其他agent调用）

        Args:
            key: 记忆键（如"偏好_回复风格"、"习惯_工作时间"）
            value: 记忆值
            category: 类别（preference/habit/fact/feature）
            source: 来源标识（哪个agent推的）

        Returns:
            dict: 推送结果
        """
        if "pushes" not in self.profile:
            self.profile["pushes"] = []

        # 按key去重：同key更新value
        found = False
        for p in self.profile["pushes"]:
            if p.get("key") == key:
                old_value = p.get("value", "")
                p["value"] = value
                p["category"] = category
                p["source"] = source
                p["updated_at"] = datetime.now().isoformat()
                found = True
                break

        if not found:
            self.profile["pushes"].append({
                "key": key,
                "value": value,
                "category": category,
                "source": source,
                "created_at": datetime.now().isoformat(),
                "updated_at": datetime.now().isoformat(),
            })

        self._save_profile(self.profile)

        return {
            "success": True,
            "action": "updated" if found else "created",
            "key": key,
            "push_count": len(self.profile["pushes"]),
        }

    def push_batch(self, items: list) -> dict:
        """
        批量推送记忆

        Args:
            items: [{"key": "...", "value": "...", "category": "..."}]

        Returns:
            dict: 批量推送结果
        """
        results = []
        for item in items:
            r = self.push(
                key=item.get("key", ""),
                value=item.get("value", ""),
                category=item.get("category", "general"),
                source=item.get("source", "mcp"),
            )
            results.append(r)
        return {"success": True, "count": len(results), "results": results}

    def get_pushes(self, category: str = None) -> list:
        """
        获取推送的记忆

        Args:
            category: 按类别筛选（可选）

        Returns:
            list: 推送记忆列表
        """
        pushes = self.profile.get("pushes", [])
        if category:
            pushes = [p for p in pushes if p.get("category") == category]
        return pushes


# 便捷函数
def create_user_profile(vault_path: str = None) -> UserProfile:
    """创建用户画像实例"""
    return UserProfile(vault_path)
