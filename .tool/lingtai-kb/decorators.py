# -*- coding: utf-8 -*-
"""
灵台 MCP 工具装饰器 — 统一注册，替代 tools.py 双注册

Phase 1 地基：每个 @tool() 装饰器自动注册元数据（名称/签名/权限/分类），
router.py 从 REGISTRY 自省构建 _TOOL_MAP，消除"改了方法签名忘了改 tools.py"的脱耦风险。

设计原则：
- 装饰器不包装函数（保持原始签名，方便 IDE 跳转和调试）
- 元数据从类型注解 + docstring 自动生成
- write=True 的工具在 router 层自动加 with_write_lock + _wrap
- system=True 的工具注册但不暴露给 AI（管线专用）
"""

import inspect
from typing import get_type_hints

# ═══════════════════════════════════════════
# 全局注册表
# ═══════════════════════════════════════════

REGISTRY: dict = {}  # name → {fn, readonly, destructive, category, description, schema, system}


# ═══════════════════════════════════════════
# JSON Schema 类型映射
# ═══════════════════════════════════════════

_PY_TO_JSON = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
    list: "array",
    dict: "object",
}


# ═══════════════════════════════════════════
# 装饰器
# ═══════════════════════════════════════════

def tool(
    *,
    readonly: bool = False,
    write: bool = False,
    destructive: bool = False,
    category: str = "general",
    desc: str = "",
    system: bool = False,
    name: str = "",
):
    """注册为 MCP 工具

    Args:
        readonly: 只读操作，标注 readOnlyHint
        write: 写操作，自动加 with_write_lock + _wrap 错误包装
        destructive: 客户端 destructiveHint（仅真正破坏性操作设为 True，如 system_restart）
        category: 功能分类（knowledge/memory/refine/observation/health/system/macro/pipeline）
        desc: 工具说明（空则从 docstring 第一行自动提取）
        system: 管线专用工具，注册但不暴露给 AI
        name: MCP 工具名（空则用方法名；方法名与工具名不同时必填）
    """
    def deco(fn):
        method_name = fn.__name__
        tool_name = name or method_name

        # 生成 JSON Schema（从类型注解 + docstring）
        schema = _build_schema(fn)

        # 提取描述
        description = desc
        if not description:
            raw = (fn.__doc__ or "").strip()
            description = raw.split("\n")[0].strip() if raw else name

        REGISTRY[tool_name] = {
            "fn_name": method_name,
            "readonly": readonly,
            "write": write,
            "destructive": destructive,
            "category": category,
            "description": description,
            "schema": schema,
            "system": system,
        }
        return fn  # 不包装，保持原始函数
    return deco


# ═══════════════════════════════════════════
# Schema 生成
# ═══════════════════════════════════════════

def _build_schema(fn) -> dict:
    """从函数签名 + 类型注解 + docstring 生成 JSON Schema"""
    try:
        hints = get_type_hints(fn)
    except Exception:
        hints = {}

    sig = inspect.signature(fn)
    properties = {}
    required = []

    param_descs = _parse_docstring_params(fn.__doc__ or "")

    for pname, param in sig.parameters.items():
        if pname == "self":
            continue

        # 类型推导：优先类型注解 → 默认值类型 → string
        ann = hints.get(pname, param.annotation)
        json_type = _type_to_json(ann)

        prop = {"type": json_type}

        # 默认值
        if param.default is not inspect.Parameter.empty:
            prop["default"] = param.default
        else:
            required.append(pname)

        # docstring 中的参数说明
        if pname in param_descs:
            prop["description"] = param_descs[pname]

        # 枚举值（从类型注解的 Literal 提取）
        enum_vals = _extract_enum(ann)
        if enum_vals:
            prop["enum"] = enum_vals

        properties[pname] = prop

    schema = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    return schema


def _type_to_json(annotation) -> str:
    """Python 类型 → JSON Schema type"""
    if annotation is inspect.Parameter.empty:
        return "string"
    origin = getattr(annotation, "__origin__", None)
    if origin is list:
        return "array"
    if origin is dict:
        return "object"
    return _PY_TO_JSON.get(annotation, "string")


def _extract_enum(annotation) -> list:
    """提取 Literal 类型中的枚举值"""
    origin = getattr(annotation, "__origin__", None)
    if origin is None:
        return []
    # typing.Literal
    if hasattr(origin, "__name__") and origin.__name__ == "Literal":
        return list(getattr(annotation, "__args__", []))
    # 非 Literal 但可能有 __args__
    return []


def _parse_docstring_params(doc: str) -> dict:
    """从 Google-style docstring 提取 Args: 的 {name}: {desc}"""
    result = {}
    lines = doc.split("\n")
    in_args = False
    for line in lines:
        stripped = line.strip()
        if stripped.lower().startswith("args:"):
            in_args = True
            continue
        if in_args:
            # 遇到下一个段落标题就停
            if stripped and (stripped.lower().startswith("returns:")
                             or stripped.lower().startswith("yields:")
                             or stripped.lower().startswith("raises:")):
                break
            if ":" in stripped:
                parts = stripped.split(":", 1)
                pname = parts[0].strip()
                pdesc = parts[1].strip()
                if pname and not pname[0].isupper() and not pname.startswith("{"):
                    result[pname] = pdesc
    return result


# ═══════════════════════════════════════════
# 导出（供 router.py 使用）
# ═══════════════════════════════════════════

# 将过细的 category 收敛为 7 个主域，帮助 AI 客户端按域预筛选
_CATEGORY_NORMALIZE = {
    "lingshi": "memory",
    "raw": "refine",
    "concept": "health",
    "maintenance": "refine",
    "macro": "pipeline",
    "output": "pipeline",
}


def export_tools_list() -> list:
    """导出为 MCP tools/list 格式（仅非 system 工具）"""
    tools = []
    for name in sorted(REGISTRY):
        info = REGISTRY[name]
        if info.get("system"):
            continue
        raw_cat = info.get("category", "general")
        category = _CATEGORY_NORMALIZE.get(raw_cat, raw_cat)
        entry = {
            "name": name,
            "description": info["description"],
            "inputSchema": info["schema"],
            "category": category,
        }
        ann = {}
        if info["readonly"]:
            ann["readOnlyHint"] = True
        if info["destructive"]:
            ann["destructiveHint"] = True
        if ann:
            entry["annotations"] = ann
        tools.append(entry)
    return tools


def get_registry_summary() -> list:
    """返回注册表摘要（名称/分类/权限）"""
    return [
        {
            "name": info["fn_name"],
            "category": info["category"],
            "readonly": info["readonly"],
            "write": info["write"],
            "system": info["system"],
        }
        for info in sorted(REGISTRY.values(), key=lambda x: (x["category"], x["fn_name"]))
    ]
