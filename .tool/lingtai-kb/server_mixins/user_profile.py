# -*- coding: utf-8 -*-
"""用户画像 mixin"""
import os
from datetime import datetime
from decorators import tool

class UserProfileMixin:
    @tool(readonly=True, write=False, category="user", system=False, name="user_push")
    def memory_push(self, key: str, value: str, category: str = "general", source: str = "mcp") -> dict:
        """
        推送记忆到用户画像（即时生效，其他agent可读）
        """
        return self.user_profile.push(key, value, category, source)

    def memory_push_batch(self, items: list) -> dict:
        """
        批量推送记忆
        """
        return self.user_profile.push_batch(items)

    def memory_get_pushes(self, category: str = None) -> dict:
        """
        获取推送的记忆
        """
        pushes = self.user_profile.get_pushes(category)
        return {"pushes": pushes, "count": len(pushes)}

    @tool(readonly=True, write=False, category="user", system=False)
    def user_feedback(self, what: str, correction: str = "") -> dict:
        """
        用户纠正/确认反馈（越用越懂）
        
        Args:
            what: 什么内容/行为被纠正或确认
            correction: 纠正方向。留空表示确认（positive）
        """
        if correction:
            # 纠正型反馈
            self.user_profile.record_correction(what, correction)
            push_key = f"纠正_{what}"
            push_value = correction
            existing = self.user_profile.get_pushes("correction")
            for p in existing:
                if p.get("key") == push_key:
                    p["value"] = correction
                    p["updated_at"] = datetime.now().isoformat()
                    break
            else:
                self.user_profile.data.setdefault("pushes", []).append({
                    "key": push_key, "value": push_value,
                    "category": "correction", "source": "feedback",
                    "created_at": datetime.now().isoformat(),
                    "updated_at": datetime.now().isoformat(),
                })
            self.user_profile._save()
            return {"success": True, "type": "correction", "what": what, "correction": correction}
        else:
            # 确认型反馈（positive signal）
            self.user_profile.record_correction(what, "(confirmed)")
            return {"success": True, "type": "confirmation", "what": what}
