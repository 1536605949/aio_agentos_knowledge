"""组装根（composition root）。

把配置、LLM、提示词、本体、技能、路由、工具、持久化、弹性与可观测性
装配成一个 :class:`Runtime`，供 API / CLI / Worker 复用。

**为什么需要它**：早期实现里依赖是"到处 new"的——``api/app.py`` 直接构造
``ToolRegistry``，``temporal/workflow.py`` 直接构造 ``IncidentReasoningGraph``，
导致同一份配置被读多次、限流/缓存/持久化等横切能力根本没有注入点。
集中装配后，替换任意一层（例如把 memory store 换成 PostgreSQL）只需改这里一处。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agents.implementations import build_agents
from agents.registry import AgentRegistry
from concurrency.locks import ResourceLockManager
from config import Settings, get_settings
from llm.factory import ResilientLLM, build_llm
from llm.prompts import PromptRegistry, build_default_registry
from memory.store import InMemoryMemoryStore
from observability.badcase import BadCaseCollector
from observability.metrics import MetricsCollector
from ontology.domain import build_ontology
from ontology.models import OntologyRegistry
from persistence.base import DocumentStore
from persistence.factory import build_store
from resilience.cache import TTLCache
from resilience.ratelimit import TokenBucketLimiter
from router.router import RouteSkillRouter
from skills.catalog import build_default_skill_registry
from skills.registry import SkillRegistry
from tools.catalog import build_default_tool_registry
from tools.registry import ToolRegistry


@dataclass
class Runtime:
    """一个装配完成的运行时。所有字段都是可替换的实现，不是具体类。"""

    settings: Settings
    llm: ResilientLLM
    prompts: PromptRegistry
    ontology: OntologyRegistry
    skills: SkillRegistry
    router: RouteSkillRouter
    tools: ToolRegistry
    agents: AgentRegistry
    store: DocumentStore
    cache: TTLCache
    limiter: TokenBucketLimiter
    locks: ResourceLockManager
    metrics: MetricsCollector
    badcases: BadCaseCollector
    memory: InMemoryMemoryStore

    def close(self) -> None:
        self.store.close()

    def health(self) -> dict[str, Any]:
        return {
            "environment": self.settings.environment.value,
            "llm_provider": self.llm.primary_provider,
            "store_backend": getattr(self.store, "backend", "unknown"),
            "ontology_version": self.ontology.version.version,
            "skills": len(self.skills.skills),
            "tools": len(self.tools.specs()),
            "agents": len(self.agents.names()),
            "security_warnings": self.settings.validate_security_posture(),
        }


def build_runtime(settings: Settings | None = None) -> Runtime:
    """按配置装配完整运行时。"""
    settings = settings or get_settings()

    store = build_store(settings)
    metrics = MetricsCollector()
    badcases = BadCaseCollector(store=store, export_path=settings.badcase_export_path)
    cache: TTLCache = TTLCache(ttl_seconds=settings.cache_ttl_seconds, max_entries=settings.cache_max_entries)
    limiter = TokenBucketLimiter(
        rate_per_minute=settings.rate_limit_per_minute,
        burst=settings.rate_limit_burst,
    )
    locks = ResourceLockManager(default_timeout_seconds=settings.resource_lock_timeout_seconds)

    tools = build_default_tool_registry(
        circuit_threshold=settings.tool_circuit_threshold,
        cooldown_seconds=settings.tool_circuit_cooldown_seconds,
        cache=cache,
        limiter=limiter,
        locks=locks,
        badcases=badcases,
        metrics=metrics,
        store=store,
    )

    ontology = build_ontology()
    skills = build_default_skill_registry()
    llm = build_llm(settings)
    prompts = build_default_registry()

    return Runtime(
        settings=settings,
        llm=llm,
        prompts=prompts,
        ontology=ontology,
        skills=skills,
        router=RouteSkillRouter(skills),
        tools=tools,
        agents=AgentRegistry(build_agents(llm=llm, prompts=prompts, ontology=ontology, tools=tools)),
        store=store,
        cache=cache,
        limiter=limiter,
        locks=locks,
        metrics=metrics,
        badcases=badcases,
        memory=InMemoryMemoryStore(),
    )


__all__ = ["Runtime", "build_runtime", "build_workflow_service", "get_runtime", "reset_runtime"]


def build_workflow_service(runtime: Runtime | None = None):
    """用运行时依赖装配外层工作流服务。

    放在组装根里，是为了保证"服务用的 LLM / 提示词 / 本体 / 工具"
    与 ``Runtime`` 里声明的是**同一批对象**，而不是各自 new 一份。
    """
    from graph.pipeline import IncidentReasoningGraph
    from temporal.workflow import IncidentWorkflowService

    runtime = runtime or get_runtime()
    graph = IncidentReasoningGraph(
        tools=runtime.tools,
        llm=runtime.llm,
        prompts=runtime.prompts,
        ontology=runtime.ontology,
        settings=runtime.settings,
    )
    return IncidentWorkflowService(
        tools=runtime.tools,
        memory=runtime.memory,
        settings=runtime.settings,
        store=runtime.store,
        locks=runtime.locks,
        limiter=runtime.limiter,
        badcases=runtime.badcases,
        metrics=runtime.metrics,
        ontology=runtime.ontology,
        router=runtime.router,
        graph=graph,
    )


_runtime: Runtime | None = None


def get_runtime(refresh: bool = False) -> Runtime:
    """进程内单例运行时。API / Worker / CLI 共用同一份装配结果。"""
    global _runtime
    if _runtime is None or refresh:
        _runtime = build_runtime()
    return _runtime


def reset_runtime() -> None:
    """释放单例（测试用）。"""
    global _runtime
    if _runtime is not None:
        _runtime.close()
    _runtime = None
