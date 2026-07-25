# -*- coding: utf-8 -*-
"""
灵台结构化索引引擎 — core 层
=============================
从丹房页 Markdown 提取结构化知识块（chunk），文件级 JSON 存储。

设计原则：
- 消费端驱动：从"LLM 检索时需要什么"倒推格式
- Markdown 独裁：JSON 是从 Markdown 异步派生的只读缓存
- 语义自包含：每条 chunk 带够元数据，LLM 不查原文也能理解

存储结构：
  .lingtai/structured-index/
  ├── chunks/              ← 每条 chunk 一个 JSON 文件
  │   ├── chunk_7a9f.json
  │   └── ...
  ├── index.json           ← 全量索引（chunk_id → 元数据）
  └── manifest.json        ← 版本信息 + 统计

用法：
    from core import StructuredIndex
    si = StructuredIndex("/path/to/lingtai")
    si.extract("丹房/00-XX/xxx.md")  # 从单篇丹房页提取
    si.reindex_all()                 # 全量重建
    results = si.search("递归")      # 朴素搜索
"""

import json
import os
import re
import hashlib
import time
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional, Tuple, Any


# ── Schema 版本 ──
SCHEMA_VERSION = "1.0"

# ── 语义原子类型 ──
CHUNK_TYPES = ("concept", "claim", "summary", "rule", "question", "reference")

# ── 品级映射 ──
GRADE_MAP = {"下品": 1, "中品": 2, "上品": 3}

# ── 关系类型 ──
EDGE_TYPES = ("链接", "引用", "依赖", "派生", "归属")


class StructuredChunk(dict):
    """单条结构化知识记录的字典封装，提供字段校验。"""

    REQUIRED_FIELDS = {"id", "chunk_type", "title", "domain", "content"}
    OPTIONAL_FIELDS = {
        "tags", "quality", "provenance", "relations",
        "retrieval_meta", "content_hash", "schema_version"
    }

    def __init__(self, data: dict):
        super().__init__(data)
        self._validate()

    def _validate(self):
        missing = self.REQUIRED_FIELDS - set(self.keys())
        if missing:
            raise ValueError(f"chunk 缺少必填字段: {missing}")
        if self["chunk_type"] not in CHUNK_TYPES:
            raise ValueError(f"chunk_type 无效: {self['chunk_type']}")
        if len(self.get("title", "")) > 30:
            raise ValueError(f"title 超过 30 字: {self['title']}")

    @staticmethod
    def new(
        chunk_type: str,
        title: str,
        domain: str,
        content: str,
        tags: list = None,
        grade: str = "下品",
        confidence: float = 0.5,
        keywords: list = None,
        query_rewrite_anchor: str = "",
        source_node_id: str = "",
        source_node_title: str = "",
    ) -> "StructuredChunk":
        """创建一条新 chunk（自动生成 id + 时间戳）。"""
        now = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
        raw = {
            "id": _make_id(content),
            "schema_version": SCHEMA_VERSION,
            "chunk_type": chunk_type,
            "title": title[:30],
            "domain": domain,
            "tags": tags or [],
            "content": content.strip(),
            "content_hash": hashlib.sha256(content.encode("utf-8")).hexdigest(),
            "quality": {
                "grade": grade,
                "confidence": round(confidence, 2),
                "correction_count": 0,
                "last_correction_at": None,
            },
            "provenance": {
                "source_node_id": source_node_id,
                "source_node_title": source_node_title,
                "source_type": "丹房页",
                "created_at": now,
                "updated_at": now,
            },
            "relations": [],
            "retrieval_meta": {
                "parent_chunk_id": None,
                "child_chunk_ids": [],
                "keywords": keywords or [],
                "query_rewrite_anchor": query_rewrite_anchor,
            },
        }
        return StructuredChunk(raw)

    @property
    def chunk_id(self) -> str:
        return self["id"]

    @property
    def grade_score(self) -> int:
        return GRADE_MAP.get(self.get("quality", {}).get("grade", "下品"), 1)

    def to_dict(self) -> dict:
        return dict(self)

    def to_search_snippet(self) -> str:
        """生成检索摘要（供 CLI/MCP 展示）。"""
        q = self.get("quality", {})
        return (
            f"[{self['chunk_type']}] {self['title']} "
            f"(品级:{q.get('grade','下品')} 置信度:{q.get('confidence',0):.2f})"
        )


# ── 辅助函数 ──

def _make_id(content: str) -> str:
    """基于内容生成确定性 id（同名内容产出同 id，天然去重）。"""
    prefix = hashlib.md5(content.encode("utf-8")).hexdigest()[:8]
    return f"chunk_{prefix}"


def _read_markdown(path: str) -> Tuple[str, dict]:
    """读取 Markdown 文件，分离 frontmatter 和正文。"""
    with open(path, "r", encoding="utf-8") as f:
        text = f.read()
    fm = {}
    body = text
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            for line in parts[1].strip().split("\n"):
                if ":" in line:
                    k, v = line.split(":", 1)
                    fm[k.strip()] = v.strip().strip('"').strip("'")
            body = parts[2].strip()
    return body, fm


def _parse_tags(body: str) -> list:
    """从正文中提取 Obsidian 风格的 #标签。"""
    return list(set(re.findall(r"#([\w\u4e00-\u9fff\-_]+)", body)))


# ── 存储层 ──

CHUNKS_DIR = "chunks"
INDEX_FILE = "index.json"
MANIFEST_FILE = "manifest.json"


class ChunkStore:
    """chunk 的本地文件存储。"""

    def __init__(self, base_dir: str):
        self.base_dir = Path(base_dir)
        self.chunks_dir = self.base_dir / CHUNKS_DIR
        self.index_path = self.base_dir / INDEX_FILE
        self.manifest_path = self.base_dir / MANIFEST_FILE
        self.chunks_dir.mkdir(parents=True, exist_ok=True)

    def save(self, chunk: StructuredChunk) -> str:
        """保存一条 chunk。已存在时更新。"""
        path = self.chunks_dir / f"{chunk.chunk_id}.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(chunk.to_dict(), f, ensure_ascii=False, indent=2)
        self._update_index(chunk)
        return chunk.chunk_id

    def save_batch(self, chunks: List[StructuredChunk]) -> List[str]:
        """批量保存。"""
        ids = []
        for c in chunks:
            ids.append(self.save(c))
        self._write_manifest()
        return ids

    def load(self, chunk_id: str) -> Optional[StructuredChunk]:
        """按 id 加载一条 chunk。"""
        path = self.chunks_dir / f"{chunk_id}.json"
        if not path.exists():
            return None
        with open(path, "r", encoding="utf-8") as f:
            return StructuredChunk(json.load(f))

    def load_all(self) -> List[StructuredChunk]:
        """加载所有 chunk。"""
        chunks = []
        for f in self.chunks_dir.glob("chunk_*.json"):
            try:
                with open(f, "r", encoding="utf-8") as fp:
                    chunks.append(StructuredChunk(json.load(fp)))
            except (json.JSONDecodeError, ValueError):
                continue
        return chunks

    def delete(self, chunk_id: str):
        """删除一条 chunk。"""
        path = self.chunks_dir / f"{chunk_id}.json"
        if path.exists():
            path.unlink()
        self._remove_from_index(chunk_id)

    def delete_by_source(self, source_node_id: str):
        """删除指定来源节点的所有 chunk。"""
        index = self._read_index()
        to_delete = [
            cid for cid, meta in index.items()
            if meta.get("source_node_id") == source_node_id
        ]
        for cid in to_delete:
            self.delete(cid)

    def stats(self) -> dict:
        """统计信息。"""
        chunks = self.load_all()
        type_count = {}
        grade_count = {}
        for c in chunks:
            t = c["chunk_type"]
            type_count[t] = type_count.get(t, 0) + 1
            g = c.get("quality", {}).get("grade", "?")
            grade_count[g] = grade_count.get(g, 0) + 1
        return {
            "total_chunks": len(chunks),
            "by_type": type_count,
            "by_grade": grade_count,
            "schema_version": SCHEMA_VERSION,
            "storage_path": str(self.chunks_dir),
        }

    # ── 内部方法 ──

    def _read_index(self) -> dict:
        if self.index_path.exists():
            with open(self.index_path, "r", encoding="utf-8") as f:
                return json.load(f)
        return {}

    def _write_index(self, index: dict):
        with open(self.index_path, "w", encoding="utf-8") as f:
            json.dump(index, f, ensure_ascii=False, indent=2)

    def _update_index(self, chunk: StructuredChunk):
        index = self._read_index()
        index[chunk.chunk_id] = {
            "title": chunk["title"],
            "domain": chunk["domain"],
            "chunk_type": chunk["chunk_type"],
            "grade": chunk.get("quality", {}).get("grade", "下品"),
            "source_node_id": chunk.get("provenance", {}).get("source_node_id", ""),
            "updated_at": chunk.get("provenance", {}).get("updated_at", ""),
        }
        self._write_index(index)

    def _remove_from_index(self, chunk_id: str):
        index = self._read_index()
        index.pop(chunk_id, None)
        self._write_index(index)

    def _write_manifest(self):
        chunks = self.load_all()
        manifest = {
            "schema_version": SCHEMA_VERSION,
            "total_chunks": len(chunks),
            "updated_at": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        with open(self.manifest_path, "w", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2)


# ── 检索层（朴素版） ──

class NaiveSearch:
    """朴素文本搜索 — 不依赖向量，粗筛以验证管线。

    后续替换为向量搜索时，保持 search() 签名不变即可。
    """

    def __init__(self, store: ChunkStore):
        self.store = store

    def search(self, query: str, top_k: int = 10, **filters) -> List[Tuple[StructuredChunk, float]]:
        """朴素搜索：query 在 title/content/keywords 中匹配。

        Args:
            query: 搜索关键词
            top_k: 最多返回条数
            **filters: 过滤条件，如 domain='00-思考', chunk_type='concept'

        Returns:
            [(chunk, score), ...] 按分数降序
        """
        q = query.lower().strip()
        if not q:
            return []

        all_chunks = self.store.load_all()
        scored = []

        for c in all_chunks:
            # 过滤
            if not self._passes_filters(c, filters):
                continue

            score = self._compute_score(c, q)
            if score > 0:
                scored.append((c, score))

        scored.sort(key=lambda x: -x[1])
        return scored[:top_k]

    def _passes_filters(self, chunk: StructuredChunk, filters: dict) -> bool:
        for key, val in filters.items():
            if key == "domain":
                if val not in chunk.get("domain", ""):
                    return False
            elif key == "chunk_type":
                if chunk.get("chunk_type") != val:
                    return False
            elif key == "min_grade":
                if chunk.grade_score < GRADE_MAP.get(val, 0):
                    return False
        return True

    def _compute_score(self, chunk: StructuredChunk, q: str) -> float:
        score = 0.0
        text = f"{chunk.get('title','')} {chunk.get('content','')} {' '.join(chunk.get('retrieval_meta',{}).get('keywords',[]))}"

        # 标题精确匹配（最高权重）
        if q in chunk.get("title", "").lower():
            score += 10.0

        # 关键词精确匹配
        for kw in chunk.get("retrieval_meta", {}).get("keywords", []):
            if q in kw.lower():
                score += 5.0

        # 内容中包含
        if q in chunk.get("content", "").lower():
            score += 3.0

        # 品级加权
        score *= 1.0 + (chunk.grade_score - 1) * 0.2

        return score

    def search_by_source(self, source_node_id: str) -> List[StructuredChunk]:
        """按来源节点 id 查找（提炼管道的反向追踪）。"""
        results = []
        for c in self.store.load_all():
            if c.get("provenance", {}).get("source_node_id") == source_node_id:
                results.append(c)
        return results


# ── 提取管道 ──

class Extractor:
    """从丹房页 Markdown 提取结构化 chunk。

    当前实现：基于规则的朴素提取（从 frontmatter + 正文结构拆解）。
    后续将接入 LLM 提取（使用 extraction-prompt.md 模板）。
    """

    def __init__(self, vault_path: str, store: ChunkStore):
        self.vault_path = Path(vault_path)
        self.store = store

    def extract_from_file(self, md_path: str) -> List[StructuredChunk]:
        """从单篇 Markdown 文件提取 chunk。

        先用规则拆解，后续通过 LLM 调用扩展。
        """
        abs_path = self.vault_path / md_path
        if not abs_path.exists():
            print(f"⚠ 文件不存在: {md_path}")
            return []

        body, fm = _read_markdown(str(abs_path))
        title = fm.get("标题", abs_path.stem)
        domain = fm.get("域", fm.get("域:", ""))
        grade = fm.get("品级", "下品")

        if not domain:
            # 从目录路径推断域
            parts = Path(md_path).parts
            for p in parts:
                if re.match(r"\d{2}-", p):
                    domain = p
                    break
            if not domain:
                domain = "00-未分类"

        # 规则提取：按二级标题切分
        chunks = []
        sections = re.split(r"\n##\s+", body)
        for i, section in enumerate(sections):
            section = section.strip()
            if len(section) < 30:
                continue  # 太短不拆

            lines = section.split("\n")
            sec_title = lines[0].strip()
            sec_body = "\n".join(lines[1:]).strip() if len(lines) > 1 else ""
            content_text = sec_body if sec_body else section[:200]

            # 判断 chunk_type
            chunk_type = self._infer_type(section, sec_title)

            # 提取关键词
            keywords = _parse_tags(section)
            if sec_title:
                keywords.insert(0, sec_title)

            anchor = f"{title}中关于{sec_title}的内容是什么" if sec_title else title

            chunk = StructuredChunk.new(
                chunk_type=chunk_type,
                title=f"{title} — {sec_title}" if sec_title else title,
                domain=domain,
                content=section[:500],  # 截断防止单条过大
                tags=_parse_tags(section),
                grade=grade,
                confidence=0.6,  # 规则提取统一低置信度
                keywords=keywords[:8],
                query_rewrite_anchor=anchor,
                source_node_id=md_path,
                source_node_title=title,
            )
            chunks.append(chunk)

        # 如果没有二级标题，整篇作为一条 summary
        if not chunks and len(body) > 30:
            chunk = StructuredChunk.new(
                chunk_type="summary",
                title=title,
                domain=domain,
                content=body[:500],
                tags=_parse_tags(body),
                grade=grade,
                confidence=0.5,
                keywords=_parse_tags(body)[:8],
                query_rewrite_anchor=f"{title}的核心内容是什么",
                source_node_id=md_path,
                source_node_title=title,
            )
            chunks.append(chunk)

        return chunks

    def extract_all(self) -> int:
        """从全部丹房页提取 chunk。返回新建 chunk 数。"""
        danfang = self.vault_path / "丹房"
        if not danfang.exists():
            print(f"⚠ 丹房目录不存在: {danfang}")
            return 0

        md_files = list(danfang.rglob("*.md"))
        total = 0
        for md in md_files:
            rel = md.relative_to(self.vault_path)
            # 跳过 index.md / 模板等辅助文件
            if md.name in ("index.md", "README.md", "模板.md"):
                continue
            try:
                chunks = self.extract_from_file(str(rel))
                if chunks:
                    # 清除旧 chunk
                    self.store.delete_by_source(str(rel))
                    self.store.save_batch(chunks)
                    total += len(chunks)
                    print(f"  + {rel} → {len(chunks)} chunks")
            except Exception as e:
                print(f"  ⚠ {rel}: {e}")

        print(f"\n总计: {total} chunks")
        return total

    def _infer_type(self, section: str, title: str) -> str:
        """根据内容和标题推断 chunk_type。"""
        if not title:
            return "summary"
        t = title.lower()

        # question 检测
        if any(q in t for q in ["怎么", "如何", "是什么", "为什么", "?", "？"]):
            return "question"
        # rule 检测
        if any(r in t for r in ["原则", "规则", "规范", "做法", "步骤", "流程"]):
            return "rule"
        # concept 检测
        if any(c in t for c in ["概念", "定义", "什么是", "含义", "本质"]):
            return "concept"
        # claim 检测
        if any(cl in t for cl in ["观点", "主张", "我认为", "关键", "核心"]):
            return "claim"
        # reference 检测
        if any(rf in t for rf in ["参考", "来源", "引用", "链接", "原文"]):
            return "reference"

        return "concept"  # 默认

    def extract_with_llm(self, md_path: str, llm_client=None) -> List[StructuredChunk]:
        """从单篇 Markdown 文件提取 chunk（LLM 版）。

        使用 AI 理解语义后提取，取代规则按标题切分。
        """
        from llm_client import LLMClient, SYSTEM_PROMPT, build_user_prompt

        abs_path = self.vault_path / md_path
        if not abs_path.exists():
            print(f"  ⚠ 文件不存在: {md_path}")
            return []

        client = llm_client or LLMClient()
        body, fm = _read_markdown(str(abs_path))
        title = fm.get("标题", abs_path.stem)
        domain = fm.get("域", fm.get("域:", ""))
        grade = fm.get("品级", "下品")

        if not domain:
            parts = Path(md_path).parts
            for p in parts:
                if re.match(r"\d{2}-", p):
                    domain = p
                    break
            if not domain:
                domain = "00-未分类"

        user_prompt = build_user_prompt(title, domain, grade, body)
        raw_chunks = client.extract_structured(SYSTEM_PROMPT, user_prompt)

        chunks = []
        for raw in raw_chunks:
            raw["schema_version"] = SCHEMA_VERSION
            raw["content_hash"] = hashlib.sha256(
                raw.get("content", "").encode("utf-8")
            ).hexdigest()
            raw.setdefault("provenance", {})
            raw["provenance"]["source_node_id"] = md_path
            raw["provenance"]["source_node_title"] = title
            raw["provenance"]["source_type"] = "丹房页"
            raw["provenance"]["domain"] = domain
            raw.setdefault("retrieval_meta", {})
            raw["chunk_type"] = raw.get("chunk_type", "concept")
            raw["domain"] = domain
            raw["id"] = _make_id(raw.get("content", ""))
            try:
                chunks.append(StructuredChunk(raw))
            except ValueError as e:
                print(f"  ⚠ chunk 校验失败: {e}")

        return chunks


# ── 主入口 ──

class StructuredIndex:
    """结构化索引引擎统一入口。

    对外接口：用于 CLI 和 MCP adapter。
    对内调用：Extractor + ChunkStore + NaiveSearch 三件套。
    """

    def __init__(self, vault_path: str):
        self.vault_path = str(Path(vault_path).resolve())
        self.index_dir = Path(self.vault_path) / ".lingtai" / "structured-index"
        self.store = ChunkStore(str(self.index_dir))
        self.searcher = NaiveSearch(self.store)
        self.extractor = Extractor(self.vault_path, self.store)

    def ensure_dirs(self):
        """确保存储目录存在。"""
        self.index_dir.mkdir(parents=True, exist_ok=True)
        (self.index_dir / CHUNKS_DIR).mkdir(parents=True, exist_ok=True)

    def extract(self, md_path: str) -> int:
        """从一篇丹房页提取（规则版）。"""
        chunks = self.extractor.extract_from_file(md_path)
        if chunks:
            self.store.delete_by_source(md_path)
            self.store.save_batch(chunks)
        return len(chunks)

    def extract_llm(self, md_path: str, llm_client=None) -> int:
        """从一篇丹房页提取（LLM 版）。"""
        from llm_client import LLMClient
        client = llm_client or LLMClient()
        chunks = self.extractor.extract_with_llm(md_path, client)
        if chunks:
            self.store.delete_by_source(md_path)
            self.store.save_batch(chunks)
        return len(chunks)

    def reindex_all(self) -> int:
        """全量重建索引。"""
        return self.extractor.extract_all()

    def search(self, query: str, top_k: int = 10, **filters) -> list:
        """搜索 chunk。"""
        results = self.searcher.search(query, top_k, **filters)
        return [
            {
                "chunk": c.to_search_snippet(),
                "title": c["title"],
                "domain": c["domain"],
                "content": c["content"][:200],
                "score": round(s, 2),
            }
            for c, s in results
        ]

    def stats(self) -> dict:
        """索引统计。"""
        return self.store.stats()