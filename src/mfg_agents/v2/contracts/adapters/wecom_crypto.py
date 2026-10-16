"""企业微信消息加解密（WXBizMsgCrypt 标准算法）。

协议要点（https://developer.work.weixin.qq.com/document/path/90968）：
1. 签名：对 [token, timestamp, nonce, encrypt/echostr] 字典序拼接后做 SHA1；
2. 密钥：Base64Decode(EncodingAESKey + "=") 得 32 字节，IV 取密钥前 16 字节；
3. 明文结构：random(16B) + msg_len(4B 网络字节序) + msg + appid；
4. 填充：PKCS#7，按 32 字节块补位（官方 SDK 行为，亦是 16 的倍数，兼容 AES 分组）；
5. 密文：AES-256-CBC 加密后 Base64。

本模块只做密码学原语，不依赖任何第三方库（AES 实现见 aes_cbc.py）。
"""
from __future__ import annotations

import base64
import hashlib
import os
import struct

from .aes_cbc import cbc_decrypt, cbc_encrypt


class WeComCryptoError(Exception):
    """企业微信加解密 / 验签失败。"""


def compute_signature(token: str, timestamp: str, nonce: str, data: str) -> str:
    """计算企业微信签名：四个参数字典序拼接后 SHA1。"""
    parts = sorted([token, timestamp, nonce, data])
    return hashlib.sha1("".join(parts).encode("utf-8")).hexdigest()


def pkcs7_pad(data: bytes, block_size: int = 32) -> bytes:
    """PKCS#7 补位到 block_size 的倍数（企微官方 SDK 使用 32）。"""
    pad_len = block_size - (len(data) % block_size)
    return data + bytes([pad_len]) * pad_len


def pkcs7_unpad(data: bytes) -> bytes:
    """去除 PKCS#7 补位；补位非法时抛出 WeComCryptoError。"""
    if not data:
        raise WeComCryptoError("待解密数据为空")
    pad_len = data[-1]
    if pad_len < 1 or pad_len > 32 or pad_len > len(data):
        raise WeComCryptoError(f"非法 PKCS#7 补位长度: {pad_len}")
    if data[-pad_len:] != bytes([pad_len]) * pad_len:
        raise WeComCryptoError("PKCS#7 补位内容不一致")
    return data[:-pad_len]


def aes_key_from_b64(aes_key: str) -> bytes:
    """EncodingAESKey → 32 字节 AES 密钥（Base64 可能缺 = 号）。"""
    padded = aes_key + "=" * ((4 - len(aes_key) % 4) % 4)
    try:
        key = base64.b64decode(padded)
    except Exception as exc:  # noqa: BLE001
        raise WeComCryptoError(f"EncodingAESKey Base64 解码失败: {exc}") from exc
    if len(key) != 32:
        raise WeComCryptoError(f"AES 密钥长度应为 32 字节，实际 {len(key)}")
    return key


def encrypt_msg(aes_key: str, appid: str, plaintext: str,
                random_prefix: bytes | None = None) -> str:
    """加密明文 → Base64 密文（random_prefix 可注入以构造确定性测试向量）。"""
    key = aes_key_from_b64(aes_key)
    prefix = random_prefix if random_prefix is not None else os.urandom(16)
    if len(prefix) != 16:
        raise WeComCryptoError("随机前缀必须为 16 字节")
    msg = plaintext.encode("utf-8")
    # random(16) + len(4B 网络序) + msg + appid
    buf = prefix + struct.pack(">I", len(msg)) + msg + appid.encode("utf-8")
    ciphertext = cbc_encrypt(key, key[:16], pkcs7_pad(buf))
    return base64.b64encode(ciphertext).decode("ascii")


def decrypt_msg(aes_key: str, appid: str, encrypt_b64: str) -> str:
    """解密密文 → 明文，并校验 appid 归属（防跨应用注入）。"""
    key = aes_key_from_b64(aes_key)
    try:
        ciphertext = base64.b64decode(encrypt_b64)
    except Exception as exc:  # noqa: BLE001
        raise WeComCryptoError(f"密文 Base64 解码失败: {exc}") from exc
    try:
        plain = pkcs7_unpad(cbc_decrypt(key, key[:16], ciphertext))
    except WeComCryptoError:
        raise
    except ValueError as exc:
        # 截断/篡改导致长度非 16 倍数等原生 ValueError → 统一契约异常（OBS-2）
        raise WeComCryptoError(f"密文解密失败: {exc}") from exc
    if len(plain) < 20:
        raise WeComCryptoError("解密后明文过短，结构非法")
    msg_len = struct.unpack(">I", plain[16:20])[0]
    msg = plain[20:20 + msg_len]
    tail_appid = plain[20 + msg_len:].decode("utf-8", errors="replace")
    if tail_appid != appid:
        raise WeComCryptoError(
            f"appid 校验失败（收到 {tail_appid!r}，期望 {appid!r}），拒绝处理")
    if len(msg) != msg_len:
        raise WeComCryptoError("明文长度字段与实际内容不一致")
    try:
        return msg.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise WeComCryptoError(
            f"解密结果不是合法 UTF-8（密文可能被篡改）: {exc}") from exc
