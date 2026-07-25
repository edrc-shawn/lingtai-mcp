# -*- coding: utf-8 -*-
"""
灵台 MCP Server 标准化错误码

统一返回结构 {ok, code?, message?, data?}。
所有新工具应使用 ok() / fail() 包装返回值。
"""
from enum import Enum
from typing import Any


class ErrorCode(str, Enum):
    """灵台业务错误码——所有工具统一使用此枚举"""
    KB_NOT_FOUND = "kb_not_found"
    SCHEMA_VIOLATION = "schema_violation"
    MEMORY_NOT_FOUND = "memory_not_found"
    PROFILE_KEY_MISSING = "profile_key_missing"
    REGISTRY_CONFLICT = "registry_conflict"
    LLM_UNAVAILABLE = "llm_unavailable"
    AGENT_NOT_AUTHORIZED = "agent_not_authorized"  # 预留：当前无触发场景
    RATE_LIMITED = "rate_limited"
    UNKNOWN = "unknown"


def ok(data: Any = None) -> dict:
    """成功响应——返回 {ok: True, data: ...}"""
    return {"ok": True, "data": data}


def fail(code: ErrorCode, message: str = "", data: Any = None) -> dict:
    """失败响应——返回 {ok: False, code: ..., message: ..., data?: ...}"""
    result: dict = {"ok": False, "code": code.value}
    if message:
        result["message"] = message
    if data is not None:
        result["data"] = data
    return result