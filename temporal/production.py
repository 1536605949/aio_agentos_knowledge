"""Temporal SDK 版本的外层持久化工作流（可选依赖）。

只有当部署环境安装了 ``temporalio`` 且配置了 Temporal 集群时才会被导入。

**与本地参考实现的关系**：两者共享同一套领域组件（本体、工具注册表、推理图），
只是持久化机制不同——

===========================  ==========================================  ==========================
关注点                        ``temporal/workflow.py``（本地参考）         本模块（Temporal SDK）
===========================  ==========================================  ==========================
持久化                        ``DocumentStore``（SQLite / 内存）           Temporal 事件历史
审批信号                      ``asyncio.Task`` + ``approve()``             ``@workflow.signal``
超时                         ``asyncio.sleep``                           ``workflow.wait_condition(timeout=...)``
重试                         ``ToolRegistry`` 的重试分级                   Temporal ``RetryPolicy`` + 工具重试
===========================  ==========================================  ==========================

关键约束：**Workflow 代码必须是确定性的**，因此所有副作用（LLM 调用、工具执行、
文件/网络 IO）都放在 Activity 里；Workflow 本体只做编排与状态迁移。
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

try:
    from temporalio import activity, workflow
except ImportError as exc:  # pragma: no cover - optional integration
    raise RuntimeError("install the orchestration extra: pip install -e '.[orchestration]'") from exc

from governance.models import Principal
from observability.trace import AgentTrace
from tools.catalog import ACTION_TO_TOOL


def _runtime():
    from bootstrap import get_runtime

    return get_runtime()


@activity.defn
async def reasoning_activity(payload: dict[str, Any]) -> dict[str, Any]:
    """在 Activity 内运行短生命周期的内层推理图。"""
    from graph.pipeline import IncidentReasoningGraph

    runtime = _runtime()
    trace = AgentTrace(trace_id=payload.get("trace_id"))
    graph = IncidentReasoningGraph(
        tools=runtime.tools,
        llm=runtime.llm,
        prompts=runtime.prompts,
        ontology=runtime.ontology,
        settings=runtime.settings,
    )
    result = await graph.run(
        payload["workflow_id"],
        trace,
        payload["alarm"],
        payload.get("logs", []),
        topology=payload.get("topology"),
        intent=payload.get("intent", "diagnose"),
    )
    return {**result, "trace_summary": trace.summary()}


@activity.defn
async def remediation_activity(payload: dict[str, Any]) -> dict[str, Any]:
    """在 Activity 内执行**受治理**的修复动作。

    注意：治理检查与幂等键都在 :class:`tools.registry.ToolRegistry` 内部完成，
    Activity 只是把参数转发过去——这样本地参考实现与 Temporal 实现走的是同一条治理路径。
    """
    runtime = _runtime()
    action = payload["action"]
    tool = payload.get("tool") or ACTION_TO_TOOL.get(action)
    if not tool or not runtime.tools.has(tool):
        return {"action": action, "executed": False, "reason": "no governed tool bound to this action"}

    trace = AgentTrace(trace_id=payload.get("trace_id"))
    return await runtime.tools.invoke(
        tool,
        {
            "service": payload.get("service", "unknown"),
            "workflow_id": payload.get("workflow_id"),
            "severity": payload.get("severity", "unknown"),
        },
        principal=Principal(subject="workflow-executor", roles={"admin"}),
        trace=trace,
        approved=bool(payload.get("approved")),
        idempotency_key=f"{payload.get('workflow_id')}:{tool}",
    )


@workflow.defn
class IncidentWorkflow:
    """故障处置工作流（确定性编排）。"""

    def __init__(self) -> None:
        self._approval: bool | None = None
        self._approver: str | None = None

    @workflow.signal
    async def approval(self, approved: bool, approver: str = "unknown") -> None:
        self._approval = approved
        self._approver = approver

    @workflow.query
    def approval_state(self) -> bool | None:
        return self._approval

    @workflow.run
    async def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        reasoning = await workflow.execute_activity(
            reasoning_activity,
            payload,
            start_to_close_timeout=timedelta(minutes=5),
            retry_policy=None,
        )

        # 本体校验未通过 -> 直接失败，不进入执行阶段
        if reasoning.get("errors"):
            return {**reasoning, "status": "failed", "reason": "ontology validation failed"}

        approved = False
        if reasoning.get("requires_approval"):
            try:
                await workflow.wait_condition(
                    lambda: self._approval is not None,
                    timeout=timedelta(seconds=payload.get("approval_timeout_seconds", 24 * 3600)),
                )
            except TimeoutError:
                return {**reasoning, "status": "timed_out", "approved": False}
            if self._approval is not True:
                return {**reasoning, "status": "rejected", "approved": False, "approver": self._approver}
            approved = True

        remediation = await workflow.execute_activity(
            remediation_activity,
            {
                "action": reasoning["proposed_action"],
                "tool": reasoning.get("action_tool"),
                "service": payload["alarm"].get("service"),
                "workflow_id": payload.get("workflow_id"),
                "severity": reasoning.get("severity"),
                "trace_id": payload.get("trace_id"),
                "approved": approved,
            },
            start_to_close_timeout=timedelta(minutes=2),
        )
        return {
            **reasoning,
            "status": "completed",
            "approved": approved,
            "approver": self._approver,
            "remediation_result": remediation,
        }


__all__ = ["IncidentWorkflow", "reasoning_activity", "remediation_activity"]
