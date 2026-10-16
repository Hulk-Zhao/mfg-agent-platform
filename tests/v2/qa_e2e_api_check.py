"""QA E2E：FastAPI V2 全端点实测（TestClient，不起真实服务）。

覆盖：POST /api/v2/query → GET /api/v2/events（SSE  drain）→
企微 GET URL 校验 → POST 解密回包（解密验证回复内容）。
"""
import base64
import json
import os
import queue
import sys
from xml.etree import ElementTree

os.environ.setdefault("IM_TOKEN", "QDG6eK")
os.environ.setdefault("IM_AES_KEY",
                      "abcdefghijklmnopqrstuvwxyz0123456789ABCDEFG")
os.environ.setdefault("IM_APPID", "wx5823bf96d3bd56c7")

from fastapi.testclient import TestClient  # noqa: E402

from mfg_agents.v2.contracts.adapters.wecom_crypto import (  # noqa: E402
    compute_signature, decrypt_msg, encrypt_msg)

sys.path.insert(0, "src")
from mfg_agents.v2.demo.api import app  # noqa: E402

TOKEN = os.environ["IM_TOKEN"]
AES_KEY = os.environ["IM_AES_KEY"]
APPID = os.environ["IM_APPID"]

client = TestClient(app)
failures = []


def check(name, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" | {detail}" if detail else ""))
    if not cond:
        failures.append(name)


print("=" * 60)
print("QA E2E · FastAPI V2 端点实测")
print("=" * 60)

# 1) health
r = client.get("/api/v2/health")
check("GET /api/v2/health", r.status_code == 200 and r.json()["status"] == "ok",
      f"status={r.status_code}")

# 2) POST /api/v2/query
r = client.post("/api/v2/query", json={
    "query": "A型号在SMT-1停线，何时复线？",
    "user_ctx": {"user_id": "u-api", "dept": "manufacturing",
                 "roles": ["manufacturing", "coordinator"]},
    "request_id": "req-qa-e2e-001"})
check("POST /api/v2/query 200", r.status_code == 200, f"status={r.status_code}")
body = r.json()
data = body.get("data", {})
check("query code=0", body.get("code") == 0)
check("路由 L0 命中 line_stop_v2",
      data.get("route", {}).get("level") == "L0"
      and data.get("scenario_key") == "line_stop_v2")
check("两个 HITL waiting_hitl",
      len(data.get("hitl", [])) == 2
      and all(h["status"] == "waiting_hitl" for h in data.get("hitl", [])))
check("影响工单 running",
      len(data.get("workflow_tasks", [])) == 1
      and data["workflow_tasks"][0]["ticket"]["status"] == "running")
check("对客报告含脱敏话术", "计划内暂停" in data.get("report", ""))
check("degraded 标记", data.get("degraded") is True)
check("provenance 含 SQL 原文",
      any(p.get("sql") for p in data.get("provenance", [])))
masked = [p for p in data.get("provenance", [])
          if p.get("query_name") == "supply_batches_today"]
check("供应链 supplier 脱敏=True", bool(masked) and masked[0]["masked"] is True)

# 3) GET /api/v2/events（drain 请求级队列）
r = client.get("/api/v2/events", params={"request_id": "req-qa-e2e-001",
                                         "wait_s": 5})
events = []
for line in r.text.splitlines():
    if line.startswith("data: "):
        events.append(json.loads(line[6:]))
check("GET /api/v2/events 有事件流", len(events) > 5, f"{len(events)} 个事件")
types = {e.get("type") for e in events}
# BUG-5 已于 Round 2 修复（commit 54aa607）：tool 级事件经 SSE 全到达
check("事件枚举与 V1 兼容（agent_start/agent_end/tool_start/tool_end/fan_in）",
      {"agent_start", "agent_end", "tool_start", "tool_end", "fan_in"} <= types,
      f"types={sorted(types)}")
check("tool 事件成对完整（6 对）",
      sum(1 for e in events if e["type"] == "tool_start") == 6
      and sum(1 for e in events if e["type"] == "tool_end") == 6)

# 4) 未知 request_id → 404
r = client.get("/api/v2/events", params={"request_id": "req-not-exist"})
check("未知 request_id 404", r.status_code == 404, f"status={r.status_code}")

# 5) 企微 GET URL 校验
echostr_cipher = encrypt_msg(AES_KEY, APPID, "echo_plain_qa")
sig = compute_signature(TOKEN, "1729000000", "qa-nonce", echostr_cipher)
r = client.get("/api/v2/im/wecom/callback", params={
    "msg_signature": sig, "timestamp": "1729000000",
    "nonce": "qa-nonce", "echostr": echostr_cipher})
check("企微 GET 校验回显明文",
      r.status_code == 200 and r.text == "echo_plain_qa",
      f"status={r.status_code} body={r.text!r}")
bad_sig = compute_signature("WRONG", "1729000000", "qa-nonce", echostr_cipher)
r = client.get("/api/v2/im/wecom/callback", params={
    "msg_signature": bad_sig, "timestamp": "1729000000",
    "nonce": "qa-nonce", "echostr": echostr_cipher})
check("企微 GET 伪造签名 403", r.status_code == 403, f"status={r.status_code}")

# 6) 企微 POST 消息回调（解密 → 编排 → 加密回包）
inner = (f"<xml><ToUserName><![CDATA[{APPID}]]></ToUserName>"
         "<FromUserName><![CDATA[zhangsan]]></FromUserName>"
         "<CreateTime>1729000000</CreateTime>"
         "<MsgType><![CDATA[text]]></MsgType>"
         "<Content><![CDATA[A型号在SMT-1停线，何时复线？]]></Content>"
         "<MsgId>6001</MsgId></xml>")
encrypt = encrypt_msg(AES_KEY, APPID, inner)
cb_sig = compute_signature(TOKEN, "1729000000", "qa-nonce", encrypt)
body_xml = f"<xml><Encrypt><![CDATA[{encrypt}]]></Encrypt></xml>"
r = client.post(
    f"/api/v2/im/wecom/callback?msg_signature={cb_sig}"
    f"&timestamp=1729000000&nonce=qa-nonce",
    content=body_xml.encode("utf-8"),
    headers={"Content-Type": "text/xml"})
check("企微 POST 回包 200", r.status_code == 200, f"status={r.status_code}")
try:
    root = ElementTree.fromstring(r.text)
    reply_encrypt = root.findtext("Encrypt")
    reply_sig = root.findtext("MsgSignature")
    ok_sig = compute_signature(TOKEN, root.findtext("TimeStamp"),
                               root.findtext("Nonce"),
                               reply_encrypt) == reply_sig
    plain = decrypt_msg(AES_KEY, APPID, reply_encrypt)
    check("回包签名自验证", ok_sig)
    check("回包解密含分析结论", "计划内暂停" in plain or "恢复" in plain,
          plain[:80].replace("\n", " "))
    check("回包含 HITL 待确认", "待人工确认" in plain)
except Exception as exc:  # noqa: BLE001
    check("回包解析", False, f"{type(exc).__name__}: {exc}")

# 7) 企微 POST 伪造签名 → 403
r = client.post(
    "/api/v2/im/wecom/callback?msg_signature=" + "0" * 40
    + "&timestamp=1729000000&nonce=qa-nonce",
    content=body_xml.encode("utf-8"),
    headers={"Content-Type": "text/xml"})
check("企微 POST 伪造签名 403", r.status_code == 403, f"status={r.status_code}")

print("=" * 60)
if failures:
    print(f"结果：{len(failures)} 项失败 -> {failures}")
    sys.exit(1)
print("结果：全部通过")
sys.exit(0)
