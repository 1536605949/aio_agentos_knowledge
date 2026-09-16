"""持久化抽象。

统一用一个「JSON 文档集合」模型承载四类数据，避免为每类数据各写一套仓储：

| collection | 承载内容 | 现有代码位置 |
|---|---|---|
| ``workflows`` | ``WorkflowState`` 快照 | ``temporal/workflow.py`` |
| ``memory`` | Short/Long/Vector/Episodic 记录 | ``memory/store.py`` |
| ``idempotency`` | 幂等键 → workflow_id 映射 | ``api/app.py`` |
| ``feedback`` | BadCase / 用户反馈 | ``observability/badcase.py`` |
| ``traces`` | Span 归档 | ``observability/trace.py`` |

生产环境可替换为 PostgreSQL 实现（实现同一 :class:`DocumentStore` 协议）。
"""

from __future__ import annotations

from datetime import UTC, datetime
from threading import RLock
from typing import Any, Protocol, runtime_checkable

META_KEY = "_key"
META_CREATED = "_created_at"
META_UPDATED = "_updated_at"


def _now() -> str:
    return datetime.now(UTC).isoformat()


@runtime_checkable
class DocumentStore(Protocol):
    """JSON 文档存储协议。实现需保证线程/协程安全。"""

    def save(self, collection: str, key: str, payload: dict[str, Any]) -> dict[str, Any]: ...

    def load(self, collection: str, key: str) -> dict[str, Any] | None: ...

    def query(
        self,
        collection: str,
        *,
        limit: int | None = None,
        newest_first: bool = True,
    ) -> list[dict[str, Any]]: ...

    def delete(self, collection: str, key: str) -> bool: ...

    def count(self, collection: str) -> int: ...

    def collections(self) -> list[str]: ...

    def close(self) -> None: ...


def stamp_new(payload: dict[str, Any], key: str) -> dict[str, Any]:
    """构造带元数据的记录（新建语义）。"""
    record = dict(payload)
    record[META_KEY] = key
    record.setdefault(META_CREATED, _now())
    record[META_UPDATED] = _now()
    return record


class InMemoryDocumentStore:
    """默认实现：进程内。重启即丢失，适用于开发与测试。"""

    backend = "memory"

    def __init__(self) -> None:
        self._data: dict[str, dict[str, dict[str, Any]]] = {}
        self._order: dict[str, list[str]] = {}
        self._lock = RLock()

    def save(self, collection: str, key: str, payload: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            bucket = self._data.setdefault(collection, {})
            existing = bucket.get(key)
            record = stamp_new(payload, key)
            if existing and META_CREATED in existing:
                record[META_CREATED] = existing[META_CREATED]
            bucket[key] = record
            order = self._order.setdefault(collection, [])
            if key in order:
                order.remove(key)
            order.append(key)
            return dict(record)

    def load(self, collection: str, key: str) -> dict[str, Any] | None:
        with self._lock:
            record = self._data.get(collection, {}).get(key)
            return dict(record) if record else None

    def query(
        self,
        collection: str,
        *,
        limit: int | None = None,
        newest_first: bool = True,
    ) -> list[dict[str, Any]]:
        with self._lock:
            bucket = self._data.get(collection, {})
            order = list(self._order.get(collection, []))
        if newest_first:
            order.reverse()
        if limit is not None:
            order = order[:limit]
        return [dict(bucket[key]) for key in order if key in bucket]

    def delete(self, collection: str, key: str) -> bool:
        with self._lock:
            bucket = self._data.get(collection)
            if not bucket or key not in bucket:
                return False
            del bucket[key]
            order = self._order.get(collection, [])
            if key in order:
                order.remove(key)
            return True

    def count(self, collection: str) -> int:
        with self._lock:
            return len(self._data.get(collection, {}))

    def collections(self) -> list[str]:
        with self._lock:
            return sorted(self._data)

    def close(self) -> None:
        with self._lock:
            self._data.clear()
            self._order.clear()
