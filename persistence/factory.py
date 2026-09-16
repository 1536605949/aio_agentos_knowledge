"""存储工厂。"""

from __future__ import annotations

from config import Settings, get_settings
from persistence.base import DocumentStore, InMemoryDocumentStore
from persistence.sqlite_store import SqliteDocumentStore

SUPPORTED_BACKENDS = ("memory", "sqlite")


def build_store(settings: Settings | None = None) -> DocumentStore:
    """按 ``AIO_AGENTOS_STORE_BACKEND`` 构建存储后端。"""
    settings = settings or get_settings()
    backend = settings.store_backend
    if backend == "sqlite":
        return SqliteDocumentStore(settings.sqlite_path)
    if backend == "memory":
        return InMemoryDocumentStore()
    raise ValueError(f"unsupported store backend: {backend!r}; expected one of {SUPPORTED_BACKENDS}")
