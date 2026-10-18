"""身份令牌与岗位门禁（F2：访问令牌 + 登录态 + 按角色收敛 HITL 匿名写接口）。

问题（竞品调研报告 F2）：门户原为「21 岗位免密登录」——任何拿到链接的人都能对
HITL 写接口（``select-action`` / ``cosign`` / ``approve`` / ``reject``）直接提交
``user_ctx``；缺省还会落到 ``DEFAULT_USER_CTX``（manufacturing + coordinator），
治理上等同「链接即权限」，会签的一人一签 SoD 也随之失去证明力。

本模块只做三件事，边界清晰，**不碰引擎里已有的判权逻辑**：

1. **签发令牌**：登录时把「岗位 → 权威角色集合」写进 HMAC-SHA256 签名载荷
   （``sub`` / ``name`` / ``dept`` / ``roles`` / ``iat`` / ``exp``），令牌不可伪造；
2. **校验令牌**：``verify_token`` 验签 + 校验过期 + 校验含角色，失败一律
   ``AuthError``（fail-closed，由 API 层转 401）；
3. **角色归一化**：登录请求里的岗位写法一律经 ``PermissionRegistry`` 归一化，
   全部未识别即拒绝——令牌里的角色因此在签发时即被服务端裁决过。

诚实边界（与 PRD-V3 记录的已知缺陷同类，写明以免被误读为「已完成 SSO」）：
- 演示环境仍是**免密码**：登录只在服务端校验「岗位是否落在 32 角色权限目录内」，
  不校验口令。生产必须替换为 SSO/SAML/OIDC，并把员工花名册搬到服务端。
- 令牌把 ``user_id`` 固定在登录所选身份上，因此一人一签的 actor 判定在同一会话内
  不可随意变更（此前每次写请求都能改 ``user_ctx.user_id``）。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import secrets
import time
from typing import Any, Iterable, Optional

from ...common.config import settings
from ..contracts.permissions import get_permission_registry

logger = logging.getLogger(__name__)

__all__ = [
    "AuthError",
    "IdentityCodec",
    "get_codec",
    "issue_token",
    "reset_codec",
    "resolve_login_roles",
    "verify_token",
]


class AuthError(Exception):
    """令牌缺失 / 非法 / 过期（fail-closed：由 API 层统一转 401）。"""


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64d(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


class IdentityCodec:
    """令牌编解码器（HMAC-SHA256 签名 + 过期校验）。

    载荷用 ``base64url(json) . base64url(hmac)`` 的紧凑格式（非 JWT，避免引入
    新依赖，也不承诺 JWT 生态兼容）。签名覆盖整段载荷，改动任一字段即验签失败。
    """

    def __init__(self, secret: str, ttl_s: int = 8 * 3600) -> None:
        if not secret:
            raise AuthError("令牌密钥不能为空")
        self._secret = str(secret).encode("utf-8")
        self.ttl_s = max(60, int(ttl_s))

    def _sign(self, body: str) -> str:
        mac = hmac.new(self._secret, body.encode("ascii"), hashlib.sha256).digest()
        return _b64e(mac)

    def issue(self, *, user_id: str, name: str = "", dept: str = "",
              roles: Iterable[str] = (),
              tenant: Optional[str] = None,
              plant: Optional[str] = None,
              line_ids: Optional[Iterable[str]] = None,
              project_ids: Optional[Iterable[str]] = None,
              customer_ids: Optional[Iterable[str]] = None,
              supplier_ids: Optional[Iterable[str]] = None,
              product_families: Optional[Iterable[str]] = None,
              max_classification_rank: Optional[int] = None,
              employment_type: Optional[str] = None,
              token_version: Optional[int] = None,
              principal_version: Optional[int] = None
              ) -> tuple[str, dict[str, Any]]:
        """签发令牌，返回 ``(token, payload)``。角色为空即拒绝（不给匿名令牌）。

        扩展维度（案例助手所需的 11 维权属主体，见架构 §4.5 / §8.1）**全部是可选
        关键字参数**：不传就不写进载荷，因此**既有 ``issue()`` 调用行为逐字节不变**
        （老令牌仍可签发、仍可验签），既有 625 条测试的行为不受影响。

        新增维度只对显式传参的调用方生效——这也是"渐进式扩展、不做静默兼容放行"
        的实现方式：案例域的 ``PrincipalResolver`` 会拒绝 ``tv < 2`` 的老令牌，
        而不是让老令牌"部分可用"。
        """
        role_list = [str(r) for r in (roles or []) if str(r)]
        if not role_list:
            raise AuthError("岗位未识别：无可签发的角色")
        now = int(time.time())
        payload: dict[str, Any] = {
            "sub": str(user_id or ""), "name": str(name or ""),
            "dept": str(dept or ""), "roles": role_list,
            "iat": now, "exp": now + self.ttl_s,
        }
        # ---- 案例域扩展载荷（仅在显式传参时写入，保持既有行为不变）----
        optional_scalars: dict[str, Any] = {
            "tenant": tenant, "plant": plant,
            "max_classification_rank": max_classification_rank,
            "employment_type": employment_type,
            "tv": token_version, "pv": principal_version,
        }
        for key, value in optional_scalars.items():
            if value is not None:
                payload[key] = value
        optional_lists: dict[str, Any] = {
            "line_ids": line_ids, "project_ids": project_ids,
            "customer_ids": customer_ids, "supplier_ids": supplier_ids,
            "product_families": product_families,
        }
        for key, value in optional_lists.items():
            if value is not None:
                payload[key] = [str(v) for v in value if str(v)]
        body = _b64e(json.dumps(payload, ensure_ascii=False,
                                separators=(",", ":"), sort_keys=True).encode("utf-8"))
        return f"{body}.{self._sign(body)}", payload

    def verify(self, token: str) -> dict[str, Any]:
        """验签 + 校验过期 → 载荷；任何不合法都抛 ``AuthError``。"""
        raw = str(token or "").strip()
        if not raw or "." not in raw:
            raise AuthError("缺少访问令牌")
        body, _, sig = raw.rpartition(".")
        if not body or not sig:
            raise AuthError("访问令牌格式不正确")
        if not hmac.compare_digest(self._sign(body), sig):
            raise AuthError("访问令牌签名无效")
        try:
            payload = json.loads(_b64d(body).decode("utf-8"))
        except Exception as exc:  # noqa: BLE001
            raise AuthError("访问令牌内容无法解析") from exc
        if not isinstance(payload, dict):
            raise AuthError("访问令牌内容无法解析")
        if int(payload.get("exp") or 0) < int(time.time()):
            raise AuthError("访问令牌已过期，请重新选择岗位")
        if not payload.get("roles"):
            raise AuthError("访问令牌不含有效岗位")
        return payload


_CODEC: Optional[IdentityCodec] = None


def get_codec() -> IdentityCodec:
    """编解码器单例。

    ``AUTH_SECRET`` 未配置时**生成进程级随机密钥**并告警——宁可让令牌在进程重启后
    失效，也不内置一个可被公开猜测的默认密钥（那等于新增一个后门）。
    """
    global _CODEC
    if _CODEC is None:
        secret = str(settings.AUTH_SECRET or "")
        if not secret:
            secret = secrets.token_urlsafe(32)
            logger.warning(
                "[auth] 未配置 AUTH_SECRET，已生成进程级随机密钥；"
                "进程重启后既有访问令牌全部失效。生产/对外演示必须显式配置。")
        _CODEC = IdentityCodec(secret, ttl_s=settings.AUTH_TOKEN_TTL_S)
    return _CODEC


def reset_codec() -> None:
    """清空单例（测试注入自定义密钥/明文比对用）。"""
    global _CODEC
    _CODEC = None


def resolve_login_roles(names: Optional[Iterable[Any]]) -> tuple[str, ...]:
    """登录请求的岗位写法 → **权限目录内的**角色 key 元组（fail-closed）。

    只接受 32 角色权限目录能识别的写法（key / 中文名 / aliases）；全部未识别返回
    空元组，由调用方拒绝登录。这一步是「令牌里的角色可信」的前提：角色在签发时
    已由服务端归一化，客户端此后无法自行发明角色。
    """
    return get_permission_registry().normalize_roles(names)


def issue_token(*, user_id: str, name: str = "", dept: str = "",
                roles: Iterable[str] = (), **extra: Any) -> tuple[str, dict[str, Any]]:
    """便捷入口：用单例编解码器签发令牌。

    ``**extra`` 透传给 :meth:`IdentityCodec.issue` 的可选维度
    （``tenant`` / ``plant`` / ``tv`` / ``pv`` / ``max_classification_rank`` …）。
    不传时行为与历史版本完全一致。
    """
    return get_codec().issue(user_id=user_id, name=name, dept=dept,
                             roles=roles, **extra)


def verify_token(token: str) -> dict[str, Any]:
    """便捷入口：用单例编解码器校验令牌。"""
    return get_codec().verify(token)
