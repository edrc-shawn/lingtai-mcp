# -*- coding: utf-8 -*-
"""
灵台MCP - LLM推理引擎
======================
基于LLM的推理引擎，提供智能分析、总结、关联发现等功能。

使用方法：
    from .tool.lingtai-kb.llm_reasoning import LLMReasoning
    
    llm = LLMReasoning()
    result = llm.analyze("待分析的文本")
    summary = llm.summarize("待总结的文本")
    related = llm.find_related("关键词", ["页面A", "页面B"])
"""

import os
import sys
import json
import hashlib
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional, Any

# 尝试导入requests
try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False

# Token 统计
try:
    from token_monitor import TokenMonitor
    _token_monitor = TokenMonitor()
    HAS_TOKEN_MONITOR = True
except Exception:
    HAS_TOKEN_MONITOR = False


class LLMReasoning:
    """灵台灵识 LLM推理引擎"""
    
    # 默认配置
    DEFAULT_MODEL = "deepseek-v4-flash"
    DEFAULT_API_URL = "https://token.sensenova.cn/v1"
    
    def __init__(self, config_path: str = None):
        """
        初始化LLM推理引擎

        Args:
            config_path: 配置文件路径（已废弃，使用统一配置模块）
        """
        # 使用统一配置模块 ~/.workbuddy/models.json
        try:
            from .config import get_model_config, list_available_models, load_model_registry
        except ImportError:
            from config import get_model_config, list_available_models, load_model_registry

        if config_path is not None:
            self._load_registry(config_path)
        else:
            # 从统一配置加载
            registry = load_model_registry()
            self.models = registry.get("models", [])
            self.routing_rules = registry.get("routing_rules", {})
            self.current_model_id = self.routing_rules.get("default", self.DEFAULT_MODEL)
            found = self._find_model(self.current_model_id)
            if found:
                self.config = found
            elif self.models:
                self.config = self.models[0]
            else:
                self.config = {"id": self.DEFAULT_MODEL, "url": self.DEFAULT_API_URL, "apiKey": "", "max_tokens": 4096}

        self.cache_dir = Path(os.path.join(os.path.dirname(__file__), ".cache"))
        self.cache_dir.mkdir(exist_ok=True)

    def _load_registry(self, config_path: str):
        """加载模型能力注册表"""
        if os.path.exists(config_path):
            try:
                with open(config_path, "r", encoding="utf-8") as f:
                    registry = json.load(f)
                self.models = registry.get("models", [])
                self.routing_rules = registry.get("routing_rules", {})
                self.current_model_id = self.routing_rules.get("default", "deepseek-v4-flash")
                found = self._find_model(self.current_model_id)
                if found:
                    self.config = found
                    return
                if self.models:
                    self.config = self.models[0]
                    return
            except Exception:
                pass
        self.models = []
        self.routing_rules = {"default": self.DEFAULT_MODEL}
        self.current_model_id = self.DEFAULT_MODEL
        self.config = {"id": self.DEFAULT_MODEL, "url": self.DEFAULT_API_URL, "apiKey": "", "max_tokens": 4096}

    def _find_model(self, model_id: str) -> Optional[dict]:
        """按模型ID查找配置"""
        for m in self.models:
            if m.get("id") == model_id:
                if "endpoint" not in m:
                    m["endpoint"] = m.get("url", self.DEFAULT_API_URL)
                if "apiKey" not in m:
                    m["apiKey"] = ""
                if "max_tokens" not in m:
                    m["max_tokens"] = 4096
                return m
        return None

    def _get_fallback_chain(self) -> list[dict]:
        """获取模型 fallback 链：当前配置的模型优先，其余按注册表顺序兜底。

        单模型用户：只有当前模型一个 entry，无 fallback。
        多模型用户：当前模型失败后自动遍历剩余模型重试，无需 routing_rules 配置。
        """
        seen_ids = set()
        chain = []

        # 当前配置的模型优先
        if self.config and self.config.get("id"):
            chain.append(self.config)
            seen_ids.add(self.config["id"])

        # 其余 models 依次补充
        for m in self.models:
            mid = m.get("id")
            if mid and mid not in seen_ids:
                chain.append(m)
                seen_ids.add(mid)

        return chain

    def route_for_task(self, task: str) -> str:
        """根据任务类型选择最优模型"""
        task_map = self.routing_rules.get("task_routing", {})
        model_id = task_map.get(task, self.routing_rules.get("default", self.current_model_id))
        model = self._find_model(model_id)
        if model:
            self.config = model
            self.current_model_id = model_id
        else:
            fallback_id = self.routing_rules.get("fallback", self.routing_rules.get("default"))
            self.config = self._find_model(fallback_id) or self.config
            self.current_model_id = self.config.get("id", "unknown")
        return self.current_model_id

    def _get_cache_key(self, text: str, operation: str) -> str:
        """生成缓存键"""
        content = f"{operation}:{text}"
        return hashlib.md5(content.encode()).hexdigest()
    
    def _get_cached(self, cache_key: str) -> Optional[str]:
        """获取缓存"""
        cache_file = self.cache_dir / f"{cache_key}.json"
        if cache_file.exists():
            try:
                with open(cache_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    # 检查缓存是否过期（24小时）
                    if datetime.now().timestamp() - data.get("timestamp", 0) < 86400:
                        return data.get("result")
            except Exception:
                pass
        return None
    
    def _set_cached(self, cache_key: str, result: str):
        """设置缓存"""
        cache_file = self.cache_dir / f"{cache_key}.json"
        try:
            with open(cache_file, "w", encoding="utf-8") as f:
                json.dump({
                    "result": result,
                    "timestamp": datetime.now().timestamp()
                }, f, ensure_ascii=False)
        except Exception:
            pass
    
    def _call_llm(self, prompt: str, max_tokens: int = 2048, action: str = "query") -> str:
        """
        调用LLM API（带自动 fallback）

        依次尝试 fallback 链中的每个模型，全部失败才报错。
        单模型用户只有当前模型一个 entry，行为不变。

        Args:
            prompt: 提示词
            max_tokens: 最大生成token数
            action: 操作类型（analyze/summarize/search/query）

        Returns:
            str: LLM返回的文本
        """
        if not HAS_REQUESTS:
            return "[错误: 需要安装 requests 库 - pip install requests]"

        candidates = self._get_fallback_chain()
        last_error = ""
        tried = []

        for model_config in candidates:
            if not model_config.get("apiKey"):
                last_error = "未配置API密钥"
                tried.append(model_config.get("id", "unknown"))
                continue

            try:
                headers = {
                    "Authorization": f"Bearer {model_config['apiKey']}",
                    "Content-Type": "application/json"
                }

                data = {
                    "model": model_config["id"],
                    "messages": [
                        {"role": "user", "content": prompt}
                    ],
                    "max_tokens": max_tokens,
                    "temperature": 0.7
                }

                endpoint = model_config.get("endpoint", "")
                if not endpoint:
                    base_url = model_config.get("url", "https://token.sensenova.cn/v1")
                    endpoint = f"{base_url}/chat/completions"

                response = requests.post(
                    endpoint,
                    headers=headers,
                    json=data,
                    timeout=30
                )

                if response.status_code == 200:
                    result = response.json()

                    # 如果用了非当前模型，静默切过去
                    if model_config["id"] != self.current_model_id:
                        self.config = model_config
                        self.current_model_id = model_config["id"]

                    # 记录 Token 消耗
                    if HAS_TOKEN_MONITOR:
                        try:
                            usage = result.get("usage", {})
                            input_tokens = usage.get("prompt_tokens", 0)
                            output_tokens = usage.get("completion_tokens", 0)
                            if input_tokens or output_tokens:
                                _token_monitor.record_usage(
                                    action=action,
                                    model=model_config["id"],
                                    input_tokens=input_tokens,
                                    output_tokens=output_tokens,
                                    saved_tokens=0
                                )
                        except Exception:
                            pass
                    return result["choices"][0]["message"]["content"]
                else:
                    last_error = f"[API错误: {response.status_code}]"
                    tried.append(f"{model_config['id']}({response.status_code})")

            except Exception as e:
                last_error = f"[调用错误: {str(e)}]"
                tried.append(f"{model_config['id']}(异常)")

        return f"{last_error} 已尝试: {', '.join(tried)}"
    
    def analyze(self, text: str) -> dict:
        """LLM文本分析"""
        self.route_for_task("analyze")
        """
        分析文本
        
        Args:
            text: 待分析的文本
        
        Returns:
            dict: 分析结果
        """
        # 检查缓存
        cache_key = self._get_cache_key(text, "analyze")
        cached = self._get_cached(cache_key)
        if cached:
            try:
                return json.loads(cached)
            except Exception:
                pass
        
        prompt = f"""请分析以下文本，返回JSON格式的分析结果：

文本：
{text[:2000]}

请返回以下JSON格式（不要包含其他内容）：
{{
    "summary": "1-2句核心概括",
    "keywords": ["关键词1", "关键词2", "关键词3"],
    "key_points": ["要点1", "要点2", "要点3"],
    "category": "分类（如：技术/哲学/商业/社会/其他）",
    "sentiment": "情感倾向（正面/中性/负面）",
    "complexity": "复杂度（简单/中等/复杂）"
}}"""
        
        result_text = self._call_llm(prompt, action="analyze")
        
        try:
            # 尝试提取JSON
            if "```json" in result_text:
                result_text = result_text.split("```json")[1].split("```")[0]
            elif "```" in result_text:
                result_text = result_text.split("```")[1].split("```")[0]
            
            result = json.loads(result_text.strip())
            self._set_cached(cache_key, json.dumps(result, ensure_ascii=False))
            return result
        except Exception:
            return {
                "summary": result_text[:200],
                "keywords": [],
                "key_points": [],
                "category": "其他",
                "sentiment": "中性",
                "complexity": "中等"
            }
    
    def summarize(self, text: str, max_length: int = 200) -> str:
        """LLM文章总结"""
        self.route_for_task("summarize")
        """
        总结文本
        
        Args:
            text: 待总结的文本
            max_length: 最大长度
        
        Returns:
            str: 总结结果
        """
        # 检查缓存
        cache_key = self._get_cache_key(text, "summarize")
        cached = self._get_cached(cache_key)
        if cached:
            return cached
        
        prompt = f"""请用1-2句话总结以下文本的核心内容（不超过{max_length}字）：

文本：
{text[:3000]}

总结："""
        
        result = self._call_llm(prompt, max_tokens=256, action="summarize")
        self._set_cached(cache_key, result)
        return result
    
    def find_related(self, keyword: str, candidates: List[str]) -> List[dict]:
        """
        找到与关键词相关的候选内容
        
        Args:
            keyword: 关键词
            candidates: 候选内容列表
        
        Returns:
            list: 相关内容列表（按相关度排序）
        """
        if not candidates:
            return []
        
        # 限制候选数量
        candidates = candidates[:10]
        
        candidates_text = "\n".join([f"{i+1}. {c[:100]}" for i, c in enumerate(candidates)])
        
        prompt = f"""关键词：{keyword}

候选内容：
{candidates_text}

请分析每个候选内容与关键词的相关度，返回JSON格式（不要包含其他内容）：
{{
    "results": [
        {{"index": 1, "relevance": 0.9, "reason": "相关原因"}},
        {{"index": 2, "relevance": 0.7, "reason": "相关原因"}}
    ]
}}

按相关度从高到低排序，只返回相关度>=0.5的结果。"""
        
        result_text = self._call_llm(prompt, action="search")
        
        try:
            if "```json" in result_text:
                result_text = result_text.split("```json")[1].split("```")[0]
            elif "```" in result_text:
                result_text = result_text.split("```")[1].split("```")[0]
            
            result = json.loads(result_text.strip())
            return result.get("results", [])
        except Exception:
            return []
    
    def extract_insights(self, text: str) -> dict:
        """LLM洞察提取"""
        self.route_for_task("extract")
        """
        提取文本中的洞察
        
        Args:
            text: 待分析的文本
        
        Returns:
            dict: 洞察结果
        """
        prompt = f"""请从以下文本中提取核心洞察，返回JSON格式：

文本：
{text[:2000]}

请返回以下JSON格式（不要包含其他内容）：
{{
    "core_insight": "核心洞察（1句话）",
    "implications": ["启示1", "启示2", "启示3"],
    "actionable": ["可行动项1", "可行动项2"],
    "connections": ["可能的关联1", "可能的关联2"]
}}"""
        
        result_text = self._call_llm(prompt, action="analyze")
        
        try:
            if "```json" in result_text:
                result_text = result_text.split("```json")[1].split("```")[0]
            elif "```" in result_text:
                result_text = result_text.split("```")[1].split("```")[0]
            
            return json.loads(result_text.strip())
        except Exception:
            return {
                "core_insight": result_text[:200],
                "implications": [],
                "actionable": [],
                "connections": []
            }
    
    def suggest_links(self, page_content: str, other_pages: List[dict]) -> List[dict]:
        """
        建议页面间的链接
        
        Args:
            page_content: 当前页面内容
            other_pages: 其他页面列表 [{"path": "...", "title": "...", "summary": "..."}]
        
        Returns:
            list: 链接建议
        """
        if not other_pages:
            return []
        
        pages_text = "\n".join([
            f"- {p.get('title', '')}: {p.get('summary', '')[:100]}"
            for p in other_pages[:10]
        ])
        
        prompt = f"""当前页面内容摘要：
{page_content[:1000]}

其他页面：
{pages_text}

请分析当前页面与哪些其他页面有关联，返回JSON格式（不要包含其他内容）：
{{
    "suggestions": [
        {{"title": "页面标题", "reason": "关联原因", "strength": "强/中/弱"}}
    ]
}}

只返回有实际关联的页面（至少需要有共同概念或主题）。"""
        
        result_text = self._call_llm(prompt, action="search")
        
        try:
            if "```json" in result_text:
                result_text = result_text.split("```json")[1].split("```")[0]
            elif "```" in result_text:
                result_text = result_text.split("```")[1].split("```")[0]
            
            result = json.loads(result_text.strip())
            return result.get("suggestions", [])
        except Exception:
            return []


# 便捷函数
def create_llm_reasoning(config_path: str = None) -> LLMReasoning:
    """创建LLM推理引擎实例"""
    return LLMReasoning(config_path)


if __name__ == "__main__":
    # 测试
    llm = LLMReasoning()
    
    print("LLM推理引擎测试")
    print("=" * 50)
    
    # 测试分析
    text = "Python是一种高级编程语言，广泛应用于数据分析、机器学习、Web开发等领域。"
    result = llm.analyze(text)
    print(f"\n分析结果: {result}")
    
    # 测试总结
    summary = llm.summarize(text)
    print(f"\n总结结果: {summary}")
    
    print("\n测试完成")
