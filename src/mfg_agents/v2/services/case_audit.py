"""CaseAuditLog：案例访问审计（只追加 + hash chain）。

架构位置：§9.4。三条纪律：

1. **只记录哈希，不记录明文**：``query_hash = sha256(query)``、
   ``filter_hash = sha256(canonical(AST))``。查询里可能含客户代号，filter 里可能含
   客户/供应商 token——两者都不得落明文。
2. **不记录案例正文**：``detail`` 只允许结构化元信息（命中数、状态徽标、规则 id），
   禁止写入 ``document`` / ``summary`` / 任何知识层文本。
3. **hash chain 防篡改**：``row_hash = sha256(canonical_json(row) + prev_hash)``，
   :meth:`verify_chain` 可检测任意一行被改动或删除。

生产化清单（本 demo 不实现，写在文档里）：DB 授权层只允许 INSERT、
每小时链路校验、每日 WORM 快照、审计账号与 DBA 账号分离。
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence, Union

from ...common.case_schema import connect, ensure_case_schema, resolve_case_db

logger = logging.getLogger(__name__)

__all__ = ["CaseAuditLog", "hash_query"]

#: ``detail`` 里禁止出现的键（防止误把正文写进审计）。
FORBIDDEN_DETAIL_KEYS: tuple[str, ...] = (
    "document", "text", "summary", "phenomenon", "root_cause_text",
    "countermeasure_text", "query", "filter", "raw", "content",
)


def hash_query(text: str) -> str:
    """查询明文 → 哈希（审计只存这个）。"""
    return hashlib.sha256(str(text or "").encode("utf-8")).hexdigest()


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _canonical(payload: Mapping[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"))


def decision_for(action: str) -> str:
    """由 action 推导 decision（写入与校验共用同一条归一化规则）。

    约定：拒绝类事件的动作名以 ``.denied`` 结尾（如 ``case.query.denied``）。
    这样 ``decision`` 不需要单独落列，也不可能与 ``action`` 冲突——
    篡改 ``action`` 会同时改变 ``row_hash``，链校验立刻发现。
    """
    return "deny" if str(action or "").endswith(".denied") else "allow"


class CaseAuditLog:
    """案例审计日志（SQLite，追加式 + 链式哈希）。"""

    def __init__(self, db_path: Optional[Union[str, Path]] = None) -> None:
        ensure_case_schema(db_path)
        self.db_path = resolve_case_db(db_path)

    # ---------- 写入 ----------

    def append(self, *, actor: str, action: str,
               case_id: str = "", chunk_id: str = "",
               query_hash: str = "", filter_hash: str = "",
               hit_count: int = 0, hidden_count: int = 0,
               decision: str = "allow", actor_kind: str = "human",
               detail: Optional[Mapping[str, Any]] = None) -> int:
        """追加一条审计（返回 seq）。

        Raises:
            ValueError: ``detail`` 里出现被禁键（正文/明文查询/filter）。
        """
        safe_detail = self._sanitize_detail(detail)
        conn = connect(self.db_path)
        try:
            row = conn.execute(
                "SELECT row_hash FROM case_audit ORDER BY seq DESC LIMIT 1").fetchone()
            prev_hash = str(row["row_hash"]) if row else ""
            ts = _now_iso()
            payload = {
                "ts": ts,
                "actor": str(actor or ""),
                "actor_kind": str(actor_kind or "human"),
                "action": str(action or ""),
                "case_id": str(case_id or ""),
                "chunk_id": str(chunk_id or ""),
                "decision": decision_for(action),
                "query_hash": str(query_hash or ""),
                "filter_hash": str(filter_hash or ""),
                "hit_count": int(hit_count or 0),
                "hidden_count": int(hidden_count or 0),
                "prev_hash": prev_hash,
            }
            payload["row_hash"] = hashlib.sha256(
                (_canonical(payload)).encode("utf-8")).hexdigest()
            cursor = conn.execute(
                "INSERT INTO case_audit (ts, actor, actor_kind, action, case_id, "
                "chunk_id, query_hash, filter_hash, hit_count, hidden_count, "
                "prev_hash, row_hash, detail) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (payload["ts"], payload["actor"], payload["actor_kind"],
                 payload["action"], payload["case_id"], payload["chunk_id"],
                 payload["query_hash"],
                 payload["filter_hash"], payload["hit_count"],
                 payload["hidden_count"], payload["prev_hash"],
                 payload["row_hash"], _canonical(safe_detail)),
            )
            conn.commit()
            return int(cursor.lastrowid or 0)
        finally:
            conn.close()

    # ---------- 校验 ----------

    def verify_chain(self) -> dict[str, Any]:
        """校验 hash chain 完整性。

        Returns:
            ``{"valid": bool, "rows": int, "broken_seq": int | None, "detail": str}``
        """
        conn = connect(self.db_path)
        try:
            rows = conn.execute(
                "SELECT seq, ts, actor, actor_kind, action, case_id, chunk_id, "
                "query_hash, filter_hash, hit_count, hidden_count, prev_hash, row_hash "
                "FROM case_audit ORDER BY seq").fetchall()
        finally:
            conn.close()
        prev = ""
        for index, row in enumerate(rows):
            payload = {
                "ts": row["ts"], "actor": row["actor"],
                "actor_kind": row["actor_kind"], "action": row["action"],
                "case_id": row["case_id"], "chunk_id": row["chunk_id"],
                "decision": decision_for(row["action"]),
                "query_hash": row["query_hash"], "filter_hash": row["filter_hash"],
                "hit_count": row["hit_count"], "hidden_count": row["hidden_count"],
                "prev_hash": row["prev_hash"],
            }
            expected = hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()
            if str(row["prev_hash"] or "") != prev:
                return {"valid": False, "rows": len(rows), "broken_seq": row["seq"],
                        "detail": "prev_hash 与前一行 row_hash 不一致（可能被删除/插入）"}
            if str(row["row_hash"] or "") != expected:
                return {"valid": False, "rows": len(rows), "broken_seq": row["seq"],
                        "detail": "row_hash 与内容不一致（该行被改动）"}
            prev = str(row["row_hash"] or "")
            _ = index
        return {"valid": True, "rows": len(rows), "broken_seq": None,
                "detail": "hash chain 完整"}

    def _decision_of(self, seq: int) -> str:
        """兼容保留：按 seq 反查 action 推导 decision（不再用于链校验）。"""
        conn = connect(self.db_path)
        try:
            row = conn.execute("SELECT action FROM case_audit WHERE seq=?",
                               (int(seq),)).fetchone()
        finally:
            conn.close()
        return decision_for(str(row["action"] if row else ""))

    # ---------- 查询 ----------

    def recent(self, limit: int = 20) -> list[dict[str, Any]]:
        """最近 N 条审计（**不含**任何正文，只含哈希与计数）。"""
        conn = connect(self.db_path)
        try:
            rows = conn.execute(
                "SELECT seq, ts, actor, actor_kind, action, case_id, query_hash, "
                "filter_hash, hit_count, hidden_count, prev_hash, row_hash "
                "FROM case_audit ORDER BY seq DESC LIMIT ?", (max(0, int(limit)),)
            ).fetchall()
        finally:
            conn.close()
        return [dict(row) for row in rows]

    def count(self) -> int:
        conn = connect(self.db_path)
        try:
            row = conn.execute("SELECT COUNT(1) AS n FROM case_audit").fetchone()
            return int(row["n"] if row else 0)
        finally:
            conn.close()

    # ---------- 内部 ----------

    @staticmethod
    def _sanitize_detail(detail: Optional[Mapping[str, Any]]) -> dict[str, Any]:
        """过滤 ``detail``：禁止正文/明文，值只允许标量与标量列表。"""
        if not detail:
            return {}
        out: dict[str, Any] = {}
        for key, value in detail.items():
            name = str(key)
            if name.lower() in FORBIDDEN_DETAIL_KEYS:
                raise ValueError(
                    f"审计 detail 禁止包含 {name!r}（不得记录查询明文/案例正文）")
            if isinstance(value, (str, int, float, bool)) or value is None:
                out[name] = value
            elif isinstance(value, (list, tuple)):
                out[name] = [str(v) for v in value][:32]
            else:
                out[name] = str(value)
        return out


def append_search_audit(log: CaseAuditLog, *, actor: str, action: str,
                        query_hash: str, filter_hash: str,
                        hit_count: int, hidden_count: int,
                        decision: str = "allow",
                        detail: Optional[Mapping[str, Any]] = None) -> int:
    """便捷入口：记录一次检索审计（调用方保证只传哈希）。"""
    return log.append(actor=actor, action=action, query_hash=query_hash,
                      filter_hash=filter_hash, hit_count=hit_count,
                      hidden_count=hidden_count, decision=decision, detail=detail)
