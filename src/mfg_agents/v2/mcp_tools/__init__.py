"""统一 Function Call 工具层：MCP / A2A / 未来 function calling 的单一事实源。

用法（任意 MCP 客户端，如 Claude Desktop / 自研 Agent）::

    uv run python -m mfg_agents.v2.mcp_tools.server

设计立场见 ``registry.py`` 模块文档：业务模块是确定性工具，不是自主 Agent。
"""
from .registry import Tool, ToolError, ToolRegistry
from .server import McpStdioServer

__all__ = ["Tool", "ToolError", "ToolRegistry", "McpStdioServer",
           "register_platform_tools"]

from .tools import register_platform_tools  # noqa: E402  (末尾重导出)
