"""版本化提示词模板与注册表。

提示词属于需要被治理的资产：它们有版本号、可被审计、可被回滚。
本模块刻意保持轻量——不引入模板引擎依赖，只做 ``{variable}`` 占位符替换。
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, Field

_PLACEHOLDER_RE = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")


class PromptRenderError(ValueError):
    """渲染失败：缺少必需变量或存在未声明占位符。"""


class PromptTemplate(BaseModel):
    name: str = Field(min_length=1)
    version: str = "1.0.0"
    template: str = Field(min_length=1)
    description: str = ""
    variables: list[str] = Field(default_factory=list)

    def model_post_init(self, __context: Any) -> None:
        if not self.variables:
            self.variables = sorted(set(_PLACEHOLDER_RE.findall(self.template)))

    def render(self, **kwargs: Any) -> str:
        missing = [name for name in self.variables if name not in kwargs]
        if missing:
            raise PromptRenderError(f"missing prompt variables for {self.name}: {missing}")
        rendered = self.template
        for name in self.variables:
            rendered = rendered.replace("{" + name + "}", str(kwargs[name]))
        leftover = sorted(set(_PLACEHOLDER_RE.findall(rendered)))
        if leftover:
            raise PromptRenderError(f"unresolved placeholders in {self.name}: {leftover}")
        return rendered


class PromptRegistry:
    """按 ``name`` 索引模板，保留同名多版本以便灰度与回滚。"""

    def __init__(self) -> None:
        self._versions: dict[str, dict[str, PromptTemplate]] = {}
        self._active: dict[str, str] = {}

    def register(self, prompt: PromptTemplate, *, activate: bool = True) -> PromptTemplate:
        bucket = self._versions.setdefault(prompt.name, {})
        bucket[prompt.version] = prompt
        if activate or prompt.name not in self._active:
            self._active[prompt.name] = prompt.version
        return prompt

    def get(self, name: str, version: str | None = None) -> PromptTemplate:
        bucket = self._versions.get(name)
        if not bucket:
            raise KeyError(f"prompt not registered: {name}")
        resolved = version or self._active[name]
        if resolved not in bucket:
            raise KeyError(f"prompt version not found: {name}@{resolved}")
        return bucket[resolved]

    def activate(self, name: str, version: str) -> None:
        if version not in self._versions.get(name, {}):
            raise KeyError(f"prompt version not found: {name}@{version}")
        self._active[name] = version

    def active_version(self, name: str) -> str:
        return self._active[name]

    def names(self) -> list[str]:
        return sorted(self._versions)

    def describe(self) -> list[dict[str, str]]:
        return [
            {
                "name": name,
                "active_version": self._active[name],
                "versions": ",".join(sorted(self._versions[name])),
                "description": self._versions[name][self._active[name]].description,
            }
            for name in self.names()
        ]


REASONING_SYSTEM_PROMPT = PromptTemplate(
    name="reasoning.system",
    version="1.0.0",
    description="根因推理 Agent 的系统提示词，约束输出为严格 JSON。",
    template=(
        "你是 AIOps 故障根因分析专家。\n"
        "你的唯一任务：基于给定的告警与证据，判断最可能的根因。\n\n"
        "严格约束：\n"
        "1. root_cause 必须从允许列表中取值，不得自创：{allowed_causes}\n"
        "2. 若证据不足以判断，root_cause 取 \"undetermined\"。\n"
        "3. confidence 为 0 到 1 之间的小数，证据越弱取值越低。\n"
        "4. 只输出 JSON 对象，不要输出任何解释性文字或 markdown 围栏。\n\n"
        "输出 JSON Schema：\n"
        '{{"root_cause": string, "confidence": number, "rationale": string, "evidence_refs": string[]}}'
    ),
)

REASONING_USER_PROMPT = PromptTemplate(
    name="reasoning.user",
    version="1.0.0",
    description="把告警与证据渲染为推理请求。",
    template=(
        "告警：\n"
        "- 服务：{service}\n"
        "- 严重级别：{severity}\n"
        "- 描述：{message}\n\n"
        "证据（按采集顺序）：\n{evidence}\n\n"
        "拓扑上下文：\n{topology}\n\n"
        "请给出根因判断。"
    ),
)

REMEDIATION_SYSTEM_PROMPT = PromptTemplate(
    name="remediation.system",
    version="1.0.0",
    description="修复建议 Agent 的系统提示词。",
    template=(
        "你是 AIOps 修复动作规划专家。\n"
        "基于根因，从允许的动作列表中选出最合适的一个：{allowed_actions}\n"
        "若没有合适动作，返回 \"collect_more_evidence\"。\n"
        "不得自行发明动作名。只输出 JSON，不要 markdown 围栏。\n\n"
        "输出 JSON Schema：\n"
        '{{"action": string, "rationale": string, "risk_notes": string}}'
    ),
)

REMEDIATION_USER_PROMPT = PromptTemplate(
    name="remediation.user",
    version="1.0.0",
    description="把根因渲染为修复建议请求。",
    template=("根因：{root_cause}\n置信度：{confidence}\n受影响服务：{service}\n证据摘要：{evidence_summary}"),
)

ALARM_SYSTEM_PROMPT = PromptTemplate(
    name="alarm.system",
    version="1.0.0",
    description="告警标准化 Agent 的系统提示词。",
    template=(
        "你是告警标准化处理器。把原始告警归一化为统一结构。\n"
        "severity 必须从 {allowed_severities} 中取值。\n"
        "只输出 JSON，不要 markdown 围栏。\n\n"
        "输出 JSON Schema：\n"
        '{{"severity": string, "category": string, "summary": string}}'
    ),
)

ALARM_USER_PROMPT = PromptTemplate(
    name="alarm.user",
    version="1.0.0",
    description="把原始告警渲染为标准化请求。",
    template="原始告警：\n{alarm_json}",
)

DEFAULT_PROMPTS: tuple[PromptTemplate, ...] = (
    REASONING_SYSTEM_PROMPT,
    REASONING_USER_PROMPT,
    REMEDIATION_SYSTEM_PROMPT,
    REMEDIATION_USER_PROMPT,
    ALARM_SYSTEM_PROMPT,
    ALARM_USER_PROMPT,
)


def build_default_registry() -> PromptRegistry:
    registry = PromptRegistry()
    for prompt in DEFAULT_PROMPTS:
        registry.register(prompt)
    return registry
