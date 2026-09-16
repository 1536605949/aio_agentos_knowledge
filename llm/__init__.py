"""LLM 能力层。

对外暴露的稳定接口只有 :class:`LLMClient` 协议与 :func:`build_llm` 工厂。
上层 Agent 不直接依赖任何具体 provider 或框架。
"""

from llm.base import (
    LLMClient,
    LLMError,
    LLMMessage,
    LLMRequest,
    LLMResponse,
    LLMResponseFormatError,
    LLMRole,
    LLMUnavailable,
    LLMUsage,
    extract_json_object,
)
from llm.deterministic import DeterministicLLM
from llm.factory import ResilientLLM, build_llm
from llm.prompts import (
    DEFAULT_PROMPTS,
    PromptRegistry,
    PromptRenderError,
    PromptTemplate,
    build_default_registry,
)

__all__ = [
    "DEFAULT_PROMPTS",
    "DeterministicLLM",
    "LLMClient",
    "LLMError",
    "LLMMessage",
    "LLMRequest",
    "LLMResponse",
    "LLMResponseFormatError",
    "LLMRole",
    "LLMUnavailable",
    "LLMUsage",
    "PromptRegistry",
    "PromptRenderError",
    "PromptTemplate",
    "ResilientLLM",
    "build_default_registry",
    "build_llm",
    "extract_json_object",
]
