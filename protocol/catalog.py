"""协议目录：把运行时能力暴露为 A2A AgentCard 与 MCP 工具描述符。

原实现里 ``protocol/`` 的两个模型（``AgentCard`` / ``MCPToolDescriptor``）是**孤立的**：
定义完整但没有任何生产者，``protocol.mcp_server`` 只是一个重命名别名。

本模块把它们接到真实的运行时上：

- :func:`build_agent_card` —— 从 Agent 注册表与技能目录生成对外能力声明
- :func:`build_mcp_catalog` —— 从工具注册表生成出站 MCP 工具目录

这样"我们能做什么"就有了一处可被外部系统发现（discover）的机器可读来源。
"""

from __future__ import annotations

from agents.registry import AgentRegistry
from config import Settings, get_settings
from protocol.a2a.models import AgentCard
from protocol.mcp.client import MCPToolCatalog
from protocol.mcp.models import MCPToolDescriptor
from skills.registry import SkillRegistry
from tools.registry import ToolRegistry


def build_agent_card(
    agents: AgentRegistry,
    skills: SkillRegistry,
    settings: Settings | None = None,
) -> AgentCard:
    """生成 A2A AgentCard。"""
    settings = settings or get_settings()
    capabilities = sorted(
        {capability for spec in agents.specs() for capability in spec.capabilities}
    )
    return AgentCard(
        name="aio-agentos",
        version="3.5.0",
        description="AIOps 故障根因分析与受治理修复的多 Agent 运行时",
        endpoint=settings.a2a_public_endpoint,
        capabilities=capabilities,
        skills=sorted(skills.skills),
        auth_schemes=["api-key"] if settings.api_key else ["none"],
    )


def build_mcp_catalog(tools: ToolRegistry) -> MCPToolCatalog:
    """生成出站 MCP 工具目录。"""
    catalog = MCPToolCatalog()
    for spec in tools.specs():
        catalog.register(
            MCPToolDescriptor(
                name=spec.name,
                description=spec.description,
                version=spec.version,
                input_schema=spec.input_schema,
                output_schema=spec.output_schema,
            )
        )
    return catalog


__all__ = ["build_agent_card", "build_mcp_catalog"]
