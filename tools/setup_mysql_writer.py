"""创建 / 收敛业务库**写账号**（最小权限；生产部署辅助脚本）。

为什么不直接用 root：应用进程只需要 DML + 自愈建表，不需要 DROP / GRANT / FILE。
本脚本授予：

    SELECT, INSERT, UPDATE, DELETE, CREATE, ALTER, INDEX

- DML：SPC 落库、KBQA 日志、看板回写；
- CREATE/ALTER/INDEX：兼容现有 ``ensure_schema`` 自愈（老库补列）；
- **不含** DROP / GRANT OPTION / FILE / PROCESS 等管理权限。

数据库本身与迁移仍由管理员完成；应用侧配置::

    MFG_MYSQL_ADMIN_URL=mysql+pymysql://root:demo123@127.0.0.1:3307/mysql \\
    MFG_MYSQL_RW_PASSWORD=demo123 \\
    uv run python tools/setup_mysql_writer.py --database mfg_agent --user mfg_rw

随后 ``DB_URL`` 指向写账号，``DB_READ_URL`` 指向只读账号（见 setup_mysql_readonly.py）。
"""
from __future__ import annotations

import argparse
import getpass
import os
import sys

from setup_mysql_readonly import _clean_host, _clean_ident, provision

#: 写账号最小权限：DML + 必要的自愈 DDL；不含 DROP/GRANT/FILE
WRITE_PRIVILEGES = ("SELECT", "INSERT", "UPDATE", "DELETE",
                    "CREATE", "ALTER", "INDEX")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="创建 / 收敛 MySQL 业务库写账号")
    parser.add_argument("--admin-url", default=os.getenv("MFG_MYSQL_ADMIN_URL", ""),
                        help="管理连接（默认取 MFG_MYSQL_ADMIN_URL）")
    parser.add_argument("--database", default="mfg_agent", help="业务库名")
    parser.add_argument("--user", default="mfg_rw", help="写账号名")
    parser.add_argument("--host", default="%", help="账号 host（默认 %% 允许远程）")
    parser.add_argument("--password", default=os.getenv("MFG_MYSQL_RW_PASSWORD", ""),
                        help="写账号密码（默认取 MFG_MYSQL_RW_PASSWORD；缺失则交互输入）")
    args = parser.parse_args(argv)
    if not args.admin_url:
        parser.error("缺少 --admin-url 或 MFG_MYSQL_ADMIN_URL")
    if not args.password:
        args.password = getpass.getpass("写账号密码: ")
    summary = provision(args.admin_url, _clean_ident(args.database, "database"),
                        _clean_ident(args.user, "user"), args.password,
                        _clean_host(args.host), WRITE_PRIVILEGES)
    print(f"完成: {summary}")
    print("应用侧配置：DB_URL 指向该写账号（协议 mysql+pymysql）；"
          "DB_READ_URL 指向只读账号；数据库与迁移仍由管理员执行。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
