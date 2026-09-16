"""工具层：规格契约、默认目录与受治理的执行注册表。"""

from tools.catalog import ACTION_RISK, ACTION_TO_TOOL, build_default_tool_registry
from tools.models import ToolHandler, ToolSpec
from tools.registry import (
    CircuitOpen,
    PermanentToolError,
    ToolError,
    ToolNotFound,
    ToolPolicyDenied,
    ToolRateLimited,
    ToolRegistry,
    TransientToolError,
)

__all__ = [
    "ACTION_RISK",
    "ACTION_TO_TOOL",
    "CircuitOpen",
    "PermanentToolError",
    "ToolError",
    "ToolHandler",
    "ToolNotFound",
    "ToolPolicyDenied",
    "ToolRateLimited",
    "ToolRegistry",
    "ToolSpec",
    "TransientToolError",
    "build_default_tool_registry",
]
