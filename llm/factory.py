"""LLM 客户端工厂。

选择顺序由 ``AIO_AGENTOS_LLM_PROVIDER`` 决定：
``deterministic``（默认） / ``openai`` / ``langchain``。

任何真实 provider 构建失败时都会自动降级为确定性实现，
保证服务不会因为缺少凭据而整体不可用。
"""

from __future__ import annotations

from typing import Any

from config import Settings, get_settings
from llm.base import LLMClient, LLMError, LLMRequest, LLMResponse
from llm.deterministic import DeterministicLLM


class ResilientLLM:
    """带降级与用量统计的 LLM 包装器。

    - 主 provider 抛 :class:`LLMError` 时切到 fallback，并在响应上标记 ``fallback_used``。
    - 累计调用次数、失败次数与 token 用量，供 ``/metrics`` 端点暴露。
    """

    provider = "resilient"

    def __init__(self, primary: LLMClient, fallback: LLMClient | None = None) -> None:
        self._primary = primary
        self._fallback = fallback or DeterministicLLM()
        self.model = getattr(primary, "model", "unknown")
        self.calls = 0
        self.failures = 0
        self.fallbacks = 0
        self.prompt_tokens = 0
        self.completion_tokens = 0

    @property
    def primary_provider(self) -> str:
        return getattr(self._primary, "provider", "unknown")

    @property
    def fallback_provider(self) -> str:
        return getattr(self._fallback, "provider", "unknown")

    async def complete(self, request: LLMRequest) -> LLMResponse:
        self.calls += 1
        try:
            response = await self._primary.complete(request)
        except LLMError:
            self.failures += 1
            self.fallbacks += 1
            response = await self._fallback.complete(request)
            response.fallback_used = True
        self.prompt_tokens += response.usage.prompt_tokens
        self.completion_tokens += response.usage.completion_tokens
        return response

    def stats(self) -> dict[str, Any]:
        total = self.prompt_tokens + self.completion_tokens
        return {
            "primary_provider": self.primary_provider,
            "fallback_provider": self.fallback_provider,
            "model": self.model,
            "calls": self.calls,
            "failures": self.failures,
            "fallback_used": self.fallbacks,
            "fallback_rate": round(self.fallbacks / self.calls, 4) if self.calls else 0.0,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": total,
        }


def build_llm(settings: Settings | None = None) -> ResilientLLM:
    """按配置构建 LLM 客户端，失败时自动降级。"""
    settings = settings or get_settings()
    provider = settings.llm_provider
    primary: LLMClient

    if provider in {"openai", "openai-compatible", "deepseek"}:
        try:
            from llm.openai_compatible import OpenAICompatibleLLM

            primary = OpenAICompatibleLLM(
                model=settings.llm_model,
                api_key=settings.llm_api_key,
                base_url=settings.llm_base_url,
                timeout_seconds=settings.llm_timeout_seconds,
                max_retries=settings.llm_max_retries,
            )
        except LLMError:
            primary = DeterministicLLM()
    elif provider == "langchain":
        try:
            from llm.langchain_adapter import LangChainLLM

            primary = LangChainLLM(
                model=settings.llm_model,
                api_key=settings.llm_api_key,
                base_url=settings.llm_base_url,
            )
        except LLMError:
            primary = DeterministicLLM()
    else:
        primary = DeterministicLLM(model=settings.llm_model)

    return ResilientLLM(primary=primary, fallback=DeterministicLLM())
