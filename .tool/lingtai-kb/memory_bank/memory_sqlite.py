# -*- coding: utf-8 -*-
"""
灵台记忆银行 SQLite 持久层
===========================
替代 memories.json 的 flat-file I/O，提供：
- WAL 模式：并发读 + 单写不阻塞
- 事务安全：crash 不丢数据（替代 coalesced_json 的 0.5s 窗口）
- FTS5 全文索引：content + tags 可搜索
- JSON 导出：保留 git 友好备份能力

设计：write-through 模式——MemoryBank 内存列表仍是运行时主数据，
本模块只负责持久化（load_all / save_one / delete_one / export_json）。

用法：
    from memory_bank.memory_sqlite import MemoryStore
    store = MemoryStore(data_dir)
    memories = store.load_all()       # 启动时加载
    store.save_one(memory_dict)       # 变更后写回
    store.export_json()               # 定期导出给 git
"""
import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Optional

from logger import get_logger

log = get_logger(__name__)

# Memory 字段 → SQLite 列映射（JSON 序列化的复合字段）
_JSON_FIELDS = ("conflicts_with", "tags", "entries", "knowledge_links", "context")


class MemoryStore:
    """SQLite 持久层（WAL + FTS5）。"""

    def __init__(self, data_dir: str):
        """
        Args:
            data_dir: memory_bank/data/ 目录路径
        """
        self.data_dir = data_dir
        self.db_path = os.path.join(data_dir, "memories.db")
        self._conn: Optional[sqlite3.Connection] = None
        os.makedirs(data_dir, exist_ok=True)

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.execute("PRAGMA busy_timeout=5000")
            self._init_schema()
        return self._conn

    def _init_schema(self):
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS memories (
                id TEXT PRIMARY KEY,
                content TEXT NOT NULL,
                source TEXT NOT NULL DEFAULT '',
                source_confidence REAL NOT NULL DEFAULT 0.5,
                current_confidence REAL NOT NULL DEFAULT 0.5,
                status TEXT NOT NULL DEFAULT 'active',
                evidence_count INTEGER NOT NULL DEFAULT 1,
                branch_id TEXT NOT NULL DEFAULT '通用',
                conflicts_with TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL DEFAULT '',
                last_verified TEXT NOT NULL DEFAULT '',
                expiry_policy TEXT NOT NULL DEFAULT 'slow_decay',
                decay_streak INTEGER NOT NULL DEFAULT 0,
                tags TEXT NOT NULL DEFAULT '[]',
                memory_type TEXT NOT NULL DEFAULT 'semantic',
                entries TEXT NOT NULL DEFAULT '[]',
                schema_version INTEGER NOT NULL DEFAULT 1,
                knowledge_links TEXT NOT NULL DEFAULT '[]',
                graduation_candidate INTEGER NOT NULL DEFAULT 0,
                graduation_marked_at TEXT NOT NULL DEFAULT '',
                graduated_at TEXT NOT NULL DEFAULT '',
                context TEXT NOT NULL DEFAULT '{}',
                expected_consumer TEXT NOT NULL DEFAULT ''
            );

            CREATE INDEX IF NOT EXISTS idx_mem_status ON memories(status);
            CREATE INDEX IF NOT EXISTS idx_mem_confidence ON memories(current_confidence);
            CREATE INDEX IF NOT EXISTS idx_mem_type ON memories(memory_type);
            CREATE INDEX IF NOT EXISTS idx_mem_branch ON memories(branch_id);
            CREATE INDEX IF NOT EXISTS idx_mem_created ON memories(created_at);

            -- FTS5 全文索引（content + tags）
            CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
                id UNINDEXED,
                content,
                tags,
                tokenize='unicode61'
            );
        """)
        self._conn.commit()

    # ─── 加载 ─────────────────────────────────────

    def load_all(self) -> list[dict]:
        """加载全部记忆为 dict 列表（启动时调用一次）。"""
        conn = self._get_conn()
        rows = conn.execute("SELECT * FROM memories").fetchall()
        results = []
        for row in rows:
            d = dict(row)
            # 反序列化 JSON 字段
            for field in _JSON_FIELDS:
                if field in d and isinstance(d[field], str):
                    try:
                        d[field] = json.loads(d[field])
                    except (json.JSONDecodeError, TypeError):
                        d[field] = [] if field != "context" else {}
            # graduation_candidate: int → bool
            d["graduation_candidate"] = bool(d.get("graduation_candidate", 0))
            results.append(d)
        log.debug("memory store loaded", extra={"count": len(results)})
        return results

    # ─── 写入 ─────────────────────────────────────

    def save_one(self, memory: dict):
        """写入或更新单条记忆（UPSERT）。"""
        conn = self._get_conn()
        row = self._to_row(memory)
        conn.execute("""
            INSERT OR REPLACE INTO memories (
                id, content, source, source_confidence, current_confidence,
                status, evidence_count, branch_id, conflicts_with,
                created_at, last_verified, expiry_policy, decay_streak,
                tags, memory_type, entries, schema_version,
                knowledge_links, graduation_candidate, graduation_marked_at,
                graduated_at, context, expected_consumer
            ) VALUES (
                :id, :content, :source, :source_confidence, :current_confidence,
                :status, :evidence_count, :branch_id, :conflicts_with,
                :created_at, :last_verified, :expiry_policy, :decay_streak,
                :tags, :memory_type, :entries, :schema_version,
                :knowledge_links, :graduation_candidate, :graduation_marked_at,
                :graduated_at, :context, :expected_consumer
            )
        """, row)
        # 同步 FTS5 索引（预分词：CJK 逐字切分，unicode61 才能索引）
        conn.execute("DELETE FROM memories_fts WHERE id = ?", (memory["id"],))
        conn.execute(
            "INSERT INTO memories_fts (id, content, tags) VALUES (?, ?, ?)",
            (memory["id"], self._tokenize_for_fts(memory.get("content", "")),
             self._tokenize_for_fts(" ".join(memory.get("tags", []))))
        )
        conn.commit()

    def save_many(self, memories: list[dict]):
        """批量写入（迁移时用）。"""
        conn = self._get_conn()
        for memory in memories:
            row = self._to_row(memory)
            conn.execute("""
                INSERT OR REPLACE INTO memories (
                    id, content, source, source_confidence, current_confidence,
                    status, evidence_count, branch_id, conflicts_with,
                    created_at, last_verified, expiry_policy, decay_streak,
                    tags, memory_type, entries, schema_version,
                    knowledge_links, graduation_candidate, graduation_marked_at,
                    graduated_at, context, expected_consumer
                ) VALUES (
                    :id, :content, :source, :source_confidence, :current_confidence,
                    :status, :evidence_count, :branch_id, :conflicts_with,
                    :created_at, :last_verified, :expiry_policy, :decay_streak,
                    :tags, :memory_type, :entries, :schema_version,
                    :knowledge_links, :graduation_candidate, :graduation_marked_at,
                    :graduated_at, :context, :expected_consumer
                )
            """, row)
            conn.execute("DELETE FROM memories_fts WHERE id = ?", (memory["id"],))
            conn.execute(
                "INSERT INTO memories_fts (id, content, tags) VALUES (?, ?, ?)",
                (memory["id"], self._tokenize_for_fts(memory.get("content", "")),
                 self._tokenize_for_fts(" ".join(memory.get("tags", []))))
            )
        conn.commit()
        log.debug("memory store batch saved", extra={"count": len(memories)})

    def delete_one(self, memory_id: str):
        """删除单条记忆。"""
        conn = self._get_conn()
        conn.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
        conn.execute("DELETE FROM memories_fts WHERE id = ?", (memory_id,))
        conn.commit()

    # ─── FTS5 搜索 ────────────────────────────────

    def fts_search(self, keyword: str, limit: int = 20) -> list[str]:
        """FTS5 全文搜索，返回匹配的 memory ID 列表。"""
        conn = self._get_conn()
        # 简单分词：CJK 逐字 + ASCII 整词
        from fts_index import tokenize
        tokens = tokenize(keyword)
        if not tokens:
            return []
        parts = tokens.split()
        if len(parts) == 1:
            fts_query = parts[0]
        else:
            fts_query = '"' + " ".join(parts) + '"'
        try:
            rows = conn.execute(
                "SELECT id FROM memories_fts WHERE memories_fts MATCH ? LIMIT ?",
                (fts_query, limit)
            ).fetchall()
            return [r["id"] for r in rows]
        except sqlite3.OperationalError:
            log.debug("fts search error", exc_info=True)
            return []

    # ─── JSON 导出（git 备份） ────────────────────

    def export_json(self, output_path: Optional[str] = None) -> str:
        """导出为 memories.json（git 友好，原子写入）。"""
        if output_path is None:
            output_path = os.path.join(self.data_dir, "memories.json")
        memories = self.load_all()
        tmp_path = output_path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(memories, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, output_path)
        log.debug("exported to json", extra={"count": len(memories), "path": output_path})
        return output_path

    # ─── 迁移 ─────────────────────────────────────

    def migrate_from_json(self, json_path: Optional[str] = None) -> int:
        """从 memories.json 迁移到 SQLite（幂等，可重复执行）。

        Returns:
            迁移的记忆条数
        """
        if json_path is None:
            json_path = os.path.join(self.data_dir, "memories.json")
        if not os.path.exists(json_path):
            return 0
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                memories = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            log.warning("migration: failed to read json", extra={"error": str(e)})
            return 0

        if not isinstance(memories, list):
            return 0

        self.save_many(memories)
        log.info("migration complete", extra={"count": len(memories)})
        return len(memories)

    # ─── 统计 ─────────────────────────────────────

    def stats(self) -> dict:
        conn = self._get_conn()
        total = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        by_status = dict(conn.execute(
            "SELECT status, COUNT(*) FROM memories GROUP BY status"
        ).fetchall())
        db_size = os.path.getsize(self.db_path) if os.path.exists(self.db_path) else 0
        return {
            "total": total,
            "by_status": by_status,
            "db_size_kb": round(db_size / 1024, 1),
        }

    # ─── 内部 ─────────────────────────────────────

    @staticmethod
    def _tokenize_for_fts(text: str) -> str:
        """预分词：CJK 逐字切分 + ASCII 整词，供 FTS5 unicode61 索引。"""
        from fts_index import tokenize
        return tokenize(text)

    def _to_row(self, memory: dict) -> dict:
        """将 memory dict 转为 SQLite 行（序列化 JSON 字段）。"""
        row = {}
        for key, value in memory.items():
            if key in _JSON_FIELDS:
                row[key] = json.dumps(value, ensure_ascii=False) if value else (
                    "[]" if key != "context" else "{}"
                )
            elif key == "graduation_candidate":
                row[key] = int(bool(value))
            else:
                row[key] = value
        # 确保必需字段存在
        row.setdefault("id", "")
        row.setdefault("content", "")
        row.setdefault("source", "")
        row.setdefault("source_confidence", 0.5)
        row.setdefault("current_confidence", 0.5)
        row.setdefault("status", "active")
        row.setdefault("evidence_count", 1)
        row.setdefault("branch_id", "通用")
        row.setdefault("conflicts_with", "[]")
        row.setdefault("created_at", "")
        row.setdefault("last_verified", "")
        row.setdefault("expiry_policy", "slow_decay")
        row.setdefault("decay_streak", 0)
        row.setdefault("tags", "[]")
        row.setdefault("memory_type", "semantic")
        row.setdefault("entries", "[]")
        row.setdefault("schema_version", 1)
        row.setdefault("knowledge_links", "[]")
        row.setdefault("graduation_candidate", 0)
        row.setdefault("graduation_marked_at", "")
        row.setdefault("graduated_at", "")
        row.setdefault("context", "{}")
        row.setdefault("expected_consumer", "")
        return row

    def close(self):
        if self._conn:
            self._conn.close()
            self._conn = None
