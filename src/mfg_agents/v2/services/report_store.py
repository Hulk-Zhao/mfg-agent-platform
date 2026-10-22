"""报告中心持久层（SQLite 单表 · 报告一等对象的真相源）。

架构位置：与 ``WorkflowEngine`` **同库**（``settings.WORKFLOW_DB``）——报告要挂
工单 / 会签 / 派发留痕，跨库会让"报告→工单"的深链在重启后悬空。

三条纪律：

1. **幂等写入**：``report_id`` 是主键，重复归档（缓存命中重放、前端重试）
   只做 UPDATE，不产生重复报告。
2. **状态机非法流转直接拒绝**（``InvalidReportTransition``），不"尽力而为"。
3. **JSON 列存结构化载荷**：``internal_brief / provenance / linked / versions``
   等以 JSON 文本落库，读时还原成 Python 对象——单表即可承载完整报告，
   避免为一个演示模块拆出七八张关联表。

与 ``spc_store.py`` 同构（模块级函数 + 幂等建表 + 可选 ``db_path`` 覆盖），
不接入 SQLAlchemy；非 SQLite 目标由 :func:`~services.workflow.assert_sqlite_backed`
在解析路径时 fail-fast（与治理写路径同一道防线）。
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Mapping, Optional, Union

from ...common.config import settings
from ...common.platform_schema import sqlite_path_from_url
from .report_repository import (
    REPORTS_TABLE,
    ReportRepository,
    SQLiteReportRepository,
)
from .workflow import assert_sqlite_backed

logger = logging.getLogger(__name__)

__all__ = [
    "REPORTS_TABLE",
    "STATUS_TRANSITIONS",
    "TERMINAL_STATUSES",
    "VISIBILITY_VALUES",
    "ReportStoreError",
    "ReportNotFound",
    "InvalidReportTransition",
    "ReportRepository",
    "resolve_report_db",
    "ensure_reports_table",
    "upsert_report",
    "list_reports",
    "get_report",
    "append_version",
    "transition_status",
    "count_by_status",
]

#: 报告状态机（``available`` 为终态之外的合法去向）。仿 ``CaseStore.STATUS_TRANSITIONS``。
STATUS_TRANSITIONS: dict[str, tuple[str, ...]] = {
    "draft": ("pending_review", "archived"),
    "pending_review": ("approved", "rejected", "draft"),
    "rejected": ("draft", "pending_review"),
    "approved": ("dispatched", "archived"),
    "dispatched": ("archived",),
    "archived": (),
}

TERMINAL_STATUSES = frozenset({"archived"})

#: 可见范围：``internal`` 内网岗位；``customer`` 含对客脱敏产物（额外对业务/客服开放）。
VISIBILITY_VALUES = ("internal", "customer")

#: 文本列（直接落库）。
_TEXT_FIELDS: tuple[str, ...] = (
    "report_id", "scenario_key", "run_id", "source_module", "title",
    "visibility", "status", "risk_level", "actor", "customer_report",
    "governing_slot", "created_at", "updated_at",
)

#: JSON 列 → (读出的字典键, 空值)
_JSON_FIELDS: dict[str, tuple[str, Any]] = {
    "internal_brief_json": ("internal_brief", {}),
    "mask_map_json": ("mask_map", []),
    "provenance_json": ("provenance", []),
    "data_gaps_json": ("data_gaps", []),
    "linked_json": ("linked", {}),
    "versions_json": ("versions", []),
}

# 注：物理表结构 / 列顺序统一下沉到 ``report_repository``（D15 契约），
# 本模块只保留业务语义（状态机 / JSON 还原 / 归一化）。


class ReportStoreError(Exception):
    """报告存储层异常基类。"""


class ReportNotFound(ReportStoreError):
    """报告不存在。"""


class InvalidReportTransition(ReportStoreError):
    """非法状态流转（如 ``archived → dispatched``）。"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def resolve_report_db(db_path: Optional[Union[str, Path]] = None) -> str:
    """解析报告库路径；缺省 = 与治理库同库（``settings.WORKFLOW_DB``）。

    ``WORKFLOW_DB`` 的解析规则（``config._resolve_workflow_db``）已保证「显式配置 →
    原样 / DB_URL 是 SQLite → 沿用 / 否则回落本地 SQLite 文件」，因此这里再补一道
    ``assert_sqlite_backed``：把 MySQL URL 当文件路径交给 ``sqlite3.connect`` 会
    在 Windows 直接抛 ``OSError``、在 Linux 静默建出错位空库，两种结局都是事故。
    """
    if db_path is not None:
        target = str(db_path)
        assert_sqlite_backed(target)
        return sqlite_path_from_url(target)
    url = str(getattr(settings, "WORKFLOW_DB", "") or "")
    assert_sqlite_backed(url)
    return sqlite_path_from_url(url)


def _repo(db_path: Optional[Union[str, Path]] = None) -> ReportRepository:
    """解析路径并构造仓储（D15：模块函数不再持有裸 ``_connect``）。"""
    return SQLiteReportRepository(resolve_report_db(db_path))


def ensure_reports_table(db_path: Optional[Union[str, Path]] = None) -> str:
    """幂等建表（含索引）；返回实际使用的库路径。"""
    target = resolve_report_db(db_path)
    SQLiteReportRepository(target).ensure_schema()
    return target


# ---------- 序列化 ----------


def _dump(value: Any, fallback: Any) -> str:
    if value is None:
        value = fallback
    try:
        return json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError):
        return json.dumps(fallback, ensure_ascii=False)


def _row_to_dict(row: Mapping[str, Any]) -> dict[str, Any]:
    """仓储行 → API 字典（JSON 列还原为对象；``degraded`` 还原为 bool）。"""
    data: dict[str, Any] = {name: row[name] for name in _TEXT_FIELDS}
    data["degraded"] = bool(row["degraded"])
    for column, (key, empty) in _JSON_FIELDS.items():
        raw = row[column]
        try:
            data[key] = json.loads(raw) if raw else empty
        except (TypeError, ValueError):
            data[key] = empty
    return data


def _normalize(row: Mapping[str, Any], *, report_id: str) -> dict[str, Any]:
    """构造一条完整可落库的行（缺列补默认值，避免 NOT NULL 撞空）。"""
    now = _now()
    created = str(row.get("created_at") or "") or now
    visibility = str(row.get("visibility") or "internal").strip() or "internal"
    if visibility not in VISIBILITY_VALUES:
        visibility = "internal"
    status = str(row.get("status") or "draft").strip() or "draft"
    if status not in STATUS_TRANSITIONS:
        raise InvalidReportTransition(f"非法报告状态: {status!r}")

    record: dict[str, Any] = {
        "report_id": report_id,
        "scenario_key": str(row.get("scenario_key") or ""),
        "run_id": str(row.get("run_id") or ""),
        "source_module": str(row.get("source_module") or ""),
        "title": str(row.get("title") or "") or "未命名报告",
        "visibility": visibility,
        "status": status,
        "risk_level": str(row.get("risk_level") or ""),
        "degraded": 1 if row.get("degraded") else 0,
        "actor": str(row.get("actor") or ""),
        "customer_report": str(row.get("customer_report") or ""),
        "governing_slot": str(row.get("governing_slot") or ""),
        "created_at": created,
        "updated_at": now,
    }
    for column, (key, empty) in _JSON_FIELDS.items():
        record[column] = _dump(row.get(key), empty)
    return record


# ---------- 写入 ----------


def upsert_report(row: Mapping[str, Any],
                  db_path: Optional[Union[str, Path]] = None) -> str:
    """按 ``report_id`` 幂等写入/更新一条报告；返回 ``report_id``。"""
    report_id = str((row or {}).get("report_id") or "").strip()
    if not report_id:
        raise ReportStoreError("报告缺少 report_id")
    record = _normalize(row, report_id=report_id)
    repo = _repo(db_path)
    repo.ensure_schema()

    # 已存在的 created_at 不被覆盖（修订不篡改首次归档时间）
    existing = repo.fetch(report_id)
    if existing and not row.get("created_at"):
        record["created_at"] = str(existing["created_at"] or record["created_at"])
    repo.upsert(record)
    return report_id


def append_version(report_id: str, version: Mapping[str, Any],
                   db_path: Optional[Union[str, Path]] = None) -> dict[str, Any]:
    """追加一条版本留痕并更新时间戳；返回更新后的报告。"""
    report = get_report(report_id, db_path)
    versions = list(report.get("versions") or [])
    entry = {k: v for k, v in dict(version or {}).items() if v is not None}
    entry.setdefault("time", _now())
    versions.append(entry)
    report["versions"] = versions
    upsert_report(report, db_path)
    return get_report(report_id, db_path)


def transition_status(report_id: str, to_status: str, *, actor: str = "",
                      note: str = "", extra: Optional[Mapping[str, Any]] = None,
                      db_path: Optional[Union[str, Path]] = None) -> dict[str, Any]:
    """状态机流转（校验合法去向 + 追加版本留痕）。"""
    target_status = str(to_status or "").strip()
    report = get_report(report_id, db_path)
    current = str(report.get("status") or "draft")
    allowed = STATUS_TRANSITIONS.get(current, ())
    if target_status not in allowed:
        raise InvalidReportTransition(
            f"报告 {report_id} 不能从 {current} 流转到 {target_status!r}"
            f"（允许：{'、'.join(allowed) or '无'}）")

    report["status"] = target_status
    if actor:
        report["actor"] = actor
    if extra:
        for key, value in dict(extra).items():
            if value is not None:
                report[key] = value
    versions = list(report.get("versions") or [])
    versions.append({
        "ver": f"status→{target_status}",
        "time": _now(),
        "actor": actor or "系统",
        "badge": target_status,
        "note": str(note or ""),
    })
    report["versions"] = versions
    upsert_report(report, db_path)
    return get_report(report_id, db_path)


# ---------- 读取 ----------


def get_report(report_id: str,
               db_path: Optional[Union[str, Path]] = None) -> dict[str, Any]:
    """按 id 取一条报告；不存在 → ``ReportNotFound``。"""
    target_id = str(report_id or "").strip()
    if not target_id:
        raise ReportNotFound("报告 id 为空")
    repo = _repo(db_path)
    repo.ensure_schema()
    row = repo.fetch(target_id)
    if row is None:
        raise ReportNotFound(f"报告不存在: {target_id}")
    return _row_to_dict(row)


def list_reports(*, scenario_key: str = "", source_module: str = "",
                 status: str = "", visibility: str = "", risk_level: str = "",
                 q: str = "", limit: int = 50, offset: int = 0,
                 db_path: Optional[Union[str, Path]] = None
                 ) -> tuple[list[dict[str, Any]], int]:
    """筛选 + 关键字搜索 + 分页；返回 ``(rows, total)``（total 为筛选后的总数）。

    关键字命中范围：标题 / 场景 / 来源模块 / 内部简报全文——够用且不引入全文索引。
    """
    repo = _repo(db_path)
    repo.ensure_schema()
    rows, total = repo.list_rows(
        scenario_key=scenario_key, source_module=source_module, status=status,
        visibility=visibility, risk_level=risk_level, q=q,
        limit=limit, offset=offset)
    return [_row_to_dict(row) for row in rows], total


def count_by_status(db_path: Optional[Union[str, Path]] = None) -> dict[str, int]:
    """各状态报告数 + 含数据缺口数（供顶部 KPI 概览条）。"""
    repo = _repo(db_path)
    repo.ensure_schema()
    summary = repo.summary()
    by_status = summary.get("by_status") or {}
    counts = {status: 0 for status in STATUS_TRANSITIONS}
    for status, count in by_status.items():
        counts[str(status)] = int(count)
    counts["total"] = sum(counts[s] for s in STATUS_TRANSITIONS)
    counts["with_gaps"] = int(summary.get("with_gaps") or 0)
    return counts
