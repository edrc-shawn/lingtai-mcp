# -*- coding: utf-8 -*-
"""
灵台全文搜索倒排索引（SQLite FTS5）
====================================
替代 fulltext_search 的暴力逐文件扫描，响应从秒级降到毫秒级。

设计：
- CJK 字符逐字切分为独立 token，ASCII 保持整词 → FTS5 默认 tokenizer 按空格切分
- 查询用 phrase match 复刻子串匹配语义
- BM25 排序（FTS5 内置）
- 持久化到 .fts5.db，服务重启零重建
- 按文件 mtime 增量更新

用法：
    from fts_index import FulltextIndex
    idx = FulltextIndex(vault_path)
    idx.ensure_built(scope_map)
    results = idx.query("关键词", scope="原料", max_results=20)
"""
__all__ = ["FulltextIndex", "tokenize"]

import os
import re
import sqlite3
import time
from pathlib import Path
from typing import Optional

from logger import get_logger

log = get_logger(__name__)

# 一次 findall 匹配：ASCII 词（整词）或 CJK 单字
_TOKEN_RE = re.compile(
    r"[a-z0-9_]+"
    r"|[\u4e00-\u9fff\u3400-\u4dbf\uf900-\ufaff"
    r"\U00020000-\U0002a6df\U0002a700-\U0002b73f]"
)


def tokenize(text: str) -> str:
    """将文本预分词为 FTS5 可索引的空格分隔 token 串。

    策略：
    - ASCII 字母数字序列保持为整词（小写化）
    - 每个 CJK 字符作为独立 token
    - 其他字符（标点、空白）丢弃

    性能：re.findall 在 C 层执行，比逐字符 Python 循环快 50-100x。
    """
    return " ".join(_TOKEN_RE.findall(text.lower()))


def _build_query_tokens(keyword: str) -> str:
    """将查询关键词转为 FTS5 phrase query token 串。"""
    toks = tokenize(keyword)
    if not toks:
        return ""
    parts = toks.split()
    if len(parts) == 1:
        return parts[0]
    # 多 token 用 phrase match 保证顺序连续（= 子串匹配语义）
    return '"' + " ".join(parts) + '"'


class FulltextIndex:
    """SQLite FTS5 全文索引管理器。"""

    # 增量检查 TTL（秒）：在此间隔内跳过文件系统扫描
    CHECK_TTL = 30.0

    def __init__(self, vault_path: str):
        self.vault_path = vault_path
        db_dir = os.path.join(vault_path, ".tool", "lingtai-kb")
        self.db_path = os.path.join(db_dir, ".fts5.db")
        self._conn: Optional[sqlite3.Connection] = None
        self._last_check: float = 0.0

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._init_schema()
        return self._conn

    def _init_schema(self):
        conn = self._conn
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS meta (
                path TEXT PRIMARY KEY,
                mtime REAL NOT NULL,
                scope TEXT NOT NULL
            );
            CREATE VIRTUAL TABLE IF NOT EXISTS docs USING fts5(
                path UNINDEXED,
                scope UNINDEXED,
                content,
                tokenize='unicode61'
            );
        """)
        conn.commit()

    # ─── 构建 / 增量更新 ───────────────────────────

    def ensure_built(self, scope_map: dict[str, tuple[str, str]]):
        """确保索引已构建且与文件系统同步。

        Args:
            scope_map: {"原料": ("原料", "灵台·原料"), ...}
                       key=scope名, value=(相对目录, source_label)
        """
        now = time.perf_counter()
        # TTL 短路：间隔内跳过文件系统扫描
        if (now - self._last_check) < self.CHECK_TTL and os.path.exists(self.db_path):
            return
        self._last_check = now

        conn = self._get_conn()
        t0 = time.perf_counter()

        # 收集当前文件系统状态
        fs_files: dict[str, tuple[float, str]] = {}  # abs_path → (mtime, scope)
        for scope_name, (rel_dir, _label) in scope_map.items():
            abs_dir = os.path.join(self.vault_path, rel_dir)
            if not os.path.isdir(abs_dir):
                continue
            for root, dirs, files in os.walk(abs_dir):
                dirs[:] = [d for d in dirs if not d.startswith(".")]
                for fname in files:
                    if not fname.endswith(".md"):
                        continue
                    fpath = os.path.join(root, fname)
                    try:
                        mt = os.path.getmtime(fpath)
                    except OSError:
                        continue
                    fs_files[fpath] = (mt, scope_name)

        # 读取索引中已有记录
        indexed: dict[str, tuple[float, str]] = {}
        try:
            for row in conn.execute("SELECT path, mtime, scope FROM meta"):
                indexed[row[0]] = (row[1], row[2])
        except sqlite3.OperationalError:
            pass

        # 计算差异
        to_add: list[str] = []
        to_remove: list[str] = []
        for fpath, (mt, scope) in fs_files.items():
            if fpath not in indexed or indexed[fpath][0] < mt:
                to_add.append(fpath)
        for fpath in indexed:
            if fpath not in fs_files:
                to_remove.append(fpath)

        if not to_add and not to_remove:
            log.debug("fts index up-to-date", extra={"files": len(fs_files)})
            return

        # 执行增量更新
        for fpath in to_remove:
            rel = os.path.relpath(fpath, self.vault_path).replace("\\", "/")
            conn.execute("DELETE FROM meta WHERE path=?", (fpath,))
            conn.execute("DELETE FROM docs WHERE path=?", (rel,))

        batch_count = 0
        for fpath in to_add:
            mt, scope = fs_files[fpath]
            rel = os.path.relpath(fpath, self.vault_path).replace("\\", "/")
            try:
                with open(fpath, "r", encoding="utf-8", errors="ignore") as fp:
                    raw_content = fp.read()
            except Exception:
                log.warning("fts read fail", extra={"path": rel}, exc_info=True)
                continue

            tokenized = tokenize(raw_content)

            # 先删旧记录（可能是更新）
            conn.execute("DELETE FROM meta WHERE path=?", (fpath,))
            conn.execute("DELETE FROM docs WHERE path=?", (rel,))

            conn.execute(
                "INSERT INTO meta (path, mtime, scope) VALUES (?, ?, ?)",
                (fpath, mt, scope),
            )
            conn.execute(
                "INSERT INTO docs (path, scope, content) VALUES (?, ?, ?)",
                (rel, scope, tokenized),
            )
            batch_count += 1

        conn.commit()
        elapsed = time.perf_counter() - t0
        log.debug(
            "fts index updated",
            extra={"added": len(to_add), "removed": len(to_remove),
                   "total": len(fs_files), "elapsed_ms": round(elapsed * 1000)},
        )

    # ─── 查询 ─────────────────────────────────────

    def query(
        self,
        keyword: str,
        scope: str = "all",
        max_results: int = 20,
    ) -> list[dict]:
        """全文检索，返回 [{path, snippet, source_label, rank}]。

        使用 FTS5 phrase match + BM25 排序。
        """
        conn = self._get_conn()
        fts_query = _build_query_tokens(keyword)
        if not fts_query:
            return []

        # 构建 SQL
        if scope == "all":
            sql = """
                SELECT path, scope,
                       snippet(docs, 2, '<b>', '</b>', '…', 32) AS snip,
                       bm25(docs) AS rank
                FROM docs
                WHERE content MATCH ?
                ORDER BY rank
                LIMIT ?
            """
            params = (fts_query, max_results)
        else:
            sql = """
                SELECT path, scope,
                       snippet(docs, 2, '<b>', '</b>', '…', 32) AS snip,
                       bm25(docs) AS rank
                FROM docs
                WHERE content MATCH ? AND scope = ?
                ORDER BY rank
                LIMIT ?
            """
            params = (fts_query, scope, max_results)

        try:
            rows = conn.execute(sql, params).fetchall()
        except sqlite3.OperationalError as e:
            log.warning("fts query error", extra={"query": fts_query, "error": str(e)})
            return []

        # scope → source_label 映射
        scope_labels = {
            "技能": "灵台·技能",
            "原料": "灵台·原料",
            "作品": "灵台·作品",
            "外部参考": "灵台·外部参考",
            "日志": "灵台·日志",
        }

        results = []
        for path, file_scope, snip, rank in rows:
            # 从原文提取更好的 snippet（FTS5 snippet 基于 token 化文本，中文可读性差）
            snippet = self._extract_snippet(path, keyword)
            if snippet is None:
                snippet = snip  # fallback 到 FTS5 snippet

            results.append({
                "path": path,
                "snippet": snippet,
                "source_label": scope_labels.get(file_scope, "灵台·资产"),
                "rank": round(rank, 4),
            })
        return results

    def _extract_snippet(self, rel_path: str, keyword: str) -> Optional[str]:
        """从原文提取 ±80 字符 snippet（保持与原实现一致的可读性）。"""
        abs_path = os.path.join(self.vault_path, rel_path.replace("/", os.sep))
        try:
            with open(abs_path, "r", encoding="utf-8", errors="ignore") as fp:
                content = fp.read()
        except Exception:
            return None

        kw_lower = keyword.lower().strip()
        idx = content.lower().find(kw_lower)
        if idx == -1:
            # 尝试逐字匹配（token 化后可能原文大小写不同）
            return None
        start = max(0, idx - 80)
        end = min(len(content), idx + len(keyword) + 80)
        snippet = content[start:end].replace("\n", " ").strip()
        if len(snippet) > 200:
            snippet = snippet[:200] + "\u2026"
        return snippet

    # ─── 统计 / 维护 ──────────────────────────────

    def stats(self) -> dict:
        """返回索引统计信息。"""
        conn = self._get_conn()
        try:
            total = conn.execute("SELECT COUNT(*) FROM meta").fetchone()[0]
            by_scope = dict(
                conn.execute("SELECT scope, COUNT(*) FROM meta GROUP BY scope").fetchall()
            )
        except sqlite3.OperationalError:
            total, by_scope = 0, {}
        db_size = os.path.getsize(self.db_path) if os.path.exists(self.db_path) else 0
        return {
            "total_files": total,
            "by_scope": by_scope,
            "db_size_kb": round(db_size / 1024, 1),
            "db_path": self.db_path,
        }

    def rebuild(self, scope_map: dict[str, tuple[str, str]]):
        """强制全量重建索引。"""
        conn = self._get_conn()
        conn.execute("DELETE FROM meta")
        conn.execute("DELETE FROM docs")
        conn.commit()
        self._last_check = 0.0  # 绕过 TTL 短路
        self.ensure_built(scope_map)

    def close(self):
        if self._conn:
            self._conn.close()
            self._conn = None
