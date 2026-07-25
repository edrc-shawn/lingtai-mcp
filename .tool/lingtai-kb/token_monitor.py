# -*- coding: utf-8 -*-
"""
灵台MCP - Token监测模块
======================
基于灵识的Token监测功能，适配灵台的Markdown知识管理系统。

功能：
- 实时统计Token消耗
- 节省量计算
- 费用估算
- 每日报告生成
- 文本图表显示
- 趋势分析
"""

import json
import sqlite3
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any


class TokenMonitor:
    """灵台灵识Token监测模块"""
    
    # 模型定价表（¥/1K tokens）
    MODEL_PRICES = {
        "hunyuan-lite": (0.001, 0.001),
        "hunyuan-standard": (0.004, 0.008),
        "hunyuan-pro": (0.015, 0.050),
        "hunyuan-turbo": (0.008, 0.025),
        "hunyuan-turbos": (0.0015, 0.006),
        "deepseek-chat": (0.001, 0.002),
        "deepseek-reasoner": (0.004, 0.016),
        "gpt-4o": (0.0175, 0.070),
        "gpt-4o-mini": (0.00105, 0.0042),
        "claude-sonnet-4": (0.021, 0.105),
        "claude-haiku-3.5": (0.0056, 0.028),
        "qwen-turbo": (0.0005, 0.001),
        "qwen-plus": (0.0016, 0.004),
        "qwen-max": (0.014, 0.056),
    }
    
    DEFAULT_MODEL = "hunyuan-turbos"
    
    def __init__(self, data_dir: str = None):
        """
        初始化Token监测模块
        
        Args:
            data_dir: 数据目录路径
        """
        if data_dir is None:
            skill_dir = Path(__file__).resolve().parent.parent.parent
            data_dir = skill_dir / ".meta"
        
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.data_dir / "token_monitor.db"
        
        # 初始化数据库
        self._init_db()
    
    def _init_db(self):
        """初始化数据库表结构"""
        conn = sqlite3.connect(str(self.db_path))
        try:
            conn.executescript("""
                -- 操作日志
                CREATE TABLE IF NOT EXISTS operation_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    action TEXT NOT NULL,
                    model TEXT NOT NULL DEFAULT '',
                    input_tokens INTEGER NOT NULL DEFAULT 0,
                    output_tokens INTEGER NOT NULL DEFAULT 0,
                    saved_tokens INTEGER NOT NULL DEFAULT 0,
                    cost REAL NOT NULL DEFAULT 0.0,
                    saved_cost REAL NOT NULL DEFAULT 0.0,
                    timestamp TEXT NOT NULL DEFAULT (datetime('now','localtime'))
                );
                
                -- 累计计数器
                CREATE TABLE IF NOT EXISTS counters (
                    key TEXT PRIMARY KEY,
                    value INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
                );
                
                -- 今日计数器
                CREATE TABLE IF NOT EXISTS today_counters (
                    key TEXT PRIMARY KEY,
                    value INTEGER NOT NULL DEFAULT 0,
                    date TEXT NOT NULL DEFAULT (date('now','localtime'))
                );
                
                -- 索引
                CREATE INDEX IF NOT EXISTS idx_oplog_ts ON operation_log(timestamp);
                CREATE INDEX IF NOT EXISTS idx_oplog_action ON operation_log(action);
            """)
            conn.commit()
        finally:
            conn.close()
    
    def record_usage(self, action: str, model: str = "", 
                     input_tokens: int = 0, output_tokens: int = 0,
                     saved_tokens: int = 0):
        """
        记录一次Token使用
        
        Args:
            action: 操作类型（learn/query/search/analyze/summarize）
            model: 模型标识
            input_tokens: 输入token数
            output_tokens: 输出token数
            saved_tokens: 节省的token数
        """
        if not model:
            model = self.DEFAULT_MODEL
        
        # 计算费用
        cost = self._calc_cost(model, input_tokens, output_tokens)
        saved_cost = self._calc_saving(model, saved_tokens)
        
        conn = sqlite3.connect(str(self.db_path))
        try:
            # 记录操作日志
            conn.execute("""
                INSERT INTO operation_log
                    (action, model, input_tokens, output_tokens, saved_tokens, cost, saved_cost)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (action, model, input_tokens, output_tokens, saved_tokens, cost, saved_cost))
            
            # 更新累计计数器
            self._counter_incr(conn, "total_consumed", input_tokens + output_tokens)
            self._counter_incr(conn, "total_cost", round(cost * 100000))
            if saved_tokens > 0:
                self._counter_incr(conn, "token_savings", saved_tokens)
                self._counter_incr(conn, "total_saved_cost", round(saved_cost * 100000))
            
            # 更新今日计数器
            today = datetime.now().strftime("%Y-%m-%d")
            self._today_incr(conn, "today_consumed", input_tokens + output_tokens, today)
            self._today_incr(conn, "today_cost", round(cost * 100000), today)
            if saved_tokens > 0:
                self._today_incr(conn, "today_saved_tokens", saved_tokens, today)
                self._today_incr(conn, "today_saved_cost", round(saved_cost * 100000), today)
            
            conn.commit()
        finally:
            conn.close()
    
    def get_savings(self) -> dict:
        """获取节省统计"""
        conn = sqlite3.connect(str(self.db_path))
        try:
            # 累计数据
            total_consumed = self._counter_get(conn, "total_consumed")
            total_cost = self._counter_get(conn, "total_cost") / 100000
            total_saved = self._counter_get(conn, "token_savings")
            total_saved_cost = self._counter_get(conn, "total_saved_cost") / 100000
            
            # 今日数据
            today = datetime.now().strftime("%Y-%m-%d")
            today_consumed = self._today_get(conn, "today_consumed", today)
            today_cost = self._today_get(conn, "today_cost", today) / 100000
            today_saved = self._today_get(conn, "today_saved_tokens", today)
            today_saved_cost = self._today_get(conn, "today_saved_cost", today) / 100000
            
            return {
                "today": {
                    "consumed": today_consumed,
                    "cost": round(today_cost, 4),
                    "saved": today_saved,
                    "saved_cost": round(today_saved_cost, 4)
                },
                "total": {
                    "consumed": total_consumed,
                    "cost": round(total_cost, 4),
                    "saved": total_saved,
                    "saved_cost": round(total_saved_cost, 4)
                }
            }
        finally:
            conn.close()
    
    def get_cost_summary(self, days: int = 7) -> dict:
        """获取费用汇总"""
        conn = sqlite3.connect(str(self.db_path))
        try:
            start_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
            
            rows = conn.execute("""
                SELECT date(timestamp) as date,
                       SUM(input_tokens + output_tokens) as total_tokens,
                       SUM(cost) as total_cost,
                       SUM(saved_tokens) as saved_tokens,
                       SUM(saved_cost) as saved_cost
                FROM operation_log
                WHERE timestamp >= ?
                GROUP BY date(timestamp)
                ORDER BY date
            """, (start_date,)).fetchall()
            
            daily_data = []
            for row in rows:
                daily_data.append({
                    "date": row[0],
                    "tokens": row[1],
                    "cost": round(row[2], 4),
                    "saved": row[3],
                    "saved_cost": round(row[4], 4)
                })
            
            return {
                "days": days,
                "daily": daily_data
            }
        finally:
            conn.close()
    
    def get_action_stats(self) -> dict:
        """获取操作统计"""
        conn = sqlite3.connect(str(self.db_path))
        try:
            rows = conn.execute("""
                SELECT action, COUNT(*) as count, 
                       SUM(input_tokens + output_tokens) as total_tokens,
                       SUM(cost) as total_cost
                FROM operation_log
                GROUP BY action
                ORDER BY count DESC
            """).fetchall()
            
            stats = {}
            for row in rows:
                stats[row[0]] = {
                    "count": row[1],
                    "tokens": row[2],
                    "cost": round(row[3], 4)
                }
            
            return stats
        finally:
            conn.close()
    
    def _calc_cost(self, model: str, input_tokens: int, output_tokens: int) -> float:
        """计算费用"""
        prices = self.MODEL_PRICES.get(model, self.MODEL_PRICES[self.DEFAULT_MODEL])
        input_cost = input_tokens / 1000 * prices[0]
        output_cost = output_tokens / 1000 * prices[1]
        return round(input_cost + output_cost, 6)
    
    def _calc_saving(self, model: str, saved_tokens: int) -> float:
        """计算节省费用"""
        if saved_tokens <= 0:
            return 0.0
        
        prices = self.MODEL_PRICES.get(model, self.MODEL_PRICES[self.DEFAULT_MODEL])
        # 混合计算：40%输入，60%输出
        price = prices[0] * 0.4 + prices[1] * 0.6
        return round(saved_tokens / 1000 * price, 6)
    
    def _counter_incr(self, conn: sqlite3.Connection, key: str, delta: int):
        """增量更新计数器"""
        conn.execute("""
            INSERT INTO counters (key, value, updated_at)
            VALUES (?, ?, datetime('now','localtime'))
            ON CONFLICT(key) DO UPDATE SET
                value = value + excluded.value,
                updated_at = datetime('now','localtime')
        """, (key, delta))
    
    def _counter_get(self, conn: sqlite3.Connection, key: str) -> int:
        """读取计数器"""
        row = conn.execute("SELECT value FROM counters WHERE key=?", (key,)).fetchone()
        return row[0] if row else 0
    
    def _today_incr(self, conn: sqlite3.Connection, key: str, delta: int, today: str):
        """增量更新今日计数器"""
        conn.execute("""
            INSERT INTO today_counters (key, value, date)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
                value = value + excluded.value,
                date = excluded.date
        """, (key, delta, today))
    
    def _today_get(self, conn: sqlite3.Connection, key: str, today: str) -> int:
        """读取今日计数器"""
        row = conn.execute(
            "SELECT value FROM today_counters WHERE key=? AND date=?", 
            (key, today)
        ).fetchone()
        return row[0] if row else 0
    
    def generate_daily_report(self, date: str = None) -> str:
        """
        生成每日报告
        
        Args:
            date: 日期（YYYY-MM-DD），默认今天
        
        Returns:
            str: 格式化的每日报告
        """
        if date is None:
            date = datetime.now().strftime("%Y-%m-%d")
        
        conn = sqlite3.connect(str(self.db_path))
        try:
            # 获取当日数据
            rows = conn.execute("""
                SELECT action, COUNT(*) as count, 
                       SUM(input_tokens + output_tokens) as total_tokens,
                       SUM(cost) as total_cost,
                       SUM(saved_tokens) as saved_tokens,
                       SUM(saved_cost) as saved_cost
                FROM operation_log
                WHERE date(timestamp) = ?
                GROUP BY action
                ORDER BY count DESC
            """, (date,)).fetchall()
            
            if not rows:
                return f"📊 {date} 每日报告\n{'='*40}\n暂无数据"
            
            # 计算汇总
            total_count = sum(r[1] for r in rows)
            total_tokens = sum(r[2] for r in rows)
            total_cost = sum(r[3] for r in rows)
            total_saved = sum(r[4] for r in rows)
            total_saved_cost = sum(r[5] for r in rows)
            
            # 生成报告
            report = []
            report.append(f"📊 {date} 每日报告")
            report.append("=" * 40)
            report.append(f"总计: {total_count} 次操作, {total_tokens:,} tokens")
            report.append(f"费用: ¥{total_cost:.4f} | 节省: ¥{total_saved_cost:.4f}")
            report.append(f"节省率: {total_saved/total_tokens*100:.1f}%" if total_tokens > 0 else "节省率: 0%")
            report.append("")
            report.append("操作明细:")
            report.append("-" * 40)
            
            for row in rows:
                action, count, tokens, cost, saved, saved_cost = row
                report.append(f"  {action:12} | {count:3}次 | {tokens:6} tokens | ¥{cost:.4f}")
            
            report.append("-" * 40)
            report.append(f"  节省总计: {total_saved:,} tokens (¥{total_saved_cost:.4f})")
            
            return "\n".join(report)
        finally:
            conn.close()
    
    def print_text_chart(self, days: int = 7):
        """
        打印文本图表
        
        Args:
            days: 显示最近N天
        """
        conn = sqlite3.connect(str(self.db_path))
        try:
            start_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
            
            rows = conn.execute("""
                SELECT date(timestamp) as date,
                       SUM(input_tokens + output_tokens) as total_tokens,
                       SUM(cost) as total_cost
                FROM operation_log
                WHERE timestamp >= ?
                GROUP BY date(timestamp)
                ORDER BY date
            """, (start_date,)).fetchall()
            
            if not rows:
                print("📊 暂无数据")
                return
            
            # 找到最大值用于缩放
            max_tokens = max(r[1] for r in rows)
            chart_width = 30
            
            print(f"\n📊 最近{days}天Token消耗趋势")
            print("=" * 50)
            
            for row in rows:
                date, tokens, cost = row
                # 计算柱状图长度
                bar_len = int(tokens / max_tokens * chart_width) if max_tokens > 0 else 0
                bar = "█" * bar_len
                # 只显示日期的月日部分
                short_date = date[5:]  # MM-DD
                print(f"  {short_date} | {bar} {tokens:>6} tokens (¥{cost:.4f})")
            
            print("=" * 50)
        finally:
            conn.close()
    
    def get_trend_analysis(self, days: int = 7) -> dict:
        """
        获取趋势分析
        
        Args:
            days: 分析最近N天
        
        Returns:
            dict: 趋势分析结果
        """
        conn = sqlite3.connect(str(self.db_path))
        try:
            start_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
            
            # 获取每日数据
            rows = conn.execute("""
                SELECT date(timestamp) as date,
                       SUM(input_tokens + output_tokens) as total_tokens,
                       SUM(cost) as total_cost,
                       SUM(saved_tokens) as saved_tokens,
                       SUM(saved_cost) as saved_cost,
                       COUNT(*) as op_count
                FROM operation_log
                WHERE timestamp >= ?
                GROUP BY date(timestamp)
                ORDER BY date
            """, (start_date,)).fetchall()
            
            if not rows:
                return {"trend": "无数据", "avg_daily_tokens": 0, "avg_daily_cost": 0}
            
            # 计算趋势
            daily_data = []
            for row in rows:
                daily_data.append({
                    "date": row[0],
                    "tokens": row[1],
                    "cost": row[2],
                    "saved": row[3],
                    "saved_cost": row[4],
                    "ops": row[5]
                })
            
            # 计算平均值
            avg_tokens = sum(d["tokens"] for d in daily_data) / len(daily_data)
            avg_cost = sum(d["cost"] for d in daily_data) / len(daily_data)
            avg_saved = sum(d["saved"] for d in daily_data) / len(daily_data)
            
            # 判断趋势
            if len(daily_data) >= 2:
                recent = daily_data[-1]["tokens"]
                previous = daily_data[-2]["tokens"]
                if recent > previous * 1.1:
                    trend = "上升"
                elif recent < previous * 0.9:
                    trend = "下降"
                else:
                    trend = "平稳"
            else:
                trend = "数据不足"
            
            return {
                "trend": trend,
                "days": days,
                "avg_daily_tokens": round(avg_tokens),
                "avg_daily_cost": round(avg_cost, 4),
                "avg_daily_saved": round(avg_saved),
                "total_tokens": sum(d["tokens"] for d in daily_data),
                "total_cost": round(sum(d["cost"] for d in daily_data), 4),
                "daily_data": daily_data
            }
        finally:
            conn.close()


# 便捷函数
def create_token_monitor(data_dir: str = None) -> TokenMonitor:
    """创建Token监测实例"""
    return TokenMonitor(data_dir)


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="灵识Token监测工具")
    subparsers = parser.add_subparsers(dest="command", help="可用命令")
    
    # report 命令
    report_parser = subparsers.add_parser("report", help="生成每日报告")
    report_parser.add_argument("--date", default=None, help="日期（YYYY-MM-DD），默认今天")
    
    # chart 命令
    chart_parser = subparsers.add_parser("chart", help="显示文本图表")
    chart_parser.add_argument("--days", type=int, default=7, help="显示最近N天")
    
    # trend 命令
    trend_parser = subparsers.add_parser("trend", help="趋势分析")
    trend_parser.add_argument("--days", type=int, default=7, help="分析最近N天")
    
    # stats 命令
    stats_parser = subparsers.add_parser("stats", help="查看统计")
    
    # record 命令
    record_parser = subparsers.add_parser("record", help="记录使用")
    record_parser.add_argument("action", help="操作类型")
    record_parser.add_argument("--model", default="hunyuan-turbos", help="模型标识")
    record_parser.add_argument("--input", type=int, default=0, help="输入token数")
    record_parser.add_argument("--output", type=int, default=0, help="输出token数")
    record_parser.add_argument("--saved", type=int, default=0, help="节省token数")
    
    args = parser.parse_args()
    
    monitor = TokenMonitor()
    
    if args.command == "report":
        report = monitor.generate_daily_report(args.date)
        print(report)
    
    elif args.command == "chart":
        monitor.print_text_chart(args.days)
    
    elif args.command == "trend":
        trend = monitor.get_trend_analysis(args.days)
        print(f"\n📈 趋势分析（最近{trend['days']}天）")
        print("=" * 40)
        print(f"趋势: {trend['trend']}")
        print(f"日均消耗: {trend['avg_daily_tokens']:,} tokens")
        print(f"日均费用: ¥{trend['avg_daily_cost']:.4f}")
        print(f"日均节省: {trend['avg_daily_saved']:,} tokens")
        print(f"总消耗: {trend['total_tokens']:,} tokens")
        print(f"总费用: ¥{trend['total_cost']:.4f}")
    
    elif args.command == "stats":
        savings = monitor.get_savings()
        print(f"\n📊 Token统计")
        print("=" * 40)
        print(f"今日消耗: {savings['today']['consumed']:,} tokens")
        print(f"今日费用: ¥{savings['today']['cost']:.4f}")
        print(f"今日节省: {savings['today']['saved']:,} tokens")
        print(f"累计消耗: {savings['total']['consumed']:,} tokens")
        print(f"累计费用: ¥{savings['total']['cost']:.4f}")
        print(f"累计节省: {savings['total']['saved']:,} tokens")
    
    elif args.command == "record":
        monitor.record_usage(args.action, args.model, args.input, args.output, args.saved)
        print(f"✅ 已记录: {args.action}")
    
    else:
        parser.print_help()
