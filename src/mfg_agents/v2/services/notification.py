"""NotificationService：分级通知（P0 电话 / P1 IM / P2 邮件）。

契约先行：服务面向 NotificationChannel 抽象编程；
LogNotificationChannel 为本地实现（日志 + 内存 outbox，供 demo 验证），
IM 通道预留（实现 IMGateway.push 即可接入企业微信等平台）。
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any, Optional

logger = logging.getLogger(__name__)

LEVELS = ("P0", "P1", "P2")


class NotificationChannel(ABC):
    """通知通道抽象（实现 send 即接入新通道）。"""

    @abstractmethod
    def send(self, level: str, targets: list[str], title: str,
             body: str) -> dict[str, Any]:
        """发送通知，返回投递记录。"""


class LogNotificationChannel(NotificationChannel):
    """日志通道：记录到 logger + 内存 outbox（demo/测试可断言）。"""

    def __init__(self) -> None:
        self.outbox: list[dict[str, Any]] = []

    def send(self, level: str, targets: list[str], title: str,
             body: str) -> dict[str, Any]:
        record = {"level": level, "targets": list(targets),
                  "title": title, "body": body}
        self.outbox.append(record)
        logger.info("[notify][%s] %s -> %s | %s", level, title, targets, body[:80])
        return record


class IMNotificationChannel(NotificationChannel):
    """IM 通道（预留）：经 IMGateway.push 主动推送卡片。"""

    def __init__(self, gateway: Any) -> None:
        self.gateway = gateway

    def send(self, level: str, targets: list[str], title: str,
             body: str) -> dict[str, Any]:
        from ..contracts.im_gateway import ReplyCard
        card = ReplyCard(title=title, body=body, level=level)
        for target in targets:
            self.gateway.push(target, card)
        return {"level": level, "targets": list(targets), "title": title,
                "body": body, "via": "im"}


class NotificationService:
    """通知服务：域组/工作流引擎统一经此发送，禁止各模块自行发送。"""

    def __init__(self, channel: Optional[NotificationChannel] = None) -> None:
        self.channel = channel or LogNotificationChannel()

    def send(self, level: str, targets: list[str], title: str,
             body: str) -> dict[str, Any]:
        if level not in LEVELS:
            raise ValueError(f"通知级别必须是 {LEVELS}，收到 {level!r}")
        if not targets:
            raise ValueError("通知目标不能为空")
        return self.channel.send(level, targets, title, body)

    def broadcast_hitl(self, task: dict[str, Any],
                       targets: list[str]) -> dict[str, Any]:
        """HITL 待办播报（卡片形式）。"""
        title = f"[待确认] {task.get('title', '')}"
        body = (f"{task.get('hitl_prompt', '')}\n"
                f"审批人: {','.join(task.get('approver_roles', []))}\n"
                f"操作: {' / '.join(a.get('label', '') for a in task.get('actions', []))}")
        return self.send("P1", targets, title, body)
