# -*- coding: utf-8 -*-
"""
CheckPoint 引擎 — 制作者-检查者分离的核心验证模块
==================================================
Loop Engineering §4 启发二的落地实现：
将生产和检查拆成独立模块，输出结构化 pass/fail + 差异报告。

设计原则:
- 每个 check 是对应验证方法，返回 CheckResult
- run_checks(scope) 批量执行，聚合报告
- 检查结果有且仅有两个输出：pass / fail + 差异详情
"""

import json
import os
from pathlib import Path
from dataclasses import dataclass, asdict, field
from datetime import datetime
from typing import Dict, List, Optional


@dataclass
class CheckResult:
    """单条检查结果"""
    name: str          # 检查项名称（如 rule5_compliance）
    passed: bool       # 通过/不通过
    detail: str        # 详细说明（不通过时描述具体问题）
    metric: Dict = field(default_factory=dict)  # 关键指标（如 {triggered:34, completed:15}）


@dataclass
class CheckReport:
    """检查报告"""
    timestamp: str
    scope: str            # 检查范围
    total_checks: int
    passed_checks: int
    failed_checks: int
    passed: bool          # 全局是否通过（全部通过=true）
    checks: List[Dict]    # 每项检查结果
    summary: str          # 一句话总结


class CheckPointEngine:
    """CheckPoint 验证引擎"""
    
    def __init__(self, vault_path: str = None):
        if vault_path is None:
            self.vault_path = r"."
        else:
            self.vault_path = vault_path
        
        self.丹房 = Path(self.vault_path) / "丹房"
        self.report_dir = Path(self.vault_path) / "体检" / "checker"
        self.report_dir.mkdir(parents=True, exist_ok=True)
        
        # 感知统计文件路径
        self._stats_path = Path(self.vault_path) / ".meta" / "perception_stats.json"
        
        # index.json 缓存（优化：避免 check_index 和 check_refine_quality 重复 IO）
        self._cached_index = None
        self._cached_index_mtime = 0
        self._index_path = self.丹房 / ".meta" / "index.json"
    
    # ─── 公共入口 ───
    
    def run_checks(self, scope: str = "all") -> dict:
        """
        运行指定范围的检查
        
        Args:
            scope: "all" | "rule5" | "index" | "patrol" | "refine"
            
        Returns:
            CheckReport 的 dict 表示
        """
        checks_map = {
            "all":    [self.check_rule5, self.check_index, self.check_refine_quality],
            "rule5":  [self.check_rule5],
            "index":  [self.check_index],
            "patrol": [self.check_patrol_completeness],
            "refine": [self.check_refine_quality],
        }
        
        check_funcs = checks_map.get(scope, [self.check_rule5, self.check_index])
        
        results: List[CheckResult] = []
        for fn in check_funcs:
            try:
                result = fn()
                results.append(result)
            except Exception as e:
                results.append(CheckResult(
                    name=fn.__name__.replace("check_", ""),
                    passed=False,
                    detail=f"检查执行异常: {e}",
                    metric={}
                ))
        
        passed_checks = sum(1 for r in results if r.passed)
        report = CheckReport(
            timestamp=datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
            scope=scope,
            total_checks=len(results),
            passed_checks=passed_checks,
            failed_checks=len(results) - passed_checks,
            passed=passed_checks == len(results),
            checks=[asdict(r) for r in results],
            summary=self._build_summary(results, passed_checks, len(results))
        )
        
        return asdict(report)
    
    # ─── Checks ───
    
    def check_rule5(self) -> CheckResult:
        """
        规则⑤ 检索纪律检查
        
        检查感知统计中的 rule5 找到率（非三步完成率，详见 perception_stats）
        阈值 ≥ 50% 表示至少一半的检索请求有返回结果
        """
        stats = self._load_stats()
        rule5 = stats.get("rules", {}).get("rule5_search", {})
        triggered = rule5.get("triggered", 0)
        completed = rule5.get("completed", 0)
        
        if triggered < 3:
            # 数据量太少，不判定违规
            return CheckResult(
                name="rule5_compliance",
                passed=True,
                detail=f"数据量不足（triggered={triggered}），暂不判定",
                metric={"triggered": triggered, "completed": completed, "rate": 0}
            )
        
        rate = completed / triggered if triggered > 0 else 0
        threshold = 0.5
        
        if rate >= threshold:
            return CheckResult(
                name="rule5_compliance",
                passed=True,
                detail=f"检索纪律合规: 完成率 {rate:.1%} ({completed}/{triggered})",
                metric={"triggered": triggered, "completed": completed, "rate": round(rate, 3)}
            )
        else:
            return CheckResult(
                name="rule5_compliance",
                passed=False,
                detail=(
                    f"检索纪律违规: 完成率 {rate:.1%} ({completed}/{triggered}) "
                    f"低于阈值 {threshold:.0%}。"
                    f"建议: (1) 检查是否跳过了三步检索管线; "
                    f"(2) 关键词是否匹配已有索引; "
                    f"(3) 是否有知识缺口需要补充"
                ),
                metric={"triggered": triggered, "completed": completed, "rate": round(rate, 3)}
            )
    
    def check_index(self) -> CheckResult:
        """
        索引完整性检查
        
        验证 index.json 中的文件路径在磁盘上真实存在
        """
        index_path = self.丹房 / ".meta" / "index.json"
        if not index_path.exists():
            return CheckResult(
                name="index_integrity",
                passed=False,
                detail=f"索引文件不存在: {index_path}",
                metric={"total": 0, "missing": 0, "ok": 0}
            )
        
        index = self._load_index()
        
        pages = index.get("pages", [])
        missing = []
        checked = 0
        
        for page in pages:
            path = page.get("path", "")
            if not path:
                continue
            checked += 1
            # 将丹房/xxx/yyy 转为磁盘路径
            disk_path = self.丹房 / path.replace("丹房/", "", 1) if path.startswith("丹房/") else Path(self.vault_path) / f"{path}.md"
            # 确保 .md 后缀（处理路径中已含 .md 的情况，如 CLAUDE.md 中文译本）
            disk_str = str(disk_path)
            if not disk_str.endswith(".md"):
                disk_str += ".md"
            if not Path(disk_str).exists():
                missing.append(path)
        
        if not missing:
            return CheckResult(
                name="index_integrity",
                passed=True,
                detail=f"索引完整: {checked} 页全部在磁盘上",
                metric={"total": checked, "missing": 0, "ok": checked}
            )
        else:
            return CheckResult(
                name="index_integrity",
                passed=False,
                detail=f"索引不一致: {len(missing)}/{checked} 页在磁盘上找不到: {missing[:5]}",
                metric={"total": checked, "missing": len(missing), "ok": checked - len(missing)}
            )
    
    def check_patrol_completeness(self) -> CheckResult:
        """
        巡更完整性检查
        
        验证今日各巡更任务是否完成（工作印记已取代人类版日志，跳过于此）"""
        today = datetime.now().strftime("%Y-%m-%d")
        
        return CheckResult(
            name="patrol_completeness",
            passed=True,
            detail="人类版日志已退役，改用工作印记",
            metric={}
        )
        
        with open(log_path, "r", encoding="utf-8") as f:
            content = f.read()
        
        # 期望的巡更任务类型（按工作时间）
        expected = {
            # "早安": "08",   # 早安日报
            "体检": "18",      # 每日检
            "内观": "21",      # 每日内观
        }
        
        found = []
        missing = []
        for name, hour in expected.items():
            if f"[{today[:5]}|{today[:5]} {hour}" in content or f"{name}" in content and today[:5] in content:
                found.append(name)
            else:
                # 更精确检查：日志行包含今天日期+该类型
                pass_flag = False
                for line in content.split("\n"):
                    if today[:5] in line and name in line:
                        pass_flag = True
                        break
                if pass_flag:
                    found.append(name)
                else:
                    missing.append(name)
        
        if not missing:
            return CheckResult(
                name="patrol_completeness",
                passed=True,
                detail=f"今日巡更任务全部完成: {', '.join(found)}",
                metric={"expected": list(expected.keys()), "found": found, "missing": missing}
            )
        else:
            return CheckResult(
                name="patrol_completeness",
                passed=False,
                detail=f"巡更任务未完成: 缺少 {', '.join(missing)}（已完成的: {', '.join(found)}）",
                metric={"expected": list(expected.keys()), "found": found, "missing": missing}
            )

    def check_refine_quality(self) -> CheckResult:
        """
        提炼产出质量检查

        验证最近提炼的丹房页面的：
        1. frontmatter 完整性（标题/日期/类型/品级/关联）
        2. wikilink 可达性（链接的页面在 index.json 中存在）
        """
        # 中文字段名 → index.json 字段名映射
        fm_mapping = {
            "标题": "title",
            "日期": "date",
            "品级": "pinji",
        }
        required_fm = {"标题", "日期"}
        index_path = self.丹房 / ".meta" / "index.json"
        if not index_path.exists():
            return CheckResult(
                name="refine_quality",
                passed=False,
                detail="索引文件不存在，无法验证",
                metric={"checked": 0, "fm_ok": 0, "fm_fail": 0, "links_ok": 0, "links_fail": 0}
            )

        index = self._load_index()

        pages = index.get("pages", [])
        today = datetime.now().strftime("%Y-%m-%d")

        # 找出今日创建/修改的丹房页（date 字段匹配今日）
        today_pages = []
        for p in pages:
            page_date = p.get("date", "")
            if page_date and today in page_date:
                today_pages.append(p)

        if not today_pages:
            return CheckResult(
                name="refine_quality",
                passed=True,
                detail="今日无新提炼页面，不检查",
                metric={"checked": 0, "fm_ok": 0, "fm_fail": 0, "links_ok": 0, "links_fail": 0}
            )

        # 建立所有页面路径的集合（用于链接可达性检查）
        all_paths = set()
        for p in pages:
            path = p.get("path", "")
            if path:
                all_paths.add(path)

        fm_ok = 0
        fm_fail = 0
        links_ok = 0
        links_fail = 0
        fm_issues = []
        link_issues = []

        for page in today_pages:
            page_title = page.get("title", "")
            page_path = page.get("path", "")
            missing_fm = []
            for field in required_fm:
                idx_key = fm_mapping.get(field, field.lower())
                if not page.get(idx_key, ""):
                    missing_fm.append(field)
            if missing_fm:
                fm_fail += 1
                fm_issues.append(f"{page_title}(缺失: {', '.join(missing_fm)})")
            else:
                fm_ok += 1

            # wikilink 可达性检查
            links_to = page.get("links_to", [])
            broken = []
            for link in links_to:
                if link.startswith("http") or link.startswith("#") or link.startswith("标签"):
                    continue
                link_clean = link.lstrip("[[") if "]]" not in link else link
                if link_clean not in all_paths:
                    alt = f"丹房/{link_clean}" if not link_clean.startswith("丹房/") else None
                    if alt and alt not in all_paths:
                        broken.append(link_clean)

            if broken:
                links_fail += 1
                link_issues.append(f"{page_title}(死链: {', '.join(broken[:3])})")
            else:
                links_ok += 1

        fm_passed = fm_fail == 0
        links_passed = links_fail == 0
        passed = fm_passed and links_passed

        issues = []
        if not fm_passed:
            issues.append(f"FM 不完整: {', '.join(fm_issues)}")
        if not links_passed:
            issues.append(f"死链: {', '.join(link_issues)}")

        return CheckResult(
            name="refine_quality",
            passed=passed,
            detail=f"检查 {len(today_pages)} 页。{'全部通过' if passed else '; '.join(issues)}",
            metric={
                "checked": len(today_pages),
                "fm_ok": fm_ok, "fm_fail": fm_fail,
                "links_ok": links_ok, "links_fail": links_fail
            }
        )

    # ─── 内部方法 ───
    
    def _load_stats(self) -> dict:
        """加载感知统计数据"""
        if self._stats_path.exists():
            with open(self._stats_path, "r", encoding="utf-8") as f:
                return json.load(f)
        return {}
    
    def _load_index(self) -> dict:
        """加载 index.json（带 mtime 缓存：同一次 run_checks 中复用）"""
        index_path = self._index_path
        if not index_path.exists():
            return {}
        
        try:
            current_mtime = index_path.stat().st_mtime
        except OSError:
            current_mtime = 0
        
        # 缓存命中：mtime 未变，直接复用
        if self._cached_index is not None and current_mtime == self._cached_index_mtime:
            return self._cached_index
        
        with open(index_path, "r", encoding="utf-8") as f:
            self._cached_index = json.load(f)
        self._cached_index_mtime = current_mtime
        return self._cached_index
    
    def _build_summary(self, results: List[CheckResult], passed: int, total: int) -> str:
        """生成一句话总结"""
        if total == 0:
            return "无检查项"
        if passed == total:
            return f"全部通过: {passed}/{total}"
        else:
            failed_items = [r.name for r in results if not r.passed]
            return f"未通过: {total-passed}/{total} 失败 ({', '.join(failed_items)})"