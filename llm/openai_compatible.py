"""OpenAI 兼容协议的 HTTP 适配器。

适用于任何暴露 ``/chat/completions`` 的服务：OpenAI、DeepSeek、Moonshot、
vLLM、Ollama(兼容层)、企业内部网关等。
"""

from __future__ import annotations

import asyncio
from time import perf_counter
from typing import Any

import httpx

from llm.base import (
    LLMError,
    LLMRequest,
    LLMResponse,
    LLMUnavailable,
    LLMUsage,
)


class OpenAICompatibleLLM:
    """通过 HTTP 调用 OpenAI 兼容的 Chat Completions 接口。"""

    provider = "openai-compatible"

    def __init__(
        self,
        model: str,
        api_key: str | None,
        base_url: str = "https://api.deepseek.com/v1",
        timeout_seconds: float = 30.0,
        max_retries: int = 2,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not api_key:
            raise LLMUnavailable("LLM API key is required for the openai provider")
        self.model = model
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout_seconds
        self._max_retries = max(0, max_retries)
        self._client = client

    def _endpoint(self) -> str:
        return f"{self._base_url}/chat/completions"

    def _payload(self, request: LLMRequest) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": request.model or self.model,
            "messages": [{"role": message.role.value, "content": message.content} for message in request.messages],
            "temperature": request.temperature,
            "max_tokens": request.max_tokens,
        }
        if request.expect_json:
            payload["response_format"] = {"type": "json_object"}
        return payload

    async def complete(self, request: LLMRequest) -> LLMResponse:
        headers = {"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"}
        payload = self._payload(request)
        last_error: Exception | None = None

        for attempt in range(self._max_retries + 1):
            started = perf_counter()
            try:
                if self._client is not None:
                    response = await self._client.post(
                        self._endpoint(), json=payload, headers=headers, timeout=self._timeout
                    )
                else:
                    async with httpx.AsyncClient(timeout=self._timeout) as client:
                        response = await client.post(self._endpoint(), json=payload, headers=headers)
                response.raise_for_status()
                body = response.json()
                return self._to_response(body, started, attempt + 1)
            except httpx.HTTPStatusError as exc:
                last_error = exc
                status = exc.response.status_code
                if status < 500 and status != 429:
                    raise LLMError(f"LLM request rejected with HTTP {status}: {exc.response.text[:200]}") from exc
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_error = exc
            except (KeyError, ValueError, TypeError) as exc:
                raise LLMError(f"unexpected LLM response shape: {exc}") from exc

            if attempt < self._max_retries:
                await asyncio.sleep(min(0.2 * (2**attempt), 2.0))

        raise LLMError(f"LLM request failed after {self._max_retries + 1} attempts: {last_error}") from last_error

    def _to_response(self, body: dict[str, Any], started: float, attempts: int) -> LLMResponse:
        choices = body.get("choices") or []
        if not choices:
            raise LLMError("LLM response contained no choices")
        text = (choices[0].get("message") or {}).get("content") or ""
        usage = body.get("usage") or {}
        return LLMResponse(
            text=text,
            provider=self.provider,
            model=str(body.get("model") or self.model),
            usage=LLMUsage(
                prompt_tokens=int(usage.get("prompt_tokens") or 0),
                completion_tokens=int(usage.get("completion_tokens") or 0),
                total_tokens=int(usage.get("total_tokens") or 0),
            ),
            latency_ms=round((perf_counter() - started) * 1000, 3),
            attempts=attempts,
        )
