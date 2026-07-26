#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sleep_evolution_run.py — 每日 03:00 睡眠进化执行脚本
直接调用 evolve_engine 完成：收割→挖掘→回验→定级→暂存
"""
import sys
import os
import json
from datetime import datetime, date
import re

VAULT = r"C:\Obsidian仓库\edrc\灵台"
SKILLOPT_DIR = os.path.join(VAULT, ".tool", "lingtai-kb", "skillopt")

# 确保 Python 能找到 skillopt 包
sys.path.insert(0, os.path.join(VAULT, ".tool", "lingtai-kb"))

def run_evolution():
    """执行完整进化轮次，返回摘要。"""
    from skillopt.evolve_engine import EvolveEngine
    import session_tracker
    
    engine = EvolveEngine(vault_path=VAULT)
    summary = engine.run()
    # 脚本路径（绕过 MCP tools/call）直接驱动 skillopt 引擎，
    # 这里补记一次 skillopt_run，使其计入工具覆盖报告
    try:
        session_tracker.record(
            "skillopt_run",
            data_chars=len(json.dumps(summary, ensure_ascii=False)),
            client="script/sleep@0300",
        )
    except Exception:
        pass
    return summary

def check_existing_stage_today():
    """检查今天是否已经运行过（防止重复执行）。"""
    today = date.today().isoformat()
    staged_dir = os.path.join(SKILLOPT_DIR, "staged", today)
    if os.path.isdir(staged_dir) and os.listdir(staged_dir):
        return True
    return False

def write_logs(summary, actions_taken, auto_adopted):
    """写入 丹房/日志.md 和 oplog.jsonl"""
    now = datetime.now()
    ts = now.strftime("%Y-%m-%dT%H:%M:00+08:00")
    ts_short = now.strftime("%y-%m-%d %H:%M")
    
    harvested = summary.get("harvested", 0)
    sessions = summary.get("sessions", 0)
    patterns = summary.get("patterns", 0)
    candidates = summary.get("candidates", 0)
    validated = summary.get("validated", 0)
    scored = summary.get("scored", 0)
    staged = summary.get("staged", 0)
    recommended = summary.get("recommended", 0)
    review = summary.get("review", 0)
    
    # Build log line
    summary_line = f"每日睡眠进化：收割{harvested}条→模式{patterns}个→候选{candidates}条→自动采纳{auto_adopted}条"

    
    # 日志.md（在末尾追加）
    log_entry = f"[{ts_short}] WB auto | skillopt | {summary_line} | sleep@0300\n"
    
    log_path = os.path.join(VAULT, "丹房", "日志.md")
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(log_entry)
    
    # oplog.jsonl（末尾追加）
    oplog_path = os.path.join(VAULT, "丹房", ".meta", "oplog.jsonl")
    oplog_entry = {
        "t": ts,
        "op": "WB",
        "mode": "auto",
        "type": "skillopt",
        "summary": summary_line,
        "links": ["sleep@0300"],
        "stats": {
            "harvested": harvested,
            "sessions": sessions,
            "patterns": patterns,
            "candidates": candidates,
            "validated": validated,
            "scored": scored,
            "staged": staged,
            "recommended": recommended,
            "review": review
        }
    }
    with open(oplog_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(oplog_entry, ensure_ascii=False) + "\n")
    
    return log_entry, oplog_entry

def git_commit():
    """执行 Git 提交。"""
    import subprocess
    repo = r"C:\Obsidian仓库\edrc"
    today = datetime.now().strftime("%d%H")
    try:
        subprocess.run(
            ["git", "add", "-A"],
            cwd=repo,
            capture_output=True,
            text=True,
            timeout=30,
            check=False
        )
        result = subprocess.run(
            ["git", "commit", "-m", f"skillopt: 每日睡眠进化 {today}"],
            cwd=repo,
            capture_output=True,
            text=True,
            timeout=30,
            check=False
        )
        return result.returncode, result.stdout.strip()[:200]
    except Exception as e:
        return -1, str(e)

def main():
    print("=" * 60)
    print(f"🌙 灵识·睡眠进化 — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)
    
    # 检查是否已经运行过
    if check_existing_stage_today():
        print("⚠️  今日 staged 目录已存在，跳过进化（防止重复执行）")
        return
    
    # 第1步：状态感知（已内建到 evolve_engine 的 harvest）
    print("\n📡 第1步：状态感知...")
    
    # 第2-3步：执行进化（包含 dry-run 和 full run）
    print("\n⚙️  第2-3步：执行进化管线...")
    try:
        summary = run_evolution()
    except Exception as e:
        print(f"❌ 进化失败: {e}")
        import traceback
        traceback.print_exc()
        return
    
    harvested = summary.get("harvested", 0)
    patterns = summary.get("patterns", 0)
    candidates = summary.get("candidates", 0)
    validated = summary.get("validated", 0)
    scored = summary.get("scored", 0)
    staged = summary.get("staged", 0)
    recommended = summary.get("recommended", 0)
    review = summary.get("review", 0)
    auto_adopted = summary.get("auto_adopted", 0)
    
    print(f"\n📊 收割: {harvested} 条观察")
    print(f"📦 Sessions: {summary.get('sessions', 0)} 条")
    print(f"🔍 模式: {patterns} 个")
    print(f"📝 候选规则: {candidates} 条")
    print(f"✅ 已验证: {validated} 条")
    print(f"📈 已评分: {scored} 条")
    print(f"🏗️  已暂存: {staged} 条")
    print(f"🟢 自动采纳: {auto_adopted} 条")
    print(f"🟡 待审阅: {review} 条")
    
    # 检查是否无新数据
    if harvested == 0:
        print("\n⏭️  收割 = 0，无新数据。跳过后续步骤。")
        return
    
    # 第4步：检查结果（摘要输出 - Token 约束）
    print("\n📋 第4步：结果摘要")
    # 自信度分布
    auto_msg = f"🟢 自动采纳: {auto_adopted} 条"
    if auto_adopted > 0:
        auto_msg += f" ✅ 已写入感知规则.md"
    print(f"自信度分布: {auto_msg} / 🟡 {review} 条 / ⚪ {scored - review - auto_adopted} 条")
    
    today = date.today().isoformat()
    if review > 0:
        from skillopt.stager import Stager
        stager = Stager()
        staged_summaries = stager.read_summary(today)
        if staged_summaries:
            print(f"\n🟡 待审阅规则摘要（共{review}条）：")
            for r in staged_summaries:
                if r.get("level") == "🟡":
                    print(f"  - {r['rule_id']} (自信度: {r['confidence']})")
    
    # 第5步：自动采纳（evolve_engine 已在 _stage 中自动处理 🟢 规则）
    print("\n🎯 第5步：自动采纳")
    if auto_adopted > 0:
        print(f"🟢 已自动采纳 {auto_adopted} 条规则 → 感知规则.md")
        actions_taken = True
    else:
        print("🟢 无自动采纳规则")
        actions_taken = False
    
    # 第6步：日志登记
    print("\n📝 第6步：日志登记")
    log_entry, oplog_entry = write_logs(summary, actions_taken, auto_adopted)
    print(f"日志: {log_entry.strip()}")
    
    # 第7步：Git 提交
    print("\n🔧 第7步：Git 提交")
    ret, msg = git_commit()
    if ret == 0:
        print(f"✅ Git 提交成功: {msg}")
    elif ret == 1:
        print("ℹ️  无变更需要提交")
    else:
        print(f"⚠️  Git 提交结果: 返回码={ret}, {msg}")
    
    # 清理 30 天前的 staged 目录
    from skillopt.stager import Stager
    stager = Stager()
    purged = stager.purge(days=30)
    if purged > 0:
        print(f"\n🧹 已清理 {purged} 个过期 staged 目录")
    
    # 输出最终的 memory_bank 衰减信息
    for log_item in summary.get("log", []):
        if log_item.get("event") == "mem_decay":
            print(f"\n💾 记忆银行衰减:")
            print(f"   衰减: {log_item.get('decayed', 0)} 条")
            print(f"   废弃: {log_item.get('deprecated', 0)} 条")
            print(f"   晋升: {log_item.get('promoted', 0)} 条")
            print(f"   清理: {log_item.get('cleaned', 0)} 条")
        elif log_item.get("event") == "mem_decay_error":
            print(f"\n⚠️  记忆银行衰减出错: {log_item.get('error', '')}")
    
    print("\n" + "=" * 60)
    print("🌙 睡眠进化完成")
    print("=" * 60)

if __name__ == "__main__":
    main()
