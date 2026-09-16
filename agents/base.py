"""Agent 基类。

三类职责被刻意分开：

1. **生命周期**（:class:`runtime.lifecycle.AgentLifecycle`）—— 显式状态机，非法迁移直接抛错。
2. **可观测**（:class:`observability.trace.AgentTrace`）—— 每个 Agent 调用都是一个带层级的 Span。
3. **LLM 交互**（:meth:`BaseAgent.call_llm`）—— 提示词从注册表取、变量由本体词表渲染、
   返回体解析为 JSON 对象并记录 provider / token / 是否降级。

Agent 依赖全部通过构造函数注入（LLM、提示词、本体、工具注册表、主体身份），
因此可以在测试里替换任意一层而不改 Agent 代码。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from agents.specs import AgentSpec
from governance.models import Principal
from llm import (
    LLMClient,
    LLMMessage,
    LLMRequest,
    LLMResponse,
    LLMRole,
    PromptRegistry,
    build_default_registry,
    build_llm,
)
from observability.models import SpanKind
from observability.trace import AgentTrace
from ontology.domain import build_ontology
from ontology.models import OntologyRegistry
from runtime.context import AgentContext
from runtime.lifecycle import AgentLifecycle, transition
from tools.registry import ToolRegistry


class AgentOutputError(RuntimeError):
    """Agent 输出不符合 :attr:`AgentSpec.output_schema` 声明的字段。"""


class BaseAgent(ABC):
    """所有 Agent 的公共骨架。"""

    spec: AgentSpec

    def __init__(
        self,
        llm: LLMClient | None = None,
        prompts: PromptRegistry | None = None,
        ontology: OntologyRegistry | None = None,
        tools: ToolRegistry | None = None,
        principal: Principal | None = None,
    ) -> None:
        self.llm: LLMClient = llm or build_llm()
        self.prompts: PromptRegistry = prompts or build_default_registry()
        self.ontology: OntologyRegistry = ontology or build_ontology()
        self.tools = tools
        self.principal = principal or Principal(subject=f"agent:{self.spec.name}", roles={"operator"})

    # ------------------------------------------------------------------ 执行

    async def run(self, context: AgentContext, trace: AgentTrace) -> dict[str, Any]:
        context.lifecycle = transition(context.lifecycle, AgentLifecycle.RUNNING)
        try:
            with trace.span(
                "agent.run",
                kind=SpanKind.AGENT,
                agent=self.spec.name,
                capability=self.spec.capabilities[0] if self.spec.capabilities else "",
            ):
                output = await self.execute(context, trace)
            self.validate_output(output)
            context.outputs = output
            context.lifecycle = transition(context.lifecycle, AgentLifecycle.COMPLETED)
            return output
        except Exception:
            context.lifecycle = transition(context.lifecycle, AgentLifecycle.FAILED)
            raise

    @abstractmethod
    async def execute(self, context: AgentContext, trace: AgentTrace) -> dict[str, Any]:
        raise NotImplementedError

    def validate_output(self, output: dict[str, Any]) -> None:
        """按 ``output_schema.properties`` 校验必需字段。"""
        properties = (self.spec.output_schema or {}).get("properties") or {}
        missing = [name for name in properties if name not in output]
        if missing:
            raise AgentOutputError(f"{self.spec.name} output missing declared fields: {missing}")

    # ------------------------------------------------------- LLM 调用助手

    async def call_llm(
        self,
        *,
        task: str,
        system_prompt: str,
        user_prompt: str,
        variables: dict[str, Any],
        trace: AgentTrace,
        metadata: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], LLMResponse]:
        """渲染提示词 -> 调用 LLM -> 解析 JSON 对象。

        提示词由 :class:`llm.prompts.PromptRegistry` 提供并带版本号，
        因此 Span 上会记录 ``prompt_version``，便于回溯"这次输出是哪版提示词产生的"。
        """
        system_template = self.prompts.get(system_prompt)
        user_template = self.prompts.get(user_prompt)
        request = LLMRequest(
            messages=[
                LLMMessage(role=LLMRole.SYSTEM, content=system_template.render(**variables)),
                LLMMessage(role=LLMRole.USER, content=user_template.render(**variables)),
            ],
            metadata={"task": task, **(metadata or {})},
        )
        with trace.span(
            "llm.complete",
            kind=SpanKind.LLM,
            task=task,
            prompt=system_prompt,
            prompt_version=system_template.version,
        ) as span:
            response = await self.llm.complete(request)
            span.attributes.update(
                {
                    "provider": response.provider,
                    "model": response.model,
                    "total_tokens": response.usage.total_tokens,
                    "fallback_used": response.fallback_used,
                    "latency_ms": response.latency_ms,
                }
            )
        return response.json_object(), response

    # ---------------------------------------------------------- 工具调用助手

    async def call_tool(self, name: str, payload: dict[str, Any], trace: AgentTrace) -> dict[str, Any]:
        """调用受治理的工具。未注入注册表或工具不存在时返回 ``None`` 语义由调用方决定。"""
        if self.tools is None or not self.tools.has(name):
            raise AgentOutputError(f"tool not available to agent {self.spec.name}: {name}")
        return await self.tools.invoke(name, payload, principal=self.principal, trace=trace)


__all__ = ["AgentOutputError", "BaseAgent"]
