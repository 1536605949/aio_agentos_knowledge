"""工具注册表：受治理、可容错、可观测的工具调用入口。

一次 ``invoke`` 的完整链路（顺序即设计）：

1. **存在性检查** —— 未注册的工具直接拒绝，不做隐式放行。
2. **熔断检查** —— 连续失败达阈值后直接短路，避免打垮下游。
3. **治理检查（在重试循环之外）** —— 策略拒绝**不允许被重试掩盖**；
   这是早期实现里最危险的缺陷：策略拒绝被当成瞬时故障重试。
4. **权限检查** —— ``required_permission`` 必须出现在 principal 的权限集合中。
5. **出站限流** —— 按工具名隔离令牌桶，保护上游配额。
6. **幂等查询** —— 带副作用的工具传入 ``idempotency_key`` 时命中即返回，
   保证「重试不重复执行」。
7. **结果缓存** —— 只读工具按 payload 哈希缓存。
8. **资源锁** —— ``resource_field`` 指定的资源在调用期间互斥。
9. **重试循环** —— 仅对**瞬时错误**重试；``PermanentToolError`` 立即失败。
10. **失败归档** —— 最终失败写入 BadCase，构成闭环输入。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from time import monotonic, perf_counter
from typing import Any

from concurrency.locks import ResourceLockManager, ResourceLockTimeout
from governance.models import Principal
from governance.policy import PolicyEngine
from observability.badcase import BadCaseCollector
from observability.metrics import MetricsCollector
from observability.models import BadCaseCategory, SpanKind
from observability.trace import AgentTrace
from persistence.base import DocumentStore
from resilience.cache import TTLCache
from resilience.ratelimit import TokenBucketLimiter
from tools.models import ToolHandler, ToolSpec

IDEMPOTENCY_COLLECTION = "tool_idempotency"


class ToolError(RuntimeError):
    """工具调用失败的基类。"""


class ToolNotFound(ToolError):
    pass


class ToolPolicyDenied(ToolError):
    pass


class CircuitOpen(ToolError):
    pass


class ToolRateLimited(ToolError):
    pass


class TransientToolError(ToolError):
    """瞬时故障（网络抖动、下游 5xx、超时）。**会**被重试。"""


class PermanentToolError(ToolError):
    """永久故障（参数非法、资源不存在、权限不足）。**不会**被重试。"""


@dataclass
class _ToolEntry:
    spec: ToolSpec
    handler: ToolHandler
    consecutive_failures: int = 0
    circuit_open_until: float = 0.0
    invocations: int = 0
    failures: int = 0
    retries: int = 0
    cache_hits: int = 0
    idempotent_hits: int = 0
    policy_denials: int = 0
    rate_limited: int = 0
    latencies: list[float] = field(default_factory=list)


class ToolRegistry:
    """工具注册表 + 治理拦截器 + 容错执行器。"""

    def __init__(
        self,
        policy: PolicyEngine | None = None,
        circuit_threshold: int = 3,
        cooldown_seconds: float = 30.0,
        *,
        cache: TTLCache | None = None,
        limiter: TokenBucketLimiter | None = None,
        locks: ResourceLockManager | None = None,
        badcases: BadCaseCollector | None = None,
        metrics: MetricsCollector | None = None,
        store: DocumentStore | None = None,
    ) -> None:
        self._tools: dict[str, _ToolEntry] = {}
        self.policy = policy or PolicyEngine()
        self.circuit_threshold = circuit_threshold
        self.cooldown_seconds = cooldown_seconds
        self.cache = cache
        self.limiter = limiter
        self.locks = locks
        self.badcases = badcases
        self.metrics = metrics
        self.store = store
        self._idempotent: dict[str, dict[str, Any]] = {}

    # ------------------------------------------------------------ 注册与查询

    def register(self, spec: ToolSpec, handler: ToolHandler) -> None:
        self._tools[spec.name] = _ToolEntry(spec=spec, handler=handler)

    def specs(self) -> list[ToolSpec]:
        return [entry.spec for entry in self._tools.values()]

    def has(self, name: str) -> bool:
        return name in self._tools

    def get(self, name: str) -> ToolSpec:
        entry = self._tools.get(name)
        if entry is None:
            raise ToolNotFound(name)
        return entry.spec

    def stats(self) -> dict[str, dict[str, Any]]:
        now = monotonic()
        return {
            name: {
                "risk_level": int(entry.spec.risk_level),
                "invocations": entry.invocations,
                "failures": entry.failures,
                "retries": entry.retries,
                "cache_hits": entry.cache_hits,
                "idempotent_hits": entry.idempotent_hits,
                "policy_denials": entry.policy_denials,
                "rate_limited": entry.rate_limited,
                "consecutive_failures": entry.consecutive_failures,
                "circuit_open": entry.circuit_open_until > now,
                "p95_latency_ms": _p95(entry.latencies),
            }
            for name, entry in sorted(self._tools.items())
        }

    def reset_circuit(self, name: str) -> bool:
        entry = self._tools.get(name)
        if entry is None:
            return False
        entry.circuit_open_until = 0.0
        entry.consecutive_failures = 0
        return True

    def describe(self) -> list[dict[str, Any]]:
        now = monotonic()
        return [
            {
                "name": entry.spec.name,
                "version": entry.spec.version,
                "description": entry.spec.description,
                "risk_level": int(entry.spec.risk_level),
                "required_permission": entry.spec.required_permission,
                "timeout_seconds": entry.spec.timeout_seconds,
                "max_retries": entry.spec.max_retries,
                "cacheable": entry.spec.cacheable,
                "idempotent": entry.spec.idempotent,
                "circuit_open": entry.circuit_open_until > now,
                "input_schema": entry.spec.input_schema,
            }
            for entry in sorted(self._tools.values(), key=lambda item: item.spec.name)
        ]

    # ---------------------------------------------------------------- 调用入口

    async def invoke(
        self,
        name: str,
        payload: dict[str, Any],
        principal: Principal,
        trace: AgentTrace,
        approved: bool = False,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        entry = self._tools.get(name)
        if entry is None:
            raise ToolNotFound(name)
        if entry.circuit_open_until > monotonic():
            raise CircuitOpen(name)

        # --- 3/4. 治理与权限：位于重试循环之外 ---
        decision = self.policy.evaluate_tool(principal, entry.spec.risk_level, approved=approved)
        if not decision.allowed:
            entry.policy_denials += 1
            self._badcase(BadCaseCategory.POLICY_DENIED, name, decision.reason, trace)
            raise ToolPolicyDenied(decision.reason)
        permissions = self.policy.permissions_for(principal)
        if entry.spec.required_permission not in permissions:
            entry.policy_denials += 1
            reason = f"missing permission: {entry.spec.required_permission}"
            self._badcase(BadCaseCategory.POLICY_DENIED, name, reason, trace)
            raise ToolPolicyDenied(reason)

        # --- 5. 出站限流 ---
        if self.limiter is not None:
            verdict = await self.limiter.acquire(f"tool:{name}")
            if not verdict.allowed:
                entry.rate_limited += 1
                self._badcase(BadCaseCategory.RATE_LIMITED, name, verdict.reason, trace)
                raise ToolRateLimited(f"{name} rate limited; retry after {verdict.retry_after_seconds}s")

        # --- 6. 幂等 ---
        key = idempotency_key or (payload.get("idempotency_key") if entry.spec.idempotent else None)
        if entry.spec.idempotent and key:
            cached = self._idempotent_lookup(name, str(key))
            if cached is not None:
                entry.idempotent_hits += 1
                return cached

        # --- 7. 结果缓存 ---
        cache_key = None
        if entry.spec.cacheable and self.cache is not None:
            cache_key = f"tool:{name}:{_stable_hash(payload)}"
            hit = await self.cache.get(cache_key)
            if hit is not None:
                entry.cache_hits += 1
                return hit

        entry.invocations += 1
        started = perf_counter()
        last_error: Exception | None = None

        try:
            async with self._maybe_lock(entry, payload, name):
                for attempt in range(entry.spec.max_retries + 1):
                    try:
                        with trace.span(
                            "tool.invoke",
                            kind=SpanKind.TOOL,
                            tool=name,
                            attempt=attempt,
                            risk=int(entry.spec.risk_level),
                            approved=approved,
                        ):
                            result = await asyncio.wait_for(
                                entry.handler(payload), timeout=entry.spec.timeout_seconds
                            )
                        entry.consecutive_failures = 0
                        self._record_latency(entry, started, name)
                        if cache_key is not None and self.cache is not None:
                            await self.cache.set(cache_key, result, entry.spec.cache_ttl_seconds)
                        if entry.spec.idempotent and key:
                            self._idempotent_store(name, str(key), result)
                        return result
                    except PermanentToolError as exc:
                        last_error = exc
                        entry.failures += 1
                        entry.consecutive_failures += 1
                        break
                    except Exception as exc:
                        last_error = exc
                        entry.failures += 1
                        entry.consecutive_failures += 1
                        if entry.consecutive_failures >= self.circuit_threshold:
                            entry.circuit_open_until = monotonic() + self.cooldown_seconds
                            break
                        if attempt < entry.spec.max_retries:
                            entry.retries += 1
                            await asyncio.sleep(min(0.05 * (2**attempt), 0.5))
        except ResourceLockTimeout as exc:
            self._badcase(BadCaseCategory.RATE_LIMITED, name, str(exc), trace, category_hint="resource_contention")
            raise ToolError(f"resource busy for {name}: {exc}") from exc

        self._record_latency(entry, started, name)
        self._badcase(BadCaseCategory.TOOL_ERROR, name, str(last_error), trace)
        raise ToolError(f"{name} failed after retries: {last_error}") from last_error

    # ---------------------------------------------------------------- 内部实现

    @asynccontextmanager
    async def _maybe_lock(self, entry: _ToolEntry, payload: dict[str, Any], name: str):
        if self.locks is None or not entry.spec.resource_field:
            yield
            return
        resource = f"{name}:{payload.get(entry.spec.resource_field, 'unknown')}"
        async with self.locks.acquire(resource, holder=name):
            yield

    def _record_latency(self, entry: _ToolEntry, started: float, name: str) -> None:
        elapsed_ms = round((perf_counter() - started) * 1000, 3)
        entry.latencies.append(elapsed_ms)
        if len(entry.latencies) > 512:
            del entry.latencies[: len(entry.latencies) - 512]
        if self.metrics is not None:
            self.metrics.increment("tool_invocations", tool=name)
            self.metrics.observe("tool_latency_ms", elapsed_ms, tool=name)

    def _idempotent_lookup(self, tool: str, key: str) -> dict[str, Any] | None:
        scoped = f"{tool}:{key}"
        if self.store is not None:
            record = self.store.load(IDEMPOTENCY_COLLECTION, scoped)
            if record and "result" in record:
                return dict(record["result"])
            return None
        return self._idempotent.get(scoped)

    def _idempotent_store(self, tool: str, key: str, result: dict[str, Any]) -> None:
        scoped = f"{tool}:{key}"
        if self.store is not None:
            self.store.save(IDEMPOTENCY_COLLECTION, scoped, {"result": result})
            return
        self._idempotent[scoped] = result

    def _badcase(
        self,
        category: BadCaseCategory,
        tool: str,
        message: str,
        trace: AgentTrace,
        category_hint: str | None = None,
    ) -> None:
        if self.badcases is None:
            return
        self.badcases.capture(
            category,
            source=tool,
            message=message,
            trace_id=trace.trace_id,
            detail={"hint": category_hint} if category_hint else {},
        )


def _stable_hash(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:16]


def _p95(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return round(ordered[min(len(ordered) - 1, int(0.95 * len(ordered)))], 3)


__all__ = [
    "CircuitOpen",
    "PermanentToolError",
    "ToolError",
    "ToolNotFound",
    "ToolPolicyDenied",
    "ToolRateLimited",
    "ToolRegistry",
    "TransientToolError",
]
