"""治理工单仓储契约（D15）：SQLite 实现先行，MySQL/RDS 实现排期。

为什么必须抽这一层（《架构改造推荐方向》D15 / 风险清单第 3 条）：

- ``WorkflowEngine`` 直连 ``sqlite3`` 的写法已被复制到 6 个 store
  （workflow / report_store / site_board / spc_store / case_schema /
  orchestrator 播种）；不抽契约，R13 连接器框架无处挂载；
- 本契约先收敛**引擎自身**的全部读写（get / list / insert / update），
  ``WorkflowEngine`` 不再持有裸 ``_connect`` 的业务 SQL；
- 其余 5 处按同一形状迁移（见 ``docs/R13-R14-连接器与场景屏决策.md``
  的迁移清单），禁止再新增第 7 份裸 ``_connect``。

契约边界：

- 只暴露**行级**语义（dict 进 / dict 出），不暴露 SQL 方言；MySQL 实现
  只需换连接与占位符；
- ``ensure_schema`` 负责建表与播种，幂等；
- ``connect()`` 是 SQLite 实现的**兼容逃生口**（既有测试与运维脚本用），
  不属于跨后端契约。
"""
from __future__ import annotations

import sqlite3
import threading
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Optional

from ...common.platform_schema import (
    ensure_platform_schema,
    ensure_platform_schema_sqlite,
    is_sqlite_target,
    sqlite_path_from_url,
)

__all__ = [
    "WorkflowBackendError",
    "assert_sqlite_backed",
    "WorkflowRepository",
    "SQLiteWorkflowRepository",
]

TASK_TABLE = "platform_workflow_task"

#: 签名事实表（会签 / 终批 / 驳回的独立落地表；幂等由唯一索引兜底）
SIGNOFF_TABLE = "wf_signoff"

#: 工单表写入列（顺序即 INSERT 参数顺序；与 SQLite 实现共用）
TASK_COLUMNS: tuple[str, ...] = (
    "task_uid", "wf_id", "title", "status", "priority", "assignees",
    "context_json", "hitl_prompt", "history_json", "governance_json",
    "owner_scope", "created_at", "updated_at", "sla_due_at",
)

#: 签名表写入列（``id`` 自增，不写入）
SIGNOFF_COLUMNS: tuple[str, ...] = (
    "task_uid", "role_key", "role_name", "actor", "actor_id", "actor_kind",
    "verb", "decision", "comment", "on_behalf_of", "overdue_signoff", "ts",
)

#: 可更新列（task_uid 是主键，不允许改）
TASK_UPDATABLE: frozenset[str] = frozenset(TASK_COLUMNS) - {"task_uid"}


class WorkflowBackendError(NotImplementedError):
    """治理写路径不接受非 SQLite 后端——启动即失败，绝不静默降级。"""


def assert_sqlite_backed(db_url: str) -> None:
    """校验 ``db_url`` 可作为 SQLite 文件路径使用，否则 fail-fast。

    背景（这条防线是必须的，别删）：本引擎直连 ``sqlite3``，不走 SQLAlchemy，
    而 :func:`sqlite_path_from_url` 对无法识别的 URL **原样返回整条字符串**。
    若把 ``DB_URL=mysql+pymysql://root:pw@host:3306/db`` 直接传进来，会得到：

    - Linux / 容器：``sqlite3.connect("<整条 URL>")`` 在工作目录静默建出一个
      文件名即该 URL 的空库 → 工单写进错误位置，跨进程重启（CWD 变化）后"消失"；
    - Windows：文件名含 ``:`` 属非法字符 → 直接抛 ``OSError``，治理链整体不可用。

    两种结局都是事故，且第一种**不报错**，排查成本极高。因此在此显式拒绝，
    让错误在启动时以清晰信息暴露。方言判定复用
    :func:`~mfg_agents.common.platform_schema.is_sqlite_target`，与建表路径同源。
    """
    if is_sqlite_target(db_url):
        return
    raise WorkflowBackendError(
        f"WorkflowEngine 目前仅支持 SQLite 持久化，收到非 SQLite 地址：{db_url!r}。\n"
        f"治理写路径（工单 / 会签 / SLA / HITL）直连 sqlite3，未接入 SQLAlchemy 抽象；\n"
        f"把 MySQL URL 当作文件路径会静默建出错误的库（Windows 直接抛异常）。\n"
        f"解决方式：\n"
        f"  1) 单独指定治理库（推荐）——在 .env 设 "
        f"WORKFLOW_DB=sqlite:////absolute/path/to/platform.db，与 DB_URL 解耦；\n"
        f"  2) 或把 DB_URL 改回 SQLite；\n"
        f"  3) 需要治理数据落 MySQL 时，实现本文件的 WorkflowRepository 契约\n"
        f"     （SQLAlchemy 后端），无需再改 WorkflowEngine。")


class WorkflowRepository(ABC):
    """工单仓储契约：行级读写，不暴露 SQL 方言。"""

    @abstractmethod
    def ensure_schema(self) -> None:
        """建表 + 播种（幂等）。"""

    @abstractmethod
    def fetch_task(self, task_uid: str) -> Optional[dict[str, Any]]:
        """按主键取一行（不存在返回 None，不抛错）。"""

    @abstractmethod
    def list_tasks(self, *, status: Optional[str] = None,
                   run_id: Optional[str] = None) -> list[dict[str, Any]]:
        """按状态 / run_id 过滤，``ORDER BY id DESC``。"""

    @abstractmethod
    def insert_task(self, row: dict[str, Any]) -> None:
        """插入一行（``row`` 的键必须覆盖 TASK_COLUMNS；JSON 列已序列化）。"""

    @abstractmethod
    def update_task(self, task_uid: str, fields: dict[str, Any]) -> int:
        """按主键更新指定列，返回受影响行数（不存在的工单返回 0）。"""

    # ---------- 签名事实表（多实例幂等的 DB 级防线） ----------

    @abstractmethod
    def insert_signoff(self, row: dict[str, Any]) -> bool:
        """写入一条签名事实；命中唯一索引（重复签名）返回 False，不抛错。"""

    @abstractmethod
    def list_signoffs(self, task_uid: str) -> list[dict[str, Any]]:
        """按工单取签名事实（``id`` 升序）。"""

    @abstractmethod
    def delete_signoffs(self, task_uid: str, verb: Optional[str] = None) -> int:
        """删除某工单的签名事实（可选按 verb），返回删除行数。"""


class SQLiteWorkflowRepository(WorkflowRepository):
    """SQLite 实现（治理库；``assert_sqlite_backed`` 在构造期 fail-fast）。"""

    def __init__(self, db_url: str) -> None:
        assert_sqlite_backed(db_url)
        self.db_url = db_url
        self.db_path = sqlite_path_from_url(db_url)
        self._memory_conn: Optional[sqlite3.Connection] = None
        self._lock = threading.RLock()
        # :memory: 需要进程内持久连接（DDL 与 DML 同一库，BUG-4 修复）
        if self.db_path == ":memory:":
            self._memory_conn = sqlite3.connect(":memory:")
            self._memory_conn.row_factory = sqlite3.Row
        self.ensure_schema()

    # ---------- schema ----------

    def ensure_schema(self) -> None:
        if self._memory_conn is not None:
            ensure_platform_schema_sqlite(self._memory_conn)
            return
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        ensure_platform_schema(self.db_url)

    # ---------- 连接（SQLite 实现的兼容逃生口，非跨后端契约） ----------

    def connect(self) -> sqlite3.Connection:
        if self._memory_conn is not None:
            return self._memory_conn
        conn = sqlite3.connect(self.db_path, timeout=10.0)
        conn.row_factory = sqlite3.Row
        return conn

    # ---------- 读 ----------

    def fetch_task(self, task_uid: str) -> Optional[dict[str, Any]]:
        with self.connect() as conn:
            row = conn.execute(
                f"SELECT * FROM {TASK_TABLE} WHERE task_uid = ?",
                (task_uid,)).fetchone()
        return dict(row) if row is not None else None

    def list_tasks(self, *, status: Optional[str] = None,
                   run_id: Optional[str] = None) -> list[dict[str, Any]]:
        sql = f"SELECT * FROM {TASK_TABLE}"
        conditions: list[str] = []
        params: list[Any] = []
        if status:
            conditions.append("status = ?")
            params.append(status)
        if run_id:
            conditions.append("context_json LIKE ?")
            params.append(f'%"{run_id}"%')
        if conditions:
            sql += " WHERE " + " AND ".join(conditions)
        sql += " ORDER BY id DESC"
        with self.connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    # ---------- 写 ----------

    def insert_task(self, row: dict[str, Any]) -> None:
        missing = [c for c in TASK_COLUMNS if c not in row]
        if missing:
            raise ValueError(f"工单插入缺列: {missing}")
        with self._lock, self.connect() as conn:
            conn.execute(
                f"INSERT INTO {TASK_TABLE} ({', '.join(TASK_COLUMNS)}) "
                f"VALUES ({', '.join('?' for _ in TASK_COLUMNS)})",
                tuple(row[c] for c in TASK_COLUMNS))

    def update_task(self, task_uid: str, fields: dict[str, Any]) -> int:
        payload = dict(fields)
        illegal = sorted(set(payload) - TASK_UPDATABLE)
        if illegal:
            raise ValueError(f"工单更新非法列: {illegal}")
        if not payload:
            return 0
        assignments = ", ".join(f"{k} = ?" for k in payload)
        with self._lock, self.connect() as conn:
            cur = conn.execute(
                f"UPDATE {TASK_TABLE} SET {assignments} WHERE task_uid = ?",
                (*payload.values(), task_uid))
            return int(cur.rowcount or 0)

    # ---------- 签名事实表 ----------

    def insert_signoff(self, row: dict[str, Any]) -> bool:
        missing = [c for c in SIGNOFF_COLUMNS if c not in row]
        if missing:
            raise ValueError(f"签名插入缺列: {missing}")
        with self._lock, self.connect() as conn:
            try:
                conn.execute(
                    f"INSERT INTO {SIGNOFF_TABLE} ({', '.join(SIGNOFF_COLUMNS)}) "
                    f"VALUES ({', '.join('?' for _ in SIGNOFF_COLUMNS)})",
                    tuple(row[c] for c in SIGNOFF_COLUMNS))
            except sqlite3.IntegrityError:
                return False
        return True

    def list_signoffs(self, task_uid: str) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM {SIGNOFF_TABLE} WHERE task_uid = ? ORDER BY id",
                (task_uid,)).fetchall()
        return [dict(r) for r in rows]

    def delete_signoffs(self, task_uid: str, verb: Optional[str] = None) -> int:
        sql = f"DELETE FROM {SIGNOFF_TABLE} WHERE task_uid = ?"
        params: list[Any] = [task_uid]
        if verb:
            sql += " AND verb = ?"
            params.append(verb)
        with self._lock, self.connect() as conn:
            cur = conn.execute(sql, params)
            return int(cur.rowcount or 0)
