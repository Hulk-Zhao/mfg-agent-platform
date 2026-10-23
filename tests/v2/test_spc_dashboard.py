"""SPC 过程能力看板增量增强 · 验收测试（契约 ``docs/spc-dashboard-upgrade-design.md`` §10）。

口径铁律（改动前必读）：

- 板级三类筛选 —— 统计口径 ``window`` / 产品分类 ``product_category`` / 工段 ``stage``
  —— 必须与能力表、柏拉图、L1 卡片、L2 看板、弹窗**同源**（同一批测量记录、
  同一个窗口、同一套维度折叠）；凡不同源即为缺陷。
- 时间窗口三档（唯一事实源 ``dashboard.WINDOWS``）：
  ``window`` = 2026-10-08~10-14（最近 7 天）、``day`` = 2026-10-15（当日）、
  ``month`` = 2026-10-01~10-15（当月）。
- 弹窗端点为 ``GET /api/v2/dashboard/spc/capability?characteristic=&window=&stage=&product_category=``，
  与 L1 卡片复用 ``_spc_caps_from_rows`` / ``compute_capability`` → **逐字段可对账**。
- 403 fail-closed：无令牌 401 / 岗位无权 403，**绝不**降级成 200 空数据或演示明细。
- 不编造：缺 USL/LSL → ``data_gap``；筛选无命中 → 显式空态（不是 0 值）。
- 最小变更：不传新参数时，**能力表**取数与改动前逐字段一致（柏拉图改为跟随 window
  是设计 §2.2.1 明确许可的**唯一**默认行为变化，由 A6 单独守护）。

本文件的取舍：

- **不依赖工程师的种子 SQL 已导入演示库**：全部 SPC 数据在 ``tmp_path`` 里自建
  临时 SQLite（``spc_measurement`` 含 ``process_stage`` / ``product_category`` 两列、
  ``quality_defect`` 含同名列），经 ``DataSourceRegistry`` + ``SQLiteDataSource``
  只读接入（沿用 ``tests/v2/test_bugfix_round2.py`` 的最小 registry 构造范式）。
  「种子覆盖度」另用**静态文本断言**守护（读 ``data/seed_spc*.sql``，不导入）。
- **静态源码断言一律用子串 / 正则**，不依赖行号（``screens.js`` / ``app.js``
  处于并发编辑窗口，行号会漂移）；断言取「宽松到能容忍合理实现差异」的下限
  （例如只要求排除链里出现 ``data-spc-modal``，不要求写法与行序完全一致）。
- 演示库（``data/mfg_agent.db``）仅用于**回归**用例（制造 / 品质 / 订单看板与
  既有下钻行为），沿用 ``tests/v2/test_std_team_detail.py`` 的
  ``create_default_orchestrator().runtime.data_registry`` 夹具写法。
"""
from __future__ import annotations

import json
import re
import shutil
import sqlite3
import subprocess
from pathlib import Path

import pytest

from mfg_agents.v2.core.spc import compute_capability, normalize_measurements
from mfg_agents.v2.services.dashboard import (
    DRILL_DEFS,
    MFG_PRODUCT_CATEGORIES,
    MFG_STAGES,
    WINDOWS,
    build_drill,
    build_spc_dashboard,
)

# ============================================================================
# 0. 常量 · 模型 · 工具
# ============================================================================

_ROOT = Path(__file__).resolve().parents[2]
_UI = _ROOT / "src" / "mfg_agents" / "v2" / "demo" / "ui"
_SCREENS = _UI / "screens.js"
_APP = _UI / "app.js"
_DEMO_DATA = _UI / "demo-data.js"

#: 契约 §1.1 的三档时间窗口（唯一事实源在 ``dashboard.WINDOWS``，此处是契约快照）
_CONTRACT_WINDOWS = {
    "window": ("2026-10-08", "2026-10-14"),
    "day": ("2026-10-15", "2026-10-15"),
    "month": ("2026-10-01", "2026-10-15"),
}

#: 四个工段 / 四个产品分类（后端常量与前端 chips 同源）
_STAGES = ("SMT", "PCM", "PACK", "整机")
_PCATS = ("消费电子", "智能电源", "储能电源", "车载")
_TIERS = ("合格", "偏低", "不合格")

#: 产品分类 → 该组合唯一日期（保证三档窗口各自有数据；10-05 只落在当月档）
_PCAT_DATE = {
    "消费电子": "2026-10-10",
    "智能电源": "2026-10-12",
    "储能电源": "2026-10-15",
    "车载": "2026-10-05",
}

#: 判定档位 → (LSL, USL)。同一批测量值（离散度固定）下，半宽越大 Cpk 越高。
_TIER_SPEC = {
    "合格": (0.40, 0.60),
    "偏低": (0.44, 0.56),
    "不合格": (0.47, 0.53),
}

#: 测量值模板：每个子组 5 个值，组内极差恒为 0.04，均值恒为 0.50
_VALUE_STEP = 0.01
_SUBGROUP_SIZE = 5
_GROUP_RANGE = 0.04
_CENTER = 0.50

#: 子组容量 5 → d2 = 2.326（``core/spc.D2_CONSTANTS``）
_D2_N5 = 2.326
#: 双西格玛解析值（用于 A4 最小变更守卫的硬锚点）
_SIGMA_WITHIN = _GROUP_RANGE / _D2_N5
_SIGMA_OVERALL = 0.0149071  # 10 个值 [0.48..0.52]×2 的样本标准差(ddof=1)

#: 「无规格限」特性名（缺 USL/LSL → data_gap）
_GAP_CHAR = "SMT-消费电子-无规格"
#: 「老行」特性名（process_stage / product_category 均为 NULL）
_LEGACY_CHAR = "旧行-无维度"
#: 「越限」特性名（3 条超差值，用于 A5 分子分母同源核对）
_OVER_CHAR = "SMT-消费电子-超差"


def _tier_of(stage: str, pcat: str) -> str:
    """(工段, 产品分类) → 判定档位（旋转分配，保证每档窗口都能看到三档判定）。"""
    si = _STAGES.index(stage)
    pi = _PCATS.index(pcat)
    return _TIERS[(si + pi) % 3]


def _char_name(stage: str, pcat: str) -> str:
    return f"{stage}-{pcat}"


def _subgroups(center: float = _CENTER, step: float = _VALUE_STEP) -> list[list[float]]:
    """两个子组，每组 5 个值（组内极差 0.04、均值 0.50）。"""
    out = []
    for _ in range(2):
        out.append([round(center + (i - 2) * step, 4)
                    for i in range(_SUBGROUP_SIZE)])
    return out


def _over_subgroups() -> list[list[float]]:
    """超差特性：3 条超限值（0.45 < LSL、0.55 > USL、0.53 > USL）。

    规格限 lsl=0.48 / usl=0.52 → 越限判定为「严格大于 usl 或严格小于 lsl」，
    故 0.52 恰在界上**不算**越限 → 实际越限 3 条（不是 2 条）。
    """
    return [[0.45, 0.49, 0.50, 0.51, 0.55], [0.49, 0.50, 0.51, 0.52, 0.53]]


def _build_model() -> dict:
    """构造测试数据模型（同时是「期望值」的推导源）。

    每个特性记录：所属工段 / 产品分类 / 唯一日期 / 判定档位 / 规格限 / 测量值。
    期望的能力指数不写死，而是用同一套确定性内核 ``compute_capability`` 对模型
    复算 → 断言的是「实现与口径同源」，而不是「实现等于某个魔数」。
    """
    model: dict[str, dict] = {}
    for stage in _STAGES:
        for pcat in _PCATS:
            tier = _tier_of(stage, pcat)
            lsl, usl = _TIER_SPEC[tier]
            name = _char_name(stage, pcat)
            model[name] = {
                "name": name, "stage": stage, "pcat": pcat,
                "date": _PCAT_DATE[pcat], "tier": tier,
                "lsl": lsl, "usl": usl,
                "subgroups": _subgroups(), "n_total": 10, "n_subgroups": 2,
                "over_num": 0, "over_den": 10, "data_gap": False,
            }
    # 超差特性（3 条越限值）—— 让「越限率 ≠ 0」可核对
    model[_OVER_CHAR] = {
        "name": _OVER_CHAR, "stage": "SMT", "pcat": "消费电子",
        "date": "2026-10-10", "tier": "不合格",
        "lsl": 0.48, "usl": 0.52, "subgroups": _over_subgroups(),
        "n_total": 10, "n_subgroups": 2,
        "over_num": 3, "over_den": 10, "data_gap": False,
    }
    # 老行：两个维度列均为 NULL（不参与「等于」匹配，但不过滤时可见）
    model[_LEGACY_CHAR] = {
        "name": _LEGACY_CHAR, "stage": None, "pcat": None,
        "date": "2026-10-12", "tier": "合格",
        "lsl": 0.40, "usl": 0.60, "subgroups": _subgroups(),
        "n_total": 10, "n_subgroups": 2,
        "over_num": 0, "over_den": 10, "data_gap": False,
    }
    # 缺规格限 → data_gap（能力指数留空，不编造）
    model[_GAP_CHAR] = {
        "name": _GAP_CHAR, "stage": "SMT", "pcat": "消费电子",
        "date": "2026-10-10", "tier": "不可判定",
        "lsl": None, "usl": None, "subgroups": _subgroups(),
        "n_total": 10, "n_subgroups": 2,
        "over_num": 0, "over_den": 10, "data_gap": True,
    }
    return model


def _in_window(date: str, window_key: str) -> bool:
    start, end = _CONTRACT_WINDOWS[window_key]
    return start <= date <= end


def _chars_in_scope(model: dict, window_key: str, stage: str | None = None,
                    pcat: str | None = None) -> list[str]:
    """按「窗口 + 维度」筛出模型里应当出现的特性名（= 期望集合）。"""
    out = []
    for name, m in model.items():
        if not _in_window(m["date"], window_key):
            continue
        if stage is not None and m["stage"] != stage:
            continue
        if pcat is not None and m["pcat"] != pcat:
            continue
        out.append(name)
    return sorted(out)


def _expected_capability(m: dict) -> dict:
    """模型 → 期望能力指数（缺规格限时按 data_gap 形状）。"""
    if m["data_gap"]:
        return {"characteristic": m["name"], "data_gap": True,
                "verdict": "不可判定", "cp": None, "cpk": None,
                "pp": None, "ppk": None,
                "n_total": m["n_total"], "n_subgroups": m["n_subgroups"],
                "spec": {"usl": None, "lsl": None}}
    return compute_capability(characteristic=m["name"], subgroups=m["subgroups"],
                              usl=m["usl"], lsl=m["lsl"])


def _build_spc(registry, **kwargs):
    """调用 ``build_spc_dashboard``；新参数未实现时给出可读失败信息。"""
    try:
        return build_spc_dashboard(registry, **kwargs)
    except TypeError as exc:  # pragma: no cover - 实现未到位时的可读报错
        if "unexpected keyword argument" in str(exc):
            pytest.fail(
                "build_spc_dashboard 尚未支持新参数 "
                f"{sorted(kwargs)}（契约 §2.2.1 / §4.2）：{exc}")
        raise


def _cap_by_name(payload: dict) -> dict:
    return {c["characteristic"]: c for c in payload.get("capabilities") or []}


def _kpi_by_name(payload: dict) -> dict:
    return {k["characteristic"]: k for k in payload.get("kpis") or []}


# ---------------------------------------------------------------------------
# 前端静态源码工具（子串 / 正则，不用行号）
# ---------------------------------------------------------------------------

def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _fn_block(src: str, header: str) -> str:
    """函数体提取：按下一个同级 ``function`` / ``async function`` 声明切边界。"""
    start = src.index(header)
    tail = src[start + len(header):]
    ends = [tail.find("\n  " + kw) for kw in ("async function ", "function ")]
    ends = [x for x in ends if x != -1]
    return tail[:min(ends)] if ends else tail


def _enclosing_block(src: str, index: int) -> str:
    """取包含 ``index`` 的最近一个顶层函数块（按 ``\\n  function`` 声明定位）。"""
    heads = [m.start() for m in re.finditer(r"\n  (?:async )?function ", src)]
    heads = [h for h in heads if h < index]
    if not heads:
        return src[max(0, index - 2000): index + 2000]
    start = heads[-1]
    return _fn_block(src, src[start + 1:src.index("(", start)])


def _braced_block(src: str, anchor: str) -> str:
    """从 ``anchor`` 处的 ``{`` 起按花括号配平取整块（换档处理器等内联回调用）。"""
    start = src.index(anchor)
    open_at = src.index("{", start)
    depth = 0
    for i in range(open_at, len(src)):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[open_at:i + 1]
    raise AssertionError(f"anchor {anchor} 之后的花括号未配平")


def _qs_calls(block: str) -> list[tuple[str, str, int]]:
    """解析块内所有 ``querySelectorAll(...)``：返回 (拼接后的选择器, 原始实参, 起始位置)。

    选择器常写成多行字符串拼接（``"[a]" + ":not([b])"``）→ 拼接后再比对，
    避免对写法 / 换行敏感。
    """
    out: list[tuple[str, str, int]] = []
    for m in re.finditer(r"querySelectorAll\(", block):
        start = m.end()
        depth = 1
        i = start
        while i < len(block) and depth:
            ch = block[i]
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
            i += 1
        arg = block[start:i - 1]
        parts = re.findall(r'"([^"]*)"|\'([^\']*)\'', arg)
        selector = "".join(a or b for a, b in parts)
        out.append((selector, arg, m.start()))
    return out


def _drill_filter_scope_table() -> dict:
    """从 ``screens.js`` 解析 ``DRILL_FILTER_SCOPE``（"screen:kpi" → 生效字段集）。"""
    src = _read(_SCREENS)
    block = src[src.index("const DRILL_FILTER_SCOPE = {"):]
    block = block[:block.index("\n  };")]
    table: dict[str, set] = {}
    for m in re.finditer(r'"([a-z_]+:[a-z_0-9]+)":\s*\{([^}]*)\}', block):
        table[m.group(1)] = set(re.findall(r"(\w+):\s*true", m.group(2)))
    return table


# ============================================================================
# 1. 夹具：临时库 / 演示库 / HTTP 客户端
# ============================================================================

@pytest.fixture(scope="module")
def model() -> dict:
    return _build_model()


@pytest.fixture(scope="module")
def spc_db(tmp_path_factory, model):
    """自建临时 SQLite（演示种子尚未导入时的可重复测试底座）。

    两张表：
    - ``spc_measurement``：**含** ``process_stage`` / ``product_category`` 两列
      （契约 §2.1.1），并保留若干两列均为 NULL 的「老行」；
    - ``quality_defect``：含 ``process_stage`` / ``defect_category`` /
      ``product_category``（柏拉图侧直接列折叠，契约 §2.2.2）。
    """
    folder = tmp_path_factory.mktemp("spc_dash")
    db_path = folder / "spc_dash.db"
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(
            """
            CREATE TABLE spc_measurement (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                characteristic VARCHAR(80) NOT NULL,
                subgroup_no INTEGER NOT NULL,
                seq INTEGER NOT NULL,
                value REAL NOT NULL,
                usl REAL,
                lsl REAL,
                measured_at VARCHAR(30),
                source VARCHAR(40),
                ingested_by VARCHAR(40),
                ingested_at VARCHAR(30) NOT NULL,
                process_stage TEXT,
                product_category TEXT
            );
            CREATE TABLE quality_defect (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                batch_no TEXT,
                product_model TEXT,
                line_code TEXT,
                defect_type TEXT,
                defect_qty INTEGER,
                total_qty INTEGER,
                inspect_time TEXT,
                process_stage TEXT,
                defect_category TEXT,
                product_category TEXT
            );
            """)
        rows = []
        rid = 0
        for m in model.values():
            for gno, group in enumerate(m["subgroups"], start=1):
                for seq, value in enumerate(group, start=1):
                    rid += 1
                    rows.append((rid, m["name"], gno, seq, value,
                                 m["usl"], m["lsl"],
                                 f"{m['date']} 08:{20 + 10 * gno:02d}:00",
                                 "qa-fixture", "qa", "2026-10-20T00:00:00",
                                 m["stage"], m["pcat"]))
        conn.executemany(
            "INSERT INTO spc_measurement (id, characteristic, subgroup_no, seq, "
            "value, usl, lsl, measured_at, source, ingested_by, ingested_at, "
            "process_stage, product_category) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
        # 柏拉图：三种窗口各放一类缺陷 + 一类「所有窗口之外」的缺陷
        # （窗口外那一类用于 A6「柏拉图跟随 window」的反证）
        conn.executemany(
            "INSERT INTO quality_defect (id, batch_no, product_model, line_code, "
            "defect_type, defect_qty, total_qty, inspect_time, process_stage, "
            "defect_category, product_category) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            [
                (1, "B-1", "A型号", "SMT-1", "窗口内缺陷", 30, 100,
                 "2026-10-15 09:00:00", "SMT", "外观", "消费电子"),
                (2, "B-2", "B型号", "DIP-1", "默认窗口缺陷", 20, 100,
                 "2026-10-10 09:00:00", "PCM", "外观", "智能电源"),
                (3, "B-3", "C型号", "PACK-1", "月初缺陷", 10, 100,
                 "2026-10-05 09:00:00", "PACK", "外观", "储能电源"),
                (4, "B-4", "D型号", "SMT-2", "窗口外缺陷", 70, 100,
                 "2026-10-20 09:00:00", "SMT", "外观", "消费电子"),
            ])
        conn.commit()
    finally:
        conn.close()
    return db_path


@pytest.fixture(scope="module")
def spc_registry(spc_db):
    """临时库 → 只读 registry（``DataSourceRegistry`` + ``SQLiteDataSource``）。"""
    from mfg_agents.v2.contracts.data_source import (
        DataSourceRegistry,
        SQLiteDataSource,
    )
    registry = DataSourceRegistry()
    registry.register(SQLiteDataSource(str(spc_db), name="dev"))
    return registry


@pytest.fixture(scope="module")
def big_registry(tmp_path_factory):
    """行数上限探测库：单个特性 250 条测量值（> registry 默认 200 行上限）。"""
    from mfg_agents.v2.contracts.data_source import (
        DataSourceRegistry,
        SQLiteDataSource,
    )
    folder = tmp_path_factory.mktemp("spc_big")
    db_path = folder / "spc_big.db"
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "CREATE TABLE spc_measurement ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, characteristic VARCHAR(80) "
            "NOT NULL, subgroup_no INTEGER NOT NULL, seq INTEGER NOT NULL, "
            "value REAL NOT NULL, usl REAL, lsl REAL, measured_at VARCHAR(30), "
            "source VARCHAR(40), ingested_by VARCHAR(40), "
            "ingested_at VARCHAR(30) NOT NULL, process_stage TEXT, "
            "product_category TEXT)")
        conn.execute(
            "CREATE TABLE quality_defect ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, batch_no TEXT, "
            "product_model TEXT, line_code TEXT, defect_type TEXT, "
            "defect_qty INTEGER, total_qty INTEGER, inspect_time TEXT, "
            "process_stage TEXT, defect_category TEXT, product_category TEXT)")
        rows = []
        rid = 0
        for gno in range(1, 51):
            for seq, value in enumerate(_subgroups()[0], start=1):
                rid += 1
                # 规格限须满足 usl > lsl（``compute_capability`` 对 usl <= lsl 直接
                # 返回 data_gap，且 gap 形态**不带 n_total** → 断言会 KeyError，
                # 根本走不到「是否被 row_cap 截断」这一步）。
                # 这里取 usl=0.60 / lsl=0.40：``_subgroups()`` 的值域 0.46~0.54
                # 全部落在规格内 → 能力可正常估计，本守卫才真正测到行数上限。
                rows.append((rid, "行数上限探测特性", gno, seq, value, 0.60, 0.40,
                             "2026-10-10 08:00:00", "qa-fixture", "qa",
                             "2026-10-20T00:00:00", "SMT", "消费电子"))
        conn.executemany(
            "INSERT INTO spc_measurement (id, characteristic, subgroup_no, seq, "
            "value, usl, lsl, measured_at, source, ingested_by, ingested_at, "
            "process_stage, product_category) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
        conn.commit()
    finally:
        conn.close()
    registry = DataSourceRegistry()
    registry.register(SQLiteDataSource(str(db_path), name="dev"))
    return registry


@pytest.fixture(scope="module")
def demo_registry():
    """演示库 registry（仅用于回归用例；沿用既有测试的构造方式）。"""
    from mfg_agents.v2.core.orchestrator import create_default_orchestrator
    return create_default_orchestrator().runtime.data_registry


class _FakeRuntime:
    def __init__(self, registry):
        self.data_registry = registry


class _FakeOrchestrator:
    def __init__(self, registry):
        self.runtime = _FakeRuntime(registry)


@pytest.fixture(scope="module")
def client(spc_registry, monkeypatch_module):
    """HTTP 客户端：把编排器指向临时库（演示种子是否导入不影响本文件）。"""
    from fastapi.testclient import TestClient

    from mfg_agents.v2.demo import api

    monkeypatch_module.setattr(api, "get_orchestrator",
                               lambda: _FakeOrchestrator(spc_registry))
    return TestClient(api.app)


@pytest.fixture(scope="module")
def monkeypatch_module():
    """模块级 monkeypatch（``client`` 与 ``demo_client`` 共享同一作用域）。"""
    from _pytest.monkeypatch import MonkeyPatch
    mp = MonkeyPatch()
    try:
        yield mp
    finally:
        mp.undo()


@pytest.fixture(scope="module")
def demo_client():
    """演示库 HTTP 客户端（回归用例用，不打桩）。"""
    from fastapi.testclient import TestClient

    from mfg_agents.v2.demo.api import app

    return TestClient(app)


def _token(client, *roles: str) -> str:
    resp = client.post("/api/v2/auth/login", json={"roles": list(roles)})
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["token"]


def _get(client, path: str, roles: tuple[str, ...] = ("gm",)):
    headers = {"Authorization": f"Bearer {_token(client, *roles)}"}
    return client.get(path, headers=headers)


# ============================================================================
# 2. §10.1 筛选类断言 A1~A9
# ============================================================================

def test_a0_window_constants_match_contract():
    """A0 前置：后端 ``WINDOWS`` 三档窗口与契约一致（口径漂移的最上游守卫）。"""
    assert set(WINDOWS) >= set(_CONTRACT_WINDOWS)
    for key, (start, end) in _CONTRACT_WINDOWS.items():
        assert WINDOWS[key]["start"] == start, key
        assert WINDOWS[key]["end"] == end, key
    assert MFG_STAGES == _STAGES
    assert MFG_PRODUCT_CATEGORIES == _PCATS


def test_a1_window_selects_measurement_dates(spc_registry, model):
    """A1：三档窗口各自取到对应日期的测量记录（day 全部落在 2026-10-15）。"""
    for key, (start, end) in _CONTRACT_WINDOWS.items():
        payload = _build_spc(spc_registry, window=key)
        assert payload["window"]["start"] == start, key
        assert payload["window"]["end"] == end, key
        assert payload["window"]["key"] == key
        got = set(_cap_by_name(payload))
        assert got == set(_chars_in_scope(model, key)), (
            f"{key}: 窗口取到的特性集合与模型不符")


def test_a1_window_day_records_are_all_demo_today(spc_registry, model):
    """A1 强化：day 档的**原始记录**日期全为 2026-10-15。"""
    from mfg_agents.v2.services import dashboard as dash

    detail = getattr(dash, "build_spc_capability_detail", None)
    assert detail is not None, "缺少 build_spc_capability_detail（契约 §2.2.5）"
    # day 档 = 2026-10-15 当日；模型里落在该日的只有「储能电源」这一族
    # （``_PCAT_DATE`` 把「消费电子」固定在 10-10）→ 取储能电源特性才有当日记录。
    char = _char_name("SMT", "储能电源")
    out = detail(spc_registry, window="day", characteristic=char)
    records = out.get("records") or []
    assert records, "day 档应有原始记录"
    assert {str(r.get("measured_at"))[:10] for r in records} == {"2026-10-15"}
    assert all(r.get("characteristic") == char for r in records)


def test_a2_illegal_window_stage_category_rejected(client):
    """A2：非法 window / stage / product_category → 400（不得静默当「全部」）。"""
    for query in ("window=%E4%B8%8D%E5%AD%98%E5%9C%A8",
                  "stage=NOT_A_STAGE",
                  "product_category=NOT_A_PCAT"):
        resp = _get(client, f"/api/v2/dashboard/spc?{query}")
        assert resp.status_code == 400, f"{query} → {resp.status_code} {resp.text}"
    # 白名单边界：合法值不得 400
    # 注意 URL 编码须与「消费电子」逐字节对应（%E6%B6%88%E8%B4%B9%E7%94%B5%E5%AD%90），
    # 抄错一个字节会解码成别的词 → 后端按「未知产品分类」正确返回 400。
    for query in ("window=window", "window=day", "window=month",
                  "stage=SMT", "product_category=%E6%B6%88%E8%B4%B9%E7%94%B5%E5%AD%90"):
        resp = _get(client, f"/api/v2/dashboard/spc?{query}")
        assert resp.status_code == 200, f"{query} → {resp.status_code} {resp.text}"


def test_a3_stage_filter_is_exact_match(spc_registry, model):
    """A3：``stage=SMT`` 只返回 SMT 的特性（不含其它工段 / 老行）。"""
    payload = _build_spc(spc_registry, window="month", stage="SMT")
    got = set(_cap_by_name(payload))
    assert got == set(_chars_in_scope(model, "month", stage="SMT"))
    assert _LEGACY_CHAR not in got, "老行（维度为 NULL）不得被「等于」匹配命中"


def test_a3_product_category_filter_is_exact_match(spc_registry, model):
    """A3：``product_category=车载`` 只返回车载的特性。"""
    payload = _build_spc(spc_registry, window="month", product_category="车载")
    got = set(_cap_by_name(payload))
    assert got == set(_chars_in_scope(model, "month", pcat="车载"))
    assert _LEGACY_CHAR not in got


def test_a3_filtered_records_carry_the_filtered_dimension(spc_registry):
    """A3 强化：筛选后弹窗明细里每条记录的工段 / 产品分类恒等于筛选值。"""
    from mfg_agents.v2.services import dashboard as dash

    detail = getattr(dash, "build_spc_capability_detail", None)
    assert detail is not None, "缺少 build_spc_capability_detail（契约 §2.2.5）"
    char = _char_name("SMT", "消费电子")
    out = detail(spc_registry, window="month", characteristic=char,
                 stage="SMT", product_category="消费电子")
    records = out.get("records") or []
    assert records
    assert {r.get("process_stage") for r in records} == {"SMT"}
    assert {r.get("product_category") for r in records} == {"消费电子"}


def test_a4_minimal_change_guard_capability_table_unchanged(spc_registry, model):
    """A4 最小变更守卫：不传新参数时，**能力表**与改动前逐字段一致。

    可执行方案（不是口号）：

    1. 「改动前」= 固定 10-08~10-14 窗口 + 不做任何工段 / 产品分类折叠 →
       期望集合 = 模型中落在默认窗口且两个维度均非 NULL 的特性；
    2. 「改动后」= ``build_spc_dashboard(registry)``（不传任何新参数）；
    3. 逐特性比对 cp / cpk / pp / ppk / spec / n_total / n_subgroups / verdict
       以及卡片的 over_num / over_den / over_rate / status；
    4. 集合本身也必须相等（防止「默认就偷偷加了筛选」把特性筛掉）。

    柏拉图改为跟随 window 是契约 §2.2.1 明确许可的唯一默认行为变化，
    由 A6 单独守护，不在本断言内。
    """
    payload = build_spc_dashboard(spc_registry)  # 不传任何新参数
    assert payload["window"]["start"] == _CONTRACT_WINDOWS["window"][0]
    assert payload["window"]["end"] == _CONTRACT_WINDOWS["window"][1]

    expected_names = set(_chars_in_scope(model, "window"))
    got_caps = _cap_by_name(payload)
    got_kpis = _kpi_by_name(payload)
    assert set(got_caps) == expected_names, (
        "默认口径的特性集合被改动（等价于默认就应用了维度筛选）")
    assert set(got_kpis) == expected_names

    for name in sorted(expected_names):
        m = model[name]
        exp = _expected_capability(m)
        cap = got_caps[name]
        for field in ("cp", "cpk", "pp", "ppk", "n_total", "n_subgroups",
                      "verdict", "data_gap"):
            assert cap.get(field) == exp.get(field), f"{name}.{field}"
        assert cap.get("spec") == exp.get("spec"), name
        kpi = got_kpis[name]
        assert kpi["over_num"] == m["over_num"], name
        assert kpi["over_den"] == m["over_den"], name
        assert kpi["over_rate"] == pytest.approx(
            100.0 * m["over_num"] / m["over_den"], abs=0.1), name
        assert kpi["value"] == kpi["cpk"], name
        assert kpi["target"] == 1.33 and kpi["higher_is_better"] is True, name


def test_a4_double_sigma_anchor_values_unchanged(spc_registry):
    """A4 硬锚点：既有口径的 Cpk / Ppk 精确值未被改动（组内 σ vs 整体 σ）。"""
    # 取 month 档（2026-10-01~10-15）：三档锚点特性覆盖全部 4 个产品分类的日期，
    # 其中「SMT-储能电源」落在 2026-10-15，**不在**默认 window 档（10-08~10-14）内。
    # 各特性的 Cpk/Ppk 只由它自己的 subgroup 决定，与窗口内是否另有特性无关。
    payload = build_spc_dashboard(spc_registry, window="month")
    caps = _cap_by_name(payload)
    good = caps.get(_char_name("SMT", "消费电子"))
    marginal = caps.get(_char_name("SMT", "智能电源"))
    bad = caps.get(_char_name("SMT", "储能电源"))
    assert good and marginal and bad, "既有 3 档特性必须仍在"
    # 组内 σ：R̄/d2 = 0.04/2.326 → Cpk = 0.10/(3σ) = 1.9383
    assert good["cpk"] == pytest.approx(0.10 / (3 * _SIGMA_WITHIN), abs=0.01)
    # 整体 σ（ddof=1）→ Ppk = 0.10/(3σ) = 2.2361；与 Cpk 严格不同（双西格玛）
    assert good["ppk"] == pytest.approx(0.10 / (3 * _SIGMA_OVERALL), abs=0.01)
    assert good["cpk"] != good["ppk"]
    assert marginal["cpk"] == pytest.approx(0.06 / (3 * _SIGMA_WITHIN), abs=0.01)
    assert bad["cpk"] == pytest.approx(0.03 / (3 * _SIGMA_WITHIN), abs=0.01)
    assert {good["verdict"], marginal["verdict"], bad["verdict"]} == set(_TIERS)


def test_a5_over_rate_and_capability_share_same_records(spc_registry, model):
    """A5 口径同源：越限率的分子分母与能力指数来自**同一批**测量记录。"""
    for window_key in ("window", "day", "month"):
        for stage in (None, "SMT", "整机"):
            payload = _build_spc(spc_registry, window=window_key, stage=stage)
            caps = _cap_by_name(payload)
            kpis = _kpi_by_name(payload)
            for name, cap in caps.items():
                kpi = kpis[name]
                assert kpi["over_den"] == cap["n_total"], (
                    f"{window_key}/{stage}/{name}: 越限率分母与能力指数样本数不同源")
                assert kpi["over_num"] <= kpi["over_den"]
                assert kpi["over_rate"] == pytest.approx(
                    100.0 * kpi["over_num"] / kpi["over_den"], abs=0.1)
            # 分子必须等于「该范围内越限条数」→ 用模型独立核对
            for name, m in model.items():
                if name not in caps:
                    continue
                assert kpis[name]["over_num"] == m["over_num"], name


def test_a6_pareto_follows_window(spc_registry):
    """A6 柏拉图跟随 window（需求 A 修口径不一致的核心价值）。

    临时库故意放了一类「所有窗口之外」的缺陷（2026-10-20）：
    改动前的「全量无 WHERE」口径会把它算进去 —— 修好之后必须消失。
    """
    day = _build_spc(spc_registry, window="day")
    names = [p["name"] for p in day["pareto"]]
    assert "窗口内缺陷" in names, day["pareto"]
    assert "窗口外缺陷" not in names, (
        "柏拉图仍按全量取数（未跟随 window）—— 这是本次要修的口径不一致")
    assert "默认窗口缺陷" not in names, "day 档不应含 10-10 的缺陷"

    win = _build_spc(spc_registry, window="window")
    win_names = [p["name"] for p in win["pareto"]]
    assert "默认窗口缺陷" in win_names
    assert "窗口内缺陷" not in win_names, "最近 7 天档不含 10-15 的缺陷"

    month = _build_spc(spc_registry, window="month")
    month_names = [p["name"] for p in month["pareto"]]
    assert {"窗口内缺陷", "默认窗口缺陷", "月初缺陷"} <= set(month_names)
    assert "窗口外缺陷" not in month_names
    # 累计占比单调不减（帕累托不变量）
    cums = [p["cum_share"] for p in month["pareto"]]
    assert cums == sorted(cums)
    assert 0.0 < cums[-1] <= 100.5


def _all_spc_seed_text() -> str:
    """提取**全部**种子文件里 ``spc_measurement`` 的 INSERT 段并拼成一段文本。

    为什么跨文件：既有 3 特性的原始行在 ``data/seed_v3.sql``，而 10-15 补数在
    ``data/seed_spc_dimensions.sql`` → 「同一特性规格限漂移」「同一 subgroup_no
    跨日期复用」这两类缺陷**只在跨文件比对时才暴露**（QA 实测：只扫
    ``seed_spc*.sql`` 会漏检，因为漂移的两端分居两个文件）。

    只取 ``spc_measurement`` 段（不取 ``quality_defect`` 段）—— 否则会把别的表的
    元组也喂给测量行的正则，产生假阳性。
    """
    chunks: list[str] = []
    for path in sorted((_ROOT / "data").glob("seed*.sql")):
        text = path.read_text(encoding="utf-8")
        for m in re.finditer(
                r"INSERT\s+(?:OR\s+IGNORE\s+)?INTO\s+spc_measurement\b.*?;",
                text, re.I | re.S):
            chunks.append(m.group(0))
    assert chunks, "未在任何种子文件中找到 spc_measurement 的 INSERT 段"
    return "\n".join(chunks)


def test_a7_seed_coverage_static_assertion():
    """A7 种子覆盖度守卫（静态文本断言，**不导入数据库**）。

    读 ``data/seed_spc*.sql``（文件名由实现方决定 → 取并集）断言：
    覆盖 2026-10-15（当日档非空）、四个工段值、四个产品分类值，
    且不得引入新的 ``quality_defect`` 行（缺陷类型 10 词表是红线）。
    """
    candidates = sorted((_ROOT / "data").glob("seed_spc*.sql"))
    assert candidates, "缺少 SPC 种子文件 data/seed_spc*.sql"
    text = "\n".join(p.read_text(encoding="utf-8") for p in candidates)

    assert re.search(r"INSERT\s+(?:OR\s+IGNORE\s+)?INTO\s+spc_measurement",
                     text, re.I), "种子未写入 spc_measurement"
    assert "process_stage" in text and "product_category" in text, \
        "种子未补两列维度"

    dates = set(re.findall(r"2026-10-(\d{2})", text))
    assert "15" in dates, "种子未覆盖 2026-10-15（当日口径将恒为 0 行）"
    assert dates & {f"{d:02d}" for d in range(8, 15)}, \
        "种子未覆盖默认窗口 10-08~10-14"

    for stage in _STAGES:
        assert stage in text, f"种子缺少工段 {stage}"
    for pcat in _PCATS:
        assert pcat in text, f"种子缺少产品分类 {pcat}"

    assert not re.search(r"INSERT\s+(?:OR\s+IGNORE\s+)?INTO\s+quality_defect",
                          text, re.I), "种子不得新增 quality_defect 行（缺陷词表红线）"


def test_a7_seed_keeps_spec_limits_stable_per_characteristic():
    """A7 扩展守卫：同一特性的**规格限必须唯一**（跨日期不得漂移）。

    为什么必须守卫（真实缺陷，工程师 2026-10-20 报出并实测确认）：
    ``_spc_caps_from_rows`` 取该特性**第一行**的 usl/lsl 作为规格限
    → 同一特性在不同日期写不同规格限，会让**换口径即换判定**：
    锡膏厚度 10-13 写 ``150/90``、10-15 写 ``160/120`` → 最近 7 天 Cpk=1.146「偏低」，
    换到当日口径变 0.877「不合格」；回流焊 10-14 ``255/235``、10-15 ``250/235`` 同理。
    这不是「口径跟随窗口」的正常表现，而是**数据自相矛盾** → 违反「不编造数据」。

    断言走**静态 SQL 文本**：按 (特性, 日期) 分组，若同一特性出现多个规格限组合即失败。

    注意比对范围必须**跨全部种子文件**（``seed_v3.sql`` 存既有 3 特性、
    ``seed_spc_dimensions.sql`` 存 10-15 补数）—— 漂移正是**跨文件**发生的，
    只看 ``seed_spc*.sql`` 会漏检（QA 实测踩过这个坑）。
    """
    text = _all_spc_seed_text()

    # 抓 INSERT 的值元组。实际种子形如：
    #   (4001, '贴装偏移量(μm)', 1, 1, 35.6164, 120.0, 0.0, '2026-10-10 08:20:00', ...)
    # 即 (可选 id, 特性, subgroup_no, seq, value, usl, lsl, 时间, ...)。
    # 正则写成**两种形态都兼容**：id 可有可无（``(?:\d+\s*,\s*)?``）。
    row_re = re.compile(
        r"\(\s*(?:\d+\s*,\s*)?'([^']+)'\s*,\s*(\d+)\s*,\s*(\d+)\s*,"
        r"\s*[-0-9.]+\s*,\s*([0-9.]+)\s*,\s*([0-9.]+)\s*,\s*'([^']+)'", re.M)
    specs: dict[str, set[tuple[str, str]]] = {}
    for name, _gno, _seq, usl, lsl, _ts in row_re.findall(text):
        specs.setdefault(name, set()).add((usl, lsl))

    assert specs, "未从种子 SQL 解析到任何测量行（正则可能需随实现调整）"
    drifted = {n: sorted(v) for n, v in specs.items() if len(v) > 1}
    assert not drifted, (
        "同一特性出现多个规格限组合 → 换口径会翻转判定（_spc_caps_from_rows 取首行规格限）: "
        f"{drifted}")


def test_a7_seed_does_not_reuse_subgroup_no_across_dates():
    """A7 扩展守卫：同一特性在不同日期**不得复用同一 subgroup_no**。

    为什么必须守卫（真实缺陷，工程师 2026-10-20 报出并实测确认）：
    既有 3 特性在 10-15 补数时把 ``subgroup_no`` 写成 1-5（与 10-13/10-14 重复）
    → 当月口径（10-01~10-15）把两天数据**并成同一子组** → 组容量变成 10
    → ``compute_capability`` 按 n=10 取 d2=3.078（真实子组是 n=5，d2=2.326）
    → ``sigma_within`` 假性变小 → Cpk 假性偏低；
    且因各组容量**整齐都是 10**，「剔除非众数子组」告警**不触发** → **静默失真**。

    断言走**静态 SQL 文本**：同一 (特性, subgroup_no) 不得出现在两个不同日期。
    比对范围同样**跨全部种子文件**（补数在 ``seed_spc_dimensions.sql``、原数据在 ``seed_v3.sql``）。
    """
    text = _all_spc_seed_text()

    row_re = re.compile(
        r"\(\s*(?:\d+\s*,\s*)?'([^']+)'\s*,\s*(\d+)\s*,\s*(\d+)\s*,"
        r"\s*[-0-9.]+\s*,\s*([0-9.]+)\s*,\s*([0-9.]+)\s*,\s*'([^']+)'", re.M)
    seen: dict[tuple[str, str], set[str]] = {}
    for name, gno, _seq, _usl, _lsl, ts in row_re.findall(text):
        day = ts[:10]
        seen.setdefault((name, gno), set()).add(day)

    reused = {k: sorted(v) for k, v in seen.items() if len(v) > 1}
    assert not reused, (
        "同一 (特性, subgroup_no) 出现在多个日期 → 当月口径会把多天并成同一子组，"
        f"组容量与 d2 常数表失配（Cpk 静默失真）: {reused}")


def test_a8_three_verdict_tiers_survive_filter(spc_registry, model):
    """A8：三档判定（Cpk 合格 / 偏低 / 不合格）在筛选后**仍各自可见**。

    判定「没被筛没」的可执行口径：对每个窗口 + 每个工段筛选，
    实际返回的判定档位集合必须等于模型按同一窗口 / 维度算出的期望集合；
    并且**不筛选**时三档判定必须齐备（否则看板一筛就只剩一档）。
    """
    for window_key in ("window", "day", "month"):
        payload = _build_spc(spc_registry, window=window_key)
        verdicts = {c["verdict"] for c in payload["capabilities"]}
        assert set(_TIERS) <= verdicts, (
            f"{window_key}: 三档判定未齐备 → {verdicts}")
        for stage in _STAGES:
            scoped = _build_spc(spc_registry, window=window_key, stage=stage)
            got = {c["verdict"] for c in scoped["capabilities"]}
            expected = {model[n]["tier"]
                        for n in _chars_in_scope(model, window_key, stage=stage)}
            assert got == expected, f"{window_key}/{stage}: {got} ≠ {expected}"


def test_a9_legacy_rows_null_dimension_excluded_only_when_filtered(spc_registry, model):
    """A9：老行（两个维度列均 NULL）不参与「等于」匹配，但不过滤时仍然可见。"""
    unfiltered = _build_spc(spc_registry, window="month")
    assert _LEGACY_CHAR in _cap_by_name(unfiltered), "不过滤时老行必须可见"

    for kwargs in ({"stage": "SMT"}, {"product_category": "消费电子"},
                   {"stage": "SMT", "product_category": "消费电子"}):
        scoped = _build_spc(spc_registry, window="month", **kwargs)
        assert _LEGACY_CHAR not in _cap_by_name(scoped), (
            f"老行不应被 {kwargs} 命中")


def test_a10_payload_echoes_window_and_filter(spc_registry):
    """A1/A3 补充：载荷必须回显窗口与筛选（前端口径标签的数据来源）。"""
    payload = _build_spc(spc_registry, window="day", stage="SMT",
                         product_category="消费电子")
    assert payload["window"]["key"] == "day"
    filt = payload.get("filter")
    assert isinstance(filt, dict), "载荷缺少 filter 回显"
    assert filt.get("stage") == "SMT"
    assert filt.get("product_category") == "消费电子"
    default = _build_spc(spc_registry, window="window")
    dfilter = default.get("filter") or {}
    assert not dfilter.get("stage") and not dfilter.get("product_category")


# ============================================================================
# 3. §10.2 下钻类断言 B1~B4
# ============================================================================

def test_b1_drill_l2_rows_limited_to_filtered_stage(spc_registry, model):
    """B1：板级选 SMT 后进 2 级看板，2 级行的实体集合**只含** SMT 的特性。"""
    out = build_drill(spc_registry, "spc", "spc_capability", None,
                      window="month", stage="SMT")
    assert out["level"] == 2
    names = {r["name"] for r in out["rows"]}
    assert names == set(_chars_in_scope(model, "month", stage="SMT"))
    for row in out["rows"]:
        assert row["entity"] == {"characteristic": row["name"]}
        assert row["denominator"] == model[row["name"]]["over_den"]
        assert row["numerator"] == model[row["name"]]["over_num"]


def test_b1_drill_l2_rows_limited_to_filtered_category(spc_registry, model):
    """B1 强化：按产品分类筛选后 2 级行只含该分类的特性。"""
    out = build_drill(spc_registry, "spc", "spc_capability", None,
                      window="month", product_category="车载")
    names = {r["name"] for r in out["rows"]}
    assert names == set(_chars_in_scope(model, "month", pcat="车载"))


def test_b2_drill_filter_scope_registered_for_spc():
    """B2：``DRILL_FILTER_SCOPE`` 已登记 spc 两条（否则板级筛选不下沉到下钻）。"""
    table = _drill_filter_scope_table()
    for key in ("spc:spc_capability", "spc:spc_pareto"):
        assert key in table, f"前端未登记 {key}（下钻不吃板级筛选）"
        assert {"window", "stage", "product_category"} <= table[key], key
    # 后端声明是唯一事实源：两指标都要声明 spc_scope（列精确匹配形态）
    for kpi in ("spc_capability", "spc_pareto"):
        scope = DRILL_DEFS[kpi].get("spc_scope")
        assert scope, f"{kpi} 未声明 spc_scope"
        assert scope.get("stage_col") == "process_stage", kpi
        assert scope.get("pcat_col") == "product_category", kpi


def test_b3_drill_response_echoes_filter(spc_registry):
    """B3：下钻响应含 ``filter`` 回显（stage / product_category）。"""
    out = build_drill(spc_registry, "spc", "spc_capability", None,
                      window="month", stage="SMT", product_category="消费电子")
    filt = out.get("filter")
    assert isinstance(filt, dict), "下钻响应缺少 filter 回显"
    assert filt.get("stage") == "SMT"
    assert "消费电子" in list(filt.values()), filt


def test_b3_drill_without_filter_has_no_filter_echo(spc_registry):
    """B3 边界：不带筛选下钻时不应回显一个「假筛选」。"""
    out = build_drill(spc_registry, "spc", "spc_capability", None, window="month")
    filt = out.get("filter")
    if filt is not None:
        assert not filt.get("stage") and not filt.get("product_category"), filt


def test_b4_drill_reset_only_on_screen_switch():
    """B4（静态代理）：**换档**不得把下钻层级退回看板首页。

    自动化边界：换档时「下钻层级不丢」是纯前端状态机行为，静态断言只能守住
    「换档处理器里复位必须被下钻态守卫」这一条；完整交互需浏览器人工复核
    （见测试方案）。

    取块范围收窄到**三个换档处理器内部**（订单 / 制造 / SPC 的
    ``[data-window]`` 监听器）。此前扫全文件的写法会误伤两类**合法**复位：
    换档处理器的 ``else st.boardDrill = null;``（此时本就没有下钻，复位是
    幂等清理）与 ``setDrill()`` 里降到 1 级的复位 —— 二者都不是「换档误退回」。

    每个换档处理器块内断言两件事：
    1. 存在保留层级的分支 ``if (drill)``（换档时若处于下钻态，**不**退回看板首页）；
    2. 块内的 ``st.boardDrill = null`` 只出现在 ``else`` 分支里（即仅在无下钻
       时清理），不得出现无条件的 ``st.boardDrill = null;``。
    """
    src = _read(_SCREENS)
    handlers = {
        "orders": 'querySelectorAll("#orders-windows [data-window]")',
        "mfg": 'querySelectorAll("#mfg-windows [data-window]")',
        "spc": 'querySelectorAll("#spc-windows [data-window]")',
    }
    for name, anchor in handlers.items():
        assert src.count(anchor) == 1, f"{name} 换档处理器锚点不唯一：{anchor}"
        block = _braced_block(src, anchor)
        # 1) 换档时若处于下钻态 → 保留层级（不退回看板首页）
        assert re.search(r"if\s*\(\s*drill\s*\)", block), (
            f"{name} 换档处理器缺少保留下钻层级的分支 if (drill)"
            "（换档会误退回看板首页）")
        # 2) 复位只允许出现在 else 分支（无下钻时清理），不得有无条件复位
        for m in re.finditer(r"st\.boardDrill\s*=\s*null", block):
            before = block[:m.start()]
            assert re.search(r"\belse\b[^{};]*$", before), (
                f"{name} 换档处理器出现无条件的 st.boardDrill = null"
                "（换档会误退回看板首页）—— 复位必须只发生在 else 分支")


# ============================================================================
# 4. §10.3 弹窗类断言 C1~C7
# ============================================================================

def test_c1_mfg_bind_excludes_spc_modal_on_three_chains():
    """C1：``mfgBind`` 的 L2 / L4-L5 / L3 三条排除链都排除 ``[data-spc-modal]``。

    排除链是「防双触发」的硬约束：SPC 的 2 级行只带 ``data-spc-modal``，
    但 L1 侧的行仍带 ``data-drill-entity`` → 少一条排除就会出现「一次点击
    既展开又弹窗」。
    """
    src = _read(_SCREENS)
    block = _fn_block(src, "function mfgBind")
    calls = _qs_calls(block)
    anchors = {
        "L2": "[data-kpi]",
        "L4-L5": "[data-drill-step]",
        "L3": "[data-drill-entity]",
    }
    for label, anchor in anchors.items():
        matched = [c for c in calls if c[0].startswith(anchor)]
        assert matched, f"mfgBind 内找不到 {anchor} 绑定（{label}）"
        assert any("data-spc-modal" in c[1] for c in matched), (
            f"{label} 排除链未排除 [data-spc-modal]（会双触发）")


def test_c1_spc_modal_binding_branch_registered():
    """C1：新增 ``[data-spc-modal]`` 绑定分支，且置于三条排除链**之前**。"""
    src = _read(_SCREENS)
    block = _fn_block(src, "function mfgBind")
    calls = _qs_calls(block)
    modal = [c for c in calls if c[0].strip() == "[data-spc-modal]"]
    assert modal, "mfgBind 未新增 [data-spc-modal] 绑定分支"
    assert "openSpcCapabilityModal" in block, "SPC 弹窗分支未调用 openSpcCapabilityModal"
    for anchor in ("[data-kpi]", "[data-drill-step]", "[data-drill-entity]"):
        other = [c for c in calls if c[0].startswith(anchor)]
        assert other, anchor
        assert modal[0][2] < other[0][2], (
            f"[data-spc-modal] 分支必须早于 {anchor} 绑定（否则同一次点击双触发）")


def test_c1_l2_rows_use_spc_modal_hook():
    """C1：SPC 的 2 级看板行改用 ``data-spc-modal`` + ``data-spc-entity``。"""
    src = _read(_SCREENS)
    block = _fn_block(src, "function mfgDrillL2Body")
    assert "data-spc-modal" in block, "L2 行未挂 data-spc-modal 钩子"
    assert "data-spc-entity" in block, "L2 行未挂 data-spc-entity 实体钩子"
    assert re.search(r"data-spc-modal\s*=\s*[\"']capability[\"']", block), \
        "data-spc-modal 取值应为 \"capability\""


def _spc_l1_rendered_html(tmp_path, payload) -> str:
    """用 **node 探针**把 SPC 的 L1 正文**真渲染**一次，返回 HTML。

    为什么必须真渲染（而不是正则查源码）：``mfgKpiCard`` 里的 ``data-kpi`` 是
    **动态拼**的（``${UI.esc(kpi.key)}``）→ 源码里根本没有字面量 ``"spc_capability"``。
    正则查源码要么写不出来，要么写死了某种实现（改个写法就假红）。
    真渲染则**与实现无关**：只要 HTML 里没有可捕获的元素，守卫就红。
    """
    node = shutil.which("node")
    if not node:
        pytest.skip("环境无 node，跳过 SPC L1 渲染探针")
    probe = Path(__file__).resolve().parent / "_spc_l1_render_probe.js"
    assert probe.exists(), f"缺少渲染探针：{probe}"
    pj = tmp_path / "payload.json"
    pj.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    out = tmp_path / "spc_l1.html"
    proc = subprocess.run([node, str(probe), str(pj), str(out)],
                          capture_output=True, text=True)
    assert proc.returncode == 0, f"SPC L1 渲染探针执行失败:\n{proc.stderr}"
    assert out.exists(), "渲染探针未产出 HTML"
    return out.read_text(encoding="utf-8")


def _l2_chain_selector(src: str) -> str:
    """从 ``mfgBind`` 里取出 L2 链那条 ``querySelectorAll`` 的选择器。"""
    bind = _fn_block(src, "function mfgBind")
    for sel, _args, _pos in _qs_calls(bind):
        if sel.strip().startswith("[data-kpi]") and ":not(" in sel:
            return sel
    return ""


def test_x3_spc_l1_has_l2_reachable_entry_element(tmp_path, demo_registry):
    """X3（SPC 2 级看板**入口可达性**）：L1 渲染结果里必须存在一个**能被 L2 链捕获**的元素。

    为什么必须补这条（覆盖盲区，QA 承认）：
    既有守卫 ``test_c1_l2_rows_use_spc_modal_hook`` 只检查 **``mfgDrillL2Body`` 代码块里
    挂了钩子**，**不检查 L2 入口是否可达** —— 于是「L2 行的钩子写对了，但整个 L2
    根本点不进去」这类缺陷**完全测不出来**。

    真实缺陷形态（2026-10-20 QA 实测复现）：SPC 的 L1 逐特性 KPI 卡**全部**由
    ``mfgKpiCard(UI, k, "spc", k.entity)`` 渲染，而 ``k.entity`` 非空 → 卡上带
    ``data-drill-entity`` → 而 ``mfgBind`` 的 L2 链是
    ``[data-kpi]:not([data-drill-entity]):not([data-drill-pareto]):not([data-order-group])
    :not([data-mfg-modal]):not([data-spc-modal])`` → **带 entity 的元素被 L2 链排除**
    → L1 上**没有任何**元素能触发 L2 → **SPC 2 级看板入口不可达**。

    判据（断**语义**、不锁实现）：把 L1 **真渲染**出来（node 探针），要求其中
    至少有一个元素同时满足：
      ① 有 ``data-kpi`` 属性（值不限 —— L2 链只要求有该属性）；
      ② **不带** L2 链任何一个 ``:not([...])`` 排除项所指的属性
         （即：套进 L2 链的选择器后**仍然匹配**）。
    """
    from mfg_agents.v2.services.dashboard import build_spc_dashboard

    src = _read(_SCREENS)
    selector = _l2_chain_selector(src)
    assert selector, "mfgBind 里找不到 L2 链的 [data-kpi] 选择器"
    excluded = re.findall(r":not\(\s*\[([a-zA-Z-]+)\]", selector)
    assert excluded, f"L2 链排除项解析失败：{selector}"

    payload = build_spc_dashboard(demo_registry, "window")
    html = _spc_l1_rendered_html(tmp_path, payload)

    # 取渲染结果里所有带 data-kpi 的元素（标签级）
    elements = re.findall(r"<[a-zA-Z][^<>]*?data-kpi[^<>]*?>", html)
    assert elements, "SPC 的 L1 渲染结果里没有任何带 data-kpi 的元素"

    def blocked(el: str) -> list[str]:
        return [a for a in excluded if re.search(rf"{re.escape(a)}\s*=", el)]

    reachable = [el for el in elements if not blocked(el)]
    assert reachable, (
        "SPC 的 L1 渲染结果里**没有**能被 L2 链捕获的元素 → "
        "**SPC 2 级看板入口不可达**（L2 行钩子写得再对也点不进去）。\n"
        f"  L2 链选择器：{selector}\n"
        f"  排除项：{excluded}\n"
        f"  渲染出的 data-kpi 元素共 {len(elements)} 个，全部被排除；样例：\n"
        + "\n".join("    " + e[:200] for e in elements[:3]))



def test_c2_modal_matches_l1_and_l2(spc_registry, model):
    """C2：弹窗 cpk / 判定 / 样本数 与 L1 卡片、L2 行**三处同源一致**。"""
    from mfg_agents.v2.services import dashboard as dash

    detail = getattr(dash, "build_spc_capability_detail", None)
    assert detail is not None, "缺少 build_spc_capability_detail（契约 §2.2.5）"
    char = _char_name("SMT", "消费电子")
    out = detail(spc_registry, window="month", characteristic=char)
    payload = _build_spc(spc_registry, window="month")
    kpi = _kpi_by_name(payload)[char]
    # L2（按 characteristic 分解）必须**不传 entity**：传了 entity 就进 L3 分支，
    # 返回体里没有 ``rows`` 键（L3 是单实体视图，非分组行）。
    l2 = build_drill(spc_registry, "spc", "spc_capability", window="month")
    assert l2["level"] == 2, l2["level"]
    row = next(r for r in l2["rows"] if r["name"] == char)

    assert out["cpk"] == kpi["cpk"], "弹窗 cpk 与 L1 卡片不一致"
    assert out["verdict"] == kpi["verdict"], "弹窗判定与 L1 卡片不一致"
    assert out["n_total"] == kpi["n_total"], "弹窗样本数与 L1 卡片不一致"
    assert out.get("n_subgroups") == kpi.get("n_subgroups")
    assert out["over_num"] == kpi["over_num"], "弹窗越限数与 L1 卡片不一致"
    assert out["over_den"] == kpi["over_den"]
    assert out["over_rate"] == pytest.approx(kpi["over_rate"], abs=0.1)
    # L2 行的分子 / 分母键名即 ``numerator`` / ``denominator``（``_l2_items`` 构造器）
    assert out["over_num"] == row["numerator"], "弹窗越限数与 L2 行不一致"
    assert out["over_den"] == row["denominator"], "弹窗样本数与 L2 行不一致"


def test_c2_l2_row_transmits_board_filters_to_modal():
    """C2 静态：L2 行必须把**板级筛选维度**透传给弹窗钩子。

    为什么必须守卫（真实缺陷，非假想）：
    ``mfgBind`` 的 ``[data-spc-modal]`` 绑定分支读的是 ``el.dataset.spcStage`` /
    ``el.dataset.spcPcat``，而 ``mfgDrillL2Body`` 的 SPC 分支若不渲染对应的
    ``data-spc-stage`` / ``data-spc-pcat`` 属性 → 维度恒为空 → 弹窗请求不带
    维度 → 弹窗显示的是该特性在**全厂全分类**的样本，与 L1 卡片的**筛选后**
    样本数对不上（实测 100 vs 25）→ 违反「口径唯一事实源」硬约束。

    断言方式保持宽松（子串匹配，不依赖行号、不依赖具体取值方式）：
    只要 L2 行渲染了这两个 data 属性即视为通过 —— 无论实现是从响应顶层
    ``filter`` 取、还是从行字段取，渲染结果都必须落到这两个钩子上。
    """
    src = _read(_SCREENS)
    block = _fn_block(src, "function mfgDrillL2Body")
    assert "data-spc-stage" in block, (
        "L2 行未把板级工段维度透传给弹窗钩子（弹窗会退化成全厂口径，与 L1 卡片对不上）")
    assert "data-spc-pcat" in block, (
        "L2 行未把板级产品分类维度透传给弹窗钩子（弹窗会退化成全厂口径，与 L1 卡片对不上）")
    # 绑定分支必须读这两个钩子（否则渲染了也读不到）
    modal_idx = src.find('box.querySelectorAll("[data-spc-modal]")')
    assert modal_idx != -1, "未找到 [data-spc-modal] 绑定分支"
    bind = _enclosing_block(src, modal_idx)
    assert "spcStage" in bind and "spcPcat" in bind, (
        "[data-spc-modal] 绑定分支未读取板级维度钩子（渲染了也不会生效）")


def test_c2_modal_with_filters_matches_l1_filtered(spc_registry):
    """C2 数值层：**带板级筛选**时，弹窗样本数必须与「筛选后的 L1 卡片」相等。

    这是 C2 的核心口径断言：不带筛选时三者天然同源（都是全厂口径），
    **只有在带筛选时**才暴露「弹窗丢维度」这一类缺陷 —— 弹窗按全厂算、
    L1 卡片按筛选后算 → 两者样本数不等（实测 100 vs 25）。
    """
    from mfg_agents.v2.services import dashboard as dash

    detail = getattr(dash, "build_spc_capability_detail", None)
    assert detail is not None, "缺少 build_spc_capability_detail（契约 §2.2.5）"
    char = _char_name("SMT", "消费电子")
    # L1 卡片：带工段 + 产品分类筛选
    payload = _build_spc(spc_registry, window="month",
                         stage="SMT", product_category="消费电子")
    kpi = _kpi_by_name(payload)[char]
    # 弹窗：**同样带**筛选 → 必须与上面这张卡片逐字段一致
    out = detail(spc_registry, window="month", characteristic=char,
                 stage="SMT", product_category="消费电子")
    assert out["n_total"] == kpi["n_total"], (
        "弹窗（带筛选）样本数与筛选后的 L1 卡片不一致 → 板级维度未贯通")
    assert out.get("n_subgroups") == kpi.get("n_subgroups")
    assert out["cpk"] == kpi["cpk"], "弹窗（带筛选）cpk 与筛选后的 L1 卡片不一致"
    assert out["over_num"] == kpi["over_num"]
    assert out["over_den"] == kpi["over_den"]
    # L2 行：同样带筛选 → 分母必须与卡片一致
    l2 = build_drill(spc_registry, "spc", "spc_capability", window="month",
                     stage="SMT", product_category="消费电子")
    row = next(r for r in l2["rows"] if r["name"] == char)
    assert row["denominator"] == kpi["over_den"], (
        "L2 行（带筛选）分母与筛选后的 L1 卡片不一致")
    # 维度必须被回显到 L2 响应顶层（前端据此渲染 data-spc-stage / data-spc-pcat）
    filt = l2.get("filter") or {}
    assert filt.get("stage") == "SMT", (
        "L2 响应未回显板级工段筛选（前端无法把维度透传给弹窗）")
    assert filt.get("product_category") == "消费电子", (
        "L2 响应未回显板级产品分类筛选（前端无法把维度透传给弹窗）")


def test_c2_modal_endpoint_matches_l1(client):
    """C2 接口层：弹窗端点返回的 cpk 与 L1 接口同源相等。"""
    char = _char_name("SMT", "消费电子")
    detail = _get(client, "/api/v2/dashboard/spc/capability"
                         f"?characteristic={char}&window=month")
    assert detail.status_code == 200, detail.text
    l1 = _get(client, "/api/v2/dashboard/spc?window=month")
    assert l1.status_code == 200, l1.text
    kpi = next(k for k in l1.json()["data"]["kpis"]
               if k["characteristic"] == char)
    data = detail.json()["data"]
    assert data["cpk"] == kpi["cpk"]
    assert data["verdict"] == kpi["verdict"]
    assert data["over_num"] == kpi["over_num"]
    assert data["over_den"] == kpi["over_den"]


def test_c3_modal_endpoint_forbidden_for_unauthorized_role(client):
    """C3：弹窗端点无权岗位 → 403，且**不降级**成 200 空数据 / 演示明细。"""
    resp = _get(client, "/api/v2/dashboard/spc/capability"
                      f"?characteristic={_char_name('SMT', '消费电子')}",
                roles=("warehouse",))
    assert resp.status_code == 403, resp.text
    assert "无权" in resp.text
    assert '"code": 0' not in resp.text.replace(" ", "")


def test_c3_modal_front_end_shows_denied_not_demo():
    """C3 静态：SPC 弹窗有「无权查看」分支，且不回落演示明细。"""
    src = _read(_SCREENS)
    idx = src.find("openSpcCapabilityModal")
    assert idx > 0, "缺少 openSpcCapabilityModal（SPC 弹窗组件未实现）"
    block = _enclosing_block(src, idx)
    assert re.search(r"permission_denied|403", block), \
        "SPC 弹窗缺少 403 / permission_denied 分支"
    assert re.search(r"无权", block), "SPC 弹窗缺少「无权查看」提示"


def test_c4_modal_no_data_returns_found_false_not_404(client):
    """C4：弹窗无数据 → 200 + 空骨架（found:false），**绝不 404**。"""
    resp = _get(client, "/api/v2/dashboard/spc/capability"
                      "?characteristic=不存在的特性&window=month")
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    found = data.get("found")
    if found is not None:
        assert found is False, data
    assert not data.get("records"), "无数据时不得返回原始记录"
    assert data.get("cpk") is None, "无数据时不得编造能力指数"


def test_c4_modal_no_data_service_level(spc_registry):
    """C4 服务层：特性不存在时返回空骨架，不抛异常、不编造。"""
    from mfg_agents.v2.services import dashboard as dash

    detail = getattr(dash, "build_spc_capability_detail", None)
    assert detail is not None, "缺少 build_spc_capability_detail（契约 §2.2.5）"
    out = detail(spc_registry, window="month", characteristic="不存在的特性")
    found = out.get("found")
    if found is not None:
        assert found is False, out
    assert not out.get("records")
    assert out.get("cpk") is None


def test_c5_offline_degradation_is_explicit():
    """C5：离线降级必须**显式标注**（不是静默空）。"""
    demo_src = _read(_DEMO_DATA)
    assert "SPC_DASHBOARD" in demo_src, "demo-data.js 缺 SPC_DASHBOARD 快照"
    assert "不支持板级筛选" in demo_src, \
        "离线快照未标注「不支持板级筛选」（会假装是筛选后的汇总）"
    assert re.search(r"离线", demo_src), "离线降级未显式标注"


def test_c6_modal_has_enter_level3_entry():
    """C6：弹窗内「进入 3 级看板」入口走既有 ``setDrill``。"""
    src = _read(_SCREENS)
    # 弹窗由两个函数协作：``openSpcCapabilityModal``（事件绑定 + setDrill 调用）
    # 与 ``spcCapabilityModalBody``（正文渲染，含「进入 3 级看板」按钮文案）。
    # 故「入口文案」在**两块并集**里查；``setDrill`` / ``level: 3`` 属事件绑定，
    # 仍只在 ``openSpcCapabilityModal`` 块里查。
    open_block = _fn_block(src, "async function openSpcCapabilityModal")
    body_block = _fn_block(src, "function spcCapabilityModalBody")
    both = open_block + "\n" + body_block

    assert "setDrill" in open_block, "弹窗未复用 setDrill（面包屑 / 返回 / 口径切换会断）"
    assert re.search(r"level\s*:\s*3", open_block), "弹窗未提供 3 级看板入口"
    assert "进入 3 级看板" in both or "进入3级看板" in both, \
        "弹窗缺少「进入 3 级看板」入口文案"


def test_c7_modal_endpoint_requires_characteristic(client):
    """C7：弹窗端点缺 ``characteristic`` → 400（不得静默取全量）。"""
    resp = _get(client, "/api/v2/dashboard/spc/capability?window=month")
    assert resp.status_code == 400, resp.text
    assert "characteristic" in resp.text or "特性" in resp.text


# ============================================================================
# 5. §10.4 安全与数据纪律断言 D1~D7
# ============================================================================

def test_d1_new_endpoint_requires_token(client):
    """D1：新端点无令牌 → 401（匿名不得读业务数据）。"""
    resp = client.get("/api/v2/dashboard/spc/capability"
                      "?characteristic=焊点高度(mm)")
    assert resp.status_code == 401, resp.text


def test_d1_new_endpoint_forbidden_for_unauthorized_role(client):
    """D1：新端点无权岗位 → 403，**绝不返回 200 空数据**。"""
    resp = _get(client, "/api/v2/dashboard/spc/capability"
                      "?characteristic=焊点高度(mm)", roles=("warehouse",))
    assert resp.status_code == 403, resp.text
    assert '"code":0' not in resp.text.replace(" ", "")


def test_d1_l1_endpoint_fail_closed(client):
    """D1：L1 端点同样 fail-closed（401 / 403，绝不 200 空数据）。"""
    assert client.get("/api/v2/dashboard/spc").status_code == 401
    resp = _get(client, "/api/v2/dashboard/spc", roles=("warehouse",))
    assert resp.status_code == 403, resp.text


def test_d2_client_methods_use_scoped_getter():
    """D2：``spcDashboard`` / ``spcCapability`` 客户端方法**仍走** ``_getScoped``。"""
    src = _read(_APP)
    for method in ("spcDashboard", "spcCapability"):
        idx = src.find(f"{method}(")
        assert idx > 0, f"app.js 缺少 {method} 客户端方法"
        tail = src[idx: idx + 600]
        assert "_getScoped(" in tail, f"{method} 未走 _getScoped（失去 401 补签重试）"


def test_d3_ingest_without_dimensions_writes_nulls(tmp_path):
    """D3：``spc.ingest`` 不传维度时写入行新列为 NULL 且**不报错**。"""
    from mfg_agents.v2.services.spc_store import (
        append_measurements,
        ensure_measurements_table,
    )

    db_path = tmp_path / "ingest.db"
    ensure_measurements_table(str(db_path))
    conn = sqlite3.connect(db_path)
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(spc_measurement)")}
    finally:
        conn.close()
    assert {"process_stage", "product_category"} <= cols, \
        "spc_measurement 未补两列（契约 §2.1.2 / §2.1.3）"

    res = normalize_measurements(characteristic="X", values=[1.0, 1.1, 1.2, 1.3, 1.4])
    assert res["n_accepted"] == 5 and res["n_rejected"] == 0
    for row in res["accepted"]:
        assert row.get("process_stage") is None
        assert row.get("product_category") is None
    written = append_measurements(str(db_path), res["accepted"],
                                  ingested_by="qa", ingested_at="2026-10-20T00:00:00")
    assert written == 5
    conn = sqlite3.connect(db_path)
    try:
        rows = list(conn.execute(
            "SELECT process_stage, product_category FROM spc_measurement"))
    finally:
        conn.close()
    assert len(rows) == 5
    assert all(stage is None and pcat is None for stage, pcat in rows)


def test_d3_normalize_measurements_keeps_optional_dims():
    """D3 纯函数侧：不传维度时 accepted 行带两个键且值为 None（向后兼容）。"""
    res = normalize_measurements(characteristic="X", values=[1.0, 1.1, 1.2, 1.3, 1.4])
    for row in res["accepted"]:
        assert "process_stage" in row and "product_category" in row
        assert row["process_stage"] is None and row["product_category"] is None


def test_d4_illegal_dimension_rejected_not_fabricated():
    """D4：非法维度值进 ``rejected``，**绝不编造**。"""
    try:
        res = normalize_measurements(characteristic="X",
                                     values=[1.0, 1.1, 1.2, 1.3, 1.4],
                                     process_stage="不存在的工段")
    except TypeError as exc:  # pragma: no cover - 实现未到位
        pytest.fail(f"normalize_measurements 尚未支持维度入参（契约 §2.1.3）：{exc}")
    assert res["n_accepted"] == 0, "非法维度不得产出 accepted 行"
    assert res["n_rejected"] >= 1, "非法维度必须进 rejected"
    for row in res["accepted"]:
        assert row.get("process_stage") != "不存在的工段"
    assert res.get("warnings"), "拒绝必须有说明"

    try:
        res2 = normalize_measurements(characteristic="X",
                                      values=[1.0, 1.1, 1.2, 1.3, 1.4],
                                      product_category="不存在的分类")
    except TypeError as exc:  # pragma: no cover - 实现未到位
        pytest.fail(f"normalize_measurements 尚未支持维度入参（契约 §2.1.3）：{exc}")
    assert res2["n_accepted"] == 0


def test_d4_valid_dimension_kept_on_accepted_rows():
    """D4 正向：合法维度值原样落在 accepted 行上。"""
    try:
        res = normalize_measurements(characteristic="X",
                                     values=[1.0, 1.1, 1.2, 1.3, 1.4],
                                     process_stage="SMT",
                                     product_category="消费电子")
    except TypeError as exc:  # pragma: no cover - 实现未到位
        pytest.fail(f"normalize_measurements 尚未支持维度入参（契约 §2.1.3）：{exc}")
    assert res["n_accepted"] == 5
    for row in res["accepted"]:
        assert row.get("process_stage") == "SMT"
        assert row.get("product_category") == "消费电子"


def test_d5_missing_spec_marks_data_gap(spc_registry):
    """D5：缺 USL/LSL 的特性标 ``data_gap``，能力指数**留空不编造**。"""
    payload = _build_spc(spc_registry, window="month")
    caps = _cap_by_name(payload)
    gap = caps.get(_GAP_CHAR)
    assert gap is not None, "缺规格限的特性必须仍出现在能力表里"
    assert gap["data_gap"] is True
    assert gap["verdict"] == "不可判定"
    for field in ("cp", "cpk", "pp", "ppk"):
        assert gap[field] is None, f"缺规格限时 {field} 不得编造"
    assert gap["spec"] == {"usl": None, "lsl": None}
    assert gap.get("warnings"), "data_gap 必须说明原因"
    kpi = _kpi_by_name(payload)[_GAP_CHAR]
    assert kpi["data_gap"] is True
    assert kpi["cpk"] is None


def test_d6_empty_filter_result_is_explicit_not_zero(spc_registry):
    """D6：筛选无命中 → 显式空态（``kpis`` 为空 + ``data_gap``），**不是 0 值**。"""
    payload = _build_spc(spc_registry, window="day", stage="PACK",
                         product_category="车载")
    assert payload["kpis"] == [], "无命中时不得编造 0 值卡片"
    assert payload["capabilities"] == []
    assert payload["data_gap"] is True, "无命中必须标数据缺口"
    for kpi in payload["kpis"]:
        assert kpi["cpk"] is not None


def test_d7_data_scope_spc_block_unchanged_and_inherited(client):
    """D7：``data_scope.yaml`` 的 ``spc:`` 块**不需改**（新端点自动继承白名单）。"""
    import yaml

    scope_path = _ROOT / "src" / "mfg_agents" / "v2" / "data" / "data_scope.yaml"
    conf = yaml.safe_load(scope_path.read_text(encoding="utf-8"))
    # 顶层是 ``dashboards``，spc 块在其下（``conf["spc"]`` → KeyError）
    spc = conf["dashboards"]["spc"]
    assert sorted(spc["allow_roles"]) == sorted(
        ["gm", "manufacturing", "quality_director", "pqe", "sqe", "qa",
         "product_engineering", "test_engineer", "ie_engineer"]), \
        "spc 白名单被改动（新端点应自动继承既有 9 角色）"
    assert not spc.get("mask"), "本次新增字段非敏感，不应新增 mask 规则"

    # 自动继承的证据：白名单内岗位可读新端点，白名单外岗位被拒
    allowed = _get(client, "/api/v2/dashboard/spc/capability"
                          f"?characteristic={_char_name('SMT', '消费电子')}")
    assert allowed.status_code == 200, allowed.text
    denied = _get(client, "/api/v2/dashboard/spc/capability"
                         f"?characteristic={_char_name('SMT', '消费电子')}",
                  roles=("warehouse",))
    assert denied.status_code == 403, denied.text


# ============================================================================
# 6. §10.5 回归类断言 E1~E6
# ============================================================================

def test_e1_new_endpoint_registered_in_api():
    """E1 局部：SPC 弹窗端点已注册（契约 §2.2.5 的路径，**不是** /spc/detail）。"""
    src = _read(_ROOT / "src" / "mfg_agents" / "v2" / "demo" / "api.py")
    assert '"/api/v2/dashboard/spc/capability"' in src, \
        "弹窗端点路径应为 /api/v2/dashboard/spc/capability"


def test_e2_node_syntax_check_on_changed_js():
    """E2：改动过的 js 必须 ``node --check`` 通过（并发编辑的语法红线）。"""
    node = shutil.which("node")
    if not node:
        pytest.skip("环境无 node，跳过 node --check")
    for path in (_SCREENS, _APP, _DEMO_DATA):
        proc = subprocess.run([node, "--check", str(path)],
                              capture_output=True, text=True)
        assert proc.returncode == 0, f"{path.name} 语法错误:\n{proc.stderr}"


def test_e3_manufacturing_quality_orders_boards_unchanged(demo_registry):
    """E3：制造 / 品质 / 订单看板**零变化**（L1 形状与口径切换仍工作）。"""
    from mfg_agents.v2.services.dashboard import (
        build_manufacturing_dashboard,
        build_order_dashboard,
        build_quality_dashboard,
    )

    mfg = build_manufacturing_dashboard(demo_registry, "window")
    assert mfg["screen"] == "manufacturing"
    assert mfg["kpis"] and mfg["window"]["key"] == "window"
    for win in ("day", "month"):
        assert build_manufacturing_dashboard(demo_registry, win)["window"]["key"] == win

    qual = build_quality_dashboard(demo_registry, "window")
    assert qual["screen"] == "quality" and qual["kpis"]

    orders = build_order_dashboard(demo_registry, "window")
    assert orders["screen"] == "orders" and orders["kpis"]


def test_e3_other_board_toolbars_and_bindings_intact():
    """E3 前端侧：制造 / 订单看板的工具栏与绑定分支未被本次改动破坏。"""
    src = _read(_SCREENS)
    for anchor in ("#mfg-windows", "#orders-windows"):
        assert anchor in src, f"{anchor} 工具栏被破坏"
    block = _fn_block(src, "function mfgBind")
    for anchor in ("[data-mfg-modal]", "[data-order-group]", "[data-kpi]",
                   "[data-drill-step]", "[data-drill-entity]"):
        assert anchor in block, f"mfgBind 缺少 {anchor} 绑定（既有行为被破坏）"
    # 制造 / 订单的排除链必须仍在（本次只**追加** :not([data-spc-modal])）
    assert block.count(":not([data-mfg-modal])") >= 3, \
        "制造侧排除链被破坏（应只追加不减）"


def test_e4_quality_q_scope_behavior_unchanged(demo_registry):
    """E4：``q_scope`` 声明的品质两指标（不良率 / 直通率）行为逐字段不变。"""
    for kpi in ("defect_rate", "fpy"):
        qs = DRILL_DEFS[kpi].get("q_scope")
        assert qs, f"{kpi} 的 q_scope 声明被改动"
        assert qs.get("stage_col") == "process_stage", kpi
        assert qs.get("category_col") == "defect_category", kpi

    out = build_drill(demo_registry, "quality", "defect_rate", None,
                      window="window", stage="SMT", category="外观")
    filt = out.get("filter")
    assert isinstance(filt, dict)
    assert set(filt) == {"stage", "category"}, \
        "品质 q_scope 的 filter 回显形状被改动（应仍是 stage + category）"
    assert filt["stage"] == "SMT" and filt["category"] == "外观"
    # 筛选确实生效（口径仍按 process_stage / defect_category 精确匹配）
    unfiltered = build_drill(demo_registry, "quality", "defect_rate", None,
                             window="window")
    assert out["rows"] != unfiltered["rows"] or not out["rows"]


def test_e4_spc_scope_does_not_reuse_q_scope():
    """E4 隔离：SPC 走的是 ``spc_scope``（产品分类），不得混用 ``q_scope``。"""
    for kpi in ("spc_capability", "spc_pareto"):
        defn = DRILL_DEFS[kpi]
        assert defn.get("q_scope") is None, f"{kpi} 不应声明 q_scope"
        assert defn.get("spc_scope"), f"{kpi} 应声明 spc_scope"


def test_e5_drill_filter_scope_table_matches_backend():
    """E5：前端筛选生效表与后端声明逐项一致（比对范围含 spc 屏）。"""
    table = _drill_filter_scope_table()
    known = {f'{d["screen"]}:{k}' for k, d in DRILL_DEFS.items()}
    expected: dict[str, set] = {}
    for kpi, defn in DRILL_DEFS.items():
        if defn["screen"] not in ("quality", "manufacturing", "spc"):
            continue
        sc, qs, sp = (defn.get("scope") or {}, defn.get("q_scope") or {},
                      defn.get("spc_scope") or {})
        keys = set()
        if defn.get("date_expr") and not defn.get("fixed_range"):
            keys.add("window")
        if sc.get("stage_col") or qs.get("stage_col") or sp.get("stage_col"):
            keys.add("stage")
        if sc.get("pcat_col") or sp.get("pcat_col"):
            keys.add("product_category")
        if qs.get("category_col"):
            keys.add("category")
        if keys:
            expected[f'{defn["screen"]}:{kpi}'] = keys
    assert expected, "后端应有吃板级筛选的下钻指标"
    assert set(table) <= known, f"登记了未知指标：{set(table) - known}"
    assert {k: v for k, v in table.items() if v} == expected, \
        "前端筛选条生效表与后端声明不一致（新增指标须同步登记）"


def test_e6_existing_spc_assertions_scoped_to_legacy_characteristics():
    """E6：既有 SPC 断言已按设计 §2.1.5 改为「限定既有 3 特性子集」。

    守护方式：静态扫描 ``tests/v2/test_dashboard_drill.py``，不得再出现
    「全量特性数 == 3」「全量 cpk 列表精确比较」这类会被新增特性打破的断言。
    """
    path = _ROOT / "tests" / "v2" / "test_dashboard_drill.py"
    src = path.read_text(encoding="utf-8")
    for brittle in ('len(d["kpis"]) == 3', 'len(d["capabilities"]) == 3',
                    "approx([0.615, 1.146, 1.461])"):
        assert brittle not in src, (
            f"既有 SPC 断言仍按全量特性数比较（{brittle}）—— "
            "新增特性后会红，须按设计 §2.1.5 限定为既有 3 特性子集")
    assert "焊点高度" in src, "既有 SPC 断言应显式限定既有特性名"


# ============================================================================
# 7. §10.6 交付纪律断言 F1~F3
# ============================================================================

def test_f3_memory_daily_file_records_spc():
    """F3：``.workbuddy/memory/2026-10-20.md`` 已 append 记录本需求。

    CI 可见性（2026-10-23 修复「每个 run 必红」）：
    ``.workbuddy/`` 在 .gitignore 里（本地工作数据目录，刻意不入库），全新克隆 /
    CI checkout 中整个目录不存在——本条纪律是**本地工作纪律**，CI 无从验证。
    旧写法无条件 ``assert path.exists()``：本地（日志在场）全绿、远端每个 run
    必红，且红得毫无信息量——CI 汇总 "1 failed, 2283 passed" 的唯一失败项
    一直是它（2026-10-23 经 annotations 通道定位）。现改为「在场才校验」：
    缺席（fresh clone，或日志已被 30 天蒸馏规则清理）→ skip；在场仍校验
    内容关键词，本地守护价值不变。
    """
    path = _ROOT / ".workbuddy" / "memory" / "2026-10-20.md"
    if not path.exists():
        pytest.skip(".workbuddy/memory 不在库内（.gitignore）或已被蒸馏清理——本地纪律，CI 不验证")
    assert "SPC" in path.read_text(encoding="utf-8"), \
        "当日记忆未记录 SPC 看板增量"


# ============================================================================
# 8. 扩展守卫（由 §10 + 数据现实推导，非清单条目但直接决定清单能否成立）
# ============================================================================

def test_x1_capability_query_not_truncated_by_row_cap(big_registry):
    """扩展守卫：能力表取数**不得被 registry 默认行数上限截断**。

    为什么必须有：设计 §2.1.4 规划的新种子有 ~1275 行，而
    ``DataSourceRegistry.execute_readonly_sql`` 默认 ``row_cap=200`` →
    若聚合仍走默认上限，Cpk 会按前 200 行算（静默算错），A5「同源」与
    A8「三档可见」随之失真。本守卫用 250 行数据把该风险钉死。
    """
    payload = _build_spc(big_registry, window="window")
    caps = _cap_by_name(payload)
    assert "行数上限探测特性" in caps, "探测特性缺失"
    assert caps["行数上限探测特性"]["n_total"] == 250, (
        "能力表被行数上限截断（Cpk 静默算错）—— 聚合需抬高 row_cap")


def test_x2_api_dashboard_payload_docstring_mentions_spc_window():
    """扩展守卫：``_dashboard_payload`` 文档串不得再称「spc 不接口径参数」。"""
    src = _read(_ROOT / "src" / "mfg_agents" / "v2" / "demo" / "api.py")
    block = _fn_block(src, "def _dashboard_payload")
    # 说明：早先的断言写成 ``"spc" not in block or "不接口径参数" not in block``
    # —— 这是一个「或」：只要**其中一个**子串缺席就通过，过于宽松；而新契约下
    # 文档串里「spc 支持 window|day|month」与「不接口径参数（orders）」**分属
    # 不同的主语**，两者同时合法出现 → 旧断言必然误报。
    # 这里改成**按主语归属**精确断言（比旧断言更强，不是放松）：
    #   1) spc 必须被明确列入「支持时间口径」的看板；
    #   2) 「不接口径参数」只能归属 orders；
    #   3) spc 附近不得出现「不接口径参数」。
    assert re.search(r"spc[^\n]{0,40}支持", block), (
        "_dashboard_payload 文档串未声明 spc 支持时间口径（window|day|month）")
    assert re.search(r"不接口径参数[^\n]{0,20}orders", block), (
        "_dashboard_payload 文档串未把「不接口径参数」归属到 orders")
    assert not re.search(r"spc[^\n]{0,20}不接口径参数", block), (
        "_dashboard_payload 文档串仍写「spc 不接口径参数」（与新契约矛盾）")
