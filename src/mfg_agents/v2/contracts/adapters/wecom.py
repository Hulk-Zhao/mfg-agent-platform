"""企业微信适配器：实现真实回调协议（验签 / AES 解密 / 被动回复 XML 组装）。

覆盖企业微信自建应用回调的四类交互：
1. GET  URL 校验：echostr + SHA1(token/timestamp/nonce) 签名，解密后回显；
2. POST 消息接收：加密 XML → 验签 → AES-256-CBC 解密 → InboundMessage；
3. 被动回复：ReplyCard → 文本 XML → 加密 → 组装 <Encrypt> 响应体；
4. **主动推送（应用消息）**：``WeComPushClient`` 走 ``gettoken → message/send``，
   把卡片文本推给 userid（**@ 责任人**的真实发送路径）。

安全边界：验签失败 / appid 不匹配 / 补位非法一律拒绝（抛 WeComCryptoError）；
推送凭据（corp_id + agent_id + corp_secret）只从环境变量注入，不入库。

分层：``WeComAdapter`` 是**回调**实现（签名/加解密）；``WeComPushClient`` 是**应用消息**
实现。两者都在本文件内，因为凭据体系与协议细节（错误码、token 缓存）同源；
``WeComAdapter(push_client=…)`` 可把主动推送委托给它——同一个适配器既能收也能推。
"""
from __future__ import annotations

import hmac
import json
import logging
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable, Optional
from xml.etree import ElementTree

from ....common.config import settings
from ...core import cache
from ..im_gateway import IMGateway, InboundMessage, ReplyCard
from .wecom_crypto import (
    WeComCryptoError,
    compute_signature,
    decrypt_msg,
    encrypt_msg,
)

logger = logging.getLogger(__name__)

#: 企业微信服务端根地址（可覆盖用于自建代理 / 测试替身）
DEFAULT_BASE_URL = "https://qyapi.weixin.qq.com"

#: access_token 默认有效期（企微 7200s；提前 300s 过期重取）
_TOKEN_TTL_S = 7200
_TOKEN_SKEW_S = 300

#: 回调时间戳最大允许偏差（秒）；nonce 去重窗口取其 2 倍
DEFAULT_MAX_SKEW_S = 300


class _NonceGuard:
    """进程内 nonce 去重（Redis 不可用时的降级实现；带 TTL 与容量上限）。"""

    def __init__(self, max_entries: int = 4096) -> None:
        self._seen: dict[str, float] = {}
        self._max_entries = max_entries
        self._lock = threading.Lock()

    def consume(self, nonce: str, ttl_s: int) -> bool:
        """首次使用返回 True；窗口内重复使用返回 False。"""
        now = time.time()
        with self._lock:
            if len(self._seen) >= self._max_entries:
                self._seen = {k: v for k, v in self._seen.items() if v > now}
            expires = self._seen.get(nonce)
            if expires is not None and expires > now:
                return False
            self._seen[nonce] = now + ttl_s
            return True


#: 进程级去重表（API 每次请求新建 adapter，故不能挂在实例上）
_NONCE_GUARD = _NonceGuard()


def _configured_max_skew() -> int:
    try:
        value = int(str(getattr(settings, "IM_CALLBACK_MAX_SKEW_S", DEFAULT_MAX_SKEW_S)))
    except (TypeError, ValueError):
        return DEFAULT_MAX_SKEW_S
    return value if value > 0 else DEFAULT_MAX_SKEW_S


def _cdata(text: str) -> str:
    """XML CDATA 包裹（内容含 ]]> 时按平台惯例原样包裹，边界由测试覆盖）。"""
    return f"<![CDATA[{text}]]>"


# ---------------- 传输层（默认 stdlib urllib；可注入替身，离线可测） ----------------

class WeComTransportError(RuntimeError):
    """传输层失败（网络 / HTTP 非 2xx）。"""


def urllib_transport(method: str, path: str, *,
                     params: Optional[dict[str, str]] = None,
                     payload: Optional[dict[str, Any]] = None,
                     base_url: str = DEFAULT_BASE_URL,
                     timeout_s: float = 8.0) -> dict[str, Any]:
    """默认传输：stdlib urllib（不引入 requests/httpx，与 feishu_calendar 同法）。"""
    url = base_url.rstrip("/") + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as resp:  # noqa: S310
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:  # 4xx/5xx 也带 JSON 体，尽量解析出来
        raw = exc.read().decode("utf-8", errors="replace")
        raise WeComTransportError(f"HTTP {exc.code}: {raw[:200]}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise WeComTransportError(f"网络失败: {exc}") from exc
    try:
        return json.loads(raw or "{}")
    except json.JSONDecodeError as exc:
        raise WeComTransportError(f"响应不是 JSON: {raw[:200]}") from exc


# ---------------- 应用消息（主动推送 / @ 责任人） ----------------

class WeComPushClient:
    """企业微信**应用消息**客户端：``gettoken`` → ``message/send``。

    参数（全部来自环境变量注入的凭据，**不入库**）：

    - ``corp_id``：企业 ID（CorpID）；
    - ``agent_id``：自建应用 AgentId；
    - ``corp_secret``：自建应用 Secret；
    - ``transport``：可注入的传输实现（默认 ``urllib_transport``，测试注入替身）。

    行为纪律：

    - token 带 TTL 缓存（企微 7200s，提前 300s 重取），避免每次推送都换 token；
    - 企微接口的 ``errcode != 0`` 一律抛 ``WeComTransportError``（fail-explicit：
      推不出去要让调用方记账，绝不静默当成功）；
    - 凭据缺失时构造期即报错——配置错误不该等到"要发消息的那一刻"才暴露。
    """

    GET_TOKEN_PATH = "/cgi-bin/gettoken"
    SEND_PATH = "/cgi-bin/message/send"

    def __init__(self, corp_id: str, agent_id: str, corp_secret: str,
                 *, base_url: str = DEFAULT_BASE_URL,
                 transport: Optional[Callable[..., dict[str, Any]]] = None,
                 timeout_s: float = 8.0) -> None:
        if not (str(corp_id).strip() and str(agent_id).strip()
                and str(corp_secret).strip()):
            raise WeComTransportError(
                "应用消息凭据不完整：需要 corp_id + agent_id + corp_secret"
                "（环境变量 IM_CORP_ID / IM_AGENT_ID / IM_CORP_SECRET）")
        self.corp_id = str(corp_id).strip()
        self.agent_id = str(agent_id).strip()
        self.corp_secret = str(corp_secret).strip()
        self.base_url = base_url
        self.timeout_s = float(timeout_s)
        self._transport = transport or urllib_transport
        self._token = ""
        self._token_expire_at = 0.0

    # ---------- 令牌 ----------

    def access_token(self, force: bool = False) -> str:
        """取（并缓存）企业微信 access_token；``force=True`` 跳过缓存重取。"""
        if not force and self._token and time.time() < self._token_expire_at:
            return self._token
        resp = self._transport(
            "GET", self.GET_TOKEN_PATH,
            params={"corpid": self.corp_id, "corpsecret": self.corp_secret},
            base_url=self.base_url, timeout_s=self.timeout_s)
        token = str(resp.get("access_token") or "")
        if not token or int(resp.get("errcode") or 0) != 0:
            raise WeComTransportError(
                f"获取 access_token 失败: [{resp.get('errcode')}] {resp.get('errmsg')}")
        expire = int(resp.get("expires_in") or _TOKEN_TTL_S)
        self._token = token
        self._token_expire_at = time.time() + max(60, expire - _TOKEN_SKEW_S)
        logger.info("[wecom] access_token 已刷新（有效 %ds）", expire)
        return token

    # ---------- 发送 ----------

    def send_text(self, to_user: str, content: str, *,
                  safe: int = 0) -> dict[str, Any]:
        """发一条文本应用消息给 ``to_user``（企微 userid，多个用 ``|`` 分隔）。"""
        if not str(to_user or "").strip():
            raise WeComTransportError("应用消息收件人（userid）为空")
        resp = self._transport(
            "POST", self.SEND_PATH,
            params={"access_token": self.access_token()},
            payload={"touser": str(to_user), "msgtype": "text",
                     "agentid": int(self.agent_id),
                     "text": {"content": str(content)}, "safe": int(safe)},
            base_url=self.base_url, timeout_s=self.timeout_s)
        if int(resp.get("errcode") or 0) != 0:
            raise WeComTransportError(
                f"应用消息发送失败: [{resp.get('errcode')}] {resp.get('errmsg')} "
                f"（invaliduser={resp.get('invaliduser') or '—'}）")
        return resp

    def push(self, user_id: str, card: ReplyCard) -> None:
        """``IMGateway.push`` 同签名：把卡片文本推给该 userid（供通道直接当网关用）。

        这样"只有应用消息凭据、没有回调凭据"的部署也能直接把它交给
        ``IMGroupNotificationChannel(gateway=…)``——不必为了推送去凑 ASE 密钥。
        """
        self.send_text(user_id, WeComAdapter.card_to_text(card))



class WeComAdapter(IMGateway):
    """企业微信回调适配器（IMGateway 实现）。

    参数：
        token:     回调 Token（验签用）
        aes_key:   EncodingAESKey（43 位 Base64）
        appid:     AgentID / 企业 ID（解密后归属校验）
    """

    def __init__(self, token: str, aes_key: str, appid: str,
                 push_client: Optional["WeComPushClient"] = None,
                 max_skew_s: Optional[int] = None) -> None:
        if not token or not aes_key or not appid:
            raise ValueError("token / aes_key / appid 均不能为空")
        self.token = token
        self.aes_key = aes_key
        self.appid = appid
        # 可选：配齐企业应用凭据后，主动推送走真实应用消息（见 WeComPushClient）
        self.push_client = push_client
        self.max_skew_s = int(max_skew_s) if max_skew_s is not None else _configured_max_skew()
        # nonce 去重窗口覆盖整个允许时间窗（窗口外的时间戳已被时间校验拒绝）
        self.nonce_ttl_s = max(60, self.max_skew_s * 2)

    # ---------- IMGateway 契约 ----------

    def _verify_signature(self, payload: dict[str, Any]) -> bool:
        """只校验签名（不消费 nonce、不校验时间戳）。"""
        data = payload.get("encrypt") or payload.get("echostr") or ""
        expected = compute_signature(
            self.token, str(payload.get("timestamp", "")),
            str(payload.get("nonce", "")), data)
        ok = hmac.compare_digest(expected, str(payload.get("msg_signature", "")))
        if not ok:
            logger.warning("企微回调验签失败: expected=%s got=%s",
                           expected, payload.get("msg_signature"))
        return ok

    def _check_timestamp(self, payload: dict[str, Any]) -> tuple[bool, str]:
        raw = str(payload.get("timestamp", "")).strip()
        if not raw.isdigit():
            return False, f"timestamp 非法: {raw!r}"
        skew = abs(time.time() - int(raw))
        if skew > self.max_skew_s:
            return False, f"timestamp 偏差 {skew:.0f}s 超过 {self.max_skew_s}s"
        return True, ""

    def _consume_nonce(self, nonce: str) -> bool:
        """nonce 一次性消费：Redis 原子 NX 优先；不可用时退回进程内去重。"""
        # Redis 可用时多实例共享同一份去重状态；返回 None 表示不可用
        redis_ok = cache.set_if_absent(
            f"mfg:im:nonce:{self.appid}:{nonce}", {"ts": time.time()},
            ttl=self.nonce_ttl_s)
        if redis_ok is not None:
            return redis_ok
        return _NONCE_GUARD.consume(nonce, self.nonce_ttl_s)

    def verify_callback(self, payload: dict[str, Any],
                        headers: Optional[dict[str, str]] = None) -> bool:
        """校验回调：签名 + 时间戳窗口 + nonce 一次性消费（防重放）。

        payload 需含 msg_signature/timestamp/nonce 及 encrypt 或 echostr。
        **一次 payload 只能验证成功一次**：重复提交同 nonce 会被拒绝。
        """
        if not self._verify_signature(payload):
            return False
        fresh, why = self._check_timestamp(payload)
        if not fresh:
            logger.warning("企微回调时间戳拒绝: %s", why)
            return False
        nonce = str(payload.get("nonce", "")).strip()
        if not nonce or len(nonce) > 128:
            logger.warning("企微回调 nonce 为空或过长，拒绝（无法做重放防护）")
            return False
        if not self._consume_nonce(nonce):
            logger.warning("企微回调 nonce 重放，拒绝: nonce=%s", nonce)
            return False
        return True

    def receive(self, payload: dict[str, Any]) -> InboundMessage:
        """解密回调消息体 → InboundMessage（调用前必须先 verify_callback）。

        这里只重复做签名与时间戳校验，**不再消费 nonce**——否则「先验后收」
        的合法请求会被自己的重放防护拒绝。
        """
        if not self._verify_signature(payload):
            raise WeComCryptoError("回调验签失败，拒绝解析消息")
        fresh, why = self._check_timestamp(payload)
        if not fresh:
            raise WeComCryptoError(f"回调时间戳无效：{why}")
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
        """主动推送：配了 ``push_client`` → 走真实应用消息 API；否则**只留痕并告警**。

        为什么保留"只留痕"这条路径：未配企业应用凭据时（演示态默认），推送不该
        假装成功也不该直接抛断链路——适配器记录 ``self.pushed`` 并打 WARNING，
        由上层（``IMGroupNotificationChannel``）据此如实记账为 failed/skipped。
        """
        if self.push_client is not None:
            self.push_client.push(user_id, card)
            return
        logger.warning("[IM push] 未配置企业应用凭据（IM_CORP_ID/IM_AGENT_ID/"
                       "IM_CORP_SECRET），本次推送只留痕不真发 -> %s | %s",
                       user_id, card.title)
        self.pushed: list[dict[str, Any]] = getattr(self, "pushed", [])
        self.pushed.append({"user_id": user_id, "title": card.title,
                            "body": card.body, "actions": card.actions,
                            "delivered": False})

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
