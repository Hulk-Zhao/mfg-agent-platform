"""登录守卫契约（公网防刷）：验证码 + 账号口令 + 限速 + 前端接线。

背景：平台经 Cloudflare 隧道暴露公网，``/api/v2/auth/login`` 原先只校验角色、
不校验任何凭证——拿到链接的脚本可无限频次调用。守卫（``MFG_LOGIN_GUARD``，
默认开）补上三道闸：

1. **图形验证码**（一次性，5 分钟 TTL）：``GET /api/v2/auth/captcha`` 签发 →
   登录必须携带正确的 ``captcha_id + captcha_code``；
2. **账号口令**：默认 admin/admin（``MFG_ADMIN_USER`` / ``MFG_ADMIN_PASS`` 覆盖）；
3. **签发限速**：每 IP 每分钟 30 张，超限 429。

守三件事：
1. 守卫开启时：凭证三重校验逐项 fail-closed（缺码/错码/过期/一次性/错密码），
   正确凭证成功签发令牌；
2. 守卫关闭时（tests/conftest.py 统一注入 ``MFG_LOGIN_GUARD=0``）：行为与
   既有免密演示完全一致——2100+ 存量用例的基线不被本特性污染；
3. 前端登录页真实接线：凭证区（预填 admin/admin）+ 验证码图 + captcha() API。
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from mfg_agents.v2.demo.api import app
from mfg_agents.v2.services.captcha import (
    check_captcha_rate,
    reset_captcha,
    verify_captcha,
)

UI = Path(__file__).resolve().parents[2] / "src" / "mfg_agents" / "v2" / "demo" / "ui"


@pytest.fixture(autouse=True)
def _clean_captcha_store():
    """每个用例前后清空验证码存储与限速表：用例间互不污染。"""
    reset_captcha()
    yield
    reset_captcha()


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture()
def guard_on(monkeypatch):
    """开启登录守卫（conftest 默认关）：逐用例显式开启。"""
    monkeypatch.setenv("MFG_LOGIN_GUARD", "1")
    yield


def _captcha(client: TestClient) -> dict:
    resp = client.get("/api/v2/auth/captcha")
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


def _login(client: TestClient, **overrides) -> object:
    """用一张**真答案**验证码走完整登录（overrides 覆盖任意载荷字段）。

    答案从服务端存储侧读取（HTTP 应答不回明文）：密码类用例需要「码对、密错」
    才能独立测到账密校验分支。
    """
    cap = _captcha(client)
    from mfg_agents.v2.services import captcha as cap_mod
    answer = next(iter(cap_mod._STORE.values()))[0]
    payload = {"roles": ["qa"], "username": "admin", "password": "admin",
               "captcha_id": cap["captcha_id"], "captcha_code": answer,
               **overrides}
    return client.post("/api/v2/auth/login", json=payload)


# ---------- 守卫关闭：免密演示基线不变 ----------


def test_guard_off_keeps_legacy_passwordless_login(client):
    """conftest 注入 MFG_LOGIN_GUARD=0：不带任何凭证直接登录成功（存量基线）。"""
    assert client.post("/api/v2/auth/login", json={"roles": ["qa"]}).status_code == 200


def test_guard_off_reports_guard_false(client):
    cap = _captcha(client)
    assert cap["guard"] is False
    assert "<svg" in cap["svg"]


# ---------- 守卫开启：验证码逐项 fail-closed ----------


def test_guard_on_rejects_missing_captcha(client, guard_on):
    resp = client.post("/api/v2/auth/login", json={"roles": ["qa"]})
    assert resp.status_code == 400
    assert "验证码" in resp.json()["detail"]


def test_guard_on_rejects_wrong_captcha(client, guard_on):
    resp = _login(client, captcha_code="XXXX")
    assert resp.status_code == 400
    assert "验证码" in resp.json()["detail"]


def test_guard_on_captcha_is_case_insensitive_and_one_shot(client, guard_on):
    """签发应答里没有明文（防脚本直读），正确码（忽略大小写）通过；同码第二次失败。"""
    cap = _captcha(client)
    assert "answer" not in cap
    from mfg_agents.v2.services import captcha as cap_mod
    # 从存储侧取答案（服务端内部行为）；确认一次性 + 大小写不敏感
    answer = next(iter(cap_mod._STORE.values()))[0]
    ok, _ = verify_captcha(cap["captcha_id"], answer.lower())
    assert ok
    ok2, msg = verify_captcha(cap["captcha_id"], answer)   # 同号重放
    assert not ok2 and "失效" in msg


def test_guard_on_rejects_wrong_password(client, guard_on):
    resp = _login(client, password="wrong-pass")
    assert resp.status_code == 400
    assert "账号或密码错误" in resp.json()["detail"]


def test_guard_on_rejects_wrong_username(client, guard_on):
    resp = _login(client, username="root")
    assert resp.status_code == 400
    assert "账号或密码错误" in resp.json()["detail"]


def test_guard_on_valid_credentials_issue_token(client, guard_on):
    resp = _login(client)
    assert resp.status_code == 200, resp.text
    body = resp.json()["data"]
    assert body["token"] and body["expires_in"] > 0
    assert body["user_ctx"]["roles"]


def test_guard_on_custom_admin_pass(monkeypatch, client, guard_on):
    monkeypatch.setenv("MFG_ADMIN_PASS", "s3cret!")
    assert _login(client, password="admin").status_code == 400
    assert _login(client, password="s3cret!").status_code == 200


def test_guard_on_captcha_consumed_even_when_password_wrong(client, guard_on):
    """密码错误时验证码也已消费：同码重试必须失败（防爆破语义）。"""
    cap = _captcha(client)
    from mfg_agents.v2.services import captcha as cap_mod
    answer = next(iter(cap_mod._STORE.values()))[0]
    body = {"roles": ["qa"], "username": "admin", "password": "bad",
            "captcha_id": cap["captcha_id"], "captcha_code": answer}
    assert client.post("/api/v2/auth/login", json=body).status_code == 400
    body["password"] = "admin"
    resp = client.post("/api/v2/auth/login", json=body)
    assert resp.status_code == 400
    assert "失效" in resp.json()["detail"]


def test_guard_on_valid_session_relogin_skips_captcha(client, guard_on):
    """守卫开启 + 携带**有效**令牌补签/换岗：免验证码直接换发新令牌。

    场景：应用内切换岗位（setRole 清空 state.token 后静默补签，旧令牌经
    Authorization 头透传）。若无此豁免，验证码已消费 → 400 → 用户被无故
    弹回登录页（2026-10-23 用户反馈的切换角色 BUG）。
    """
    first = _login(client)
    assert first.status_code == 200
    old_token = first.json()["data"]["token"]
    resp = client.post("/api/v2/auth/login",
                       json={"roles": ["gm"], "captcha_id": "", "captcha_code": ""},
                       headers={"Authorization": f"Bearer {old_token}"})
    assert resp.status_code == 200, resp.text
    new_token = resp.json()["data"]["token"]
    assert new_token and new_token != old_token      # 确实换发了新令牌


def test_guard_on_invalid_stale_token_still_requires_captcha(client, guard_on):
    """令牌缺失/非法/过期一律 fail-closed：仍要求完整凭证（防令牌伪造绕过守卫）。"""
    for headers in ({}, {"Authorization": "Bearer forged.token.here"}):
        resp = client.post("/api/v2/auth/login", json={"roles": ["qa"]}, headers=headers)
        assert resp.status_code == 400
        assert "验证码" in resp.json()["detail"]


# ---------- 验证码服务内核 ----------


def test_captcha_issue_returns_unique_ids_and_svg(client):
    ids = {_captcha(client)["captcha_id"] for _ in range(5)}
    assert len(ids) == 5
    svg = _captcha(client)["svg"]
    assert "<svg" in svg and "</svg>" in svg
    assert svg.count("<text") == 4          # 4 位字符
    assert "#F4F7FC" in svg                 # 浅底：明暗主题下都可读


def test_captcha_rate_limit_per_ip():
    reset_captcha()
    granted = sum(check_captcha_rate("1.2.3.4") for _ in range(40))
    assert granted == 30                    # 每分钟 30 张，第 31 张起 429
    assert check_captcha_rate("5.6.7.8")    # 其他 IP 不受影响


def test_captcha_rejects_unknown_session():
    ok, msg = verify_captcha("no-such-id", "ABCD")
    assert not ok and "失效" in msg


# ---------- 前端接线守卫 ----------


def test_frontend_captcha_wiring():
    """登录页凭证区 + 验证码交互 + API 方法真实存在（防接线回归）。"""
    app_js = (UI / "app.js").read_text(encoding="utf-8")
    css = (UI / "app.css").read_text(encoding="utf-8")
    # 凭证区：账号/密码预填 admin + 验证码输入与图片
    assert 'id="login-cred"' in app_js
    assert 'id="login-user"' in app_js and 'value="admin"' in app_js
    assert 'id="login-pass"' in app_js
    assert 'id="login-captcha-input"' in app_js and 'id="login-captcha-img"' in app_js
    # 交互：点图刷新、回车提交、守卫拒绝(400/429)回登录页
    assert "fetchCaptcha" in app_js and "collectCreds" in app_js
    assert re.search(r"login-captcha-img\"\);?\s*\n?\s*if", app_js) or \
        "capImg.addEventListener" in app_js
    assert "captcha_code" in app_js and "captcha_id" in app_js
    # API 层与 CSS
    assert "captcha()" in app_js
    assert ".login-cred" in css and ".cred-captcha-img" in css
