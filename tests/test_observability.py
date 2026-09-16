"""可观测性测试：Span 父子层级、调用树、指标采集、BadCase 闭环。"""

from __future__ import annotations

import asyncio

import pytest

from observability import (
    AgentTrace,
    BadCaseCategory,
    BadCaseCollector,
    Feedback,
    MetricsCollector,
    SpanKind,
)
from persistence import InMemoryDocumentStore

# ------------------------------------------------------------------ Trace

def test_spans_nest_into_a_tree():
    trace = AgentTrace()
    with trace.span("workflow.run", kind=SpanKind.WORKFLOW):
        with trace.span("agent.run", kind=SpanKind.AGENT, agent="reasoning"):
            with trace.span("llm.complete", kind=SpanKind.LLM):
                pass

    summary = trace.summary()
    assert summary["span_count"] == 3
    assert summary["root_span_count"] == 1
    assert summary["max_depth"] == 3
    assert summary["error_span_count"] == 0

    tree = trace.tree()
    assert len(tree) == 1
    assert tree[0]["name"] == "workflow.run"
    assert tree[0]["children"][0]["name"] == "agent.run"
    assert tree[0]["children"][0]["children"][0]["kind"] == "llm"


def test_sibling_spans_share_the_same_parent():
    trace = AgentTrace()
    with trace.span("workflow.run"):
        with trace.span("agent.run", agent="log"):
            pass
        with trace.span("agent.run", agent="reasoning"):
            pass

    roots = trace.roots()
    assert len(roots) == 1
    children = trace.children_of(roots[0].span_id)
    assert len(children) == 2
    assert {child.attributes["agent"] for child in children} == {"log", "reasoning"}


def test_failed_span_is_recorded_on_exception():
    trace = AgentTrace()
    with pytest.raises(RuntimeError):
        with trace.span("tool.invoke", kind=SpanKind.TOOL, tool="service.restart"):
            raise RuntimeError("boom")

    failed = trace.failed_spans()
    assert len(failed) == 1
    assert failed[0].status.value == "error"
    assert "RuntimeError: boom" in (failed[0].error or "")
    # 异常路径也必须落盘
    assert trace.summary()["span_count"] == 1


def test_span_stack_is_reset_after_exit():
    """退出 Span 后上下文栈必须复位，否则后续 Span 会挂到错误的父节点上。"""
    trace = AgentTrace()
    with trace.span("first"):
        pass
    with trace.span("second"):
        pass
    assert trace.summary()["root_span_count"] == 2


def test_trace_to_dict_and_duration():
    trace = AgentTrace("fixed-trace-id")
    with trace.span("op"):
        pass
    payload = trace.to_dict()
    assert payload["trace_id"] == "fixed-trace-id"
    assert payload["spans"][0]["duration_ms"] is not None


# ---------------------------------------------------------------- Metrics

def test_metrics_counters_gauges_and_percentiles():
    metrics = MetricsCollector()
    metrics.increment("workflow_started", intent="diagnose")
    metrics.increment("workflow_started", intent="diagnose")
    metrics.increment("workflow_started", intent="remediate")
    metrics.gauge("queue_depth", 7)
    for value in (10.0, 20.0, 30.0, 40.0, 100.0):
        metrics.observe("tool_latency_ms", value, tool="service.restart")

    assert metrics.counter("workflow_started", intent="diagnose") == 2
    snapshot = metrics.snapshot()
    assert snapshot["counters"]["aio_agentos_workflow_started{intent=diagnose}"] == 2
    assert snapshot["gauges"]["aio_agentos_queue_depth"] == 7
    assert snapshot["latency_ms"]["aio_agentos_tool_latency_ms{tool=service.restart}"]["max"] == 100.0
    assert metrics.percentile("tool_latency_ms", 0.5, tool="service.restart") == 30.0

    assert any(sample.unit == "ms" for sample in metrics.samples())
    metrics.reset()
    assert metrics.snapshot()["counters"] == {}


# ---------------------------------------------------------------- BadCase

def test_badcase_collector_captures_and_summarises():
    store = InMemoryDocumentStore()
    collector = BadCaseCollector(store=store)

    collector.capture(BadCaseCategory.TOOL_ERROR, "service.restart", "downstream 500", workflow_id="w1")
    collector.capture(BadCaseCategory.ONTOLOGY_VIOLATION, "supervisor", "root_cause=made_up", workflow_id="w1")
    collector.capture(BadCaseCategory.TOOL_ERROR, "config.rollback", "timeout", workflow_id="w2")

    assert collector.count() == 3
    summary = collector.summary()
    assert summary["total"] == 3
    assert summary["by_category"]["tool_error"] == 2
    assert summary["by_source"]["service.restart"] == 1

    only_tool_errors = collector.list(category=BadCaseCategory.TOOL_ERROR)
    assert len(only_tool_errors) == 2

    limited = collector.list(limit=1)
    assert len(limited) == 1


def test_badcase_from_feedback_escalates_negative_ratings():
    collector = BadCaseCollector()
    good = Feedback(trace_id="t1", rating=5, comment="great")
    assert collector.from_feedback(good) is None

    bad = Feedback(trace_id="t2", rating=1, comment="wrong root cause", workflow_id="w1")
    case = collector.from_feedback(bad)
    assert case is not None
    assert case.category is BadCaseCategory.NEGATIVE_FEEDBACK
    assert case.workflow_id == "w1"
    assert collector.count() == 1

    flagged = Feedback(trace_id="t3", rating=4, bad_case=True)
    assert collector.from_feedback(flagged) is not None


def test_badcase_resolve_marks_record():
    store = InMemoryDocumentStore()
    collector = BadCaseCollector(store=store)
    case = collector.capture(BadCaseCategory.TOOL_ERROR, "svc", "boom")
    assert collector.summary()["unresolved"] == 1

    assert collector.resolve(case.id) is True
    assert collector.summary()["unresolved"] == 0
    assert collector.list(unresolved_only=True) == []


def test_badcase_export_to_jsonl(tmp_path):
    path = tmp_path / "nested" / "badcases.jsonl"
    collector = BadCaseCollector(export_path=str(path))
    collector.capture(BadCaseCategory.RATE_LIMITED, "api", "too many requests")

    lines = path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    assert "rate_limited" in lines[0]


def test_badcase_survives_store_roundtrip():
    store = InMemoryDocumentStore()
    first = BadCaseCollector(store=store)
    case = first.capture(BadCaseCategory.POLICY_DENIED, "tool", "denied", workflow_id="w9")

    second = BadCaseCollector(store=store)
    reloaded = second.list()
    assert len(reloaded) == 1
    assert reloaded[0].id == case.id
    assert reloaded[0].category is BadCaseCategory.POLICY_DENIED


# ----------------------------------------------------------------- 并发安全

async def test_trace_is_safe_under_concurrent_agents():
    trace = AgentTrace()

    async def worker(name: str) -> None:
        with trace.span("agent.run", agent=name):
            await asyncio.sleep(0)

    await asyncio.gather(*(worker(f"a{i}") for i in range(8)))
    assert trace.summary()["span_count"] == 8
    assert trace.summary()["root_span_count"] == 8
