"""企业微信应用消息（@ 责任人）凭据与发送链路的契约测试。

守四件事：
1. **报文正确**：``gettoken(corpid+corpsecret)`` → ``message/send(touser+agentid+text)``，
   字段名与取值都对得上（企微按字段名解析，错一个字段就静默丢消息）；
2. **令牌缓存**：同一进程内多次推送不重复换 token（7200s 有效，提前 300s 重取）；
3. **fail-explicit**：凭据不齐 → 构造期报错；接口 ``errcode != 0`` → 抛错；
   适配器未配 ``push_client`` → 只留痕并告警，不假装已投递；
4. **装配接线**：默认运行时按凭据决定网关——齐 → 真实推送客户端；缺 → None
   （现场群通道据此如实记 failed，见 tests/v2/test_im_groups.py）。
"""
from __future__ import annotations

import pytest

from mfg_agents.v2.contracts.adapters.wecom import (
    WeComAdapter,
    WeComPushClient,
    WeComTransportError,
)
from mfg_agents.v2.contracts.im_gateway import ReplyCard

_CORP = "wx-test-corp"
_AGENT = "1000002"
_SECRET = "test-secret"


class _FakeTransport:
    """传输层替身：记录调用并按脚本应答（离线可测，不触网）。"""

    def __init__(self, token_resp: dict | None = None,
                 send_resp: dict | None = None) -> None:
        self.calls: list[dict] = []
        self.token_resp = token_resp or {"errcode": 0, "access_token": "TOKEN-1",
                                         "expires_in": 7200}
        self.send_resp = send_resp or {"errcode": 0, "errmsg": "ok"}

    def __call__(self, method: str, path: str, *, params=None, payload=None,
                 base_url: str = "", timeout_s: float = 0.0) -> dict:
        self.calls.append({"method": method, "path": path, "params": dict(params or {}),
                           "payload": payload, "base_url": base_url})
        if path == WeComPushClient.GET_TOKEN_PATH:
            return dict(self.token_resp)
        return dict(self.send_resp)


def _card(title: str = "客诉处置", body: str = "请现场确认围堵") -> ReplyCard:
    return ReplyCard(title=title, body=body, level="P1")


# ---------------------------------------------------------------- 一、报文与缓存


def test_send_text_uses_expected_payload():
    """报文口径：touser / msgtype=text / agentid / text.content 必须齐且取值正确。"""
    transport = _FakeTransport()
    client = WeComPushClient(_CORP, _AGENT, _SECRET, transport=transport)
    client.send_text("zhang.san", "【客诉处置】请现场确认围堵")

    token_call, send_call = transport.calls
    assert token_call["method"] == "GET"
    assert token_call["path"] == "/cgi-bin/gettoken"
    assert token_call["params"] == {"corpid": _CORP, "corpsecret": _SECRET}

    assert send_call["method"] == "POST"
    assert send_call["path"] == "/cgi-bin/message/send"
    assert send_call["params"] == {"access_token": "TOKEN-1"}
    payload = send_call["payload"]
    assert payload["touser"] == "zhang.san"
    assert payload["msgtype"] == "text"
    assert payload["agentid"] == int(_AGENT)
    assert payload["text"] == {"content": "【客诉处置】请现场确认围堵"}


def test_push_renders_card_to_text_and_caches_token():
    """``push`` 与 IMGateway 同签名（卡片转文本）；第二次推送复用 token 不重取。"""
    transport = _FakeTransport()
    client = WeComPushClient(_CORP, _AGENT, _SECRET, transport=transport)
    client.push("zhang.san", _card())
    client.push("li.si", _card(title="8D 整改工单通知"))

    token_calls = [c for c in transport.calls if c["path"] == "/cgi-bin/gettoken"]
    send_calls = [c for c in transport.calls if c["path"] == "/cgi-bin/message/send"]
    assert len(token_calls) == 1, "token 必须缓存（重复推送不重换）"
    assert [c["payload"]["touser"] for c in send_calls] == ["zhang.san", "li.si"]
    assert "【8D 整改工单通知】" in send_calls[1]["payload"]["text"]["content"]

    client.access_token(force=True)
    assert len([c for c in transport.calls
                if c["path"] == "/cgi-bin/gettoken"]) == 2, "force 应重取"


# ---------------------------------------------------------------- 二、fail-explicit


def test_incomplete_credentials_rejected_at_construction():
    """凭据不齐 → 构造期即报错（配置错误不该等"要发消息的那一刻"才暴露）。"""
    for args in (("", _AGENT, _SECRET), (_CORP, "", _SECRET), (_CORP, _AGENT, "")):
        with pytest.raises(WeComTransportError, match="凭据不完整"):
            WeComPushClient(*args, transport=_FakeTransport())


def test_api_errcode_raises():
    """企微 ``errcode != 0`` 一律抛错——推不出去必须让上层记账，不静默当成功。"""
    bad_token = WeComPushClient(_CORP, _AGENT, _SECRET,
                                transport=_FakeTransport(
                                    token_resp={"errcode": 40013, "errmsg": "invalid corpid"}))
    with pytest.raises(WeComTransportError, match="40013"):
        bad_token.access_token()

    bad_send = WeComPushClient(_CORP, _AGENT, _SECRET,
                               transport=_FakeTransport(
                                   send_resp={"errcode": 81013, "errmsg": "invalid user",
                                              "invaliduser": "ghost"}))
    with pytest.raises(WeComTransportError, match="81013"):
        bad_send.send_text("ghost", "x")

    empty_user = WeComPushClient(_CORP, _AGENT, _SECRET, transport=_FakeTransport())
    with pytest.raises(WeComTransportError, match="userid）为空"):
        empty_user.send_text("", "x")


def test_adapter_without_push_client_only_traces_and_warns():
    """未配 push_client → 只留痕并标注 delivered=False（不假装已投递）。"""
    adapter = WeComAdapter("t", "a" * 43, "corpid")
    adapter.push("zhang.san", _card())
    assert adapter.pushed[0]["delivered"] is False

    transport = _FakeTransport()
    wired = WeComAdapter("t", "a" * 43, "corpid",
                         push_client=WeComPushClient(_CORP, _AGENT, _SECRET,
                                                     transport=transport))
    wired.push("zhang.san", _card())
    assert [c["path"] for c in transport.calls] == [
        "/cgi-bin/gettoken", "/cgi-bin/message/send"]
    assert getattr(wired, "pushed", []) == [], "配了 push_client 就不该再走只留痕路径"


# ---------------------------------------------------------------- 三、装配接线


def test_runtime_gateway_depends_on_credentials(monkeypatch):
    """默认运行时：凭据齐 → 真实推送网关；缺 → None（通道据此如实记 failed）。"""
    from mfg_agents.common.config import settings
    from mfg_agents.v2.core.orchestrator import _build_im_push_gateway

    for key, value in (("IM_CORP_ID", ""), ("IM_AGENT_ID", ""), ("IM_CORP_SECRET", "")):
        monkeypatch.setattr(settings, key, value)
    monkeypatch.setattr(settings, "IM_APPID", "")
    assert _build_im_push_gateway(settings) is None

    # IM_CORP_ID 缺省回落 IM_APPID（回调侧同一字段 = 企业 CorpID）
    monkeypatch.setattr(settings, "IM_APPID", _CORP)
    monkeypatch.setattr(settings, "IM_AGENT_ID", _AGENT)
    assert _build_im_push_gateway(settings) is None, "缺 corp_secret 仍不得视为已配置"

    monkeypatch.setattr(settings, "IM_CORP_SECRET", _SECRET)
    gateway = _build_im_push_gateway(settings)
    assert isinstance(gateway, WeComPushClient)
    assert (gateway.corp_id, gateway.agent_id) == (_CORP, _AGENT)


def test_channel_delivers_members_through_push_client(monkeypatch, tmp_path):
    """端到端：配齐凭据后，工段群里的「责任人」经真实应用消息客户端投递并记账为 pushed。"""
    from mfg_agents.common.config import settings
    from mfg_agents.v2.core.orchestrator import _build_im_push_gateway
    from mfg_agents.v2.services import im_groups
    from mfg_agents.v2.services.notification import IMGroupNotificationChannel

    records = tmp_path / "terms.yaml"
    records.write_text(
        "version: 1\ngroups:\n  SMT:\n    name: SMT 现场处置群\n    slug: smt\n",
        encoding="utf-8")
    monkeypatch.setenv("IM_GROUP_MEMBERS_SMT", "zhang.san,li.si")
    monkeypatch.setattr(settings, "IM_CORP_ID", _CORP)
    monkeypatch.setattr(settings, "IM_AGENT_ID", _AGENT)
    monkeypatch.setattr(settings, "IM_CORP_SECRET", _SECRET)
    im_groups.reset_im_groups()

    transport = _FakeTransport()
    gateway = _build_im_push_gateway(settings)
    gateway._transport = transport          # 注入替身：不触网
    channel = IMGroupNotificationChannel(registry=im_groups.get_im_groups(str(records)),
                                        gateway=gateway)
    record = channel.send("P1", ["SMT"], "客诉处置", "请现场确认围堵")

    assert record["failed"] == []
    assert [p["user"] for p in record["pushed"]] == ["zhang.san", "li.si"]
    assert all(p["via"] == "app" for p in record["pushed"])
    assert [c["payload"]["touser"] for c in transport.calls
            if c["path"] == "/cgi-bin/message/send"] == ["zhang.san", "li.si"]
    im_groups.reset_im_groups()
