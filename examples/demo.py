"""端到端纵向切片演示。

展示完整链路：::

    告警 -> 路由(意图->技能->Agent) -> 归一化 -> 拓扑/日志取证
         -> LLM 根因推理 -> LLM 动作规划 -> 本体公理校验
         -> 高风险动作挂起等待审批 -> 人工批准
         -> 受治理工具执行（策略/权限/幂等/资源锁） -> 完成 + Trace + Episodic Memory

运行：``python -m examples.demo`` 或 ``aio-agentos demo``
"""

from __future__ import annotations

import asyncio
import json

from bootstrap import build_runtime, build_workflow_service
from governance.models import Principal
from temporal.models import TERMINAL_STATUSES, WorkflowStatus

TERMINAL_VALUES = {status.value for status in TERMINAL_STATUSES}


def _show(title: str, payload) -> None:
    print(f"\n--- {title} ---")
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


async def main(service_name: str = "checkout", auto_approve: bool = True) -> None:
    runtime = build_runtime()
    service = build_workflow_service(runtime)

    print(f"运行时：{json.dumps(runtime.health(), ensure_ascii=False)}")

    state = await service.start(
        alarm={
            "alarm_id": "A-1001",
            "service": service_name,
            "severity": "critical",
            "message": "checkout latency spike, 5xx rate 12%",
        },
        logs=[
            "upstream timeout while calling payment",
            "payment p99 latency 8.2s exceeds 3s budget",
        ],
        intent="diagnose",
        idempotency_key="demo-A-1001",
    )

    for _ in range(300):
        state = service.get(state.workflow_id)
        if state.status.value in TERMINAL_VALUES or state.status is WorkflowStatus.WAITING_APPROVAL:
            break
        await asyncio.sleep(0.01)

    _show(
        "推理完成，等待审批门",
        {
            "workflow_id": state.workflow_id,
            "status": state.status.value,
            "intent": state.intent,
            "severity": state.severity,
            "topology": state.topology,
            "evidence": state.evidence,
            "root_cause": state.root_cause,
            "confidence": state.confidence,
            "proposed_action": state.proposed_action,
            "action_tool": state.action_tool,
            "approval_required": state.approval_required,
            "ontology_errors": state.ontology_errors,
            "steps": state.steps,
        },
    )

    if state.status is WorkflowStatus.WAITING_APPROVAL and auto_approve:
        state = await service.approve(
            state.workflow_id,
            approved=True,
            principal=Principal(subject="oncall@example.com", roles={"approver"}),
        )
        _show(
            "审批通过，受治理执行完成",
            {
                "status": state.status.value,
                "approved_by": state.approved_by,
                "remediation_result": state.remediation_result,
            },
        )

    trace = service.trace(state.workflow_id)
    _show("Trace 摘要", trace.summary())
    print("\n调用树：")
    for node in trace.tree():
        _print_tree(node)

    episodes = service.memory.list(state.workflow_id)
    _show("Episodic Memory", [episode.model_dump(mode="json") for episode in episodes])
    _show("BadCase 汇总", runtime.badcases.summary())
    _show("LLM 用量", runtime.llm.stats())


def _print_tree(node: dict, depth: int = 0) -> None:
    status = node.get("status")
    marker = "!" if status == "error" else "-"
    print(f"{'  ' * depth}{marker} [{node.get('kind')}] {node.get('name')} ({node.get('duration_ms')}ms)")
    for child in node.get("children", []):
        _print_tree(child, depth + 1)


if __name__ == "__main__":
    asyncio.run(main())
