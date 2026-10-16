"""企业微信适配器：实现真实回调协议（验签 / AES 解密 / 被动回复 XML 组装）。

覆盖企业微信自建应用回调的三类交互：
1. GET  URL 校验：echostr + SHA1(token/timestamp/nonce) 签名，解密后回显；
2. POST 消息接收：加密 XML → 验签 → AES-256-CBC 解密 → InboundMessage；
3. 被动回复：ReplyCard → 文本 XML → 加密 → 组装 <Encrypt> 响应体。

安全边界：验签失败 / appid 不匹配 / 补位非法一律拒绝（抛 WeComCryptoError）。
主动推送（push）为预留能力：记录日志，真实实现走企业微信应用消息 API。
"""
from __future__ import annotations

import logging
import time
from typing import Any, Optional
from xml.etree import ElementTree

from ..im_gateway import IMGateway, InboundMessage, ReplyCard
from .wecom_crypto import (
    WeComCryptoError,
    compute_signature,
    decrypt_msg,
    encrypt_msg,
)

logger = logging.getLogger(__name__)


def _cdata(text: str) -> str:
    """XML CDATA 包裹（内容含 ]]> 时按平台惯例原样包裹，边界由测试覆盖）。"""
    return f"<![CDATA[{text}]]>"


class WeComAdapter(IMGateway):
    """企业微信回调适配器（IMGateway 实现）。

    参数：
        token:     回调 Token（验签用）
        aes_key:   EncodingAESKey（43 位 Base64）
        appid:     AgentID / 企业 ID（解密后归属校验）
    """

    def __init__(self, token: str, aes_key: str, appid: str) -> None:
        if not token or not aes_key or not appid:
            raise ValueError("token / aes_key / appid 均不能为空")
        self.token = token
        self.aes_key = aes_key
        self.appid = appid

    # ---------- IMGateway 契约 ----------

    def verify_callback(self, payload: dict[str, Any],
                        headers: Optional[dict[str, str]] = None) -> bool:
        """校验回调签名。payload 需含 msg_signature/timestamp/nonce 及 encrypt 或 echostr。"""
        data = payload.get("encrypt") or payload.get("echostr") or ""
        expected = compute_signature(
            self.token, str(payload.get("timestamp", "")),
            str(payload.get("nonce", "")), data)
        ok = expected == payload.get("msg_signature", "")
        if not ok:
            logger.warning("企微回调验签失败: expected=%s got=%s",
                           expected, payload.get("msg_signature"))
        return ok

    def receive(self, payload: dict[str, Any]) -> InboundMessage:
        """解密回调消息体 → InboundMessage（调用前必须先 verify_callback）。"""
        if not self.verify_callback(payload):
            raise WeComCryptoError("回调验签失败，拒绝解析消息")
        plain = decrypt_msg(self.aes_key, self.appid, payload["encrypt"])
        return self._parse_msg_xml(plain)

    def reply(self, msg: InboundMessage, card: ReplyCard) -> str:
        """被动回复：ReplyCard → 文本消息 XML → 加密 → 平台响应体。"""
        timestamp = str(int(time.time()))
        nonce = _make_nonce()
        to_user = msg.user_id                       # 回复给消息发送方
        from_user = msg.raw.get("ToUserName", self.appid)
        text_xml = (
            "<xml>"
            f"<ToUserName>{_cdata(to_user)}</ToUserName>"
            f"<FromUserName>{_cdata(from_user)}</FromUserName>"
            f"<CreateTime>{timestamp}</CreateTime>"
            f"<MsgType>{_cdata('text')}</MsgType>"
            f"<Content>{_cdata(self.card_to_text(card))}</Content>"
            "</xml>"
        )
        return self.build_reply_xml(text_xml, timestamp, nonce)

    def push(self, user_id: str, card: ReplyCard) -> None:
        """主动推送（预留）：记录通知日志，真实实现调应用消息 API。"""
        logger.info("[IM push] -> %s | %s | %s", user_id, card.title,
                    card.body[:80])
        self.pushed: list[dict[str, Any]] = getattr(self, "pushed", [])
        self.pushed.append({"user_id": user_id, "title": card.title,
                            "body": card.body, "actions": card.actions})

    # ---------- 企业微信协议细节 ----------

    def verify_url(self, params: dict[str, Any]) -> str:
        """GET URL 校验：验签通过后解密 echostr 并回显明文。"""
        if not self.verify_callback(params):
            raise WeComCryptoError("URL 校验验签失败")
        return decrypt_msg(self.aes_key, self.appid, params["echostr"])

    @staticmethod
    def parse_callback_xml(xml_text: str) -> dict[str, str]:
        """从 POST 回调体中提取 Encrypt 字段。"""
        try:
            root = ElementTree.fromstring(xml_text)
        except ElementTree.ParseError as exc:
            raise WeComCryptoError(f"回调 XML 解析失败: {exc}") from exc
        encrypt = root.findtext("Encrypt")
        if not encrypt:
            raise WeComCryptoError("回调体缺少 Encrypt 字段")
        return {"encrypt": encrypt}

    def build_reply_xml(self, plaintext_xml: str, timestamp: str, nonce: str) -> str:
        """被动回复 XML 组装：加密 + 签名 + 标准四字段。"""
        encrypt = encrypt_msg(self.aes_key, self.appid, plaintext_xml)
        signature = compute_signature(self.token, timestamp, nonce, encrypt)
        return (
            "<xml>"
            f"<Encrypt>{_cdata(encrypt)}</Encrypt>"
            f"<MsgSignature>{_cdata(signature)}</MsgSignature>"
            f"<TimeStamp>{timestamp}</TimeStamp>"
            f"<Nonce>{_cdata(nonce)}</Nonce>"
            "</xml>"
        )

    @staticmethod
    def card_to_text(card: ReplyCard) -> str:
        """卡片 → 纯文本（被动回复窗口仅可靠支持 text/news）。"""
        lines = [f"【{card.title}】", card.body]
        if card.actions:
            lines.append("待确认操作：" + " / ".join(
                f"{a.get('label', '')}({a.get('key', '')})" for a in card.actions))
        return "\n".join(lines)

    def _parse_msg_xml(self, plain: str) -> InboundMessage:
        """解析解密后的消息 XML（当前支持 text / event 两类）。"""
        try:
            root = ElementTree.fromstring(plain)
        except ElementTree.ParseError as exc:
            raise WeComCryptoError(f"消息 XML 解析失败: {exc}") from exc
        msg_type = (root.findtext("MsgType") or "").strip()
        content = (root.findtext("Content") or "").strip()
        if msg_type == "event":
            content = f"[event:{root.findtext('Event') or 'unknown'}]"
        return InboundMessage(
            user_id=(root.findtext("FromUserName") or "").strip(),
            conversation_id=(root.findtext("ToUserName") or "").strip(),
            text=content,
            channel="wecom",
            raw={
                "ToUserName": (root.findtext("ToUserName") or "").strip(),
                "FromUserName": (root.findtext("FromUserName") or "").strip(),
                "MsgType": msg_type,
                "MsgId": (root.findtext("MsgId") or "").strip(),
            },
        )


def _make_nonce() -> str:
    """生成回调 nonce（演示用时间戳随机串；真实场景可用 uuid）。"""
    import secrets
    return secrets.token_hex(8)
