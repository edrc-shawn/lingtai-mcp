# -*- coding: utf-8 -*-
"""系统工具 mixin — AnySearch/Tavily/日志/Token/规则/推荐/热重载"""
import os
import sys
import json
import importlib
from pathlib import Path
from datetime import datetime
from .shared import (VAULT_PATH, TAVILY_API_KEY, TAVILY_API_URL, TAVILY_MONTHLY_LIMIT,
                     _tavily_month, _tavily_count,
                     ANYSEARCH_API_URL, ANYSEARCH_API_KEY)
import urllib.request
import urllib.error
from decorators import tool
from logger import get_logger


def _get_depth_suggestion(depth_score: float, strong: int, weak: int, weak_pages: list) -> str:
    """根据论证深度检测结果生成建议。"""
    if depth_score >= 0.6:
        return f"论证深度良好：{strong} 页达到深度论证。{weak} 页偏浅，可补强。"
    elif depth_score >= 0.3:
        tips = []
        if weak_pages:
            tips.append(f"优先补强: {weak_pages[0]['path']}")
        tips.append("建议为浅层页添加「补角」段落或局限分析")
        return f"论证深度中等：{depth_score:.0%} 页达标。{'；'.join(tips)}"
    else:
        return f"论证深度偏低：仅 {depth_score:.0%} 页达标。建议从 {weak_pages[0]['path'] if weak_pages else '最早页面'} 起步补强。"

log = get_logger(__name__)

class SystemMixin:
    # ═══════════════════════════════════════════════════════════
    #  联网搜索（AnySearch → Tavily 双通道）
    # ═══════════════════════════════════════════════════════════
    
    @tool(readonly=True, write=False, category="system", system=False)
    def web_search(self, keyword: str, max_results: int = 5) -> dict:
        """
        联网搜索（知识库无答案时的最后回退）。AnySearch(优先) → Tavily(降级)。
        场景：知识库四层全空、确认需要外部实时信息时；需要精细控制搜索 query 时。
        区别：knowledge_search 已含联网层作为第4回退，通常不需要单独调我。

        Returns:
            dict: {results: [...], source: "anysearch"|"tavily"|"none", ...}
        """
        # 优先 AnySearch（1000次/天免费）
        result = self._anysearch_search(keyword, max_results)
        if result.get("results"):
            return result
        
        # 降级 Tavily（1000次/月）
        result = self.tavily_search(keyword, max_results)
        if result.get("results"):
            return result
        
        return {"results": [], "source": "none", "keyword": keyword,
                "error": "AnySearch 和 Tavily 均无结果"}
    
    def _anysearch_search(self, keyword: str, max_results: int = 5) -> dict:
        """
        AnySearch API 搜索（REST API，不依赖 Chrome CDP）
        免费额度：1000次/天
        POST /v1/search  with JSON body + API key
        """
        try:
            url = f"{ANYSEARCH_API_URL}/search"
            body = json.dumps({
                "query": keyword,
                "max_results": min(max_results, 10),
            }).encode('utf-8')
            headers = {
                'Content-Type': 'application/json',
                'Accept': 'application/json',
                'User-Agent': 'LingTai-MCP/4.0',
            }
            if ANYSEARCH_API_KEY:
                headers['Authorization'] = f'Bearer {ANYSEARCH_API_KEY}'
            
            req = urllib.request.Request(url, data=body, headers=headers, method='POST')
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode('utf-8'))
            
            items = data.get("data", {}).get("results", data.get("results", []))
            return {
                "keyword": keyword,
                "total_results": len(items),
                "source": "AnySearch (外部网络搜索)",
                "results": [
                    {
                        "title": r.get("title", ""),
                        "url": r.get("url", r.get("link", "")),
                        "content": r.get("snippet", r.get("content", r.get("description", "")))[:300],
                        "score": r.get("score", 0),
                    }
                    for r in items[:max_results]
                ],
            }
        except Exception as e:
            return {"results": [], "source": "anysearch", "error": str(e)}
    def tavily_search(self, keyword: str, max_results: int = 5) -> dict:
        """
        联网搜索（Tavily API）。三步检索无结果时调用，获取外部信息
        """
        global _tavily_month, _tavily_count
        from datetime import date
        this_month = str(date.today())[:7]

        if _tavily_month != this_month:
            _tavily_month = this_month
            _tavily_count = 0

        if _tavily_count >= TAVILY_MONTHLY_LIMIT:
            return {"error": "已超过每月搜索上限（1000次）", "results": []}

        if not TAVILY_API_KEY:
            return {"error": "未配置Tavily API密钥", "results": []}

        try:
            import requests
            resp = requests.post(TAVILY_API_URL, json={
                "api_key": TAVILY_API_KEY,
                "query": keyword,
                "max_results": min(max_results, 10),
                "search_depth": "basic"
            }, timeout=15)
            data = resp.json()
            _tavily_count += 1

            results = data.get("results", [])
            return {
                "keyword": keyword,
                "total_results": len(results),
                "usage_this_month": _tavily_count,
                "monthly_limit": TAVILY_MONTHLY_LIMIT,
                "source": "Tavily (外部网络搜索)",
                "results": [
                    {
                        "title": r.get("title", ""),
                        "url": r.get("url", ""),
                        "content": r.get("content", "")[:300],
                        "score": r.get("score", 0),
                    }
                    for r in results[:max_results]
                ],
            }
        except Exception as e:
            return {"error": f"搜索失败: {e}", "results": []}

    # ── 日志搜索缓存（同一 MCP 进程内复用，避免重复全量扫描）──
    _LOG_SEARCH_CACHE_TTL = 120  # 秒

    def _log_search_cache_get(self, key):
        cache = getattr(self, "_log_search_cache", None)
        if cache is None:
            return None
        entry = cache.get(key)
        if entry is None:
            return None
        if (datetime.now() - entry["_ts"]).total_seconds() > self._LOG_SEARCH_CACHE_TTL:
            del cache[key]
            return None
        return entry["data"]

    def _log_search_cache_set(self, key, data):
        if not hasattr(self, "_log_search_cache"):
            self._log_search_cache = {}
        self._log_search_cache[key] = {"_ts": datetime.now(), "data": data}

    @tool(readonly=True, write=False, category="system", system=True, name="system_search_logs")
    def search_logs(self, keyword: str, days: int = 7) -> dict:
        """
        搜索日志和体检记录（v2 性能优化版）
        - 主源：结构化机读日志 丹房/.meta/oplog.jsonl（按 t 时间戳过滤 + 关键字匹配）
        - 进程内结果缓存（TTL 120s）：重复查询零重扫，避免同一会话连发时反复全量 IO
        - 输出归一化：所有结果统一带 content 字段，修复原版体检命中在 knowledge_search
          追加段被静默丢弃的问题（原版体检结果只给 matches/snippets，_append_log_results 取不到）
        """
        vault = os.environ.get("LINGTAI_VAULT", r".")
        keyword_lower = keyword.lower().strip()
        cache_key = (keyword_lower, days)

        # ── 缓存命中：直接返回，零 IO ──
        cached = self._log_search_cache_get(cache_key)
        if cached is not None:
            return cached

        from datetime import timedelta
        results = []
        cutoff = datetime.now() - timedelta(days=days)

        # ── 主源：结构化 oplog.jsonl（行级 + 时间戳过滤，无需全文读入内存）──
        oplog_path = os.path.join(vault, "丹房", ".meta", "oplog.jsonl")
        used_oplog = False
        if os.path.isfile(oplog_path):
            used_oplog = True
            try:
                with open(oplog_path, "r", encoding="utf-8", errors="ignore") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            entry = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        # 时间戳过滤（t 含 +08:00，剥离后按本地 naive 比较）
                        t_str = entry.get("t", "")
                        try:
                            ts = datetime.fromisoformat(t_str)
                            if ts.tzinfo is not None:
                                ts = ts.replace(tzinfo=None)
                            if ts < cutoff:
                                continue
                        except (ValueError, TypeError):
                            pass  # 解析失败则不过滤，仍参与关键字匹配
                        # 关键字匹配（summary/type/op/links 联合）
                        haystack = " ".join([
                            str(entry.get("summary", "")),
                            str(entry.get("type", "")),
                            str(entry.get("op", "")),
                            " ".join(entry.get("links", [])),
                        ]).lower()
                        if keyword_lower in haystack:
                            results.append({
                                "source": "丹房/.meta/oplog.jsonl",
                                "content": str(entry.get("summary", ""))[:200],
                                "t": t_str,
                            })
            except (OSError, UnicodeDecodeError) as e:
                results.append({"source": "丹房/.meta/oplog.jsonl", "error": f"读取失败: {e}"})

        # ── 体检/ 目录（mtime 窗口过滤 + 关键字匹配）──
        exam_dir = os.path.join(vault, "体检")
        if os.path.isdir(exam_dir):
            for fname in os.listdir(exam_dir):
                fpath = os.path.join(exam_dir, fname)
                if not fname.endswith((".md", ".json", ".html")) or not os.path.isfile(fpath):
                    continue
                try:
                    mtime = os.path.getmtime(fpath)
                    file_date = datetime.fromtimestamp(mtime)
                    if (datetime.now() - file_date) > timedelta(days=days):
                        continue
                except OSError:
                    pass
                try:
                    with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
                        content = f.read()
                    if keyword_lower in content.lower():
                        lines = content.split("\n")
                        matching_lines = [l.strip()[:150] for l in lines if keyword_lower in l.lower()]
                        snippet = " ⏎ ".join(matching_lines[:3])
                        results.append({
                            "source": f"体检/{fname}",
                            "content": snippet[:200],
                            "matches": len(matching_lines),
                        })
                except (OSError, UnicodeDecodeError):
                    pass

        data = {
            "keyword": keyword,
            "days": days,
            "total_matches": len(results),
            "results": results[:20],
            "source": "oplog",
            "note": "从 丹房/.meta/oplog.jsonl（机读版）+ 体检/ 检索",
        }
        self._log_search_cache_set(cache_key, data)
        return data

    @tool(readonly=False, write=True, category="system", system=True, name="system_token")
    def token(self, period: str = "today") -> dict:
        """
        查询 Token 消耗
        """
        from pathlib import Path
        _sys_path = list(sys.path)
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        from token_monitor import TokenMonitor
        sys.path = _sys_path
        
        monitor = TokenMonitor()
        
        if period == "today":
            savings = monitor.get_savings()
            return {
                "period": "today",
                "consumed": savings["today"]["consumed"],
                "saved": savings["today"]["saved"],
                "cost": savings["today"]["cost"],
                "saved_cost": savings["today"]["saved_cost"],
                "save_rate": round(savings["today"]["saved"] / max(savings["today"]["consumed"], 1) * 100, 1),
            }
        elif period == "week":
            trend = monitor.get_trend_analysis(days=7)
            return {
                "period": "week",
                "avg_daily_tokens": trend["avg_daily_tokens"],
                "avg_daily_cost": trend["avg_daily_cost"],
                "avg_daily_saved": trend["avg_daily_saved"],
                "total_tokens": trend["total_tokens"],
                "total_cost": trend["total_cost"],
                "trend": trend["trend"],
            }
        elif period == "month":
            trend = monitor.get_trend_analysis(days=30)
            return {
                "period": "month",
                "avg_daily_tokens": trend["avg_daily_tokens"],
                "avg_daily_cost": trend["avg_daily_cost"],
                "avg_daily_saved": trend["avg_daily_saved"],
                "total_tokens": trend["total_tokens"],
                "total_cost": trend["total_cost"],
                "trend": trend["trend"],
            }
        else:
            savings = monitor.get_savings()
            return {
                "period": "all",
                "total_consumed": savings["total"]["consumed"],
                "total_saved": savings["total"]["saved"],
                "total_cost": savings["total"]["cost"],
                "total_saved_cost": savings["total"]["saved_cost"],
            }

    @tool(readonly=True, write=False, category="health", system=False)
    def health_inspect(self) -> dict:
        """
        全量体检汇总——一次调用看知识库全貌。聚合知识缺口、热度图、规则监控、反思五检、对账面板。
        场景：需要知识库全景健康报告时；收尾时主动发现问题时（规则⑳要求）。
        区别：只看单一维度用对应子工具（knowledge_gaps/knowledge_heatmap/lifecycle_scan）；标记缺口状态用 health_ledger。
        v2 优化：自结果缓存——同一 MCP 进程内 60 秒内复用缓存。
        """
        from datetime import datetime, timedelta
        import json, os
        from pathlib import Path

        # ────── 缓存检查（60s 内复用） ──────
        cache_key = "_health_inspect_cache"
        if hasattr(self, cache_key):
            cached = self.__dict__[cache_key]
            elapsed = (datetime.now() - cached["_ts"]).total_seconds()
            if elapsed < 60:
                return cached["data"]

        # 1. 知识缺口
        gaps = self.gaps()
        gap_list = gaps.get("gaps", [])
        gap_count = len(gap_list)

        # 2. 热度图 top 5
        heatmap = self.heatmap(top_n=5)
        hot_pages = heatmap.get("pages", [])

        # 3. 规则监控（summary）
        try:
            rule_health = self.perception_stats_monitor.get_summary()
        except Exception:
            rule_health = {"error": "规则监控不可用"}

        # 4. 读取对账.md
        ledger_path = Path(self.vault_path) / "体检" / "系统" / "对账.md"
        ledger_active = 0
        ledger_resolved = 0
        if ledger_path.exists():
            text = ledger_path.read_text(encoding="utf-8")
            ledger_active = text.count("推迟") + text.count("🔴 待处理")
            ledger_resolved = text.count("已修")

        # 5. 孤立页（入链=0且出链=0的页面）
        isolated = self.memory.get_isolated_pages() if hasattr(self.memory, "get_isolated_pages") else []

        # 6. 冷页（低活跃 + 低引用）
        cold_pages = []
        try:
            cold_pages = self.memory.get_cold_pages(days=30, min_backlinks=3, max_results=5)
        except Exception:
            log.debug("suppressed", exc_info=True)

        # 7. 组件边界审计（P3-2）
        arch_health = {}
        try:
            from dependency_auditor import audit
            arch_health = audit()
        except Exception:
            arch_health = {"summary": {"score": "SKIP", "note": "审计模块不可用"}}

        # 8. 工具延迟报告
        tool_latency_report = {}
        try:
            if hasattr(self, 'tool_latency') and self.tool_latency:
                tool_latency_report = self.tool_latency.get_report(days=7)
        except Exception:
            tool_latency_report = {"status": "error"}

        data = {
            "timestamp": datetime.now().isoformat(),
            "summary": {
                "knowledge_gaps": gap_count,
                "ledger_active": ledger_active,
                "ledger_resolved": ledger_resolved,
                "hot_pages": len(hot_pages),
                "isolated_pages": len(isolated),
                "rule_health": rule_health.get("health_status", "unknown") if isinstance(rule_health, dict) else "unknown",
                "cold_page_count": len(cold_pages),
                "arch_score": arch_health.get("summary", {}).get("score", "SKIP"),
                "tool_latency": tool_latency_report.get("status", "unknown"),
            },
            "gaps": gap_list[:5],
            "hot_pages": hot_pages,
            "isolated_pages": [p.get("path", "") for p in isolated[:5]],
            "cold_pages": cold_pages,
            "arch_health": arch_health.get("summary", {}),
            "rule_health": rule_health,
            "tool_latency": tool_latency_report,
            "note": "全量体检汇总。单独看详情请调对应的 health_* 工具。",
        }

        # 综合健康评分（P3-3）
        score = 100
        score -= min(gap_count * 5, 30)              # 每个缺口-5，上限-30
        score -= min(ledger_active * 10, 30)           # 每个待处理-10，上限-30
        score -= min(len(isolated) * 5, 20)           # 每个孤立页-5，上限-20
        score -= min(len(cold_pages) * 5, 15)         # 每个冷页-5，上限-15
        if arch_health.get("summary", {}).get("score") != "PASS":
            score -= 20
        score = max(score, 0)
        if score >= 90:
            grade = "A"
        elif score >= 75:
            grade = "B"
        elif score >= 50:
            grade = "C"
        else:
            grade = "D"
        data["summary"]["overall_score"] = f"{score}/100 ({grade})"

        # M8: 吸收 system_health + system_check_status
        try:
            import os as _os
            raw_dir = _os.path.join(self.vault_path, '原料')
            raw_count = 0; pending_raw = 0
            if _os.path.isdir(raw_dir):
                for root, dirs, files in _os.walk(raw_dir):
                    for f in files:
                        if not f.endswith('.md'):
                            continue
                        raw_count += 1
                        try:
                            with open(_os.path.join(root, f), 'r', encoding='utf-8') as fh:
                                fc = fh.read(500)
                                if '处理状态' not in fc or '已提炼' not in fc:
                                    pending_raw += 1
                        except Exception:
                            log.debug("suppressed", exc_info=True)
            data["system_health"] = {"total_raw": raw_count, "pending_raw": pending_raw}
        except Exception:
            data["system_health"] = {"error": "不可用"}
        try:
            import subprocess
            repo = os.path.dirname(self.vault_path)
            result = subprocess.run(['git', 'status', '--short'], capture_output=True, text=True, cwd=repo, encoding='utf-8', errors='ignore')
            dirty = result.stdout.strip()
            data["git_status"] = {"has_changes": bool(dirty), "changes": dirty.split('\\n') if dirty else []}
        except Exception:
            data["git_status"] = {"has_changes": False, "error": "git 不可用"}

        # 记忆冲突扫描
        try:
            conflicts = self.memory_bank.scan_conflicts()
            data["memory_conflicts"] = {
                "count": conflicts.get("count", 0),
                "conflicts": [
                    {
                        "a": c.get("a", {}).get("content", "")[:60],
                        "b": c.get("b", {}).get("content", "")[:60],
                        "reason": c.get("reason", ""),
                    }
                    for c in conflicts.get("conflicts", [])[:5]
                ],
            }
        except Exception:
            data["memory_conflicts"] = {"count": 0, "conflicts": []}

        # 召回效能审计（P0：接通 audit.py 已设计的 query_hit/query_miss）
        try:
            _audit = self.memory_bank.audit_log
            _stats = _audit.get_stats(days=7)
            _by_action = _stats.get("by_action", {})
            _hit_count = _by_action.get("query_hit", 0)
            _miss_count = _by_action.get("query_miss", 0)
            _total_queries = _hit_count + _miss_count
            _empty_rate = round(_miss_count / max(_total_queries, 1) * 100, 1) if _total_queries > 0 else 0.0
            # 被引用最多的记忆（召回最多的 Top 3）
            _ref_entries = _audit.get_entries(action="query_hit", days=7)
            from collections import Counter as _Counter
            _hit_ids = _Counter(e.get("memory_id", "") for e in _ref_entries if e.get("memory_id"))
            _top_recalled = [{"id": mid, "hit_count": cnt} for mid, cnt in _hit_ids.most_common(3)]
            data["recall_efficacy"] = {
                "total_queries_7d": _total_queries,
                "hit_count": _hit_count,
                "miss_count": _miss_count,
                "empty_recall_rate": _empty_rate,
                "alert": _empty_rate > 50 and _total_queries >= 3,
                "top_recalled": _top_recalled,
            }
        except Exception:
            data["recall_efficacy"] = {"error": "审计不可用"}

        # Q3: 质量板块——读取基准历史计算趋势
        import json as _json, os as _os
        _quality = {}
        _bm_path = _os.path.join(_os.path.dirname(__file__), '..', 'logs', 'quality_benchmark.jsonl')
        if _os.path.exists(_bm_path):
            try:
                _scores = []
                with open(_bm_path, 'r', encoding='utf-8') as _f:
                    for _line in _f:
                        _line = _line.strip()
                        if _line:
                            try:
                                _entry = _json.loads(_line)
                                if "score" in _entry:
                                    _scores.append(_entry["score"])
                            except _json.JSONDecodeError:
                                pass
                if _scores:
                    _quality["retrieval"] = {
                        "current": _scores[-1],
                        "trend": "↑" if len(_scores) >= 2 and _scores[-1] > _scores[-2]
                                 else "↓" if len(_scores) >= 2 and _scores[-1] < _scores[-2]
                                 else "→",
                        "history": _scores[-10:],
                    }
            except Exception:
                log.debug("suppressed", exc_info=True)
        if _quality:
            data["quality"] = _quality

        # 写质量快照日志
        _qlog = _os.path.join(_os.path.dirname(__file__), '..', 'logs', 'quality_snapshots.jsonl')
        try:
            _os.makedirs(_os.path.dirname(_qlog), exist_ok=True)
            with open(_qlog, 'a', encoding='utf-8') as _f:
                _snap = {
                    "timestamp": datetime.now().isoformat(),
                    "gaps": data.get("summary", {}).get("knowledge_gaps", -1),
                    "isolated": data.get("summary", {}).get("isolated_pages", -1),
                    "retrieval_score": _quality.get("retrieval", {}).get("current", -1),
                }
                _f.write(_json.dumps(_snap, ensure_ascii=False) + "\n")
        except Exception:
            log.debug("suppressed", exc_info=True)

        # ── 域间密度对比（方向二：缺口驱动推荐）──
        # 扫描丹房各域页面数，检测密度不均，主动建议补薄弱域
        try:
            import os as _os2, glob as _glob2
            danfang_dir = _os2.path.join(self.vault_path, '丹房')
            domain_counts = {}
            if _os2.path.isdir(danfang_dir):
                for entry in _os2.listdir(danfang_dir):
                    domain_path = _os2.path.join(danfang_dir, entry)
                    if _os2.path.isdir(domain_path):
                        md_count = len(_glob2.glob(_os2.path.join(domain_path, '*.md')))
                        if md_count > 0:
                            domain_counts[entry] = md_count
            if domain_counts:
                sorted_domains = sorted(domain_counts.items(), key=lambda x: -x[1])
                thickest_domain, thickest_count = sorted_domains[0]
                thinnest_domain, thinnest_count = sorted_domains[-1]
                ratio = thickest_count / max(thinnest_count, 1)
                # 触发条件：最厚/最薄 >= 5 倍，或最薄域 <= 5 页
                trigger = ratio >= 5 or thinnest_count <= 5
                _DOMAIN_LABELS = {
                    "00-思考与认知": "认知框架",
                    "01-内容创作": "内容创作",
                    "02-成长与日常": "成长日常",
                    "03-社会观察": "社会观察",
                    "04-身体与健康": "身体健康",
                    "05-哲学与思想": "哲学思想",
                    "06-商业与投资": "商业投资",
                    "07-工具与AI": "工具与AI",
                    "08-教育": "教育",
                    "99-一人公司": "一人公司",
                }
                thin_label = _DOMAIN_LABELS.get(thinnest_domain, thinnest_domain)
                thick_label = _DOMAIN_LABELS.get(thickest_domain, thickest_domain)
                suggestion = None
                if trigger:
                    suggestion = (
                        f"知识密度不均：「{thick_label}」域 {thickest_count} 页 vs "
                        f"「{thin_label}」域 {thinnest_count} 页（{ratio:.1f}倍）。"
                        f"如果要做变现/输出，「{thin_label}」可能是短板——值得补一补。"
                    )
                data["domain_balance"] = {
                    "thickest": {"domain": thickest_domain, "count": thickest_count},
                    "thinnest": {"domain": thinnest_domain, "count": thinnest_count},
                    "ratio": round(ratio, 1),
                    "trigger": trigger,
                    "suggestion": suggestion,
                    "all_domains": dict(sorted_domains),
                }
        except Exception:
            log.debug("suppressed", exc_info=True)

        self.__dict__["_health_inspect_cache"] = {"_ts": datetime.now(), "data": data}
        return data

    @tool(readonly=False, write=True, category="health", system=False)
    def health_ledger(self, action: str = "read", gap: str = "", status: str = "") -> dict:
        """
        对账面板操作。读取或标记缺口状态。
        
        Args:
            action: read（读对账）| close（关闭缺口）
            gap: 要关闭的缺口名称（action=close时必填）
            status: 关闭状态（已修/跳过/推迟），默认"已修"
        """
        from pathlib import Path
        from datetime import datetime
        import re

        ledger_path = Path(self.vault_path) / "体检" / "系统" / "对账.md"
        
        if not ledger_path.exists():
            return {"error": "对账.md 不存在", "path": str(ledger_path)}

        if action == "read":
            text = ledger_path.read_text(encoding="utf-8")
            # 解析缺口
            active_gaps = []
            resolved_gaps = []
            in_active = False
            in_resolved = False
            
            for line in text.split("\n"):
                stripped = line.strip()
                if "🔴 待处理" in stripped:
                    in_active = True
                    in_resolved = False
                elif "✅ 已消解" in stripped:
                    in_resolved = True
                    in_active = False
                elif stripped.startswith("---") or stripped.startswith("##") or stripped.startswith("#"):
                    in_active = False
                    in_resolved = False
                
                # 解析表格行
                if in_active and "|" in stripped and "--" not in stripped:
                    parts = [p.strip() for p in stripped.split("|")]
                    if len(parts) >= 4 and parts[1] and parts[1] not in ("缺口", ""):
                        active_gaps.append({"gap": parts[1], "source": parts[2], "status": parts[3]})
                elif in_resolved and "|" in stripped and "--" not in stripped:
                    parts = [p.strip() for p in stripped.split("|")]
                    if len(parts) >= 4 and parts[1] and parts[1] not in ("缺口", ""):
                        resolved_gaps.append({"gap": parts[1], "source": parts[2], "status": parts[3]})

            return {
                "action": "read",
                "active_gaps": active_gaps,
                "resolved_gaps": resolved_gaps,
                "active_count": len(active_gaps),
                "resolved_count": len(resolved_gaps),
                "note": "标记缺口关闭用 action=close&gap=缺口名称&status=已修|跳过|推迟",
            }

        elif action == "close":
            if not gap:
                return {"error": "必须提供 gap 名称"}
            if not status:
                status = "已修"
            today = datetime.now().strftime("%m-%d")

            text = ledger_path.read_text(encoding="utf-8")
            # 在 🔴 待处理区找到匹配缺口行，移到 ✅ 已消解区
            lines = text.split("\n")
            
            moved = False
            new_lines = []
            in_pending = False
            in_resolved = False
            pending_end = 0
            resolved_end = 0
            
            for i, line in enumerate(lines):
                if "🔴 待处理" in line:
                    in_pending = True
                    in_resolved = False
                elif "✅ 已消解" in line:
                    in_resolved = True
                    in_pending = False
                elif line.strip().startswith("---") and in_pending and not pending_end:
                    pending_end = i
                
                # 在待处理区找匹配行
                if in_pending and not moved and "|" in line and "--" not in line:
                    parts = [p.strip() for p in line.split("|")]
                    if len(parts) >= 2 and parts[1] == gap:
                        # 改成已修，移到后面
                        new_line = f"| {gap} | {parts[2]} | {status} {today} |"
                        new_lines.append(new_line)
                        moved = True
                        continue  # 跳过原行
                
                new_lines.append(line)
            
            if moved:
                # 把已修行追加到 ✅ 已消解 后面
                result_lines = []
                inserted = False
                for line in new_lines:
                    result_lines.append(line)
                    if "✅ 已消解" in line and not inserted:
                        # 找到已消解区末尾，追加新行
                        inserted = True
                
                # 在已消解表格的末尾插入
                result_text = "\n".join(result_lines)
                # 在第一个 --- 分隔符前插入
                parts = result_text.split("---", 1)
                resolved_section = parts[0]
                rest = parts[1] if len(parts) > 1 else ""
                
                # 在已消解表格中追加一行
                # 找已消解表格的当前最后一行
                resolved_lines = resolved_section.split("\n")
                # 在最后一行前插入
                # 找到表格最后一行的索引
                last_table_row = len(resolved_lines) - 1
                for i in range(len(resolved_lines) - 1, -1, -1):
                    if "|" in resolved_lines[i] and "--" not in resolved_lines[i] and "缺口" not in resolved_lines[i]:
                        last_table_row = i
                        break
                resolved_lines.insert(last_table_row + 1, f"| {gap} | — | {status} {today} |")
                resolved_section = "\n".join(resolved_lines)
                result_text = resolved_section + ("---" + rest if rest else "")

                ledger_path.write_text(result_text, encoding="utf-8")
                return {"action": "close", "gap": gap, "status": status, "result": "缺口已关闭"}
            else:
                return {"action": "close", "gap": gap, "error": "未找到匹配缺口"}

        return {"error": f"未知操作: {action}"}

    @tool(readonly=True, write=False, category="system", system=False, name="session_broker_status")
    def session_broker_status(self) -> dict:
        """查询当前活跃会话——哪些桌面端（Reasonix/WorkBuddy等）正在连接灵台 MCP。
        
        返回当前所有活跃/过期会话列表，按端聚合统计，附带最近变更事件。
        场景：多端并行时灵识想知道"我现在不是一个人在干活"。
        """
        try:
            from session_tracker import _broker, _event_bus
            result = _broker.status()
            result["recent_events"] = _event_bus.recent(10)
            return result
        except Exception as e:
            return {"error": str(e), "active_sessions": 0, "sessions": []}

    @tool(readonly=True, write=False, category="system", system=False, name="event_bus_poll")
    def event_bus_poll(self, since: str = "", client_filter: str = "", max_events: int = 20) -> dict:
        """拉取变更事件——查看其他端最近做了什么操作。

        Args:
            since: ISO 时间戳，只返回该时间之后的事件（空=返回全部）
            client_filter: 只返回指定客户端的事件（空=不过滤）
            max_events: 最大返回条数（默认 20）

        Returns:
            dict: 事件列表 + 统计
        """
        try:
            from session_tracker import _event_bus
            events = _event_bus.poll(since=since, client_filter=client_filter)
            if len(events) > max_events:
                events = events[-max_events:]
            return {
                "total_events": len(events),
                "events": events,
                "note": "事件保留最近 100 条，环形缓冲区",
            }
        except Exception as e:
            return {"error": str(e), "total_events": 0, "events": []}

    @tool(readonly=False, write=True, category="system", system=False, name="lease_acquire")
    def lease_acquire(self, resource: str, duration: int = 30, force: bool = False) -> dict:
        """获取排他性资源租约——确保同一时间只有一个端操作排他资源。

        Args:
            resource: 资源标识（如 "page:丹房/00-思考与认知/含人量"）
            duration: 租约时长（秒，默认 30，最大 300）
            force: 是否强制获取（会释放已有租约，默认 False）

        Returns:
            dict: 成功/失败 + 租约信息
        """
        from session_tracker import _lease_manager
        client = getattr(self, 'client', 'unknown')
        duration = min(max(duration, 5), 300)
        return _lease_manager.acquire(resource, client, duration=duration, force=force)

    @tool(readonly=False, write=True, category="system", system=False, name="lease_release")
    def lease_release(self, resource: str) -> dict:
        """释放排他性资源租约。

        Args:
            resource: 资源标识（与 acquire 时一致）
        """
        from session_tracker import _lease_manager
        client = getattr(self, 'client', 'unknown')
        return _lease_manager.release(resource, client=client)

    @tool(readonly=True, write=False, category="system", system=False, name="lease_status")
    def lease_status(self, resource: str = "") -> dict:
        """查询排他性资源租约状态。

        Args:
            resource: 资源标识（空=返回全部活跃租约）
        """
        from session_tracker import _lease_manager
        return _lease_manager.status(resource=resource)

    @tool(readonly=False, write=True, category="system", system=True, name="system_restart")
    def restart(self) -> dict:
        """
        热重启——退出当前 MCP 进程，Reasonix 检测到进程退出后自动重启。
        重启后新工具（如 health_*）才会注册到客户端。
        """
        import sys, os
        # 先 flush 会话日志缓冲：os._exit 会跳过 atexit，否则最后一段（<300s 窗口内）
        # 工具调用记录在热重启时静默丢失
        try:
            from .shared import get_session_logger
            get_session_logger()._flush()
        except Exception:
            log.debug("suppressed", exc_info=True)
        # 刷新 stdout/stderr 确保响应发送
        sys.stdout.flush()
        sys.stderr.flush()
        # 给客户端时间读到响应后再退出
        import time
        time.sleep(0.5)
        os._exit(0)

    def quality_check_single(self, keyword: str, hops: int = 2) -> dict:
        """单次检索质量检测（被 knowledge_quality_check 和 sys_reload 共用）

        Args:
            keyword: 查询词
            hops: 图扩散跳数

        Returns:
            dict: {"keyword", "hit_count", "expected_found", "expected_pages", "latency_ms"}
        """
        import time, json, os
        queries_path = os.path.join(os.path.dirname(__file__), '..', 'benchmark_queries.json')
        if not os.path.exists(queries_path):
            return {"error": "benchmark_queries.json not found"}
        with open(queries_path, 'r', encoding='utf-8') as f:
            all_queries = json.load(f)

        start = time.time()
        search_result = self.query(keyword=keyword, hops=hops)
        elapsed = round((time.time() - start) * 1000, 2)

        # 取结果
        hits = search_result.get("results", []) + search_result.get("related", [])

        # 查找匹配的预期页
        matched = [q for q in all_queries if q["query"] == keyword]
        expected_pages = matched[0]["expected_pages"] if matched else []

        found_pages = []
        for ep in expected_pages:
            found = False
            for h in hits:
                h_path = h.get("path", "")
                if ep in h_path or ep.split("/")[-1] in h_path:
                    found = True
                    break
            found_pages.append({"page": ep, "found": found})

        return {
            "keyword": keyword,
            "hit_count": len(hits),
            "expected_total": len(expected_pages),
            "expected_found": sum(1 for f in found_pages if f["found"]),
            "expected_pages": found_pages,
            "latency_ms": elapsed,
        }

    def _check_argument_depth(self) -> dict:
        """论证深度检查——扫描丹房页的多角度论证指标，对齐规则 20。

        检测维度：
        1. 多视角结构：≥2 个 ## 子标题（不同角度展开）
        2. 局限/冲突标记：包含「局限」「冲突」「矛盾」「反驳」等自省段落
        3. 补角模式：包含「补角」标记（灵台特有增量论证模式）
        4. 对比表格：包含 | 表格结构（多维度对比）
        5. 警告框：包含 [!warning] 或 [!conflict] 标注

        Returns:
            dict: {depth_score, total_pages, passed, failed, details, stats}
        """
        import re, os
        from datetime import datetime

        vault = getattr(self, 'vault_path', None) or r"."
        danfang_dir = os.path.join(vault, "丹房")
        if not os.path.isdir(danfang_dir):
            return {"error": "丹房目录不存在", "depth_score": 0}

        # 收集所有丹房页
        pages = []
        for root, dirs, files in os.walk(danfang_dir):
            dirs[:] = [d for d in dirs if not d.startswith('.')]
            for f in files:
                if f.endswith('.md'):
                    pages.append(os.path.join(root, f))

        if not pages:
            return {"error": "丹房无页面", "depth_score": 0}

        # 抽样策略：按品级分层，每层最多 30 页
        pinji_samples = {"上品": [], "中品": [], "下品": [], "": []}
        for p in pages:
            try:
                with open(p, 'r', encoding='utf-8-sig') as fp:
                    head = ''.join(fp.readline() for _ in range(8))
            except Exception:
                continue
            pm = re.search(r'品级:\s*(\S+)', head)
            level = pm.group(1) if pm else ""
            if len(pinji_samples.get(level, [])) < 30:
                pinji_samples.setdefault(level, []).append(p)

        sampled = []
        for level_pages in pinji_samples.values():
            sampled.extend(level_pages)

        # 指标检测
        stats = {"total_sampled": len(sampled), "multi_section": 0, "has_limitation": 0,
                 "has_bujiao": 0, "has_table": 0, "has_warning": 0}
        details = []

        SECTION_RE = re.compile(r'^##\s+', re.MULTILINE)
        LIMIT_RE = re.compile(r'(局限|冲突|矛盾|反驳|争议|不足|缺陷|短板|反面)')
        BUJIAO_RE = re.compile(r'补角')
        TABLE_RE = re.compile(r'\|.*\|.*\|')
        WARNING_RE = re.compile(r'\[!warning\]|\[!conflict\]|\[!矛盾\]')

        for path in sampled:
            try:
                with open(path, 'r', encoding='utf-8-sig') as fp:
                    content = fp.read()
            except Exception:
                continue

            h2_count = len(SECTION_RE.findall(content))
            has_lim = bool(LIMIT_RE.search(content))
            has_bj = bool(BUJIAO_RE.search(content))
            has_tb = bool(TABLE_RE.search(content))
            has_warn = bool(WARNING_RE.search(content))

            if h2_count >= 2:
                stats["multi_section"] += 1
            if has_lim:
                stats["has_limitation"] += 1
            if has_bj:
                stats["has_bujiao"] += 1
            if has_tb:
                stats["has_table"] += 1
            if has_warn:
                stats["has_warning"] += 1

            # 综合评分：每个维度 1 分，满分 5
            score = sum([1 if h2_count >= 2 else 0, 1 if has_lim else 0,
                         1 if has_bj else 0, 1 if has_tb else 0, 1 if has_warn else 0])

            detail = {
                "path": os.path.relpath(path, vault).replace('\\', '/'),
                "h2_count": h2_count,
                "has_limitation": has_lim,
                "has_bujiao": has_bj,
                "has_table": has_tb,
                "has_warning": has_warn,
                "depth_score": score,
            }
            details.append(detail)

        # 分级统计
        strong = sum(1 for d in details if d["depth_score"] >= 3)  # ≥3 分 = 深度论证
        moderate = sum(1 for d in details if d["depth_score"] == 2)  # 2 分 = 中度
        weak = sum(1 for d in details if d["depth_score"] <= 1)  # ≤1 分 = 浅层

        depth_score = round(strong / len(details), 2) if details else 0.0

        # 找出最弱页面（≤1 分且非日志/索引页）
        weak_pages = [d for d in details if d["depth_score"] <= 1
                      and "日志" not in d["path"] and "索引" not in d["path"] and "README" not in d["path"]]
        weak_pages.sort(key=lambda x: x["depth_score"])

        return {
            "depth_score": depth_score,
            "depth_score_label": f"{depth_score:.0%} 页达到深度论证（≥3/5 维度）",
            "total_sampled": stats["total_sampled"],
            "strong": strong,
            "moderate": moderate,
            "weak": weak,
            "stats": stats,
            "weak_pages": [w["path"] for w in weak_pages[:10]],
            "suggestion": _get_depth_suggestion(depth_score, strong, weak, weak_pages[:5]),
            "check_time": datetime.now().isoformat(timespec="seconds"),
        }

    @tool(readonly=True, write=False, category="health", system=False, name="argument_depth_check")
    def argument_depth_check_tool(self) -> dict:
        """论证深度检查——扫描丹房页的多角度论证指标，对齐规则 20。
        
        检测 5 个维度：多视角结构、局限/冲突标记、补角模式、对比表格、警告框。
        返回深度评分 + 弱页面列表 + 改进建议。
        """
        return self._check_argument_depth()

    @tool(readonly=True, write=False, category="health", system=False, name="knowledge_quality_check")
    def quality_check_tool(self, mode: str = "quick") -> dict:
        """
        检索质量基准检测——用标准查询集验证知识库检索是否退化

        Args:
            mode: "quick"（5 组核心查询）| "full"（全部 10 组）| "depth"（论证深度检查，对齐规则 20）

        Returns:
            dict: {score, passed, failed, details, trend}（depth 模式返回 depth_score 和 depth_details）
        """
        if mode == "depth":
            return self._check_argument_depth()

        import json, os
        queries_path = os.path.join(os.path.dirname(__file__), '..', 'benchmark_queries.json')
        if not os.path.exists(queries_path):
            return {"error": "benchmark_queries.json not found", "score": 0, "passed": 0, "failed": 1}
        with open(queries_path, 'r', encoding='utf-8') as f:
            all_queries = json.load(f)

        # 按 group 筛选
        if mode == "quick":
            queries = [q for q in all_queries if q.get("group") == "core"]
        else:
            queries = all_queries

        details = []
        passed = 0
        failed = 0
        for q in queries:
            result = self.quality_check_single(q["query"])
            if "error" in result:
                failed += 1
                details.append(result)
                continue
            # 没有预期页面的查询：只要有命中就算通过
            if not q["expected_pages"]:
                ok = result["hit_count"] > 0
            else:
                ok = result["expected_found"] >= 1
            if ok:
                passed += 1
            else:
                failed += 1
            details.append(result)

        total = passed + failed
        score = round(passed / total, 2) if total > 0 else 0.0

        # 读历史趋势
        trend = "→"
        logs_path = os.path.join(os.path.dirname(__file__), '..', 'logs', 'quality_benchmark.jsonl')
        if os.path.exists(logs_path):
            try:
                with open(logs_path, 'r', encoding='utf-8') as f:
                    last = None
                    for line in f:
                        line = line.strip()
                        if line:
                            try:
                                last = json.loads(line)
                            except json.JSONDecodeError:
                                pass
                    if last and last.get("score", 0) < score:
                        trend = "↑"
                    elif last and last.get("score", 0) > score:
                        trend = "↓"
            except Exception:
                log.debug("suppressed", exc_info=True)

        # 写当前结果到日志
        import time as _time
        from datetime import datetime
        try:
            os.makedirs(os.path.dirname(logs_path), exist_ok=True)
            with open(logs_path, 'a', encoding='utf-8') as f:
                f.write(json.dumps({
                    "timestamp": datetime.now().isoformat(),
                    "mode": mode,
                    "score": score,
                    "passed": passed,
                    "failed": failed,
                    "total": total,
                }, ensure_ascii=False) + "\n")
        except Exception:
            log.debug("suppressed", exc_info=True)

        return {
            "score": score,
            "passed": passed,
            "failed": failed,
            "total": total,
            "trend": trend,
            "mode": mode,
            "details": details,
        }

    @tool(readonly=False, write=True, category="system", system=False, name="sys_reload")
    def reload(self) -> dict:
        """
        热重载——重新加载 MCP 工具定义和分派表，不中断当前会话。
        改 tools.py 或 server_mixins/ 代码后调用，新工具立即生效。
        相当于热重启的无中断版本。
        """
        import importlib, sys, json
        results = {"modules_reloaded": [], "errors": []}
        
        # 1. 重载 decorators → 刷新 REGISTRY（清空后重新装饰）
        try:
            import decorators
            # 先清空 REGISTRY，让重载后的 mixins 重新注册
            decorators.REGISTRY.clear()
            results["modules_reloaded"].append("decorators (REGISTRY reset)")
        except Exception as e:
            results["errors"].append(f"decorators REGISTRY 重置失败: {str(e)}")

        # 1.5 弹出 topic_gate 缓存并重新加载，让 perception 重新导入
        # （代码变更不触发自动重载）
        try:
            if 'topic_gate' in sys.modules:
                importlib.reload(sys.modules['topic_gate'])
            else:
                import topic_gate
                importlib.reload(topic_gate)
            results["modules_reloaded"].append("topic_gate (reloaded)")
        except Exception as e:
            results["errors"].append(f"topic_gate 重载失败: {str(e)}")

        # 1.6 重载 raw_derive（独立模块，被 perception.py 运行时导入）
        try:
            import raw_derive as _raw_derive_mod
            importlib.reload(_raw_derive_mod)
            results["modules_reloaded"].append("raw_derive")
        except Exception as e:
            results["errors"].append(f"raw_derive 重载失败: {str(e)}")

        # 2. 重载 server_mixins 模块 + 核心引擎 + 降级检测模块（@tool 装饰器会重新注册到 REGISTRY）
        mixins = [
            'server_mixins.knowledge', 'server_mixins.perception',
            'server_mixins.macros', 'server_mixins.system',
            'server_mixins.observation', 'server_mixins.memory_bank',
            'server_mixins.user_profile', 'server_mixins.kar',
            'server_mixins.llm', 'server_mixins.skillopt',
            'server_mixins.check_point', 'server_mixins.output',
            'server_mixins.concept_collision',
            'server_mixins.health_indicators', 'server_mixins.macro_tracker',
            'reasoning_engine', 'llm_reasoning',
            'skillopt.evolve_engine', 'skillopt.pattern_detector',
            'skillopt.rule_candidate', 'skillopt.replay_validator',
            'skillopt.confidence_scorer', 'skillopt.stager',
            'skillopt.probation_monitor',
        ]
        for mod_name in mixins:
            try:
                if mod_name in sys.modules:
                    importlib.reload(sys.modules[mod_name])
                    results["modules_reloaded"].append(mod_name)
            except Exception as e:
                results["errors"].append(f"{mod_name} 重载失败: {str(e)}")

        # 2.5 显式重载核心引擎模块（可能被 module-level import 引用但不在 sys.modules）
        for mod_name in ['degradation', 'memory_engine', 'perception', 'memory_bank.bank', 'skill_router']:
            try:
                if mod_name in sys.modules:
                    importlib.reload(sys.modules[mod_name])
                else:
                    importlib.import_module(mod_name)
                results["modules_reloaded"].append(mod_name)
            except Exception as e:
                results["errors"].append(f"{mod_name} 重载失败: {str(e)}")
        
        # 3. 重载 server.py → 重建 LingtaiMCPServer 方法
        try:
            import server
            importlib.reload(server)
            # 更新当前实例的方法：从新的 server 类复制方法
            NewClass = server.LingtaiMCPServer
            for attr_name in dir(NewClass):
                if attr_name.startswith('_') and attr_name not in ('__init__',):
                    continue
                attr = getattr(NewClass, attr_name)
                if callable(attr) and not attr_name.startswith('__'):
                    setattr(self, attr_name, attr.__get__(self, NewClass))
            results["server_methods_updated"] = True
            results["modules_reloaded"].append("server")
            # 重置惰性加载引擎缓存（让下次访问时用新代码重新创建）
            self._reasoning = None
            self._reflect_engine = None
            # 刷新 PerceptionTools 的 MemoryEngine（实例级别，绕过类重载限制）
            if hasattr(self, 'perception') and hasattr(self.perception, 'memory'):
                try:
                    self.perception.memory.refresh()
                    results["perception_memory_refreshed"] = True
                except Exception as e:
                    results["perception_memory_refresh_error"] = str(e)
            # 清除 context_load 缓存，下次调用重新计算
            self._context_loaded = False
            self._context_cache = None
            self._context_index_mtime = 0.0
            # 强制记忆银行重建实例（加载最新代码 + 数据）
            if hasattr(self, 'memory_bank'):
                try:
                    from memory_bank.bank import MemoryBank as _MB
                    _vault = self.memory_bank.data_dir.parent if hasattr(self.memory_bank, 'data_dir') else self.vault_path
                    # 保留 vault_path 和 registry，重建实例
                    _old_vault = getattr(self.memory_bank, 'vault_path', self.vault_path)
                    _old_reg = getattr(self.memory_bank, 'registry', None)
                    self.memory_bank = _MB(_old_vault, registry=_old_reg)
                    # 恢复 client 标识
                    _client = getattr(self, 'client', '') or getattr(self, 'client_version', '')
                    if _client and _client != 'unknown':
                        self.memory_bank.set_client(_client)
                    results["memory_bank_reloaded"] = True
                    results["memory_bank_count"] = len(self.memory_bank.memories)
                except Exception as e:
                    results["memory_bank_reload_error"] = str(e)
            # 重置 skillopt 引擎（直接重建，绕过 _ensure_skillopt 的 _ 方法不复制问题）
            try:
                import importlib
                import sys as _sys
                # 确保技能进化模块已加载
                if "skillopt.evolve_engine" in _sys.modules:
                    importlib.reload(_sys.modules["skillopt.evolve_engine"])
                if "skillopt.stager" in _sys.modules:
                    importlib.reload(_sys.modules["skillopt.stager"])
                from skillopt.evolve_engine import EvolveEngine
                from skillopt.stager import Stager
                self.skillopt_engine = EvolveEngine(self.vault_path)
                self.skillopt_stager = Stager()
                self._skillopt_loaded = True
                results["skillopt_reset"] = True
            except Exception as e2:
                results["skillopt_reset_error"] = str(e2)
            results["engines_reset"] = True
        except Exception as e:
            results["errors"].append(f"server 重载失败: {str(e)}")
        
        # 4. 重建 router._TOOL_MAP（只更新 lambda 指向新方法的别名）
        try:
            import router as rmod
            # 重新读取 router 的 _TOOL_MAP 定义（需要重新加载 router）
            importlib.reload(rmod)
            # 重载后 router 会新建一个 LingtaiMCPServer 实例，将它指回当前实例
            rmod.server = self
            results["router_updated"] = True
        except Exception as e:
            results["errors"].append(f"router 重载失败: {str(e)}")
        
        # 5. 刷新观察引擎 pending（从已清空的磁盘文件重新加载）
        try:
            if hasattr(self, 'observation'):
                self.observation.pending = self.observation._load_pending()
                results["observation_pending_reloaded"] = True
        except Exception as e:
            results["errors"].append(f"observation pending 刷新失败: {str(e)}")
        
        results["status"] = "ok" if not results["errors"] else "partial"
        results["message"] = f"热重载完成：{len(results['modules_reloaded'])} 模块已更新" +             (f"，{len(results['errors'])} 个错误" if results['errors'] else "")

        # Q1: 热重载后自动跑快速检索质量基准（不阻断重载）
        try:
            qc = self.quality_check_tool(mode="quick")
            results["quality_check"] = {
                "score": qc.get("score", 0),
                "passed": qc.get("passed", 0),
                "failed": qc.get("failed", 0),
            }
        except Exception as e:
            results["quality_check"] = {"error": str(e)}

        return results

    def recommend_resources(self, topic: str = None) -> dict:
        """
        知识缺口推荐：检测缺口 + Tavily搜索外部资源
        """
        # 1. 检测知识缺口 — 修复: observation_engine → observation
        if topic:
            reflect_result = self.observation.reflect_topic(topic) if hasattr(self.observation, 'reflect_topic') else {"findings": []}
        else:
            reflect_result = {"findings": []}

        # 2. 从原料中提取未提炼的主题
        raw_dir = os.path.join(VAULT_PATH, "原料")
        pending_topics = []
        if os.path.isdir(raw_dir):
            for f in os.listdir(raw_dir):
                if f.endswith('.md'):
                    path = os.path.join(raw_dir, f)
                    try:
                        with open(path, 'r', encoding='utf-8', errors='ignore') as fp:
                            content = fp.read(200)
                        if '处理状态: 待提炼' in content:
                            pending_topics.append(f.replace('.md', ''))
                    except Exception:
                        log.debug("suppressed", exc_info=True)

        # 3. 选取前3个待提炼主题，用Tavily搜索推荐
        recommendations = []
        search_topics = pending_topics[:3] if pending_topics else []
        if topic:
            search_topics = [topic]

        for t in search_topics:
            tavily_result = self.tavily_search(t, max_results=3)
            results = tavily_result.get("results", [])
            recommendations.append({
                "topic": t,
                "external_resources": [{"title": r.get("title", ""), "url": r.get("url", "")} for r in results],
            })

        return {
            "pending_count": len(pending_topics),
            "pending_topics": pending_topics[:10],
            "recommendations": recommendations,
        }

    def deep_analysis(self, topic: str) -> dict:
        """
        横纵分析法深度研究（借鉴hv-analysis）
        """
        # 1. 灵识内部：查询相关知识
        qr = self.memory.query(topic)
        internal = qr.get("results", [])

        # 2. 灵识内部：图扩散
        graph = self.memory.search_graph(topic, hops=2, weighted=True)

        # 3. 灵识内部：相关页面
        related = self.memory.get_related_pages(topic, max_results=10)

        # 4. 外部搜索：Tavily
        external = self.tavily_search(topic, max_results=5)

        return {
            "topic": topic,
            "framework": {
                "纵向": "纵轴：追踪研究对象从诞生到当下的完整生命历程",
                "横向": "横轴：在当下时间截面上与竞品/同类进行系统性横向对比",
                "交汇": "交叉两条轴产出独到洞察",
            },
            "internal_knowledge": {
                "direct_matches": len(internal),
                "top_pages": [{"title": p.get("title", ""), "path": p.get("path", "")} for p in internal[:5]],
                "graph_nodes": len(graph),
                "related_pages": [p.get("title", "") for p in related[:5]],
            },
            "external_suggestion": {
                "tavily_results": [{"title": r.get("title", ""), "url": r.get("url", "")} for r in external.get("results", [])],
                "note": "灵识提供框架和内部数据，外部搜索结果供进一步研究",
            },
            "report_template": [
                "一、一句话定义",
                "二、纵向分析（6000-15000字）：起源→诞生→演进→决策逻辑→阶段划分",
                "三、横向分析（3000-10000字）：竞品识别→核心差异→用户口碑→生态位→趋势",
                "四、横纵交汇洞察（1500-3000字）：历史塑造当下→优势/劣势根源→三个未来剧本",
                "五、信息来源",
            ],
        }

    @tool(readonly=False, write=True, category="system", system=True, name="system_check_status")
    def check_status(self) -> dict:
        """
        检查外部变更状态（git status + 最近10条操作记录）
        """
        import subprocess, json
        vault = VAULT_PATH
        repo = os.path.dirname(vault)
        
        result = subprocess.run(
            ['git', 'status', '--short'],
            capture_output=True, text=True, cwd=repo, encoding='utf-8', errors='ignore'
        )
        dirty = result.stdout.strip()
        
        # 读 oplog 获取近期操作（人类版日志.md 已退役）
        recent_ops = []
        oplog_path = os.path.join(vault, '丹房', '.meta', 'oplog.jsonl')
        if os.path.isfile(oplog_path):
            try:
                with open(oplog_path, 'r', encoding='utf-8') as f:
                    lines = f.readlines()
                for line in reversed(lines[-30:]):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    summary = entry.get("summary", "")
                    if summary:
                        recent_ops.append(summary)
                        if len(recent_ops) >= 10:
                            break
            except (OSError, UnicodeDecodeError):
                recent_ops = []
        
        return {
            "has_changes": bool(dirty),
            "changes": dirty.split('\n') if dirty else [],
            "recent_operations": recent_ops,
            "tip": "有外部变更时先 `git pull` 或确认变更来源后再操作"
        }

    @tool(readonly=True, write=False, category="system", system=False, name="system_sop")
    def sop(self, tool: str = None) -> dict:
        """
        工具SOP按需披露：返回指定工具的详细使用指南
        """
        from sop import get_sop, list_tools_with_sop
        if tool:
            return get_sop(tool)
        return {"tools_with_sop": list_tools_with_sop(), "count": len(list_tools_with_sop())}

    # ─── 内容注册表 ───

    def registry_lookup(self, content: str) -> dict:
        """查内容注册表：内容是否已在灵台出现过"""
        from content_registry import ContentRegistry
        registry = ContentRegistry(self.vault_path)
        result = registry.lookup(content)
        if result:
            return {"found": True, **result}
        return {"found": False}

    @tool(readonly=False, write=True, category="system", system=True, name="system_registry_scan")
    def registry_scan(self, force: bool = False) -> dict:
        """全量扫描重建内容注册表"""
        from content_registry import ContentRegistry
        registry = ContentRegistry(self.vault_path)
        result = registry.build_from_scan(force=force)
        return result

    def registry_stats(self) -> dict:
        """内容注册表统计"""
        from content_registry import ContentRegistry
        registry = ContentRegistry(self.vault_path)
        return registry.stats()

    # ─── 技能路由 ───

    @tool(readonly=True, write=False, category="general", system=False, name="detect_memory_signal")
    def detect_memory_signal(self, text: str) -> dict:
        """
        检测用户消息中的【显式记忆信号】，返回记忆写入建议——信号闭环入口。

        灵台不缺信源分级（confidence.py SOURCE_LEVELS），缺的是把用户自然语言
        解析成 detect_source_type 需要的 context/source_type。本工具填这个空：
          纠正类（"不对/应该是/别再"）      -> user_correction (0.9, active)
          指令类（"记住/以后/从今往后"）    -> user_directive  (0.8, active)
          偏好陈述（"我偏好/我喜欢/我习惯"） -> user_stated     (0.4, pending)

        用法：灵识每轮回复前对用户最新消息调用本工具；is_signal=True 时按返回的
        source_type/context/tags/suggested_status 调 memory_write 完成写入闭环。

        Returns:
            is_signal + signal_kind + source_type + confidence_hint +
            suggested_status + content + suggested_tags + context + note
        """
        from skill_router import SkillRouter
        router = SkillRouter(self.vault_path)
        return router.detect_memory_signal(text)

    # ─── 向量索引状态（方向⑫）───

    @tool(readonly=True, write=False, category="general", system=True)
    def vector_index_status(self) -> dict:
        """
        向量索引状态查询（方向⑫）
        灵台使用内置语义搜索（基于index.json的keyword+anchor+graph）
        """
        import os
        from datetime import datetime

        stats = self.memory.get_stats()
        result = {
            'status': 'ready',
            'strategy': 'builtin_semantic_anchor_graph',
            'total_pages': stats.get('total_pages', 0),
            'total_links': stats.get('total_links', 0),
            'anchor_system': {
                'relation_anchors': len(self.memory.RELATION_ANCHORS),
                'model_anchors': len(self.memory.MODEL_ANCHORS),
                'tech_anchors': len(self.memory.TECH_ANCHORS),
            },
        }
        index_path = os.path.join(self.vault_path, '丹房', '.meta', 'index.json')
        if os.path.exists(index_path):
            mtime = os.path.getmtime(index_path)
            result['index_updated'] = datetime.fromtimestamp(mtime).isoformat()
            result['index_age_hours'] = round((datetime.now().timestamp() - mtime) / 3600, 1)
        result['status'] = 'ready' if result.get('index_updated') else 'needs_rebuild'
        return result

    # ─── 域可见性（方向⑨：继承链管理）───

    @tool(readonly=True, write=False, category="general", system=True)
    def domain_visibility(self, domain: str = None) -> dict:
        """查询域访问权限和继承规则（public/private/domain_only 三级）"""
        defaults = {
            '00-思考与认知': 'public', '01-内容创作': 'public',
            '02-成长与日常': 'public', '03-社会观察': 'public',
            '04-身体与健康': 'public', '05-哲学与思想': 'public',
            '06-商业与投资': 'public', '07-工具与AI': 'public',
            '08-教育': 'public', '99-一人公司': 'public',
            '98-敏感': 'private',
            '97-草稿': 'domain_only',
            '作品': 'public', '入门': 'public',
        }
        if domain:
            v = defaults.get(domain, 'public')
            return {'domain': domain, 'visibility': v,
                    'note': 'public=全局搜索 | private=仅主动search | domain_only=仅同域搜索'}
        return {'domains': defaults, 'total': len(defaults),
                'rule': 'public/private/domain_only 三级可见性'}

    # ═══════════════════════════════════════════
    #  全文搜索（非丹房资产——技能/原料/作品/外部参考）
    # ═══════════════════════════════════════════

    # FTS5 scope 映射（类级常量，供 fulltext_search 和索引构建共用）
    _FTS_SCOPE_MAP = {
        "技能": ("技能", "灵台·技能"),
        "原料": ("原料", "灵台·原料"),
        "作品": ("作品", "灵台·作品"),
        "外部参考": ("../外部参考和skills", "灵台·外部参考"),
        "日志": (".tool/lingtai-kb/logs", "灵台·日志"),
    }

    @tool(readonly=True, write=False, category="system", system=False)
    def fulltext_search(self, keyword: str, scope: str = "all", max_results: int = 20) -> dict:
        """
        全文搜索非丹房目录——技能/原料/作品/外部参考。
        场景：搜原料原文、技能模板、作品文件、外部参考 SKILL.md 时。
        区别：knowledge_search 只搜丹房页；此工具补盲搜索其他 .md 资产。

        Args:
            keyword: 搜索关键词
            scope: 限定搜索范围（技能/原料/作品/外部参考/日志/all）
            max_results: 最大返回条数（默认20）

        Returns:
            dict: {found: bool, scope: str, total_matches: int, results: [{path, snippet, source_label}]}
        """
        # 参数校验
        if scope != "all" and scope not in self._FTS_SCOPE_MAP:
            return {"found": False, "scope": scope,
                    "error": f"无效 scope：{scope}，可选：技能/原料/作品/外部参考/日志/all"}

        # 懒加载 FTS5 索引
        if not hasattr(self, "_fts_index"):
            from fts_index import FulltextIndex
            self._fts_index = FulltextIndex(self.vault_path)

        try:
            self._fts_index.ensure_built(self._FTS_SCOPE_MAP)
            hits = self._fts_index.query(keyword, scope=scope, max_results=max_results)
        except Exception as e:
            log.warning("fts5 query failed, fallback to brute-force",
                        extra={"keyword": keyword, "error": str(e)}, exc_info=True)
            hits = self._fts_fallback(keyword, scope, max_results)

        results = [
            {"path": h["path"], "snippet": h["snippet"], "source_label": h["source_label"]}
            for h in hits
        ]
        return {
            "found": len(results) > 0,
            "scope": scope,
            "keyword": keyword,
            "total_matches": len(results),
            "results": results[:max_results],
        }

    def _fts_fallback(self, keyword: str, scope: str, max_results: int) -> list[dict]:
        """FTS5 不可用时的暴力扫描降级路径。"""
        vault = self.vault_path
        kw_lower = keyword.lower().strip()

        if scope == "all":
            dirs_to_search = [rel for rel, _ in self._FTS_SCOPE_MAP.values()]
        else:
            dirs_to_search = [self._FTS_SCOPE_MAP[scope][0]]

        results = []
        seen = set()
        for rel_dir in dirs_to_search:
            abs_dir = os.path.join(vault, rel_dir)
            if not os.path.isdir(abs_dir):
                continue
            source_label = "灵台·资产"
            for s, (rel, label) in self._FTS_SCOPE_MAP.items():
                if rel == rel_dir:
                    source_label = label
                    break
            for root, _dirs, files in os.walk(abs_dir):
                _dirs[:] = [d for d in _dirs if not d.startswith(".")]
                for fname in files:
                    if not fname.endswith(".md"):
                        continue
                    fpath = os.path.join(root, fname)
                    try:
                        with open(fpath, "r", encoding="utf-8", errors="ignore") as fp:
                            content = fp.read()
                    except Exception:
                        continue
                    if kw_lower not in content.lower():
                        continue
                    norm = os.path.normpath(fpath)
                    if norm in seen:
                        continue
                    seen.add(norm)
                    idx = content.lower().find(kw_lower)
                    start = max(0, idx - 80)
                    end = min(len(content), idx + len(keyword) + 80)
                    snippet = content[start:end].replace("\n", " ").strip()
                    if len(snippet) > 200:
                        snippet = snippet[:200] + "\u2026"
                    rel_path = os.path.relpath(fpath, vault).replace("\\", "/")
                    results.append({
                        "path": rel_path,
                        "snippet": snippet,
                        "source_label": source_label,
                    })
                    if len(results) >= max_results:
                        return results
        return results

    @tool(readonly=True, write=False, category="system", system=True)
    def system_health(self) -> dict:
        """系统健康度仪表盘。
        
        返回待提炼原料量、工具健康度、距上次会话天数等指标。
        不驱动行为，只驱动提醒和建议。
        """
        try:
            from server_mixins.health_indicators import compute_indicators
            vault = getattr(self, 'vault_path', None) or r"."
            return compute_indicators(vault)
        except Exception as e:
            return {"error": str(e), "ingestion_backlog": 0, "tool_health": {}, "last_session_gap_days": 0}

    @tool(readonly=True, write=False, category="system", system=False)
    def episodic_search(self, keyword: str = None, limit: int = 10, days: int = None) -> dict:
        """搜索/浏览历史会话的交互日志（双模式：关键词搜索 或 按天浏览近期）。
        场景：回溯具体某次对话的操作细节时（传 keyword）；想知道"最近几天做了什么"时（传 days，不传 keyword）。
        区别：找提炼后的教训/偏好/纠正用 memory_search；查知识用 knowledge_search。

        Args:
            keyword: 搜索关键词（与 days 二选一；都不传时默认返回最近7天）
            limit: 最大返回条数（默认10）
            days: 查看最近N天的摘要（传此参数时走近期浏览模式，含 follow_ups）
        """
        try:
            from memory_bank.episodic import EpisodicMemory
            vault = getattr(self, 'vault_path', None) or r"."
            ep = EpisodicMemory(vault)
            # 近期浏览模式：传了 days 或 keyword 为空
            if days is not None or not keyword:
                d = days if days is not None else 7
                results = ep.get_recent(days=d, limit=limit)
                follow_ups = ep.get_follow_ups()
                return {"found": len(results) > 0, "total": len(results), "mode": "recent",
                        "days": d, "sessions": results, "follow_ups": follow_ups}
            # 关键词搜索模式
            results = ep.query(keyword=keyword, limit=limit)
            return {"found": len(results) > 0, "total": len(results), "mode": "search",
                    "keyword": keyword, "sessions": results}
        except Exception as e:
            return {"found": False, "error": str(e), "sessions": []}
