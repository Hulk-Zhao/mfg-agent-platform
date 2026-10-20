"""现场看板推送层（SQLite 单表 ·「客诉 → 责任工段现场看板」的推送真相源）。

架构位置：与 ``WorkflowEngine`` / ``report_store`` **同库**（``settings.WORKFLOW_DB``）——
推送行要挂客诉 8D 工单（现场处置完成后回流 8D 关闭确认），跨库会让深链在重启后悬空。

职责边界（三段各管一段，避免把判定散到三处）：

1. **归因**（本模块）：``resolve_stage`` 把工段判出来——先认显式工段，再认产线前缀，
   最后按批次查 ``quality_defect`` 反推。判不出来**如实返回空串**（调用方转人工兜底），
   **绝不猜一个工段硬推**——推错工段比不推更贵。
2. **投递**（``services/notification.py::BoardNotificationChannel``）：命中工段白名单的
   target 才落一条推送行；非工段 target（如「质量总监」这类岗位角色）静默跳过。
3. **状态机**（本模块）：``pushed → acked → closed``，非法流转直接拒绝
   （``InvalidSiteBoardTransition``），与 ``report_store`` 同口径。

与 ``spc_store.py`` / ``report_store.py`` 同构（模块级函数 + 幂等建表 + 可选 ``db_path``），
不接入 SQLAlchemy；非 SQLite 目标由 ``workflow.assert_sqlite_backed`` fail-fast。
"""
from __future__ import annotations

import hashlib
import logging
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Union

from ...common.config import settings
from ...common.platform_schema import is_sqlite_target, sqlite_path_from_url
from ..core.dimensions import MFG_STAGES
from .workflow import assert_sqlite_backed

logger = logging.getLogger(__name__)

__all__ = [
    "SITE_BOARD_TABLE",
    "STAGES",
    "STAGE_LINE_PREFIX",
    "STATUS_TRANSITIONS",
    "TERMINAL_STATUSES",
    "SiteBoardError",
    "PushNotFound",
    "InvalidSiteBoardTransition",
    "resolve_stage",
    "resolve_stage_from_batch",
    "push_id_for",
    "resolve_site_db",
    "ensure_site_board_table",
    "push_message",
    "dispatch_complaint",
    "make_site_dispatch",
    "board_push_ids",
    "list_pushes",
    "list_by_stage",
    "get_push",
    "transition_status",
    "ack",
    "close",
]

SITE_BOARD_TABLE = "site_board_push"

#: 工段白名单（唯一事实源 = ``core/dimensions.MFG_STAGES``，本模块仅再导出）。
STAGES: tuple[str, ...] = MFG_STAGES

#: 工段 → 产线前缀（与 ``dashboard._MFG_STAGE_LINE_LIKE`` 的 LIKE 模式同源；
#: 不跨服务导入对方私有常量，改用 ``test_site_board`` 的漂移守卫逐项比对）。
STAGE_LINE_PREFIX: dict[str, str] = {
    "SMT-": "SMT", "DIP-": "PCM", "PACK-": "PACK", "ASSY-": "整机",
}

#: 推送行状态机：推送 → 现场接收 → 处置闭环（终态不可再流转）。
STATUS_TRANSITIONS: dict[str, tuple[str, ...]] = {
    "pushed": ("acked",),
    "acked": ("closed",),
    "closed": (),
}

TERMINAL_STATUSES = frozenset({"closed"})

_DDL = """
CREATE TABLE IF NOT EXISTS site_board_push (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    push_id TEXT NOT NULL UNIQUE,
    stage TEXT NOT NULL,
    level TEXT NOT NULL DEFAULT 'P1',
    title TEXT NOT NULL DEFAULT '',
    body TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pushed',
    source TEXT NOT NULL DEFAULT '',
    ref_id TEXT NOT NULL DEFAULT '',
    complaint_no TEXT NOT NULL DEFAULT '',
    batch_no TEXT NOT NULL DEFAULT '',
    customer TEXT NOT NULL DEFAULT '',
    ack_by TEXT NOT NULL DEFAULT '',
    ack_at TEXT,
    closed_by TEXT NOT NULL DEFAULT '',
    closed_at TEXT,
    feedback TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_site_board_stage_status
    ON site_board_push (stage, status);
CREATE INDEX IF NOT EXISTS idx_site_board_complaint
    ON site_board_push (complaint_no);
"""

#: 老库升级（``CREATE TABLE IF NOT EXISTS`` 对已存在的表不生效）：PRAGMA 守卫 + 幂等补列。
#: 客户名单独成列而不是内联进 ``body``，就是为了让读接口能按岗位脱敏它
#: （正文是自由文本，脱敏规则改不动）。
_ALTER_COLUMNS: tuple[tuple[str, str], ...] = (
    ("customer", "ALTER TABLE site_board_push ADD COLUMN customer TEXT DEFAULT ''"),
)

_TEXT_FIELDS: tuple[str, ...] = (
    "push_id", "stage", "level", "title", "body", "status", "source",
    "ref_id", "complaint_no", "batch_no", "customer", "ack_by", "closed_by",
    "feedback", "created_at", "updated_at",
)
_NULLABLE_FIELDS: tuple[str, ...] = ("ack_at", "closed_at")
_ALL_COLUMNS: tuple[str, ...] = _TEXT_FIELDS + _NULLABLE_FIELDS


class SiteBoardError(Exception):
    """现场看板层基础异常。"""


class PushNotFound(SiteBoardError):
    """推送行不存在。"""


class InvalidSiteBoardTransition(SiteBoardError):
    """状态机非法流转（不做"尽力而为"）。"""


# ---------------- 时间与库路径 ----------------


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def resolve_site_db(db_path: Optional[Union[str, Path]] = None) -> str:
    """解析推送库路径；缺省 = 与治理库同库（``settings.WORKFLOW_DB``）。

    ``assert_sqlite_backed`` 是必过的一道防线：把 MySQL URL 当文件路径交给
    ``sqlite3.connect`` 会直接抛 ``OSError`` / 静默建出错位空库（同 ``report_store``）。
    """
    if db_path is not None:
        target = str(db_path)
        assert_sqlite_backed(target)
        return sqlite_path_from_url(target)
    url = str(getattr(settings, "WORKFLOW_DB", "") or "")
    assert_sqlite_backed(url)
    return sqlite_path_from_url(url)


def _main_db_path() -> str:
    """演示库（业务数据）文件路径；非 SQLite 目标（Docker MySQL）返回空串。"""
    url = str(getattr(settings, "DB_URL", "") or "")
    if not url or not is_sqlite_target(url):
        return ""
    return sqlite_path_from_url(url)


def _connect(db_path: str) -> sqlite3.Connection:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=10.0)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_site_board_table(db_path: Optional[Union[str, Path]] = None) -> str:
    """幂等建表（含索引 + 老库补列）；返回实际使用的库路径。"""
    target = resolve_site_db(db_path)
    conn = _connect(target)
    try:
        with conn:
            conn.executescript(_DDL)
            existing = {r[1] for r in conn.execute(
                f"PRAGMA table_info({SITE_BOARD_TABLE})")}
            for column, ddl in _ALTER_COLUMNS:
                if existing and column not in existing:
                    conn.execute(ddl)
                    logger.info("[site_board] 迁移加列: %s.%s",
                                SITE_BOARD_TABLE, column)
    finally:
        conn.close()
    return target


# ---------------- 归因（客诉 → 责任工段） ----------------


def resolve_stage(*, process_stage: Optional[str] = None,
                  line_code: Optional[str] = None,
                  batch_no: Optional[str] = None,
                  main_db_path: Optional[Union[str, Path]] = None) -> str:
    """按「显式工段 → 产线前缀 → 批次反推」的固定顺序判定责任工段。

    三层都判不出来时返回 ``""``（调用方据此转人工兜底），不抛错、不猜值。
    ``main_db_path`` 仅用于批次反推（指向**演示库**；缺省 = ``settings.DB_URL``），
    与推送行落库的治理库（``settings.WORKFLOW_DB``）不是同一个库，别混用。
    """
    stage = str(process_stage or "").strip()
    if stage in STAGES:
        return stage

    line = str(line_code or "").strip().upper()
    if line:
        for prefix, mapped in STAGE_LINE_PREFIX.items():
            if line.startswith(prefix):
                return mapped

    if str(batch_no or "").strip():
        return resolve_stage_from_batch(str(batch_no).strip(),
                                        main_db_path=main_db_path)
    return ""


def resolve_stage_from_batch(batch_no: str,
                             *,
                             main_db_path: Optional[Union[str, Path]] = None,
                             ) -> str:
    """按批次反推工段：取该批次不良量最大的那一行所属工段。

    读演示库用 ``mode=ro`` 只读 URI——业务数据查询通道**只读**是架构红线，
    归因读也照此办事，不给"顺手写一下"留口子。
    批次不存在 / 库不是 SQLite / 表缺列时一律返回 ``""``（降级不报错）。
    """
    batch = str(batch_no or "").strip()
    if not batch:
        return ""
    target = str(main_db_path) if main_db_path is not None else _main_db_path()
    if not target or not Path(target).exists():
        return ""
    try:
        conn = sqlite3.connect(f"file:{target}?mode=ro", uri=True, timeout=5.0)
        conn.row_factory = sqlite3.Row
    except sqlite3.Error as exc:  # noqa: BLE001 - 归因读失败必须降级，不能带崩链路
        logger.warning("[site_board] 批次反推打不开演示库: %s", exc)
        return ""
    try:
        rows = conn.execute(
            "SELECT process_stage AS stage, line_code AS line, "
            "COALESCE(SUM(defect_qty), 0) AS qty "
            "FROM quality_defect WHERE batch_no = ? "
            "GROUP BY process_stage, line_code "
            "ORDER BY qty DESC, line ASC",
            (batch,)).fetchall()
    except sqlite3.Error as exc:  # noqa: BLE001 - 同一降级口径
        logger.warning("[site_board] 批次反推查询失败: %s", exc)
        return ""
    finally:
        conn.close()

    for row in rows:
        stage = resolve_stage(process_stage=row["stage"], line_code=row["line"])
        if stage:
            return stage
    return ""


def push_id_for(stage: str, ref_id: str = "",
                title: str = "", body: str = "") -> str:
    """推送行主键：有业务引用（如客诉单号）用引用，否则用内容指纹。

    内容指纹让"同一通知重放"天然幂等（重放不产生第二行），而不同内容各自成行
    ——工段级通用通知（如停线告知）没有单号可引用，靠它避免互相覆盖。
    """
    token = str(ref_id or "").strip()
    if not token:
        digest = hashlib.sha1(
            f"{title}|{body}".encode("utf-8")).hexdigest()[:12]
        token = f"h{digest}"
    return f"SB-{token}-{stage}"


# ---------------- 写入 ----------------


def push_message(*, stage: str, title: str = "", body: str = "",
                 level: str = "P1", source: str = "",
                 ref_id: str = "", complaint_no: str = "",
                 batch_no: str = "", customer: str = "",
                 push_id: Optional[str] = None,
                 db_path: Optional[Union[str, Path]] = None) -> str:
    """落一条推送行（按 ``push_id`` 幂等）；返回 ``push_id``。

    幂等口径：重复推送（前端重试 / 通知重放）只刷新标题正文与时间戳，
    **不动 status 与现场回填字段**——否则现场刚 ack 完又被重推打回 ``pushed``。
    """
    target_stage = str(stage or "").strip()
    if target_stage not in STAGES:
        raise SiteBoardError(
            f"目标工段非法: {stage!r}（合法值：{'、'.join(STAGES)}）")
    pid = str(push_id or "").strip() or push_id_for(
        target_stage, ref_id=str(ref_id or ""), title=title, body=body)
    now = _now()
    record = {
        "push_id": pid, "stage": target_stage, "level": str(level or "P1"),
        "title": str(title or ""), "body": str(body or ""), "status": "pushed",
        "source": str(source or ""), "ref_id": str(ref_id or ""),
        "complaint_no": str(complaint_no or ""),
        "batch_no": str(batch_no or ""),
        "customer": str(customer or ""),
        "ack_by": "", "ack_at": None, "closed_by": "", "closed_at": None,
        "feedback": "", "created_at": now, "updated_at": now,
    }
    target = ensure_site_board_table(db_path)
    conn = _connect(target)
    try:
        with conn:
            columns = ", ".join(_ALL_COLUMNS)
            placeholders = ", ".join("?" for _ in _ALL_COLUMNS)
            # 冲突时只刷新内容列；status / ack_* / closed_* / feedback 保持现场已推进的进度。
            updates = ", ".join(
                f"{name}=excluded.{name}" for name in
                ("title", "body", "level", "source", "ref_id",
                 "complaint_no", "batch_no", "customer", "updated_at"))
            conn.execute(
                f"INSERT INTO {SITE_BOARD_TABLE} ({columns}) "
                f"VALUES ({placeholders}) "
                f"ON CONFLICT(push_id) DO UPDATE SET {updates}",
                [record.get(name) for name in _ALL_COLUMNS],
            )
    finally:
        conn.close()
    return pid


def dispatch_complaint(notifier: Any, complaint: Mapping[str, Any],
                       *, process_stage: Optional[str] = None,
                       line_code: Optional[str] = None,
                       level: str = "P1",
                       main_db_path: Optional[Union[str, Path]] = None,
                       ) -> dict[str, Any]:
    """客诉 → 责任工段现场看板：归因 + 投递，返回可审计的分派结果。

    走 ``NotificationService`` 投递（架构铁律：禁止各模块自行发送），推送行由
    ``BoardNotificationChannel`` 落**治理库**——本函数只负责「判工段 + 组织卡片
    文案 + 核对结果」，``main_db_path`` 只影响归因读（演示库），不影响推送落库。

    三种结局都不抛错，由调用方按 ``ok`` / ``data_gap`` 决定下一步：
    - 归因不到工段（缺工段/产线/批次线索）→ ``data_gap``，转人工分派；
    - 通道未接现场看板（无推送行）→ ``data_gap``，配置错误必须显式暴露；
    - 正常 → ``ok=True`` + ``push_ids``。
    """
    data = dict(complaint or {})
    no = str(data.get("complaint_no") or "").strip()
    batch = str(data.get("batch_no") or "").strip()
    stage = resolve_stage(
        process_stage=process_stage or data.get("process_stage"),
        line_code=line_code or data.get("line_code"),
        batch_no=batch,
        main_db_path=main_db_path,
    )
    if stage not in STAGES:
        gap = (f"客诉 {no or '（缺单号）'} 未能归因到工段"
               f"（工段/产线/批次线索不足），需人工分派")
        logger.warning("[site_board] %s", gap)
        return {"ok": False, "data_gap": gap, "stage": "",
                "complaint_no": no, "push_ids": []}

    title = f"[客诉] {no or '（缺单号）'} · {stage} 现场处置"
    # 正文**不内联客户名**：客户身份走 ``customer`` 字段，由读接口按岗位脱敏
    # （正文是自由文本，脱敏规则改不动）。现场要的是"哪批 / 什么缺陷 / 怎么处置"。
    body = (
        f"型号 {data.get('product_model') or '—'}｜批次 {batch or '—'}\n"
        f"缺陷 {data.get('defect_desc') or '—'}\n"
        f"请 {stage} 现场确认围堵与措施落地，处置结果回传后闭环"
    )
    notice = notifier.send(
        level, [stage], title, body,
        context={"source": "complaint_8d", "ref_id": no,
                 "complaint_no": no, "batch_no": batch,
                 "customer": str(data.get("customer") or "")},
    )
    push_ids = board_push_ids(notice)
    if not push_ids:
        gap = (f"客诉 {no or '（缺单号）'} 已归因到 {stage}，但通知通道未接入"
               f"现场看板（无推送行落库）")
        logger.warning("[site_board] %s", gap)
        return {"ok": False, "data_gap": gap, "stage": stage,
                "complaint_no": no, "push_ids": []}

    logger.info("[site_board] 客诉 %s → 工段 %s，推送行 %s",
                no or "（缺单号）", stage, ",".join(push_ids))
    return {"ok": True, "stage": stage, "complaint_no": no,
            "push_ids": push_ids, "title": title, "level": level}


def board_push_ids(notice: Any) -> list[str]:
    """从投递记录里取出看板推送行 id。

    兼容两种记录形状：单通道记录（含 ``pushed``）与 ``MultiChannel`` 组合记录
    （``results`` 里逐通道并列）——调用方不必知道自己挂了几路通道。
    """
    if not isinstance(notice, Mapping):
        return []
    results: Any = notice.get("results") if notice.get("via") == "multi" else [notice]
    ids: list[str] = []
    for result in results or []:
        if not isinstance(result, Mapping):
            continue
        for item in result.get("pushed") or []:
            pid = str((item or {}).get("push_id") or "").strip()
            if pid:
                ids.append(pid)
    return ids


# ---------------- DAG 节点：现场分派（客诉 8D 场景的第 4.5 步） ----------------

#: 批号识别（与 ``agents/quality._BATCH_RE`` 同口径；两处各有漂移守卫用例比对，
#: 归因读与 8D D4 取证必须认同一个批号，否则会出现"PQE 分析 A 批次、现场分派 B 工段"）
_BATCH_RE = re.compile(r"BATCH-[A-Za-z0-9\-]+")


def _batch_hint(task: Any, ctx: Any) -> str:
    """批号线索：问题文本 → 共享上下文（QA 牵头已写入）→ 入口实体（同 QA 牵头顺序）。"""
    found = _BATCH_RE.search(str(getattr(task, "query", "") or ""))
    if found:
        return found.group(0)
    from_ctx = ctx.get("batch_no")
    if from_ctx:
        return str(from_ctx)
    return str((getattr(task, "entities", {}) or {}).get("batch_no") or "")


def make_site_dispatch(notifier: Any, t2s: Any = None) -> Callable[..., dict[str, Any]]:
    """构造「客诉 → 责任工段现场看板」DAG 节点 handler（确定性，不调 LLM）。

    单号 / 客户 / 型号 / 缺陷描述取自既有注册查询 ``complaint_detail``（不编造），
    工段归因走 :func:`resolve_stage`，投递走 ``NotificationService``（架构铁律：
    节点不得自行发送通知）。归因成功时把工段写进 payload 的 ``site_stage`` /
    ``site_assignees``，供下游 8D 工单按工段追加现场责任；归因失败如实标
    ``data_gap`` 转人工，**不硬推**。
    """

    def handler(task: Any, ctx: Any, sink: Any) -> dict[str, Any]:
        entities = dict(getattr(task, "entities", {}) or {})
        complaint: dict[str, Any] = {
            "batch_no": _batch_hint(task, ctx),
            "line_code": str(entities.get("line_code") or ctx.get("line_code") or ""),
        }
        if t2s is not None:
            sink.emit({"type": "tool_start", "tool": "现场分派",
                       "detail": "读取客诉单明细（CRM 客诉单）"})
            qr = t2s.query_registered("complaint_detail", {},
                                      getattr(task, "user_ctx", {}) or {})
            rows = qr.rows or []
            if rows:
                head = rows[0]
                for key in ("complaint_no", "customer", "product_model", "defect_desc"):
                    if head.get(key):
                        complaint[key] = head[key]
                complaint["batch_no"] = complaint["batch_no"] or head.get("batch_no") or ""
            sink.emit({"type": "tool_end", "tool": "现场分派",
                       "detail": f"未闭环客诉 {len(rows)} 单；批号线索 "
                                 f"{complaint.get('batch_no') or '（无）'}"})

        result = dispatch_complaint(notifier, complaint)
        if not result.get("ok"):
            gap = str(result.get("data_gap") or "现场分派未完成")
            sink.emit({"type": "tool_end", "tool": "现场分派", "detail": gap})
            return {"domain": "site_board", "status": "degraded",
                    "payload": {"data_gap": [f"现场分派：{gap}"],
                                "site_summary": gap},
                    "summary": gap}

        stage = str(result["stage"])
        summary = (f"客诉 {result.get('complaint_no') or '（缺单号）'} 已分派至 {stage} "
                   f"现场看板（推送行 {len(result.get('push_ids') or [])} 条）")
        sink.emit({"type": "tool_end", "tool": "现场分派", "detail": summary})
        return {"domain": "site_board", "status": "ok", "payload": {
            "site_stage": stage,
            "site_assignees": [stage],
            "site_push_ids": list(result.get("push_ids") or []),
            "site_complaint_no": str(result.get("complaint_no") or ""),
            "site_summary": summary,
        }, "summary": summary}

    return handler


# ---------------- 状态机 ----------------


def transition_status(push_id: str, to_status: str, *, actor: str = "",
                      note: str = "", extra: Optional[Mapping[str, Any]] = None,
                      db_path: Optional[Union[str, Path]] = None) -> dict[str, Any]:
    """状态机流转（校验合法去向 + 写现场回填字段）；返回流转后的推送行。"""
    target_status = str(to_status or "").strip()
    current = get_push(push_id, db_path)
    allowed = STATUS_TRANSITIONS.get(str(current.get("status") or ""), ())
    if target_status not in allowed:
        raise InvalidSiteBoardTransition(
            f"推送行 {push_id} 不能从 {current.get('status')!r} 流转到 "
            f"{target_status!r}（允许：{'、'.join(allowed) or '无'}）")

    now = _now()
    record = dict(current)
    record["status"] = target_status
    record["updated_at"] = now
    if target_status == "acked":
        record["ack_by"] = str(actor or "")
        record["ack_at"] = now
    elif target_status == "closed":
        record["closed_by"] = str(actor or "")
        record["closed_at"] = now
    if note:
        record["feedback"] = str(note)
    if extra:
        for key, value in dict(extra).items():
            if value is not None and key in _ALL_COLUMNS:
                record[key] = value

    conn = _connect(resolve_site_db(db_path))
    try:
        with conn:
            assignments = ", ".join(f"{name}=?" for name in _ALL_COLUMNS
                                    if name != "push_id")
            conn.execute(
                f"UPDATE {SITE_BOARD_TABLE} SET {assignments} "
                f"WHERE push_id = ?",
                [record.get(name) for name in _ALL_COLUMNS
                 if name != "push_id"] + [record["push_id"]],
            )
    finally:
        conn.close()
    return get_push(push_id, db_path)


def ack(push_id: str, *, actor: str = "", note: str = "",
        db_path: Optional[Union[str, Path]] = None) -> dict[str, Any]:
    """现场接收（``pushed → acked``）。"""
    return transition_status(push_id, "acked", actor=actor, note=note,
                             db_path=db_path)


def close(push_id: str, *, actor: str = "", feedback: str = "",
          db_path: Optional[Union[str, Path]] = None) -> dict[str, Any]:
    """现场处置闭环（``acked → closed``）；``feedback`` 即处置结论留痕。"""
    return transition_status(push_id, "closed", actor=actor, note=feedback,
                             db_path=db_path)


# ---------------- 读取 ----------------


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {name: row[name] for name in _ALL_COLUMNS}


def get_push(push_id: str,
             db_path: Optional[Union[str, Path]] = None) -> dict[str, Any]:
    """按 ``push_id`` 取一条推送行；不存在 → ``PushNotFound``。"""
    pid = str(push_id or "").strip()
    if not pid:
        raise SiteBoardError("缺少 push_id")
    target = ensure_site_board_table(db_path)
    conn = _connect(target)
    try:
        row = conn.execute(
            f"SELECT * FROM {SITE_BOARD_TABLE} WHERE push_id = ?", (pid,)).fetchone()
    finally:
        conn.close()
    if row is None:
        raise PushNotFound(f"推送行不存在: {pid}")
    return _row_to_dict(row)


def list_pushes(*, stage: Optional[str] = None, status: Optional[str] = None,
                limit: int = 50,
                db_path: Optional[Union[str, Path]] = None) -> list[dict[str, Any]]:
    """取现场看板推送队列（按创建时间倒序；``stage`` 缺省 = 全部工段）。"""
    sql = f"SELECT * FROM {SITE_BOARD_TABLE} WHERE 1 = 1"
    params: list[Any] = []
    if stage:
        target_stage = str(stage).strip()
        if target_stage not in STAGES:
            raise SiteBoardError(
                f"目标工段非法: {stage!r}（合法值：{'、'.join(STAGES)}）")
        sql += " AND stage = ?"
        params.append(target_stage)
    if status:
        sql += " AND status = ?"
        params.append(str(status))
    sql += " ORDER BY created_at DESC, id DESC LIMIT ?"
    params.append(max(1, int(limit)))

    target = ensure_site_board_table(db_path)
    conn = _connect(target)
    try:
        rows = conn.execute(sql, params).fetchall()
    finally:
        conn.close()
    return [_row_to_dict(row) for row in rows]


def list_by_stage(stage: str, *, status: Optional[str] = None, limit: int = 50,
                  db_path: Optional[Union[str, Path]] = None) -> list[dict[str, Any]]:
    """取某工段的现场看板推送队列（工段必填，非法即拒绝）。"""
    return list_pushes(stage=stage, status=status, limit=limit, db_path=db_path)


# ---------------- 状态机 ----------------
