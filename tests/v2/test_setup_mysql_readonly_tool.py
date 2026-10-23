"""只读账号部署脚本的输入守卫（无真库）：标识符白名单，防 DDL 注入。"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

from setup_mysql_readonly import (  # noqa: E402
    _clean_host,
    _clean_ident,
    provision,
)


def test_identifiers_accept_safe_values():
    assert _clean_ident("mfg_agent", "database") == "mfg_agent"
    assert _clean_ident("mfg_ro", "user") == "mfg_ro"
    assert _clean_host("%") == "%"
    assert _clean_host("127.0.0.1") == "127.0.0.1"


@pytest.mark.parametrize("bad", ["mfg-agent", "mfg agent", "a`b", "x'; DROP USER y;--", ""])
def test_identifiers_reject_injection(bad):
    with pytest.raises(SystemExit):
        _clean_ident(bad, "database")


def test_empty_password_rejected_before_connect():
    with pytest.raises(SystemExit, match="密码"):
        provision("mysql+pymysql://root:pw@127.0.0.1:1/x", "mfg_agent",
                  "mfg_ro", "", "%")
