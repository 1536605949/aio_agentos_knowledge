"""工具契约。

:class:`ToolSpec` 是"工具能做什么、有多危险、怎么容错"的**唯一声明点**——
治理层读它的 ``risk_level`` 决定是否需要审批，注册表读它的重试/缓存/幂等声明
决定调用策略，协议层读它的 schema 生成 MCP 描述符。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from pydantic import BaseModel, Field

from governance.models import RiskLevel

ToolHandler = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


class ToolSpec(BaseModel):
    name: str
    version: str = "1.0.0"
    description: str = ""
    risk_level: RiskLevel = RiskLevel.LOW
    timeout_seconds: float = Field(default=10.0, gt=0)
    max_retries: int = Field(default=1, ge=0, le=5)
    required_permission: str = "tool:read"
    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] = Field(default_factory=dict)

    cacheable: bool = False
    """只读工具可开启结果缓存（同一 payload 在 TTL 内直接命中）。"""
    cache_ttl_seconds: float | None = None
    """为 None 时使用全局缓存 TTL。"""

    idempotent: bool = False
    """开启后，调用方传入 ``idempotency_key`` 可保证副作用只发生一次。"""

    resource_field: str | None = None
    """payload 中用于资源锁隔离的字段名（如 ``service``），避免并发操作同一资源。"""


__all__ = ["ToolHandler", "ToolSpec"]
