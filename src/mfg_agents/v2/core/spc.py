"""SPC 统计内核（纯函数，零框架 / 零数据源依赖）。

移植自 Hogo-QA-tool 的 ``src/core/**``——该内核被其 ESLint 强制「零 React / 零 DOM」，
本质是一批确定性纯函数。本模块按平台铁律「业务能力一律收敛为**确定性工具**」把它抽为
Python 纯函数：给定输入 → 确定输出，可独立单测，不 import 任何 Web/框架/数据源。

核心口径（**必须保留，勿走样**）——双西格玛：
- ``Cp / Cpk`` 用**组内** σ（σ_within；Xbar-R 走 R̄/d2）；
- ``Pp / Ppk`` 用**整体** σ（σ_overall；样本标准差 ddof=1）。

旧工具 ``std_sub = std_total`` 会让 Pp 恒等于 Cp，抹平组内/整体差异——本模块按标准口径
拆开，并以单测钉死（见 ``tests/v2/test_mcp_tools_spc.py``）。

放置说明：未落在 ``mfg_agents/tools/`` 下——该包 ``__init__`` 会连带 import
``rag_tools``（torch/FlagEmbedding 重依赖），任何一种导入都会触发它；本内核零依赖，
故就近放在 v2 ``core`` 下。
"""
from __future__ import annotations

import math
from typing import Any

__all__ = [
    "compute_capability",
    "build_pareto",
    "normalize_measurements",
    "sigma_within_from_ranges",
    "sigma_overall",
    "D2_CONSTANTS",
    "CPK_GOOD",
    "CPK_MARGINAL",
]

# 控制图常数（Xbar-R 的 d2），子组容量 n = 2..15
D2_CONSTANTS: dict[int, float] = {
    2: 1.128, 3: 1.693, 4: 2.059, 5: 2.326, 6: 2.534, 7: 2.704, 8: 2.847,
    9: 2.970, 10: 3.078, 11: 3.173, 12: 3.258, 13: 3.336, 14: 3.407, 15: 3.472,
}

CPK_GOOD = 1.33       # Cpk ≥ 1.33：过程能力合格
CPK_MARGINAL = 1.00   # 1.00 ≤ Cpk < 1.33：偏低；Cpk < 1.00：不合格


def _round(value: float | None, digits: int = 4) -> float | None:
    """四舍五入；None / NaN / Inf 一律归一为 None（不编造数字）。"""
    if value is None:
        return None
    try:
        fv = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(fv) or math.isinf(fv):
        return None
    return round(fv, digits)


def _mean(values: list[float]) -> float | None:
    return (sum(values) / len(values)) if values else None


def _gap(msg: str, **extra: Any) -> dict[str, Any]:
    """诚实降级的统一形状：显式数据缺口 + 原因，绝不补齐"看起来对"的数字。"""
    out: dict[str, Any] = {"data_gap": True, "warnings": [msg]}
    out.update(extra)
    return out


def sigma_overall(values: list[float]) -> float | None:
    """整体标准差（样本，ddof=1）—— ``Pp / Ppk`` 的 σ 口径。"""
    n = len(values)
    if n < 2:
        return None
    mu = sum(values) / n
    var = sum((x - mu) ** 2 for x in values) / (n - 1)
    return math.sqrt(var)


def sigma_within_from_ranges(ranges: list[float], subgroup_size: int) -> float | None:
    """组内标准差 σ_within = R̄ / d2 —— ``Cp / Cpk`` 的 σ 口径（Xbar-R）。

    子组容量超 d2 常数表（2..15）时返回 None（触发诚实降级，而非近似糊弄）。
    """
    d2 = D2_CONSTANTS.get(int(subgroup_size))
    if d2 is None or not ranges:
        return None
    return (sum(ranges) / len(ranges)) / d2


def _normality(values: list[float]) -> dict[str, Any]:
    """正态性**经验提示**：偏度 / 峰度（纯 Python，不引入 scipy 依赖）。

    不是严格正态性检验（如 Shapiro-Wilk），只给经验提示——样本偏离正态时能力指数
    仅供参考。严守「不编造」铁律：结论标注为「指标」而非「检验」。
    """
    n = len(values)
    if n < 8:
        return {"available": False, "method": None,
                "note": "样本不足（<8），不做正态性评估"}
    mu = sum(values) / n
    sd = sigma_overall(values)
    if not sd:
        return {"available": False, "method": None, "note": "σ=0，无法评估正态性"}
    skew = sum(((x - mu) / sd) ** 3 for x in values) / n
    kurt = sum(((x - mu) / sd) ** 4 for x in values) / n - 3.0
    ok = abs(skew) <= 0.5 and abs(kurt) <= 1.0
    return {
        "available": True, "method": "偏度/峰度经验指标",
        "skewness": _round(skew), "excess_kurtosis": _round(kurt),
        "normal": bool(ok),
        "note": "偏度/峰度在经验阈内，近似正态" if ok
                else "偏度/峰度偏离经验阈，正态性存疑，能力指数仅供参考",
    }


def _to_subgroups(flat: list[float], size: int) -> tuple[list[list[float]], list[str]]:
    """把扁平测量值按子组容量切分；不能整除时丢弃末尾不足一组（显式告警）。"""
    if size < 2:
        raise ValueError("subgroup_size 必须 ≥ 2")
    groups = [flat[i:i + size] for i in range(0, len(flat), size)]
    warnings: list[str] = []
    if len(flat) % size:
        warnings.append(
            f"测量值 {len(flat)} 条不能被子组容量 {size} 整除，末尾不足一组已丢弃")
        groups = groups[:-1]
    return groups, warnings


def compute_capability(*, usl: float, lsl: float, values: Any = None,
                       subgroups: Any = None, subgroup_size: int = 5,
                       characteristic: str = "") -> dict[str, Any]:
    """过程能力分析：Cp/Cpk（组内 σ）与 Pp/Ppk（整体 σ）**双口径**。

    输入二选一：``subgroups``（已分组）或 ``values``（扁平，按 ``subgroup_size`` 切分）；
    规格限 ``usl`` / ``lsl`` 必填。数据不足或口径不可估时返回 ``data_gap=True`` 并说明
    原因，绝不编造指数。
    """
    warnings: list[str] = []
    gap_kw = {"characteristic": characteristic, "verdict": "不可判定", "cp": None,
              "cpk": None, "pp": None, "ppk": None, "sigma_within": None,
              "sigma_overall": None}
    try:
        usl_f, lsl_f = float(usl), float(lsl)
    except (TypeError, ValueError):
        return _gap("规格限 usl/lsl 必须为数值", **gap_kw)
    if usl_f <= lsl_f:
        return _gap("规格限非法：usl 必须大于 lsl", **gap_kw)

    try:
        if subgroups:
            groups = [[float(x) for x in g] for g in subgroups if g]
        elif values:
            groups, warnings = _to_subgroups([float(v) for v in values], int(subgroup_size))
        else:
            return _gap("未提供测量值（values 或 subgroups）", **gap_kw)
    except (TypeError, ValueError) as exc:
        return _gap(f"测量值非法：{exc}", **gap_kw)

    if not groups:
        return _gap("有效子组为空", **gap_kw)

    # 子组容量以众数为准；容量不一致的子组剔除以保证 σ 口径自洽（显式告警）
    sizes = [len(g) for g in groups]
    n = max(set(sizes), key=sizes.count)
    dropped = {i for i, s in enumerate(sizes) if s != n}
    if dropped:
        groups = [g for i, g in enumerate(groups) if i not in dropped]
        warnings.append(f"子组容量不一致，已剔除 {len(dropped)} 个非 n={n} 子组")
    if len(groups) < 2:
        return _gap("有效子组不足 2 组，无法估计组内 σ", **gap_kw)

    flat = [x for g in groups for x in g]
    mean = _mean(flat)
    sd_overall = sigma_overall(flat)
    sd_within = sigma_within_from_ranges(
        [max(g) - min(g) for g in groups], n)
    if sd_within is None:
        warnings.append(
            f"子组容量 n={n} 超出 d2 常数表(2..15)，组内 σ 不可估 → Cp/Cpk 暂缺")

    def _index(sd: float | None) -> tuple[float | None, float | None]:
        if not sd:
            return None, None
        cp = (usl_f - lsl_f) / (6.0 * sd)
        cpk = min((usl_f - mean) / (3.0 * sd), (mean - lsl_f) / (3.0 * sd))
        return cp, cpk

    cp, cpk = _index(sd_within)
    pp, ppk = _index(sd_overall)
    basis = cpk if cpk is not None else ppk
    if basis is None:
        verdict = "不可判定"
    elif basis >= CPK_GOOD:
        verdict = "合格"
    elif basis >= CPK_MARGINAL:
        verdict = "偏低"
    else:
        verdict = "不合格"

    return {
        "characteristic": characteristic,
        "data_gap": False,
        "spec": {"usl": _round(usl_f), "lsl": _round(lsl_f)},
        "n_total": len(flat), "n_subgroups": len(groups), "subgroup_size": n,
        "mean": _round(mean),
        "sigma_within": _round(sd_within), "sigma_overall": _round(sd_overall),
        "cp": _round(cp), "cpk": _round(cpk), "pp": _round(pp), "ppk": _round(ppk),
        "sigma_level_short_term": _round(3.0 * cpk) if cpk is not None else None,
        "sigma_level_long_term": _round(3.0 * cpk + 1.5) if cpk is not None else None,
        "verdict": verdict,
        "thresholds": {"good": CPK_GOOD, "marginal": CPK_MARGINAL},
        "normality": _normality(flat),
        "warnings": warnings,
    }


def _finite_number(value: Any) -> float | None:
    """把输入转成有限浮点数；bool / 非数值 / NaN / Inf 一律 None（不合规即拒绝）。"""
    if isinstance(value, bool):
        return None
    try:
        fv = float(value)
    except (TypeError, ValueError):
        return None
    return fv if math.isfinite(fv) else None


def normalize_measurements(*, characteristic: str, values: Any = None,
                           subgroups: Any = None, subgroup_size: int = 5,
                           usl: float | None = None, lsl: float | None = None,
                           measured_at: str | None = None,
                           source: str | None = None) -> dict[str, Any]:
    """校验并归一化测量值录入（纯函数，不落库）。

    与 ``compute_capability`` 同输入形状（``values`` 扁平 / ``subgroups`` 已分组），
    归一为统一的 ``{characteristic, subgroup_no, seq, value, usl, lsl, measured_at,
    source}`` 行序列；每条值必须为**有限数值**。非法值**不静默丢弃**——进 ``rejected``
    并附原因，绝不编造/补齐（供 ``spc.ingest`` 落库前的确定性校验）。
    """
    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    warnings: list[str] = []

    seq_rows: list[tuple[int, int, Any]] = []
    if subgroups:
        for gi, group in enumerate(subgroups):
            if not isinstance(group, (list, tuple)):
                rejected.append({"index": gi, "reason": "子组必须是数值列表",
                                 "record": group})
                continue
            for si, value in enumerate(group):
                seq_rows.append((gi + 1, si + 1, value))
    elif values:
        try:
            size = int(subgroup_size)
        except (TypeError, ValueError):
            size = 0
        if size < 2:
            warnings.append("subgroup_size 必须 ≥ 2，无法切分子组")
            return {"accepted": [], "rejected": [], "n_accepted": 0,
                    "n_rejected": 0, "warnings": warnings}
        for i, value in enumerate(values):
            seq_rows.append((i // size + 1, i % size + 1, value))
    else:
        warnings.append("未提供测量值（values 或 subgroups）")
        return {"accepted": [], "rejected": [], "n_accepted": 0,
                "n_rejected": 0, "warnings": warnings}

    for idx, (subgroup_no, seq, raw) in enumerate(seq_rows):
        num = _finite_number(raw)
        if num is None:
            rejected.append({"index": idx, "subgroup_no": subgroup_no, "seq": seq,
                             "reason": "非有限数值", "record": raw})
            continue
        accepted.append({"characteristic": characteristic, "subgroup_no": subgroup_no,
                         "seq": seq, "value": num, "usl": usl, "lsl": lsl,
                         "measured_at": measured_at, "source": source})
    if rejected:
        warnings.append(f"{len(rejected)} 条非法测量值已拒绝（未编造/未补齐）")
    return {"accepted": accepted, "rejected": rejected,
            "n_accepted": len(accepted), "n_rejected": len(rejected),
            "warnings": warnings}


def build_pareto(items: Any, *, threshold: float = 0.8,
                 name_key: str = "name", qty_key: str = "qty") -> dict[str, Any]:
    """柏拉图：不良项降序 + 累计占比 + 关键少数（累计首达 threshold 的项目集合）。

    ``items`` 形如 ``[{"name": ..., "qty": ...}, ...]``。无有效项时 ``data_gap=True``。
    """
    pairs: list[tuple[str, float]] = []
    for it in items or []:
        if not isinstance(it, dict):
            continue
        name = str(it.get(name_key) or "").strip()
        try:
            qty = float(it.get(qty_key))
        except (TypeError, ValueError):
            continue
        if name and qty > 0:
            pairs.append((name, qty))
    total = sum(q for _, q in pairs)
    if not pairs or total <= 0:
        return _gap("无可统计的不良项/数量", total=0, count=0, items=[],
                    vital_few=[], vital_few_count=0, threshold=threshold)

    pairs.sort(key=lambda r: (-r[1], r[0]))
    out: list[dict[str, Any]] = []
    cumulative = 0.0
    vital = 0
    crossed = False
    for name, qty in pairs:
        pct = qty / total
        cumulative += pct
        out.append({"name": name, "qty": _round(qty),
                    "pct": _round(pct), "cum_pct": _round(cumulative)})
        if not crossed:
            vital += 1
            if cumulative >= threshold - 1e-12:
                crossed = True
    return {
        "data_gap": False, "total": _round(total), "count": len(out),
        "threshold": threshold, "items": out,
        "vital_few": out[:vital], "vital_few_count": vital,
        "vital_few_cum_pct": out[vital - 1]["cum_pct"],
        "warnings": [],
    }
