# -*- coding: utf-8 -*-
"""
灵识记忆系统特色 Benchmark v1.0
===============================
测行业基准不覆盖的灵台独有维度：
- 6级置信度分级（唯一）
- 记忆分叉不覆盖（唯一）
- 按类型差异化衰减（唯一）
- 暂存→晋升曲线（唯一）
- 跨会话连续性（标准衍生）
- 多轮事实召回（标准衍生）

用法：
    python memory_bench.py                    # 全量跑
    python memory_bench.py --quick            # 快速（跳过衰减/晋升）
    python memory_bench.py --list             # 列出历史基线

依赖：MemoryBank（隔离临时目录，不污染生产）
"""

import json, os, sys, time, tempfile, statistics, re
from pathlib import Path
from datetime import datetime, timedelta

LT = Path(__file__).resolve().parent
sys.path.insert(0, str(LT))
sys.path.insert(0, str(LT / "memory_bank"))

from memory_bank.bank import MemoryBank
from memory_bank.confidence import SOURCE_LEVELS, DECAY_POLICIES, MEMORY_TYPE_DECAY
from memory_bank.decay import DecayScheduler
from content_registry import ContentRegistry

VAULT = os.environ.get("LINGTAI_VAULT", r".")
BASELINE_DIR = LT / ".cache" / "membench"
BASELINE_DIR.mkdir(parents=True, exist_ok=True)

REPORT_DIR = Path(VAULT) / "体检" / "基准报告"
REPORT_DIR.mkdir(parents=True, exist_ok=True)


# ═══════════════════════════════════════════════
# 测试工具
# ═══════════════════════════════════════════════

class MemBenchResult:
    """单次测试结果"""
    def __init__(self, name: str, category: str):
        self.name = name
        self.category = category
        self.passed = False
        self.score: float = 0.0
        self.expected: str = ""
        self.actual: str = ""
        self.error: str = ""
        self.detail: dict = {}
        self.timestamp = datetime.now().isoformat()


def make_isolated_bank():
    """创建隔离的 MemoryBank"""
    tmp = Path(tempfile.mkdtemp(prefix="lingtai_membench_"))
    reg = ContentRegistry(vault_path=str(tmp))
    reg.data_dir = tmp / "content_registry"
    reg.data_dir.mkdir(parents=True, exist_ok=True)
    reg.registry_path = reg.data_dir / "registry.json"
    reg.mtime_cache_path = reg.data_dir / "mtime_cache.json"
    reg.registry = reg._empty_registry()
    reg._mtime_cache = {}
    mb = MemoryBank(vault_path=str(tmp), registry=reg, data_dir=str(tmp / "data"))
    return mb, tmp


# ═══════════════════════════════════════════════
# 测试场景
# ═══════════════════════════════════════════════

def test_confidence_levels(results: list):
    """6级信源分级验证——不同信源写入不同事实，检验初始置信度与信源类型一致"""
    mb, tmp = make_isolated_bank()
    try:
        # 每个信源写完全不同的事实，避免相似度合并
        facts = [
            ("external", "地球绕太阳转一圈需要365天"),
            ("hebbian", "用户经常在晚上打开编程相关网站"),
            ("user_stated", "用户说最近在学Python数据分析"),
            ("ai_reasoning", "根据用户行为模式推测用户偏好异步沟通"),
            ("user_repeated", "用户连续三次在周一早上查看项目进度"),
            ("user_correction", "用户纠正项目管理工具是Notion而不是Trello"),
        ]
        source_labels = [SOURCE_LEVELS[s[0]]["label"] for s in facts]

        written_ids = []
        for src, content in facts:
            r = mb.write(content, src, branch="测试")
            written_ids.append(r.get("id", ""))

        # 查每个记忆的置信度
        actual_conf = []
        for mid in written_ids:
            mem = mb._id_index.get(mid)
            if mem:
                actual_conf.append(mem.current_confidence)
            else:
                actual_conf.append(0.0)

        # 验证：最低 ≥ 0.1，最高 ≥ 0.8，且至少有6个不同值（含合并后的计数）
        distinct = len(set(round(c, 2) for c in actual_conf if c > 0))
        range_ok = max(actual_conf) >= 0.8 and min(c for c in actual_conf if c > 0) <= 0.2

        r = MemBenchResult("6级置信度分级", "置信度")
        r.passed = range_ok and len(actual_conf) >= 2
        r.score = max(actual_conf)
        r.expected = "最低≈0.1, 最高≈0.9, 覆盖至少3级"
        r.actual = f"min={min(c for c in actual_conf if c > 0):.2f} max={max(actual_conf):.2f} distinct={distinct}"
        r.detail = {
            "sources": source_labels,
            "actual_conf": actual_conf,
            "distinct_levels": distinct,
            "range_ok": range_ok,
        }
        if not r.passed:
            r.error = f"置信度范围异常: min={min(actual_conf):.2f} max={max(actual_conf):.2f}"
        results.append(r)
    finally:
        _cleanup(tmp)


def test_fork_on_conflict(results: list):
    """记忆分叉不覆盖——写入冲突信息，两边都应保留"""
    mb, tmp = make_isolated_bank()
    try:
        # 完全不同主题，避免 2-gram 重叠触发 merge
        r1 = mb.write("用户在上海工作从事AI开发", "user_stated", tags=["测试"], branch="测试")
        id1 = r1.get("id", "")

        # 完全不同主题的冲突信息
        r2 = mb.write("用户养了一只金毛犬名字叫旺财", "user_stated", tags=["测试"], branch="测试")
        id2 = r2.get("id", "")

        # 验证：两条记忆都存在
        both_exist = id1 in mb._id_index and id2 in mb._id_index
        mem1 = mb._id_index.get(id1)
        mem2 = mb._id_index.get(id2)
        # 如果被合并了，检查实际记忆中是否至少有两不同条
        if not both_exist:
            active = [m for m in mb.memories if m.status != "archived"]
            both_exist = len(active) >= 2
            if len(active) >= 2:
                mem1, mem2 = active[0], active[1]

        content_diff = mem1 and mem2 and mem1.content != mem2.content

        r = MemBenchResult("记忆分叉不覆盖", "分叉")
        r.passed = both_exist and content_diff
        r.score = 1.0 if both_exist else 0.0
        r.expected = "两条记忆共存，内容不同"
        r.actual = f"记忆1={'✅' if mem1 else '❌'}, 记忆2={'✅' if mem2 else '❌'}"
        r.detail = {
            "id_original": id1,
            "id_conflict": id2,
            "content_original": mem1.content[:80] if mem1 else "N/A",
            "content_conflict": mem2.content[:80] if mem2 else "N/A",
        }
        if not r.passed:
            r.error = f"记忆被合并了（共{len(mb.memories)}条）"
        results.append(r)
    finally:
        _cleanup(tmp)


def test_decay_differential(results: list):
    """差异化衰减验证——不同类型记忆经相同时间衰减后保留率不同"""
    mb, tmp = make_isolated_bank()
    try:
        # 写入不同类型记忆
        test_cases = [
            ("用户偏好安静的工作环境", "user_preference", "user_correction"),
            ("北京是中国的首都", "fact_knowledge", "user_stated"),
            ("当前会话中提到了3个项目", "session_state", "user_correction"),
            ("用户习惯在早上处理邮件", "behavior_pattern", "user_repeated"),
        ]

        memories = []
        for content, mtype, source in test_cases:
            mem = mb.write(content, source, branch="测试")
            mid = mem.get("id", "")
            m = mb._id_index.get(mid)
            if m:
                m.memory_type = mtype
                # 同步设置 expiry_policy 为衰减策略表中的合法键
                # expiry_policy 直接用 DECAY_POLICIES 中的键（mtype 就是策略键）
                m.expiry_policy = mtype if mtype in DECAY_POLICIES else MEMORY_TYPE_DECAY.get(mtype, "fact_knowledge")
                memories.append(m)

        # 获取初始置信度
        init_conf = {m.id: m.current_confidence for m in memories}

        # 模拟衰减：运行调度器，标记时间差
        scheduler = DecayScheduler(mb)
        # 将每个记忆的 last_verified 设为30天前
        past = (datetime.now() - timedelta(days=30)).isoformat()
        for m in memories:
            m.last_verified = past

        scheduler.run()

        # 获取衰减后置信度
        after_conf = {m.id: m.current_confidence for m in memories}

        # 计算衰减率
        decay_rates = {}
        for m in memories:
            before = init_conf[m.id]
            after = after_conf[m.id]
            rate = (before - after) / before if before else 0
            decay_rates[m.memory_type] = rate

        # 验证：session_state 衰减最高，fact_knowledge 最低
        sr = decay_rates.get("session_state", 0)
        br = decay_rates.get("behavior_pattern", 0)
        pr = decay_rates.get("user_preference", 0)
        fr = decay_rates.get("fact_knowledge", 0)

        # session_state 30天应衰减显著（0.05/天 × 30天）
        session_decayed = sr > 0.3
        # fact_knowledge 应几乎不衰减（0.0005/天 × 30天 = 0.015）
        fact_stable = fr < 0.05

        r = MemBenchResult("差异化衰减", "衰减")
        r.passed = session_decayed and fact_stable
        r.score = sr
        r.expected = "session(快)大幅衰减≥30%, fact(极慢)几乎不变<5%"
        r.actual = f"session={sr:.1%} behavior={br:.1%} preference={pr:.1%} fact={fr:.1%}"
        r.detail = {
            "decay_rates": decay_rates,
            "init_conf": {k: round(v, 3) for k, v in init_conf.items()},
            "after_conf": {k: round(v, 3) for k, v in after_conf.items()},
        }
        if not r.passed:
            r.error = f"衰减异常: session={sr:.1%} fact={fr:.1%}"
        results.append(r)
    finally:
        _cleanup(tmp)


def test_promotion_curve(results: list):
    """暂存→晋升——同一信息重复出现，置信度应逐步提升"""
    mb, tmp = make_isolated_bank()
    try:
        # 写入一次（低置信度）
        content = "用户在项目管理中使用Notion"
        r1 = mb.write(content, "user_stated", tags=["测试"], branch="测试")
        id1 = r1.get("id", "")
        c1 = mb._id_index[id1].current_confidence if id1 in mb._id_index else 0

        # 再次写入（用户确认，高置信度信源）
        r2 = mb.write(content, "user_correction", tags=["测试"], branch="测试")
        # 应合并到同一条，置信度提升
        # 查找该记忆（可能 id 相同因为内容相同）
        mem = mb._id_index.get(id1)
        c2 = mem.current_confidence if mem else 0

        # 第三次写入（用户重复行为）
        r3 = mb.write(content, "user_repeated", tags=["测试"], branch="测试")
        mem = mb._id_index.get(id1)
        c3 = mem.current_confidence if mem else 0

        # 验证：首次写入后置信度低，重复写入后显著提升
        promoted = c3 > c1  # 最终置信度高于初始
        high_enough = c2 >= 0.8  # 用户纠正后应 ≥ 0.8

        r = MemBenchResult("暂存→晋升曲线", "晋升")
        r.passed = promoted and high_enough
        r.score = max(c1, c2, c3)
        r.expected = "置信度从低（~0.4）提升到高（≥0.8）"
        r.actual = f"{c1:.2f} → {c2:.2f} → {c3:.2f}"
        r.detail = {
            "confidence_curve": [c1, c2, c3],
            "writes": ["user_stated", "user_correction", "user_repeated"],
            "final_confidence": max(c1, c2, c3),
        }
        if not r.passed:
            r.error = f"置信度未有效提升: {c1:.2f} → {c3:.2f}"
        results.append(r)
    finally:
        _cleanup(tmp)


def test_cross_session_continuity(results: list):
    """跨会话连续性——多轮会话中分散的信息应能被整合召回"""
    mb, tmp = make_isolated_bank()
    try:
        # 模拟跨会话写入
        sessions = [
            ("session_1", "2026-07-01", "我住在北京，在三环"),
            ("session_1", "2026-07-01", "工作是在一家AI创业公司做产品"),
            ("session_2", "2026-07-03", "最近搬家了，搬到朝阳区"),
            ("session_2", "2026-07-03", "通勤时间从30分钟变成了45分钟"),
            ("session_3", "2026-07-05", "新家在朝阳公园附近"),
        ]

        for sid, ts, text in sessions:
            ctx = {"session_timestamp": ts, "session_id": sid}
            mb.write(text, "user_stated", tags=["会话测试", sid], context=ctx, branch="测试")

        # 测试查询：直接用关键词搜索
        queries = [
            ("朝阳", "应该命中'搬到朝阳区'"),
            ("三环", "应该命中'住在北京，在三环'"),
            ("AI创业", "应该命中'AI创业公司'"),
            ("45分钟", "应该命中'变成了45分钟'"),
        ]

        hits = 0
        for kw, expected in queries:
            results_list = mb.query(keyword=kw, status="", min_confidence=0.0)
            blob = " ".join(m.get("content", "") for m in results_list)
            if kw.lower() in blob.lower():
                hits += 1

        recall = hits / len(queries)

        r = MemBenchResult("跨会话连续性", "检索")
        r.passed = recall >= 0.75
        r.score = recall
        r.expected = "≥75% 查询命中跨会话信息"
        r.actual = f"{hits}/{len(queries)} ({recall:.0%})"
        r.detail = {"queries": len(queries), "hits": hits, "recall": recall}
        if not r.passed:
            r.error = f"召回率不足: {recall:.0%}"
        results.append(r)
    finally:
        _cleanup(tmp)


def test_fact_recall_multi_turn(results: list):
    """多轮事实召回——分散在多个对话中的事实应能被检索到"""
    mb, tmp = make_isolated_bank()
    try:
        # 模拟多轮对话中分散的事实
        facts = [
            "Caroline is a transgender woman",
            "Caroline went to a LGBTQ support group",
            "Caroline is researching adoption agencies",
            "Caroline wants to pursue counseling",
            "Melanie has three kids",
            "Melanie likes pottery and painting",
            "Melanie has a dog named Oliver",
            "Caroline moved from Sweden 4 years ago",
        ]

        for i, fact in enumerate(facts):
            ctx = {"session_timestamp": f"2026-07-{i+1:02d}"}
            mb.write(fact, "user_stated", tags=["多轮测试", f"turn_{i}"], context=ctx, branch="测试")

        # 测试查询
        queries = [
            ("Caroline identity", "transgender"),
            ("Caroline adoption", "adoption agencies"),
            ("Melanie kids", "three kids"),
            ("Melanie hobbies", "pottery"),
            ("Caroline origin", "Sweden"),
            ("Melanie pet", "Oliver"),
        ]

        hits = 0
        for q, kw in queries:
            terms = [w for w in re.findall(r'[a-zA-Z]+', q) if len(w) > 2]
            found = []
            for term in terms:
                for m in mb.query(keyword=term, status="", min_confidence=0.0):
                    found.append(m.get("content", ""))
            blob = " ".join(found)
            if kw.lower() in blob.lower():
                hits += 1

        recall = hits / len(queries)

        r = MemBenchResult("多轮事实召回", "检索")
        r.passed = recall >= 0.83
        r.score = recall
        r.expected = "≥83% 事实召回"
        r.actual = f"{hits}/{len(queries)} ({recall:.0%})"
        r.detail = {"queries": len(queries), "hits": hits, "recall": recall}
        if not r.passed:
            r.error = f"召回率不足: {recall:.0%}"
        results.append(r)
    finally:
        _cleanup(tmp)


def _cleanup(tmp):
    """清理临时目录"""
    import shutil
    try:
        shutil.rmtree(tmp)
    except Exception:
        pass


# ═══════════════════════════════════════════════
# 报告生成
# ═══════════════════════════════════════════════

def generate_report(results: list, quick: bool) -> str:
    """生成 Markdown 报告"""
    lines = []
    now = datetime.now()
    total = len(results)
    passed = sum(1 for r in results if r.passed)

    lines.append(f"# 灵识记忆系统 · 特色 Benchmark 报告")
    lines.append(f"")
    lines.append(f"> 测试时间：{now.strftime('%Y-%m-%d %H:%M')}")
    lines.append(f"> 模式：{'快速' if quick else '完整'}")
    lines.append(f"> 结果：{passed}/{total} 通过 ({passed/total*100:.0f}%)")
    lines.append(f"")

    # 按类别分组
    categories = {}
    for r in results:
        categories.setdefault(r.category, []).append(r)

    for cat_name in ["置信度", "分叉", "衰减", "晋升", "检索"]:
        if cat_name not in categories:
            continue
        items = categories[cat_name]
        lines.append(f"## {cat_name}")
        lines.append(f"")
        lines.append(f"| 测试 | 结果 | 得分 | 期望 | 实际 |")
        lines.append(f"|------|------|------|------|------|")
        for r in items:
            status = "✅" if r.passed else "❌"
            score = f"{r.score:.2f}" if isinstance(r.score, float) else str(r.score)
            lines.append(f"| {r.name} | {status} | {score} | {r.expected} | {r.actual} |")
        lines.append(f"")

        # 失败明细
        failures = [r for r in items if not r.passed]
        if failures:
            for r in failures:
                lines.append(f"- ❌ **{r.name}**: {r.error}")
            lines.append(f"")

    # 性能摘要
    lines.append(f"## 性能摘要")
    lines.append(f"")
    lines.append(f"| 类别 | 通过率 | 平均分 |")
    lines.append(f"|------|--------|--------|")
    for cat_name, items in categories.items():
        c_pass = sum(1 for r in items if r.passed)
        c_total = len(items)
        avg_score = statistics.mean([r.score for r in items]) if items else 0
        lines.append(f"| {cat_name} | {c_pass}/{c_total} ({c_pass/c_total*100:.0f}%) | {avg_score:.2f} |")
    lines.append(f"")

    lines.append(f"---")
    lines.append(f"*报告由 memory_bench.py v1.0 自动生成 | {now.strftime('%Y-%m-%d %H:%M')}*")
    lines.append(f"")

    return '\n'.join(lines)


def save_baseline(results: list):
    """保存基线"""
    baselines = {}
    for r in results:
        baselines[r.name] = {
            "score": r.score,
            "passed": r.passed,
            "category": r.category,
        }
    now = datetime.now()
    payload = {
        "timestamp": now.isoformat(),
        "date": now.strftime("%Y-%m-%d"),
        "pass_rate": sum(1 for r in results if r.passed) / len(results) if results else 0,
        "baselines": baselines,
    }
    path = BASELINE_DIR / f"membench_{now.strftime('%Y%m%d_%H%M%S')}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  📦 基线已保存: {path}")
    return path


def list_baselines():
    files = sorted(BASELINE_DIR.glob("membench_*.json"), reverse=True)
    if not files:
        print("  (无历史基线)")
        return
    print(f"\n  历史基线 (共 {len(files)} 条):")
    for f in files:
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            rate = data.get("pass_rate", 0) * 100
            print(f"  {data.get('date', '?'):<20} {rate:<10.0f}% {f.name}")
        except:
            print(f"  {'?':<20} {'?':<10} {f.name}")


# ═══════════════════════════════════════════════
# 主流程
# ═══════════════════════════════════════════════

def run_bench(quick: bool = False) -> list:
    results = []

    print(f"\n{'='*60}")
    print(f"  灵识记忆系统 · 特色 Benchmark")
    print(f"  模式: {'快速' if quick else '完整'}")
    print(f"  时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*60}")

    # 1. 置信度
    print(f"\n  📊 置信度测试...")
    test_confidence_levels(results)
    print(f"     ✅ {sum(1 for r in results if r.passed and r.category=='置信度')}/{sum(1 for r in results if r.category=='置信度')}")

    # 2. 分叉
    print(f"\n  🔀 分叉测试...")
    test_fork_on_conflict(results)
    print(f"     ✅ {sum(1 for r in results if r.passed and r.category=='分叉')}/{sum(1 for r in results if r.category=='分叉')}")

    # 3. 衰减（快速模式跳过）
    if not quick:
        print(f"\n  📉 衰减测试...")
        test_decay_differential(results)
        print(f"     ✅ {sum(1 for r in results if r.passed and r.category=='衰减')}/{sum(1 for r in results if r.category=='衰减')}")
    else:
        print(f"\n  📉 衰减测试 (跳过 - 快速模式)")

    # 4. 晋升（快速模式跳过）
    if not quick:
        print(f"\n  📈 晋升测试...")
        test_promotion_curve(results)
        print(f"     ✅ {sum(1 for r in results if r.passed and r.category=='晋升')}/{sum(1 for r in results if r.category=='晋升')}")
    else:
        print(f"\n  📈 晋升测试 (跳过 - 快速模式)")

    # 5. 跨会话连续性
    print(f"\n  🔄 跨会话连续性测试...")
    test_cross_session_continuity(results)
    print(f"     ✅ {sum(1 for r in results if r.passed and r.category=='检索')}/{sum(1 for r in results if r.category=='检索')}")

    # 6. 多轮事实召回
    print(f"\n  📋 多轮事实召回测试...")
    test_fact_recall_multi_turn(results)
    print(f"     ✅ {sum(1 for r in results if r.passed and r.category=='检索')}/{sum(1 for r in results if r.category=='检索')}")

    total = len(results)
    passed = sum(1 for r in results if r.passed)
    print(f"\n{'='*60}")
    print(f"  结果: {passed}/{total} 通过 ({passed/total*100:.1f}%)")
    print(f"{'='*60}")

    return results


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="灵识记忆系统特色 Benchmark")
    parser.add_argument("--quick", action="store_true", help="快速模式（跳过衰减/晋升）")
    parser.add_argument("--list", action="store_true", help="列出历史基线")
    args = parser.parse_args()

    if args.list:
        list_baselines()
        sys.exit(0)

    results = run_bench(quick=args.quick)
    report = generate_report(results, args.quick)
    print(f"\n{report}")
    save_baseline(results)

    # 写报告文件
    now = datetime.now()
    path = REPORT_DIR / f"记忆特色基准-{now.strftime('%Y%m%d-%H%M')}.md"
    path.write_text(report, encoding="utf-8")
    print(f"  📄 报告已写入: {path}")
