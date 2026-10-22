"""连接器回写（R13 / D14）：意图注册 + 幂等回执 + mock 连接器 + R6 接线。

D14 的落地边界：

- ``DataSource.write_back(intent, payload) -> Receipt`` 是唯一回写入口
  （见 ``contracts/data_source.py``）；本模块提供**模拟连接器**实现，
  让 R6 催交到货确认真实走一遍「意图 → 幂等 → 回执 → 审计」链路；
- 业务库仍以 ``mode=ro`` 只读，回写事实落**审计台账**
  （``writeback_outbox.jsonl``，与治理库同目录），不冒充已写回 ERP；
- ``tier=1`` 的意图（对客承诺类）必须先经 HITL 会签，未带
  ``consent={ticket_id, approved: true}`` 时只返回 ``pending_consent``
  回执，不落地；``tier=2`` 的意图（到货/收货类）直接落地但留痕事后审计，
  ``tier=0`` 不允许出现在回写注册表（只读意图不应该走写回）。

显式不做的（诚实边界）：

- 没有真实 ERP / MES 连接器，``mode=mock`` 永远为真；回执文案固定标注
  「演示回写（未写回 ERP）」，与 R6 的不可关闭徽标同源；
- ``consent`` 只做**形状校验**（工单号 + approved），工单的真实会签事实由
  发起来源（治理链）负责；接真实连接器时必须加签名/回调校验。
"""
from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from ..contracts.data_source import (
    DataSource,
    Receipt,
    WritebackNotSupported,
)

__all__ = [
    "WRITEBACK_INTENTS",
    "WritebackIntentError",
    "MockWritebackDataSource",
    "connector_for",
    "emit_arrival_receipt",
]

#: 回写意图注册表（intent → 治理分级 / 槽位 / 文案）。
#:
#: tier 语义与 ``mcp_tools/registry.py::Tool.tier`` 对齐：
#: - 1 = 预执行同意（先产 HITL 工单，会签通过才落地）；
#: - 2 = 事后审计（可执行但有副作用，事后留痕复核）。
WRITEBACK_INTENTS: dict[str, dict[str, Any]] = {
    "erp.purchase_receipt": {
        "label": "采购到货回写",
        "tier": 2,
        "slot": "",
        "system": "ERP(模拟)",
        "domain": "procurement",
    },
    "erp.delivery_commit": {
        "label": "对客交期承诺回写",
        "tier": 1,
        "slot": "delivery_commit",
        "system": "ERP(模拟)",
        "domain": "sales",
    },
}

#: 模拟连接器固定文案（演示态不可关闭）
MOCK_LABEL = "演示回写（未写回 ERP）"
MOCK_MODE = "mock"


class WritebackIntentError(ValueError):
    """未注册的写回意图（fail-closed，不猜意图、不静默丢弃）。"""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _digest(raw: str) -> str:
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "y"}


def consent_is_approved(consent: Optional[dict[str, Any]]) -> bool:
    """预执行同意凭据是否齐备：必须带工单号 + 明确 approved。

    这是**形状校验**（见模块 docstring）；真实会签事实由治理链保证。
    """
    if not isinstance(consent, dict):
        return False
    ticket = str(consent.get("ticket_id") or "").strip()
    return bool(ticket) and _truthy(consent.get("approved"))


class MockWritebackDataSource(DataSource):
    """模拟回写连接器：幂等回执 + JSONL 审计台账，不写业务库、不写真实 ERP。

    Args:
        outbox_path: 审计台账路径（JSONL）。空串表示仅进程内留存（测试用）。
        name: 数据源名（一个台账一个连接器实例）。
    """

    def __init__(self, outbox_path: str = "", name: str = "erp-mock") -> None:
        self.name = name
        self.outbox_path = str(outbox_path or "")
        self.meta = {
            "system": "ERP(模拟)",
            "domain": "procurement",
            "kind": "writeback-mock",
            "note": "只回写审计台账，不写业务库/真实 ERP",
        }
        self._lock = threading.RLock()
        # 幂等键 → 最新回执（从台账恢复，重启后同键不重复落地）
        self._records: dict[str, Receipt] = {}
        if self.outbox_path:
            self._load_outbox()

    # ---------- 读接口（模拟连接器没有可读表） ----------

    def list_tables(self) -> list[str]:
        return ["outbox"]

    def run_query(self, name: str, params: Optional[dict] = None) -> list[dict]:
        raise KeyError(f"模拟回写连接器未注册查询: {name!r}")

    def execute_readonly_sql(self, sql: str, params: Optional[dict] = None,
                             row_cap: int = 200) -> list[dict]:
        raise WritebackNotSupported("模拟回写连接器只支持 write_back，不提供 SQL 读接口")

    def describe(self) -> dict[str, Any]:
        base = super().describe()
        base["kind"] = "MockWritebackDataSource"
        base["writeback_intents"] = sorted(WRITEBACK_INTENTS)
        return base

    # ---------- 写回 ----------

    def write_back(self, intent: str, payload: Optional[dict[str, Any]] = None) -> Receipt:
        meta = WRITEBACK_INTENTS.get(intent)
        if meta is None:
            raise WritebackIntentError(
                f"未注册的写回意图: {intent!r}（已注册: {sorted(WRITEBACK_INTENTS)}）")
        if payload is not None and not isinstance(payload, dict):
            raise TypeError("write_back 的 payload 必须是字典")

        data = dict(payload or {})
        key_raw = str(data.get("idempotency_key") or "").strip()
        if not key_raw:
            # 不把 consent 算进幂等键：同一笔业务在待会签/已会签阶段是同一回执
            body = {k: v for k, v in data.items() if k != "consent"}
            key_raw = _digest(json.dumps(
                {"intent": intent, "payload": body}, ensure_ascii=False, sort_keys=True,
                default=str))
        key = f"{intent}|{key_raw}"
        receipt_id = "rc-" + _digest(key)[:12]
        tier = int(meta.get("tier") or 2)

        with self._lock:
            prev = self._records.get(key)
            # 已落地的回执：幂等重放，绝不重复落地
            if prev is not None and prev.status == "applied":
                return replace(prev, replayed=True)

            consent = data.get("consent") if isinstance(data.get("consent"), dict) else {}
            if tier == 1 and not consent_is_approved(consent):
                receipt = self._receipt(
                    receipt_id=receipt_id, intent=intent, key=key_raw, tier=tier,
                    status="pending_consent", consent=consent, payload=data,
                    next_step=(f"先经「{meta.get('slot')}」会签：workflow.create → "
                               f"cosign → approve，带 consent={{ticket_id, approved:true}} "
                               f"再回写"))
                self._append(receipt)
                return receipt

            receipt = self._receipt(
                receipt_id=receipt_id, intent=intent, key=key_raw, tier=tier,
                status="applied", consent=consent, payload=data)
            self._append(receipt)
            return receipt

    def list_receipts(self) -> list[dict[str, Any]]:
        """按最近生成倒序返回每个幂等键的最新回执（审计视图）。"""
        with self._lock:
            rows = [r.to_dict() for r in self._records.values()]
        return sorted(rows, key=lambda r: r.get("created_at") or "", reverse=True)

    # ---------- 内部 ----------

    def _receipt(self, *, receipt_id: str, intent: str, key: str, tier: int,
                 status: str, consent: dict[str, Any], payload: dict[str, Any],
                 next_step: str = "") -> Receipt:
        meta = WRITEBACK_INTENTS[intent]
        return Receipt(
            receipt_id=receipt_id,
            intent=intent,
            status=status,
            idempotency_key=key,
            tier=tier,
            mode=MOCK_MODE,
            demo=True,
            label=MOCK_LABEL,
            system=str(meta.get("system") or "ERP(模拟)"),
            created_at=_now_iso(),
            consent=dict(consent),
            payload=dict(payload),
            next_step=next_step,
        )

    def _append(self, receipt: Receipt) -> None:
        self._records[f"{receipt.intent}|{receipt.idempotency_key}"] = receipt
        if not self.outbox_path:
            return
        path = Path(self.outbox_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        record = {"recorded_at": _now_iso(), "receipt": receipt.to_dict()}
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")

    def _load_outbox(self) -> None:
        path = Path(self.outbox_path)
        if not path.exists():
            return
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                record = json.loads(line)
                data = record.get("receipt") or {}
                if not data.get("intent") or not data.get("idempotency_key"):
                    continue
                allowed = set(Receipt.__dataclass_fields__)
                receipt = Receipt(**{k: v for k, v in data.items() if k in allowed})
                self._records[f"{receipt.intent}|{receipt.idempotency_key}"] = receipt
        except (OSError, TypeError, ValueError):
            # 台账损坏不阻断回写：本次从空索引开始，下一次写入照常追加
            self._records = {}


# 一个审计台账路径对应一个连接器实例（幂等索引跨调用复用）
_CONNECTORS: dict[str, MockWritebackDataSource] = {}
_CONNECTOR_LOCK = threading.Lock()


def connector_for(outbox_path: str) -> MockWritebackDataSource:
    """按台账路径取连接器单例（进程内）。"""
    key = str(Path(outbox_path).resolve()) if outbox_path else ""
    with _CONNECTOR_LOCK:
        source = _CONNECTORS.get(key)
        if source is None:
            source = MockWritebackDataSource(outbox_path=key,
                                             name="erp-mock" + ("" if not key else "-" +
                                                                _digest(key)[:6]))
            _CONNECTORS[key] = source
        return source


def emit_arrival_receipt(row: dict[str, Any], *, outbox_path: str,
                         actor: str = "",
                         arrival_qty: Optional[float] = None) -> dict[str, Any]:
    """R6 到货确认 → 连接器回写（tier=2：落地 + 事后审计）。

    Args:
        row: 催交行（至少含 ``followup_id`` / ``part_no`` / ``work_order_set``）。
        outbox_path: 与治理库同目录的审计台账路径。
        actor: 确认人（人工到货确认是治理动作，写入回执审计）。
        arrival_qty: 到货数量。

    Returns:
        回执字典（``demo_writeback`` / ``label`` / ``status`` / ``replayed`` 齐全）。
    """
    followup_id = str(row.get("followup_id") or "")
    payload = {
        # 同一催交单的到货回写只落地一次（状态机也保证不能重复 arrival）
        "idempotency_key": f"{followup_id}|arrival",
        "followup_id": followup_id,
        "work_order_set": str(row.get("work_order_set") or ""),
        "part_no": str(row.get("part_no") or ""),
        "supplier": str(row.get("supplier") or ""),
        "arrival_qty": arrival_qty,
        "consent": {"approved": True, "ticket_id": followup_id,
                    "actor": actor, "source": "human_arrival_confirm"},
    }
    connector = connector_for(outbox_path)
    return connector.write_back("erp.purchase_receipt", payload).to_dict()
