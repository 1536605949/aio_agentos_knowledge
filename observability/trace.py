"""Trace 采集。

关键设计：用 :class:`contextvars.ContextVar` 维护**当前 Span 栈**，
使嵌套的 ``with trace.span(...)`` 自动建立父子关系，
而不是像早期实现那样全部落成兄弟节点（``parent_span_id`` 恒为 ``None``）。

同时保证：**异常路径也落盘**——``finally`` 中先标记 ``ERROR`` 再写入，
这是 Agent 系统排障最关键的部分。
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from threading import RLock
from time import perf_counter
from typing import Any
from uuid import uuid4

from observability.models import Span, SpanKind, SpanStatus

_current_span: ContextVar[Span | None] = ContextVar("aio_current_span", default=None)


class AgentTrace:
    """一次工作流执行的全链路 Span 采集器。"""

    def __init__(self, trace_id: str | None = None) -> None:
        self.trace_id = trace_id or str(uuid4())
        self._spans: list[Span] = []
        self._lock = RLock()

    # ------------------------------------------------------------ 反序列化

    @classmethod
    def from_spans(cls, trace_id: str, spans: list[Span]) -> AgentTrace:
        """用已归档的 Span 重建 Trace（跨进程重启后回读用）。"""
        trace = cls(trace_id=trace_id)
        trace._spans = list(spans)
        return trace

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> AgentTrace:
        spans = [Span.model_validate(item) for item in payload.get("spans", [])]
        trace_id = payload.get("trace_id") or str(uuid4())
        return cls.from_spans(trace_id, spans)

    # --------------------------------------------------------------- 采集入口

    @contextmanager
    def span(
        self,
        name: str,
        parent_span_id: str | None = None,
        kind: SpanKind | str = SpanKind.AGENT,
        **attributes: Any,
    ) -> Iterator[Span]:
        """开启一个 Span。

        父 Span 判定优先级：显式传入的 ``parent_span_id`` > 当前上下文栈顶 > 无父节点。
        ``kind`` 决定调用树中的着色分类（workflow / agent / tool / llm / governance ...）。
        """
        parent = parent_span_id
        if parent is None:
            current = _current_span.get()
            parent = current.span_id if current is not None else None

        span = Span(
            trace_id=self.trace_id,
            parent_span_id=parent,
            name=name,
            kind=SpanKind(kind),
            attributes=attributes,
        )
        token = _current_span.set(span)
        started = perf_counter()
        try:
            yield span
        except Exception as exc:
            span.status = SpanStatus.ERROR
            span.error = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            span.ended_at = datetime.now(UTC)
            span.duration_ms = round((perf_counter() - started) * 1000, 3)
            with self._lock:
                self._spans.append(span)
            _current_span.reset(token)

    # --------------------------------------------------------------- 读取视图

    def spans(self) -> list[Span]:
        with self._lock:
            return list(self._spans)

    def roots(self) -> list[Span]:
        return [span for span in self.spans() if span.parent_span_id is None]

    def children_of(self, span_id: str) -> list[Span]:
        return [span for span in self.spans() if span.parent_span_id == span_id]

    def failed_spans(self) -> list[Span]:
        return [span for span in self.spans() if span.status is SpanStatus.ERROR]

    def tree(self) -> list[dict[str, Any]]:
        """返回嵌套结构，便于前端直接渲染调用树。"""
        with self._lock:
            snapshot = list(self._spans)
        by_parent: dict[str | None, list[Span]] = {}
        for span in snapshot:
            by_parent.setdefault(span.parent_span_id, []).append(span)

        def build(parent_id: str | None) -> list[dict[str, Any]]:
            nodes: list[dict[str, Any]] = []
            for span in by_parent.get(parent_id, []):
                node = span.model_dump(mode="json")
                node["children"] = build(span.span_id)
                nodes.append(node)
            return nodes

        return build(None)

    def summary(self) -> dict[str, Any]:
        spans = self.spans()
        total = sum(span.duration_ms or 0.0 for span in self.roots())
        return {
            "trace_id": self.trace_id,
            "span_count": len(spans),
            "root_span_count": len(self.roots()),
            "error_span_count": len(self.failed_spans()),
            "max_depth": self._max_depth(),
            "total_duration_ms": round(total, 3),
            "by_name": _count_by_name(spans),
        }

    def _max_depth(self) -> int:
        spans = self.spans()
        parent_of = {span.span_id: span.parent_span_id for span in spans}
        depth = 0
        for span in spans:
            current: str | None = span.span_id
            steps = 0
            while current is not None and steps <= len(spans):
                current = parent_of.get(current)
                steps += 1
            depth = max(depth, steps)
        return depth

    def to_dict(self) -> dict[str, Any]:
        return {
            "trace_id": self.trace_id,
            "summary": self.summary(),
            "spans": [span.model_dump(mode="json") for span in self.spans()],
        }


def _count_by_name(spans: list[Span]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for span in spans:
        counts[span.name] = counts.get(span.name, 0) + 1
    return dict(sorted(counts.items()))
