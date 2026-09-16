"""LLM 能力层测试：JSON 抽取、提示词注册表、确定性实现、降级包装。"""

from __future__ import annotations

import pytest

from llm import (
    DeterministicLLM,
    LLMError,
    LLMMessage,
    LLMRequest,
    LLMResponse,
    LLMResponseFormatError,
    LLMRole,
    PromptRegistry,
    PromptRenderError,
    PromptTemplate,
    ResilientLLM,
    build_default_registry,
    build_llm,
    extract_json_object,
)
from ontology import REMEDIATION_ACTIONS, ROOT_CAUSES

# --------------------------------------------------------------- JSON 抽取

@pytest.mark.parametrize(
    "text",
    [
        '{"root_cause": "upstream_timeout"}',
        '```json\n{"root_cause": "upstream_timeout"}\n```',
        '```\n{"root_cause": "upstream_timeout"}\n```',
        'Here is my answer:\n{"root_cause": "upstream_timeout"}\nHope it helps.',
        'noise {"root_cause": "upstream_timeout", "nested": {"a": "}"}} trailing',
    ],
)
def test_extract_json_object_handles_real_model_output(text):
    payload = extract_json_object(text)
    assert payload is not None
    assert payload["root_cause"] == "upstream_timeout"


def test_extract_json_object_returns_none_for_non_json():
    assert extract_json_object("no json at all") is None
    assert extract_json_object("[1, 2, 3]") is None


def test_response_json_object_raises_on_bad_payload():
    response = LLMResponse(text="not json", provider="p", model="m")
    with pytest.raises(LLMResponseFormatError):
        response.json_object()


# ------------------------------------------------------------- 提示词注册表

def test_prompt_registry_versions_and_render():
    registry = PromptRegistry()
    registry.register(PromptTemplate(name="demo", version="1.0.0", template="hi {name}"))
    registry.register(PromptTemplate(name="demo", version="2.0.0", template="hello {name}!"), activate=False)

    assert registry.active_version("demo") == "1.0.0"
    assert registry.get("demo").render(name="bob") == "hi bob"

    registry.activate("demo", "2.0.0")
    assert registry.get("demo").render(name="bob") == "hello bob!"


def test_prompt_render_rejects_missing_variables():
    registry = build_default_registry()
    template = registry.get("reasoning.system")
    with pytest.raises(PromptRenderError):
        template.render()  # 缺少 allowed_causes


def test_default_registry_contains_all_agent_prompts():
    registry = build_default_registry()
    assert registry.names() == [
        "alarm.system",
        "alarm.user",
        "reasoning.system",
        "reasoning.user",
        "remediation.system",
        "remediation.user",
    ]


# ------------------------------------------------------------ 确定性 LLM

async def test_deterministic_llm_reports_usage_and_latency():
    llm = DeterministicLLM()
    response = await llm.complete(
        LLMRequest(
            messages=[LLMMessage(role=LLMRole.USER, content="upstream timeout")],
            metadata={"task": "reasoning", "allowed_causes": list(ROOT_CAUSES)},
        )
    )
    payload = response.json_object()
    assert payload["root_cause"] == "upstream_timeout"
    assert 0.0 <= payload["confidence"] <= 1.0
    assert response.usage.total_tokens > 0
    assert response.provider == "deterministic"


async def test_deterministic_llm_respects_allowed_vocabulary():
    """模型想给一个词表外的根因时，必须收敛到 undetermined。"""
    llm = DeterministicLLM()
    response = await llm.complete(
        LLMRequest(
            messages=[LLMMessage(role=LLMRole.USER, content="database connection refused")],
            metadata={"task": "reasoning", "allowed_causes": ["upstream_timeout"]},
        )
    )
    assert response.json_object()["root_cause"] == "undetermined"


async def test_deterministic_llm_remediation_stays_in_vocabulary():
    llm = DeterministicLLM()
    response = await llm.complete(
        LLMRequest(
            messages=[LLMMessage(role=LLMRole.USER, content="root cause: config_regression")],
            metadata={"task": "remediation", "allowed_actions": list(REMEDIATION_ACTIONS)},
        )
    )
    assert response.json_object()["action"] == "rollback_config"


# ------------------------------------------------------------- 降级包装

class _AlwaysFailing:
    provider = "broken"
    model = "broken-1"

    async def complete(self, request: LLMRequest) -> LLMResponse:
        raise LLMError("provider exploded")


async def test_resilient_llm_falls_back_and_records_stats():
    primary = _AlwaysFailing()
    llm = ResilientLLM(primary=primary, fallback=DeterministicLLM())
    response = await llm.complete(
        LLMRequest(
            messages=[LLMMessage(role=LLMRole.USER, content="OOM memory limit")],
            metadata={"task": "reasoning", "allowed_causes": list(ROOT_CAUSES)},
        )
    )
    assert response.fallback_used is True
    assert response.provider == "deterministic"

    stats = llm.stats()
    assert stats["calls"] == 1
    assert stats["failures"] == 1
    assert stats["fallback_used"] == 1
    assert stats["fallback_rate"] == 1.0


def test_build_llm_defaults_to_deterministic_without_credentials(monkeypatch):
    monkeypatch.delenv("AIO_AGENTOS_LLM_API_KEY", raising=False)
    monkeypatch.setenv("AIO_AGENTOS_LLM_PROVIDER", "openai")
    from config import get_settings

    llm = build_llm(get_settings(refresh=True))
    # 未配置凭据 -> 构建失败 -> 自动降级为确定性实现
    assert llm.primary_provider == "deterministic"


def test_build_llm_uses_openai_compatible_when_key_present(monkeypatch):
    monkeypatch.setenv("AIO_AGENTOS_LLM_PROVIDER", "openai")
    monkeypatch.setenv("AIO_AGENTOS_LLM_API_KEY", "sk-test")
    monkeypatch.setenv("AIO_AGENTOS_LLM_MODEL", "deepseek-chat")
    from config import get_settings

    llm = build_llm(get_settings(refresh=True))
    assert llm.primary_provider == "openai-compatible"
    assert llm.model == "deepseek-chat"
