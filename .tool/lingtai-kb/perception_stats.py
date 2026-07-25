# -*- coding: utf-8 -*-
"""
灵台MCP - 感知命中率统计模块
==============================
追踪感知规则的触发频率和命中率。

统计项：
- 规则1（知识注入）：触发次数、命中次数、命中率
- 规则2（自动学习）：触发次数、保存次数、保存率
- 规则3（关联推荐）：触发次数、推荐次数、推荐率
- 规则4（会话上下文）：生成次数
- 规则5（检索纪律）：触发次数、完整执行次数、执行率

数据存储：JSON文件（轻量级，无需SQLite）
"""

import json
import os
import atexit
from logger import get_logger

log = get_logger(__name__)
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, List, Optional


class PerceptionStats:
    """灵台灵识感知命中率统计（v2: write-back cache 优化）"""
    
    # 写盘批处理阈值：积累 N 次变更后才真正写盘
    _BATCH_FLUSH = 10
    # 时间阈值：超过 N 秒未 flush 时自动写盘
    _TIME_FLUSH_SEC = 30
    
    def __init__(self, data_dir: str = None):
        """
        初始化统计模块
        
        Args:
            data_dir: 数据目录路径
        """
        if data_dir is None:
            self.data_dir = Path(r".\.meta")
        else:
            self.data_dir = Path(data_dir)
        
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.stats_file = self.data_dir / "perception_stats.json"
        
        # 加载或初始化统计
        self.stats = self._load_stats()
        
        # Write-back cache（优化：不再每次 record 都写盘）
        self._dirty = False
        self._pending_count = 0
        self._last_flush = datetime.now()
        
        # 注册解释器关闭时 flush（atexit 在 builtins 被清理前执行）
        atexit.register(self.flush)
        
        # Sentinel: 确保违规追踪字段存在（向后兼容）
        if "violations" not in self.stats:
            self.stats["violations"] = {}
        if "corrective_actions" not in self.stats:
            self.stats["corrective_actions"] = []
        if "consecutive_violations" not in self.stats:
            self.stats["consecutive_violations"] = {}
    
    def _new_stats(self) -> dict:
        """生成新统计默认结构"""
        return {
            "rules": {
                "rule1_inject": {"triggered": 0, "hit": 0},
                "rule2_learn": {"triggered": 0, "saved": 0},
                "rule3_recommend": {"triggered": 0, "recommended": 0},
                "rule4_context": {"generated": 0},
                "rule5_search": {"triggered": 0, "completed": 0},
                "lingshi_inject": {"triggered": 0, "found": 0, "used": 0},
            },
            "daily": {},
            "updated_at": datetime.now().isoformat(),
        }

    def _ensure_schema(self, stats: dict, defaults: dict) -> dict:
        """递归合并缺失字段（向后兼容旧格式）"""
        for key, value in defaults.items():
            if key not in stats:
                stats[key] = value
            elif isinstance(value, dict) and isinstance(stats.get(key), dict):
                self._ensure_schema(stats[key], value)
        return stats

    def _load_stats(self) -> dict:
        """加载统计文件（含向后兼容合并）"""
        if self.stats_file.exists():
            try:
                with open(self.stats_file, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                return self._ensure_schema(loaded, self._new_stats())
            except (json.JSONDecodeError, OSError):
                pass
        
        return self._new_stats()
    
    def _save_stats(self):
        """标记脏数据，批量写盘（优化：避免每次 record 都 IO）"""
        self._dirty = True
        self._pending_count += 1
        # 批量阈值触发写盘
        if self._pending_count >= self._BATCH_FLUSH:
            self.flush()
        else:
            # 时间阈值触发写盘（防止长期不写盘导致丢失）
            elapsed = (datetime.now() - self._last_flush).total_seconds()
            if elapsed >= self._TIME_FLUSH_SEC:
                self.flush()
    
    def flush(self):
        """强制写盘（外部调用或批量阈值触发）"""
        if not self._dirty:
            return
        self.stats["updated_at"] = datetime.now().isoformat()
        try:
            with open(self.stats_file, "w", encoding="utf-8") as f:
                json.dump(self.stats, f, indent=2, ensure_ascii=False)
        except Exception as e:
            log.warning("perception_stats save error: %s", e)
        else:
            self._dirty = False
            self._pending_count = 0
            self._last_flush = datetime.now()
    
    def __del__(self):
        """析构时确保脏数据不丢失（解释器关闭时静默失败）"""
        try:
            self.flush()
        except Exception:
            pass  # 解释器关闭时 builtins 可能已被清理，静默忽略
    
    def record_rule1(self, hit: bool):
        """
        记录规则1（知识注入）触发
        
        Args:
            hit: 是否命中知识
        """
        self.stats["rules"]["rule1_inject"]["triggered"] += 1
        if hit:
            self.stats["rules"]["rule1_inject"]["hit"] += 1
        
        # 更新每日统计
        today = datetime.now().strftime("%Y-%m-%d")
        if today not in self.stats["daily"]:
            self.stats["daily"][today] = {"rule1": 0, "rule2": 0, "rule3": 0, "rule4": 0, "rule5": 0}
        self.stats["daily"][today]["rule1"] += 1
        
        self._save_stats()
    
    def record_rule2(self, saved: bool):
        """
        记录规则2（自动学习）触发
        
        Args:
            saved: 是否成功保存
        """
        self.stats["rules"]["rule2_learn"]["triggered"] += 1
        if saved:
            self.stats["rules"]["rule2_learn"]["saved"] += 1
        
        # 更新每日统计
        today = datetime.now().strftime("%Y-%m-%d")
        if today not in self.stats["daily"]:
            self.stats["daily"][today] = {"rule1": 0, "rule2": 0, "rule3": 0, "rule4": 0, "rule5": 0}
        self.stats["daily"][today]["rule2"] += 1
        
        self._save_stats()
    
    def record_rule3(self, recommended: bool):
        """
        记录规则3（关联推荐）触发
        
        Args:
            recommended: 是否推荐了页面
        """
        self.stats["rules"]["rule3_recommend"]["triggered"] += 1
        if recommended:
            self.stats["rules"]["rule3_recommend"]["recommended"] += 1
        
        # 更新每日统计
        today = datetime.now().strftime("%Y-%m-%d")
        if today not in self.stats["daily"]:
            self.stats["daily"][today] = {"rule1": 0, "rule2": 0, "rule3": 0, "rule4": 0, "rule5": 0}
        self.stats["daily"][today]["rule3"] += 1
        
        self._save_stats()
    
    def record_rule4(self):
        """记录规则4（会话上下文）生成"""
        self.stats["rules"]["rule4_context"]["generated"] += 1
        
        # 更新每日统计
        today = datetime.now().strftime("%Y-%m-%d")
        if today not in self.stats["daily"]:
            self.stats["daily"][today] = {"rule1": 0, "rule2": 0, "rule3": 0, "rule4": 0, "rule5": 0}
        self.stats["daily"][today]["rule4"] += 1
        
        self._save_stats()
    
    def record_rule5(self, completed: bool):
        """
        记录规则5（检索纪律）触发
        
        Args:
            completed: 是否找到结果（当前实现测量的是"是否有结果"而非"三步完成"）
        """
        self.stats["rules"]["rule5_search"]["triggered"] += 1
        if completed:
            self.stats["rules"]["rule5_search"]["completed"] += 1
        
        # 更新每日统计
        today = datetime.now().strftime("%Y-%m-%d")
        if today not in self.stats["daily"]:
            self.stats["daily"][today] = {"rule1": 0, "rule2": 0, "rule3": 0, "rule4": 0, "rule5": 0}
        self.stats["daily"][today]["rule5"] += 1
        
        self._save_stats()
    
    def record_lingshi_inject(self, found: bool, used: bool = False):
        """
        记录灵识注入调用
        
        Args:
            found: 是否找到匹配的灵识记忆
            used: AI 是否在回复中使用了注入内容
        """
        self.stats["rules"]["lingshi_inject"]["triggered"] += 1
        if found:
            self.stats["rules"]["lingshi_inject"]["found"] += 1
        if used:
            self.stats["rules"]["lingshi_inject"]["used"] += 1
        self._save_stats()
    
    def get_hit_rates(self) -> dict:
        """
        获取各规则的命中率
        
        Returns:
            dict: 命中率统计
        """
        rules = self.stats["rules"]
        
        # 规则1命中率
        r1_triggered = rules["rule1_inject"]["triggered"]
        r1_hit = rules["rule1_inject"]["hit"]
        r1_rate = r1_hit / r1_triggered * 100 if r1_triggered > 0 else 0
        
        # 规则2保存率
        r2_triggered = rules["rule2_learn"]["triggered"]
        r2_saved = rules["rule2_learn"]["saved"]
        r2_rate = r2_saved / r2_triggered * 100 if r2_triggered > 0 else 0
        
        # 规则3推荐率
        r3_triggered = rules["rule3_recommend"]["triggered"]
        r3_recommended = rules["rule3_recommend"]["recommended"]
        r3_rate = r3_recommended / r3_triggered * 100 if r3_triggered > 0 else 0
        
        # 规则5执行率
        r5_triggered = rules["rule5_search"]["triggered"]
        r5_completed = rules["rule5_search"]["completed"]
        r5_rate = r5_completed / r5_triggered * 100 if r5_triggered > 0 else 0
        
        return {
            "rule1_inject": {
                "triggered": r1_triggered,
                "hit": r1_hit,
                "rate": round(r1_rate, 1),
            },
            "rule2_learn": {
                "triggered": r2_triggered,
                "saved": r2_saved,
                "rate": round(r2_rate, 1),
            },
            "rule3_recommend": {
                "triggered": r3_triggered,
                "recommended": r3_recommended,
                "rate": round(r3_rate, 1),
            },
            "rule4_context": {
                "generated": rules["rule4_context"]["generated"],
            },
            "rule5_search": {
                "triggered": r5_triggered,
                "completed": r5_completed,
                "rate": round(r5_rate, 1),
            },
            "lingshi_inject": {
                "triggered": rules["lingshi_inject"]["triggered"],
                "found": rules["lingshi_inject"]["found"],
                "used": rules["lingshi_inject"]["used"],
            },
        }
    
    def get_daily_stats(self, days: int = 7) -> list:
        """
        获取每日统计
        
        Args:
            days: 最近N天
        
        Returns:
            list: 每日统计列表
        """
        result = []
        
        for i in range(days):
            date = (datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d")
            daily = self.stats["daily"].get(date, {"rule1": 0, "rule2": 0, "rule3": 0, "rule4": 0, "rule5": 0})
            
            result.append({
                "date": date,
                "rule1": daily.get("rule1", 0),
                "rule2": daily.get("rule2", 0),
                "rule3": daily.get("rule3", 0),
                "rule4": daily.get("rule4", 0),
                "rule5": daily.get("rule5", 0),
                "total": sum(daily.values()),
            })
        
        return result
    
    def get_summary(self) -> dict:
        """
        获取统计摘要
        
        Returns:
            dict: 统计摘要
        """
        hit_rates = self.get_hit_rates()
        daily = self.get_daily_stats(7)
        
        # 计算总计
        total_triggered = (
            hit_rates["rule1_inject"]["triggered"] +
            hit_rates["rule2_learn"]["triggered"] +
            hit_rates["rule3_recommend"]["triggered"] +
            hit_rates["rule5_search"]["triggered"]
        )
        
        total_hit = (
            hit_rates["rule1_inject"]["hit"] +
            hit_rates["rule2_learn"]["saved"] +
            hit_rates["rule3_recommend"]["recommended"] +
            hit_rates["rule5_search"]["completed"]
        )
        
        return {
            "hit_rates": hit_rates,
            "daily_stats": daily,
            "total_triggered": total_triggered,
            "total_hit": total_hit,
            "overall_rate": round(total_hit / total_triggered * 100, 1) if total_triggered > 0 else 0,
        }


    # ==================== 监控层 ====================
    
    def check_violations(self) -> List[dict]:
        """
        检查感知规则违规
        
        Returns:
            list: 违规列表
        """
        violations = []
        
        # 规则1：知识注入命中率过低
        rule1 = self.stats["rules"]["rule1_inject"]
        if rule1["triggered"] > 10 and rule1["hit"] / rule1["triggered"] < 0.3:
            violations.append({
                "rule": "rule1_inject",
                "type": "low_hit_rate",
                "severity": "warning",
                "detail": f"知识注入命中率过低: {rule1['hit']}/{rule1['triggered']} ({rule1['hit']/rule1['triggered']*100:.1f}%)",
                "suggestion": "检查感知规则是否正确触发"
            })
        
        # 规则2：自动学习保存率过低
        rule2 = self.stats["rules"]["rule2_learn"]
        if rule2["triggered"] > 5 and rule2["saved"] / rule2["triggered"] < 0.2:
            violations.append({
                "rule": "rule2_learn",
                "type": "low_save_rate",
                "severity": "warning",
                "detail": f"自动学习保存率过低: {rule2['saved']}/{rule2['triggered']} ({rule2['saved']/rule2['triggered']*100:.1f}%)",
                "suggestion": "检查学习条件是否过于严格"
            })
        
        # 规则5：检索纪律执行率过低
        rule5 = self.stats["rules"]["rule5_search"]
        if rule5["triggered"] > 5 and rule5["completed"] / rule5["triggered"] < 0.5:
            violations.append({
                "rule": "rule5_search",
                "type": "low_completion_rate",
                "severity": "error",
                "detail": f"检索纪律执行率过低: {rule5['completed']}/{rule5['triggered']} ({rule5['completed']/rule5['triggered']*100:.1f}%)",
                "suggestion": "检查是否跳过了三步检索管线"
            })
        
        return violations
    
    def get_monitoring_report(self) -> dict:
        """
        获取监控报告
        
        Returns:
            dict: 监控报告
        """
        summary = self.get_summary()
        violations = self.check_violations()
        
        return {
            "timestamp": datetime.now().isoformat(),
            "summary": summary,
            "violations": violations,
            "violation_count": len(violations),
            "health_status": "healthy" if not violations else "warning" if all(v["severity"] == "warning" for v in violations) else "error",
        }


# 便捷函数
def create_perception_stats(data_dir: str = None) -> PerceptionStats:
    """创建感知统计实例"""
    return PerceptionStats(data_dir)


if __name__ == "__main__":
    # 测试
    stats = PerceptionStats()
    
    print("感知命中率统计测试")
    print("=" * 50)
    
    # 模拟一些触发
    stats.record_rule1(hit=True)
    stats.record_rule1(hit=True)
    stats.record_rule1(hit=False)
    stats.record_rule2(saved=True)
    stats.record_rule2(saved=False)
    stats.record_rule3(recommended=True)
    stats.record_rule4()
    stats.record_rule5(completed=True)
    stats.record_rule5(completed=True)
    stats.record_rule5(completed=False)
    
    # 强制写盘以确保数据持久化
    stats.flush()
    
    # 获取统计
    summary = stats.get_summary()
    
    print(f"\n统计摘要:")
    print(f"  总触发: {summary['total_triggered']}")
    print(f"  总命中: {summary['total_hit']}")
    print(f"  总命中率: {summary['overall_rate']}%")
    
    print(f"\n各规则命中率:")
    for rule, data in summary["hit_rates"].items():
        if "rate" in data:
            print(f"  {rule}: {data['rate']}%")
        else:
            print(f"  {rule}: {data.get('generated', 0)} 次")
    
    print("\n✅ 测试完成")
