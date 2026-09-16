"""命令行入口。

::

    aio-agentos check     # 自检：配置、本体、技能、路由、工具、端到端链路
    aio-agentos demo      # 跑通一次完整的「告警 -> 审批 -> 受治理修复」
    aio-agentos serve     # 启动 API（等价于 uvicorn api.app:app）

也可以直接 ``python -m cli check``。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from typing import Any

from bootstrap import build_runtime, build_workflow_service
from governance.models import Principal
from observability.trace import AgentTrace
from temporal.models import TERMINAL_STATUSES, WorkflowStatus

TERMINAL_VALUES = {status.value for status in TERMINAL_STATUSES}


def _print(title: str, payload: Any) -> None:
    print(f"\n=== {title} ===")
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


async def _tool_probes(runtime) -> dict[str, Any]:
    """把工具层的四条关键路径都真的跑一遍。"""
    trace = AgentTrace()
    admin = Principal(subject="check", roles={"admin"})
    viewer = Principal(subject="check", roles={"viewer"})
    probes: dict[str, Any] = {}

    probes["low_risk_ok"] = await runtime.tools.invoke(
        "topology.lookup", {"service": "checkout"}, viewer, trace
    )

    try:
        await runtime.tools.invoke("service.restart", {"service": "checkout"}, admin, trace, approved=False)
        probes["high_risk_without_approval"] = "BUG: should have been denied"
    except Exception as exc:  # noqa: BLE001 - 自检需要报告任意异常
        probes["high_risk_without_approval"] = f"{type(exc).__name__}: {exc}"

    probes["high_risk_with_approval"] = await runtime.tools.invoke(
        "service.restart", {"service": "checkout"}, admin, trace, approved=True
    )

    probes["retry_succeeded"] = await runtime.tools.invoke(
        "demo.flaky", {"fail_times": 2}, viewer, trace
    )

    try:
        await runtime.tools.invoke("service.restart", {"service": ""}, admin, trace, approved=True)
        probes["permanent_error_not_retried"] = "BUG: should have raised"
    except Exception as exc:  # noqa: BLE001
        probes["permanent_error_not_retried"] = type(exc).__name__

    # 幂等：同一 key 第二次调用不应产生新的副作用
    first = await runtime.tools.invoke(
        "service.restart",
        {"service": "checkout"},
        admin,
        trace,
        approved=True,
        idempotency_key="check-idem-1",
    )
    second = await runtime.tools.invoke(
        "service.restart",
        {"service": "checkout"},
        admin,
        trace,
        approved=True,
        idempotency_key="check-idem-1",
    )
    probes["idempotent_replay_same_result"] = first == second

    return probes


async def _end_to_end(runtime) -> dict[str, Any]:
    service = build_workflow_service(runtime)
    state = await service.start(
        alarm={"alarm_id": "CHK-1", "service": "checkout", "severity": "critical"},
        logs=["upstream timeout while calling payment"],
    )
    for _ in range(300):
        state = service.get(state.workflow_id)
        if state.status.value in TERMINAL_VALUES or state.status is WorkflowStatus.WAITING_APPROVAL:
            break
        await asyncio.sleep(0.01)

    if state.status is WorkflowStatus.WAITING_APPROVAL:
        state = await service.approve(
            state.workflow_id, True, Principal(subject="check", roles={"approver"})
        )

    trace = service.trace(state.workflow_id)
    episodes = service.memory.list(state.workflow_id)
    return {
        "status": state.status.value,
        "root_cause": state.root_cause,
        "confidence": state.confidence,
        "proposed_action": state.proposed_action,
        "action_tool": state.action_tool,
        "ontology_errors": state.ontology_errors,
        "steps": state.steps,
        "trace_summary": trace.summary(),
        "episode_outcome": episodes[0].outcome if episodes else None,
    }


def cmd_check(_: argparse.Namespace) -> int:
    """自检：把所有主要模块真的跑一遍，而不是只 import。"""
    runtime = build_runtime()
    failures: list[str] = []

    _print("配置与运行时", runtime.health())
    _print("本体", runtime.ontology.summary())
    _print("路由计划", [decision.model_dump() for decision in runtime.router.plan()])

    probes = asyncio.run(_tool_probes(runtime))
    _print("工具探针", probes)
    for name, value in probes.items():
        if isinstance(value, str) and value.startswith("BUG"):
            failures.append(f"tool probe {name}: {value}")
    if probes["high_risk_without_approval"].endswith("should have been denied"):
        failures.append("high-risk tool ran without approval")
    if not probes["idempotent_replay_same_result"]:
        failures.append("idempotent replay produced a different result")

    result = asyncio.run(_end_to_end(runtime))
    _print("端到端", result)
    if result["status"] != "completed":
        failures.append(f"end-to-end did not complete: {result['status']}")
    if result["ontology_errors"]:
        failures.append(f"ontology errors: {result['ontology_errors']}")
    if result["trace_summary"]["max_depth"] < 2:
        failures.append("trace is flat (no parent/child hierarchy)")

    _print("BadCase 汇总", runtime.badcases.summary())
    _print("指标计数器", runtime.metrics.snapshot()["counters"])
    _print("LLM 用量", runtime.llm.stats())

    if failures:
        print("\n自检未通过：")
        for item in failures:
            print(f"  - {item}")
        return 1
    print("\n自检通过。")
    return 0


def cmd_demo(args: argparse.Namespace) -> int:
    from examples.demo import main as demo_main

    asyncio.run(demo_main(service_name=args.service))
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    uvicorn.run("api.app:app", host=args.host, port=args.port, reload=args.reload)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="aio-agentos", description="AIO-AgentOS 运维入口")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("check", help="自检所有主要模块").set_defaults(func=cmd_check)

    demo = sub.add_parser("demo", help="跑通端到端纵向切片")
    demo.add_argument("--service", default="checkout", help="受影响服务名")
    demo.set_defaults(func=cmd_demo)

    serve = sub.add_parser("serve", help="启动 FastAPI 服务")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument("--reload", action="store_true")
    serve.set_defaults(func=cmd_serve)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())


__all__ = ["build_parser", "cmd_check", "cmd_demo", "cmd_serve", "main"]
