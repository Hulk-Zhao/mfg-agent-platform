"""IM 网关统一契约（docs/ARCHITECTURE-V2.md §6.4）。

所有 IM 平台（企业微信 / 钉钉 / 飞书）适配器实现同一 ``IMGateway`` 接口：
入口层只依赖契约，不依赖具体平台；HITL 审批通过卡片按钮回写工作流引擎。

接入新 IM 只需三步：
1. 实现 ``IMGateway`` 四个方法（verify_callback / receive / reply / push）；
2. 在 contracts/adapters/ 下新增适配器模块；
3. 在 demo/api.py 挂载对应回调路由。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class InboundMessage:
    """标准化入站消息：各 IM 平台的原始回调统一转换为此结构。"""

    user_id: str                       # 平台侧用户标识（企微 UserID）
    conversation_id: str               # 会话标识（群ID / 单聊派生ID）
    text: str                          # 纯文本内容（已去平台格式）
    channel: str = "wecom"             # 渠道标识：wecom / dingtalk / feishu
    raw: dict[str, Any] = field(default_factory=dict)  # 原始载荷（审计用）


@dataclass
class ReplyCard:
    """标准化回复卡片：内部简报 / HITL 待确认均用此结构渲染。"""

    title: str
    body: str
    actions: list[dict[str, str]] = field(default_factory=list)
    # actions 形如 [{"key": "confirm_restart", "label": "确认复线方案"}]
    level: str = "P1"                  # 通知级别：P0 / P1 / P2
    extra: dict[str, Any] = field(default_factory=dict)


class IMGateway(ABC):
    """IM 网关抽象基类。

    平台适配器必须实现：
    - verify_callback：回调验签（防伪造，安全边界）；
    - receive：平台载荷 → InboundMessage（解密 / 解析）；
    - reply：被动回复（回调窗口内，企微要求 5s 内响应）；
    - push：主动推送（用于预警播报 / HITL 催办）。
    """

    @abstractmethod
    def verify_callback(self, payload: dict[str, Any],
                        headers: Optional[dict[str, str]] = None) -> bool:
        """校验回调签名。payload 至少含 msg_signature/timestamp/nonce/encrypt(或 echostr)。"""

    @abstractmethod
    def receive(self, payload: dict[str, Any]) -> InboundMessage:
        """解密并解析平台回调载荷为标准化入站消息。"""

    @abstractmethod
    def reply(self, msg: InboundMessage, card: ReplyCard) -> Any:
        """在回调窗口内被动回复卡片；返回平台原始响应体（如企微加密 XML）。"""

    @abstractmethod
    def push(self, user_id: str, card: ReplyCard) -> None:
        """主动推送卡片给指定用户（预留：真实实现走平台应用消息 API）。"""
