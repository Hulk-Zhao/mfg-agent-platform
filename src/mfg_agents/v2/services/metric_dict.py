"""数据口径词典（R8 · D12 三源汇总）。

产品目标（PRD P1 R8）：看板 / 问答 / 报告引用同一口径，消灭「各看各的表」。
口径当前散落三处——注册查询 ``data/queries.yaml``、指标库
``data/metrics/*.yaml``、看板 KPI（``dashboard.DRILL_DEFS``）。本模块把三源
**只读汇总**成统一词条，每条带 ``id / 口径说明 / 引用方列表``，并在覆盖说明里
明确「哪些来源、各多少条」，避免只汇总其中一源而制造新的双源（架构质疑 Q8）。

纪律：
- 只读枚举，不执行任何查询；看板 KPI 口径来自静态 ``DRILL_DEFS``，不跑聚合；
- 同名口径**不合并**：来源不同就分别成条（例如看板的两个「来料批次合格率」），
  让调用方看到「同名词条可能有两个出处」，而不是假装只有一个权威定义；
- 引用方列表由源码扫描得出（哪个模块引用了该查询/指标），不维护第二份手写映射；
- 任一来源加载失败只记覆盖缺口，不拖垮另外两源。
"""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable, Optional

from .text2sql.libraries import MetricLibrary, QueryLibrary

__all__ = [
    "SOURCE_QUERY",
    "SOURCE_METRIC",
    "SOURCE_BOARD",
    "SOURCE_LABELS",
    "build_dictionary",
    "search_dictionary",
    "get_entry",
    "resolve_entry",
]

SOURCE_QUERY = "registered_query"
SOURCE_METRIC = "metric_library"
SOURCE_BOARD = "dashboard_kpi"

SOURCE_LABELS = {
    SOURCE_QUERY: "注册查询",
    SOURCE_METRIC: "指标库",
    SOURCE_BOARD: "看板 KPI",
}

_SOURCE_ORDER = {SOURCE_QUERY: 0, SOURCE_METRIC: 1, SOURCE_BOARD: 2}

_V2_DIR = Path(__file__).resolve().parents[1]
_DEFAULT_DATA_DIR = _V2_DIR / "data"

_SCREEN_OWNERS = {
    "manufacturing": "制造域",
    "quality": "质量域",
    "orders": "供应链域",
    "spc": "质量域",
}


# ---------------------------------------------------------------- 引用方扫描


@lru_cache(maxsize=4)
def _reference_index(data_dir: str) -> dict[str, list[str]]:
    """扫描 ``v2/`` 源码，返回「查询/指标 id → 引用它的相对文件路径」。

    排除定义本身（``queries.yaml`` / ``metrics/*.yaml``），只统计真实调用点。
    结果按路径排序，进程内缓存（源码在运行期不变）。
    """
    root = _V2_DIR
    data_path = Path(data_dir)
    try:
        names = list(QueryLibrary.load(data_path / "queries.yaml").all().keys())
    except Exception:  # noqa: BLE001 - 词典不应因单一来源失败而崩溃
        names = []

    index: dict[str, list[str]] = {name: [] for name in names}
    if not names:
        return index
    patterns = {
        name: re.compile(r"(?<![A-Za-z0-9_])" + re.escape(name)
                         + r"(?![A-Za-z0-9_])")
        for name in names
    }

    for path in sorted(root.rglob("*")):
        if path.suffix not in {".py", ".yaml", ".yml"}:
            continue
        if path == data_path / "queries.yaml":
            continue
        if path == Path(__file__).resolve():
            continue
        if data_path / "metrics" in path.parents:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        rel = path.relative_to(root).as_posix()
        for name, pattern in patterns.items():
            if pattern.search(text):
                index[name].append(rel)
    return {name: refs for name, refs in index.items() if refs}


def _display_query_name(name: str, desc: str) -> str:
    """从 ``desc`` 取出业务名（「TMS：XXX（...）」→「XXX」），取不到回落查询名。"""
    text = str(desc or "").strip()
    if "：" in text:
        text = text.split("：", 1)[1]
    for sep in ("（", "("):
        if sep in text:
            text = text.split(sep, 1)[0]
    text = text.strip()
    return text or name


# ---------------------------------------------------------------- 词条构造


def _query_entries(data_dir: Path, refs: dict[str, list[str]]) -> list[dict[str, Any]]:
    lib = QueryLibrary.load(data_dir / "queries.yaml")
    entries: list[dict[str, Any]] = []
    for name, spec in sorted(lib.all().items()):
        desc = str(spec.get("desc") or "")
        entries.append({
            "id": f"query.{name}",
            "name": _display_query_name(name, desc),
            "source": SOURCE_QUERY,
            "source_label": SOURCE_LABELS[SOURCE_QUERY],
            "owner": "",
            "definition": desc,
            "tables": list(spec.get("tables") or []),
            "keywords": list(spec.get("keywords") or []),
            "formula": str(spec.get("sql") or ""),
            "unit": "",
            "target": None,
            "higher_is_better": None,
            "screen": "",
            "refs": ["问数 · 注册查询通道", "Agentic · explore_data",
                     *refs.get(name, [])],
        })
    return entries


def _metric_entries(data_dir: Path, refs: dict[str, list[str]]) -> list[dict[str, Any]]:
    lib = MetricLibrary.load(data_dir / "metrics")
    entries: list[dict[str, Any]] = []
    for metric in lib.all():
        metric_id = str(metric["id"])
        entries.append({
            "id": f"metric.{metric_id}",
            "name": str(metric.get("name") or metric_id),
            "source": SOURCE_METRIC,
            "source_label": SOURCE_LABELS[SOURCE_METRIC],
            "owner": str(metric.get("owner") or ""),
            "definition": str(metric.get("desc") or ""),
            "tables": list(metric.get("tables") or []),
            "keywords": list(metric.get("keywords") or []),
            "formula": str(metric.get("sql") or ""),
            "unit": str(metric.get("unit") or ""),
            "target": metric.get("target"),
            "higher_is_better": metric.get("higher_is_better"),
            "screen": "",
            "refs": ["问数 · 指标库通道", "MCP · metric.dict",
                     *refs.get(metric_id, [])],
        })
    return entries


def _board_entries() -> list[dict[str, Any]]:
    from .dashboard import DRILL_DEFS

    entries: list[dict[str, Any]] = []
    for key, defn in sorted(DRILL_DEFS.items()):
        screen = str(defn.get("screen") or "")
        num = str(defn.get("num_expr") or "")
        den = str(defn.get("den_expr") or "")
        formula = f"{num} ÷ {den}" if num and den else num
        entries.append({
            "id": f"board.{screen}.{key}",
            "name": str(defn.get("label") or key),
            "source": SOURCE_BOARD,
            "source_label": SOURCE_LABELS[SOURCE_BOARD],
            "owner": _SCREEN_OWNERS.get(screen, "看板"),
            "definition": str(defn.get("definition") or ""),
            "tables": [str(defn["table"])] if defn.get("table") else [],
            "keywords": [],
            "formula": formula,
            "unit": str(defn.get("unit") or ""),
            "target": defn.get("target"),
            "higher_is_better": defn.get("higher_is_better"),
            "screen": screen,
            "refs": [f"看板 · {_SCREEN_OWNERS.get(screen, screen or '未知')}",
                     "看板 KPI 卡 / L2·L3 下钻"],
        })
    return entries


# ---------------------------------------------------------------- 对外接口


def build_dictionary(data_dir: Optional[str | Path] = None) -> dict[str, Any]:
    """三源汇总词典（只读；任一来源失败只记覆盖缺口，不抛）。"""
    data_path = Path(data_dir) if data_dir else _DEFAULT_DATA_DIR
    refs = _reference_index(str(data_path))

    entries: list[dict[str, Any]] = []
    source_rows: list[dict[str, Any]] = []
    gaps: list[str] = []

    loaders = (
        (SOURCE_QUERY, "queries.yaml 注册查询（问数 / Agent 共用）",
         lambda: _query_entries(data_path, refs)),
        (SOURCE_METRIC, "metrics/*.yaml 指标定义即代码（口径唯一）",
         lambda: _metric_entries(data_path, refs)),
        (SOURCE_BOARD, "dashboard.DRILL_DEFS 看板 KPI 口径（静态，不执行查询）",
         _board_entries),
    )
    for source, description, loader in loaders:
        try:
            rows = loader()
        except Exception as exc:  # noqa: BLE001 - 单源失败不拖垮词典
            gaps.append(f"{SOURCE_LABELS[source]}不可用：{exc}")
            rows = []
        entries.extend(rows)
        source_rows.append({
            "key": source,
            "label": SOURCE_LABELS[source],
            "count": len(rows),
            "description": description,
        })

    entries.sort(key=lambda e: (
        _SOURCE_ORDER.get(e["source"], 9), e["name"], e["id"]))
    counts = {row["key"]: row["count"] for row in source_rows}
    note = (
        f"三源汇总：注册查询 {counts.get(SOURCE_QUERY, 0)} 条 + "
        f"指标库 {counts.get(SOURCE_METRIC, 0)} 条 + "
        f"看板 KPI {counts.get(SOURCE_BOARD, 0)} 条。"
        "同名口径不合并（来源不同分别成条）；看板口径来自静态 DRILL_DEFS，"
        "不执行聚合查询。"
    )
    if gaps:
        note += "覆盖缺口：" + "；".join(gaps)

    return {
        "entries": entries,
        "count": len(entries),
        "sources": source_rows,
        "coverage": {
            "total": len(entries),
            "by_source": counts,
            "gaps": gaps,
            "note": note,
        },
    }


def search_dictionary(query: str = "", source: str = "",
                      data_dir: Optional[str | Path] = None) -> list[dict[str, Any]]:
    """按关键词 / 来源过滤词条（关键词匹配 id / 名称 / 说明 / 引用方）。"""
    bundle = build_dictionary(data_dir)
    entries: Iterable[dict[str, Any]] = bundle["entries"]
    if source:
        entries = [e for e in entries if e["source"] == source]
    kw = str(query or "").strip().lower()
    if not kw:
        return list(entries)
    out = []
    for entry in entries:
        hay = " ".join(str(part) for part in (
            entry.get("id"), entry.get("name"), entry.get("definition"),
            entry.get("owner"), " ".join(entry.get("keywords") or []),
            " ".join(entry.get("tables") or []),
            " ".join(entry.get("refs") or []),
        ))
        if kw in hay.lower():
            out.append(entry)
    return out


def get_entry(entry_id: str,
              data_dir: Optional[str | Path] = None) -> Optional[dict[str, Any]]:
    """按完整词条 id 取单条（``query.*`` / ``metric.*`` / ``board.*``）。"""
    if not entry_id:
        return None
    for entry in build_dictionary(data_dir)["entries"]:
        if entry["id"] == entry_id:
            return entry
    return None


def resolve_entry(key: str,
                  data_dir: Optional[str | Path] = None) -> Optional[dict[str, Any]]:
    """兼容旧调用：指标 id / 注册查询名 / 看板 key 均可解析。"""
    key = str(key or "").strip()
    if not key:
        return None
    direct = get_entry(key, data_dir)
    if direct is not None:
        return direct
    for prefix in ("query.", "metric.", "board."):
        hit = get_entry(prefix + key, data_dir)
        if hit is not None:
            return hit
    board_hits = [
        entry for entry in build_dictionary(data_dir)["entries"]
        if entry["source"] == SOURCE_BOARD and entry["id"].endswith("." + key)
    ]
    return board_hits[0] if len(board_hits) == 1 else None
