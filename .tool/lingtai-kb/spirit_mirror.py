# -*- coding: utf-8 -*-
"""
灵魂镜像·信号采集（Spirit Mirror Signal Collector）
=================================================
在 21:00 内观前运行，采集今日多源信号，生成结构化简报供内观使用。

v2 优化：可 import 使用（避免 subprocess 开销）。
外部可传入预缓存的 git log 结果（来自 git_log_cache 模块），
避免重复启动 git 子进程。

信号维度：
- 焦点：今天在哪个域工作最久？
- 摩擦力：revert/error/中断模式的频率
- 重复：同一主题反复出现
- 缺口：原料积累未处理
- 延续：对比昨日变化
"""

import os
import sys
import json
import re
import subprocess
from pathlib import Path
from datetime import datetime, timedelta, date
from collections import Counter, defaultdict

# 路径
sys.path.insert(0, str(Path(__file__).resolve().parent))
VAULT_PATH = Path.cwd()
OBS_DIR = Path(__file__).parent / "observation"
STATE_FILE = Path(__file__).parent / "sync_state.json"

# 域映射（文件路径前缀 → 域名称）
DOMAIN_MAP = {
    "丹房/00-思考与认知/追问": "O/π追问",
    "丹房/00-思考与认知": "思考与认知",
    "丹房/01-内容创作": "内容创作",
    "丹房/02-成长与日常": "成长与日常",
    "丹房/03-社会观察": "社会观察",
    "丹房/04-身体与健康": "身体与健康",
    "丹房/05-哲学与思想": "哲学与思想",
    "丹房/06-商业与投资": "商业与投资",
    "丹房/07-工具与AI": "工具与AI",
    "丹房/08-教育": "教育",
    "丹房/09-系统自身": "系统自身",
    "丹房/99-一人公司": "一人公司",
    ".tool/": "系统基建",
    "丹房/体检": "体检巡检",
    "丹房/作品": "内容产出",
    "作品/": "内容产出",
    "丹房/入门": "文档规范",
    "原料/": "原料摄入",
    "丹房/其他": "丹房其他",
}

# Git 在 Windows 上对含非 ASCII 的路径输出八进制转义（\NNN\NNN...）。
# 现代做法：-c core.quotepath=false 让 subprocess 直出 UTF-8；
# 若来源仍带转义（如旧缓存），统一先 unescape 再按 DOMAIN_MAP 最长前缀匹配。


_ESC_RE = re.compile(r"((?:\\[0-7]{3})+)")

def unescape_git_path(path: str) -> str:
    """将 git 八进制转义路径还原为 UTF-8 明文。"""
    if "\\" not in path:
        return path

    def _conv(m):
        seq = m.group(1)
        octets = [int(seq[i + 1:i + 4], 8) for i in range(0, len(seq), 4)]
        return bytes(octets).decode("utf-8", errors="replace")

    return _ESC_RE.sub(_conv, path)


def classify_git_path(path: str) -> str:
    """从 git 输出的路径推断域（先还原转义，再按 DOMAIN_MAP 最长前缀匹配）"""
    path = unescape_git_path(path)
    for prefix, domain in sorted(DOMAIN_MAP.items(), key=lambda x: -len(x[0])):
        if prefix in path:
            return domain
    return "其他"


def get_today_commits(cached_commits: list = None) -> list:
    """
    获取今天的 git commit 记录。
    v2: 支持外部传入预缓存的 commit 列表（来自 git_log_cache 模块），避免重复 subprocess。
    
    Args:
        cached_commits: 预缓存的 commit 列表，格式同 git_log_cache.load()['commits']
    """
    today = date.today().isoformat()
    if cached_commits is not None:
        # 直接使用传入的缓存，仅保留今日 commit（缓存可能跨多日）
        commits = []
        for c in cached_commits:
            if not str(c.get("time", "")).startswith(today):
                continue
            entry = {"hash": c["hash"], "time": c["time"], "msg": c["msg"]}
            # 解析 changes: 原始格式 "A\tpath" → 保持兼容
            entry["changes"] = c.get("changes", [])
            commits.append(entry)
        return commits

    # 降级：自己查 git log
    # ⚠️ 灵台仓库即 VAULT_PATH（cwd）
    try:
        result = subprocess.run(
            ["git", "-c", "core.quotepath=false", "log",
             f"--after={today}T00:00:00", f"--until={today}T23:59:59",
             "--pretty=format:%H|%ai|%s", "--name-status"],
            cwd=str(VAULT_PATH), capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            return []
        
        lines = result.stdout.strip().split("\n")
        commits = []
        current = None
        changes = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            if "|" in line and len(line.split("|")) >= 3:
                if current and changes:
                    current["changes"] = changes
                    commits.append(current)
                parts = line.split("|", 2)
                current = {
                    "hash": parts[0][:8],
                    "time": parts[1],
                    "msg": parts[2],
                }
                changes = []
            elif current and line[0] in ("A", "M", "D", "R"):
                changes.append(line)
        if current and changes:
            current["changes"] = changes
            commits.append(current)
        return commits
    except Exception:
        return []


def classify_file(path: str) -> str:
    """将文件路径映射到域"""
    for prefix, domain in sorted(DOMAIN_MAP.items(), key=lambda x: -len(x[0])):
        if prefix in path:
            return domain
    return "其他"


def analyze_commits(commits: list) -> dict:
    """分析 commit 模式"""
    if not commits:
        return {"count": 0, "domains": {}, "messages": [], "friction": 0, "fix_count": 0}
    
    msg_counter = Counter()
    domain_counter = Counter()
    friction = 0
    fix_count = 0
    
    for c in commits:
        msg = c.get("msg", "")
        msg_counter[msg[:60]] += 1
        
        # 检测摩擦力信号
        if any(kw in msg.lower() for kw in ["fix", "修复", "bug", "回退", "revert", "重试", "错误"]):
            friction += 1
        if msg.startswith("fix") or msg.startswith("修复"):
            fix_count += 1
        
        # 从文件变更推断域
        for ch in c.get("changes", []):
            parts = ch.split("\t", 1)
            if len(parts) == 2:
                path = parts[1]
                domain = classify_git_path(path)
                domain_counter[domain] += 1
    
    return {
        "count": len(commits),
        "domains": dict(domain_counter.most_common(5)),
        "messages": [c["msg"] for c in commits[-5:]],
        "friction": friction,
        "fix_count": fix_count,
    }


def get_observation_signal() -> dict:
    """从观察层读取信号"""
    signals = {"new_observations": 0, "pending_facts": 0, "pending_topics": 0}
    obs_path = OBS_DIR / "observations.json"
    pending_path = OBS_DIR / "pending.json"
    
    if obs_path.exists():
        try:
            with open(obs_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            signals["new_observations"] = len(data.get("observations", []))
        except Exception:
            pass
    
    if pending_path.exists():
        try:
            with open(pending_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            signals["pending_topics"] = len(data)
            signals["pending_facts"] = sum(len(v.get("facts", [])) for v in data.values())
        except Exception:
            pass
    
    return signals


def get_continuity_signal() -> dict:
    """获取延续性信号（对比昨日）"""
    signals = {"yesterday_commits": 0}
    
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    
    # 读昨天的内观找关键词
    today_date = date.today()
    for days_back in range(1, 4):
        check_date = (today_date - timedelta(days=days_back)).isoformat()
        neiguan_path = Path(VAULT_PATH) / "体检" / "内观.md"
        if neiguan_path.exists():
            try:
                content = neiguan_path.read_text(encoding="utf-8", errors="ignore")
                # 找日期标记后的段落
                sections = re.split(r"#+\s*20\d{2}", content)
                for sec in sections:
                    if check_date[:10] in sec:
                        keywords = re.findall(r"[\u4e00-\u9fff]{4,}", sec[:200])
                        signals["recent_keywords"] = list(set(keywords[:10]))
                        break
            except Exception:
                pass
        if "recent_keywords" in signals:
            break
    
    return signals


def get_raw_material_gap() -> dict:
    """检测原料积累缺口"""
    raw_dir = VAULT_PATH / "原料"
    if not raw_dir.exists():
        return {"total": 0, "new_today": 0}
    
    today = date.today()
    new_today = 0
    total = 0
    for f in raw_dir.iterdir():
        if f.suffix == ".md":
            total += 1
            mtime = datetime.fromtimestamp(f.stat().st_mtime)
            if mtime.date() == today:
                new_today += 1
    
    return {"total": total, "new_today": new_today}


def get_skillopt_signal() -> dict:
    """读取 skillopt 夜间进化结果"""
    changelog = Path(__file__).parent / "skillopt" / "changelog.md"
    if not changelog.exists():
        return {"status": "unknown"}
    
    try:
        content = changelog.read_text(encoding="utf-8", errors="ignore")
        today_str = date.today().isoformat()
        if today_str in content:
            # 提取今天的最后一条记录
            sections = content.split(f"## {today_str}")
            if len(sections) > 1:
                today_section = sections[-1].split("##")[0].strip()
                return {"status": "active", "last_result": today_section[:200]}
        return {"status": "no_data"}
    except Exception:
        return {"status": "error"}


def collect_signals(cached_commits: list = None) -> dict:
    """
    采集所有信号。
    v2: 支持传入预缓存的 git commits，避免重复 subprocess。
    """
    commits = get_today_commits(cached_commits=cached_commits)
    
    return {
        "date": date.today().isoformat(),
        "time": datetime.now().strftime("%H:%M"),
        "git": analyze_commits(commits),
        "observation": get_observation_signal(),
        "continuity": get_continuity_signal(),
        "raw_material": get_raw_material_gap(),
        "skillopt": get_skillopt_signal(),
    }


def format_briefing(signals: dict) -> str:
    """将信号格式化为可读简报"""
    g = signals["git"]
    o = signals["observation"]
    r = signals["raw_material"]
    s = signals["skillopt"]
    
    lines = []
    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━")
    lines.append(f"信号简报 · {signals['date']} {signals['time']}")
    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━")
    
    # 焦点
    domains = g.get("domains", {})
    if domains:
        top_domain = max(domains, key=domains.get)
        top_count = domains[top_domain]
        lines.append(f"\n📌 焦点域: {top_domain}（{top_count} 次变更）")
        lines.append(f"   当日 commit: {g['count']} 次")
        if g["messages"]:
            lines.append(f"   最近 commit: {g['messages'][-1][:50]}...")
    else:
        lines.append("\n📌 焦点: 无 git 活动")
    
    # 摩擦力
    if g["friction"] > 0:
        lines.append(f"\n⚠️ 摩擦力信号: {g['friction']} 次修复/fix 操作")
    
    # 观察层
    if o["pending_facts"] > 0:
        lines.append(f"\n🧩 观察层待处理: {o['pending_facts']} 条事实 / {o['pending_topics']} 个主题")
    
    # 原料缺口
    if r["new_today"] > 0:
        lines.append(f"\n📥 今日新原料: {r['new_today']} 篇（累计 {r['total']} 篇）")
    
    # skillopt
    if s["status"] == "active":
        lines.append(f"\n🌙 skillopt 状态: 昨晚有进化活动")
    
    lines.append("\n━━━━━━━━━━━━━━━━━━━━━━━━━━")
    return "\n".join(lines)


def main():
    # v2: 优先从 git_log_cache 读取（避免重复 subprocess）
    commits_cache = None
    try:
        from git_log_cache import load as load_git_cache
        cached = load_git_cache()
        commits_cache = cached.get("commits")
    except (ImportError, Exception):
        pass
    
    signals = collect_signals(cached_commits=commits_cache)
    briefing = format_briefing(signals)
    print(briefing)
    
    # 保存 JSON 供内观自动化读取
    signal_dir = Path(__file__).parent / "signals"
    signal_dir.mkdir(exist_ok=True)
    signal_file = signal_dir / f"{signals['date']}.json"
    with open(signal_file, "w", encoding="utf-8") as f:
        json.dump(signals, f, ensure_ascii=False, indent=2)
    
    return signals


if __name__ == "__main__":
    main()
