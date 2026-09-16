"""LangChain 适配器（可选依赖）。

只有在安装了 ``langchain-core`` + ``langchain-openai`` 时才可用；
未安装时 :func:`langchain_available` 返回 False，工厂会自动回退。

设计上刻意把 LangChain 放在**适配器层**而非核心依赖：
核心只依赖 :class:`llm.base.LLMClient` 协议，换框架不影响 Agent 代码。
"""

from __future__ import annotations

from time import perf_counter

from llm.base import (
    LLMError,
    LLMRequest,
    LLMResponse,
    LLMUnavailable,
    LLMUsage,
)


def langchain_available() -> bool:
    try:
        import langchain_core  # noqa: F401
        import langchain_openai  # noqa: F401
    except ImportError:
        return False
    return True


class LangChainLLM:
    """用 LangChain 的 ChatModel 承载调用。

    Prompt 仍由本项目的 :class:`llm.prompts.PromptRegistry` 渲染，
    这里只接管"把 messages 发给模型"这一步，避免提示词资产被框架锁定。
    """

    provider = "langchain"

    def __init__(self, model: str, api_key: str | None, base_url: str | None = None) -> None:
        if not langchain_available():
            raise LLMUnavailable(
                "langchain is not installed; install with: pip install -e '.[langchain]'"
            )
        if not api_key:
            raise LLMUnavailable("LLM API key is required for the langchain provider")

        from langchain_openai import ChatOpenAI

        self.model = model
        kwargs: dict[str, object] = {"model": model, "api_key": api_key, "temperature": 0.0}
        if base_url:
            kwargs["base_url"] = base_url
        self._chat = ChatOpenAI(**kwargs)

    async def complete(self, request: LLMRequest) -> LLMResponse:
        from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

        mapping = {"system": SystemMessage, "user": HumanMessage, "assistant": AIMessage}
        messages = [mapping[message.role.value](content=message.content) for message in request.messages]

        started = perf_counter()
        try:
            result = await self._chat.ainvoke(messages)
        except Exception as exc:  # LangChain 抛出的异常类型不稳定，统一包装
            raise LLMError(f"langchain invocation failed: {exc}") from exc

        text = result.content if isinstance(result.content, str) else str(result.content)
        usage_meta = getattr(result, "usage_metadata", None) or {}
        return LLMResponse(
            text=text,
            provider=self.provider,
            model=self.model,
            usage=LLMUsage(
                prompt_tokens=int(usage_meta.get("input_tokens") or 0),
                completion_tokens=int(usage_meta.get("output_tokens") or 0),
                total_tokens=int(usage_meta.get("total_tokens") or 0),
            ),
            latency_ms=round((perf_counter() - started) * 1000, 3),
        )


__all__ = ["LangChainLLM", "langchain_available"]
