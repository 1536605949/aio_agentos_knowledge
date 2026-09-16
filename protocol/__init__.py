"""协议层：对外能力发现（A2A）与出站工具目录（MCP）。

方向约定：**MCP 是出站**（我们把工具暴露给/调用外部的工具服务），
**A2A 是入站**（别的 Agent 通过 AgentCard 发现我们）。
"""

from protocol.a2a import AgentCard
from protocol.catalog import build_agent_card, build_mcp_catalog
from protocol.mcp import MCPToolCatalog, MCPToolDescriptor

__all__ = [
    "AgentCard",
    "MCPToolCatalog",
    "MCPToolDescriptor",
    "build_agent_card",
    "build_mcp_catalog",
]
