from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from threading import RLock
from time import perf_counter
from typing import Any, Iterator
from uuid import uuid4

from observability.models import Span, SpanStatus


class AgentTrace:
    """In-memory trace recorder used by every layer, including failure paths."""

    def __init__(self, trace_id: str | None = None) -> None:
        self.trace_id = trace_id or str(uuid4())
        self._spans: list[Span] = []
        self._lock = RLock()

    @contextmanager
    def span(self, name: str, parent_span_id: str | None = None, **attributes: Any) -> Iterator[Span]:
        span = Span(trace_id=self.trace_id, parent_span_id=parent_span_id, name=name, attributes=attributes)
        started = perf_counter()
        try:
            yield span
        except Exception as exc:
            span.status = SpanStatus.ERROR
            span.error = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            span.ended_at = datetime.now(timezone.utc)
            span.duration_ms = round((perf_counter() - started) * 1000, 3)
            with self._lock:
                self._spans.append(span)

    def spans(self) -> list[Span]:
        with self._lock:
            return list(self._spans)
