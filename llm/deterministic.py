"""确定性 LLM 实现。

用途：
1. **让系统在没有 API Key 的环境下也能完整跑通**（CI、本地开发、演示）。
2. 作为真实模型的**回归基线**——同一套 Prompt 契约下的参考输出。

它不是"假装成模型的 if/elif"：它实现完整的 :class:`LLMClient` 协议，
接收 Prompt 渲染结果、返回结构化 JSON、上报 usage 与延迟。
把 ``AIO_AGENTOS_LLM_PROVIDER`` 切成 ``openai`` 即可替换为真实模型，
上层 Agent 代码无需任何改动。
"""

from __future__ import annotations

import json
import re
from time import perf_counter
from typing import Any

from llm.base import (
    LLMRequest,
    LLMResponse,
    LLMUsage,
)

# 领域信号词典：用于在无真实模型时给出可复现的判断。
_ROOT_CAUSE_SIGNALS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("database_dependency_failure", ("database", "db connection", "connection refused", "deadlock", "sql")),
    ("upstream_timeout", ("timeout", "timed out", "deadline exceeded", "gateway timeout", "504")),
    ("resource_exhaustion", ("oom", "out of memory", "memory limit", "cpu throttl", "disk full", "no space")),
    ("config_regression", ("config", "feature flag", "misconfigur", "invalid setting")),
    ("network_partition", ("network", "packet loss", "dns", "unreachable", "connection reset")),
)

_SEVERITY_SIGNALS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("critical", ("critical", "sev1", "p0", "fatal", "outage", "down")),
    ("high", ("high", "sev2", "p1", "error", "fail")),
    ("medium", ("medium", "sev3", "p2", "warn")),
    ("low", ("low", "sev4", "p3", "info", "notice")),
)

_ACTION_BY_CAUSE: dict[str, str] = {
    "upstream_timeout": "restart_service",
    "resource_exhaustion": "restart_service",
    "config_regression": "rollback_config",
    "network_partition": "escalate_to_network_team",
    "database_dependency_failure": "escalate_to_dba",
}


class DeterministicLLM:
    """基于规则的可复现 LLM 实现，实现 :class:`LLMClient` 协议。"""

    provider = "deterministic"

    def __init__(self, model: str = "deterministic-rule-v1") -> None:
        self.model = model
        self.call_count = 0

    async def complete(self, request: LLMRequest) -> LLMResponse:
        started = perf_counter()
        self.call_count += 1
        task = str(request.metadata.get("task", "generic"))
        user_text = self._last_user_content(request)
        prompt_chars = sum(len(message.content) for message in request.messages)

        if task == "reasoning":
            payload = self._reason(request, user_text)
        elif task == "remediation":
            payload = self._remediate(request, user_text)
        elif task == "alarm":
            payload = self._normalize_alarm(user_text)
        else:
            payload = {"result": user_text.strip()[:200]}

        text = json.dumps(payload, ensure_ascii=False)
        return LLMResponse(
            text=text,
            provider=self.provider,
            model=self.model,
            usage=LLMUsage(
                prompt_tokens=max(1, prompt_chars // 4),
                completion_tokens=max(1, len(text) // 4),
                total_tokens=max(1, (prompt_chars + len(text)) // 4),
            ),
            latency_ms=round((perf_counter() - started) * 1000, 3),
        )

    # ------------------------------------------------------------------ helpers

    @staticmethod
    def _last_user_content(request: LLMRequest) -> str:
        for message in reversed(request.messages):
            if message.role.value == "user":
                return message.content
        return ""

    @staticmethod
    def _match(text: str, table: tuple[tuple[str, tuple[str, ...]], ...]) -> str | None:
        for label, keywords in table:
            if any(keyword in text for keyword in keywords):
                return label
        return None

    def _reason(self, request: LLMRequest, user_text: str) -> dict[str, Any]:
        haystack = user_text.lower()
        allowed = request.metadata.get("allowed_causes") or []
        cause = self._match(haystack, _ROOT_CAUSE_SIGNALS)
        if cause is None or (allowed and cause not in allowed):
            cause = "undetermined"

        matched_signals = [label for label, keywords in _ROOT_CAUSE_SIGNALS if any(k in haystack for k in keywords)]
        confidence = 0.0
        if cause != "undetermined":
            confidence = round(min(0.9, 0.55 + 0.15 * len(matched_signals)), 2)
        elif matched_signals:
            confidence = 0.25

        refs = self._extract_evidence_lines(user_text)
        return {
            "root_cause": cause,
            "confidence": confidence,
            "rationale": (
                f"命中信号 {matched_signals or ['无']}；"
                f"在 {len(refs)} 条证据中匹配到根因 {cause}。"
            ),
            "evidence_refs": refs[:5],
        }

    def _remediate(self, request: LLMRequest, user_text: str) -> dict[str, Any]:
        haystack = user_text.lower()
        cause = self._match(haystack, _ROOT_CAUSE_SIGNALS) or "undetermined"
        allowed = request.metadata.get("allowed_actions") or []
        action = _ACTION_BY_CAUSE.get(cause, "collect_more_evidence")
        if allowed and action not in allowed:
            action = "collect_more_evidence"
        return {
            "action": action,
            "rationale": f"根因 {cause} 对应的标准处置动作为 {action}。",
            "risk_notes": "restart_service 属高风险动作，需人工审批。" if action == "restart_service" else "",
        }

    def _normalize_alarm(self, user_text: str) -> dict[str, Any]:
        haystack = user_text.lower()
        severity = self._match(haystack, _SEVERITY_SIGNALS) or "unknown"
        category = self._match(haystack, _ROOT_CAUSE_SIGNALS) or "unclassified"
        summary = re.sub(r"\s+", " ", user_text).strip()[:180]
        return {"severity": severity, "category": category, "summary": summary}

    @staticmethod
    def _extract_evidence_lines(user_text: str) -> list[str]:
        lines = [line.strip(" -•\t") for line in user_text.splitlines()]
        return [line for line in lines if line and not line.endswith("：") and "：" not in line[:6]][:10]
