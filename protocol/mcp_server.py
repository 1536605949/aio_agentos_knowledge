"""Compatibility shim: MCP is outbound in this architecture.

`MCPServer` is retained as an alias-like catalog wrapper for old imports. New code should
use `protocol.mcp.MCPToolCatalog`.
"""
from protocol.mcp.client import MCPToolCatalog

MCPServer = MCPToolCatalog
__all__ = ["MCPServer", "MCPToolCatalog"]
