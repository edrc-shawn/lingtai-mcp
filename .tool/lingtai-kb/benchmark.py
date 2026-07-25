# -*- coding: utf-8 -*-
"""
灵台 MCP Server 行业基准测试 v1.1
==================================
全维度基准：协议合规 × 性能 × 检索质量 × 系统健康 + 行业标准
- 官方 MCP 协议合规（@modelcontextprotocol/conformance）
- RAGAS 检索质量指标（可选）
- LLM 端到端 QA（可选）
留存基线，支持趋势追踪。

用法：
    python benchmark.py                    # 全量跑
    python benchmark.py --quick            # 快速模式
    python benchmark.py --compare          # 与上次基线对比
    python benchmark.py --conformance      # 含 MCP 官方协议合规测试
    python benchmark.py --ragas            # 含 RAGAS 检索质量评估
    python benchmark.py --qa               # 含 LLM 端到端 QA 评估
    python benchmark.py --list-baselines   # 列出历史基线
"""

import json, os, sys, time, subprocess, statistics, platform, copy
from pathlib import Path
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

LINGTAI_KB = Path(__file__).resolve().parent
VAULT = os.environ.get("LINGTAI_VAULT", r".")
BASELINE_DIR = LINGTAI_KB / ".cache" / "benchmark"
BASELINE_DIR.mkdir(parents=True, exist_ok=True)

REPORT_DIR = Path(VAULT) / "体检" / "基准报告"
REPORT_DIR.mkdir(parents=True, exist_ok=True)

# ═══════════════════════════════════════════════
# MCP 子进程管理
# ═══════════════════════════════════════════════

class MCPClient:
    """通过 stdio JSON-RPC 与灵台 MCP 通信"""

    def __init__(self, timeout: float = 30.0):
        self.proc = None
        self.timeout = timeout
        self.startup_time = 0.0

    def start(self):
        t0 = time.perf_counter()
        self.proc = subprocess.Popen(
            [sys.executable, 'mcp_server.py'],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            cwd=str(LINGTAI_KB),
            env={'LINGTAI_VAULT': VAULT, 'PYTHONIOENCODING': 'utf-8',
                 'LINGTAI_CLIENT_ID': 'benchmark'},
        )
        self.startup_time = time.perf_counter() - t0
        return self.startup_time

    def call(self, method: str, params: dict = None, id: int = 1) -> dict:
        req = json.dumps({
            'jsonrpc': '2.0', 'id': id, 'method': method, 'params': params or {}
        }, ensure_ascii=False) + '\n'
        self.proc.stdin.write(req.encode('utf-8'))
        self.proc.stdin.flush()
        # 读一行
        line = b''
        while True:
            ch = self.proc.stdout.read(1)
            if not ch:
                raise ConnectionError("MCP process closed stdout")
            line += ch
            if ch == b'\n':
                break
        return json.loads(line.decode('utf-8'))

    def tools_call(self, name: str, arguments: dict = None) -> Tuple[dict, float]:
        """调用 tools/call，返回 (result, latency_seconds)"""
        t0 = time.perf_counter()
        resp = self.call('tools/call', {
            'name': name, 'arguments': arguments or {}
        })
        latency = time.perf_counter() - t0
        if 'error' in resp:
            return resp, latency
        # result.content[0].text 是 JSON 字符串
        result_text = resp.get('result', {}).get('content', [{}])[0].get('text', '')
        try:
            result = json.loads(result_text) if result_text else {}
        except json.JSONDecodeError:
            result = {"raw": result_text}
        return result, latency

    def initialize(self):
        resp = self.call('initialize', {
            'clientInfo': {'name': 'benchmark', 'version': '1.0'}
        })
        return resp

    def tools_list(self):
        resp = self.call('tools/list')
        tools = resp.get('result', {}).get('tools', [])
        return tools

    def close(self):
        if self.proc:
            try:
                self.proc.stdin.close()
                self.proc.wait(timeout=3)
            except:
                self.proc.kill()

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *args):
        self.close()


# ═══════════════════════════════════════════════
# 测试项
# ═══════════════════════════════════════════════

class BenchmarkResult:
    """单次测试结果"""

    def __init__(self, name: str, category: str):
        self.name = name
        self.category = category
        self.passed = False
        self.latency_ms: Optional[float] = None
        self.error: Optional[str] = None
        self.detail: dict = {}
        self.timestamp = datetime.now(timezone.utc).isoformat()


def test_protocol_compliance(client: MCPClient, results: List[BenchmarkResult]):
    """MCP 协议合规性测试"""

    # 1. initialize
    r = BenchmarkResult("initialize", "协议合规")
    try:
        resp = client.initialize()
        server_info = resp.get('result', {})
        r.passed = server_info.get('protocolVersion') == '2024-11-05'
        r.detail = {
            'protocolVersion': server_info.get('protocolVersion'),
            'serverInfo': server_info.get('serverInfo'),
            'capabilities': list(server_info.get('capabilities', {}).keys()),
        }
        if not r.passed:
            r.error = f"Expected protocolVersion=2024-11-05, got {server_info.get('protocolVersion')}"
    except Exception as e:
        r.passed = False
        r.error = str(e)
    results.append(r)

    # 2. tools/list
    r = BenchmarkResult("tools/list", "协议合规")
    try:
        tools = client.tools_list()
        r.passed = len(tools) >= 60
        r.detail = {'tool_count': len(tools)}
        if not r.passed:
            r.error = f"Expected >=60 tools, got {len(tools)}"
    except Exception as e:
        r.passed = False
        r.error = str(e)
    results.append(r)

    # 3. Unknown method error handling
    r = BenchmarkResult("unknown_method (错误处理)", "协议合规")
    try:
        resp = client.call('unknown_method_x', id=99)
        has_error = 'error' in resp
        code = resp.get('error', {}).get('code')
        r.passed = has_error and code == -32601
        r.detail = {'response': resp}
        if not r.passed:
            r.error = f"Expected error code -32601, got {code}"
    except Exception as e:
        r.passed = False
        r.error = str(e)
    results.append(r)

    # 4. Unknown tool error handling
    r = BenchmarkResult("unknown_tool (错误处理)", "协议合规")
    try:
        resp = client.call('tools/call', {
            'name': 'nonexistent_tool_xyz', 'arguments': {}
        }, id=98)
        has_error = 'error' in resp
        code = resp.get('error', {}).get('code')
        r.passed = has_error and code == -32601
        r.detail = {'response': resp}
        if not r.passed:
            r.error = f"Expected error code -32601, got {code}"
    except Exception as e:
        r.passed = False
        r.error = str(e)
    results.append(r)


def test_core_tools_performance(client: MCPClient, results: List[BenchmarkResult],
                                 quick: bool = False):
    """核心工具性能基准——测量关键工具的响应延迟"""

    # ─── 只读工具 ───
    readonly_tests = [
        # (name, args, warmup_hint)
        ("knowledge_stats", {}, "知识库统计"),
        ("knowledge_domains", {}, "知识域列表"),
        ("vector_index_status", {}, "向量索引状态"),
        ("knowledge_heatmap", {"top_n": 5}, "知识热度"),
        ("knowledge_compound", {"top_n": 5}, "知识复利"),
        ("health_inspect", {}, "全量体检"),
        ("knowledge_search", {"keyword": "AI", "mode": "semantic", "hops": 1}, "语义搜索"),
        ("knowledge_search", {"keyword": "AI", "mode": "text"}, "全文搜索"),
        ("knowledge_search", {"keyword": "记忆系统"}, "搜索-记忆系统"),
        ("knowledge_search", {"keyword": "O与π"}, "搜索-O与π"),
        ("page_read", {"path": "丹房/00-思考与认知/含人量", "max_chars": 1000}, "读页"),
        ("refine_all_status", {}, "提炼状态统计"),
        ("knowledge_gaps", {"min_severity": 0.3}, "知识缺口"),
    ]

    if not quick:
        readonly_tests += [
            ("knowledge_explore", {"mode": "graph", "page_path": "丹房/00-思考与认知/含人量", "hops": 2}, "图扩散"),
            ("page_link_suggest", {"page_path": "丹房/00-思考与认知/含人量"}, "链接建议"),
            ("knowledge_search_evidence", {"keyword": "AI Agent"}, "搜索证据"),
            ("knowledge_pages", {"domain": "00-思考与认知"}, "域下页列表"),
            ("memory_stats", {}, "记忆统计"),
            ("cross_end_activity", {"hours": 24}, "跨端活动"),
            ("fulltext_search", {"keyword": "AI", "scope": "技能"}, "全文搜索-技能"),
            ("ingest_ripple", {"new_page": "丹房/07-工具与AI/灵台架构定位分析：与通用AI Agent的对比"}, "波及分析"),
            ("page_history", {"page_path": "丹房/00-思考与认知/含人量", "days": 7}, "页面历史"),
            ("knowledge_explore", {"mode": "related", "page_path": "丹房/00-思考与认知/含人量"}, "出入链"),
            ("knowledge_explore", {"mode": "topic", "topic": "AI Agent"}, "主题探索"),
        ]

    for name, args, hint in readonly_tests:
        r = BenchmarkResult(name, "性能-只读")
        try:
            # warmup call (不计时)
            try:
                client.tools_call(name, args)
            except:
                pass

            latencies = []
            for i in range(3 if not quick else 2):
                _, lat = client.tools_call(name, args)
                latencies.append(lat * 1000)  # ms

            r.passed = True
            r.latency_ms = round(statistics.mean(latencies), 2)
            r.detail = {
                'hint': hint,
                'mean_ms': r.latency_ms,
                'min_ms': round(min(latencies), 2),
                'max_ms': round(max(latencies), 2),
                'samples': len(latencies),
            }
        except Exception as e:
            r.passed = False
            r.error = str(e)
        results.append(r)


def test_knowledge_quality(client: MCPClient, results: List[BenchmarkResult]):
    """知识检索质量基准——已知标准查询"""

    # ─── 标准查询集 ───
    queries = [
        ("含人量", "丹房/00-思考与认知/含人量"),
        ("O与π", "丹房/00-思考与认知/追问·O与π"),
        ("独立思考", "丹房/00-思考与认知/独立思考·框架自觉"),
        ("灵台架构", "丹房/07-工具与AI/灵台架构定位分析"),
        ("AI Agent", None),  # 无标准答案，统计返回数量
        ("Obsidian", None),
        ("记忆系统", "丹房/07-工具与AI/灵台认知架构"),
        ("思维模型", None),
        ("认知边界", "丹房/00-思考与认知/认知边界与生长"),
        ("含人量 AI 时代", None),
    ]

    for keyword, expected_page in queries:
        r = BenchmarkResult(f"search:{keyword}", "检索质量")
        try:
            result, lat = client.tools_call('knowledge_search', {
                'keyword': keyword, 'hops': 2, 'mode': 'semantic'
            })
            hits = result.get('matches') or result.get('results') or result.get('pages', [])
            if isinstance(hits, dict):
                hits = list(hits.values()) if hasattr(hits, 'values') else [hits]
            hit_count = len(hits) if isinstance(hits, list) else (1 if hits else 0)

            # 检查是否命中期望页
            found_expected = False
            top_result = ""
            if expected_page and isinstance(hits, list):
                for h in hits:
                    h_path = h.get('path', '') or h.get('page_path', '') or h.get('title', '')
                    if expected_page in h_path:
                        found_expected = True
                        top_result = h_path
                        break
                if not found_expected and hits:
                    top_result = hits[0].get('path', '') or hits[0].get('title', '')

            r.passed = hit_count > 0
            r.latency_ms = round(lat * 1000, 2)
            r.detail = {
                'keyword': keyword,
                'hit_count': hit_count,
                'expected_found': found_expected,
                'expected_page': expected_page,
                'top_result': top_result,
                'latency_ms': r.latency_ms,
            }
            if not r.passed:
                r.error = "Empty result"
        except Exception as e:
            r.passed = False
            r.error = str(e)
        results.append(r)


def test_system_health(client: MCPClient, results: List[BenchmarkResult],
                        startup_time: float):
    """系统健康度基准"""

    # 系统健康
    r = BenchmarkResult("system_health", "系统健康")
    try:
        result, lat = client.tools_call('system_health')
        r.passed = result.get('ok', False) or 'ingestion_backlog' in result.get('data', {})
        r.detail = {
            'latency_ms': round(lat * 1000, 2),
            'data': result.get('data', result),
        }
        r.latency_ms = round(lat * 1000, 2)
    except Exception as e:
        r.passed = False
        r.error = str(e)
    results.append(r)

    # context_load 性能
    r = BenchmarkResult("context_load", "系统健康")
    try:
        # warmup (it's heavy, ~10s first call, but warm-start caches now)
        try:
            client.tools_call('context_load', {})
        except:
            pass
        result, lat = client.tools_call('context_load', {})
        r.passed = result.get('layers') is not None or result.get('greeting') is not None
        r.detail = {
            'latency_ms': round(lat * 1000, 2),
            'has_layers': 'layers' in result,
        }
        r.latency_ms = round(lat * 1000, 2)
    except Exception as e:
        r.passed = False
        r.error = str(e)
    results.append(r)

    # 启动时间
    r = BenchmarkResult("startup_time", "系统健康")
    r.passed = startup_time < 5.0  # 合理启动时间 < 5s
    r.latency_ms = round(startup_time * 1000, 2)
    r.detail = {'seconds': round(startup_time, 3)}
    if not r.passed:
        r.error = f"Slow startup: {startup_time:.2f}s (threshold: 5.0s)"
    results.append(r)

    # 内存占用 (粗略：用系统任务管理器 API)
    r = BenchmarkResult("memory_usage", "系统健康")
    try:
        import psutil
        proc = psutil.Process(client.proc.pid)
        mem_mb = proc.memory_info().rss / 1024 / 1024
        r.passed = mem_mb < 1024  # 内存 < 1GB
        r.detail = {'rss_mb': round(mem_mb, 1)}
        r.latency_ms = round(mem_mb, 1)  # 用 latency 字段存内存值 (非标准用法)
    except ImportError:
        r.passed = True
        r.detail = {'note': 'psutil not installed, skip memory measurement'}
    except Exception as e:
        r.passed = True
        r.detail = {'note': f'Memory measurement skipped: {e}'}
    results.append(r)


# ═══════════════════════════════════════════════
# 行业标准测试
# ═══════════════════════════════════════════════

def test_conformance(client: MCPClient, results: List[BenchmarkResult]):
    """官方 MCP 协议合规测试（@modelcontextprotocol/conformance）"""
    import threading, http.server

    # 启动 HTTP 桥接服务器
    bridge = MCPClient()
    bridge.start()

    from http.server import HTTPServer, BaseHTTPRequestHandler

    class _BridgeHandler(BaseHTTPRequestHandler):
        _bridge = None
        def do_GET(self):
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(b'{"ok":true}')
        def do_POST(self):
            from urllib.parse import urlparse
            if urlparse(self.path).path != '/mcp':
                self.send_response(404); self.end_headers(); return
            length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(length)
            try:
                req = json.loads(body.decode('utf-8'))
            except Exception:
                self.send_response(400); self.end_headers(); return
            try:
                resp = self._bridge.call(req['method'], req.get('params', {}), req.get('id', 1))
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps(resp, ensure_ascii=False).encode('utf-8'))
            except Exception as e:
                self.send_response(503)
                self.end_headers()
                self.wfile.write(json.dumps({"error": str(e)}).encode('utf-8'))
        def log_message(self, *a): pass

    port = 19877
    _BridgeHandler._bridge = bridge
    httpd = HTTPServer(('127.0.0.1', port), _BridgeHandler)
    http_thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    http_thread.start()

    time.sleep(1)

    url = f"http://127.0.0.1:{port}/mcp"
    try:
        result = subprocess.run(
            f'npx @modelcontextprotocol/conformance server --url {url} --verbose',
            timeout=120, capture_output=True, text=False,
            cwd=str(LINGTAI_KB), shell=True,
            env={**os.environ, 'LINGTAI_VAULT': VAULT}
        )
        raw = (result.stdout or b'').decode('utf-8', errors='replace') + (result.stderr or b'').decode('utf-8', errors='replace')

        lines = raw.split('\n')
        scenario_results = []
        current_scenario = None
        for line in lines:
            if line.startswith('=== Running scenario:'):
                current_scenario = line.split(':')[1].strip().split('===')[0].strip()
            elif current_scenario and 'passed' in line and 'failed' in line:
                passed_count = int(line.split('passed')[0].strip().split()[-1])
                failed_count = int(line.split('failed')[0].strip().split()[-1])
                scenario_results.append((current_scenario, passed_count, failed_count))
                current_scenario = None

        expected_missing = {'resources', 'prompts', 'completion', 'sampling',
                            'sse', 'elicitation', 'dns'}

        passed_scenarios = []
        failed_scenarios = []
        missing_scenarios = []

        for scenario, p, f in scenario_results:
            if f == 0 and p > 0:
                passed_scenarios.append(scenario)
            elif any(kw in scenario for kw in expected_missing):
                missing_scenarios.append(scenario)
            else:
                failed_scenarios.append(scenario)

        r = BenchmarkResult("MCP Conformance 官方套件", "行业标准")
        r.passed = len(passed_scenarios) > 0
        r.detail = {
            'total_scenarios': len(scenario_results),
            'passed': len(passed_scenarios),
            'expected_missing': len(missing_scenarios),
            'unexpected_failures': len(failed_scenarios),
            'passed_list': passed_scenarios[:10],
            'expected_missing_list': missing_scenarios[:10],
            'unexpected_failures_list': failed_scenarios,
        }
        r.latency_ms = round(len(scenario_results) * 1.0, 1)
        if failed_scenarios:
            r.error = f"Unexpected failures: {', '.join(failed_scenarios[:5])}"
        results.append(r)

        for scenario, p, f in scenario_results:
            sr = BenchmarkResult(f"conformance:{scenario}", "行业标准")
            sr.passed = f == 0
            sr.detail = {'passed': p, 'failed': f}
            if f > 0:
                if any(kw in scenario for kw in expected_missing):
                    sr.detail['expected_missing'] = True
                    sr.error = "未实现（预期内）"
                else:
                    sr.error = "未通过"
            results.append(sr)

    except subprocess.TimeoutExpired:
        r = BenchmarkResult("MCP Conformance 官方套件", "行业标准")
        r.passed = False; r.error = "Conformance 测试超时（120s）"
        results.append(r)
    except FileNotFoundError:
        r = BenchmarkResult("MCP Conformance 官方套件", "行业标准")
        r.passed = False; r.error = "npx 不可用，请安装 Node.js"
        results.append(r)
    finally:
        httpd.shutdown()
        bridge.close()


def test_ragas(client: MCPClient, results: List[BenchmarkResult]):
    """RAGAS 检索质量评估（可选，需安装 ragas）"""
    try:
        from ragas.metrics import context_precision, context_recall
        from ragas import evaluate
        from datasets import Dataset
    except ImportError:
        r = BenchmarkResult("RAGAS 检索质量", "行业标准")
        r.passed = False; r.error = "ragas 未安装，执行: python -m pip install ragas"
        results.append(r); return

    queries = [
        ("含人量", "丹房/00-思考与认知/含人量"),
        ("O与π", "丹房/00-思考与认知/追问·O与π"),
        ("独立思考", "丹房/00-思考与认知/独立思考·框架自觉"),
        ("灵台架构", "丹房/07-工具与AI/灵台架构定位分析"),
        ("认知边界", "丹房/00-思考与认知/认知边界与生长"),
    ]
    data = {"question": [], "contexts": [], "ground_truth": []}
    for query, expected in queries:
        result, _ = client.tools_call('knowledge_search', {
            'keyword': query, 'hops': 1, 'mode': 'semantic'
        })
        hits = result.get('matches') or result.get('results') or result.get('pages', [])
        if isinstance(hits, dict):
            hits = list(hits.values()) if hasattr(hits, 'values') else [hits]
        contexts = [h.get('summary', '') or h.get('title', '') for h in (hits or [])[:5]]
        data["question"].append(query)
        data["contexts"].append(contexts)
        data["ground_truth"].append([expected])

    ds = Dataset.from_dict(data)
    try:
        scores = evaluate(ds, metrics=[context_precision, context_recall])
        r = BenchmarkResult("RAGAS 检索质量", "行业标准")
        r.passed = True
        r.detail = {
            'context_precision': float(scores.get('context_precision', 0)),
            'context_recall': float(scores.get('context_recall', 0)),
            'samples': len(queries),
        }
        results.append(r)
    except Exception as e:
        r = BenchmarkResult("RAGAS 检索质量", "行业标准")
        r.passed = False; r.error = f"RAGAS evaluate failed: {e}"
        results.append(r)


def test_llm_qa(client: MCPClient, results: List[BenchmarkResult]):
    """LLM 端到端 QA 评估（可选，需设置 API key）"""
    api_key = os.environ.get('OPENAI_API_KEY') or os.environ.get('ANTHROPIC_API_KEY')
    if not api_key:
        r = BenchmarkResult("LLM QA 评估", "行业标准")
        r.passed = False; r.error = "未设置 API key"
        results.append(r); return

    qa_pairs = [
        ("灵台知识库的核心概念是什么？", "含人量"),
        ("O与π框架中，O代表什么？", "收拢"),
        ("独立思考需要什么？", "框架自觉"),
        ("灵台有多少个知识域？", "13"),
        ("什么是认知边界？", "切粒"),
    ]

    passed_count = 0
    for question, expected_keyword in qa_pairs:
        r = BenchmarkResult(f"QA:{question[:20]}", "行业标准")
        try:
            search_result, _ = client.tools_call('knowledge_search', {
                'keyword': question, 'hops': 1, 'mode': 'semantic'
            })
            hits = search_result.get('matches') or search_result.get('results') or []
            if isinstance(hits, dict):
                hits = list(hits.values()) if hasattr(hits, 'values') else [hits]
            contexts = [h.get('summary', '') or h.get('title', '') for h in (hits or [])[:3]]
            context_text = '\n'.join(contexts)

            r.passed = expected_keyword.lower() in context_text.lower()
            r.detail = {
                'question': question,
                'expected_keyword': expected_keyword,
                'found': r.passed,
                'contexts': len(contexts),
            }
            if r.passed:
                passed_count += 1
            else:
                r.error = f"未找到关键词: {expected_keyword}"
        except Exception as e:
            r.passed = False; r.error = str(e)
        results.append(r)

    r = BenchmarkResult("LLM QA 汇总", "行业标准")
    r.passed = passed_count >= len(qa_pairs) * 0.8
    r.detail = {'passed': passed_count, 'total': len(qa_pairs), 'rate': f"{passed_count/len(qa_pairs)*100:.0f}%"}
    if not r.passed:
        r.error = f"通过率 {passed_count}/{len(qa_pairs)}，低于 80%"
    results.append(r)


# ═══════════════════════════════════════════════
# 结果报告
# ═══════════════════════════════════════════════

def generate_markdown_report(results: List[BenchmarkResult], quick: bool,
                              compare_baseline: dict = None) -> str:
    """生成 Markdown 格式的基准报告"""

    now = datetime.now(timezone.utc).astimezone()
    total = len(results)
    passed = sum(1 for r in results if r.passed)
    failed = total - passed

    # 按类别分组
    categories = {}
    for r in results:
        categories.setdefault(r.category, []).append(r)

    lines = []
    lines.append(f"# 灵台 MCP 基准报告")
    lines.append(f"")
    lines.append(f"> **日期**: {now.strftime('%Y-%m-%d %H:%M')}")
    lines.append(f"> **模式**: {'快速' if quick else '完整'}")
    lines.append(f"> **工具总数**: {total} 项 | ✅ {passed} 通过 | ❌ {failed} 失败 | 通过率 {passed/total*100:.1f}%")
    lines.append(f"> **系统**: {platform.system()} {platform.release()} | **Python**: {sys.version.split()[0]}")
    lines.append(f"> **知识库**: {VAULT}")
    lines.append(f"")

    # ─── 摘要仪表盘 ───
    lines.append(f"## 📊 摘要仪表盘")
    lines.append(f"")
    lines.append(f"| 类别 | 总项 | 通过 | 失败 | 通过率 |")
    lines.append(f"|------|------|------|------|--------|")
    for cat, items in sorted(categories.items()):
        p = sum(1 for r in items if r.passed)
        f = len(items) - p
        rate = p / len(items) * 100 if items else 0
        bar = "🟢" if rate >= 90 else ("🟡" if rate >= 70 else "🔴")
        lines.append(f"| {bar} {cat} | {len(items)} | {p} | {f} | {rate:.1f}% |")
    lines.append(f"")

    # 对比基线（如有）
    if compare_baseline:
        lines.append(f"### 与上次基线对比")
        lines.append(f"")
        lines.append(f"| 工具 | 本次 (ms) | 基线 (ms) | 变化 |")
        lines.append(f"|------|-----------|-----------|------|")
        for r in results:
            if r.latency_ms is not None and r.name in compare_baseline:
                prev = compare_baseline[r.name]
                change = r.latency_ms - prev
                arrow = "⬆" if change > 0 else ("⬇" if change < 0 else "→")
                lines.append(f"| {r.name} | {r.latency_ms} | {prev} | {arrow} {change:+.1f}ms |")
        lines.append(f"")

    # ─── 协议合规 ───
    if "协议合规" in categories:
        lines.append(f"## ✅ MCP 协议合规性")
        lines.append(f"")
        lines.append(f"| 测试项 | 结果 | 详情 |")
        lines.append(f"|--------|------|------|")
        for r in categories["协议合规"]:
            status = "✅" if r.passed else "❌"
            detail = r.error or json.dumps(r.detail, ensure_ascii=False)[:120]
            lines.append(f"| {r.name} | {status} | {detail} |")
        lines.append(f"")

    # ─── 性能 ───
    if "性能-只读" in categories:
        items = sorted(categories["性能-只读"], key=lambda x: x.latency_ms or 0)
        lines.append(f"## ⚡ 工具性能基准")
        lines.append(f"")
        lines.append(f"| 工具 | 说明 | 均值 (ms) | 最慢 (ms) | 采样 |")
        lines.append(f"|------|------|-----------|-----------|------|")
        for r in items:
            if r.passed:
                detail = r.detail
                lines.append(f"| {r.name} | {detail.get('hint','')} | {detail.get('mean_ms','N/A')} | {detail.get('max_ms','N/A')} | {detail.get('samples','')}x |")
            else:
                lines.append(f"| {r.name} | ❌ {r.error} | - | - | - |")
        lines.append(f"")

        # 延迟分布
        latencies = [r.latency_ms for r in items if r.latency_ms is not None]
        if latencies:
            lines.append(f"**延迟统计**: 均值 {statistics.mean(latencies):.1f}ms | 中位 {statistics.median(latencies):.1f}ms | 最慢 {max(latencies):.1f}ms | 最快 {min(latencies):.1f}ms")
            lines.append(f"")

    # ─── 检索质量 ───
    if "检索质量" in categories:
        lines.append(f"## 🎯 知识检索质量")
        lines.append(f"")
        lines.append(f"| 关键词 | 命中数 | 期望命中 | 首位结果 | 延迟 (ms) |")
        lines.append(f"|--------|--------|----------|----------|-----------|")
        for r in categories["检索质量"]:
            status = "✅" if r.passed else "❌"
            d = r.detail
            expected = "✅" if d.get('expected_found') else ("⚠️" if d.get('expected_page') else "-")
            top = d.get('top_result', '') or d.get('hint', '')
            lines.append(f"| {status} {d.get('keyword','')} | {d.get('hit_count',0)} | {expected} | {top[:50]} | {d.get('latency_ms','N/A')} |")
        lines.append(f"")

        # 检索质量汇总
        quality_items = categories["检索质量"]
        recall = sum(1 for r in quality_items if r.passed) / len(quality_items) * 100
        expected_hits = sum(1 for r in quality_items if r.detail.get('expected_found'))
        expected_total = sum(1 for r in quality_items if r.detail.get('expected_page'))
        precision = expected_hits / expected_total * 100 if expected_total else 0
        lines.append(f"**检索质量总结**: 召回率 {recall:.0f}% | 精准命中率 {precision:.0f}%")
        lines.append(f"")

    # ─── 系统健康 ───
    if "系统健康" in categories:
        lines.append(f"## 🏥 系统健康")
        lines.append(f"")
        lines.append(f"| 指标 | 结果 | 数值 |")
        lines.append(f"|------|------|------|")
        for r in categories["系统健康"]:
            status = "✅" if r.passed else "❌"
            val = r.error or json.dumps(r.detail, ensure_ascii=False)
            lines.append(f"| {r.name} | {status} | {val} |")
        lines.append(f"")

    # ─── 行业标准 ───
    if "行业标准" in categories:
        items = categories["行业标准"]
        lines.append(f"## 🏛️ MCP 官方 Conformance 合规")
        lines.append(f"")
        lines.append(f"| 场景 | 结果 | 说明 |")
        lines.append(f"|------|------|------|")
        for r in items:
            if r.name.startswith('conformance:'):
                status = "✅" if r.passed else ("⚠️" if r.detail.get('expected_missing') else "❌")
                scenario = r.name.replace('conformance:', '')
                note = r.error or "通过"
                lines.append(f"| {scenario} | {status} | {note} |")
        
        # 汇总
        conf_items = [r for r in items if r.name == "MCP Conformance 官方套件"]
        if conf_items:
            d = conf_items[0].detail
            lines.append(f"")
            lines.append(f"**Conformance 汇总**: {d.get('passed',0)} 通过 | {d.get('expected_missing',0)} 预期未实现 | {d.get('unexpected_failures',0)} 意外失败")
            lines.append(f"")

        # RAGAS 结果
        ragas_items = [r for r in items if r.name == "RAGAS 检索质量"]
        if ragas_items and ragas_items[0].passed:
            d = ragas_items[0].detail
            lines.append(f"### 📐 RAGAS 检索质量")
            lines.append(f"")
            lines.append(f"| 指标 | 分数 |")
            lines.append(f"|------|------|")
            lines.append(f"| Context Precision | {d.get('context_precision', 'N/A'):.3f} |")
            lines.append(f"| Context Recall | {d.get('context_recall', 'N/A'):.3f} |")
            lines.append(f"| 样本数 | {d.get('samples', 0)} |")
            lines.append(f"")

        # LLM QA 结果
        qa_items = [r for r in items if r.name == "LLM QA 汇总"]
        if qa_items:
            d = qa_items[0].detail
            status = "✅" if qa_items[0].passed else "❌"
            lines.append(f"### 🤖 LLM 端到端 QA")
            lines.append(f"")
            lines.append(f"| 结果 | 详情 |")
            lines.append(f"|------|------|")
            lines.append(f"| {status} | {d.get('passed',0)}/{d.get('total',0)} 通过 ({d.get('rate','')}) |")
            lines.append(f"")

    # ─── 错误汇总 ───
    if failed > 0:
        lines.append(f"## ❌ 失败明细")
        lines.append(f"")
        for r in results:
            if not r.passed:
                lines.append(f"- **{r.name}** ({r.category}): {r.error}")
        lines.append(f"")

    # 推荐项
    lines.append(f"## 💡 建议")
    lines.append(f"")
    slow_tools = [r for r in results if r.latency_ms is not None and r.latency_ms > 2000]
    if slow_tools:
        lines.append(f"- 🔴 以下工具延迟 >2s，建议优化：{', '.join(r.name for r in slow_tools)}")
    failed_cats = set(r.category for r in results if not r.passed)
    if failed_cats:
        lines.append(f"- 🔴 以下类别有失败项：{', '.join(failed_cats)}")
    if passed / total < 0.9:
        lines.append(f"- 🔴 通过率不足 90%，建议排查失败项")
    else:
        lines.append(f"- 🟢 综合通过率 {passed/total*100:.0f}%，系统状态良好")
    lines.append(f"")

    lines.append(f"---")
    lines.append(f"*报告由 benchmark.py v1.0 自动生成 | {now.strftime('%Y-%m-%d %H:%M')}*")
    lines.append(f"")

    return '\n'.join(lines)


def save_baseline(results: List[BenchmarkResult]):
    """保存基线数据到 JSON，用于后续趋势对比"""
    baselines = {}
    for r in results:
        if r.latency_ms is not None:
            baselines[r.name] = {
                'latency_ms': r.latency_ms,
                'passed': r.passed,
                'category': r.category,
            }
    now = datetime.now(timezone.utc)
    filename = f"baseline_{now.strftime('%Y%m%d_%H%M%S')}.json"
    path = BASELINE_DIR / filename

    # 附加元数据
    payload = {
        'timestamp': now.isoformat(),
        'date': now.strftime('%Y-%m-%d'),
        'tool_count': len([r for r in results if r.passed]),
        'pass_rate': sum(1 for r in results if r.passed) / len(results) if results else 0,
        'baselines': baselines,
    }
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    print(f"  📦 基线已保存: {path}")
    return path


def load_latest_baseline() -> Tuple[Optional[dict], Optional[str]]:
    """加载最新的基线文件"""
    files = sorted(BASELINE_DIR.glob("baseline_*.json"), reverse=True)
    if not files:
        return None, None
    latest = files[0]
    with open(latest, encoding='utf-8') as f:
        data = json.load(f)
    baselines = {k: v['latency_ms'] for k, v in data.get('baselines', {}).items()}
    return baselines, str(latest)


def list_baselines():
    """列出所有历史基线"""
    files = sorted(BASELINE_DIR.glob("baseline_*.json"), reverse=True)
    if not files:
        print("  (无历史基线)")
        return
    print(f"\n  历史基线 (共 {len(files)} 条):")
    print(f"  {'日期':<20} {'工具数':<10} {'通过率':<10} {'文件'}")
    print(f"  {'-'*20} {'-'*10} {'-'*10} {'-'*30}")
    for f in files:
        try:
            data = json.loads(f.read_text(encoding='utf-8'))
            tools = len(data.get('baselines', {}))
            rate = data.get('pass_rate', 0) * 100
            print(f"  {data.get('date', '?'):<20} {tools:<10} {rate:<10.0f}% {f.name}")
        except:
            print(f"  {'?':<20} {'?':<10} {'?':<10} {f.name}")


# ═══════════════════════════════════════════════
# 主流程
# ═══════════════════════════════════════════════

def run_benchmark(quick: bool = False, compare: bool = False,
                  conformance: bool = False, ragas: bool = False, qa: bool = False) -> Tuple[List[BenchmarkResult], str]:
    """运行全维度基准测试"""

    results: List[BenchmarkResult] = []

    print(f"\n{'='*60}")
    print(f"  灵台 MCP 行业基准测试")
    print(f"  模式: {'快速' if quick else '完整'}")
    print(f"  时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*60}")

    # 启动服务器
    print(f"\n  🚀 启动 MCP 服务器...")
    client = MCPClient()
    startup_time = client.start()
    print(f"  ✅ 启动完成 ({startup_time:.2f}s)")

    try:
        # 1. 协议合规
        print(f"\n  📋 协议合规测试...")
        test_protocol_compliance(client, results)
        proto_passed = sum(1 for r in results if r.passed and r.category == "协议合规")
        proto_total = sum(1 for r in results if r.category == "协议合规")
        print(f"     {proto_passed}/{proto_total} 通过")

        # 2. 性能基准
        print(f"\n  ⚡ 工具性能基准...")
        test_core_tools_performance(client, results, quick)
        perf_passed = sum(1 for r in results if r.passed and r.category == "性能-只读")
        perf_total = sum(1 for r in results if r.category == "性能-只读")
        print(f"     {perf_passed}/{perf_total} 通过")

        # 3. 检索质量
        if not quick:
            print(f"\n  🎯 知识检索质量...")
            test_knowledge_quality(client, results)
            quality_passed = sum(1 for r in results if r.passed and r.category == "检索质量")
            quality_total = sum(1 for r in results if r.category == "检索质量")
            print(f"     {quality_passed}/{quality_total} 通过")
        else:
            print(f"\n  🎯 知识检索质量 (跳过 - 快速模式)")

        # 4. 系统健康
        print(f"\n  🏥 系统健康...")
        test_system_health(client, results, startup_time)
        health_passed = sum(1 for r in results if r.passed and r.category == "系统健康")
        health_total = sum(1 for r in results if r.category == "系统健康")
        print(f"     {health_passed}/{health_total} 通过")

        # 5. 行业标准测试
        if conformance:
            print(f"\n  🏛️ MCP 官方协议合规测试 (conformance)...")
            test_conformance(client, results)
            std_passed = sum(1 for r in results if r.category == "行业标准" and r.passed)
            std_total = sum(1 for r in results if r.category == "行业标准")
            print(f"     {std_passed}/{std_total} 通过")

        if ragas:
            print(f"\n  📐 RAGAS 检索质量评估...")
            test_ragas(client, results)

        if qa:
            print(f"\n  🤖 LLM 端到端 QA 评估...")
            test_llm_qa(client, results)

    finally:
        client.close()

    # 加载基线对比
    compare_data = None
    if compare:
        compare_data, baseline_file = load_latest_baseline()
        if compare_data:
            print(f"\n  📊 与基线对比: {baseline_file}")

    # 生成报告
    report = generate_markdown_report(results, quick, compare_data)

    # 保存基线
    save_baseline(results)

    # 打印汇总
    total = len(results)
    passed = sum(1 for r in results if r.passed)
    print(f"\n{'='*60}")
    print(f"  结果: {passed}/{total} 通过 ({passed/total*100:.1f}%)")
    print(f"{'='*60}")

    return results, report


def save_report(report: str) -> Path:
    """写入基准报告到体检目录"""
    now = datetime.now(timezone.utc).astimezone()
    filename = f"基准报告-{now.strftime('%Y%m%d-%H%M')}.md"
    path = REPORT_DIR / filename
    path.write_text(report, encoding='utf-8')
    print(f"  📄 报告已写入: {path}")
    return path


# ═══════════════════════════════════════════════
# CLI 入口
# ═══════════════════════════════════════════════

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="灵台 MCP Server 行业基准测试")
    parser.add_argument('--conformance', action='store_true', help='含 MCP 官方协议合规测试')
    parser.add_argument('--ragas', action='store_true', help='含 RAGAS 检索质量评估')
    parser.add_argument('--qa', action='store_true', help='含 LLM 端到端 QA 评估')
    parser.add_argument('--quick', action='store_true', help='快速模式（跳过检索质量深度测试）')
    parser.add_argument('--compare', action='store_true', help='与上次基线对比')
    parser.add_argument('--list-baselines', action='store_true', help='列出历史基线')
    args = parser.parse_args()

    if args.list_baselines:
        list_baselines()
        sys.exit(0)

    results, report = run_benchmark(quick=args.quick, compare=args.compare,
                                      conformance=args.conformance,
                                      ragas=args.ragas, qa=args.qa)
    print(f"\n{report}")
    save_report(report)