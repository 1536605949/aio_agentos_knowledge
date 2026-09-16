from __future__ import annotations

from collections import defaultdict
from threading import RLock

from memory.types import MemoryRecord, MemoryType


class InMemoryMemoryStore:
    """Thread-safe reference store. Replace behind this interface in production."""

    def __init__(self) -> None:
        self._records: dict[str, list[MemoryRecord]] = defaultdict(list)
        self._lock = RLock()

    def put(self, record: MemoryRecord) -> MemoryRecord:
        with self._lock:
            self._records[record.workflow_id].append(record)
        return record

    def list(self, workflow_id: str, memory_type: MemoryType | None = None) -> list[MemoryRecord]:
        with self._lock:
            records = list(self._records.get(workflow_id, []))
        if memory_type is not None:
            records = [r for r in records if r.memory_type == memory_type]
        return records
