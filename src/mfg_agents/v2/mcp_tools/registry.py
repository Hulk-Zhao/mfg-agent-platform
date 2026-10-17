"""统一 Function Call 工具层（V3.1）。

设计立场（主理人裁定，与架构铁律同源）：
- **业务模块不做成自主 Agentic Agent**——质量/供应链/制造等域的能力
  一律收敛为**确定性工具**（function call），由统一的 ``ToolRegistry``
  暴露；LLM/外部智能体只做编排与推理，不持有自主循环。
- **单一事实源**：MCP server、A2A 端点、未来的 IM function calling
  都从同一个 ``ToolRegistry`` 取工具定义——一处注册，处处可用。
- **工具不是后门**：每个工具调用必须携带 ``user_ctx``，治理权限
  （Text2SQL 表级 ACL / 字段脱敏 / 会签 / 终批）在工具层同样强制生效。

MCP 协议只暴露 4 个方法（initialize / tools/list / tools/call / ping），
基于 JSON-RPC 2.0 over stdio（零第三方依赖，见 ``server.py``）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional

__all__ = ["Tool", "ToolRegistry", "ToolError"]


class ToolError(Exception):
    """工具执行错误（JSON-RPC 层映射为 tool 执行失败的 content 输出）。"""


@dataclass(frozen=True)
class Tool:
    """一个可被 MCP / A2A / function calling 调用的确定性能力。

    Attributes:
        name: 工具名（``域.动作`` 命名，如 ``data.run_query``）。
        description: 供 LLM/外部智能体理解能力的中文描述。
        input_schema: JSON Schema（MCP ``inputSchema`` 原样透出）。
        handler: ``({args: dict, user_ctx: dict}) -> dict`` 纯确定性执行体。
        domain: 归属域（data / scenario / workflow / platform）。
        readonly: 只读工具标记（只读工具可被更宽松的客户端策略放行）。
    """

    name: str
    description: str
    input_schema: dict[str, Any]
    handler: Callable[..., dict[str, Any]]
    domain: str = "platform"
    readonly: bool = True

    def to_mcp_dict(self) -> dict[str, Any]:
        """MCP ``tools/list`` 条目（协议要求的 name/description/inputSchema）。"""
        return {"name": self.name, "description": self.description,
                "inputSchema": self.input_schema}


@dataclass
class ToolRegistry:
    """统一工具注册表：MCP / A2A / function calling 的单一事实源。"""

    _tools: dict[str, Tool] = field(default_factory=dict)

    def register(self, tool: Tool) -> None:
        """注册工具；重名即报错（配置错误必须显式暴露，不能静默覆盖）。"""
        if tool.name in self._tools:
            raise ValueError(f"工具名称冲突: {tool.name!r}")
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool:
        tool = self._tools.get(name)
        if tool is None:
            raise KeyError(f"工具不存在: {name!r}（已注册: {sorted(self._tools)}）")
        return tool

    def list(self) -> list[Tool]:
        return sorted(self._tools.values(), key=lambda t: t.name)

    def list_mcp(self) -> list[dict[str, Any]]:
        """MCP ``tools/list`` 响应体。"""
        return [t.to_mcp_dict() for t in self.list()]

    def call(self, name: str, args: Optional[dict[str, Any]] = None,
             user_ctx: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        """统一调用入口：校验必填 user_ctx → 执行确定性 handler。

        ``user_ctx`` 是治理链的权限来源（角色/部门/user_id）——所有写类
        工具（scenario.run / workflow.act）与数据工具都依赖它做 ACL，
        缺失时由工具自身 fail-closed（与 HTTP 端点同一套校验）。
        """
        tool = self.get(name)
        result = tool.handler(args=dict(args or {}), user_ctx=dict(user_ctx or {}))
        if not isinstance(result, dict):
            raise ToolError(f"工具 {name!r} 返回类型非法: {type(result).__name__}")
        return result
