"""登录验证码（公网演示防刷）。

背景：平台通过 Cloudflare 隧道暴露公网（hulkai.cn），``/api/v2/auth/login``
原先只校验角色合法性、不校验任何凭证——拿到链接的脚本可以无限频次调用。
本模块给登录加两道闸：

1. **图形验证码**：``GET /api/v2/auth/captcha`` 签发一次性验证码（SVG 内联，
   无 Pillow/字体依赖）；登录必须携带正确的 ``captcha_id + captcha_code``，
   验证后立即作废（一次性），5 分钟过期——挡住脚本批量提交；
2. **签发限速**：按来源 IP 滑动窗口限速（默认每分钟 30 张），防验证码端点
   本身被刷爆内存/带宽。

诚实边界（与 auth.py 的免密演示边界同类，写明以免被误读）：
- 存储为进程内存字典，多 worker 部署（--workers >1）时验证码可能落在
  不同于签发实例的进程上导致校验失败——当前部署为单 uvicorn 进程，成立；
  若未来多 worker，需换 Redis 等共享存储。
- 验证码防的是「自动化批量刷」，不防定点人工攻击；正式生产须换 SSO。
"""
from __future__ import annotations

import secrets
import threading
import time
from collections import OrderedDict
from typing import Optional

__all__ = [
    "CaptchaExpiredError",
    "check_captcha_rate",
    "issue_captcha",
    "reset_captcha",
    "verify_captcha",
]

# 验证码字符集：去掉 0/O、1/I 等易混淆字符，降低人工输入挫败感。
_CAPTCHA_CHARS = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"
_CAPTCHA_LEN = 4

# 一次性存储：captcha_id -> (答案, 过期时间戳)。OrderedDict 便于淘汰最旧。
_TTL_S = 300          # 5 分钟有效
_MAX_ITEMS = 20000    # 容量上限（内存防线；正常流量远达不到）
_STORE: "OrderedDict[str, tuple[str, float]]" = OrderedDict()
_LOCK = threading.Lock()

# 签发限速：每 IP 每分钟 30 张（正常人工刷新远达不到；脚本刷量直接 429）。
_RATE_WINDOW_S = 60.0
_RATE_LIMIT = 30
_MAX_IPS = 10000      # IP 记录条数上限，防字典膨胀
_RATE: "OrderedDict[str, list[float]]" = OrderedDict()


class CaptchaExpiredError(Exception):
    """验证码会话不存在 / 已过期 / 已使用（由 verify_captcha 转换为失败原因）。"""


def issue_captcha() -> tuple[str, str]:
    """签发一张验证码。返回 ``(captcha_id, svg_text)``。

    SVG 为内联文本（浏览器 ``<img src="data:image/svg+xml,...">`` 直接消费），
    纯 Python 生成：字符随机旋转/偏移/颜色 + 干扰曲线 + 噪点，无第三方依赖。
    """
    answer = "".join(secrets.choice(_CAPTCHA_CHARS) for _ in range(_CAPTCHA_LEN))
    captcha_id = secrets.token_urlsafe(16)
    svg = _render_svg(answer)
    now = time.time()
    with _LOCK:
        _evict_expired_locked(now)
        while len(_STORE) >= _MAX_ITEMS:
            _STORE.popitem(last=False)   # 淘汰最早签发的一张
        _STORE[captcha_id] = (answer, now + _TTL_S)
    return captcha_id, svg


def verify_captcha(captcha_id: str, code: str) -> tuple[bool, str]:
    """校验验证码（**一次性**：无论对错，会话即作废——防同号重试爆破）。

    返回 ``(ok, message)``；失败时 message 为面向用户的中文提示，API 层原样透传。
    比对大小写不敏感（人工输入友好；自动化防刷语义不受影响）。
    """
    cid = str(captcha_id or "").strip()
    answer_code = str(code or "").strip().upper()
    if not cid or not answer_code:
        return False, "请输入验证码"
    with _LOCK:
        item = _STORE.pop(cid, None)   # 先消费：一次性语义
    if item is None:
        return False, "验证码已失效，请点击图片刷新后重试"
    answer, expire_at = item
    if time.time() > expire_at:
        return False, "验证码已过期，请点击图片刷新后重试"
    if answer.upper() != answer_code:
        return False, "验证码不正确，请重新输入"
    return True, "ok"


def check_captcha_rate(key: str) -> bool:
    """签发限速（滑动窗口）。``key`` 通常为来源 IP。返回 False = 超限（应 429）。"""
    now = time.time()
    k = str(key or "unknown")
    with _LOCK:
        window = _RATE.pop(k, [])
        window = [t for t in window if now - t < _RATE_WINDOW_S]
        if len(window) >= _RATE_LIMIT:
            _RATE[k] = window          # 超限也回写：继续累计，不重置窗口
            _evict_rate_locked()
            return False
        window.append(now)
        _RATE[k] = window
        _RATE.move_to_end(k)
        _evict_rate_locked()
        return True


def reset_captcha() -> None:
    """清空全部验证码会话与限速记录（仅测试使用）。"""
    with _LOCK:
        _STORE.clear()
        _RATE.clear()


def _evict_expired_locked(now: float) -> None:
    """清过期会话（调用方须持锁）。"""
    expired = [cid for cid, (_, exp) in _STORE.items() if exp <= now]
    for cid in expired:
        _STORE.pop(cid, None)


def _evict_rate_locked() -> None:
    """限速表容量防线（调用方须持锁）：超上限时淘汰最旧的 IP 窗口。"""
    while len(_RATE) > _MAX_IPS:
        _RATE.popitem(last=False)


def _render_svg(text: str) -> str:
    """渲染验证码 SVG（120x44，浅色底保证明暗两套主题下都可读）。"""
    colors = ["#1B66CC", "#2E7D32", "#B26A00", "#7B3FA0", "#B3413C", "#0E7490"]
    width, height = 120, 44
    parts: list[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}">',
        f'<rect width="{width}" height="{height}" fill="#F4F7FC"/>',
    ]
    # 干扰曲线 ×3：横穿的贝塞尔曲线，颜色/起伏随机
    for _ in range(3):
        c = secrets.choice(colors)
        y1 = secrets.randbelow(height)
        y2 = secrets.randbelow(height)
        y3 = secrets.randbelow(height)
        parts.append(
            f'<path d="M0,{y1} C {width // 3},{y2} {width * 2 // 3},{y3} {width},{y1}" '
            f'stroke="{c}" stroke-width="1.2" fill="none" opacity="0.45"/>')
    # 字符：随机字号 / 基线偏移 / 旋转 / 颜色，等距铺开
    slot = width // (_CAPTCHA_LEN + 1)
    for i, ch in enumerate(text):
        size = 26 + secrets.randbelow(7)
        x = slot * (i + 1) + secrets.randbelow(5) - 2
        y = height // 2 + 9 + secrets.randbelow(7) - 3
        angle = secrets.randbelow(51) - 25          # ±25°
        color = secrets.choice(colors)
        parts.append(
            f'<text x="{x}" y="{y}" font-size="{size}" fill="{color}" '
            f'font-family="Georgia, \'Times New Roman\', serif" font-weight="700" '
            f'transform="rotate({angle} {x} {y})" '
            f'text-anchor="middle">{ch}</text>')
    # 噪点 ×10
    for _ in range(10):
        cx, cy = secrets.randbelow(width), secrets.randbelow(height)
        r = 1 + secrets.randbelow(2)
        parts.append(
            f'<circle cx="{cx}" cy="{cy}" r="{r}" '
            f'fill="{secrets.choice(colors)}" opacity="0.35"/>')
    parts.append("</svg>")
    return "".join(parts)
