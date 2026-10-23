"""写账号部署脚本的权限白名单（无真库）：不允许 DROP / GRANT / FILE。"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

from setup_mysql_readonly import provision  # noqa: E402
from setup_mysql_writer import WRITE_PRIVILEGES  # noqa: E402


def test_writer_grants_are_least_privilege():
    assert set(WRITE_PRIVILEGES) == {
        "SELECT", "INSERT", "UPDATE", "DELETE", "CREATE", "ALTER", "INDEX"}
    for forbidden in ("DROP", "GRANT OPTION", "FILE", "PROCESS", "SUPER"):
        assert forbidden not in WRITE_PRIVILEGES


@pytest.mark.parametrize("bad", [("DROP",), ("GRANT",), ("SELECT", "FILE"), ()])
def test_provision_rejects_privileges_outside_whitelist(bad):
    with pytest.raises(SystemExit, match="权限非法"):
        provision("mysql+pymysql://root:pw@127.0.0.1:1/x", "mfg_agent",
                  "mfg_rw", "demo123", "%", bad)


def test_provision_rejects_empty_password_before_connect():
    with pytest.raises(SystemExit, match="密码"):
        provision("mysql+pymysql://root:pw@127.0.0.1:1/x", "mfg_agent",
                  "mfg_rw", "", "%", WRITE_PRIVILEGES)
