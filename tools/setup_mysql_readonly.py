"""创建 / 收敛业务库只读账号（生产部署辅助脚本）。

为什么必须有这一步：``MySQLDataSource`` 的代码层只读守卫（仅 SELECT/WITH）
不是数据库权限的替代品——拿 root 连业务库时，一条注入或误操作仍可能写入。
生产必须用**只读账号**（权限在 DB 侧兜底）。

本脚本幂等：
1. ``CREATE USER IF NOT EXISTS``；
2. 更新密码（重复执行不会失败）；
3. 撤销该账号的全部权限（含 GRANT OPTION）；
4. 只授予目标库的 ``SELECT``。

用法（密码建议从环境变量读取，避免进 shell history）::

    MFG_MYSQL_ADMIN_URL=mysql+pymysql://root:demo123@127.0.0.1:3307/mysql \\
    MFG_MYSQL_RO_PASSWORD=demo123 \\
    uv run python tools/setup_mysql_readonly.py --database mfg_agent --user mfg_ro

随后把应用的**读路径**指向只读账号（`DB_READ_URL`；写通道仍用主库 `DB_URL`）。
"""
from __future__ import annotations

import argparse
import getpass
import os
import re
import sys
from typing import Any

from sqlalchemy import create_engine, text

_IDENT = re.compile(r"^[A-Za-z0-9_]{1,64}$")
_HOST = re.compile(r"^[A-Za-z0-9_.:%-]{1,255}$")


def _clean_ident(value: str, label: str) -> str:
    value = str(value or "").strip()
    if not _IDENT.match(value):
        raise SystemExit(f"{label} 非法（仅允许字母/数字/下划线，<=64 字符）: {value!r}")
    return value


def _clean_host(value: str) -> str:
    value = str(value or "").strip()
    if not _HOST.match(value):
        raise SystemExit(f"host 非法: {value!r}")
    return value


def provision(admin_url: str, database: str, user: str, password: str,
              host: str = "%") -> dict[str, Any]:
    """创建 / 收敛只读账号；返回执行摘要（不含密码）。"""
    database = _clean_ident(database, "database")
    user = _clean_ident(user, "user")
    host = _clean_host(host)
    if not str(password or ""):
        raise SystemExit("密码不能为空")
    engine = create_engine(admin_url, pool_pre_ping=False)
    account = f"'{user}'@'{host}'"
    steps: list[str] = []
    try:
        with engine.begin() as conn:
            conn.execute(text(f"CREATE USER IF NOT EXISTS {account} IDENTIFIED BY :pw"),
                         {"pw": password})
            steps.append("create_user")
            conn.execute(text(f"ALTER USER {account} IDENTIFIED BY :pw"),
                         {"pw": password})
            steps.append("update_password")
            try:
                # 已有旧授权时全部收回；无授权时 MySQL 可能报 1141，忽略即可
                conn.execute(text(f"REVOKE ALL PRIVILEGES, GRANT OPTION FROM {account}"))
                steps.append("revoke_all")
            except Exception:  # noqa: BLE001
                steps.append("revoke_skipped")
            conn.execute(text(f"GRANT SELECT ON `{database}`.* TO {account}"))
            steps.append("grant_select")
            rows = conn.execute(text(
                "SELECT privilege_type FROM information_schema.user_privileges "
                "WHERE grantee = :grantee"), {"grantee": f"'{user}'@'{host}'"}).fetchall()
            global_privs = sorted(str(r[0]) for r in rows)
    finally:
        engine.dispose()
    return {"database": database, "user": user, "host": host,
            "steps": steps, "global_privileges": global_privs}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="创建 / 收敛 MySQL 业务库只读账号")
    parser.add_argument("--admin-url", default=os.getenv("MFG_MYSQL_ADMIN_URL", ""),
                        help="管理连接（默认取 MFG_MYSQL_ADMIN_URL）")
    parser.add_argument("--database", default="mfg_agent", help="业务库名")
    parser.add_argument("--user", default="mfg_ro", help="只读账号名")
    parser.add_argument("--host", default="%", help="账号 host（默认 %% 允许远程）")
    parser.add_argument("--password", default=os.getenv("MFG_MYSQL_RO_PASSWORD", ""),
                        help="只读账号密码（默认取 MFG_MYSQL_RO_PASSWORD；缺失则交互输入）")
    args = parser.parse_args(argv)
    if not args.admin_url:
        parser.error("缺少 --admin-url 或 MFG_MYSQL_ADMIN_URL")
    if not args.password:
        args.password = getpass.getpass("只读账号密码: ")
    summary = provision(args.admin_url, args.database, args.user,
                        args.password, args.host)
    print(f"完成: {summary}")
    print("应用侧配置：把 DB_READ_URL 指向该只读账号（协议 mysql+pymysql，"
          f"账号 {summary['user']}，库 {summary['database']}，"
          "主机按部署环境填写），密码按实际填写；写通道仍用 DB_URL。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
