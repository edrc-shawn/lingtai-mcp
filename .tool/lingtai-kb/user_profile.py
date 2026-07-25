# -*- coding: utf-8 -*-
"""
灵台MCP - 用户学习层
====================
让灵识越用越懂用户。不依赖外部文件，基于交互行为自动构建。

功能：
- 兴趣跟踪：记录用户常查的域和关键词
- 偏好学习：记录用户纠正（"别这样"）和确认（"对"）
- 画像增强：将积累的学习注入 profile 工具
"""

import os
import json
from pathlib import Path
from datetime import datetime
from collections import defaultdict
from typing import Dict, List, Optional


class UserProfile:
    """用户画像学习引擎"""
    
    def __init__(self, vault_path: str = None):
        if vault_path is None:
            self.vault_path = r"."
        else:
            self.vault_path = vault_path
        
        self.store_dir = Path(__file__).parent / "profile"
        self.store_dir.mkdir(exist_ok=True)
        self.data_path = self.store_dir / "user_profile.json"
        
        self.data = self._load()
    
    def _load(self) -> dict:
        if self.data_path.exists():
            try:
                return json.loads(self.data_path.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {
            "interests": {},          # {domain: query_count}
            "recent_queries": [],     # [(keyword, timestamp), ...]
            "preferences": [],        # [{type, content, source}, ...]
            "corrections": [],        # [{what, correction, timestamp}, ...]
            "session_count": 0,
            "first_seen": datetime.now().isoformat(),
            "last_updated": datetime.now().isoformat(),
        }
    
    def _save(self):
        self.data["last_updated"] = datetime.now().isoformat()
        self.data_path.write_text(
            json.dumps(self.data, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )
    
    def record_query(self, keyword: str):
        """记录用户查询"""
        self.data["recent_queries"].append([keyword, datetime.now().isoformat()])
        if len(self.data["recent_queries"]) > 100:
            self.data["recent_queries"] = self.data["recent_queries"][-50:]
        self._save()
    
    def record_interest(self, domain: str):
        """记录对某个域的关注"""
        self.data["interests"][domain] = self.data["interests"].get(domain, 0) + 1
        self._save()
    
    def record_correction(self, what: str, correction: str):
        """记录用户纠正（越用越懂的关键）"""
        self.data["corrections"].append({
            "what": what,
            "correction": correction,
            "timestamp": datetime.now().isoformat(),
        })
        if len(self.data["corrections"]) > 50:
            self.data["corrections"] = self.data["corrections"][-30:]
        self._save()
    
    def record_session(self):
        """记录一次会话"""
        self.data["session_count"] += 1
        self._save()
    
    def get_profile_summary(self) -> dict:
        """生成画像摘要"""
        data = self.data
        
        # 热点域
        top_domains = sorted(data["interests"].items(), key=lambda x: -x[1])[:5]
        
        # 最近查询（去重）
        recent = []
        seen = set()
        for kw, ts in reversed(data["recent_queries"]):
            if kw not in seen:
                seen.add(kw)
                recent.append(kw)
            if len(recent) >= 10:
                break
        
        # 最近纠正
        recent_corrections = data["corrections"][-5:]
        
        return {
            "session_count": data["session_count"],
            "top_domains": [{"domain": d, "count": c} for d, c in top_domains],
            "recent_queries": recent,
            "recent_corrections": recent_corrections,
            "learning_since": data["first_seen"][:10],
            "push_count": len(data.get("pushes", [])),
        }

    def push(self, key: str, value: str, category: str = "general", source: str = "mcp") -> dict:
        """
        推送记忆到画像（其他agent调用，即时生效）

        Args:
            key: 记忆键（如"偏好_回复风格"、"习惯_工作时间"）
            value: 记忆值
            category: 类别（preference/habit/fact/feature）
            source: 来源标识

        Returns:
            dict: 推送结果
        """
        if "pushes" not in self.data:
            self.data["pushes"] = []

        found = False
        for p in self.data["pushes"]:
            if p.get("key") == key:
                p["value"] = value
                p["category"] = category
                p["source"] = source
                p["updated_at"] = datetime.now().isoformat()
                found = True
                break

        if not found:
            self.data["pushes"].append({
                "key": key,
                "value": value,
                "category": category,
                "source": source,
                "created_at": datetime.now().isoformat(),
                "updated_at": datetime.now().isoformat(),
            })

        self._save()

        return {
            "success": True,
            "action": "updated" if found else "created",
            "key": key,
            "push_count": len(self.data["pushes"]),
        }

    def push_batch(self, items: list) -> dict:
        """批量推送记忆"""
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
        """获取推送的记忆"""
        pushes = self.data.get("pushes", [])
        if category:
            pushes = [p for p in pushes if p.get("category") == category]
        return pushes