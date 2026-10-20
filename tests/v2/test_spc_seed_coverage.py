"""SPC 维度种子**守护测试**（生成器 ↔ 交付种子 ↔ 看板口径的配套守卫）。

为什么需要这个文件（真实事故驱动）：
- 交付种子 ``data/seed_spc_dimensions.sql`` 是**一次性脚本产物**。历史上出现过
  「改了生成器没重生成种子」「种子被改回旧版、演示库仍是旧版残留」两类**静默漂移**：
  种子与生成器脱节、演示库无法由种子复现（id 段撞号 + ``INSERT OR IGNORE`` 静默跳过）。
- 本文件把五条纪律**变成可执行断言**（不是注释、不是文档）：
  ① **生成器 ↔ 交付种子字节级配套**（改了生成器不重生成 → 立即红）；
  ② **种子覆盖度 + 三档判定齐备**（48 组 = 4 工段 × 4 产品分类 × 3 口径）；
  ③ **既有 3 特性的规格限与 ``data/seed_v3.sql`` 严格一致**且同特性跨日期恒定
     （守 P0-1 那个真实缺陷：规格限随日期漂移 → 换统计窗口即换判定）；
  ④ **老库重放 → 干净重建**（守交付纪律①：交付种子必须带 DELETE 守卫 + 显式事务
     —— 否则老库残留旧版种子时 ``INSERT OR IGNORE`` 静默跳过 → 少行 + 僵尸特性）；
  ⑤ **守卫的删除边界**（守 2026-10-20 那个真实 bug：``DELETE`` 只按归属标记删 →
     把 ``data/seed_v3.sql`` 的既有 75 行一起删掉；且让位改了 id 却没推进
     ``sqlite_sequence`` → 后续用户 ingest 的自增 id 撞号 → 用户写不进数据）。

取数纪律（与 ``tests/v2/test_spc_dashboard.py`` 同款范式）：
- **不碰演示库**：全部在 ``tmp_path`` 下自建临时 SQLite（``spc_measurement`` 由种子
  自身的 ``CREATE TABLE`` 建，``quality_defect`` 建空表），经
  ``DataSourceRegistry`` + ``SQLiteDataSource`` **只读**接入；
- **不覆盖仓库里的种子文件**：生成器的 ``main()`` 只往 ``tmp_path`` 写，并断言
  仓库里那份种子的字节摘要**前后不变**；
- 静态断言一律用**子串 / 正则**定位，不依赖行号（并发编辑窗口下行号会漂）。
"""
from __future__ import annotations

import hashlib
import importlib.util
import re
import sqlite3
import sys
from pathlib import Path

import pytest

from mfg_agents.v2.contracts.data_source import (
    DataSourceRegistry,
    SQLiteDataSource,
)
from mfg_agents.v2.services.dashboard import build_spc_dashboard

# ============================================================================
# 0. 常量
# ============================================================================

_ROOT = Path(__file__).resolve().parents[2]
_SEED = _ROOT / "data" / "seed_spc_dimensions.sql"
_SEED_V3 = _ROOT / "data" / "seed_v3.sql"
_GENERATOR = _ROOT / "tools" / "gen_spc_dimensions_seed.py"

#: 四个工段 / 四个产品分类 / 三档判定（与 ``dashboard.MFG_*`` 同源，契约快照）
_STAGES = ("SMT", "PCM", "PACK", "整机")
_PCATS = ("消费电子", "智能电源", "储能电源", "车载")
_TIERS = ("合格", "偏低", "不合格")

#: 三档时间口径（唯一事实源 ``dashboard.WINDOWS``，此处是契约快照）
_WINDOWS = ("window", "day", "month")

#: 交付种子的规模契约（全月覆盖裁决：12 新特性 × 4 分类 × 15 天 × 2 子组 × 5 值
#: = 7200 + 既有 3 特性 10-15 补数 75 + 10-01~10-13 回填 390）
_EXPECTED_SEED_ROWS = 7665
_EXPECTED_CHARACTERISTICS = 15

#: 种子里的测量行元组：(可选 id, 特性, subgroup_no, seq, value, usl, lsl, 时间戳)
_ROW_RE = re.compile(
    r"\(\s*(?:\d+\s*,\s*)?'([^']+)'\s*,\s*(\d+)\s*,\s*(\d+)\s*,"
    r"\s*([-0-9.]+)\s*,\s*([-0-9.]+)\s*,\s*([-0-9.]+)\s*,\s*'([^']+)'", re.M)


# ============================================================================
# 1. 工具
# ============================================================================

def _load_generator():
    """按路径加载生成器模块（它是脚本，不是包 → 用 importlib 按文件路径载入）。"""
    spec = importlib.util.spec_from_file_location("gen_spc_dimensions_seed", _GENERATOR)
    assert spec and spec.loader, f"无法加载生成器：{_GENERATOR}"
    module = importlib.util.module_from_spec(spec)
    # 生成器内部会 ``sys.path.insert(ROOT/'src')`` 后 import ``mfg_agents.v2.core.spc``
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _normalized_bytes(path: Path) -> bytes:
    """读文件并把 CRLF 归一为 LF（Windows 检出时 git 会把 LF 转成 CRLF，
    归一化后仍能做**逐字节**比对，且不掩盖真实内容差异）。"""
    return path.read_bytes().replace(b"\r\n", b"\n")


def _parse_rows(text: str) -> list[tuple[str, str, str, str, str, str]]:
    """SQL 文本 → 测量行元组 (特性, subgroup_no, seq, value, usl, lsl)。"""
    return [(m.group(1), m.group(2), m.group(3), m.group(4), m.group(5), m.group(6))
            for m in _ROW_RE.finditer(text)]


def _specs_by_characteristic(text: str) -> dict[str, set[tuple[str, str]]]:
    """SQL 文本 → {特性: {(usl, lsl) 组合集合}}（规格限漂移检测的原料）。"""
    out: dict[str, set[tuple[str, str]]] = {}
    for name, _sg, _seq, _value, usl, lsl in _parse_rows(text):
        out.setdefault(name, set()).add((usl, lsl))
    return out


@pytest.fixture(scope="module")
def generator():
    return _load_generator()


def _spc_insert_statements(text: str) -> list[str]:
    """从种子 SQL 文本里摘出所有 ``INSERT ... INTO spc_measurement ...;`` 语句。

    为什么只摘这一张表：``data/seed_v3.sql`` 是**整库基线种子**（含 20+ 张表的
    INSERT），其中 ``quality_defect`` 依赖 ``data/init.sql`` 建的表 → 整文件
    executescript 在空临时库里会失败。本守护只关心 SPC 测量行 → 按表摘取。
    """
    return [m.group(0) for m in re.finditer(
        r"INSERT\s+(?:OR\s+IGNORE\s+)?INTO\s+spc_measurement\b.*?;", text, re.I | re.S)]


@pytest.fixture(scope="module")
def seed_db(tmp_path_factory):
    """把**交付种子** + **既有基线**（``seed_v3.sql`` 的 75 行）导入临时 SQLite。

    - 绝不动演示库（``data/mfg_agent.db``）；
    - 组成与演示库**完全一致**：既有 75 行（id < 4001）+ 维度种子 3675 行（id 4001+）
      → 「全厂 15 特性」在**三档口径**下都成立（默认窗口里既有 3 特性的原始批次
      来自 ``seed_v3.sql``，只导维度种子会缺 3 个）；
    - ``quality_defect`` 建一张空表（柏拉图侧要查它，缺表会被降级并打告警日志）。
    """
    folder = tmp_path_factory.mktemp("spc_seed_cov")
    db_path = folder / "spc_seed_cov.db"
    conn = sqlite3.connect(db_path)
    try:
        # 1) 交付维度种子（建表 + 3675 行，id 4001+）
        conn.executescript(_SEED.read_text(encoding="utf-8"))
        # 2) 既有基线的 75 行（id 1..75，10-13 / 10-14）
        for stmt in _spc_insert_statements(_SEED_V3.read_text(encoding="utf-8")):
            conn.executescript(stmt)
        # 3) 柏拉图侧的表（空表即可，能力表断言不依赖它）
        conn.execute(
            "CREATE TABLE IF NOT EXISTS quality_defect ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, batch_no TEXT, "
            "product_model TEXT, line_code TEXT, defect_type TEXT, "
            "defect_qty INTEGER, total_qty INTEGER, inspect_time TEXT, "
            "process_stage TEXT, defect_category TEXT, product_category TEXT)")
        conn.commit()
    finally:
        conn.close()
    registry = DataSourceRegistry()
    registry.register(SQLiteDataSource(str(db_path), name="dev"))
    return registry


#: 交付种子的 id 段（显式 id 4001 起，3675 行 → 4001..7675）
_SEED_ID_START = 4001
_SEED_ID_END = _SEED_ID_START + _EXPECTED_SEED_ROWS - 1

#: 既有基线（``data/seed_v3.sql``）的 3 个特性 —— 演示库既有数据的归属
_LEGACY_CHARACTERISTICS = ("焊点高度(mm)", "锡膏厚度(μm)", "回流焊峰值温度(℃)")


@pytest.fixture(scope="module")
def production_order_db(tmp_path_factory):
    """按**生产真实顺序**重建演示库：``seed_v3.sql`` → … → ``seed_spc_dimensions.sql``。

    为什么必须有这个夹具（真实缺陷，2026-10-20 QA 复现）：
    ``orchestrator._SEED_SQLS`` 的**顺序**是 ``seed_v3.sql`` 在前、
    ``seed_spc_dimensions.sql`` 在**最后**。而上面的 ``seed_db`` 夹具是
    **反序**（先灌交付种子、再补 seed_v3 的 75 行）→ 这个反序**掩盖**了一个
    真实缺陷：交付种子的重灌守卫若按「归属标记」删行
    （``source='demo-seed' AND ingested_by='demo'``），会把 **seed_v3 的 75 行**
    一起删掉 —— 而那 75 行带的是**同样的**归属标记，且 ``seed_v3.sql`` 已经
    执行完、**不会**再补回来 → 既有 3 特性在「最近 7 天」档**整段消失**
    （实测默认窗口能力表 15 特性 → 12 特性）。

    所以「干净重建」必须按**生产顺序**验收，否则守卫的断言强度是假的。

    实现说明：``data/seed_v3.sql`` 里除 ``spc_measurement`` 外还引用了
    ``init.sql`` 建的表（``customer_complaint`` 等，MySQL 语法）→ 整文件在
    空 SQLite 库里 executescript 会失败。故这里只取 **spc_measurement 的建表段
    + 它的 75 行 INSERT**（与 ``_spc_insert_statements`` 同一取数纪律），
    其余表对本组断言无影响。
    """
    folder = tmp_path_factory.mktemp("spc_prod_order")
    db_path = folder / "prod_order.db"
    conn = sqlite3.connect(db_path)
    try:
        v3_text = _SEED_V3.read_text(encoding="utf-8")
        # 1) 先建表（取 seed_v3 里 spc_measurement 的建表段）
        create_stmt = re.search(
            r"CREATE\s+TABLE\s+IF\s+NOT\s+EXISTS\s+spc_measurement\s*\(.*?\);",
            v3_text, re.I | re.S)
        assert create_stmt, "data/seed_v3.sql 里找不到 spc_measurement 的建表语句"
        conn.executescript(create_stmt.group(0))
        # 2) 再灌 seed_v3 的既有 75 行（生产顺序：seed_v3 在交付种子**之前**）
        for stmt in _spc_insert_statements(v3_text):
            conn.executescript(stmt)
        conn.commit()
        # 3) 再灌交付种子（生产顺序：交付种子在**最后** → 它的守卫会看到既有 75 行）
        conn.executescript(_SEED.read_text(encoding="utf-8"))
        conn.commit()
        # 4) 柏拉图侧的表（空表即可，能力表断言不依赖它）
        conn.execute(
            "CREATE TABLE IF NOT EXISTS quality_defect ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, batch_no TEXT, "
            "product_model TEXT, line_code TEXT, defect_type TEXT, "
            "defect_qty INTEGER, total_qty INTEGER, inspect_time TEXT, "
            "process_stage TEXT, defect_category TEXT, product_category TEXT)")
        conn.commit()
    finally:
        conn.close()
    registry = DataSourceRegistry()
    registry.register(SQLiteDataSource(str(db_path), name="dev"))
    return registry


# ============================================================================
# 2. 守卫 ①：生成器 ↔ 交付种子字节级配套
# ============================================================================

def test_g1_generator_output_is_byte_identical_to_delivered_seed(generator):
    """守卫 ①：生成器纯函数产出的 SQL 与仓库里的交付种子**逐字节相等**。

    防止「改了生成器却忘了重生成种子」这类**静默漂移**复发（历史上真发生过：
    种子被改回旧版、演示库仍是旧版残留，两者 id 段撞号 + ``INSERT OR IGNORE``
    → 重灌被静默跳过 → 演示库无法由交付种子复现）。

    取数纪律：**只读**仓库里的种子，**不覆盖**它。
    """
    produced = generator.build_sql_text(generator.build_rows()).encode("utf-8")
    delivered = _normalized_bytes(_SEED)
    assert produced.replace(b"\r\n", b"\n") == delivered, (
        "生成器产物与交付种子 data/seed_spc_dimensions.sql 不一致 —— "
        "改了生成器必须重跑 `python tools/gen_spc_dimensions_seed.py` "
        "（否则种子与生成器脱节，演示库无法由种子复现）")


def test_g1_generator_cli_writes_identical_bytes_to_tmp_dir(generator, tmp_path):
    """守卫 ① 强化：生成器 **CLI 路径**（``main(输出路径)``）也必须字节一致。

    顺带证明「在临时目录里跑生成器」**不会**动到仓库里的交付种子
    （守护测试不得覆盖交付物）。
    """
    before = hashlib.sha256(_normalized_bytes(_SEED)).hexdigest()
    out = tmp_path / "regenerated_seed.sql"
    generator.main(out)
    assert out.exists(), "生成器未产出文件"
    assert out.read_bytes().replace(b"\r\n", b"\n") == _normalized_bytes(_SEED), (
        "生成器 CLI 产物与交付种子不一致")
    after = hashlib.sha256(_normalized_bytes(_SEED)).hexdigest()
    assert before == after, "守护测试**覆盖**了仓库里的交付种子（禁止）"


def test_g1_delivered_seed_shape():
    """守卫 ① 补充：交付种子的规模契约（行数 / 特性数 / id 段 / 幂等写法）。"""
    text = _SEED.read_text(encoding="utf-8")
    rows = _parse_rows(text)
    assert len(rows) == _EXPECTED_SEED_ROWS, (
        f"交付种子应为 {_EXPECTED_SEED_ROWS} 行，实测 {len(rows)} 行")
    names = {r[0] for r in rows}
    assert len(names) == _EXPECTED_CHARACTERISTICS, (
        f"交付种子应为 {_EXPECTED_CHARACTERISTICS} 个特性，实测 {len(names)} 个")
    assert "INSERT OR IGNORE INTO spc_measurement" in text, "种子必须幂等（INSERT OR IGNORE）"
    assert "WHERE process_stage IS NULL" in text, "老行回填必须带 IS NULL 守卫（幂等）"
    # id 段：4001 起显式给出、连续、无重复
    ids = [int(m.group(1)) for m in re.finditer(r"^\((\d+), '", text, re.M)]
    assert ids == list(range(ids[0], ids[0] + len(ids))), "种子 id 段不连续"
    assert ids[0] == 4001, f"种子 id 段应从 4001 起，实测 {ids[0]}"


# ============================================================================
# 3. 守卫 ④：老库重放 → **干净重建**（设计 §2.1.4 A′ 交付纪律①）
# ============================================================================

def test_g4_delivered_seed_has_id_guard_and_atomic_rebuild():
    """守卫 ④ 静态：交付种子必须带 **DELETE 守卫** + **显式事务**。

    为什么必须有（真实缺陷，工程师 2026-10-20 实测复现）：
    ``INSERT OR IGNORE`` + 显式 id 只保证「同 id 不重复插入」，**不保证「同 id 不残留
    旧值」**。老库若已应用过**旧版**本文件（id 段 4001-5275、特性名与规模都不同），
    重灌新种子时旧行占着同 id → 新行被 ``INSERT OR IGNORE`` **静默跳过** →
    结果 = 旧版「僵尸特性」残留 + 新特性少行（实测「元件贴装偏移量(μm)」只有 275/300 行）。
    这违反设计 §2.1.4 A′ 裁决块的交付纪律①「演示库必须能由『seed_v3 的 75 行 +
    交付种子』**干净重建**」。

    原子性同理由：``executescript`` **逐语句隐式提交** → 不加显式事务时，
    DELETE 早于 INSERT 提交 → 重灌中途失败会留下「已删未插」的**半拉子空表**
    （实测：失败后表里只剩 id=1 的旧行，id=2001 行被清空）→ **静默数据丢失**。

    静态断言用**子串 / 正则**定位（不依赖行号，并发编辑窗口下行号会漂）。
    """
    text = _SEED.read_text(encoding="utf-8")
    assert re.search(r"DELETE\s+FROM\s+spc_measurement", text, re.I), (
        "交付种子必须先删本文件拥有的行再重灌 —— 否则老库残留旧版种子时，"
        "INSERT OR IGNORE 会静默跳过（少行 + 僵尸特性），违反交付纪律①「干净重建」")
    # 守卫边界：不得无条件清空整表（会误伤既有 75 行与运行时数据）
    delete_stmt = re.search(r"DELETE\s+FROM\s+spc_measurement.*?;", text, re.I | re.S)
    assert delete_stmt, "DELETE 守卫语句无法解析"
    stmt = delete_stmt.group(0)
    # 语义要求（**断言语义、不锁实现写法**）：DELETE 守卫必须同时具备两条防线 ——
    #   ① **id 下界**：把「别的种子文件写的行」（``data/seed_v3.sql`` 的 75 行，
    #      归属标记与本文件**完全相同**）挡在删除范围之外 → 既有 3 特性在
    #      「最近 7 天」档不会整段消失（默认窗口能力表 15 → 12）；
    #   ② **归属标记**：区分「种子行」与「运行时受治理写入的行」。
    # 写法容忍：``id >= 4001`` / ``id > 4000`` / ``id BETWEEN 4001 AND ...``
    # 等价形式都接受；归属标记写成 ``= 'demo'`` 或 ``COALESCE(...) = 'demo'``
    # 也接受 —— 断的是**语义**（两个条件都在），不是字符串长相。
    assert re.search(r"id\s*(?:>=|>)\s*400[01]\b", stmt, re.I), (
        "DELETE 守卫必须限定 id 下界（4001）—— 只按归属标记删不够："
        "data/seed_v3.sql 的 75 行带的是**完全相同**的标记，"
        "会被连带删掉（既有 3 特性在「最近 7 天」档整段消失，默认窗口能力表 15→12）")
    assert re.search(r"ingested_by", stmt, re.I) and re.search(r"demo", stmt, re.I), (
        "DELETE 守卫必须按 source/ingested_by 区分「种子行」与「运行时受治理写入的行」")
    # 显式事务：DELETE 守卫 / 老行回填 / 整批重灌 原子生效
    assert re.search(r"BEGIN\s+IMMEDIATE\s*;", text, re.I), (
        "交付种子必须用显式事务（BEGIN IMMEDIATE）包裹重灌 —— 否则中途失败会留下空表")
    assert re.search(r"COMMIT\s*;", text, re.I), "交付种子缺少事务闭合 COMMIT;"
    # BEGIN 必须在 DELETE 之前、COMMIT 必须在 INSERT 之后（顺序错了等于没包住）
    begin_at = re.search(r"BEGIN\s+IMMEDIATE\s*;", text, re.I).start()
    delete_at = re.search(r"DELETE\s+FROM\s+spc_measurement", text, re.I).start()
    commit_at = re.search(r"COMMIT\s*;", text, re.I).start()
    insert_at = re.search(r"INSERT\s+OR\s+IGNORE\s+INTO\s+spc_measurement", text, re.I).start()
    assert begin_at < delete_at, "BEGIN 必须早于 DELETE 守卫"
    assert insert_at < commit_at, "COMMIT 必须晚于整批 INSERT（否则事务没包住重灌）"


def test_g4_delivered_seed_rebuilds_old_library_cleanly(tmp_path):
    """守卫 ④ 动态：老库（残留旧版种子行）重放交付种子 → **干净重建**。

    走**真实链路**：临时 SQLite + 交付种子的完整 SQL（``executescript``，与
    ``orchestrator._apply_seed`` 同一执行方式）。

    断言三件事（缺一不可）：
    ① 旧版残留的「僵尸特性」被清干净，各特性行数与目标一致；
    ② **运行时受治理写入的行不被误删**（守卫不得越界删用户数据）；
    ③ 重放两次结果恒定（幂等）。
    """
    db_path = tmp_path / "rebuild.db"
    conn = sqlite3.connect(str(db_path))
    try:
        conn.executescript(
            "CREATE TABLE IF NOT EXISTS spc_measurement ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "characteristic VARCHAR(80) NOT NULL,"
            "subgroup_no INTEGER NOT NULL, seq INTEGER NOT NULL, value REAL NOT NULL,"
            "usl REAL, lsl REAL, measured_at VARCHAR(30), source VARCHAR(40),"
            "ingested_by VARCHAR(40), ingested_at VARCHAR(30) NOT NULL,"
            "process_stage TEXT, product_category TEXT);")
        seed_end = 4001 + _EXPECTED_SEED_ROWS - 1
        # 造「老库」：旧版种子写下的僵尸行（id 落在本文件 id 段内 → 与新行撞号）
        conn.execute(
            "INSERT INTO spc_measurement (id, characteristic, subgroup_no, seq, value,"
            " usl, lsl, measured_at, source, ingested_by, ingested_at)"
            " VALUES (4001, '僵尸特性(μm)', 1, 1, 30.0, 40.0, 0.0,"
            " '2026-10-10 08:00:00', 'demo-seed', 'demo', '2026-10-16T08:00:00')")
        # 旧版种子超出本段上限的残留
        conn.execute(
            "INSERT INTO spc_measurement (id, characteristic, subgroup_no, seq, value,"
            " usl, lsl, measured_at, source, ingested_by, ingested_at)"
            f" VALUES ({seed_end + 1}, '旧版残留(μm)', 1, 1, 30.0, 40.0, 0.0,"
            " '2026-10-10 08:00:00', 'demo-seed', 'demo', '2026-10-16T08:00:00')")
        # 运行时受治理写入的行（ingested_by = 用户 id）→ 守卫不得动它
        conn.execute(
            "INSERT INTO spc_measurement (id, characteristic, subgroup_no, seq, value,"
            " usl, lsl, measured_at, source, ingested_by, ingested_at)"
            f" VALUES ({seed_end + 2}, '运行时写入(μm)', 1, 1, 30.0, 40.0, 0.0,"
            " '2026-10-15 09:00:00', '设备', 'u_alice', '2026-10-16T09:00:00')")
        conn.commit()

        # 载入 seed_v3.sql 的既有 75 行（id 1..75）—— 演示库的完整组成是
        # 「seed_v3 的 75 行 + 交付种子」，重建自检必须按这个完整组成验收
        for stmt in _spc_insert_statements(_SEED_V3.read_text(encoding="utf-8")):
            conn.executescript(stmt)
        conn.commit()

        seed_sql = _SEED.read_text(encoding="utf-8")
        conn.executescript(seed_sql)
        conn.executescript(seed_sql)  # 幂等：重放两次结果恒定

        counts = dict(conn.execute(
            "SELECT characteristic, COUNT(*) FROM spc_measurement GROUP BY 1"))
        for zombie in ("僵尸特性(μm)", "旧版残留(μm)"):
            assert zombie not in counts, (
                f"重建后仍残留僵尸特性 {zombie} —— DELETE 守卫未生效"
                "（老库重放会静默跳过新行，违反交付纪律①「干净重建」）")
        assert counts.get("运行时写入(μm)") == 1, (
            "重建把运行时受治理写入的行删掉了 —— DELETE 守卫越界（必须按 ingested_by 区分）")
        # 各特性行数（全月覆盖契约）：既有 3 特性 = seed_v3 25 行（10-14）+ 10-15 补数
        # 25 行 + 10-01~10-13 回填 130 行 = 180；新特性 = 4 分类 × 15 天 × 2 子组 × 5 值 = 600
        for name in ("焊点高度(mm)", "锡膏厚度(μm)", "回流焊峰值温度(℃)"):
            assert counts.get(name) == 180, (
                f"重建后既有特性 {name} 行数 {counts.get(name)} ≠ 180"
                "（seed_v3 25 + 10-15 补数 25 + 10-01~10-13 回填 130）")
        new_chars = [n for n in counts
                     if n not in ("焊点高度(mm)", "锡膏厚度(μm)", "回流焊峰值温度(℃)",
                                  "运行时写入(μm)")]
        assert len(new_chars) == 12, f"重建后新特性数 {len(new_chars)} ≠ 12"
        for name in new_chars:
            assert counts[name] == 600, (
                f"重建后新特性 {name} 行数 {counts[name]} ≠ 600（老库残留未被清干净）")
        # 既有 75 行必须**未被守卫波及**（守卫下界 4001）
        assert sum(counts.get(n, 0) for n in
                   ("焊点高度(mm)", "锡膏厚度(μm)", "回流焊峰值温度(℃)")) == 540, \
            "既有 3 特性的 540 行（seed_v3 75 + 交付种子 465）不全 —— DELETE 守卫越界"
    finally:
        conn.close()


# ============================================================================
# 3.5 守卫 ④ 补充：重灌守卫的**三个真实 P0**（2026-10-20 QA 复现）
# ============================================================================

def test_g4_rebuild_must_not_delete_seed_v3_legacy_rows(production_order_db):
    """守卫 ④ 补充 P0-A：重灌**不得删掉 ``seed_v3.sql`` 的既有 75 行**。

    真实缺陷（2026-10-20 QA 复现，当前交付种子**已复现**）：
    交付种子的重灌守卫改成「按归属标记删」（``source='demo-seed' AND
    ingested_by='demo'``）后，会把 **``data/seed_v3.sql`` 的 75 行一起删掉** ——
    那 75 行带的是**完全相同**的归属标记（``source='demo-seed'`` /
    ``ingested_by='demo'``），且 ``seed_v3.sql`` 在 ``_SEED_SQLS`` 里排在
    **前面**（已执行完、**不会**再补回来）→ 既有 3 特性在「最近 7 天」档
    **整段消失**。

    实测后果（生产顺序重建）：
    - 既有 3 特性行数 50 → **25**（只剩交付种子的 10-15 补数）；
    - 默认窗口（``window`` = 10-08~10-14）能力表 **15 特性 → 12 特性**，
      既有 3 特性**完全不出现在看板上**；
    - 交付种子的守卫**自己**是绿的（它只断言「种子行数对」）→ **静默数据丢失**。

    这条守卫按**生产顺序**重建（``seed_v3`` 先、交付种子后）→ 守住真实链路。
    """
    conn = production_order_db  # 走 registry 的只读 SQL 通道（不碰演示库）
    rows = conn.execute_readonly_sql(
        "SELECT COUNT(*) AS n FROM spc_measurement WHERE id <= 75")
    legacy_rows = int(rows[0]["n"]) if rows else 0
    assert legacy_rows == 75, (
        f"重建后 seed_v3 的既有 75 行只剩 {legacy_rows} 行 —— "
        "重灌守卫把别的种子文件写的行一起删了（归属标记相同 → 无法区分来源）"
        "（既有 3 特性在「最近 7 天」档会整段消失，默认窗口能力表 15→12）")


def test_g4_rebuild_preserves_all_legacy_characteristics(production_order_db):
    """守卫 ④ 补充 P0-A（能力表视角）：默认窗口下既有 3 特性必须**仍在**能力表里。

    为什么单独一条：P0-A 的后果是**看板可见的**（默认窗口少 3 个特性），
    而行数守卫视野更广、更容易被「行数对但归属错」蒙混过去 → 两条并存。
    """
    for window in ("window", "day", "month"):
        payload = build_spc_dashboard(production_order_db, window=window)
        caps = payload.get("capabilities") or []
        names = {cap.get("characteristic") for cap in caps}
        missing = [n for n in _LEGACY_CHARACTERISTICS if n not in names]
        assert not missing, (
            f"{window} 档能力表缺少既有特性 {missing} —— "
            "重灌把 seed_v3 的既有 75 行删了（既有 3 特性整段消失）")
        assert len(caps) == _EXPECTED_CHARACTERISTICS, (
            f"{window} 档全厂特性数 {len(caps)} ≠ {_EXPECTED_CHARACTERISTICS}"
            "（既有 3 特性缺失 → 看板首屏少 3 行）")


def test_g4_rebuild_must_not_lose_seed_rows_to_id_collision(tmp_path):
    """守卫 ④ 补充 P0-B：重灌后**种子行一个都不能少**（不得被 ``INSERT OR IGNORE`` 静默跳过）。

    真实缺陷（2026-10-20 QA 复现）：
    守卫为了「不误删运行时行」，会先把 id 段内的**非种子行**「让位」到 id 段之外。
    但「让位」用的新 id 若**仍落在本文件的 id 段内**（4001..7675），就会与本文件
    的种子行**撞号** → 该种子行被 ``INSERT OR IGNORE`` **静默跳过** → 种子少 1 行。

    实测（老库：旧版种子占 4001..5275 + 用户行 5276）：
    - 用户行被让位到 **5277**（**仍在 id 段内**）→ 抢占了 id=5277 的种子行；
    - 结果：种子标记行 **3674 ≠ 3675**，静默少 1 行。

    这条守卫用**「让位目标必须落在 id 段之外」**来表达，实现方式不限
    （改 id 段上界 / 改用不撞号的分配方式 / 改用 ``INSERT OR REPLACE`` 都应通过）。
    """
    db_path = tmp_path / "collision.db"
    conn = sqlite3.connect(str(db_path))
    try:
        conn.executescript(
            "CREATE TABLE IF NOT EXISTS spc_measurement ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "characteristic VARCHAR(80) NOT NULL,"
            "subgroup_no INTEGER NOT NULL, seq INTEGER NOT NULL, value REAL NOT NULL,"
            "usl REAL, lsl REAL, measured_at VARCHAR(30), source VARCHAR(40),"
            "ingested_by VARCHAR(40), ingested_at VARCHAR(30) NOT NULL,"
            "process_stage TEXT, product_category TEXT);")
        # 老库：旧版种子占住 id 段前半段（4001..5275）
        for k in range(275):
            conn.execute(
                "INSERT INTO spc_measurement (id, characteristic, subgroup_no, seq, value,"
                " usl, lsl, measured_at, source, ingested_by, ingested_at)"
                " VALUES (?, ?, 1, 1, 30.0, 40.0, 0.0,"
                " '2026-10-10 08:00:00', 'demo-seed', 'demo', '2026-10-16T08:00:00')",
                (_SEED_ID_START + k, f"旧特性{k}(μm)"))
        # 用户 ingest 的运行时行：AUTOINCREMENT 跟随表内最大 id → 拿到 5276（**落在 id 段内**）
        conn.execute(
            "INSERT INTO spc_measurement (id, characteristic, subgroup_no, seq, value,"
            " usl, lsl, measured_at, source, ingested_by, ingested_at)"
            " VALUES (5276, '用户行(μm)', 1, 1, 30.0, 40.0, 0.0,"
            " '2026-10-15 09:00:00', '设备', 'u_alice', '2026-10-16T09:00:00')")
        conn.commit()

        conn.executescript(_SEED.read_text(encoding="utf-8"))
        conn.commit()

        seed_marked = conn.execute(
            "SELECT COUNT(*) FROM spc_measurement"
            " WHERE source = 'demo-seed' AND ingested_by = 'demo'").fetchone()[0]
        assert seed_marked == _EXPECTED_SEED_ROWS, (
            f"重灌后种子行只有 {seed_marked} 行 ≠ {_EXPECTED_SEED_ROWS} —— "
            "有种子行被 INSERT OR IGNORE **静默跳过**了"
            "（多半是「让位」后的运行时行仍落在本文件 id 段内 → 撞号）")
        # 让位后的运行时行必须**真的挪出了** id 段（否则下一轮重灌还会再撞一次）
        moved = conn.execute(
            "SELECT id FROM spc_measurement WHERE characteristic = '用户行(μm)'").fetchall()
        assert moved, "运行时受治理写入的行不见了 —— 守卫越界删了用户数据"
        assert all(r[0] > _SEED_ID_END for r in moved), (
            f"运行时行被让位到 {[r[0] for r in moved]}，仍落在本文件 id 段 "
            f"({_SEED_ID_START}..{_SEED_ID_END}) 内 → 会与种子行撞号")
    finally:
        conn.close()


# ============================================================================
# 4. 守卫 ②：种子覆盖度 + 三档判定齐备（48 组）
# ============================================================================

def test_g2_seed_covers_all_48_stage_category_window_combinations(seed_db):
    """守卫 ②：48 组（4 工段 × 4 产品分类 × 3 口径）逐组校验
    ① 有特性、有数据 ② **三档判定齐备** ③ 无「不可判定」。

    走**真实链路**（``build_spc_dashboard`` + 真实 SQLite + 真实统计内核），
    不是「按行数算个大概」—— 判定档位必须在筛选后的子集上**重算**才有意义。
    """
    checked = 0
    for stage in _STAGES:
        for pcat in _PCATS:
            for window in _WINDOWS:
                payload = build_spc_dashboard(seed_db, window=window,
                                             stage=stage, product_category=pcat)
                caps = payload.get("capabilities") or []
                tag = f"{stage}/{pcat}/{window}"
                assert caps, f"{tag}: 筛选后无任何特性（种子覆盖度不足）"
                assert not payload.get("data_gap"), f"{tag}: 载荷被标为数据缺口"
                for cap in caps:
                    assert cap.get("data_gap") is not True, (
                        f"{tag}: 特性 {cap.get('characteristic')} 判定不可算（缺规格限）")
                    assert cap.get("n_total", 0) > 0, (
                        f"{tag}: 特性 {cap.get('characteristic')} 无测量数据")
                    assert cap.get("verdict") in _TIERS, (
                        f"{tag}: 特性 {cap.get('characteristic')} 判定非法 "
                        f"{cap.get('verdict')}")
                verdicts = {cap["verdict"] for cap in caps}
                assert set(_TIERS) <= verdicts, (
                    f"{tag}: 三档判定未齐备 → 实际 {sorted(verdicts)}"
                    "（看板一筛就塌，只剩单一档位）")
                checked += 1
    assert checked == 48, f"应校验 48 组，实测 {checked} 组"


def test_g2_seed_three_tier_windows_whole_plant(seed_db):
    """守卫 ② 补充：三档时间口径**全厂**（不筛选）各 15 个特性且三档齐备。

    「三档判定齐备」在**不筛选**时也必须成立 → 看板首屏不能只有一档。
    """
    for window in _WINDOWS:
        payload = build_spc_dashboard(seed_db, window=window)
        caps = payload.get("capabilities") or []
        assert len(caps) == _EXPECTED_CHARACTERISTICS, (
            f"{window}: 全厂特性数应为 {_EXPECTED_CHARACTERISTICS}，实测 {len(caps)}")
        verdicts = {cap["verdict"] for cap in caps}
        assert set(_TIERS) <= verdicts, (
            f"{window}: 全厂三档判定未齐备 → {sorted(verdicts)}")
        for cap in caps:
            assert cap.get("data_gap") is not True, (
                f"{window}: 特性 {cap.get('characteristic')} 判定不可算")


# ============================================================================
# 4. 守卫 ③：既有 3 特性的规格限与 seed_v3.sql 严格一致
# ============================================================================

def test_g3_legacy_spec_limits_match_seed_v3(generator):
    """守卫 ③：从 ``data/seed_v3.sql`` 解析既有 3 特性的 usl/lsl，
    与交付种子里**同名**特性的 usl/lsl 逐条比对 → 必须完全相等。

    为什么必须守（真实缺陷，2026-10-20）：规格限是**特性的固有属性**，不得随日期
    变化。``dashboard._spc_caps_from_rows`` 取该特性**第一行**的 usl/lsl →
    同一特性跨日期写不同规格限，会让**换统计窗口即换判定**
    （锡膏厚度 10-13 写 150/90、10-15 写 160/120 → 最近7天 1.146「偏低」
    → 当日 0.877「不合格」）→ 数据自相矛盾，违反「不编造数据」。
    """
    v3_specs = _specs_by_characteristic(_SEED_V3.read_text(encoding="utf-8"))
    assert len(v3_specs) == 3, (
        f"seed_v3.sql 预期 3 个既有特性，实测 {len(v3_specs)}：{sorted(v3_specs)}")
    seed_specs = _specs_by_characteristic(_SEED.read_text(encoding="utf-8"))
    for name, specs in sorted(v3_specs.items()):
        assert len(specs) == 1, f"seed_v3.sql 里 {name} 自身规格限就不唯一：{sorted(specs)}"
        assert name in seed_specs, f"交付种子缺少既有特性 {name}"
        assert seed_specs[name] == specs, (
            f"既有特性 {name} 的规格限与 data/seed_v3.sql 不一致："
            f"交付种子 {sorted(seed_specs[name])} ≠ seed_v3 {sorted(specs)}")


def test_g3_spec_limits_constant_across_dates_in_delivered_seed():
    """守卫 ③ 补充：交付种子里**每个**特性的 usl/lsl 组合唯一（跨日期恒定）。

    这条同时覆盖既有 3 特性（10-13 原批 + 10-15 补数）与 12 个新特性
    （10-10 / 10-12 / 10-15 三天）→ 任何一天都不会让判定翻转。
    """
    specs = _specs_by_characteristic(_SEED.read_text(encoding="utf-8"))
    drifted = {name: sorted(vals) for name, vals in specs.items() if len(vals) > 1}
    assert not drifted, (
        "同一特性出现多个规格限组合 → 换统计窗口即换判定"
        f"（_spc_caps_from_rows 取首行规格限）: {drifted}")


def test_g3_legacy_spec_limits_equal_generator_constants(generator):
    """守卫 ③ 补充：生成器常量里的既有 3 特性规格限与 ``data/seed_v3.sql`` 一致。

    防止有人改了生成器的 ``LEGACY`` 常量却没察觉 —— 交付种子与既有基线脱节。
    """
    v3_specs = _specs_by_characteristic(_SEED_V3.read_text(encoding="utf-8"))
    # ``generator.LEGACY`` 元组：(特性, 工段, 产品分类, usl, lsl, 目标 cpk, 起始时刻)
    legacy = {name: (float(usl), float(lsl))
              for name, _stage, _cat, usl, lsl, *_rest in generator.LEGACY}
    assert set(legacy) == set(v3_specs), (
        f"生成器 LEGACY 与 seed_v3.sql 的既有特性集合不一致："
        f"{sorted(legacy)} ≠ {sorted(v3_specs)}")
    for name, specs in sorted(v3_specs.items()):
        assert len(specs) == 1, name
        usl, lsl = next(iter(specs))
        assert legacy[name] == (float(usl), float(lsl)), (
            f"生成器 LEGACY 里 {name} 的规格限 {legacy[name]} "
            f"≠ seed_v3.sql 的 {(float(usl), float(lsl))}")
