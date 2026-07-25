# -*- coding: utf-8 -*-
"""共享全局变量 — 被所有 mixin 模块引用"""

import os

# 配置
VAULT_PATH = os.environ.get("LINGTAI_VAULT", r".")

# Tavily 搜索配置
import sys as _sys
_sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), '..', 'config'))
import importlib
_config_mod = importlib.import_module('api_keys')
_get_key = _config_mod.get
TAVILY_API_KEY = _get_key("tavily")
TAVILY_API_URL = "https://api.tavily.com/search"
TAVILY_MONTHLY_LIMIT = 1000
_tavily_month = ""
_tavily_count = 0

# AnySearch 配置（优先于 Tavily，1000次/天免费）
ANYSEARCH_API_URL = os.environ.get("ANYSEARCH_API_URL", "https://api.anysearch.com/v1")
ANYSEARCH_API_KEY = os.environ.get("ANYSEARCH_API_KEY", "")

# 会话日志器
_session_logger = None  # 在 server.py 初始化后注入


def get_session_logger():
    """延迟获取（避免循环导入）"""
    global _session_logger
    if _session_logger is None:
        from session_tracker import _session_logger as _sl
        _session_logger = _sl
    return _session_logger
