# -*- coding: utf-8 -*-
# MCP JSON-RPC routing — v7: decorator-driven, auto-built _TOOL_MAP
# Phase 1 地基：_TOOL_MAP 从 @tool 装饰器注册表自省构建，消除 tools.py 双注册

import json
import os
import sys

from server import LingtaiMCPServer
from errors import ok, fail, ErrorCode
from concurrency import with_write_lock
from schema_validator import validate_page_create
from server_mixins.shared import get_session_logger
from decorators import REGISTRY, export_tools_list

# ═══════════════════════════════════════════
# 服务端单例
# ═══════════════════════════════════════════

server = LingtaiMCPServer()


# ═══════════════════════════════════════════
# tools/list 响应缓存（从装饰器注册表生成）
# ═══════════════════════════════════════════

_TOOLS = export_tools_list()
_TOOLS_LIST_RESP_CACHE = json.dumps(
    {"jsonrpc": "2.0", "id": "__REQ_ID__", "result": {"tools": _TOOLS}},
    ensure_ascii=False,
    separators=(",", ":"),
)


# ═══════════════════════════════════════════
# _TOOL_MAP — 从装饰器注册表自动构建
# ═══════════════════════════════════════════

def _build_handler(name, info):
    """从装饰器元数据构建标准 handler。
    写操作自动加 with_write_lock(resource=...) + _wrap 包装。
    资源按 category 分流：不同 category 的写操作可并行。
    """
    fn_name = info["fn_name"]
    write = info.get("write", False)
    category = info.get("category", "")

    # category → 资源锁映射
    _RESOURCE_MAP = {
        "knowledge": "page",
        "memory": "memory",
        "refine": "raw",
        "system": "index",
    }
    resource = _RESOURCE_MAP.get(category, "default")

    if write:

        def handler(args):
            method = getattr(server, fn_name)
            result = with_write_lock(method, resource=resource)(**args)
            return _wrap(result)

    else:

        def handler(args):
            method = getattr(server, fn_name)
            return _wrap(method(**args))

    return handler


# 自动生成标准 handler
_TOOL_MAP = {}
for _name, _info in REGISTRY.items():
    _TOOL_MAP[_name] = _build_handler(_name, _info)


# ═══════════════════════════════════════════
# 手动覆盖 — 需要 mode dispatch / 路径校验 / 宏包装 的特殊工具
# ═══════════════════════════════════════════

# knowledge_explore: mode 分发到不同方法
_TOOL_MAP["knowledge_explore"] = lambda a: _wrap(
    server.graph(page_path=a.get("page_path", ""), hops=a.get("hops", 3))
    if a.get("mode") == "graph"
    else server.explore_topic(topic=a.get("topic", ""), depth=a.get("depth", 2))
    if a.get("mode") == "topic"
    else server.related(page_path=a.get("page_path", ""), max_results=a.get("max_results", 10))
)

# observation_rule_health: mode 分发
_TOOL_MAP["observation_rule_health"] = lambda a: _wrap(
    server.sentinel()
    if a.get("mode") == "detail"
    else server.perception_stats(period="summary")
)

# check_point: CheckPoint 引擎（原 sys_check_point 别名误指向 observation_reflect，2026-07-23 排查修复）
_TOOL_MAP["check_point"] = lambda a: _wrap(server.check_point(scope=a.get("scope", "all")))

# page_create: schema 预验证
_TOOL_MAP["page_create"] = lambda a: _safe_create(a)

# page_update / page_append_section: 路径校验
_TOOL_MAP["page_update"] = lambda a: (
    _wrap(with_write_lock(server.update_page, resource="page")(**a))
    if a.get("path", "").startswith("丹房/")
    else _wrap(fail(ErrorCode.SCHEMA_VIOLATION, "路径不合法"))
)

_TOOL_MAP["page_append_section"] = lambda a: (
    _wrap(with_write_lock(server.append_section, resource="page")(**a))
    if a.get("page", "").startswith("丹房/")
    else _wrap(fail(ErrorCode.SCHEMA_VIOLATION, "路径不合法"))
)

# 宏工具：需要 _wrap 但不需要 write_lock（内部自行管理锁）
_MACRO_TOOLS = {
    "ingest_ripple_apply",
    "knowledge_recall",
    "session_end",
    "refine_quick",
    "get_macro_stats",
    "system_health",
    "episodic_recent",
    "episodic_search",
}
for _mt in _MACRO_TOOLS:
    _info = REGISTRY.get(_mt, {})
    _fn_name = _info.get("fn_name", _mt)

    def _make_macro_handler(fn_name):
        def handler(args):
            method = getattr(server, fn_name)
            return _wrap(method(**args))
        return handler

    _TOOL_MAP[_mt] = _make_macro_handler(_fn_name)


# ═══════════════════════════════════════════
# 别名映射（向后兼容）
# ═══════════════════════════════════════════

_ALIASES = {
    # 核心别名 — AI prompt 模板硬编码
    "kb_query": "knowledge_search",
    "kb_search": "knowledge_search",
    "mem_write": "memory_write",
    "mem_query": "memory_search",
    "sys_refresh_index": "system_refresh_index",
    # 旧命名空间别名 — 渐进废弃（保留 30 天过渡）
    "kb_analyze": "knowledge_explore",
    "kb_related": "knowledge_explore",
    "kb_stats": "knowledge_stats",
    "kb_domains": "knowledge_domains",
    "kb_pages": "knowledge_pages",
    "kb_graph": "knowledge_explore",
    "kb_hebbian": "knowledge_stats",
    "perception_inject": "knowledge_inject",
    "perception_save": "raw_save",
    "perception_recommend": "knowledge_search",
    "perception_context": "context_load",
    "obs_list": "observation_list",
    "obs_stats": "observation_stats",
    "obs_sentinel": "observation_rule_health",
    "obs_perception_stats": "observation_rule_health",
    "obs_decay": "observation_reflect",
    "reflect": "observation_reflect",
    "mem_stats": "memory_stats",
    "mem_decay": "memory_decay",
    "mem_scan_conflicts": "memory_scan_conflicts",
    "mem_feedback": "memory_feedback",
    "user_push": "user_push",
    "user_feedback": "user_feedback",
    "kar_unified": "knowledge_search",
    "kar_chain": "knowledge_search",
    "kar_explore": "knowledge_explore",
    "llm_analyze": "observation_reflect",
    "llm_summarize": "observation_reflect",
    "llm_extract": "observation_reflect",
    "sys_search_logs": "system_search_logs",
    "sys_token": "system_token",
    "sys_sop": "system_sop",
    "sys_check_status": "system_check_status",
    "sys_check_point": "check_point",
    "registry_lookup": "raw_save",
    "registry_scan": "system_registry_scan",
    "registry_stats": "knowledge_stats",

    # Phase 2 合并别名（向后兼容）
    # 注：M1/M7 合并工具 knowledge_overview / observation_dashboard 未实装，
    #     原 knowledge_stats/domains/pages 与 observation_list/stats/rule_health
    #     仍是独立 @tool，取消指向幽灵名的别名，使其解析到自身。
    "memory_lifecycle": "memory_stats",        # M5
    "memory_merge": "memory_feedback",         # M6
    "memory_archive": "memory_feedback",       # M6
    "knowledge_save": "raw_save",              # P2 rename
    "episodic_recent": "episodic_search",      # P2 合并：recent 模式通过 days 参数触发
    "skill_list": "agent_skills",              # P2 合并：agent_skills 含分组+模式过滤
}


# ═══════════════════════════════════════════
# 辅助函数
# ═══════════════════════════════════════════

def _wrap(result):
    """统一为标准错误码结构。
    
    支持三种入口：
    1. 已含 ok 字段 → 透传（已统一格式）
    2. {"success": True, ...} → 转为 ok(data)
    3. {"success": False, "error": "..."} → 转为 fail(ErrorCode.UNKNOWN, message)
    """
    if isinstance(result, dict) and result.get("ok") is not None:
        return result
    if isinstance(result, dict) and "success" in result:
        ok_val = result.pop("success")
        if ok_val:
            return ok(result)
        else:
            msg = result.pop("error", result.pop("message", "unknown error"))
            return fail(ErrorCode.UNKNOWN, msg, result)
    return ok(result)


def _safe_create(args):
    """page_create 安全入口：先 schema 校验再写入"""
    domain = args.get("domain", "07-工具与AI")
    title = args.get("title", "")
    content = args.get("content", "")
    r = validate_page_create(title, content, domain, server.vault_path)
    if not r.get("ok"):
        return r
    return _wrap(with_write_lock(server.create_page, resource="page")(**args))


def resolve_tool_name(name):
    return _ALIASES.get(name, name)


# ═══════════════════════════════════════════
# 中间件管道 — handle_request 重构
# ═══════════════════════════════════════════

def _mw_parse(state):
    """解析 MCP 方法 + 提取参数"""
    request = state["request"]
    state["method"] = request.get("method")
    state["params"] = request.get("params", {})
    state["req_id"] = request.get("id")

    if state["method"] not in ("initialize", "tools/list", "tools/call"):
        return {
            "jsonrpc": "2.0",
            "id": state["req_id"],
            "error": {"code": -32601, "message": f"Unknown method: {state['method']}"},
        }


def _mw_initialize(state):
    """initialize: 捕获端标识 → 返回协议信息"""
    if state["method"] != "initialize":
        return
    try:
        ci = state["params"].get("clientInfo") or {}
        cname = ci.get("name") or os.environ.get("LINGTAI_CLIENT_ID", "unknown")
        # 端标识归一化：connector 层桥接的端记实际名
        _CLIENT_ALIAS = {"connector:custom-mcp:lingtai-kb": "workbuddy"}
        cname = _CLIENT_ALIAS.get(cname, cname)
        server.set_client(cname, ci.get("version"))
    except Exception:
        pass
    return {
        "jsonrpc": "2.0",
        "id": state["req_id"],
        "result": {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "lingtai-knowledge-base", "version": "4.0.0"},
        },
    }


def _mw_tools_list(state):
    """tools/list: 返回预序列化缓存"""
    if state["method"] != "tools/list":
        return
    return _TOOLS_LIST_RESP_CACHE.replace('"__REQ_ID__"', json.dumps(state["req_id"]))


def _mw_resolve_alias(state):
    """解析工具别名"""
    if state["method"] != "tools/call":
        return
    raw = state["params"].get("name")
    state["tool_name"] = resolve_tool_name(raw)
    state["args"] = state["params"].get("arguments", {})


def _mw_lazy_context(state):
    """懒加载上下文 — 首次 tools/call 自动触发"""
    if state["method"] != "tools/call":
        return
    if not getattr(server, "_context_loaded", False):
        try:
            server.ensure_context()
        except Exception:
            pass


def _mw_execute(state):
    """执行工具 + 记录会话"""
    if state["method"] != "tools/call":
        return
    name = state["tool_name"]
    handler = _TOOL_MAP.get(name)
    if not handler:
        return {
            "jsonrpc": "2.0",
            "id": state["req_id"],
            "error": {"code": -32601, "message": f"Unknown tool: {name}"},
        }
    try:
        result = handler(state["args"])
        data = json.dumps(result, ensure_ascii=False, separators=(",", ":"))
        try:
            get_session_logger().record_call(name, len(data), client=getattr(server, "client", "unknown"))
        except Exception:
            pass
        return {
            "jsonrpc": "2.0",
            "id": state["req_id"],
            "result": {"content": [{"type": "text", "text": data}]},
        }
    except Exception as e:
        try:
            get_session_logger().record_call(name, 0, client=getattr(server, "client", "unknown"), outcome="error")
        except Exception:
            pass
        return {
            "jsonrpc": "2.0",
            "id": state["req_id"],
            "error": {"code": -32000, "message": str(e)},
        }


# 管道编排
_PIPELINE = [
    _mw_parse,
    _mw_initialize,
    _mw_tools_list,
    _mw_resolve_alias,
    _mw_lazy_context,
    _mw_execute,
]


def handle_request(request):
    """管道式 MCP 请求处理"""
    state = {"request": request}
    for mw in _PIPELINE:
        result = mw(state)
        if result is not None:
            return result
    # fallback（不应到达）
    return {
        "jsonrpc": "2.0",
        "id": state.get("req_id"),
        "error": {"code": -32601, "message": "Unhandled"},
    }


# ═══════════════════════════════════════════
# 主循环
# ═══════════════════════════════════════════

def main():
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
            response = handle_request(request)
            if isinstance(response, str):
                sys.stdout.write(response)
            else:
                sys.stdout.write(json.dumps(response, ensure_ascii=False))
            sys.stdout.write("\n")
            sys.stdout.flush()
        except json.JSONDecodeError:
            r = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}
            sys.stdout.write(json.dumps(r, ensure_ascii=False) + "\n")
            sys.stdout.flush()
        except Exception as e:
            import traceback

            traceback.print_exc(file=sys.stderr)
            sys.stderr.flush()
            rid = request.get("id") if isinstance(request, dict) else None
            r = {"jsonrpc": "2.0", "id": rid, "error": {"code": -32000, "message": f"Server error: {e}"}}
            sys.stdout.write(json.dumps(r, ensure_ascii=False) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--test":
        names = [t["name"] for t in _TOOLS]
        print(f"MCP Server v7 - {len(_TOOLS)} tools (decorator-driven)")
        for n in names:
            print(f"  {n}")
    else:
        main()
