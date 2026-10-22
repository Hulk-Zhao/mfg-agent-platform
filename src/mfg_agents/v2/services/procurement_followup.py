"""齐套催交状态机（R6 · 治理轨 · 双库分工 · 演示回写徽标）。

产品目标（PRD P1 R6 / 用户故事 U2）：缺口清单（料号 / 缺口数 / 供应商 / 催交人）
一键派发 → 催交状态可见 → 到货确认 → 关闭。

架构判定（《架构改造推荐方向》D9 / D10 / Q2）：
- **不写业务表**：业务库经 ``SQLiteDataSource`` 以 ``file:...?mode=ro`` 连接，
  驱动层拒绝写；回写语义（幂等 / 补偿 / 审计回执）随 R13 连接器契约交付，
  本期只做「治理轨催交状态机」。
- **双库分工**：演示态落 ``DEMO_DB_PATH``（``data/demo_storyline/demo_governance.db``），
  真实态落 ``settings.WORKFLOW_DB``。两套物理库互不污染；演示库重启不丢。
- **演示回写徽标**：演示态到货确认不冒充「已写回 ERP」，行上记 ``writeback_mode=demo``，
  响应带 ``demo_writeback`` 与不可关闭的徽标文案（同 ``site_board`` ack/feedback 纪律）。
- **契约先行**：``ProcurementFollowupRepository`` ABC + ``SQLiteProcurementFollowupRepository``
  实现，禁止再复制第 7 份裸 ``_connect``（新 store 一律「契约 + 实现」）。

状态机（PRD v1.2 草案）::

    draft（缺口清单）→ dispatched（已派发待办）→ supplier_replied（供应商已回复）
      → arrival_confirmed（到货已确认）→ closed（关闭）
    任一非终态超 SLA → escalated（复用本模块 SLA 字段，不新造计时器）

幂等键：``(part_no, supplier, work_order_set)``——一次问的是「工单集」，
重复派发同一缺口返回既有行（不产生第二条催交待办）。
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import sqlite3
from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Optional, Union

from sqlalchemy import create_engine, text

from ...common.config import settings
from ...common.platform_schema import is_sqlite_target, sqlite_path_from_url
from .workflow import assert_sqlite_backed
from .writeback import emit_arrival_receipt

logger = logging.getLogger(__name__)

__all__ = [
    "FOLLOWUP_TABLE",
    "STATUSES",
    "STATUS_TRANSITIONS",
    "TERMINAL_STATUSES",
    "ACTION_TARGETS",
    "DEFAULT_OWNER",
    "DEFAULT_SLA_HOURS",
    "FollowupError",
    "FollowupNotFound",
    "InvalidFollowupTransition",
    "FollowupDataError",
    "ProcurementFollowupRepository",
    "SQLiteProcurementFollowupRepository",
    "SQLAlchemyProcurementFollowupRepository",
    "resolve_followup_target",
    "resolve_followup_db",
    "followup_id_for",
    "ensure_followup_table",
    "build_followup_gaps",
    "plan_followups",
    "dispatch_followups",
    "list_followups",
    "get_followup",
    "transition",
    "escalate_overdue",
]

FOLLOWUP_TABLE = "procurement_followup"

#: 状态机（终态不可再流转；escalated 是「升级中」仍在办，允许继续回落到正常链路）
STATUSES: tuple[str, ...] = (
    "draft", "dispatched", "supplier_replied", "arrival_confirmed",
    "escalated", "closed",
)
STATUS_TRANSITIONS: dict[str, tuple[str, ...]] = {
    "draft": ("dispatched",),
    "dispatched": ("supplier_replied", "escalated"),
    "supplier_replied": ("arrival_confirmed", "escalated"),
    "arrival_confirmed": ("closed",),
    "escalated": ("supplier_replied", "arrival_confirmed", "closed"),
    "closed": (),
}
TERMINAL_STATUSES = frozenset({"closed"})

#: 前端/接口动作 → 目标状态（动作名面向业务，状态名面向状态机）
ACTION_TARGETS: dict[str, str] = {
    "dispatch": "dispatched",
    "reply": "supplier_replied",
    "arrival": "arrival_confirmed",
    "close": "closed",
    "escalate": "escalated",
}

#: 催交责任人（自由文本，仅作展示；判据仍走岗位权限，不做归一化）
DEFAULT_OWNER = "采购员"

#: 催交 SLA（小时）：超时未回复/未到货 → escalated
DEFAULT_SLA_HOURS = float(os.getenv("MFG_FOLLOWUP_SLA_HOURS", "48") or 48)

_DDL = """
CREATE TABLE IF NOT EXISTS procurement_followup (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    followup_id TEXT NOT NULL UNIQUE,
    work_order_set TEXT NOT NULL,
    part_no TEXT NOT NULL,
    part_name TEXT NOT NULL DEFAULT '',
    supplier TEXT NOT NULL DEFAULT '',
    shortage_qty REAL NOT NULL DEFAULT 0,
    available_qty REAL NOT NULL DEFAULT 0,
    safety_stock REAL NOT NULL DEFAULT 0,
    owner TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'draft',
    supplier_reply TEXT NOT NULL DEFAULT '',
    reply_at TEXT,
    arrival_qty REAL,
    arrival_at TEXT,
    closed_at TEXT,
    close_note TEXT NOT NULL DEFAULT '',
    writeback_mode TEXT NOT NULL DEFAULT 'demo',
    sla_due_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    history_json TEXT NOT NULL DEFAULT '[]'
);
CREATE INDEX IF NOT EXISTS idx_followup_status ON procurement_followup (status);
CREATE INDEX IF NOT EXISTS idx_followup_set ON procurement_followup (work_order_set);
"""

#: MySQL 版建表（索引内联，幂等）。
MYSQL_FOLLOWUP_DDL = """
CREATE TABLE IF NOT EXISTS procurement_followup (
    id BIGINT NOT NULL AUTO_INCREMENT,
    followup_id VARCHAR(80) NOT NULL,
    work_order_set VARCHAR(128) NOT NULL,
    part_no VARCHAR(80) NOT NULL,
    part_name VARCHAR(255),
    supplier VARCHAR(255),
    shortage_qty DOUBLE NOT NULL DEFAULT 0,
    available_qty DOUBLE NOT NULL DEFAULT 0,
    safety_stock DOUBLE NOT NULL DEFAULT 0,
    owner VARCHAR(64),
    status VARCHAR(32) NOT NULL DEFAULT 'draft',
    supplier_reply LONGTEXT,
    reply_at VARCHAR(40) NULL,
    arrival_qty DOUBLE NULL,
    arrival_at VARCHAR(40) NULL,
    closed_at VARCHAR(40) NULL,
    close_note LONGTEXT,
    writeback_mode VARCHAR(16) NOT NULL DEFAULT 'demo',
    sla_due_at VARCHAR(40) NULL,
    created_at VARCHAR(40) NOT NULL,
    updated_at VARCHAR(40) NOT NULL,
    history_json LONGTEXT,
    PRIMARY KEY (id),
    UNIQUE KEY ux_followup_id (followup_id),
    KEY idx_followup_status (status),
    KEY idx_followup_set (work_order_set)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""


class FollowupError(Exception):
    """催交状态机基础异常。"""


class FollowupNotFound(FollowupError):
    """催交行不存在。"""


class InvalidFollowupTransition(FollowupError):
    """非法状态流转（不做"尽力而为"）。"""


class FollowupDataError(FollowupError):
    """缺口清单数据不可用（权限/查询失败）——如实失败，不编造缺口。"""


# ---------------- 时间 / 库路径 ----------------


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def _scope_for_target(target: str) -> str:
    """按目标名判库域：演示库名含 ``demo``（``demo_governance.db``）→ demo。"""
    name = str(target).replace("\\", "/").rsplit("/", 1)[-1].lower()
    return "demo" if "demo" in name else "governance"


def resolve_followup_target(
        db_path: Optional[Union[str, Path]] = None) -> tuple[str, str]:
    """解析催交库目标（SQLite 路径 / MySQL URL）与库域（``demo`` / ``governance``）。

    D10 双库分工不变：
    - 显式参数 / ``MFG_FOLLOWUP_DB`` 优先；
    - 演示态（``MFG_DEMO_MODE`` 默认开）→ ``DEMO_DB_PATH``；
    - 真实态 → ``settings.WORKFLOW_DB``（显式配成 MySQL 时原样返回）。
    """
    if db_path is not None:
        target = str(db_path)
        return target, _scope_for_target(target)

    explicit = (os.getenv("MFG_FOLLOWUP_DB") or "").strip()
    if explicit:
        return explicit, _scope_for_target(explicit)

    from ..demo.config_demo import DEMO_DB_PATH, DEMO_MODE

    if DEMO_MODE:
        return str(DEMO_DB_PATH), "demo"

    url = str(getattr(settings, "WORKFLOW_DB", "") or "")
    return url, "governance"


def resolve_followup_db(db_path: Optional[Union[str, Path]] = None) -> tuple[str, str]:
    """兼容入口：只接受 SQLite 目标，返回 ``(文件路径, 库域)``。

    MySQL 目标用 :func:`resolve_followup_target`（模块内 ``_repo`` 按方言分派）。
    """
    target, scope = resolve_followup_target(db_path)
    assert_sqlite_backed(target)
    return sqlite_path_from_url(target), scope


def followup_id_for(part_no: str, supplier: str, work_order_set: str) -> str:
    """幂等键 → 稳定 id：``(part_no, supplier, work_order_set)``。"""
    raw = f"{part_no.strip()}|{supplier.strip()}|{work_order_set.strip()}"
    return "fo-" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


def _connect(db_path: str) -> sqlite3.Connection:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=10.0)
    conn.row_factory = sqlite3.Row
    return conn


# ---------------- 契约层 ----------------


class ProcurementFollowupRepository(ABC):
    """催交状态机仓储契约（SQLite 实现先行；R13 接 MySQL/ERP 时换实现即可）。"""

    @abstractmethod
    def ensure_schema(self) -> None: ...

    @abstractmethod
    def get(self, followup_id: str) -> Optional[dict[str, Any]]: ...

    @abstractmethod
    def list_rows(self, *, status: Optional[str] = None,
                  work_order_set: Optional[str] = None,
                  limit: int = 200) -> list[dict[str, Any]]: ...

    @abstractmethod
    def insert(self, row: dict[str, Any]) -> dict[str, Any]: ...

    @abstractmethod
    def update(self, followup_id: str, fields: dict[str, Any],
               event: Optional[dict[str, Any]] = None) -> dict[str, Any]: ...


class SQLiteProcurementFollowupRepository(ProcurementFollowupRepository):
    """SQLite 实现（落治理轨；``assert_sqlite_backed`` 防线在解析期已过）。"""

    def __init__(self, db_path: str, scope: str = "governance") -> None:
        self.db_path = db_path
        self.scope = scope

    # -- schema --
    def ensure_schema(self) -> None:
        conn = _connect(self.db_path)
        try:
            with conn:
                conn.executescript(_DDL)
        finally:
            conn.close()

    # -- 读 --
    def get(self, followup_id: str) -> Optional[dict[str, Any]]:
        self.ensure_schema()
        conn = _connect(self.db_path)
        try:
            row = conn.execute(
                f"SELECT * FROM {FOLLOWUP_TABLE} WHERE followup_id = ?",
                (followup_id,)).fetchone()
        finally:
            conn.close()
        return _row_to_dict(row) if row else None

    def list_rows(self, *, status: Optional[str] = None,
                  work_order_set: Optional[str] = None,
                  limit: int = 200) -> list[dict[str, Any]]:
        self.ensure_schema()
        sql = f"SELECT * FROM {FOLLOWUP_TABLE} WHERE 1=1"
        params: list[Any] = []
        if status:
            sql += " AND status = ?"
            params.append(status)
        if work_order_set:
            sql += " AND work_order_set = ?"
            params.append(work_order_set)
        sql += " ORDER BY created_at DESC, id DESC LIMIT ?"
        params.append(max(1, min(int(limit), 500)))
        conn = _connect(self.db_path)
        try:
            rows = conn.execute(sql, params).fetchall()
        finally:
            conn.close()
        return [_row_to_dict(r) for r in rows]

    # -- 写 --
    def insert(self, row: dict[str, Any]) -> dict[str, Any]:
        self.ensure_schema()
        payload = _normalize_row(row, scope=self.scope)
        payload["history_json"] = json.dumps(payload.pop("history"), ensure_ascii=False)
        columns = list(payload)
        conn = _connect(self.db_path)
        try:
            with conn:
                conn.execute(
                    f"INSERT INTO {FOLLOWUP_TABLE} ({', '.join(columns)}) "
                    f"VALUES ({', '.join('?' for _ in columns)})",
                    [payload[c] for c in columns])
        finally:
            conn.close()
        return self.get(str(payload["followup_id"])) or {}

    def update(self, followup_id: str, fields: dict[str, Any],
               event: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        current = self.get(followup_id)
        if not current:
            raise FollowupNotFound(f"催交单不存在: {followup_id}")
        history = list(current.get("history") or [])
        if event:
            history.append(event)
        payload = dict(fields)
        payload["updated_at"] = _iso(_now())
        payload["history_json"] = json.dumps(history, ensure_ascii=False)
        assignments = ", ".join(f"{k} = ?" for k in payload)
        conn = _connect(self.db_path)
        try:
            with conn:
                conn.execute(
                    f"UPDATE {FOLLOWUP_TABLE} SET {assignments} WHERE followup_id = ?",
                    [*payload.values(), followup_id])
        finally:
            conn.close()
        return self.get(followup_id) or {}


class SQLAlchemyProcurementFollowupRepository(ProcurementFollowupRepository):
    """跨方言实现：SQLite 复用原生实现，MySQL 用命名参数与 InnoDB DDL。"""

    def __init__(self, db_url: str, scope: str = "governance") -> None:
        self.db_url = db_url
        self.scope = scope
        self.engine = create_engine(db_url, pool_pre_ping=True, future=True)
        self.ensure_schema()

    def ensure_schema(self) -> None:
        dialect = self.engine.dialect.name
        if dialect == "sqlite":
            SQLiteProcurementFollowupRepository(
                sqlite_path_from_url(self.db_url), self.scope).ensure_schema()
            return
        if dialect != "mysql":
            raise NotImplementedError(
                f"催交仓储暂只支持 SQLite / MySQL，收到方言 {dialect!r}")
        with self.engine.begin() as conn:
            conn.execute(text(MYSQL_FOLLOWUP_DDL))

    def get(self, followup_id: str) -> Optional[dict[str, Any]]:
        with self.engine.connect() as conn:
            row = conn.execute(
                text(f"SELECT * FROM {FOLLOWUP_TABLE} WHERE followup_id = :fid"),
                {"fid": followup_id}).mappings().first()
        return _row_to_dict(dict(row)) if row else None

    def list_rows(self, *, status: Optional[str] = None,
                  work_order_set: Optional[str] = None,
                  limit: int = 200) -> list[dict[str, Any]]:
        sql = f"SELECT * FROM {FOLLOWUP_TABLE} WHERE 1 = 1"
        params: dict[str, Any] = {}
        if status:
            sql += " AND status = :status"
            params["status"] = status
        if work_order_set:
            sql += " AND work_order_set = :set"
            params["set"] = work_order_set
        sql += " ORDER BY created_at DESC, id DESC LIMIT :lim"
        params["lim"] = max(1, min(int(limit), 500))
        with self.engine.connect() as conn:
            rows = conn.execute(text(sql), params).mappings().all()
        return [_row_to_dict(dict(r)) for r in rows]

    def insert(self, row: dict[str, Any]) -> dict[str, Any]:
        payload = _normalize_row(row, scope=self.scope)
        payload["history_json"] = json.dumps(payload.pop("history"), ensure_ascii=False)
        columns = list(payload)
        with self.engine.begin() as conn:
            conn.execute(
                text(f"INSERT INTO {FOLLOWUP_TABLE} ({', '.join(columns)}) "
                     f"VALUES ({', '.join(':' + c for c in columns)})"),
                payload)
        return self.get(str(payload["followup_id"])) or {}

    def update(self, followup_id: str, fields: dict[str, Any],
               event: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        current = self.get(followup_id)
        if not current:
            raise FollowupNotFound(f"催交单不存在: {followup_id}")
        history = list(current.get("history") or [])
        if event:
            history.append(event)
        payload = dict(fields)
        payload["updated_at"] = _iso(_now())
        payload["history_json"] = json.dumps(history, ensure_ascii=False)
        payload["fid"] = followup_id
        assignments = ", ".join(f"{k} = :{k}" for k in payload if k != "fid")
        with self.engine.begin() as conn:
            conn.execute(
                text(f"UPDATE {FOLLOWUP_TABLE} SET {assignments} "
                     f"WHERE followup_id = :fid"), payload)
        return self.get(followup_id) or {}


# ---------------- 行归一化 ----------------


def _row_to_dict(row: Mapping[str, Any]) -> dict[str, Any]:
    data = dict(row)
    try:
        data["history"] = json.loads(data.get("history_json") or "[]")
    except json.JSONDecodeError:
        data["history"] = []
    data["demo_writeback"] = data.get("writeback_mode") == "demo"
    data["writeback_label"] = ("演示回写（状态存演示治理库，未写回 ERP）"
                              if data["demo_writeback"]
                              else "治理轨状态（未写回 ERP）")
    return data


def _normalize_row(row: dict[str, Any], *, scope: str) -> dict[str, Any]:
    now = _iso(_now())
    out: dict[str, Any] = {
        "followup_id": str(row["followup_id"]),
        "work_order_set": str(row.get("work_order_set") or ""),
        "part_no": str(row.get("part_no") or ""),
        "part_name": str(row.get("part_name") or ""),
        "supplier": str(row.get("supplier") or ""),
        "shortage_qty": float(row.get("shortage_qty") or 0),
        "available_qty": float(row.get("available_qty") or 0),
        "safety_stock": float(row.get("safety_stock") or 0),
        "owner": str(row.get("owner") or DEFAULT_OWNER),
        "status": str(row.get("status") or "draft"),
        "supplier_reply": str(row.get("supplier_reply") or ""),
        "reply_at": row.get("reply_at"),
        "arrival_qty": row.get("arrival_qty"),
        "arrival_at": row.get("arrival_at"),
        "closed_at": row.get("closed_at"),
        "close_note": str(row.get("close_note") or ""),
        "writeback_mode": "demo" if scope == "demo" else "governance",
        "sla_due_at": row.get("sla_due_at") or _iso(_now() + timedelta(hours=DEFAULT_SLA_HOURS)),
        "created_at": str(row.get("created_at") or now),
        "updated_at": now,
        "history": list(row.get("history") or []),
    }
    return out


def _repo(db_path: Optional[Union[str, Path]] = None
          ) -> ProcurementFollowupRepository:
    target, scope = resolve_followup_target(db_path)
    if is_sqlite_target(target):
        return SQLiteProcurementFollowupRepository(
            sqlite_path_from_url(target), scope=scope)
    return SQLAlchemyProcurementFollowupRepository(target, scope=scope)


def _outbox_path(repo: ProcurementFollowupRepository) -> str:
    """回写审计台账：SQLite 与治理库同目录；MySQL 用 ``MFG_WRITEBACK_OUTBOX``。

    未配置时返回空串（仅进程内留存），不往容器工作目录乱写文件。
    """
    db_path = str(getattr(repo, "db_path", "") or "")
    if db_path and db_path != ":memory:":
        return str(Path(db_path).with_name("writeback_outbox.jsonl"))
    return (os.getenv("MFG_WRITEBACK_OUTBOX") or "").strip()


# ---------------- 缺口清单（数据事实，不编造） ----------------


def build_followup_gaps(run_query: Callable[[str, dict], list[dict]],
                        *, limit: int = 20) -> dict[str, Any]:
    """从注册查询组装缺口清单（料号 / 缺口数 / 供应商 / 催交人）。

    数据口径（如实写进返回的 ``basis``）：
    - 缺口数 = ``material_shortage``：``safety_stock - 可用量``（低于安全库存口径）；
    - 供应商 = ``supplier_followup``：按料号取批次最多的一家（并列取字典序第一）；
    - **未做 BOM 逐单齐套**：BOM 数据不在业务库（``data/demo_storyline/boms.jsonl``
      才有），本模块不假装逐单比对，缺口按料号粒度呈现；
    - 查询失败/权限不足 → 抛 ``FollowupDataError``（绝不返回空清单冒充"齐套"）。
    """
    try:
        shortage_rows = run_query("material_shortage", {}) or []
        supplier_rows = run_query("supplier_followup", {}) or []
    except Exception as exc:  # noqa: BLE001 - 统一转数据缺口异常
        raise FollowupDataError(f"缺口清单查询不可用：{exc}") from exc

    by_part: dict[str, dict[str, Any]] = {}
    for row in supplier_rows:
        part = str(row.get("part_no") or "").strip()
        if not part:
            continue
        supplier = str(row.get("supplier") or "").strip()
        cnt = int(row.get("batch_cnt") or 0)
        cur = by_part.get(part)
        if cur is None or cnt > cur["batch_cnt"] or (
                cnt == cur["batch_cnt"] and supplier < cur["supplier"]):
            by_part[part] = {"supplier": supplier, "batch_cnt": cnt}

    gaps: list[dict[str, Any]] = []
    for row in shortage_rows:
        part = str(row.get("part_no") or "").strip()
        if not part:
            continue
        supplier = (by_part.get(part) or {}).get("supplier", "")
        gaps.append({
            "part_no": part,
            "part_name": str(row.get("part_name") or ""),
            "shortage_qty": float(row.get("shortage_qty") or 0),
            "available_qty": float(row.get("available_qty") or 0),
            "safety_stock": float(row.get("safety_stock") or 0),
            "supplier": supplier,
            "owner": DEFAULT_OWNER,
        })
    gaps.sort(key=lambda g: (-g["shortage_qty"], g["part_no"]))
    top = gaps[:max(1, int(limit))]

    # 供应商补全：supplier_followup 只覆盖有来料批次的料号；材料库存表本身带
    # supplier（material_stock_by_part），按料号补全，仍取不到才留空并登记缺口。
    # 补全查询失败不影响缺口清单本身（供应商是增强字段，不是判定依据）。
    for gap in top:
        if gap["supplier"]:
            continue
        try:
            stock_rows = run_query("material_stock_by_part",
                                   {"part_no": gap["part_no"]}) or []
        except Exception:  # noqa: BLE001 - 增强查询失败不阻断派发
            continue
        names = [str(r.get("supplier") or "").strip() for r in stock_rows]
        names = [n for n in names if n]
        if names:
            gap["supplier"] = max(sorted(set(names)), key=names.count)

    return {
        "gaps": top,
        "total_gaps": len(gaps),
        "truncated": len(gaps) > len(top),
        "missing_supplier": [g["part_no"] for g in top if not g["supplier"]],
        "basis": ("缺口＝低于安全库存（material_shortage）；供应商取该料号库存/来料批次"
                  "中最常见的一家（material_stock_by_part / supplier_followup）；"
                  "未做 BOM 逐单齐套（BOM 未入业务库）"),
    }


# ---------------- 状态机操作 ----------------


def _history_entry(action: str, actor: str, from_status: str,
                   to_status: str, note: str = "") -> dict[str, Any]:
    return {"ts": _iso(_now()), "action": action, "actor": actor,
            "from": from_status, "to": to_status, "note": note}


def ensure_followup_table(db_path: Optional[Union[str, Path]] = None) -> str:
    """幂等建表；返回实际使用的目标（SQLite 路径 / MySQL URL）。"""
    target, _ = resolve_followup_target(db_path)
    _repo(target).ensure_schema()
    return target


def plan_followups(work_order_set: str, gaps: Iterable[dict[str, Any]],
                   *, actor: str = "",
                   db_path: Optional[Union[str, Path]] = None
                   ) -> list[dict[str, Any]]:
    """缺口清单 → draft 行（幂等：同键已存在则原样返回，不覆盖现场进度）。"""
    repo = _repo(db_path)
    out: list[dict[str, Any]] = []
    for gap in gaps:
        fid = followup_id_for(str(gap.get("part_no") or ""),
                              str(gap.get("supplier") or ""),
                              work_order_set)
        existing = repo.get(fid)
        if existing:
            out.append(existing)
            continue
        row = _normalize_row({
            "followup_id": fid,
            "work_order_set": work_order_set,
            "part_no": gap.get("part_no"),
            "part_name": gap.get("part_name"),
            "supplier": gap.get("supplier"),
            "shortage_qty": gap.get("shortage_qty"),
            "available_qty": gap.get("available_qty"),
            "safety_stock": gap.get("safety_stock"),
            "owner": gap.get("owner"),
            "status": "draft",
            "history": [_history_entry("draft", actor, "", "draft",
                                       "缺口清单生成")],
        }, scope=repo.scope)
        out.append(repo.insert(row))
    return out


def dispatch_followups(work_order_set: str, gaps: Iterable[dict[str, Any]],
                       *, actor: str = "",
                       db_path: Optional[Union[str, Path]] = None
                       ) -> list[dict[str, Any]]:
    """一键派发：draft → dispatched（幂等：已派发的不重复派发）。"""
    planned = plan_followups(work_order_set, gaps, actor=actor, db_path=db_path)
    out: list[dict[str, Any]] = []
    for row in planned:
        if row.get("status") == "draft":
            row = transition(str(row["followup_id"]), "dispatch", actor=actor,
                             note="派发到采购员待办", db_path=db_path)
        out.append(row)
    return out


def list_followups(*, status: Optional[str] = None,
                   work_order_set: Optional[str] = None,
                   limit: int = 200,
                   db_path: Optional[Union[str, Path]] = None
                   ) -> list[dict[str, Any]]:
    if status and status not in STATUSES:
        raise FollowupError(f"未知状态: {status}")
    return _repo(db_path).list_rows(status=status,
                                    work_order_set=work_order_set,
                                    limit=limit)


def get_followup(followup_id: str,
                 db_path: Optional[Union[str, Path]] = None) -> dict[str, Any]:
    row = _repo(db_path).get(followup_id)
    if not row:
        raise FollowupNotFound(f"催交单不存在: {followup_id}")
    return row


def transition(followup_id: str, action: str, *, actor: str = "",
               note: str = "", arrival_qty: Optional[float] = None,
               db_path: Optional[Union[str, Path]] = None) -> dict[str, Any]:
    """执行一次状态流转（非法流转抛 ``InvalidFollowupTransition``，409）。

    ``action`` ∈ ``ACTION_TARGETS``；``arrival`` 在演示库写 ``writeback_mode=demo``，
    响应行带 ``demo_writeback`` 与不可关闭的徽标文案。
    """
    target = ACTION_TARGETS.get(action)
    if not target:
        raise FollowupError(f"未知动作: {action}")
    repo = _repo(db_path)
    row = repo.get(followup_id)
    if not row:
        raise FollowupNotFound(f"催交单不存在: {followup_id}")
    current = str(row.get("status") or "")
    if target not in STATUS_TRANSITIONS.get(current, ()):
        raise InvalidFollowupTransition(
            f"催交单 {followup_id} 不允许从 {current} 流转到 {target}")
    if action == "close" and not str(note or "").strip():
        raise FollowupError("关闭催交单必须填写结论（到货结果/后续安排）")

    now = _iso(_now())
    fields: dict[str, Any] = {"status": target}
    if action == "reply":
        if not str(note or "").strip():
            raise FollowupError("供应商回复必须填写回复内容")
        fields.update({"supplier_reply": note.strip(), "reply_at": now})
    elif action == "arrival":
        fields.update({"arrival_at": now,
                       "arrival_qty": float(arrival_qty) if arrival_qty is not None else None})
        if note:
            fields["close_note"] = str(note).strip()
    elif action == "close":
        fields.update({"closed_at": now, "close_note": str(note).strip()})
    elif action == "escalate":
        fields["close_note"] = str(note or "超 SLA 升级").strip()

    event = _history_entry(action, actor, current, target,
                           str(note or "").strip())
    receipt: Optional[dict[str, Any]] = None
    if action == "arrival":
        # R13 / D14：到货确认走连接器回写（tier=2：落地 + 事后审计），
        # 回执同时进 history（可审计）与响应行（前端展示演示回写徽标事实）。
        receipt = emit_arrival_receipt(
            row,
            outbox_path=_outbox_path(repo),
            actor=actor, arrival_qty=arrival_qty)
        event["receipt"] = receipt

    updated = repo.update(followup_id, fields, event=event)
    if receipt is not None:
        updated["writeback_receipt"] = receipt
    return updated


def escalate_overdue(*, db_path: Optional[Union[str, Path]] = None,
                     now: Optional[datetime] = None) -> list[dict[str, Any]]:
    """把超过 SLA 的非终态催交单升级为 escalated（复用本模块 SLA 字段）。"""
    base = now or _now()
    rows = _repo(db_path).list_rows(limit=500)
    out: list[dict[str, Any]] = []
    for row in rows:
        if row.get("status") in TERMINAL_STATUSES or row.get("status") == "escalated":
            continue
        due = str(row.get("sla_due_at") or "")
        if not due:
            continue
        try:
            due_dt = datetime.fromisoformat(due)
        except ValueError:
            continue
        if due_dt.tzinfo is None:
            due_dt = due_dt.replace(tzinfo=timezone.utc)
        if due_dt > base:
            continue
        try:
            out.append(transition(str(row["followup_id"]), "escalate",
                                  actor="system", note="超 SLA 未闭环",
                                  db_path=db_path))
        except FollowupError as exc:  # 并发下已被处理，不阻断其余行
            logger.warning("[followup] 升级跳过 %s: %s", row.get("followup_id"), exc)
    return out
