"""NotificationService：分级通知（P0 电话 / P1 IM / P2 邮件）。

契约先行：服务面向 NotificationChannel 抽象编程；
LogNotificationChannel 为本地实现（日志 + 内存 outbox，供 demo 验证），
IM 通道预留（实现 IMGateway.push 即可接入企业微信等平台），
BoardNotificationChannel 把命中工段白名单的 target 落到**现场看板推送表**。
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any, Mapping, Optional

logger = logging.getLogger(__name__)

LEVELS = ("P0", "P1", "P2")


class NotificationChannel(ABC):
    """通知通道抽象（实现 send 即接入新通道）。

    ``context`` 是**新增可选投递上下文**（缺省 ``None`` = 老行为）：承载通道落库
    需要的业务引用，如 ``{"source": "complaint_8d", "ref_id": "CS-…",
    "complaint_no": …, "batch_no": …}``。加为可选关键字而非位置参数，
    既有 4 参调用与既有实现零改动。
    """

    @abstractmethod
    def send(self, level: str, targets: list[str], title: str,
             body: str,
             *, context: Optional[Mapping[str, Any]] = None) -> dict[str, Any]:
        """发送通知，返回投递记录。"""


class LogNotificationChannel(NotificationChannel):
    """日志通道：记录到 logger + 内存 outbox（demo/测试可断言）。"""

    def __init__(self) -> None:
        self.outbox: list[dict[str, Any]] = []

    def send(self, level: str, targets: list[str], title: str,
             body: str,
             *, context: Optional[Mapping[str, Any]] = None) -> dict[str, Any]:
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
             body: str,
             *, context: Optional[Mapping[str, Any]] = None) -> dict[str, Any]:
        from ..contracts.im_gateway import ReplyCard
        card = ReplyCard(title=title, body=body, level=level)
        targets_sent = []
        for target in targets:
            self.gateway.push(target, card)
            targets_sent.append(str(target))
        return {"level": level, "targets": list(targets), "title": title,
                "body": body, "via": "im", "pushed": [
                    {"target": t} for t in targets_sent]}


class BoardNotificationChannel(NotificationChannel):
    """现场看板通道：把**命中工段白名单**的 target 落一条看板推送行。

    为什么按 target 过滤：本通道与日志/IM 通道并联挂在同一个 ``NotificationService``
    上（见 ``orchestrator.build_default``），而通知 target 绝大多数是岗位角色
    （「质量总监」「QA」「业务」）——它们不是看板对象。只有 target 恰为工段
    （SMT/PCM/PACK/整机）时才落库，其余**显式跳过并如实记账**（返回 ``skipped``），
    既不静默丢弃也不硬塞一条看不懂的推送。

    ``db_path`` 可注入（测试隔离用）；缺省 = 治理库（``settings.WORKFLOW_DB``）。
    """

    def __init__(self, store: Any = None,
                 db_path: Optional[str] = None) -> None:
        # store 缺省 = 本模块的 site_board（模块级函数集），注入是为了测试
        self.store = store
        self.db_path = db_path

    def _store(self) -> Any:
        if self.store is not None:
            return self.store
        from . import site_board
        return site_board

    def send(self, level: str, targets: list[str], title: str,
             body: str,
             *, context: Optional[Mapping[str, Any]] = None) -> dict[str, Any]:
        store = self._store()
        ctx = dict(context or {})
        pushed: list[dict[str, Any]] = []
        skipped: list[str] = []
        for target in targets:
            name = str(target or "").strip()
            if name not in store.STAGES:
                skipped.append(name)
                continue
            push_id = store.push_message(
                stage=name, title=title, body=body, level=level,
                source=str(ctx.get("source") or "notification"),
                ref_id=str(ctx.get("ref_id") or ""),
                complaint_no=str(ctx.get("complaint_no") or ""),
                batch_no=str(ctx.get("batch_no") or ""),
                customer=str(ctx.get("customer") or ""),
                db_path=self.db_path,
            )
            pushed.append({"push_id": push_id, "stage": name})
        if skipped:
            logger.info("[notify][board] 非工段 target 已跳过: %s", skipped)
        return {"level": level, "targets": list(targets), "title": title,
                "body": body, "via": "site_board",
                "pushed": pushed, "skipped": skipped}


class IMGroupNotificationChannel(NotificationChannel):
    """现场群通道：把**工段级**通知投到该工段的企微群（并按需 @ 责任人）。

    与 ``BoardNotificationChannel`` 的分工（两者并联，同一条通知走两路）：

    - 看板通道 = **站内留痕**（落 ``site_board_push``，现场屏可见、可 ack/闭环）；
    - 本通道 = **IM 触达**（群里的人知道有这件事）。工段 → 群的对应关系来自
      ``v2/data/im_groups.yaml``（结构在 YAML、凭据在 env，见 ``services/im_groups.py``）。

    投递按"配置能力"逐条降级，**每一步都记账**（``pushed`` / ``skipped`` / ``failed``）：

    - 配置了群机器人 key → 经 ``sender`` 发群消息（默认 urllib POST，测试可注入替身）；
    - 只配了责任人 members → 经 ``IMGateway.push`` 推应用消息给这些人；
    - 有配置但缺投递实现（如未配凭据）→ ``skipped`` + 理由；
    - 两者皆无（演示态默认）→ ``skipped`` + 「未配置凭据，仅落站内看板」；
    - target 不是工段（岗位角色）→ ``skipped``（本通道只认工段）。

    ``registry`` / ``gateway`` / ``sender`` 均可注入（测试用）；缺省取单例与真实实现。
    """

    def __init__(self, registry: Any = None, gateway: Any = None,
                 sender: Any = None) -> None:
        self.registry = registry
        self.gateway = gateway
        self.sender = sender if sender is not None else _default_webhook_sender

    def _registry(self) -> Any:
        if self.registry is not None:
            return self.registry
        from .im_groups import get_im_groups
        return get_im_groups()

    def send(self, level: str, targets: list[str], title: str,
             body: str,
             *, context: Optional[Mapping[str, Any]] = None) -> dict[str, Any]:
        from ..contracts.im_gateway import ReplyCard

        registry = self._registry()
        card = ReplyCard(title=title, body=body, level=level)
        pushed: list[dict[str, Any]] = []
        skipped: list[str] = []
        failed: list[str] = []

        for target in targets:
            stage = str(target or "").strip()
            group = registry.resolve(stage)
            if group is None:
                skipped.append(f"{stage or '（空 target）'}：未在 im_groups.yaml 登记现场群")
                continue
            if not group.configured:
                skipped.append(group.gap_reason())
                continue

            if group.key:
                try:
                    self.sender(group.key, group.name, card)
                    pushed.append({"stage": stage, "chat": group.name,
                                   "via": "webhook"})
                except Exception as exc:  # noqa: BLE001 —— 投递失败逐条记账，不吞
                    failed.append(f"{group.name}：群消息投递失败（{type(exc).__name__}）")
                    logger.warning("[notify][im_group] %s 群消息投递失败: %s",
                                   group.name, exc)
            if group.members:
                if self.gateway is None:
                    # 应用消息（@ 责任人）需要真实 IM 网关：仓库里 WeComAdapter.push
                    # 仍是**预留**（只留痕、不真发），故此处如实记 failed 并说清原因，
                    # 不假装"已 @ 到人"。群消息走群机器人 webhook（已实现），不受影响。
                    failed.append(f"{group.name}：缺 IM 网关（应用消息发送实现为预留），"
                                  f"{len(group.members)} 名责任人未触达")
                else:
                    for member in group.members:
                        try:
                            self.gateway.push(member, card)
                            pushed.append({"stage": stage, "chat": group.name,
                                           "via": "app", "user": member})
                        except Exception as exc:  # noqa: BLE001 —— 同上
                            failed.append(f"{group.name}/{member}："
                                          f"应用消息投递失败（{type(exc).__name__}）")

        if pushed:
            logger.info("[notify][im_group] %s 已投递 %d 条", title, len(pushed))
        if skipped:
            logger.info("[notify][im_group] 跳过: %s", skipped)
        return {"level": level, "targets": list(targets), "title": title,
                "body": body, "via": "im_group",
                "pushed": pushed, "skipped": skipped, "failed": failed}


def _default_webhook_sender(key: str, chat_name: str, card: Any) -> None:
    """群机器人默认发送器：POST 企微 webhook（标准库实现，不引第三方依赖）。

    真实部署由 ``IM_GROUP_KEY_<SLUG>`` 提供 key 后自动生效；演示态未配 key 时
    本函数不会被调用（通道在 ``configured`` 判定处就记账跳过了）。
    """
    import json
    import urllib.request

    url = f"https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key={key}"
    content = f"【{card.title}】\n{card.body}"
    payload = json.dumps({"msgtype": "text",
                          "text": {"content": content}},
                         ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url, data=payload, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=8.0) as resp:  # noqa: S310 - 固定企微域名
        if getattr(resp, "status", 200) >= 400:
            raise RuntimeError(f"webhook HTTP {resp.status}（群 {chat_name}）")


class MultiChannel(NotificationChannel):
    """组合通道：按序扇出到多个通道，逐通道结果并列返回（互不影响）。

    为什么需要它：生产投递是「落库留痕 + IM 触达」等多路的组合，而
    ``NotificationService`` 只认单一 channel——组合放在通道层，服务层与
    各调用点（工作流节点 / HITL）保持零改动。
    """

    def __init__(self, channels: list[NotificationChannel]) -> None:
        self.channels = list(channels)

    def send(self, level: str, targets: list[str], title: str,
             body: str,
             *, context: Optional[Mapping[str, Any]] = None) -> dict[str, Any]:
        results = [channel.send(level, targets, title, body, context=context)
                   for channel in self.channels]
        return {"level": level, "targets": list(targets), "title": title,
                "body": body, "via": "multi", "results": results}


class NotificationService:
    """通知服务：域组/工作流引擎统一经此发送，禁止各模块自行发送。"""

    def __init__(self, channel: Optional[NotificationChannel] = None) -> None:
        self.channel = channel or LogNotificationChannel()

    def send(self, level: str, targets: list[str], title: str,
             body: str,
             *, context: Optional[Mapping[str, Any]] = None) -> dict[str, Any]:
        if level not in LEVELS:
            raise ValueError(f"通知级别必须是 {LEVELS}，收到 {level!r}")
        if not targets:
            raise ValueError("通知目标不能为空")
        return self.channel.send(level, targets, title, body, context=context)

    def broadcast_hitl(self, task: dict[str, Any],
                       targets: list[str]) -> dict[str, Any]:
        """HITL 待办播报（卡片形式）。"""
        title = f"[待确认] {task.get('title', '')}"
        body = (f"{task.get('hitl_prompt', '')}\n"
                f"审批人: {','.join(task.get('approver_roles', []))}\n"
                f"操作: {' / '.join(a.get('label', '') for a in task.get('actions', []))}")
        return self.send("P1", targets, title, body)
