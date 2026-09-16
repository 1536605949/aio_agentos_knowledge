"""持久化层。

默认使用进程内实现（开发/测试友好），通过 ``AIO_AGENTOS_STORE_BACKEND=sqlite``
可切换到跨重启持久化的 SQLite 实现。
"""

from persistence.base import (
    META_CREATED,
    META_KEY,
    META_UPDATED,
    DocumentStore,
    InMemoryDocumentStore,
    stamp_new,
)
from persistence.factory import build_store
from persistence.sqlite_store import SqliteDocumentStore

__all__ = [
    "META_CREATED",
    "META_KEY",
    "META_UPDATED",
    "DocumentStore",
    "InMemoryDocumentStore",
    "SqliteDocumentStore",
    "build_store",
    "stamp_new",
]
