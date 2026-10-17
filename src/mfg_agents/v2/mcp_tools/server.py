"""最小 MCP Server（stdio，JSON-RPC 2.0，零第三方依赖）。

为什么自实现而不是引入 ``mcp`` SDK：项目铁律"零新依赖"；MCP 的工具
能力面只需 4 个方法（initialize / tools/list / tools/call / ping），
协议即 JSON-RPC 2.0 over stdio——标准库足够，且工具定义不绑定任何
SDK（``ToolRegistry`` 是单一事实源，未来接官方 SDK 只换传输层）。

协议要点（MCP 2024-11-05 规范的工具子集）：
- ``initialize`` → 返回 protocolVersion / capabilities.tools / serverInfo
- ``notifications/initialized`` → 客户端确认（无需响应）
- ``tools/list`` → ToolRegistry 的工具清单（name/description/inputSchema）
- ``tools/call`` → 执行工具；业务错误以 ``isError: true`` 的 content 返回
  （MCP 语义：工具执行失败是**结果**而非协议错误）
- ``ping`` → {}

按域启动（多 server 分治，单 server ≤ 15 工具）：命令行 ``--domain <name>``
或环境变量 ``MFG_MCP_DOMAIN`` 指定 ``data`` / ``asset`` / ``quality`` /
``workflow`` / ``knowledge``，则 ``tools/list`` 与 ``tools/call`` 只暴露/接受
该域工具；**不指定时行为与单 server 全量暴露完全一致**。

启动方式（任意 MCP 客户端可挂）::

    uv run python -m mfg_agents.v2.mcp_tools.server
    uv run python -m mfg_agents.v2.mcp_tools.server --domain asset
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from typing import Any, Optional

from ..core.orchestrator import V2Orchestrator, create_default_orchestrator
from ..services.workflow import PermissionDenied
from .registry import ToolError, ToolRegistry
from .tools import register_domain_toolsets, register_platform_tools

logger = logging.getLogger(__name__)

PROTOCOL_VERSION = "2024-11-05"
SERVER_INFO = {"name": "mfg-agent-platform", "version": "3.1.0"}


def _server_info(domain: str) -> dict[str, Any]:
    """serverInfo：指定域时名称体现域，缺省与既有行为完全一致。"""
    if domain:
        return {"name": f"mfg-agent-platform-mcp:{domain}",
                "version": SERVER_INFO["version"]}
    return dict(SERVER_INFO)


__all__ = ["McpStdioServer", "main", "PROTOCOL_VERSION", "SERVER_INFO"]


class McpStdioServer:
    """stdio MCP server：工具能力从 ToolRegistry 读取（单一事实源）。

    ``domain`` 非空时只装载该域工具（多 server 分治）；为空时装载全部工具。
    """

    def __init__(self, registry: Optional[ToolRegistry] = None,
                 orchestrator: Optional[V2Orchestrator] = None,
                 stdin: Optional[Any] = None, stdout: Optional[Any] = None,
                 domain: str = "") -> None:
        self.orchestrator = orchestrator or create_default_orchestrator()
        self.domain = (domain or "").strip()
        if registry is None:
            if self.domain:
                toolsets = register_domain_toolsets(self.orchestrator)
                if self.domain not in toolsets:
                    raise KeyError(
                        f"未知域 {self.domain!r}（可选: {sorted(toolsets)}）")
                registry = toolsets[self.domain]
            else:
                registry = register_platform_tools(ToolRegistry(),
                                                   self.orchestrator)
        self.registry = registry
        self.server_info = _server_info(self.domain)
        self._stdin = stdin if stdin is not None else sys.stdin
        self._stdout = stdout if stdout is not None else sys.stdout

    # ---------- JSON-RPC 分发 ----------

    def handle_message(self, msg: dict[str, Any]) -> Optional[dict[str, Any]]:
        """处理一条 JSON-RPC 消息；通知（无 id）返回 None。"""
        method = str(msg.get("method") or "")
        msg_id = msg.get("id")
        params = msg.get("params") or {}
        try:
            result = self._dispatch(method, params)
            if msg_id is None:            # notification：不需要响应
                return None
            return {"jsonrpc": "2.0", "id": msg_id, "result": result}
        except ToolError as exc:          # 工具执行失败 → 结果级错误
            if msg_id is None:
                return None
            return self._tool_error(msg_id, str(exc))
        except KeyError as exc:
            if msg_id is None:
                return None
            return self._protocol_error(msg_id, -32602, str(exc))
        except Exception as exc:          # noqa: BLE001 —— 协议层兜底
            logger.exception("MCP 处理异常 method=%s", method)
            if msg_id is None:
                return None
            return self._protocol_error(msg_id, -32603, f"内部错误: {exc}")

    def _dispatch(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        if method == "initialize":
            return {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": self.server_info,
            }
        if method == "ping":
            return {}
        if method == "tools/list":
            return {"tools": self.registry.list_mcp()}
        if method == "tools/call":
            return self._tools_call(params)
        raise KeyError(f"未知方法: {method!r}")

    def _tools_call(self, params: dict[str, Any]) -> dict[str, Any]:
        name = str(params.get("name") or "")
        # 工具存在性先于执行：未知工具是协议错误（-32602），由调用方处理；
        # 已注册工具的执行失败才是工具结果（isError=true）
        self.registry.get(name)  # KeyError → JSON-RPC -32602
        arguments = dict(params.get("arguments") or {})
        # user_ctx 由客户端放在 arguments.user_ctx（工具 schema 已声明）——
        # 这是治理权限的唯一来源；缺失时工具自身 fail-closed。
        user_ctx = arguments.pop("user_ctx", None) or {}
        try:
            data = self.registry.call(name, arguments, user_ctx=user_ctx)
        except KeyError as exc:
            raise ToolError(str(exc)) from exc
        except (ValueError, PermissionDenied) as exc:
            raise ToolError(str(exc)) from exc
        except Exception as exc:  # noqa: BLE001 —— 统一按工具失败返回
            logger.exception("工具 %s 执行失败", name)
            raise ToolError(f"工具执行失败: {exc}") from exc
        return {
            "content": [{"type": "text",
                         "text": json.dumps(data, ensure_ascii=False)}],
            "structuredContent": data,
        }

    @staticmethod
    def _tool_error(msg_id: Any, message: str) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": msg_id,
                "result": {"content": [{"type": "text", "text": message}],
                           "isError": True}}

    @staticmethod
    def _protocol_error(msg_id: Any, code: int, message: str) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": msg_id,
                "error": {"code": code, "message": message}}

    # ---------- stdio 循环 ----------

    def serve(self) -> None:
        """主循环：逐行读 JSON-RPC → 写响应（LSP 风格按行分帧）。"""
        for line in self._stdin:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError as exc:
                self._write({"jsonrpc": "2.0", "id": None,
                             "error": {"code": -32700,
                                       "message": f"解析错误: {exc}"}})
                continue
            resp = self.handle_message(msg)
            if resp is not None:
                self._write(resp)
            if msg.get("method") == "shutdown":
                break

    def _write(self, payload: dict[str, Any]) -> None:
        self._stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
        self._stdout.flush()


def main() -> None:
    """stdio 入口：日志必须走 stderr（stdout 是协议通道）。

    ``--domain`` 或环境变量 ``MFG_MCP_DOMAIN`` 指定域（data/asset/quality/
    workflow/knowledge）时只暴露该域工具；都不给则全量暴露（既有行为不变）。
    """
    parser = argparse.ArgumentParser(description="mfg-agent-platform MCP server")
    parser.add_argument("--domain", default=None,
                        help="按域启动（data/asset/quality/workflow/knowledge）")
    args = parser.parse_args()
    domain = (args.domain or os.getenv("MFG_MCP_DOMAIN") or "").strip()

    logging.basicConfig(stream=sys.stderr, level=logging.INFO)
    McpStdioServer(domain=domain).serve()


if __name__ == "__main__":
    main()
