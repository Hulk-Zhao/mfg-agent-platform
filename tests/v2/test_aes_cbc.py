"""AES-CBC 纯标准库实现的已知向量测试（NIST SP800-38A / FIPS-197）。

外部锚点：NIST 官方测试向量验证实现正确性（不是自证循环）。
"""
import os

import pytest

from mfg_agents.v2.contracts.adapters.aes_cbc import (
    AESCipher,
    _SBOX,
    cbc_decrypt,
    cbc_encrypt,
)

# FIPS-197 示例：AES-128 单块
FIPS197_KEY = bytes(range(16))
FIPS197_PT = bytes.fromhex("00112233445566778899aabbccddeeff")
FIPS197_CT = bytes.fromhex("69c4e0d86a7b0430d8cdb78070b4c55a")

# NIST SP800-38A F.2.1 CBC-AES128.Encrypt（前三块）
NIST_KEY = bytes.fromhex("2b7e151628aed2a6abf7158809cf4f3c")
NIST_IV = bytes.fromhex("000102030405060708090a0b0c0d0e0f")
NIST_PT = bytes.fromhex(
    "6bc1bee22e409f96e93d7e117393172a"
    "ae2d8a571e03ac9c9eb76fac45af8e51"
    "30c81c46a35ce411e5fbc1191a0a52ef"
)
NIST_CT = bytes.fromhex(
    "7649abac8119b246cee98e9b12e9197d"
    "5086cb9b507219ee95db113a917678b2"
    "73bed6b8e3c1743b7116e69e22229516"
)


def test_fips197_single_block_vector():
    """FIPS-197 AES-128 单块已知向量。"""
    assert AESCipher(FIPS197_KEY).encrypt_block(FIPS197_PT) == FIPS197_CT
    assert AESCipher(FIPS197_KEY).decrypt_block(FIPS197_CT) == FIPS197_PT


def test_nist_cbc_aes128_known_vector():
    """NIST SP800-38A CBC-AES128 已知向量（3 块，含 IV 链式反应）。"""
    assert cbc_encrypt(NIST_KEY, NIST_IV, NIST_PT) == NIST_CT
    assert cbc_decrypt(NIST_KEY, NIST_IV, NIST_CT) == NIST_PT


def test_sbox_generated_values():
    """S-Box 由数学定义生成，抽点验证标准值。"""
    assert _SBOX[0x00] == 0x63
    assert _SBOX[0x53] == 0xED
    assert _SBOX[0xFF] == 0x16


def test_roundtrip_aes256_random():
    """AES-256（企微密钥长度）随机 round-trip。"""
    key = os.urandom(32)
    iv = os.urandom(16)
    plaintext = os.urandom(64)
    assert cbc_decrypt(key, iv, cbc_encrypt(key, iv, plaintext)) == plaintext


def test_invalid_key_length_rejected():
    with pytest.raises(ValueError):
        AESCipher(b"too-short-key")


def test_cbc_requires_block_multiple():
    key = os.urandom(16)
    iv = os.urandom(16)
    with pytest.raises(ValueError):
        cbc_encrypt(key, iv, b"not-16-bytes")
    with pytest.raises(ValueError):
        cbc_decrypt(key, iv, b"not-16-bytes")
