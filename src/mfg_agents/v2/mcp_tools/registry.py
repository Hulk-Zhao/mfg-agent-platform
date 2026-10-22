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

__all__ = ["Tool", "ToolRegistry", "ToolError", "ConsentRequired", "consent_is_approved"]


class ToolError(Exception):
    """工具执行错误（JSON-RPC 层映射为 tool 执行失败的 content 输出）。"""


class ConsentRequired(ToolError):
    """``tier=1`` 且声明 ``requires_consent`` 的工具缺预执行同意（R13 / D14）。

    「预执行同意」的含义：先产 HITL 工单，会签通过后带
    ``consent={"ticket_id": "...", "approved": True}`` 再调用；
    未同意前不得落地（fail-closed）。

    Attributes:
        tool_name: 触发的工具名。
        required_shape: 期望的 consent 形状（用于自解释提示）。
    """

    def __init__(self, message: str = "", tool_name: str = "",
                 required_shape: Optional[dict[str, Any]] = None) -> None:
        self.tool_name = tool_name
        self.required_shape = dict(required_shape or {
            "ticket_id": "<HITL 工单号>", "approved": True})
        super().__init__(message or (
            f"工具 {tool_name!r} 需要预执行同意：先经 workflow.create 产 HITL 工单，"
            f"会签通过后带 consent={self.required_shape} 再调用（未同意不得落地）"))


def consent_is_approved(consent: Optional[dict[str, Any]]) -> bool:
    """预执行同意凭据形状校验：工单号非空 + approved 为真。"""
    if not isinstance(consent, dict):
        return False
    ticket = str(consent.get("ticket_id") or "").strip()
    approved = consent.get("approved")
    if isinstance(approved, str):
        approved = approved.strip().lower() in {"1", "true", "yes", "y"}
    return bool(ticket) and bool(approved)


@dataclass(frozen=True)
class Tool:
    """一个可被 MCP / A2A / function calling 调用的确定性能力。

    Attributes:
        name: 工具名（``域.动作`` 命名，如 ``data.run_query``）。
        description: 供 LLM/外部智能体理解能力的中文描述。
        input_schema: JSON Schema（MCP ``inputSchema`` 原样透出）。
        handler: ``({args: dict, user_ctx: dict}) -> dict`` 纯确定性执行体。
        domain: 归属域（data / asset / quality / workflow / knowledge / platform）。
        readonly: 只读工具标记（只读工具可被更宽松的客户端策略放行）。
        tier: 治理分级（0/1/2）——随工具元数据显式分级，与 HITL 槽位对齐：
            ``0`` 只读自动放行（无副作用，可被宽松客户端直接调用）；
            ``1`` 预执行同意（写类只发起：先产出待人工确认工单，会签通过后方可落地）；
            ``2`` 事后审计（可执行但有副作用，事后留痕复核）。
            默认 ``0`` 保证既有只读工具构造不受影响。
        requires_consent: 是否强制预执行同意（仅 ``tier=1`` 有意义）。
            既有 ``tier=1`` 工具（workflow.create / quality.8d）是「只发起」工具，
            它们本身就是产生工单的一步，故默认 ``False``；**真正落地的写工具**
            （如连接器回写）必须显式声明 ``requires_consent=True``，调用时由
            ``ToolRegistry.call`` 校验 ``consent``，未同意即 fail-closed。
    """

    name: str
    description: str
    input_schema: dict[str, Any]
    handler: Callable[..., dict[str, Any]]
    domain: str = "platform"
    readonly: bool = True
    tier: int = 0
    requires_consent: bool = False

    def to_mcp_dict(self) -> dict[str, Any]:
        """MCP ``tools/list`` 条目（协议要求的 name/description/inputSchema）。

        ``tier`` 以非标准扩展字段一并透出（MCP 客户端可忽略；用于治理分级
        展示与"预执行同意"门控：``tier=1`` 的工具在落地前必须经会签确认）。
        """
        data: dict[str, Any] = {"name": self.name, "description": self.description,
                                "inputSchema": self.input_schema, "tier": self.tier}
        if self.requires_consent:
            data["requiresConsent"] = True
        return data


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

    def domains(self) -> list[str]:
        """返回去重且排序的工具归属域列表（用于按域拆分 server）。"""
        return sorted({t.domain for t in self._tools.values()})

    def subset(self, domain: str) -> "ToolRegistry":
        """返回**仅含指定域工具**的新 registry（原 registry 不被修改）。"""
        return self.toolset([t for t in self.list() if t.domain == domain])

    @classmethod
    def toolset(cls, tools: list[Tool]) -> "ToolRegistry":
        """用给定工具列表构造新 registry（多 server 分治的装配入口）。"""
        registry = cls()
        for tool in tools:
            registry.register(tool)
        return registry

    def list_mcp(self) -> list[dict[str, Any]]:
        """MCP ``tools/list`` 响应体。"""
        return [t.to_mcp_dict() for t in self.list()]

    def call(self, name: str, args: Optional[dict[str, Any]] = None,
             user_ctx: Optional[dict[str, Any]] = None,
             consent: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        """统一调用入口：校验预执行同意 → 校验 user_ctx → 执行确定性 handler。

        ``consent`` 只对声明 ``requires_consent=True`` 的 ``tier=1`` 工具生效：
        未带已审批工单（``ticket_id`` + ``approved``）时直接抛
        ``ConsentRequired``，**不执行 handler**（未同意不得落地）。

        ``user_ctx`` 是治理链的权限来源（角色/部门/user_id）——所有写类
        工具（scenario.run / workflow.act）与数据工具都依赖它做 ACL，
        缺失时由工具自身 fail-closed（与 HTTP 端点同一套校验）。
        """
        tool = self.get(name)
        if tool.requires_consent and not consent_is_approved(consent):
            raise ConsentRequired(tool_name=name)
        result = tool.handler(args=dict(args or {}), user_ctx=dict(user_ctx or {}))
        if not isinstance(result, dict):
            raise ToolError(f"工具 {name!r} 返回类型非法: {type(result).__name__}")
        return result
