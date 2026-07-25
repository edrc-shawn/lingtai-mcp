# -*- coding: utf-8 -*-
"""观察层 mixin"""
import time
from pathlib import Path
from decorators import tool
from logger import get_logger
log = get_logger(__name__)

class ObservationMixin:
    @tool(readonly=True, write=False, category="observation", system=True, name="observation_list")
    def observations(self, keyword: str = "", limit: int = 20) -> dict:
        """
        查询自动归纳出的观察

        Args:
            keyword: 搜索关键词（可选，空则返回全部）
            limit: 最大返回数

        Returns:
            dict: 观察列表（facts 已截断，防 >200k tokens 爆输出）
        """
        if keyword:
            results_raw = self.observation.query(keyword)
        else:
            results_raw = [obs.to_dict() for obs in self.observation.observations]

        # 截断每个 observation 的 facts
        max_facts = 3
        max_fact_length = 200
        truncated = []
        for obs in results_raw:
            entry = dict(obs)
            if "facts" in entry and isinstance(entry["facts"], list):
                entry["fact_count"] = len(entry["facts"])
                entry["facts"] = [
                    {
                        "content": (f["content"][:max_fact_length] + "..."
                                    if len(f["content"]) > max_fact_length
                                    else f["content"]),
                        "source": f.get("source", ""),
                        "added_at": f.get("added_at", ""),
                    }
                    for f in entry["facts"][:max_facts]
                ]
            truncated.append(entry)

        return {
            "total": len(results_raw),
            "observations": truncated[:limit],
        }

    @tool(readonly=True, write=False, category="observation", system=True)
    def observation_stats(self) -> dict:
        """
        观察层统计信息
        
        Returns:
            dict: 统计
        """
        return self.observation.get_stats()

    @tool(readonly=True, write=False, category="observation", system=True, name="observation_rule_health")
    def sentinel(self) -> dict:
        """
        感知规则监控报告（Sentinel）。检查各规则的健康状态和违规情况
        
        Returns:
            dict: 监控报告（含健康状态、违规列表、统计摘要）
        """
        return self.perception_stats_monitor.get_monitoring_report()

    def decay_observations(self) -> dict:
        """
        时序衰减：降低长期未更新观察的置信度（巡更自动化触发）
        """
        return self.observation.decay()

    @tool(readonly=True, write=False, category="observation", system=False, name="observation_reflect")
    def reflect(self, depth: str = "standard") -> dict:
        """
        全量反思五检
        
        Args:
            depth: 深度（quick/standard/deep）
        
        Returns:
            dict: 反思报告
        """
        return self.reflect_engine.reflect(depth=depth)

    @tool(readonly=True, write=False, category="knowledge", system=False, name="knowledge_gaps")
    def gaps(self, domain: str = None, min_severity: float = 0.0) -> dict:
        """
        知识缺口：扫描原料中待提炼且丹房无对应条目的内容
        
        Args:
            domain: 按域筛选（可选，如"07-工具与AI"）
            min_severity: 最低严重度（0.0-1.0，默认0全部返回）
        
        Returns:
            dict: 缺口列表 + 统计 + last_checked_days_ago
        """
        import os, json
        from datetime import datetime
        from reflect_engine import Finding
        # 缓存缺口分析结果 30 分钟（避免每次调用都扫描 780+ 原料）
        _cache_attr = '_gaps_cache'
        _cache = getattr(self, _cache_attr, None)
        now = time.time()
        if _cache and now - _cache['ts'] < 1800:
            raw = _cache['raw']
        else:
            raw = self.reflect_engine._check_knowledge_gaps()
            setattr(self, _cache_attr, {'raw': raw, 'ts': now})
            # 持久化当前检查时间戳
            self._persist_gaps_check(raw)
        gaps_list = []
        for f in raw:
            if f.severity < min_severity:
                continue
            gap = {"topic": f.topic, "severity": f.severity, "detail": f.detail, "suggestion": f.suggestion}
            if domain:
                if domain.lower() in f.topic.lower() or domain.lower() in f.detail.lower():
                    gaps_list.append(gap)
            else:
                gaps_list.append(gap)
        # 读取上次检查时间
        last_checked_days = self._read_last_gaps_check()
        return {
            "total_gaps": len(raw),
            "returned": len(gaps_list),
            "domain_filter": domain,
            "last_checked_days_ago": last_checked_days,
            "gaps": gaps_list,
        }

    def _persist_gaps_check(self, findings: list) -> None:
        """将本次缺口检查结果写入 knowledge_gaps.jsonl"""
        import os, json
        from datetime import datetime
        try:
            logs_dir = os.path.join(self.vault_path, ".tool", "lingtai-kb", "logs")
            os.makedirs(logs_dir, exist_ok=True)
            path = os.path.join(logs_dir, "knowledge_gaps.jsonl")
            entry = {
                "timestamp": datetime.now().isoformat(),
                "total_gaps": len(findings),
                "topics": [f.topic for f in findings[:20]],
            }
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception:
            log.debug("suppressed", exc_info=True)

    def _read_last_gaps_check(self) -> float:
        """读取距上次缺口检查的天数，返回 float（无记录时返回 -1）"""
        import os, json
        from datetime import datetime, timezone
        try:
            path = os.path.join(self.vault_path, ".tool", "lingtai-kb", "logs", "knowledge_gaps.jsonl")
            if not os.path.exists(path):
                return -1.0
            last_ts = None
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                        ts = entry.get("timestamp", "")
                        if ts:
                            last_ts = ts
                    except json.JSONDecodeError:
                        continue
            if last_ts:
                last_dt = datetime.fromisoformat(last_ts)
                now_dt = datetime.now(timezone.utc).astimezone() if last_dt.tzinfo else datetime.now()
                delta = (now_dt - last_dt).total_seconds() / 86400
                return round(delta, 1)
            return -1.0
        except Exception:
            return -1.0

    # ═══ P2：轻量自主丰富 ═══

    @tool(readonly=True, write=False, category="maintenance", system=False, name="nightly_enrich")
    def nightly_enrich(self, mode: str = "scan") -> dict:
        """
        轻量自主丰富（P2）——扫描原料实体 + 自动建 stub + 孤岛检测

        Args:
            mode: "scan"（只扫描不写入）
                  "auto_stubs"（自动建 stub 页）
                  "orphan_check"（孤岛检测）
                  "full"（全部）

        Returns:
            dict: 扫描结果，含 findings/warnings/stubs_created 等
        """
        import os, re, json
        from pathlib import Path
        from datetime import datetime

        vault = Path(self.vault_path)
        now = datetime.now()
        result = {"mode": mode, "timestamp": now.isoformat()}
        raw_dir = vault / "原料"
        danfang_dir = vault / "丹房"

        # ── Scan: 实体扫描 ──
        if mode in ("scan", "full"):
            raw_dir = vault / "原料"
            raw_files = sorted(raw_dir.glob("*.md")) if raw_dir.is_dir() else []

            # 扫描原料文件名 + 正文头部，提取高频概念
            raw_titles = []
            for rf in raw_files:
                stem = rf.stem.strip()
                # 跳过索引/系统文件
                if stem in ("索引", "README", "模板"):
                    continue
                raw_titles.append(stem)

            # 收集丹房已有页面标题
            danfang_dir = vault / "丹房"
            danfang_pages = set()
            for dd in (danfang_dir.glob("*/")) if danfang_dir.is_dir() else []:
                for dp in dd.glob("*.md"):
                    danfang_pages.add(dp.stem.strip())

            # 提取原料中高频但丹房未覆盖的实体
            _NOISE_WORDS = frozenset([
                'v1','v2','v3','v4','v5','v6','第一版','第二版','第三版','第四版',
                '初版','终版','定稿','草稿','原版','版本',
                '思考-','思考','笔记','记录','副本','备份','copy',
            ])
            _NOISE_RE = re.compile(r'^[0-9vV\s\-_]+$')
            from collections import Counter
            word_freq = Counter()
            for t in raw_titles:
                parts = re.split(r'[：:，,。.、/\s]+', t)
                for p in parts:
                    p = p.strip().lower()
                    if len(p) < 2 or len(p) > 20:
                        continue
                    if p in _NOISE_WORDS or _NOISE_RE.match(p):
                        continue
                    word_freq[p] += 1

            # 找到高提及率但丹房无对应页的实体
            entities = []
            for word, freq in word_freq.most_common(50):
                if word in danfang_pages:
                    continue
                # 检查是否有丹房页标题包含该词
                existing = [p for p in danfang_pages if word in p]
                if existing:
                    continue
                severity = min(1.0, freq / 10)  # 10次以上->1.0
                if severity >= 0.3:
                    entities.append({
                        "entity": word, "mentions": freq, "severity": round(severity, 2),
                    })

            result["scan"] = {
                "raw_count": len(raw_files),
                "danfang_count": len(danfang_pages),
                "high_severity": [e for e in entities if e["severity"] >= 0.7],
                "medium_severity": [e for e in entities if 0.3 <= e["severity"] < 0.7],
                "total_candidates": len(entities),
            }

        # ── Auto Stubs: 自动建 stub ──
        if mode in ("auto_stubs", "full"):
            scan_data = result.get("scan") or self._run_scan_internal(vault)
            high_sev = scan_data.get("high_severity", [])
            created = []
            skipped = []
            for ent in high_sev[:5]:  # 一次最多建5个
                entity_name = ent["entity"]
                safe_name = re.sub(r'[<>:"/\\|?*#]', '', entity_name).strip()
                if not safe_name:
                    skipped.append({"entity": entity_name, "reason": "名称含非法字符"})
                    continue

                # 检查是否已存在（防并发竞态）
                page_path = f"丹房/07-工具与AI/{safe_name}.md"
                abs_path = vault / page_path.replace('/', os.sep)
                if abs_path.exists():
                    skipped.append({"entity": entity_name, "reason": "页面已存在"})
                    continue

                # 构建 stub 内容
                today_str = now.strftime('%Y-%m-%d')
                stub_content = f"""---
标题: {safe_name}
创建日期: {today_str}
更新日期: {today_str}
品级: 下品
标签: [自动生成, 待提炼]
---

# {safe_name}

#自动生成 #待提炼

> 此页面由 night_enrich 自动生成，内容待人工提炼。
> 来源：原料中高频提及（{ent['mentions']} 次）。

<!-- 编译真理区 -->
## 编译真理

暂无综合判断。

<!-- 时间线区 -->
## 时间线

### {today_str}
自动生成 stub，来源：原料高频提及。
"""
                try:
                    abs_path.parent.mkdir(parents=True, exist_ok=True)
                    with open(abs_path, 'w', encoding='utf-8') as f:
                        f.write(stub_content)
                    # 注册到 memory engine
                    try:
                        if hasattr(self, 'memory'):
                            self.memory.hot_register_page(
                                path=page_path, title=safe_name,
                                domain="07-工具与AI", summary="自动生成 stub，待提炼",
                                tags=["自动生成", "待提炼"], pinji="下品",
                            )
                    except Exception:
                        log.debug("suppressed", exc_info=True)
                    created.append({
                        "entity": entity_name, "path": page_path,
                        "mentions": ent["mentions"],
                    })
                except Exception as e:
                    skipped.append({"entity": entity_name, "reason": str(e)})

            result["stubs"] = {"created": created, "skipped": skipped, "total_created": len(created)}

        # ── Orphan Check: 孤岛检测（用 memory engine 的 linked_from 数据）──
        if mode in ("orphan_check", "full"):
            orphans = []
            # 优先从 memory engine 读取页面数据
            if hasattr(self, 'memory') and hasattr(self.memory, 'pages'):
                for p in self.memory.pages:
                    path = p.get("path", "")
                    title = p.get("title", "")
                    domain = p.get("domain", "")
                    backlinks = len(p.get("linked_from", []))
                    if backlinks == 0 and path and domain:
                        orphans.append({
                            "path": path,
                            "title": title,
                            "domain": domain,
                            "pinji": p.get("pinji", ""),
                        })
            # 内存数据不可用时回退到全量扫描（慢）
            elif danfang_dir.is_dir():
                for dd in sorted(danfang_dir.glob("*/")):
                    domain_name = dd.name
                    for dp in dd.glob("*.md"):
                        content = dp.read_text(encoding='utf-8', errors='replace')
                        slug = dp.stem
                        inlinks = 0
                        for other_dd in danfang_dir.glob("*/"):
                            for other_dp in other_dd.glob("*.md"):
                                if other_dp == dp:
                                    continue
                                other_text = other_dp.read_text(encoding='utf-8', errors='replace')
                                if f"[[{slug}" in other_text or f"[[{dp.name}" in other_text:
                                    inlinks += 1
                                    break
                            if inlinks > 0:
                                break
                        if inlinks == 0:
                            title = slug
                            m = re.search(r'^标题:\s*(.+)', content, re.MULTILINE)
                            if m:
                                title = m.group(1).strip()
                            orphans.append({
                                "path": str(dp.relative_to(vault)).replace('\\', '/'),
                                "title": title,
                                "domain": domain_name,
                            })

            result["orphan_check"] = {
                "total_orphans": len(orphans),
                "orphans": orphans[:20],  # 最多返回20个
                "note": "孤岛页（入链为0）建议补充链接或考虑删除",
            }

        # ── Tool Latency: 延迟异常检测 ──
        if mode in ("scan", "full"):
            try:
                if hasattr(self, 'tool_latency') and self.tool_latency:
                    anomalies = self.tool_latency.detect_anomalies(days=7)
                    if anomalies:
                        result["tool_latency"] = {
                            "anomalies": anomalies,
                            "note": "发现工具延迟异常，建议调 health_inspect 查看详情",
                        }
            except Exception:
                log.debug("suppressed", exc_info=True)

        # ── Full: 汇总 ──
        if mode == "full" and result.get("stubs", {}).get("created"):
            result["summary"] = (
                f"实体扫描: {result['scan']['total_candidates']} 候选 / "
                f"{len(result['scan']['high_severity'])} 高严重度 | "
                f"Stubs: 创建 {result['stubs']['total_created']} 个 | "
                f"孤岛: {result['orphan_check']['total_orphans']} 页"
            )

        return result

    def _run_scan_internal(self, vault: Path) -> dict:
        """内部扫描（供 auto_stubs 模式在无 scan 数据时调用）"""
        import re
        raw_dir = vault / "原料"
        raw_files = sorted(raw_dir.glob("*.md")) if raw_dir.is_dir() else []
        raw_titles = [rf.stem.strip() for rf in raw_files if rf.stem.strip() not in ("索引", "README", "模板")]

        danfang_pages = set()
        danfang_dir = vault / "丹房"
        if danfang_dir.is_dir():
            for dd in danfang_dir.glob("*/"):
                for dp in dd.glob("*.md"):
                    danfang_pages.add(dp.stem.strip())

        from collections import Counter
        _NOISE_WORDS = frozenset([
            'v1','v2','v3','v4','v5','v6','第一版','第二版','第三版','第四版',
            '初版','终版','定稿','草稿','原版','版本',
            '思考-','思考','笔记','记录','副本','备份','copy',
        ])
        _NOISE_RE = re.compile(r'^[0-9vV\s\-_]+$')
        word_freq = Counter()
        for t in raw_titles:
            parts = re.split(r'[：:，,。.、/\s]+', t)
            for p in parts:
                p = p.strip().lower()
                if len(p) < 2 or len(p) > 20:
                    continue
                if p in _NOISE_WORDS or _NOISE_RE.match(p):
                    continue
                word_freq[p] += 1

        entities = []
        for word, freq in word_freq.most_common(50):
            if word in danfang_pages:
                continue
            if any(word in p for p in danfang_pages):
                continue
            severity = min(1.0, freq / 10)
            if severity >= 0.3:
                entities.append({"entity": word, "mentions": freq, "severity": round(severity, 2)})

        return {
            "raw_count": len(raw_files), "danfang_count": len(danfang_pages),
            "high_severity": [e for e in entities if e["severity"] >= 0.7],
            "medium_severity": [e for e in entities if 0.3 <= e["severity"] < 0.7],
            "total_candidates": len(entities),
        }
