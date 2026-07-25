"""统一 API Key 加载器。所有脚本通过此模块读取密钥，不硬编码。"""
import json, os

_CONFIG_PATH = os.path.join(os.path.dirname(__file__), "api_keys.json")
_cache = None

def _load():
    global _cache
    if _cache is None:
        with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
            _cache = json.load(f)
    return _cache

def get(service: str, field: str = "key") -> str:
    """获取指定服务的 API Key 或其他字段。
    
    用法:
        from config.api_keys import get
        agnes_key = get("agnes")           # 等同 get("agnes", "key")
        endpoint = get("agnes", "endpoint")
        tavily_key = get("tavily")
    """
    data = _load()
    if service not in data:
        raise KeyError(f"Unknown service: {service}. Available: {list(data.keys())}")
    svc = data[service]
    if field not in svc:
        raise KeyError(f"Field '{field}' not in service '{service}'. Available: {list(svc.keys())}")
    return svc[field]
