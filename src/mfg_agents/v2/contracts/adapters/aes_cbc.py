"""纯标准库 AES-CBC 实现（FIPS-197）。

为什么自实现：企业微信消息加解密（WXBizMsgCrypt）需要 AES-256-CBC，
但项目约束"不新增重依赖"（pycryptodome/cryptography 未安装），
故用 Python 标准库实现并通过 NIST SP800-38A 已知向量单测校验。

支持 AES-128/192/256；仅实现加解密所需原语，追求可读与可验证，
不追求极致性能（消息体长度为百字节级，无性能瓶颈）。
"""
from __future__ import annotations

# ---------- GF(2^8) 基础运算 ----------


def _gmul(a: int, b: int) -> int:
    """GF(2^8) 乘法（不可约多项式 0x11B）。"""
    p = 0
    for _ in range(8):
        if b & 1:
            p ^= a
        hi = a & 0x80
        a = (a << 1) & 0xFF
        if hi:
            a ^= 0x1B
        b >>= 1
    return p


def _rotl8(x: int, shift: int) -> int:
    """8 位循环左移。"""
    return ((x << shift) | (x >> (8 - shift))) & 0xFF


def _build_sbox() -> list[int]:
    """由数学定义生成 AES S-Box（GF(2^8) 逆元 + 仿射变换），避免硬编码表笔误。"""
    inv = [0] * 256
    for a in range(1, 256):
        for b in range(1, 256):
            if _gmul(a, b) == 1:
                inv[a] = b
                break
    sbox = [0] * 256
    for a in range(256):
        x = inv[a]
        # 仿射变换：b[i] ^ b[i+4] ^ b[i+5] ^ b[i+6] ^ b[i+7] ^ c[i]，c = 0x63
        s = x ^ _rotl8(x, 1) ^ _rotl8(x, 2) ^ _rotl8(x, 3) ^ _rotl8(x, 4) ^ 0x63
        sbox[a] = s & 0xFF
    return sbox


_SBOX = _build_sbox()
_INV_SBOX = [0] * 256
for _i, _v in enumerate(_SBOX):
    _INV_SBOX[_v] = _i

_RCON = [0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80, 0x1B, 0x36, 0x6C, 0xD8, 0xAB, 0x4D]


class AESCipher:
    """AES 分组密码（FIPS-197），支持 CBC 模式。

    状态表示：16 字节列表，``s[4c + r] = state[r][c]``（列优先），
    与 FIPS-197 的 ``state[r][c] = in[r + 4c]`` 一致。
    """

    def __init__(self, key: bytes) -> None:
        if len(key) not in (16, 24, 32):
            raise ValueError(f"AES 密钥长度必须为 16/24/32 字节，当前 {len(key)}")
        self._nk = len(key) // 4
        self._nr = self._nk + 6
        self._round_keys = self._expand_key(key)

    # ---------- 密钥扩展 ----------

    def _expand_key(self, key: bytes) -> list[int]:
        words = [int.from_bytes(key[4 * i:4 * i + 4], "big") for i in range(self._nk)]
        for i in range(self._nk, 4 * (self._nr + 1)):
            temp = words[i - 1]
            if i % self._nk == 0:
                # RotWord + SubWord + Rcon
                temp = ((temp << 8) | (temp >> 24)) & 0xFFFFFFFF
                temp = ((_SBOX[(temp >> 24) & 0xFF] << 24)
                        | (_SBOX[(temp >> 16) & 0xFF] << 16)
                        | (_SBOX[(temp >> 8) & 0xFF] << 8)
                        | _SBOX[temp & 0xFF])
                temp ^= _RCON[i // self._nk - 1] << 24
            elif self._nk > 6 and i % self._nk == 4:
                temp = ((_SBOX[(temp >> 24) & 0xFF] << 24)
                        | (_SBOX[(temp >> 16) & 0xFF] << 16)
                        | (_SBOX[(temp >> 8) & 0xFF] << 8)
                        | _SBOX[temp & 0xFF])
            words.append(words[i - self._nk] ^ temp)
        return words

    # ---------- 状态变换原语 ----------

    def _add_round_key(self, s: list[int], rnd: int) -> None:
        for c in range(4):
            w = self._round_keys[rnd * 4 + c]
            s[4 * c + 0] ^= (w >> 24) & 0xFF
            s[4 * c + 1] ^= (w >> 16) & 0xFF
            s[4 * c + 2] ^= (w >> 8) & 0xFF
            s[4 * c + 3] ^= w & 0xFF

    @staticmethod
    def _sub_bytes(s: list[int]) -> None:
        for i in range(16):
            s[i] = _SBOX[s[i]]

    @staticmethod
    def _inv_sub_bytes(s: list[int]) -> None:
        for i in range(16):
            s[i] = _INV_SBOX[s[i]]

    @staticmethod
    def _shift_rows(s: list[int]) -> None:
        out = list(s)
        for r in range(4):
            for c in range(4):
                out[4 * c + r] = s[4 * ((c + r) % 4) + r]
        s[:] = out

    @staticmethod
    def _inv_shift_rows(s: list[int]) -> None:
        out = list(s)
        for r in range(4):
            for c in range(4):
                out[4 * c + r] = s[4 * ((c - r) % 4) + r]
        s[:] = out

    @staticmethod
    def _mix_columns(s: list[int]) -> None:
        for c in range(4):
            t0, t1, t2, t3 = s[4 * c:4 * c + 4]
            s[4 * c + 0] = _gmul(t0, 2) ^ _gmul(t1, 3) ^ t2 ^ t3
            s[4 * c + 1] = t0 ^ _gmul(t1, 2) ^ _gmul(t2, 3) ^ t3
            s[4 * c + 2] = t0 ^ t1 ^ _gmul(t2, 2) ^ _gmul(t3, 3)
            s[4 * c + 3] = _gmul(t0, 3) ^ t1 ^ t2 ^ _gmul(t3, 2)

    @staticmethod
    def _inv_mix_columns(s: list[int]) -> None:
        for c in range(4):
            t0, t1, t2, t3 = s[4 * c:4 * c + 4]
            s[4 * c + 0] = _gmul(t0, 14) ^ _gmul(t1, 11) ^ _gmul(t2, 13) ^ _gmul(t3, 9)
            s[4 * c + 1] = _gmul(t0, 9) ^ _gmul(t1, 14) ^ _gmul(t2, 11) ^ _gmul(t3, 13)
            s[4 * c + 2] = _gmul(t0, 13) ^ _gmul(t1, 9) ^ _gmul(t2, 14) ^ _gmul(t3, 11)
            s[4 * c + 3] = _gmul(t0, 11) ^ _gmul(t1, 13) ^ _gmul(t2, 9) ^ _gmul(t3, 14)

    # ---------- 分组加解密 ----------

    def encrypt_block(self, block: bytes) -> bytes:
        if len(block) != 16:
            raise ValueError("分组长度必须为 16 字节")
        s = list(block)
        self._add_round_key(s, 0)
        for rnd in range(1, self._nr):
            self._sub_bytes(s)
            self._shift_rows(s)
            self._mix_columns(s)
            self._add_round_key(s, rnd)
        self._sub_bytes(s)
        self._shift_rows(s)
        self._add_round_key(s, self._nr)
        return bytes(s)

    def decrypt_block(self, block: bytes) -> bytes:
        if len(block) != 16:
            raise ValueError("分组长度必须为 16 字节")
        s = list(block)
        self._add_round_key(s, self._nr)
        for rnd in range(self._nr - 1, 0, -1):
            self._inv_shift_rows(s)
            self._inv_sub_bytes(s)
            self._add_round_key(s, rnd)
            self._inv_mix_columns(s)
        self._inv_shift_rows(s)
        self._inv_sub_bytes(s)
        self._add_round_key(s, 0)
        return bytes(s)


def cbc_encrypt(key: bytes, iv: bytes, plaintext: bytes) -> bytes:
    """AES-CBC 加密（plaintext 长度必须为 16 的倍数）。"""
    if len(plaintext) % 16 != 0:
        raise ValueError("CBC 明文长度必须为 16 的倍数")
    cipher = AESCipher(key)
    prev = iv
    out = bytearray()
    for i in range(0, len(plaintext), 16):
        block = bytes(a ^ b for a, b in zip(plaintext[i:i + 16], prev))
        enc = cipher.encrypt_block(block)
        out += enc
        prev = enc
    return bytes(out)


def cbc_decrypt(key: bytes, iv: bytes, ciphertext: bytes) -> bytes:
    """AES-CBC 解密（ciphertext 长度必须为 16 的倍数）。"""
    if len(ciphertext) % 16 != 0:
        raise ValueError("CBC 密文长度必须为 16 的倍数")
    cipher = AESCipher(key)
    prev = iv
    out = bytearray()
    for i in range(0, len(ciphertext), 16):
        block = ciphertext[i:i + 16]
        dec = cipher.decrypt_block(block)
        out += bytes(a ^ b for a, b in zip(dec, prev))
        prev = block
    return bytes(out)
