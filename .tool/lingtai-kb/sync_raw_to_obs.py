# -*- coding: utf-8 -*-
"""
原料 → 灵识观察 自动同步
========================
检测原料目录的新增/修改文件，自动同步到 observation_engine。

触发方式：
1. git post-commit hook（每次提交后自动运行）
2. 手动调用（python sync_raw_to_obs.py）

跟踪机制：
- sync_state.json 记录每文件的最后处理时间
- 只处理未处理过的新文件（首次）或间隔 ≥ 1 小时的修改文件
"""

import os
import sys
import json
import re
import subprocess
from pathlib import Path
from datetime import datetime, timedelta

# 添加当前目录到路径，以便导入 observation_engine
sys.path.insert(0, str(Path(__file__).resolve().parent))

from observation_engine import ObservationEngine


# 路径配置
VAULT_PATH = Path.cwd()
RAW_DIR = VAULT_PATH / "原料"
STATE_FILE = Path(__file__).resolve().parent / "sync_state.json"
MIN_INTERVAL = timedelta(hours=1)  # 同一文件至少间隔 1 小时才重新处理


def load_state() -> dict:
    """加载同步状态"""
    if STATE_FILE.exists():
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return {"files": {}, "last_run": ""}


def save_state(state: dict):
    """保存同步状态"""
    state["last_run"] = datetime.now().isoformat()
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def get_changed_raw_files() -> list:
    """
    扫描原料目录，获取上次同步以来新增/修改的文件
    
    使用文件系统 mtime 而非 git diff（避免中文文件名编码问题）
    
    Returns:
        list: [(filepath, status), ...] 相对于仓库根的路径
    """
    state = load_state()
    last_run_str = state.get("last_run", "")
    last_run = datetime.fromisoformat(last_run_str) if last_run_str else (datetime.now() - timedelta(days=7))
    
    changed = []
    prefix = "灵台/原料/"
    
    if not RAW_DIR.exists():
        return changed
    
    for f in sorted(RAW_DIR.iterdir()):
        if not f.is_file() or not f.name.endswith(".md"):
            continue
        file_mtime = datetime.fromtimestamp(f.stat().st_mtime)
        
        # 判断是否为新文件或上次运行后有修改
        rel_path = f"灵台/原料/{f.name}"
        last_processed = state["files"].get(rel_path)
        is_new = not last_processed
        is_modified = last_processed and file_mtime > datetime.fromisoformat(last_processed)
        
        if is_new or is_modified:
            status = "A" if is_new else "M"
            changed.append((rel_path, status))
    
    return changed


def process_file(filepath: str, status: str, engine: ObservationEngine, state: dict) -> dict:
    """
    处理单个原料文件的同步
    
    Returns:
        dict: 处理结果
    """
    abs_path = VAULT_PATH / filepath
    
    if not abs_path.exists():
        return {"file": filepath, "status": "skipped", "reason": "文件不存在（可能已被删除）"}
    
    # 检查是否需要处理（根据跟踪状态和间隔）
    rel_path = str(abs_path.relative_to(VAULT_PATH))
    file_mtime = datetime.fromtimestamp(abs_path.stat().st_mtime)
    last_processed = state["files"].get(rel_path)
    
    if last_processed:
        last_time = datetime.fromisoformat(last_processed)
        if datetime.now() - last_time < MIN_INTERVAL:
            return {"file": filepath, "status": "skipped", "reason": f"距上次处理 < {MIN_INTERVAL.total_seconds()/60:.0f} 分钟"}
    
    # 读取文件内容
    try:
        with open(abs_path, "r", encoding="utf-8") as f:
            content = f.read()
    except UnicodeDecodeError:
        try:
            with open(abs_path, "r", encoding="gbk") as f:
                content = f.read()
        except Exception as e:
            return {"file": filepath, "status": "error", "reason": f"编码读取失败: {e}"}
    except Exception as e:
        return {"file": filepath, "status": "error", "reason": f"读取失败: {e}"}
    
    # 提取 frontmatter 和正文
    clean_content = content
    category = "原料"
    if content.startswith("---"):
        parts = content.split("---", 2)
        if len(parts) >= 3:
            fm_text = parts[1].strip()
            clean_content = parts[2].strip()
            # 从 frontmatter 提取域
            fm_match = re.search(r"^域:\s*(.+)$", fm_text, re.MULTILINE)
            if fm_match:
                category = fm_match.group(1).strip()
    
    if not clean_content.strip():
        return {"file": filepath, "status": "skipped", "reason": "内容为空"}
    
    # 触发观察引擎
    obs_result = engine.on_save(
        content=clean_content[:500],  # 截断长内容
        category=category,
        source=f"原料同步:{status}",
    )
    
    # 更新跟踪状态
    state["files"][rel_path] = datetime.now().isoformat()
    
    return {
        "file": filepath,
        "status": "synced",
        "observation": obs_result,
    }


def main():
    """主入口"""
    print(f"[sync] ⏳ 原料→灵识同步开始 ({datetime.now().strftime('%H:%M:%S')})")
    
    state = load_state()
    engine = ObservationEngine(str(VAULT_PATH))
    
    changed = get_changed_raw_files()
    if not changed:
        print("[sync] ℹ️ 本次无可同步的原料文件")
        # 即使没有 git 变更，也保存状态
        save_state(state)
        return
    
    print(f"[sync] 🔍 发现 {len(changed)} 个原料文件变更:")
    results = []
    for filepath, status in changed:
        result = process_file(filepath, status, engine, state)
        results.append(result)
        status_mark = "✅" if result["status"] == "synced" else "⏭️" if result["status"] == "skipped" else "❌"
        print(f"  {status_mark} [{status}] {filepath} → {result.get('observation', {}).get('action', result.get('reason', '?'))}")
    
    save_state(state)
    
    synced = sum(1 for r in results if r["status"] == "synced")
    skipped = sum(1 for r in results if r["status"] == "skipped")
    errors = sum(1 for r in results if r["status"] == "error")
    
    print(f"[sync] ✅ 完成: {synced} 同步 / {skipped} 跳过 / {errors} 错误")
    
    # 返回结果摘要（供 post-commit hook 判断是否需要输出）
    if synced > 0:
        print(f"\n[sync] 💡 灵识观察更新: {synced} 条新原料已喂入观察层，下次 skillopt 进化时将自动收割")


if __name__ == "__main__":
    main()
