"""业务知识中枢数据仓库（R4/D4：BOM 版本 + ECN 变更记录）。

定位（为什么它是「契约先行」的第一个新 store）：

- 项目记忆里的「治理写直连 sqlite3」实为 **6 处模式复制**（workflow /
  report_store / site_board / spc_store / case_schema / orchestrator，
  2026-10-22 架构师读码核实）。R6/R13 若再复制就是第 7 处。本 store 是
  第一个按「契约 ABC + SQLite 实现」范式落的新仓库，为 R13 抽
  ``WorkflowRepository`` 练手（README 生产化清单第 1 条）。
- 数据落**治理轨**（``settings.WORKFLOW_DB``）：知识主数据的写治理与
  工单/会签同库同防线（``assert_sqlite_backed`` fail-fast +
  ``is_sqlite_target`` 方言判定），**不碰业务读轨**——
  ``contracts/data_source.py`` 是 ``mode=ro``，写不进去也不该写
  （「数据质量优先于智能能力」的既有边界）。

表（``knowledge_bom`` / ``knowledge_ecn``）：

- BOM 按 ``(part_no, version)`` 唯一；``current_bom()`` 只认 active——
  「工艺问答答到现行版本」的数据基础；
- ``version_stamp()`` 嵌 L0 缓存键（D6）：ECN 任何写操作 → 戳变 → 旧
  缓存键自然不可达，杜绝「ECN 生效后旧答案照旧命中、按旧版参数做报废
  一批」的事故。

演示种子（``ensure_demo_seed``，幂等）：B 型支架 ECN-0921 焊接参数变更
（参考图场景 1 的事实基准）。**不写** ``data/demo_storyline/*.jsonl``——
该目录由 demo_gen 生成器独占（并发编辑会话），代码种子走
``_ensure_8d_seed`` 同款幂等范式，避免与生成器抢文件。
"""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Optional

from ...common.config import settings
from ...common.platform_schema import sqlite_path_from_url

logger = logging.getLogger(__name__)

__all__ = [
    "KnowledgeRepository",
    "SQLiteKnowledgeRepository",
    "get_knowledge_repository",
    "reset_knowledge_repository",
    "ensure_demo_seed",
]

#: 建表 DDL（幂等：IF NOT EXISTS；新连接首建）。SQLite 专有写法仅限
#: datetime('now') 默认值——本 store 走 assert_sqlite_backed  fail-fast，
#: 不接受非 SQLite 后端（与 WorkflowEngine 同防线）。
_DDL: tuple[str, ...] = (
    """CREATE TABLE IF NOT EXISTS knowledge_bom (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        part_no TEXT NOT NULL,
        part_name TEXT NOT NULL DEFAULT '',
        version TEXT NOT NULL,
        ecn_id TEXT NOT NULL DEFAULT '',
        params_json TEXT NOT NULL DEFAULT '{}',
        effective_date TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'active',
        note TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        UNIQUE(part_no, version)
    )""",
    """CREATE TABLE IF NOT EXISTS knowledge_ecn (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ecn_id TEXT NOT NULL UNIQUE,
        title TEXT NOT NULL DEFAULT '',
        part_no TEXT NOT NULL DEFAULT '',
        effective_date TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'effective',
        doc_ref TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    )""",
)


class KnowledgeRepository(ABC):
    """业务知识契约（BOM 版本 + ECN）。实现可换 SQLite/MySQL——调用方只认契约。

    ``apply_ecn`` 是具体方法（由原语组合）：ECN 生效 = 登记变更 + 新版本
    置 active + 其余活跃版本置 superseded。语义收敛在本契约，换后端不换行为。
    """

    # ---------- 原语（实现方提供） ----------

    @abstractmethod
    def current_bom(self, part_no: str) -> Optional[dict]:
        """某料号现行版本（status=active，取最新生效）。"""

    @abstractmethod
    def bom_versions(self, part_no: str) -> list[dict]:
        """某料号全部版本（新 → 旧）。"""

    @abstractmethod
    def ecn_by_id(self, ecn_id: str) -> Optional[dict]:
        """按 ECN 号取变更记录。"""

    @abstractmethod
    def ecn_list(self, part_no: str = "") -> list[dict]:
        """变更记录列表（可按料号过滤，新 → 旧）。"""

    @abstractmethod
    def search_business(self, query: str, limit: int = 5) -> list[dict]:
        """业务知识检索（BOM/ECN 关键词命中，带 score 降序）。"""

    @abstractmethod
    def pending_withdraw_cards(self, limit: int = 20) -> list[dict]:
        """已作废但尚未撤换的工艺卡（superseded 且无撤换记录）。"""

    @abstractmethod
    def version_stamp(self) -> str:
        """数据版本戳（缓存命名空间用；任何写操作必须改戳）。"""

    @abstractmethod
    def upsert_bom(self, part_no: str, version: str, *, part_name: str = "",
                   ecn_id: str = "", params: Optional[dict] = None,
                   effective_date: str = "", status: str = "active",
                   note: str = "") -> dict:
        """写入/更新一个 BOM 版本（按 part_no+version 幂等）。"""

    @abstractmethod
    def insert_ecn(self, ecn_id: str, *, title: str = "", part_no: str = "",
                   effective_date: str = "", doc_ref: str = "") -> dict:
        """登记一条 ECN（按 ecn_id 幂等）。"""

    # ---------- 组合语义（契约层收敛，实现无关） ----------

    def apply_ecn(self, ecn_id: str, part_no: str, new_version: str,
                  params: dict, *, title: str = "", effective_date: str = "",
                  doc_ref: str = "") -> dict:
        """ECN 生效：登记变更 + 新版本置 active + 旧活跃版本置 superseded。"""
        ecn = self.insert_ecn(ecn_id, title=title or f"{part_no} 工艺变更",
                              part_no=part_no, effective_date=effective_date,
                              doc_ref=doc_ref)
        row = self.upsert_bom(part_no, new_version, ecn_id=ecn_id,
                              params=params, effective_date=effective_date,
                              status="active")
        for old in self.bom_versions(part_no):
            if old["version"] == new_version or old["status"] != "active":
                continue
            self.upsert_bom(part_no, old["version"], status="superseded",
                            part_name=old.get("part_name", ""),
                            ecn_id=old.get("ecn_id", ""),
                            params=old.get("params") or {},
                            effective_date=old.get("effective_date", ""),
                            note=old.get("note", ""))
        return {"ecn": ecn, "bom": row}


def _row_to_bom(row: sqlite3.Row) -> dict:
    out = dict(row)
    try:
        out["params"] = json.loads(out.pop("params_json") or "{}")
    except (TypeError, ValueError):
        out["params"] = {}
    return out


class SQLiteKnowledgeRepository(KnowledgeRepository):
    """SQLite 实现（治理轨同库；「契约 + 实现」范式的样板）。"""

    def __init__(self, db_path: Optional[str] = None) -> None:
        if db_path is None:
            from .workflow import assert_sqlite_backed
            assert_sqlite_backed(settings.WORKFLOW_DB)  # fail-fast 同防线
            db_path = sqlite_path_from_url(settings.WORKFLOW_DB)
        self.db_path = str(db_path)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()

    # ---------- 连接（线程本地；DDL 幂等随连随建） ----------

    def _connect(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self.db_path, timeout=10.0)
            conn.row_factory = sqlite3.Row
            for ddl in _DDL:
                conn.execute(ddl)
            conn.commit()
            self._local.conn = conn
        return conn

    def close(self) -> None:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None

    # ---------- 读 ----------

    def current_bom(self, part_no: str) -> Optional[dict]:
        row = self._connect().execute(
            "SELECT * FROM knowledge_bom WHERE part_no = :p "
            "AND status = 'active' ORDER BY effective_date DESC, id DESC LIMIT 1",
            {"p": part_no}).fetchone()
        return _row_to_bom(row) if row else None

    def bom_versions(self, part_no: str) -> list[dict]:
        rows = self._connect().execute(
            "SELECT * FROM knowledge_bom WHERE part_no = :p "
            "ORDER BY effective_date DESC, id DESC", {"p": part_no}).fetchall()
        return [_row_to_bom(r) for r in rows]

    def ecn_by_id(self, ecn_id: str) -> Optional[dict]:
        row = self._connect().execute(
            "SELECT * FROM knowledge_ecn WHERE ecn_id = :e",
            {"e": ecn_id}).fetchone()
        return dict(row) if row else None

    def ecn_list(self, part_no: str = "") -> list[dict]:
        sql = "SELECT * FROM knowledge_ecn"
        args: dict[str, Any] = {}
        if part_no:
            sql += " WHERE part_no = :p"
            args["p"] = part_no
        rows = self._connect().execute(
            sql + " ORDER BY effective_date DESC, id DESC", args).fetchall()
        return [dict(r) for r in rows]

    def search_business(self, query: str, limit: int = 5) -> list[dict]:
        """关键词检索：拉丁/数字整词 + CJK 二元组，命中 part_no/料名/
        ECN 号/标题/参数文本计分；active 行加权（现行版本优先）。"""
        import re

        norm = (query or "").lower()
        tokens: set[str] = set()
        for m in re.finditer(r"[a-z0-9][a-z0-9\-\.]{1,}", norm):
            tokens.add(m.group(0))
        for m in re.finditer(r"[\u4e00-\u9fff]+", norm):
            run = m.group(0)
            if len(run) == 1:
                tokens.add(run)
            for i in range(len(run) - 1):
                tokens.add(run[i:i + 2])
        tokens.discard("")
        if not tokens:
            return []

        hits: list[dict] = []
        for bom in self._all_boms():
            text = " ".join([
                str(bom.get("part_no") or ""), str(bom.get("part_name") or ""),
                str(bom.get("ecn_id") or ""),
                json.dumps(bom.get("params") or {}, ensure_ascii=False),
            ]).lower()
            score = sum(2.0 if t in str(bom.get("part_no") or "").lower()
                        or t in str(bom.get("ecn_id") or "").lower()
                        else 1.0 for t in tokens if t in text)
            if score > 0 and bom.get("status") == "active":
                score += 2.0  # 现行版本加权：参数类问法要的是现行版，不是变更记录
            if score > 0:
                hits.append({"kind": "bom", "score": round(score, 2),
                             "part_no": bom["part_no"], "version": bom["version"],
                             "status": bom["status"], "ecn_id": bom["ecn_id"],
                             "params": bom.get("params") or {},
                             "effective_date": bom.get("effective_date") or "",
                             "label": f"{bom['part_no']} {bom['version']}"
                                      f"（{bom.get('part_name') or ''}）"})
        for ecn in self._all_ecns():
            text = " ".join([str(ecn.get("ecn_id") or ""),
                             str(ecn.get("title") or ""),
                             str(ecn.get("part_no") or "")]).lower()
            score = sum(2.0 if t in str(ecn.get("ecn_id") or "").lower()
                        else 1.0 for t in tokens if t in text)
            if score > 0:
                hits.append({"kind": "ecn", "score": round(score, 2),
                             "ecn_id": ecn["ecn_id"], "title": ecn.get("title") or "",
                             "part_no": ecn.get("part_no") or "",
                             "effective_date": ecn.get("effective_date") or "",
                             "label": f"{ecn['ecn_id']}（{ecn.get('title') or ''}）"})
        hits.sort(key=lambda h: h["score"], reverse=True)
        return hits[:limit]

    def pending_withdraw_cards(self, limit: int = 20) -> list[dict]:
        """已作废版本 = 待撤换工艺卡（附带现行版本号，便于派待办）。"""
        rows = self._connect().execute(
            "SELECT * FROM knowledge_bom WHERE status = 'superseded' "
            "ORDER BY effective_date DESC, id DESC LIMIT :lim",
            {"lim": int(limit)}).fetchall()
        out = []
        for r in rows:
            bom = _row_to_bom(r)
            current = self.current_bom(bom["part_no"])
            out.append({
                "part_no": bom["part_no"], "version": bom["version"],
                "part_name": bom.get("part_name") or "",
                "superseded_by": (current or {}).get("version") or "",
                "card_ref": f"工艺卡 {bom['part_no']} {bom['version']}",
                "note": bom.get("note") or "",
            })
        return out

    def version_stamp(self) -> str:
        row = self._connect().execute(
            "SELECT (SELECT COUNT(*) FROM knowledge_bom) AS b_cnt, "
            "       (SELECT IFNULL(MAX(id), 0) FROM knowledge_bom) AS b_max, "
            "       (SELECT COUNT(*) FROM knowledge_ecn) AS e_cnt, "
            "       (SELECT IFNULL(MAX(id), 0) FROM knowledge_ecn) AS e_max"
        ).fetchone()
        return (f"bom:{row['b_cnt']}:{row['b_max']}"
                f"|ecn:{row['e_cnt']}:{row['e_max']}")

    # ---------- 写 ----------

    def upsert_bom(self, part_no: str, version: str, *, part_name: str = "",
                   ecn_id: str = "", params: Optional[dict] = None,
                   effective_date: str = "", status: str = "active",
                   note: str = "") -> dict:
        conn = self._connect()
        with conn:
            conn.execute(
                "INSERT INTO knowledge_bom (part_no, part_name, version, ecn_id, "
                "params_json, effective_date, status, note) "
                "VALUES (:p, :n, :v, :e, :j, :d, :s, :note) "
                "ON CONFLICT(part_no, version) DO UPDATE SET "
                "part_name=excluded.part_name, ecn_id=excluded.ecn_id, "
                "params_json=excluded.params_json, "
                "effective_date=excluded.effective_date, "
                "status=excluded.status, note=excluded.note",
                {"p": part_no, "n": part_name, "v": version, "e": ecn_id,
                 "j": json.dumps(params or {}, ensure_ascii=False),
                 "d": effective_date, "s": status, "note": note})
        row = conn.execute(
            "SELECT * FROM knowledge_bom WHERE part_no = :p AND version = :v",
            {"p": part_no, "v": version}).fetchone()
        return _row_to_bom(row)

    def insert_ecn(self, ecn_id: str, *, title: str = "", part_no: str = "",
                   effective_date: str = "", doc_ref: str = "") -> dict:
        conn = self._connect()
        with conn:
            conn.execute(
                "INSERT INTO knowledge_ecn (ecn_id, title, part_no, "
                "effective_date, doc_ref) VALUES (:e, :t, :p, :d, :r) "
                "ON CONFLICT(ecn_id) DO NOTHING",
                {"e": ecn_id, "t": title, "p": part_no,
                 "d": effective_date, "r": doc_ref})
        row = conn.execute(
            "SELECT * FROM knowledge_ecn WHERE ecn_id = :e",
            {"e": ecn_id}).fetchone()
        return dict(row)

    # ---------- 内部 ----------

    def _all_boms(self) -> list[dict]:
        rows = self._connect().execute(
            "SELECT * FROM knowledge_bom ORDER BY id DESC").fetchall()
        return [_row_to_bom(r) for r in rows]

    def _all_ecns(self) -> list[dict]:
        rows = self._connect().execute(
            "SELECT * FROM knowledge_ecn ORDER BY id DESC").fetchall()
        return [dict(r) for r in rows]


# ---------------------------------------------------------------- 单例

_repo: Optional[SQLiteKnowledgeRepository] = None
_repo_lock = threading.Lock()


def get_knowledge_repository(db_path: Optional[str] = None,
                             force_reload: bool = False) -> KnowledgeRepository:
    """知识仓储单例（默认治理轨 WORKFLOW_DB；测试可注入 tmp 路径）。"""
    global _repo
    if db_path is not None:
        return SQLiteKnowledgeRepository(db_path=db_path)
    if _repo is not None and not force_reload:
        return _repo
    with _repo_lock:
        if _repo is None or force_reload:
            _repo = SQLiteKnowledgeRepository()
        return _repo


def reset_knowledge_repository() -> None:
    """清单例（测试隔离用）。"""
    global _repo
    with _repo_lock:
        _repo = None


# ---------------------------------------------------------------- 演示种子

#: (ecn_id, title, part_no, part_name, new_version, params, effective_date, doc_ref)
_DEMO_ECNS: tuple[tuple[str, str, str, str, str, dict, str, str], ...] = (
    ("ECN-0921", "B 型支架新材料焊接工艺变更", "BKT-B01", "B型支架", "V3",
     {"焊接电流": "180A", "焊接速度": "35cm/min", "保护气": "氩气 99.99%",
      "预热温度": "120℃", "依据": "ECN-0921 附件《B型支架焊接作业指导书》V3"},
     "2026-09-21", "WI-Weld-B01-V3"),
    ("ECN-0915", "电机支架轴承装配公差变更", "MTR-M02", "电机支架", "V2",
     {"轴承型号": "6204-2RS", "装配公差": "H7/k6", "压装力": "1.2kN",
      "拧紧扭矩": "12N·m", "依据": "ECN-0915 附件《电机支架装配作业指导书》V2"},
     "2026-09-15", "WI-Asm-M02-V2"),
)

#: ECN 前的旧版本（superseded，制造「旧卡待撤换」事实）
_DEMO_OLD_VERSIONS: tuple[tuple[str, str, str, dict, str], ...] = (
    ("BKT-B01", "V2", "B型支架",
     {"焊接电流": "160A", "焊接速度": "30cm/min", "保护气": "氩气 99.99%"},
     "V2 版工艺卡尚未撤换，随 ECN-0921 生效作废"),
    ("MTR-M02", "V1", "电机支架",
     {"轴承型号": "6204", "装配公差": "H7/js6", "压装力": "1.0kN"},
     "V1 版工艺卡尚未撤换，随 ECN-0915 生效作废"),
)


def ensure_demo_seed(repo: Optional[KnowledgeRepository] = None) -> dict:
    """幂等落入演示知识种子（ECN + BOM 新旧版本；重复执行行数不变）。"""
    repo = repo or get_knowledge_repository()
    before = repo.version_stamp()
    for ecn_id, title, part_no, part_name, ver, params, eff, ref in _DEMO_ECNS:
        repo.apply_ecn(ecn_id, part_no, ver, params, title=title,
                       effective_date=eff, doc_ref=ref)
    for part_no, old_ver, part_name, params, note in _DEMO_OLD_VERSIONS:
        repo.upsert_bom(part_no, old_ver, part_name=part_name, params=params,
                        status="superseded", note=note)
    return {"stamp_before": before, "stamp_after": repo.version_stamp()}
