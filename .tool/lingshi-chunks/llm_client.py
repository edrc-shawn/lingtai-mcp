# -*- coding: utf-8 -*-
"""
LLM 客户端 — 轻量 OpenAI-compatible API 调用

用于 lingshi-chunks 的 LLM 提取管道。
复用灵台现有的 api_keys.json 配置。

支持的 provider（按推荐顺序）：
  - glm: GLM-4，质量好（默认）
  - siliconflow: 备选
  - sensenova: 备选
"""

import json
import os
import sys
import requests
from typing import List, Optional
from pathlib import Path


class LLMClient:
    """轻量 OpenAI-compatible API 客户端。"""

    def __init__(self, provider: str = "glm"):
        config = self._load_config(provider)
        self.api_key = config["key"]
        self.base_url = config.get("endpoint", "https://open.bigmodel.cn/api/paas/v4").rstrip("/")
        self.model = config.get("model", "glm-4-flash")
        self.timeout = 120

    def _chat_url(self) -> str:
        """构建 chat/completions URL，兼容 endpoint 含或不含完整路径。"""
        if self.base_url.endswith("/chat/completions"):
            return self.base_url
        return f"{self.base_url}/chat/completions"

    def _load_config(self, provider: str) -> dict:
        """从 api_keys.json 加载配置。"""
        # 先在 lingshi-chunks 同级找
        candidates = [
            Path(__file__).parent.parent.parent / "config" / "api_keys.json",
            Path(__file__).parent.parent / "config" / "api_keys.json",
        ]
        for p in candidates:
            if p.exists():
                with open(p, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if provider in data:
                    return data[provider]
                # 回退到 glm
                if "glm" in data:
                    return data["glm"]
                # 回退到第一个
                for k, v in data.items():
                    if "key" in v and "endpoint" in v:
                        return v
                raise KeyError(f"无可用 provider，配置中 keys: {list(data.keys())}")

        raise FileNotFoundError(f"未找到 api_keys.json，尝试路径: {candidates}")

    def chat(self, messages: List[dict], temperature: float = 0.3) -> str:
        """调用 chat/completions。

        Args:
            messages: [{"role": "system", "content": "..."}, {"role": "user", "content": "..."}]
            temperature: 提取场景应较低（0.1-0.3）

        Returns:
            AI 回复内容
        """
        url = self._chat_url()
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": 4096,
        }

        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=self.timeout)
            resp.raise_for_status()
            data = resp.json()
            return data["choices"][0]["message"]["content"]
        except requests.exceptions.Timeout:
            raise TimeoutError(f"LLM 请求超时（{self.timeout}s）")
        except Exception as e:
            raise RuntimeError(f"LLM 调用失败: {e}")

    def extract_structured(self, system_prompt: str, user_content: str) -> List[dict]:
        """结构化提取：调用 LLM 并解析 JSON 数组响应。

        Args:
            system_prompt: 系统提示（身份 + 指令）
            user_content: 用户内容（Markdown 笔记全文）

        Returns:
            解析后的 chunk dict 列表
        """
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ]
        raw = self.chat(messages, temperature=0.1)

        # 从回复中提取 JSON 数组
        return self._parse_json_response(raw)

    def _parse_json_response(self, text: str) -> List[dict]:
        """从 LLM 回复中提取并解析 JSON 数组。

        处理常见格式：
        - 纯 JSON 数组：[...]
        - 被 ```json ... ``` 包裹
        - 被 ``` ... ``` 包裹
        """
        text = text.strip()

        # 尝试 JSON 块提取
        for marker in ["```json", "```JSON", "```"]:
            if marker in text:
                parts = text.split(marker, 1)
                if len(parts) >= 2:
                    text = parts[1]
                    if "```" in text:
                        text = text.split("```", 1)[0]
                    text = text.strip()
                    break

        # 尝试 JSON 数组
        if text.startswith("["):
            try:
                data = json.loads(text)
                if isinstance(data, list):
                    return data
            except json.JSONDecodeError:
                pass

        # 尝试 JSON 对象（单挑）
        if text.startswith("{"):
            try:
                data = json.loads(text)
                return [data]
            except json.JSONDecodeError:
                pass

        # 尝试用正则找第一个 json block
        import re
        match = re.search(r"\[\s*\{.*\}\s*\]", text, re.DOTALL)
        if match:
            try:
                data = json.loads(match.group())
                if isinstance(data, list):
                    return data
            except json.JSONDecodeError:
                pass

        print(f"⚠ LLM 返回无法解析: {text[:200]}...")
        return []


def get_llm_client(provider: str = "glm") -> LLMClient:
    """工厂函数 — 获取 LLM 客户端实例。"""
    return LLMClient(provider)


# ── 提取 prompt 模板 ──

SYSTEM_PROMPT = """你是一位知识结构化专家。你的任务是从一篇 Markdown 笔记中提取多个语义原子，每条原子作为一条 JSON 记录输出。

## 核心要求

1. content 字段必须是**自然语言片段**，不是三元组，不是列表。写一段人类（以及 LLM）能直接理解的浓缩描述。

2. 每一条 chunk 必须**语义自包含**——不依赖其他 chunk 的上下文。

3. 不贴原文——用你自己的话高密度重述。保留具体数字、日期、名称。

4. 判断 chunk_type：
   - concept：给术语或思想下定义（XX是什么）
   - claim：作者表达的观点或主张（我认为……）
   - summary：对较长内容的浓缩（本文讲了……）
   - rule：可操作的准则（遇X时应做Y）
   - question：提出问题（XX是怎么回事？）
   - reference：引用外部资料（引用论文/链接）

5. quality.confidence：0.0~1.0，你对自己提取的准确度有多确定。
   如果原文明确写了的 → 0.9+
   如果原文隐含但你能合理推断的 → 0.6-0.8
   如果你不太确定的 → 0.3-0.5
   不要给低于 0.3 的——不确定就别提这条。

6. relations：只提取原文中显式提到或明显隐含的关系。
   不要脑补不存在的关联。不确定就不填 relations。注意用中文标签。

7. retrieval_meta.keywords：提取 3-8 个最能代表本条知识的关键词。
   retrieval_meta.query_rewrite_anchor：写一句假设的搜索问题。

8. title 不要超过 30 字，带上主谓宾。

9. 不强制每条笔记产出固定数量的 chunk。最少 1 条，最多不限。
   但太碎（<30 字）的 chunk 不要提——合并到父 chunk 的 content 中。

10. 如果笔记太短或太随机，输出空数组 []。

## 输出格式

必须是合法的 JSON 数组。每条元素格式：
{
  "title": "字符串",
  "chunk_type": "concept|claim|summary|rule|question|reference",
  "domain": "域路径",
  "tags": ["标签1", "标签2"],
  "content": "浓缩后的自然语言正文",
  "quality": { "grade": "下品|中品|上品", "confidence": 0.0~1.0 },
  "relations": [{"target_id": null, "type": "链接|引用|依赖|派生|归属", "label": "关系名称"}],
  "retrieval_meta": { "keywords": ["关键词"], "query_rewrite_anchor": "假设的搜索问题" }
}

只输出 JSON 数组，不要其他文字。"""


def build_user_prompt(title: str, domain: str, grade: str, body: str) -> str:
    """构建用户侧 prompt。"""
    return f"""请从以下 Markdown 笔记中提取结构化知识。

笔记信息：
- 标题：{title}
- 域：{domain}
- 当前品级：{grade}

---笔记开始---
{body}
---笔记结束---"""