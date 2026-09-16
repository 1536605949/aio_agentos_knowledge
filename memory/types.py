from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


class MemoryType(StrEnum):
    """记忆类型。``short`` 服务于单次会话，``episodic`` 记录一次故障的完整处置。"""

    SHORT = "short"
    LONG = "long"
    VECTOR = "vector"
    EPISODIC = "episodic"


class MemoryRecord(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    workflow_id: str
    memory_type: MemoryType
    content: dict[str, Any]
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    tags: list[str] = Field(default_factory=list)


class ShortMemory(MemoryRecord):
    memory_type: MemoryType = MemoryType.SHORT


class LongMemory(MemoryRecord):
    memory_type: MemoryType = MemoryType.LONG


class VectorMemory(MemoryRecord):
    memory_type: MemoryType = MemoryType.VECTOR
    embedding_ref: str | None = None


class EpisodicMemory(MemoryRecord):
    memory_type: MemoryType = MemoryType.EPISODIC
    outcome: str | None = None
