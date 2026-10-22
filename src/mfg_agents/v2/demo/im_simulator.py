"""IM 模拟器：离线模拟企业微信发消息 → 完整链路 → 消息卡片效果。

不依赖外网/企微后台：用适配器的加密原语构造真实格式的回调载荷
（AES-256-CBC + SHA1 验签），再走 验签 → 解密 → 编排 → 回复卡片 全流程。

用法：
    uv run python -m mfg_agents.v2.demo.im_simulator
    uv run python -m mfg_agents.v2.demo.im_simulator "A型号在SMT-1停线，何时复线？"
"""
from __future__ import annotations

import logging
import sys
import time
import uuid
from typing import Any, Optional

from ..contracts.adapters.wecom import WeComAdapter
from ..contracts.adapters.wecom_crypto import (
    compute_signature,
    decrypt_msg,
    encrypt_msg,
)
from ..contracts.im_gateway import ReplyCard
from ..core.events import QueueSink
from ..core.orchestrator import create_default_orchestrator
from ...common.llm_factory import llm_available

# 企业微信官方文档示例凭据（公开测试向量，仅本地演示用）
DEMO_TOKEN = "QDG6eK"
DEMO_AES_KEY = "abcdefghijklmnopqrstuvwxyz0123456789ABCDEFG"
DEMO_APPID = "wx5823bf96d3bd56c7"

DEFAULT_QUERY = "A型号在SMT-1停线，何时复线？"


def _section(title: str) -> None:
    print()
    print(f"---------- {title} " + "-" * max(0, 46 - len(title)))


def run(query: str = DEFAULT_QUERY) -> dict[str, Any]:
    """离线模拟一次完整的企微消息交互。"""
    print("=" * 60)
    print(" V2 · 企业微信 IM 模拟器（离线，真实回调协议格式）")
    print("=" * 60)

    adapter = WeComAdapter(token=DEMO_TOKEN, aes_key=DEMO_AES_KEY,
                           appid=DEMO_APPID)

    # 1) 构造回调载荷（与企微推送格式一致：Encrypt + 签名）
    _section("1. 构造企业微信回调（加密 + 签名）")
    # 时间戳取当前时间、GET/POST 用不同 nonce：回调重放防护会拒过期/重复 nonce
    timestamp = str(int(time.time()))
    nonce = "sim-post-" + uuid.uuid4().hex[:8]
    inner_xml = (
        "<xml>"
        f"<ToUserName><![CDATA[{DEMO_APPID}]]></ToUserName>"
        "<FromUserName><![CDATA[zhangsan]]></FromUserName>"
        f"<CreateTime>{timestamp}</CreateTime>"
        "<MsgType><![CDATA[text]]></MsgType>"
        f"<Content><![CDATA[{query}]]></Content>"
        "<MsgId>6001234567890123456</MsgId>"
        "</xml>"
    )
    encrypt = encrypt_msg(DEMO_AES_KEY, DEMO_APPID, inner_xml)
    signature = compute_signature(DEMO_TOKEN, timestamp, nonce, encrypt)
    payload = {"msg_signature": signature, "timestamp": timestamp,
               "nonce": nonce, "encrypt": encrypt}
    print(f"echostr/encrypt 长度: {len(encrypt)}（AES-256-CBC, PKCS#7@32B）")
    print(f"msg_signature: {signature}")

    # 2) GET URL 校验演示
    _section("2. GET URL 校验（echostr + SHA1 验签）")
    echostr_cipher = encrypt_msg(DEMO_AES_KEY, DEMO_APPID, "echo_plain_text")
    get_ts = str(int(time.time()))
    get_nonce = "sim-get-" + uuid.uuid4().hex[:8]
    echostr_sig = compute_signature(DEMO_TOKEN, get_ts, get_nonce, echostr_cipher)
    echostr_plain = adapter.verify_url({
        "msg_signature": echostr_sig, "timestamp": get_ts,
        "nonce": get_nonce, "echostr": echostr_cipher})
    print(f"验签通过，解密 echostr -> {echostr_plain!r}")

    # 3) 验签 + 解密 + 解析
    _section("3. POST 消息回调（验签 → 解密 → InboundMessage）")
    print(f"verify_callback: {adapter.verify_callback(payload)}")
    msg = adapter.receive(payload)
    print(f"InboundMessage: user_id={msg.user_id} "
          f"conversation_id={msg.conversation_id} text={msg.text!r} "
          f"channel={msg.channel}")

    # 4) 负向用例：篡改签名必须被拒绝
    _section("4. 安全负向用例（篡改签名/密文必须拒绝）")
    tampered = dict(payload, msg_signature="0" * 40)
    print(f"篡改签名 verify_callback: {adapter.verify_callback(tampered)}（期望 False）")
    # 保持 Base64 长度合法（翻转中间字符），解密必须失败
    mid = len(encrypt) // 2
    flipped = encrypt[:mid] + ("B" if encrypt[mid] != "B" else "C") + encrypt[mid + 1:]
    try:
        decrypt_msg(DEMO_AES_KEY, DEMO_APPID, flipped)
        print("篡改密文解密: 未拒绝（异常！）")
    except Exception as exc:  # noqa: BLE001
        print(f"篡改密文解密被拒绝: {type(exc).__name__}: {exc}")

    # 5) 全链路编排
    _section("5. 全链路编排（TwoLevelRouter → YamlDagExecutor）")
    print(f"LLM 可用性：{'可用' if llm_available() else '不可用（degraded）'}")
    events: list[dict] = []

    def _print(ev: dict) -> None:
        events.append(ev)
        detail = ev.get("detail", "") or ev.get("summary", "")
        print(f"  [{ev.get('type')}] {ev.get('node') or ev.get('label') or ''} "
              f"{detail[:70]}")

    orchestrator = create_default_orchestrator()
    result = orchestrator.handle(
        msg.text, {"user_id": msg.user_id, "dept": "manufacturing",
                   "roles": ["manufacturing", "coordinator"]},
        sink=QueueSink(callback=_print))

    # 6) 消息卡片 + 被动回复
    _section("6. 消息卡片效果（ReplyCard）")
    actions: list[dict[str, str]] = []
    for item in result.hitl:
        actions.extend(item.get("actions", []))
    lines = [result.report or "（暂无结论）"]
    if result.hitl:
        lines += ["", "待人工确认："]
        lines += [f"- {i.get('prompt')}（{'、'.join(i.get('approver_roles', []))}）"
                  for i in result.hitl]
    if result.degraded:
        lines += ["", "⚠ degraded 模式：结论为规则/模板化输出，请人工复核。"]
    card = ReplyCard(title="停线应急协同 · 分析简报", body="\n".join(lines),
                     actions=actions, level="P1")
    print(f"标题: {card.title}")
    print(f"正文:\n{card.body}")
    print(f"按钮: {[a.get('label') for a in card.actions]}")

    _section("7. 被动回复 XML（加密回包，可解密验证）")
    reply_xml = adapter.reply(msg, card)
    print(f"回复体长度: {len(reply_xml)} 字符")
    # 回包自验证：从响应 XML 取 Encrypt 解密，应与明文文本一致
    from xml.etree import ElementTree
    root = ElementTree.fromstring(reply_xml)
    reply_encrypt = root.findtext("Encrypt")
    reply_sig = root.findtext("MsgSignature")
    ok = compute_signature(DEMO_TOKEN, root.findtext("TimeStamp"),
                           root.findtext("Nonce"), reply_encrypt) == reply_sig
    plain = decrypt_msg(DEMO_AES_KEY, DEMO_APPID, reply_encrypt)
    print(f"回复签名自验证: {ok}")
    print(f"解密回复明文（前 200 字）:\n{plain[:200]}")

    _section("8. 主动推送（push，预留通道）")
    adapter.push("manager-li", ReplyCard(
        title="停线影响评估待处理", body="请评估排产/物料/对客影响（SLA 2h）",
        level="P1"))
    for record in getattr(adapter, "pushed", []):
        print(f"  push -> {record['user_id']}: {record['title']}")

    _section("汇总")
    print(f"事件数: {len(events)} | HITL: {len(result.hitl)} | "
          f"工单: {len(result.workflow_tasks)} | degraded: {result.degraded}")
    print(f"总耗时: {result.elapsed_ms:.0f}ms")
    return result.to_dict()


def main(argv: Optional[list[str]] = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    query = args[0] if args else DEFAULT_QUERY
    logging.basicConfig(level=logging.WARNING,
                        format="%(levelname)s %(name)s: %(message)s")
    run(query)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
