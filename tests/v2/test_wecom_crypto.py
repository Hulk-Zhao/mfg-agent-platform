"""企业微信加解密/验签测试：构造已知向量 + 协议结构 + 安全负向用例。"""
import hashlib
import re
import time
import uuid
from xml.etree import ElementTree

import pytest

from mfg_agents.v2.contracts.adapters.wecom import WeComAdapter
from mfg_agents.v2.contracts.adapters.wecom_crypto import (
    WeComCryptoError,
    compute_signature,
    decrypt_msg,
    encrypt_msg,
    pkcs7_pad,
    pkcs7_unpad,
)

# 企业微信官方文档示例凭据（公开测试向量）
TOKEN = "QDG6eK"
AES_KEY = "abcdefghijklmnopqrstuvwxyz0123456789ABCDEFG"
APPID = "wx5823bf96d3bd56c7"


# ---------- 签名 ----------

def test_signature_known_vector():
    """签名 = SHA1(字典序拼接)，用独立 hashlib 实现构造已知向量。"""
    expected = hashlib.sha1(
        "".join(sorted([TOKEN, "1729000000", "nonce123", "encrypt_data"]))
        .encode("utf-8")).hexdigest()
    assert compute_signature(TOKEN, "1729000000", "nonce123",
                             "encrypt_data") == expected


def test_signature_order_insensitive_to_input_order():
    """签名只看字典序，与传入顺序无关。"""
    a = compute_signature(TOKEN, "1", "2", "3")
    b = compute_signature(TOKEN, "3", "2", "1")
    assert a == b


# ---------- 加解密 ----------

def test_encrypt_decrypt_roundtrip_fixed_prefix():
    """固定随机前缀 → 确定性密文；解密必须还原明文（协议结构向量）。"""
    prefix = bytes(range(16))
    plaintext = "<xml><Content><![CDATA[A型号在SMT-1停线]]></Content></xml>"
    cipher = encrypt_msg(AES_KEY, APPID, plaintext, random_prefix=prefix)
    assert cipher == encrypt_msg(AES_KEY, APPID, plaintext, random_prefix=prefix)
    assert decrypt_msg(AES_KEY, APPID, cipher) == plaintext


def test_decrypt_rejects_wrong_appid():
    """appid 归属校验：跨应用密文必须拒绝（防注入）。"""
    cipher = encrypt_msg(AES_KEY, APPID, "hello")
    with pytest.raises(WeComCryptoError, match="appid"):
        decrypt_msg(AES_KEY, "wxotherappid99", cipher)


def test_decrypt_rejects_tampered_ciphertext():
    """篡改密文（保持 Base64 长度合法）必须解密失败。"""
    cipher = encrypt_msg(AES_KEY, APPID, "hello world")
    mid = len(cipher) // 2
    flipped = (cipher[:mid]
               + ("B" if cipher[mid] != "B" else "C")
               + cipher[mid + 1:])
    with pytest.raises(WeComCryptoError):
        decrypt_msg(AES_KEY, APPID, flipped)


def test_pkcs7_pad_unpad_roundtrip():
    data = b"mfg-agent-platform"
    padded = pkcs7_pad(data)
    assert len(padded) % 32 == 0
    assert pkcs7_unpad(padded) == data


def test_pkcs7_unpad_rejects_invalid():
    with pytest.raises(WeComCryptoError):
        pkcs7_unpad(b"abc" + bytes([99]) * 4)  # 补位内容不一致
    with pytest.raises(WeComCryptoError):
        pkcs7_unpad(b"")                        # 空数据


def test_bad_base64_rejected():
    with pytest.raises(WeComCryptoError):
        decrypt_msg(AES_KEY, APPID, "!!!not-base64!!!")


# ---------- 适配器协议 ----------

def _make_payload(encrypt: str, signature: str, *,
                  timestamp: str | None = None,
                  nonce: str | None = None) -> dict:
    return {"msg_signature": signature,
            "timestamp": str(timestamp if timestamp is not None else int(time.time())),
            "nonce": nonce or ("nonce-" + uuid.uuid4().hex[:10]),
            "encrypt": encrypt}


def test_adapter_verify_url_flow():
    """GET URL 校验：echostr 加密 + 签名 → 验签通过 → 解密回显。"""
    adapter = WeComAdapter(TOKEN, AES_KEY, APPID)
    echostr = encrypt_msg(AES_KEY, APPID, "echostr_plain")
    timestamp, nonce = str(int(time.time())), "nonce-url-" + uuid.uuid4().hex[:8]
    sig = compute_signature(TOKEN, timestamp, nonce, echostr)
    assert adapter.verify_url({**_make_payload(echostr, sig, timestamp=timestamp,
                                               nonce=nonce),
                               "echostr": echostr}) == "echostr_plain"


def test_adapter_verify_rejects_forged_signature():
    adapter = WeComAdapter(TOKEN, AES_KEY, APPID)
    echostr = encrypt_msg(AES_KEY, APPID, "echostr_plain")
    assert adapter.verify_callback(_make_payload(echostr, "0" * 40)) is False
    with pytest.raises(WeComCryptoError):
        adapter.verify_url({**_make_payload(echostr, "0" * 40),
                            "echostr": echostr})


def test_adapter_rejects_stale_timestamp():
    adapter = WeComAdapter(TOKEN, AES_KEY, APPID, max_skew_s=60)
    echostr = encrypt_msg(AES_KEY, APPID, "echo")
    timestamp = str(int(time.time()) - 120)
    sig = compute_signature(TOKEN, timestamp, "nonce-stale", echostr)
    assert adapter.verify_callback(
        _make_payload(echostr, sig, timestamp=timestamp, nonce="nonce-stale")) is False


def test_adapter_rejects_future_timestamp():
    adapter = WeComAdapter(TOKEN, AES_KEY, APPID, max_skew_s=60)
    echostr = encrypt_msg(AES_KEY, APPID, "echo")
    timestamp = str(int(time.time()) + 120)
    sig = compute_signature(TOKEN, timestamp, "nonce-future", echostr)
    assert adapter.verify_callback(
        _make_payload(echostr, sig, timestamp=timestamp,
                      nonce="nonce-future")) is False


def test_adapter_rejects_nonce_replay():
    """同一 nonce 第二次验证必须失败（重放防护）。"""
    adapter = WeComAdapter(TOKEN, AES_KEY, APPID)
    echostr = encrypt_msg(AES_KEY, APPID, "echo")
    timestamp, nonce = str(int(time.time())), "nonce-replay-" + uuid.uuid4().hex[:8]
    sig = compute_signature(TOKEN, timestamp, nonce, echostr)
    payload = _make_payload(echostr, sig, timestamp=timestamp, nonce=nonce)
    assert adapter.verify_callback(payload) is True
    assert adapter.verify_callback(payload) is False


def test_adapter_nonce_replay_fallback_without_redis(monkeypatch):
    """Redis 不可用时退回进程内去重，重放防护不能因此失效。"""
    from mfg_agents.v2.core import cache

    monkeypatch.setattr(cache, "set_if_absent", lambda *a, **k: None)
    adapter = WeComAdapter(TOKEN, AES_KEY, APPID)
    echostr = encrypt_msg(AES_KEY, APPID, "echo")
    timestamp = str(int(time.time()))
    nonce = "nonce-fallback-" + uuid.uuid4().hex[:8]
    sig = compute_signature(TOKEN, timestamp, nonce, echostr)
    payload = _make_payload(echostr, sig, timestamp=timestamp, nonce=nonce)
    assert adapter.verify_callback(payload) is True
    assert adapter.verify_callback(payload) is False


def test_adapter_receive_after_verify_does_not_consume_nonce_again():
    """先 verify_callback 再 receive 是合法链路，不能被自己的重放防护误杀。"""
    adapter = WeComAdapter(TOKEN, AES_KEY, APPID)
    inner = ("<xml><ToUserName><![CDATA[wx5823bf96d3bd56c7]]></ToUserName>"
             "<FromUserName><![CDATA[lisi]]></FromUserName>"
             "<MsgType><![CDATA[text]]></MsgType>"
             "<Content><![CDATA[你好]]></Content></xml>")
    encrypt = encrypt_msg(AES_KEY, APPID, inner)
    timestamp, nonce = str(int(time.time())), "nonce-flow-" + uuid.uuid4().hex[:8]
    sig = compute_signature(TOKEN, timestamp, nonce, encrypt)
    payload = _make_payload(encrypt, sig, timestamp=timestamp, nonce=nonce)
    assert adapter.verify_callback(payload) is True
    assert adapter.receive(payload).text == "你好"


def test_adapter_receive_parses_text_message():
    """POST 消息：解密 → InboundMessage（user/text/channel/raw）。"""
    adapter = WeComAdapter(TOKEN, AES_KEY, APPID)
    inner = ("<xml><ToUserName><![CDATA[wx5823bf96d3bd56c7]]></ToUserName>"
             "<FromUserName><![CDATA[zhangsan]]></FromUserName>"
             "<CreateTime>1729000000</CreateTime>"
             "<MsgType><![CDATA[text]]></MsgType>"
             "<Content><![CDATA[A型号在SMT-1停线，何时复线？]]></Content>"
             "<MsgId>6001</MsgId></xml>")
    encrypt = encrypt_msg(AES_KEY, APPID, inner)
    timestamp, nonce = str(int(time.time())), "nonce-recv-" + uuid.uuid4().hex[:8]
    sig = compute_signature(TOKEN, timestamp, nonce, encrypt)
    msg = adapter.receive(_make_payload(encrypt, sig, timestamp=timestamp,
                                        nonce=nonce))
    assert msg.user_id == "zhangsan"
    assert msg.text == "A型号在SMT-1停线，何时复线？"
    assert msg.channel == "wecom"
    assert msg.raw["ToUserName"] == APPID


def test_adapter_reply_xml_self_verifies():
    """被动回复：XML 组装 → 签名自验证 → 解密内容与卡片一致。"""
    from mfg_agents.v2.contracts.im_gateway import InboundMessage, ReplyCard
    adapter = WeComAdapter(TOKEN, AES_KEY, APPID)
    msg = InboundMessage(user_id="zhangsan", conversation_id=APPID,
                         text="停线了吗", channel="wecom",
                         raw={"ToUserName": APPID})
    card = ReplyCard(title="停线应急协同", body="预计2小时恢复",
                     actions=[{"key": "confirm_restart", "label": "确认复线"}])
    reply_xml = adapter.reply(msg, card)
    root = ElementTree.fromstring(reply_xml)
    encrypt = root.findtext("Encrypt")
    sig = root.findtext("MsgSignature")
    assert compute_signature(TOKEN, root.findtext("TimeStamp"),
                             root.findtext("Nonce"), encrypt) == sig
    plain = decrypt_msg(AES_KEY, APPID, encrypt)
    assert "预计2小时恢复" in plain
    assert "确认复线" in plain
    assert "<ToUserName><![CDATA[zhangsan]]>" in plain


def test_parse_callback_xml_requires_encrypt():
    adapter = WeComAdapter(TOKEN, AES_KEY, APPID)
    with pytest.raises(WeComCryptoError, match="Encrypt"):
        adapter.parse_callback_xml("<xml><Foo>bar</Foo></xml>")
    with pytest.raises(WeComCryptoError, match="解析失败"):
        adapter.parse_callback_xml("not-xml")


def test_adapter_requires_nonempty_credentials():
    with pytest.raises(ValueError):
        WeComAdapter("", AES_KEY, APPID)
