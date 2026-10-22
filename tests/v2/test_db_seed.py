"""D15 迁移第 5 项：SeedRunner 契约与启动播种。"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from mfg_agents.v2.services.db_seed import SeedRunner, SQLiteSeedRunner
from mfg_agents.v2.core import orchestrator


def _write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def test_seed_runner_applies_idempotent_sql_in_order(tmp_path):
    first = _write(tmp_path / "seed_a.sql",
                   "CREATE TABLE IF NOT EXISTS demo_seed (id INTEGER PRIMARY KEY, v TEXT);")
    second = _write(tmp_path / "seed_b.sql",
                    "INSERT OR IGNORE INTO demo_seed (id, v) VALUES (1, 'x');")
    db = str(tmp_path / "demo.db")
    runner = SQLiteSeedRunner([first, second])
    assert isinstance(runner, SeedRunner)

    assert runner.apply(db) == 2
    assert runner.apply(db) == 2  # 可重放

    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM demo_seed").fetchone()[0] == 1


def test_seed_runner_skips_missing_files(tmp_path):
    runner = SQLiteSeedRunner([tmp_path / "nope.sql"])
    assert runner.apply(str(tmp_path / "demo.db")) == 0


def test_seed_runner_failure_does_not_raise(tmp_path):
    bad = _write(tmp_path / "bad.sql", "THIS IS NOT SQL;")
    runner = SQLiteSeedRunner([bad])
    assert runner.apply(str(tmp_path / "demo.db")) == 0  # 降级，不阻断启动


def test_seed_runner_adds_legacy_columns_before_seed(tmp_path):
    db = str(tmp_path / "legacy.db")
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE quality_defect (id INTEGER PRIMARY KEY, qty REAL)")
        conn.execute("CREATE TABLE std_standard_audit (id INTEGER PRIMARY KEY)")
        conn.execute("CREATE TABLE equipment_run_time (id INTEGER PRIMARY KEY)")
        conn.execute("CREATE TABLE spc_measurement (id INTEGER PRIMARY KEY)")

    # 空操作种子：只触发 _ensure_columns
    noop = _write(tmp_path / "noop.sql", "SELECT 1;")
    SQLiteSeedRunner([noop]).apply(db)

    with sqlite3.connect(db) as conn:
        assert {"process_stage", "defect_category", "product_category"} <= {
            r[1] for r in conn.execute("PRAGMA table_info(quality_defect)")}
        assert {"process_stage", "team"} <= {
            r[1] for r in conn.execute("PRAGMA table_info(std_standard_audit)")}
        assert "stop_reason" in {
            r[1] for r in conn.execute("PRAGMA table_info(equipment_run_time)")}
        assert {"process_stage", "product_category"} <= {
            r[1] for r in conn.execute("PRAGMA table_info(spc_measurement)")}


def test_orchestrator_compat_entrypoints_delegate():
    """旧入口保留（工具脚本/注释仍引用），不再在本模块持有 sqlite3。"""
    assert callable(orchestrator._apply_seed)
    assert callable(orchestrator._ensure_columns)
    assert not hasattr(orchestrator, "sqlite3")  # 局部 import 也已移除
