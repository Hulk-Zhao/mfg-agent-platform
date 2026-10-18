"""CaseStore：案例主数据 CRUD + ACL 继承（只可收紧）+ 状态机 + 种子导入。

架构位置：§6.1 / §6.2（存储）、§8.3（ACL 继承算法，BR-12）、§8.1（生命周期状态机）。

三条纪律：

1. **ACL 只可收紧不可放宽（BR-12）**：新 ACL 必须是旧 ACL 的子集，否则
   ``AclWideningNotAllowed``。种子导入显式用 ``allow_widen=True``（首次写入）。
2. **状态机非法流转直接拒绝**（``InvalidTransition``），不"尽力而为"地改状态。
3. **无来源单据不得入库（BR-11）**：``case.source_ticket_type/id`` 非空是硬约束；
   本层再做一次断言（入库服务层已校验一次），防止绕过服务层直写库。
"""
from __future__ import annotations

import json
import logging
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping, Optional, Sequence, Union

from ...common.case_schema import connect, ensure_case_schema, resolve_case_db
from ...common.config import settings
from ..contracts.case_acl import TOKEN_DIMS

logger = logging.getLogger(__name__)

__all__ = [
    "CaseStoreError",
    "InvalidTransition",
    "AclWideningNotAllowed",
    "SourceTicketRequired",
    "CaseNotFound",
    "VISIBLE_STATUS",
    "STATUS_TRANSITIONS",
    "SYSTEM_ACL_DIMS",
    "CaseStore",
]

#: 可被检索的状态（PRD §6.3）。``draft/review/rejected/withdrawn`` 永不进检索。
VISIBLE_STATUS: tuple[str, ...] = ("active", "deprecated", "superseded", "archived")

#: 合法状态流转表（PRD §6.3 状态机；``withdrawn`` 是终态）。
STATUS_TRANSITIONS: dict[str, tuple[str, ...]] = {
    "draft": ("review", "rejected", "withdrawn"),
    "review": ("active", "rejected", "draft"),
    "rejected": ("draft", "review"),
    "active": ("deprecated", "superseded", "withdrawn"),
    "deprecated": ("active", "archived", "withdrawn"),
    "superseded": ("archived", "withdrawn"),
    "archived": ("withdrawn",),
    "withdrawn": (),
}

#: 系统 ACL 维度（由案例标量属性合成，不是业务授权条目）。
SYSTEM_ACL_DIMS: tuple[str, ...] = (
    "tenant", "status", "cls_rank", "layer", "visibility_class",
)

#: 业务 ACL 维度（→ 主体令牌前缀）。
BUSINESS_ACL_DIMS: tuple[str, ...] = (
    "dept", "plant", "line", "prj", "cus", "sup", "role", "pf", "etype",
)

#: ``case`` 表可写字段（白名单，防注入式字段名）。
CASE_FIELDS: tuple[str, ...] = (
    "case_id", "case_code", "tenant", "category", "subcategory", "title",
    "summary", "phenomenon", "root_cause_text", "countermeasure_text",
    "defect_mode_code", "root_cause_code", "plant", "line_code", "owner_dept",
    "cls_rank", "status", "content_version", "visibility_class", "acl_version",
    "source_ticket_type", "source_ticket_id", "is_export_controlled",
    "published_at", "last_verified_at", "verified", "citation_count",
    "superseded_by", "author_role", "created_at",
)

#: 改动这些列即视为"内容变更"（`update_fields` 会 bump ``content_version``）。
CONTENT_FIELDS: tuple[str, ...] = (
    "title", "summary", "phenomenon", "root_cause_text", "countermeasure_text",
)

#: 种子导入前必须过脱敏的文本字段（与 ``case_ingest.TEXT_FIELDS`` 同口径）。
SEED_TEXT_FIELDS: tuple[str, ...] = (
    "title", "summary", "phenomenon", "root_cause_text", "countermeasure_text",
)


#: 数值型字段（缺省为 0，其余缺省为空串）。``content_version`` 有非零默认。
_NUMERIC_FIELDS: frozenset[str] = frozenset({
    "cls_rank", "content_version", "visibility_class", "acl_version",
    "is_export_controlled", "verified", "citation_count",
})

#: 字段级默认值（覆盖 `_NUMERIC_FIELDS` 里不能为 0 的那几个）。
_FIELD_DEFAULTS: dict[str, Any] = {
    "content_version": 1,
    "acl_version": 1,
    "status": "draft",
}


class CaseStoreError(Exception):
    """案例存储层异常基类。"""

class InvalidTransition(CaseStoreError):
    """非法状态流转（如 ``withdrawn → active``）。"""


class AclWideningNotAllowed(CaseStoreError):
    """试图放宽 ACL（BR-12）。"""


class SourceTicketRequired(CaseStoreError):
    """缺少来源单据（BR-11）。"""


class CaseNotFound(CaseStoreError):
    """案例不存在。"""


class CaseStore:
    """案例关系库访问对象（关系库 = 真相源）。"""

    def __init__(self, db_path: Optional[Union[str, Path]] = None) -> None:
        ensure_case_schema(db_path)
        self.db_path = resolve_case_db(db_path)

    # ---------- 连接 ----------

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = connect(self.db_path)
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    # ---------- 写入 ----------

    def create_case(self, case: Mapping[str, Any], *,
                    allow_widen_acl: bool = False) -> str:
        """创建/覆盖一条案例（按 ``case_id`` 幂等）。"""
        case_id = str(case.get("case_id") or "").strip()
        if not case_id:
            raise CaseStoreError("案例缺少 case_id")
        source_type = str(case.get("source_ticket_type") or "").strip()
        source_id = str(case.get("source_ticket_id") or "").strip()
        if not source_type or not source_id:
            raise SourceTicketRequired(
                f"案例 {case_id} 缺少来源单据（BR-11：无来源不得进入共享池）")

        payload = {key: case.get(key) for key in CASE_FIELDS if key in case}
        payload["case_id"] = case_id
        payload.setdefault("status", "draft")
        status = str(payload.get("status") or "draft")
        if status not in STATUS_TRANSITIONS:
            raise InvalidTransition(f"非法案例状态: {status!r}")
        # 缺省值归一：None 会撞 NOT NULL 约束（那是"漏填"，不是"无授权"）
        payload["status"] = status
        for name in CASE_FIELDS:
            if payload.get(name) is not None:
                continue
            payload[name] = _FIELD_DEFAULTS.get(
                name, 0 if name in _NUMERIC_FIELDS else "")
        # 租户缺省：编译出的硬维度是 ``tenant == principal.tenant``，
        # 存空串会造出一条"谁都看不见"的案例（与 ACL 维度名写错同一类故障），
        # 因此缺省时落到 ``CASE_DEFAULT_TENANT``，而不是留空。
        if not str(payload.get("tenant") or "").strip():
            payload["tenant"] = str(settings.CASE_DEFAULT_TENANT or "")

        columns = ", ".join(CASE_FIELDS)
        placeholders = ", ".join("?" for _ in CASE_FIELDS)
        updates = ", ".join(f"{name}=excluded.{name}" for name in CASE_FIELDS
                            if name != "case_id")
        with self._conn() as conn:
            conn.execute(
                f'INSERT INTO "case" ({columns}) VALUES ({placeholders}) '
                f"ON CONFLICT(case_id) DO UPDATE SET {updates}",
                [payload.get(name) for name in CASE_FIELDS],
            )
        acl = case.get("acl")
        if acl is not None:
            self.set_acl(case_id, list(acl), allow_widen=allow_widen_acl)
        return case_id

    def update_fields(self, case_id: str, **fields: Any) -> None:
        """更新案例字段（只允许 ``CASE_FIELDS`` 内的列名，且 ``status`` 只能走状态机）。

        **改到知识层文本时自动 bump ``content_version``**：版本号必须能代表内容，
        否则审计对账、缓存失效、引用三元组（BR-05）都会拿着一个"没变过"的版本号
        去对一份已经变了的内容。引用安全本身由 ``text_hash`` 兜底（版本一致但文本
        被绕过管道改动 → 丢弃该断言），但版本号不 bump 是另一类问题：不修的话，
        同一个 ``(case_id, chunk_id, v1)`` 会在不同时间指向两份不同内容。
        """
        target = str(case_id or "").strip()
        if not target:
            raise CaseStoreError("更新案例缺少 case_id")
        if "status" in fields:
            raise CaseStoreError("状态变更必须走 transition()，不允许直接改 status")
        unknown = [key for key in fields if key not in CASE_FIELDS]
        if unknown:
            raise CaseStoreError(f"未知案例字段: {unknown}")
        if not fields:
            return
        if "content_version" not in fields and any(
                key in fields for key in CONTENT_FIELDS):
            current = self.get_case(target)
            if current is not None:
                fields["content_version"] = int(
                    current.get("content_version") or 1) + 1
        assignments = ", ".join(f"{key}=?" for key in fields)
        with self._conn() as conn:
            cursor = conn.execute(
                f'UPDATE "case" SET {assignments} WHERE case_id=?',
                [*fields.values(), target])
            if cursor.rowcount == 0:
                raise CaseNotFound(f"案例不存在: {target}")

    def transition(self, case_id: str, new_status: str,
                   *, extra: Optional[Mapping[str, Any]] = None) -> str:
        """状态流转（非法流转 → ``InvalidTransition``）。"""
        target = str(case_id or "").strip()
        case = self.get_case(target)
        if case is None:
            raise CaseNotFound(f"案例不存在: {target}")
        current = str(case.get("status") or "")
        nxt = str(new_status or "").strip()
        allowed = STATUS_TRANSITIONS.get(current, ())
        if nxt not in allowed:
            raise InvalidTransition(
                f"非法状态流转: {current} → {nxt}（允许：{list(allowed)}）")
        fields: dict[str, Any] = {"status": nxt}
        for key, value in (extra or {}).items():
            if key not in CASE_FIELDS:
                raise CaseStoreError(f"未知案例字段: {key}")
            fields[key] = value
        assignments = ", ".join(f"{key}=?" for key in fields)
        with self._conn() as conn:
            conn.execute(f'UPDATE "case" SET {assignments} WHERE case_id=?',
                         [*fields.values(), target])
        return nxt

    # ---------- ACL ----------

    def set_acl(self, case_id: str, entries: Sequence[Mapping[str, Any]],
                *, allow_widen: bool = False) -> tuple[str, ...]:
        """写入案例 ACL（默认**只可收紧**，BR-12）。

        Args:
            case_id: 案例。
            entries: ``[{"dim": "dept", "value": "quality", "effect": "allow"}]``。
            allow_widen: 首次写入 / 种子导入时置 ``True``。

        Returns:
            写入后的业务 ACL 维度值元组（用于计算 ``visibility_class``）。

        Raises:
            AclWideningNotAllowed: 新 ACL 不是旧 ACL 的子集。
        """
        target = str(case_id or "").strip()
        if self.get_case(target) is None:
            raise CaseNotFound(f"案例不存在: {target}")
        normalized: list[tuple[str, str, str]] = []
        for entry in entries or ():
            if not isinstance(entry, Mapping):
                raise CaseStoreError(f"ACL 条目格式非法: {entry!r}")
            dim = str(entry.get("dim") or "").strip()
            value = str(entry.get("value") or "").strip()
            effect = str(entry.get("effect") or "allow").strip().lower()
            if not dim:
                raise CaseStoreError("ACL 条目缺少 dim")
            if effect not in ("allow", "deny"):
                raise CaseStoreError(f"ACL effect 非法: {effect!r}")
            if dim in SYSTEM_ACL_DIMS:
                raise CaseStoreError(
                    f"系统维度 {dim} 不允许被外部 ACL 覆盖（强制叠加，见 §8.3）")
            if dim not in TOKEN_DIMS:
                # fail-fast：写错的维度名不会命中任何主体令牌，若静默忽略就会造出
                # 一条"谁都看不见"的案例——这是最难排查的一类故障，必须在入口拒掉。
                raise CaseStoreError(
                    f"ACL 维度名非法: {dim!r}（允许：{', '.join(TOKEN_DIMS)}）")
            if effect == "allow" and not value:
                raise CaseStoreError(f"ACL 条目 {dim} 缺少 value")
            if dim == "user" and ":" in value:
                # 令牌前缀由 compute_acl_tokens / principal_token 统一拼；
                # 这里再写一遍会变成 "user:user:xxx"，永远匹配不上（静默失效）。
                raise CaseStoreError(
                    f"ACL 维度 user 的值应是纯 user_id（不带 'user:' 前缀）：{value!r}")
            normalized.append((dim, value, effect))

        existing = {(row["dim"], row["value"], row["effect"])
                    for row in self.acl_entries(target)}
        new_set = set(normalized)
        if existing and not allow_widen:
            widened = new_set - existing
            if widened:
                raise AclWideningNotAllowed(
                    f"案例 {target} 的 ACL 只可收紧不可放宽（BR-12）："
                    f"新增 {sorted(widened)}")
        with self._conn() as conn:
            conn.execute("DELETE FROM case_acl WHERE case_id=?", (target,))
            conn.executemany(
                "INSERT OR REPLACE INTO case_acl (case_id, dim, value, effect, origin) "
                "VALUES (?, ?, ?, ?, ?)",
                [(target, dim, value, effect, "inherited")
                 for dim, value, effect in normalized],
            )
            conn.execute(
                'UPDATE "case" SET acl_version = acl_version + 1 WHERE case_id=?',
                (target,))
        tokens = tuple(sorted(f"{dim}:{value}" for dim, value, effect in normalized
                            if effect == "allow"))
        # ★ B4：改了 ACL 必须重算 visibility_class，否则快路径会误放行。
        # vc 与 acl_tokens 是同一个判定维度的两个投影（VISIBILITY_FIELDS），
        # 只改一个等于把"有业务 ACL"的案例留在 vc=0（全员开放）快路径上。
        self.set_visibility(target, 0 if not tokens else 1, tokens)
        return tokens

    def acl_entries(self, case_id: str) -> list[dict[str, Any]]:
        """业务 ACL 条目（不含系统维度）。"""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT case_id, dim, value, effect, origin FROM case_acl "
                "WHERE case_id=? ORDER BY dim, value", (str(case_id),)).fetchall()
        return [dict(row) for row in rows]

    def eval_rows(self, case_id: str) -> list[dict[str, Any]]:
        """二次授权用的权威 ACL 行：业务条目 + **系统标量行**。

        系统行把案例的 ``tenant/status/cls_rank/layer/visibility_class`` 也表达成
        "行"，这样 ``AclEvaluator`` 只需要一种数据结构（行），不需要认识 ``Case``。

        ★ B1（结构等价）：**案例不存在时也必须先把 ``case_acl`` 查一遍**再返回空集。
        早前这里写成 ``if case is None: return []``，于是"不存在"路径少一次
        ``SELECT ... FROM case_acl``、少一次 connect——即使补齐了审计写入，
        两条 404 路径下发的 SQL 仍不等价（QA 用 ``set_trace_callback`` 量到
        3 connect/6 语句 vs 4 connect/7 语句）。耗时对上是概率，语句模板一致
        才是结构性的不可区分。
        """
        case = self.get_case(case_id)
        rows = self.acl_entries(case_id)      # 不存在时自然返回空集，但查询确实发生
        if case is None:
            return rows
        rows.extend([
            {"case_id": case_id, "dim": "tenant",
             "value": str(case.get("tenant") or ""), "effect": "system"},
            {"case_id": case_id, "dim": "status",
             "value": str(case.get("status") or ""), "effect": "system"},
            {"case_id": case_id, "dim": "cls_rank",
             "value": str(case.get("cls_rank") or 0), "effect": "system"},
            # 关系库里的案例文本永远是知识层（证据层在 case_evidence，独立存储）
            {"case_id": case_id, "dim": "layer", "value": "knowledge",
             "effect": "system"},
            {"case_id": case_id, "dim": "visibility_class",
             "value": str(case.get("visibility_class") or 0), "effect": "system"},
        ])
        return rows

    def set_visibility(self, case_id: str, visibility_class: int,
                       tokens: Sequence[str]) -> None:
        """写入 ``visibility_class``（0=OPEN / 1=SCOPED）与 token 归属。

        ``tokens`` 只用于日志与对账（真正的令牌在 chunk metadata 里由索引服务生成）。
        """
        value = 0 if int(visibility_class) == 0 else 1
        with self._conn() as conn:
            conn.execute('UPDATE "case" SET visibility_class=? WHERE case_id=?',
                         (value, str(case_id)))
        logger.debug("[case_store] %s visibility_class=%d tokens=%d",
                     case_id, value, len(list(tokens or ())))

    # ---------- 查询 ----------

    def get_case(self, case_id: str) -> Optional[dict[str, Any]]:
        with self._conn() as conn:
            row = conn.execute('SELECT * FROM "case" WHERE case_id=?',
                               (str(case_id or ""),)).fetchone()
        return dict(row) if row else None

    def list_cases(self, *, status: Optional[Iterable[str]] = None,
                   category: Optional[str] = None,
                   tenant: Optional[str] = None) -> list[dict[str, Any]]:
        sql = 'SELECT * FROM "case" WHERE 1=1'
        params: list[Any] = []
        statuses = [str(s) for s in (status or ())]
        if statuses:
            sql += f" AND status IN ({', '.join('?' for _ in statuses)})"
            params.extend(statuses)
        if category:
            sql += " AND category=?"
            params.append(str(category))
        if tenant:
            sql += " AND tenant=?"
            params.append(str(tenant))
        sql += " ORDER BY case_id"
        with self._conn() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [dict(row) for row in rows]

    def count(self, *, status: Optional[Iterable[str]] = None) -> int:
        return len(self.list_cases(status=status))

    # ---------- chunk 映射 ----------

    def replace_chunks(self, case_id: str,
                       rows: Sequence[Mapping[str, Any]]) -> None:
        """重写某案例的 chunk 映射行（``vector_status`` 由索引服务维护）。"""
        target = str(case_id or "")
        with self._conn() as conn:
            conn.execute("DELETE FROM case_chunk WHERE case_id=?", (target,))
            conn.executemany(
                "INSERT OR REPLACE INTO case_chunk "
                "(chunk_id, case_id, chunk_type, content_version, vector_status, "
                " text_hash, indexed_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                [(str(row.get("chunk_id")), target, str(row.get("chunk_type")),
                  int(row.get("content_version") or 1),
                  str(row.get("vector_status") or "pending"),
                  str(row.get("text_hash") or ""),
                  str(row.get("indexed_at") or "")) for row in rows],
            )

    def backfill_chunk_text_hash(self, case_id: str, chunk_id: str,
                                 text_hash: str) -> None:
        """回填 ``case_chunk.text_hash``（老库升级用；不改内容、不改版本）。"""
        if not str(text_hash or "").strip():
            return
        with self._conn() as conn:
            conn.execute(
                "UPDATE case_chunk SET text_hash=? WHERE case_id=? AND chunk_id=?",
                (str(text_hash), str(case_id), str(chunk_id)))

    def chunk_rows(self, case_id: str) -> list[dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM case_chunk WHERE case_id=? ORDER BY chunk_id",
                (str(case_id),)).fetchall()
        return [dict(row) for row in rows]

    def set_chunk_status(self, case_id: str, vector_status: str,
                         indexed_at: str = "") -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE case_chunk SET vector_status=?, indexed_at=? WHERE case_id=?",
                (str(vector_status), str(indexed_at), str(case_id)))

    # ---------- 证据层（只存元数据） ----------

    def add_evidence(self, case_id: str, *,
                     evidence_id: str = "", kind: str = "", uri: str = "",
                     cls_rank: int = 3, retain_until: str = "") -> str:
        """登记证据层元数据（**只存 uri + 密级**，不存内容、不进向量库）。"""
        target = str(case_id or "")
        if self.get_case(target) is None:
            raise CaseNotFound(f"案例不存在: {target}")
        eid = str(evidence_id or f"{target}::{kind or 'evidence'}::{cls_rank}")
        with self._conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO case_evidence "
                "(evidence_id, case_id, kind, uri, cls_rank, retain_until) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (eid, target, str(kind), str(uri), int(cls_rank), str(retain_until)))
        return eid

    def evidence_rows(self, case_id: str) -> list[dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM case_evidence WHERE case_id=? ORDER BY id",
                (str(case_id),)).fetchall()
        return [dict(row) for row in rows]

    # ---------- 种子导入 ----------

    def seed_from_json(self, path: Union[str, Path], *,
                       tenant: Optional[str] = None,
                       redactor: Any = None) -> int:
        """从 JSON 导入案例（**幂等**：按 ``case_id`` 覆盖，ACL 全量重写）。

        返回导入条数。JSON 结构见 ``data/case_seed.json``。

        ``redactor``（B3）：必须传一个 ``RedactionService``，种子文本要和线上
        入库走**同一套脱敏 + 复检**。为什么不能信任种子文件"已经是干净的"：
        种子是人工维护的 JSON，一次复制粘贴就可能把真名带进来；如果只有入库
        管道有脱敏，种子就是一条绕过所有护栏的后门。缺省仍会尝试取全局服务，
        取不到才退化为"只做复检"（宁可启动失败，也不静默放过）。
        """
        target = Path(path)
        if not target.is_file():
            raise CaseStoreError(f"种子文件不存在: {target}")
        payload = json.loads(target.read_text(encoding="utf-8"))
        cases = payload.get("cases") if isinstance(payload, dict) else payload
        if not isinstance(cases, list):
            raise CaseStoreError(f"种子文件格式非法（应为 list 或 {{cases: []}}）: {target}")
        default_tenant = str((payload or {}).get("tenant", "") if isinstance(payload, dict)
                             else "") or str(tenant or "")
        if redactor is None:
            try:
                from .redaction import get_redaction_service

                redactor = get_redaction_service()
            except Exception:  # noqa: BLE001 - 不引入硬依赖，但下面会校验
                redactor = None
        imported = 0
        for item in cases:
            if not isinstance(item, dict):
                raise CaseStoreError(f"种子案例格式非法: {item!r}")
            record = dict(item)
            record.setdefault("tenant", default_tenant)
            record.setdefault("created_at", str(record.get("published_at") or ""))
            acl = list(record.get("acl") or [])
            case_id = str(record.get("case_id") or "")
            # 步 0（B3）：种子文本先过脱敏 + 复检，再落库——种子不是豁免通道
            for field in SEED_TEXT_FIELDS:
                if field in record and record.get(field) is not None:
                    record[field] = self._redact_seed_field(
                        case_id, field, str(record.get(field) or ""), redactor)
            self._purge(case_id)
            self.create_case(record, allow_widen_acl=True)
            tokens = [f"{e['dim']}:{e['value']}" for e in acl
                      if str(e.get("effect", "allow")) == "allow"]
            self.set_visibility(case_id, 0 if not tokens else 1, tokens)
            for evidence in (record.get("evidence") or []):
                self.add_evidence(
                    case_id,
                    evidence_id=str(evidence.get("evidence_id") or ""),
                    kind=str(evidence.get("kind") or ""),
                    uri=str(evidence.get("uri") or ""),
                    cls_rank=int(evidence.get("cls_rank") or 3),
                    retain_until=str(evidence.get("retain_until") or ""),
                )
            imported += 1
        logger.info("[case_store] 种子导入完成: %s（%d 条）", target, imported)
        return imported

    def _redact_seed_field(self, case_id: str, field: str, text: str,
                           redactor: Any) -> str:
        """种子单字段：脱敏 → 复检 → 命中即**整体拒绝导入**（不降级放行）。"""
        if not text:
            return text
        if redactor is None:
            raise CaseStoreError(
                f"种子 {case_id} 的 {field} 无法脱敏（RedactionService 不可用）："
                "拒绝导入。种子与线上入库必须走同一套脱敏管道。")
        result = redactor.redact(text)
        residue = redactor.verify_no_leak(result.text)
        if residue:
            kinds = sorted({hit.kind for hit in residue})
            raise CaseStoreError(
                f"种子 {case_id} 的 {field} 脱敏后仍有残留（{kinds}）："
                f"{[hit.value for hit in residue][:3]}；拒绝导入")
        return result.text

    def _purge(self, case_id: str) -> None:
        """清除某案例的既有行（种子重放幂等用；不改动其它案例）。"""
        if not case_id:
            return
        with self._conn() as conn:
            conn.execute("DELETE FROM case_acl WHERE case_id=?", (case_id,))
            conn.execute("DELETE FROM case_chunk WHERE case_id=?", (case_id,))
            conn.execute("DELETE FROM case_evidence WHERE case_id=?", (case_id,))
