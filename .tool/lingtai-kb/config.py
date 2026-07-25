# -*- coding: utf-8 -*-
"""统一配置模块 — 汇聚所有 API 密钥和模型注册表。

用法:
    from config import get_api_key, get_model_config, find_model

    # 获取服务密钥 (来自 api_keys.json)
    tavily_key = get_api_key("tavily")
    agnes_key = get_api_key("agnes")
    github_token = get_api_key("github", "token")

    # 获取模型配置 (来自 ~/.workbuddy/models.json)
    model = get_model_config("deepseek-v4-flash")
    model_id = find_model_for_task("analyze")
"""

import json
import os
from pathlib import Path
from typing import Optional

# ── 路径 ──────────────────────────────────────────────────────────────
_THIS_DIR = Path(__file__).parent
_VAULT = Path(os.environ.get("LINGTAI_VAULT", _THIS_DIR.parent.parent))
_API_KEYS_PATH = _VAULT / ".tool" / "config" / "api_keys.json"
_MODELS_JSON = Path(os.path.expanduser("~")) / ".workbuddy" / "models.json"


# ── 服务密钥 (来自 api_keys.json) ──────────────────────────────────────
_api_keys_cache = None

def _load_api_keys() -> dict:
    global _api_keys_cache
    if _api_keys_cache is None:
        if _API_KEYS_PATH.exists():
            with open(_API_KEYS_PATH, "r", encoding="utf-8") as f:
                _api_keys_cache = json.load(f)
        else:
            _api_keys_cache = {}
    return _api_keys_cache


def get_api_key(service: str, field: str = "key") -> str:
    """获取指定服务的 API Key 或其他字段。

    Args:
        service: 服务名，如 "agnes", "tavily", "sensenova", "github", "gitee", "glm", "siliconflow"
        field: 字段名，默认 "key"（GitHub/Gitee 为 "token"）

    Returns:
        密钥字符串

    Raises:
        KeyError: 服务或字段不存在
    """
    data = _load_api_keys()
    if service not in data:
        raise KeyError(f"未知服务: {service}。可用: {list(data.keys())}")
    svc = data[service]
    if field not in svc:
        raise KeyError(f"字段 '{field}' 不在服务 '{service}' 中。可用: {list(svc.keys())}")
    return svc[field]


# ── 模型注册表 (来自 ~/.workbuddy/models.json) ──────────────────────────
_models_cache = None

def _load_models() -> dict:
    global _models_cache
    if _models_cache is None:
        if _MODELS_JSON.exists():
            try:
                with open(_MODELS_JSON, "r", encoding="utf-8") as f:
                    _models_cache = json.load(f)
            except Exception:
                _models_cache = {"models": []}
        else:
            _models_cache = {"models": []}
    return _models_cache


def get_model_config(model_id: str) -> Optional[dict]:
    """按模型 ID 查找完整配置（含 apiKey、endpoint 等）。

    Args:
        model_id: 模型 ID，如 "deepseek-v4-flash", "sensenova-6.7-flash-lite"

    Returns:
        模型配置字典，或 None（未找到）
    """
    registry = _load_models()
    for m in registry.get("models", []):
        if m.get("id") == model_id or m.get("name") == model_id:
            return m
    return None


def find_model_for_task(task_type: str, default: str = "deepseek-v4-flash") -> str:
    """按任务类型选择最优模型。

    Args:
        task_type: 任务类型，如 "analyze", "summarize", "extract", "reflect", "quick"
        default: 默认模型 ID

    Returns:
        模型 ID
    """
    registry = _load_models()
    rules = registry.get("routing_rules", {})
    task_map = rules.get("task_routing", {})
    return task_map.get(task_type, rules.get("default", default))


def list_available_models() -> list[dict]:
    """列出所有可用模型。"""
    registry = _load_models()
    return registry.get("models", [])


def list_available_services() -> list[str]:
    """列出 api_keys.json 中所有已配置的服务。"""
    return list(_load_api_keys().keys())


def load_model_registry() -> dict:
    """加载完整模型注册表（供需要原始数据的模块使用）。

    兼容 JSON 数组格式（WorkBuddy 原生格式）和字典格式（含 routing_rules）。
    """
    data = _load_models()
    if isinstance(data, list):
        return {"models": data, "routing_rules": {}}
    return data


# ── 兼容旧接口 ─────────────────────────────────────────────────────────
# 让 from api_keys import get 仍能工作（过渡期支持）
def get(service: str, field: str = "key") -> str:
    """兼容旧版 config.api_keys.get() 接口"""
    return get_api_key(service, field)