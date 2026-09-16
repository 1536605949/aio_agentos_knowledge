from __future__ import annotations

from protocol.mcp.models import MCPToolDescriptor


class MCPToolCatalog:
    """Outbound MCP catalog; transport/auth belongs to the deployment adapter."""

    def __init__(self) -> None:
        self._tools: dict[str, MCPToolDescriptor] = {}

    def register(self, descriptor: MCPToolDescriptor) -> None:
        self._tools[descriptor.name] = descriptor

    def list_tools(self) -> list[MCPToolDescriptor]:
        return list(self._tools.values())
