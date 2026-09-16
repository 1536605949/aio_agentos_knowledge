"""LLM 抽象层的数据契约。

设计要点：上层（Agent）只依赖 :class:`LLMClient` 协议与 :class:`LLMResponse`，
不关心底层是确定性规则实现、OpenAI 兼容 HTTP 服务，还是 LangChain 封装。
"""

from __future__ import annotations

import json
import re
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, Field


class LLMRole(StrEnum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"


class LLMMessage(BaseModel):
    role: LLMRole
    content: str


class LLMRequest(BaseModel):
    messages: list[LLMMessage] = Field(min_length=1)
    model: str | None = None
    temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    max_tokens: int = Field(default=768, gt=0)
    expect_json: bool = True
    """为 True 时，调用方期望返回体是 JSON 对象。"""
    metadata: dict[str, Any] = Field(default_factory=dict)


class LLMUsage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cost_usd: float = 0.0


class LLMResponse(BaseModel):
    text: str
    provider: str
    model: str
    usage: LLMUsage = Field(default_factory=LLMUsage)
    latency_ms: float = 0.0
    attempts: int = 1
    fallback_used: bool = False

    def json_object(self) -> dict[str, Any]:
        """把返回文本解析为 JSON 对象；失败时抛 :class:`LLMResponseFormatError`。"""
        payload = extract_json_object(self.text)
        if payload is None:
            raise LLMResponseFormatError(f"response is not a JSON object: {self.text[:200]!r}")
        return payload


class LLMError(RuntimeError):
    """LLM 调用失败的基类。"""


class LLMUnavailable(LLMError):
    """未配置凭据或依赖缺失，可安全回退到确定性实现。"""


class LLMResponseFormatError(LLMError):
    """模型返回内容无法解析为期望的结构。"""


_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)


def extract_json_object(text: str) -> dict[str, Any] | None:
    """从模型输出中稳健地抽取 JSON 对象。

    依次尝试：整体解析 → 去 markdown 围栏 → 截取首个平衡的花括号片段。
    """
    candidates: list[str] = [text.strip()]
    fenced = _FENCE_RE.search(text)
    if fenced:
        candidates.append(fenced.group(1).strip())
    brace = _extract_balanced_braces(text)
    if brace:
        candidates.append(brace)

    for candidate in candidates:
        if not candidate:
            continue
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def _extract_balanced_braces(text: str) -> str | None:
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    return None


@runtime_checkable
class LLMClient(Protocol):
    """LLM 客户端协议。实现方需提供 ``provider`` / ``model`` 与 ``complete``。"""

    provider: str
    model: str

    async def complete(self, request: LLMRequest) -> LLMResponse:
        """执行一次补全。失败时抛 :class:`LLMError` 子类。"""
        ...
