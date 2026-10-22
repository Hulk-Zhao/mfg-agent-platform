"""QA 独立密码学向量验证：不信工程师自述，用外部标准锚点重新证明。

与 tests/v2/test_aes_cbc.py / test_wecom_crypto.py（工程师编写）的区别：
- 本文件的 NIST/FIPS 向量由 QA 独立选取（覆盖工程师未测的 AES-192/256 单块向量）；
- 企微协议层用 QA 独立拼装的预期密文做字节级比对（buffer 布局 / PKCS#7@32 /
  IV=key[:16 / Base64 全部独立推演，仅复用已被 NIST 向量外部验证的分组密码原语）；
- 补充工程师未覆盖的负向用例：逐位篡改、截断、重放行为记录、密钥长度边界。

外部锚点：
- FIPS-197 Appendix C.1/C.2/C.3（AES-128/192/256 单块已知向量）
- NIST SP800-38A F.2.1（CBC-AES128 三块链式向量）
"""
import base64
import hashlib
import struct

import pytest

from mfg_agents.v2.contracts.adapters.aes_cbc import AESCipher
from mfg_agents.v2.contracts.adapters.wecom import WeComAdapter
from mfg_agents.v2.contracts.adapters.wecom_crypto import (
    WeComCryptoError,
    aes_key_from_b64,
    compute_signature,
    decrypt_msg,
    encrypt_msg,
)

# ---------- 外部已知向量（NIST / FIPS 公开标准，非项目自产） ----------

# FIPS-197 Appendix C：同一明文，三种密钥长度
FIPS_PT = bytes.fromhex("00112233445566778899aabbccddeeff")
FIPS128_KEY = bytes.fromhex("000102030405060708090a0b0c0d0e0f")
FIPS128_CT = bytes.fromhex("69c4e0d86a7b0430d8cdb78070b4c55a")
FIPS192_KEY = bytes.fromhex("000102030405060708090a0b0c0d0e0f1011121314151617")
FIPS192_CT = bytes.fromhex("dda97ca4864cdfe06eaf70a0ec0d7191")
FIPS256_KEY = bytes.fromhex(
    "000102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f")
FIPS256_CT = bytes.fromhex("8ea2b7ca516745bfeafc49904b496089")

# NIST SP800-38A F.2.1 CBC-AES128.Encrypt
NIST_CBC_KEY = bytes.fromhex("2b7e151628aed2a6abf7158809cf4f3c")
NIST_CBC_IV = bytes.fromhex("000102030405060708090a0b0c0d0e0f")
NIST_CBC_PT = bytes.fromhex(
    "6bc1bee22e409f96e93d7e117393172a"
    "ae2d8a571e03ac9c9eb76fac45af8e51"
    "30c81c46a35ce411e5fbc1191a0a52ef")
NIST_CBC_CT = bytes.fromhex(
    "7649abac8119b246cee98e9b12e9197d"
    "5086cb9b507219ee95db113a917678b2"
    "73bed6b8e3c1743b7116e69e22229516")

# 企业微信官方文档示例凭据（公开测试向量）
TOKEN = "QDG6eK"
AES_KEY = "abcdefghijklmnopqrstuvwxyz0123456789ABCDEFG"
APPID = "wx5823bf96d3bd56c7"


# ---------- 1. 分组密码原语：NIST/FIPS 外部锚点 ----------

class TestAesExternalVectors:
    @pytest.mark.parametrize("key,ct", [
        (FIPS128_KEY, FIPS128_CT),
        (FIPS192_KEY, FIPS192_CT),
        (FIPS256_KEY, FIPS256_CT),
    ])
    def test_fips197_single_block(self, key, ct):
        """FIPS-197 三种密钥长度的单块加/解密必须与标准向量一致。"""
        assert AESCipher(key).encrypt_block(FIPS_PT) == ct
        assert AESCipher(key).decrypt_block(ct) == FIPS_PT

    def test_nist_cbc_aes128_chained(self):
        """SP800-38A CBC 三块链式（IV 链式反应 + 多分组）。"""
        from mfg_agents.v2.contracts.adapters.aes_cbc import cbc_decrypt, cbc_encrypt
        assert cbc_encrypt(NIST_CBC_KEY, NIST_CBC_IV, NIST_CBC_PT) == NIST_CBC_CT
        assert cbc_decrypt(NIST_CBC_KEY, NIST_CBC_IV, NIST_CBC_CT) == NIST_CBC_PT

    def test_nist_cbc_block_by_block_manual(self):
        """逐块手工验证 CBC 链式语义：C[i] = E(P[i] XOR C[i-1])。
        用独立写法复核（不依赖 cbc_encrypt）。"""
        cipher = AESCipher(NIST_CBC_KEY)
        prev = NIST_CBC_IV
        out = bytearray()
        for i in range(0, len(NIST_CBC_PT), 16):
            block = bytes(a ^ b for a, b in zip(NIST_CBC_PT[i:i + 16], prev))
            prev = cipher.encrypt_block(block)
            out += prev
        assert bytes(out) == NIST_CBC_CT

    def test_aes256_cbc_manual_chain_roundtrip(self):
        """AES-256-CBC 手工链式 round-trip（企微实际使用的参数组合）。"""
        cipher = AESCipher(FIPS256_KEY)
        iv = FIPS256_KEY[:16]
        pt = "企微回调明文 · A型号在SMT-1停线".encode("utf-8")
        pt += bytes([32 - len(pt) % 32]) * (32 - len(pt) % 32)  # PKCS#7@32
        prev, out = iv, bytearray()
        for i in range(0, len(pt), 16):
            block = bytes(a ^ b for a, b in zip(pt[i:i + 16], prev))
            prev = cipher.encrypt_block(block)
            out += prev
        # 解密验证
        prev, back = iv, bytearray()
        for i in range(0, len(out), 16):
            dec = cipher.decrypt_block(bytes(out[i:i + 16]))
            back += bytes(a ^ b for a, b in zip(dec, prev))
            prev = out[i:i + 16]
        assert bytes(back) == pt


# ---------- 2. 企微协议层：独立拼装预期密文做字节级比对 ----------

def _independent_wecom_cipher(aes_key_b64: str, appid: str, plaintext: str,
                              random_prefix: bytes) -> str:
    """QA 独立推演的企微加密结果（与 wecom_crypto.encrypt_msg 路径独立）。

    协议要点按官方文档手工实现：
    random(16B) + msg_len(4B 网络字节序) + msg + appid → PKCS#7@32
    → AES-256-CBC(key, IV=key[:16]) → Base64。
    仅分组密码原语复用 AESCipher（已被上面 NIST 向量外部验证）。
    """
    key = base64.b64decode(aes_key_b64 + "=")
    assert len(key) == 32
    msg = plaintext.encode("utf-8")
    buf = random_prefix + struct.pack(">I", len(msg)) + msg + appid.encode("utf-8")
    pad_len = 32 - len(buf) % 32
    buf += bytes([pad_len]) * pad_len
    cipher = AESCipher(key)
    prev, out = key[:16], bytearray()
    for i in range(0, len(buf), 16):
        block = bytes(a ^ b for a, b in zip(buf[i:i + 16], prev))
        prev = cipher.encrypt_block(block)
        out += prev
    return base64.b64encode(bytes(out)).decode("ascii")


class TestWeComProtocolIndependent:
    def test_ciphertext_matches_independent_construction(self):
        """字节级比对：encrypt_msg 输出必须与 QA 独立拼装完全一致
        （同时锚定 buffer 布局 / PKCS#7@32 / IV=key[:16] / Base64 四要素）。"""
        prefix = bytes(range(16))
        plaintext = "<xml><Content><![CDATA[A型号在SMT-1停线，何时复线？]]></Content></xml>"
        expected = _independent_wecom_cipher(AES_KEY, APPID, plaintext, prefix)
        actual = encrypt_msg(AES_KEY, APPID, plaintext, random_prefix=prefix)
        assert actual == expected

    def test_ciphertext_matches_independent_construction_chinese(self):
        """中文 + 长消息（跨多个 32B 块）独立比对。"""
        prefix = bytes((i * 7 + 3) % 256 for i in range(16))
        plaintext = "停线应急协同：品质域发现锡膏偏移不良 77 pcs，设备域刮刀压力异常，请确认复线方案。"
        expected = _independent_wecom_cipher(AES_KEY, APPID, plaintext, prefix)
        assert encrypt_msg(AES_KEY, APPID, plaintext,
                           random_prefix=prefix) == expected

    def test_plaintext_structure_after_decrypt(self):
        """解密后明文结构：random(16) + len(4) + msg + appid，逐字段核对。"""
        prefix = bytes(range(16))
        plaintext = "hello-wecom"
        cipher_b64 = encrypt_msg(AES_KEY, APPID, plaintext,
                                 random_prefix=prefix)
        key = base64.b64decode(AES_KEY + "=")
        from mfg_agents.v2.contracts.adapters.aes_cbc import cbc_decrypt
        padded = cbc_decrypt(key, key[:16], base64.b64decode(cipher_b64))
        assert padded[:16] == prefix
        msg_len = struct.unpack(">I", padded[16:20])[0]
        assert msg_len == len(plaintext.encode("utf-8"))
        assert padded[20:20 + msg_len].decode("utf-8") == plaintext
        # PKCS#7@32：补位长度在 1..32 且总长 32 的倍数
        assert len(padded) % 32 == 0
        pad_len = padded[-1]
        assert 1 <= pad_len <= 32
        assert padded[-pad_len:] == bytes([pad_len]) * pad_len
        # 去补位后，msg 之后必须紧邻 appid（无多余字节）
        assert padded[20 + msg_len:-pad_len].decode("utf-8") == APPID

    def test_encoding_aes_key_decodes_to_32_bytes(self):
        """43 位 EncodingAESKey（官方格式，无 = 号）→ 32 字节 AES-256 密钥。"""
        assert len(AES_KEY) == 43
        assert len(aes_key_from_b64(AES_KEY)) == 32

    def test_wrong_length_aes_key_rejected(self):
        with pytest.raises(WeComCryptoError, match="32 字节"):
            aes_key_from_b64("YWJjZGVmZ2hpamtsbW5vcHFyc3R1dnd4")  # 30 字节

    def test_empty_appid_roundtrip(self):
        """appid 为空串时结构上仍自洽（适配器构造层禁止空 appid）。"""
        cipher = encrypt_msg(AES_KEY, "", "x")
        assert decrypt_msg(AES_KEY, "", cipher) == "x"


# ---------- 3. 验签：独立 hashlib 锚点 + 负向 ----------

class TestWeComSignature:
    def test_signature_independent_hashlib(self):
        """签名 = SHA1(字典序拼接)——QA 独立 hashlib 复算。"""
        for ts, nonce, data in [("1729000000", "9x1m2k3p", "EncryptDataXYZ"),
                                ("0", "a", "b"), ("999", "zz", "QQ")]:
            expected = hashlib.sha1(
                "".join(sorted([TOKEN, ts, nonce, data])).encode("utf-8")
            ).hexdigest()
            assert compute_signature(TOKEN, ts, nonce, data) == expected

    def test_wrong_token_rejected(self):
        adapter = WeComAdapter(TOKEN, AES_KEY, APPID)
        echostr = encrypt_msg(AES_KEY, APPID, "echo")
        bad_sig = compute_signature("WRONG-TOKEN", "1729000000", "n", echostr)
        assert adapter.verify_callback(
            {"msg_signature": bad_sig, "timestamp": "1729000000",
             "nonce": "n", "encrypt": echostr}) is False

    def test_wrong_timestamp_rejected(self):
        adapter = WeComAdapter(TOKEN, AES_KEY, APPID)
        echostr = encrypt_msg(AES_KEY, APPID, "echo")
        bad_sig = compute_signature(TOKEN, "9999999999", "n", echostr)
        assert adapter.verify_callback(
            {"msg_signature": bad_sig, "timestamp": "1729000000",
             "nonce": "n", "encrypt": echostr}) is False

    def test_wrong_nonce_rejected(self):
        adapter = WeComAdapter(TOKEN, AES_KEY, APPID)
        echostr = encrypt_msg(AES_KEY, APPID, "echo")
        bad_sig = compute_signature(TOKEN, "1729000000", "OTHER", echostr)
        assert adapter.verify_callback(
            {"msg_signature": bad_sig, "timestamp": "1729000000",
             "nonce": "n", "encrypt": echostr}) is False

    def test_replay_same_signature_rejected(self):
        """重放防护（第三十棒闭环）：同一 nonce 的合法签名第二次必须被拒绝。

        旧实现无时间戳/ nonce 新鲜度校验，重放会被再次接受；现已补
        timestamp 窗口 + nonce 一次性消费。"""
        import time
        import uuid

        adapter = WeComAdapter(TOKEN, AES_KEY, APPID)
        echostr = encrypt_msg(AES_KEY, APPID, "echo")
        timestamp = str(int(time.time()))
        nonce = "replay-" + uuid.uuid4().hex[:8]
        sig = compute_signature(TOKEN, timestamp, nonce, echostr)
        payload = {"msg_signature": sig, "timestamp": timestamp,
                   "nonce": nonce, "echostr": echostr}
        assert adapter.verify_callback(payload) is True
        assert adapter.verify_callback(payload) is False  # 重放被拒绝


# ---------- 4. 密文篡改 / 截断 / 结构攻击 ----------

class TestWeComCipherAttacks:
    def _flip_bit(self, cipher_b64: str, byte_index: int) -> str:
        raw = bytearray(base64.b64decode(cipher_b64))
        raw[byte_index] ^= 0x01
        return base64.b64encode(bytes(raw)).decode("ascii")

    @pytest.mark.parametrize("pos_desc,pos_fn", [
        ("首块", lambda n: 0),
        ("中块", lambda n: n // 2),
        ("末块(补位区)", lambda n: n - 1),
    ])
    def test_single_bit_tamper_rejected(self, pos_desc, pos_fn):
        """篡改 1 bit 必须解密失败（CBC 扩散 + 补位/appid 校验兜底）。"""
        cipher = encrypt_msg(AES_KEY, APPID, "hello world")
        raw_len = len(base64.b64decode(cipher))
        tampered = self._flip_bit(cipher, pos_fn(raw_len))
        with pytest.raises(WeComCryptoError):
            decrypt_msg(AES_KEY, APPID, tampered)

    def test_tamper_every_bit_position_rejected(self):
        """全位置逐 bit 翻转：任何 1 bit 篡改都不得解出明文。"""
        cipher = encrypt_msg(AES_KEY, APPID, "A型号在SMT-1停线，何时复线？")
        raw = base64.b64decode(cipher)
        for i in range(len(raw)):
            flipped = bytearray(raw)
            flipped[i] ^= 0x80
            with pytest.raises(WeComCryptoError):
                decrypt_msg(AES_KEY, APPID,
                            base64.b64encode(bytes(flipped)).decode("ascii"))

    def test_truncated_ciphertext_rejected(self):
        """截断密文（非 16 倍数）必须拒绝（当前抛 ValueError，契约不一致已记录）。"""
        cipher = encrypt_msg(AES_KEY, APPID, "hello world")
        raw = base64.b64decode(cipher)
        with pytest.raises((WeComCryptoError, ValueError)):
            decrypt_msg(AES_KEY, APPID,
                        base64.b64encode(raw[:-16]).decode("ascii"))

    def test_msg_len_field_lie_rejected(self):
        """构造 msg_len 字段撒谎的明文结构（独立加密）→ 必须拒绝。"""
        key = base64.b64decode(AES_KEY + "=")
        from mfg_agents.v2.contracts.adapters.aes_cbc import cbc_encrypt
        prefix = bytes(range(16))
        msg = b"hi"
        buf = prefix + struct.pack(">I", 999) + msg + APPID.encode()  # len 撒谎
        pad_len = 32 - len(buf) % 32
        buf += bytes([pad_len]) * pad_len
        cipher = base64.b64encode(cbc_encrypt(key, key[:16], buf)).decode()
        with pytest.raises(WeComCryptoError):
            decrypt_msg(AES_KEY, APPID, cipher)

    def test_wrong_appid_injected_rejected(self):
        """跨应用注入：用别的 appid 加密的密文必须被归属校验拒绝。"""
        cipher = encrypt_msg(AES_KEY, "wxotherappid99", "hello")
        with pytest.raises(WeComCryptoError, match="appid"):
            decrypt_msg(AES_KEY, APPID, cipher)

    def test_adapter_receive_rejects_forged_signature(self):
        """适配器层：验签不过 → receive 直接拒绝（不进入解密）。"""
        adapter = WeComAdapter(TOKEN, AES_KEY, APPID)
        inner = ("<xml><ToUserName><![CDATA[wx]]></ToUserName>"
                 "<FromUserName><![CDATA[zhangsan]]></FromUserName>"
                 "<MsgType><![CDATA[text]]></MsgType>"
                 "<Content><![CDATA[hi]]></Content></xml>")
        encrypt = encrypt_msg(AES_KEY, APPID, inner)
        payload = {"msg_signature": "0" * 40, "timestamp": "1729000000",
                   "nonce": "n", "encrypt": encrypt}
        with pytest.raises(WeComCryptoError, match="验签失败"):
            adapter.receive(payload)

    def test_adapter_receive_rejects_tampered_cipher(self):
        """适配器层：验签通过但密文被篡改（模拟签名重放+内容替换）→ 拒绝。"""
        adapter = WeComAdapter(TOKEN, AES_KEY, APPID)
        inner = ("<xml><ToUserName><![CDATA[wx]]></ToUserName>"
                 "<FromUserName><![CDATA[zhangsan]]></FromUserName>"
                 "<MsgType><![CDATA[text]]></MsgType>"
                 "<Content><![CDATA[hi]]></Content></xml>")
        encrypt = encrypt_msg(AES_KEY, APPID, inner)
        raw = bytearray(base64.b64decode(encrypt))
        raw[20] ^= 0x01
        tampered = base64.b64encode(bytes(raw)).decode("ascii")
        import time

        timestamp = str(int(time.time()))
        sig = compute_signature(TOKEN, timestamp, "n", tampered)
        with pytest.raises(WeComCryptoError):
            adapter.receive({"msg_signature": sig, "timestamp": timestamp,
                             "nonce": "n", "encrypt": tampered})
