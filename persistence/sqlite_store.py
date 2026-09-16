"""基于 SQLite 的文档存储实现。

用标准库 ``sqlite3``，不引入额外依赖即可获得**跨进程重启的状态持久化**——
这正是幂等表、工作流状态、BadCase 归档所必需的（原实现在进程内 dict，
重启即丢，与 Temporal 的持久化语义冲突）。

生产环境替换为 PostgreSQL 只需实现同一 :class:`persistence.base.DocumentStore` 协议。
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from threading import RLock
from typing import Any

from persistence.base import META_CREATED, META_KEY, META_UPDATED, stamp_new

_SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    collection TEXT NOT NULL,
    key        TEXT NOT NULL,
    payload    TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (collection, key)
);
CREATE INDEX IF NOT EXISTS idx_documents_collection_updated
    ON documents (collection, updated_at DESC);
"""


class SqliteDocumentStore:
    """把 JSON 文档落到单个 SQLite 文件。"""

    backend = "sqlite"

    def __init__(self, path: str = "aio_agentos.db") -> None:
        self.path = path
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = RLock()
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    def save(self, collection: str, key: str, payload: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            row = self._conn.execute(
                "SELECT created_at FROM documents WHERE collection=? AND key=?",
                (collection, key),
            ).fetchone()
            record = stamp_new(payload, key)
            created = row["created_at"] if row else record[META_CREATED]
            record[META_CREATED] = created
            self._conn.execute(
                """
                INSERT INTO documents (collection, key, payload, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(collection, key) DO UPDATE SET
                    payload=excluded.payload,
                    updated_at=excluded.updated_at
                """,
                (
                    collection,
                    key,
                    json.dumps(payload, ensure_ascii=False, default=str),
                    created,
                    record[META_UPDATED],
                ),
            )
            self._conn.commit()
            return record

    def load(self, collection: str, key: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT payload, created_at, updated_at FROM documents WHERE collection=? AND key=?",
                (collection, key),
            ).fetchone()
        if row is None:
            return None
        record = json.loads(row["payload"])
        record[META_KEY] = key
        record[META_CREATED] = row["created_at"]
        record[META_UPDATED] = row["updated_at"]
        return record

    def query(
        self,
        collection: str,
        *,
        limit: int | None = None,
        newest_first: bool = True,
    ) -> list[dict[str, Any]]:
        direction = "DESC" if newest_first else "ASC"
        sql = (
            "SELECT key, payload, created_at, updated_at FROM documents "
            f"WHERE collection=? ORDER BY updated_at {direction}"
        )
        params: tuple[Any, ...] = (collection,)
        if limit is not None:
            sql += " LIMIT ?"
            params = (collection, limit)
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        results: list[dict[str, Any]] = []
        for row in rows:
            record = json.loads(row["payload"])
            record[META_KEY] = row["key"]
            record[META_CREATED] = row["created_at"]
            record[META_UPDATED] = row["updated_at"]
            results.append(record)
        return results

    def delete(self, collection: str, key: str) -> bool:
        with self._lock:
            cursor = self._conn.execute(
                "DELETE FROM documents WHERE collection=? AND key=?",
                (collection, key),
            )
            self._conn.commit()
            return cursor.rowcount > 0

    def count(self, collection: str) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) AS n FROM documents WHERE collection=?",
                (collection,),
            ).fetchone()
        return int(row["n"]) if row else 0

    def collections(self) -> list[str]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT DISTINCT collection FROM documents ORDER BY collection"
            ).fetchall()
        return [row["collection"] for row in rows]

    def close(self) -> None:
        with self._lock:
            self._conn.close()
