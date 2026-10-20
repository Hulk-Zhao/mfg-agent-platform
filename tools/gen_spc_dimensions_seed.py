"""SPC 过程能力维度种子生成器（一次性脚本，产出 data/seed_spc_dimensions.sql）。

口径依据：docs/spc-dashboard-upgrade-design.md §2.1.4（数据层种子策略）+ §10.1（验收断言）。

为什么要有这个脚本：
- ``spc_measurement`` 此前只有 75 行（3 特性 × 10-13/10-14），既无 ``process_stage``
  也没有 ``product_category`` → 板级「工段 / 产品分类」筛选必然筛空；
- 「当日」口径（DEMO_TODAY=2026-10-15）更是 0 行 → 三档时间口径里有一档是空板。
- 脚本只做**确定性拼装**：固定随机种子 + 反标定 σ，同一输入永远产出同一份 SQL
  （字节级可重现，便于评审与复现）。

规模（**与当前交付种子一致**）：
- 既有 3 特性（焊点高度 / 锡膏厚度 / 回流焊峰值温度）**取值一字不改**：
  10-15 各补一天（5 子组 × 5 值 = 25 行/特性 → 75 行，子组号 **6-10**，既有批次占
  1-5 → 两批不混样）+ **10-01 ~ 10-13 回填**（每天 2 子组，子组号 **11 起**逐日递增，
  3 × 13 × 10 = 390 行）——10-14 是 seed_v3 的既有批次（1-5），全月仅此一天来自
  既有数据，子组号三段（1-5 / 6-10 / 11+）互不混样，否则「当月」口径会把多天
  并进同一子组 → 组容量错而 d2 常数表按 n=5 取 → Cpk 静默失真；
- 新增特性 **12 个**（**每工段 3 个，一档一个**：合格 / 偏低 / 不合格各一），
  每特性覆盖 4 产品分类 × **当月 15 天** × 2 子组 × 5 值
  = 600 行 → 合计 7200 行；
- 总计 7665 行，id 段 4001+；加 seed_v3 的 75 行 → 演示库全表 7740 行
  （低于 ``dashboard._SPC_MEAS_CAP = 8000`` 查询截断红线，余量 260 行）。

为什么是 12 个（不是 4 个）——A′ 裁决（2026-10-20，主理人拍板）：
原设计每工段只有 1 个新特性 → 板级筛选命中任一工段后**只剩单一判定档位**
（能力看板一筛就「塌」），违背需求 B「补充数据」的本意。改为每工段 3 个
（合格 / 偏低 / 不合格 各一）后，**任意 (工段 × 产品分类 × 口径) 组合下三档判定
都齐备**（契约 §10 A8 由「不可能成立」变为「可执行断言」）。

判定档位：σ 由目标 Cpk 反推（σ ≈ (usl-lsl)/(6·Cpk)），再用真实内核
``core/spc.compute_capability`` 迭代标定（口径与看板完全同源，不另算一套）。
目标档位取 合格 ≈1.55 / 偏低 ≈1.15 / 不合格 ≈0.80 —— 故意与既有 3 特性
（1.461 / 1.146 / 0.615）错开，避免新特性抢走「最差特性」排名，既有断言不受扰动。

σ 标定的**粒度**是「(天 × 产品分类) 批次」，不是「整个特性」——这是 A′ 方案的
硬要求（48 组全绿）与现实的交汇点：
- 判定档位是在**筛选后的子集**上重算的。单个「特性 × 分类」组合在「当日」口径下
  只剩 5 个子组 → σ_within 由 R̄/d2 估计，n=5 时相对标准误约 **45%**
  （SE(σ̂)/σ ≈ 1.04/√5 ≈ 0.45）→ 实测「整特性一个 σ」时 Cpk 在 [0.60, 2.92] 漂移，
  48 组里有 **14 组**掉出档位（实测记录见交付报告）→ 靠调 TIER_CPK **无法**救回；
- 每个 (天 × 产品分类) 批次本身就是**独立的生产批**（不同日、不同产品的工艺变差
  本就不同）→ 逐批次反标定 σ 既是统计上正确的做法，也能让每个批次自身命中目标档位；
- 组合口径（最近 7 天 = 2 个批次 / 当月 = 3 个批次）的 Cpk 落在
  [目标, 目标×1.11] 区间内（见 ``_self_check`` 的实测矩阵）→ 48 组全部稳定。

幂等：id 段 4001+ 归本文件所有 —— **重灌前先清空本文件拥有的行**（``DELETE`` 守卫）
+ ``INSERT OR IGNORE`` + 显式 id；老行两列用 ``process_stage IS NULL`` 守卫的
UPDATE 回填，重复执行结果恒定。

**为什么必须有 DELETE 守卫**（真实缺陷，工程师 2026-10-20 实测复现）：
``INSERT OR IGNORE`` + 显式 id 只保证「同 id 不重复插入」，**不保证「同 id 不残留旧值」**。
老库若已应用过**旧版**本文件（id 段 4001-5275，特性名与规模都不同），重灌新种子时：
- 旧行占着 4001-5275 → 新种子的同 id 行被 ``INSERT OR IGNORE`` **静默跳过**；
- 结果 = 旧版「僵尸特性」残留 + 新特性少行（实测「元件贴装偏移量(μm)」只有 275/300 行）。
这违反设计 §2.1.4 A′ 裁决块的**交付纪律①**「演示库必须能由『seed_v3 的 75 行 +
交付种子』**干净重建**」→ 守卫必须**先删本文件拥有的行再整批重灌**。

守卫的**归属判定 + id 下界 + 让位机制**（四者缺一不可，实测踩过的坑）：
- **归属判定**：按**种子标记**（``source='demo-seed' AND ingested_by='demo'``）判 —— 本文件
  与它的任何旧版本都写这个标记 → 旧版残留（僵尸特性）一并清掉；
- **id 下界 4001**：归属判定**必须**再叠一条 ``id >= 4001`` —— ``data/seed_v3.sql`` 的
  既有 75 行（id 1..75）**也**写 ``source='demo-seed' AND ingested_by='demo'`` →
  只按归属删会把**既有基线**一起删掉（真实 bug，2026-10-20 由守护测试当场抓到：
  既有 3 特性从 50 行掉到 25 行）。归属 + id 下界才是「本文件拥有的行」的完整定义。
- **让位而非删除**：id 段内若残留**非种子行**（用户受治理写入的数据），先把它**迁到高水位
  区**（``id = MAX(表内最大id, 100000) + 行号``）→ 用户数据**不丢**，同时腾出本文件的
  id 段。高水位基线**必须**取 ``MAX(表内最大 id, 100000)``：若只取「表内最大 id」，
  老库残留旧种子（最大 id 5275）时迁出目标是 5276 → **仍落在本文件 id 段内** →
  与本文件的显式 id 撞号 → 重灌被静默跳过。
- **为什么不能只按 id 段删**（``id >= 4001`` 或 ``id <= 上限``）：``AUTOINCREMENT`` 的
  ``sqlite_sequence`` 跟随**表内已有最大 id** → 老库残留旧种子（最大 id 5275）时，用户
  ingest 的一行会拿到 **5276**，**落进本文件的 id 段内** → 按 id 段删会**误删用户数据**。
  实测两种按 id 段的写法都误删（简化版「id >= 4001」与「id <= 上限」式）→ 必须按归属判。

**原子性**：``DELETE`` 守卫 + 老行回填 + 整批重灌 全部包在**显式事务**
（``BEGIN IMMEDIATE;`` … ``COMMIT;``）里 → 重灌中途失败不会留下「已删未插」的半拉子表
（``executescript`` 逐语句隐式提交，不加显式事务时 DELETE 早于 INSERT 提交 → 表被清空）。
``BEGIN IMMEDIATE``（而非 ``BEGIN``）避免 SQLite 延迟事务的**锁升级死锁**：
读者持共享锁时，写者的升级会直接 BUSY 而非等待 busy_timeout。

自检（生成即自曝，不等 QA 复跑）：
- ``_check_spec_and_subgroup_consistency``：同特性规格限唯一 + 同 (特性, subgroup_no)
  不跨日期 —— 这两条曾造成**静默** Cpk 失真（2026-10-20 真实缺陷，QA 复现）；
- ``_self_check``：**48 组**（4 工段 × 4 产品分类 × 3 口径）逐组校验
  ① 有数据 ② **三档判定齐备** ③ 无「不可判定」→ 任一组不过即**非零退出**、
  **不落盘**坏种子（fail-fast）；
- ``_self_check_rebuild``：临时库里造「老库」（旧版种子的僵尸行 + 运行时受治理行）→
  重放本文件 → 断言 ① 僵尸特性清干净、各特性行数正确 ② **运行时受治理行不受影响**
  ③ 重放两次结果恒定（幂等）—— 守住设计 §2.1.4 A′ 交付纪律①「演示库必须能由
  『seed_v3 的 75 行 + 交付种子』**干净重建**」。

用法：python tools/gen_spc_dimensions_seed.py [输出路径]
      （默认写到 data/seed_spc_dimensions.sql；传参只为守护测试在临时目录里复现，
        不用于覆盖仓库里的交付种子）
"""
from __future__ import annotations

import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from mfg_agents.v2.core.spc import compute_capability  # noqa: E402

OUT = ROOT / "data" / "seed_spc_dimensions.sql"
SEED_ID_START = 4001
RNG_SEED = 20261020

#: 新特性取数日：**当月全覆盖 10-01 ~ 10-15（15 天，每天 2 子组）**——
#: 用户反馈：3 天版（10-10/10-12/10-15）让「最近 7 天 / 当月」逐日表成片「—」，
#: 补成全月后三档口径的逐日序列**零缺日**（「—」只会在真实无生产时出现）。
#: 行数预算：12 特性 × 4 分类 × 15 天 × 2 子组 × 5 值 = 7200 行；加既有 3 特性
#: （seed_v3 75 + 10-15 补数 75 + 10-01~10-13 回填 390）→ 全表 7740 行，
#: 仍低于 ``dashboard._SPC_MEAS_CAP = 8000``（查询截断红线）。
ALL_DAYS = tuple(f"2026-10-{d:02d}" for d in range(1, 16))
#: 每天每「特性 × 产品分类」批次的子组数（组容量恒 5，与 d2=2.326 匹配）。
#: 5 → 2 的取舍：全月覆盖与 8000 行红线不可兼得时，优先**日期完整性**；
#: 单批 10 样本的 σ 反标定精度由 ``_calibrate`` 迭代保证，自检 48 组兜底。
DAILY_SUBGROUPS = 2
CATS = ("消费电子", "智能电源", "储能电源", "车载")
STAGES = ("SMT", "PCM", "PACK", "整机")

#: 判定档位目标 Cpk（不与既有 3 特性重合，见模块 docstring）
TIER_CPK = {"合格": 1.55, "偏低": 1.15, "不合格": 0.80}

#: 既有 3 特性（取值不动；仅在 10-15 补一天 + 追加两列维度）
#: （特性, 工段, 产品分类, usl, lsl, 目标 cpk, 起始时刻）
#: **usl/lsl 是该特性的固有属性**（与 ``data/seed_v3.sql`` 的原值完全一致）——
#: 规格限不得随日期变化，否则换统计窗口即换判定（真实缺陷，见模块 docstring 自检）。
LEGACY = (
    ("焊点高度(mm)", "SMT", "消费电子", 0.75, 0.45, 1.46, 8 * 60 + 30),
    ("锡膏厚度(μm)", "SMT", "消费电子", 150.0, 90.0, 1.15, 14 * 60 + 10),
    ("回流焊峰值温度(℃)", "PCM", "智能电源", 255.0, 235.0, 0.615, 10 * 60 + 40),
)

#: 既有 3 特性在 10-15 补数的子组号段（既有批次占 1-5 → 补数用 6-10，两批不混样）
LEGACY_SUBGROUP_BASE = 6
#: 既有 3 特性在 **10-01 ~ 10-13** 的回填子组号段（6-10 被 10-15 补数占用；
#: 10-14 是 seed_v3 的既有批次（1-5）→ 回填从 11 起，同特性内跨日期不混样）。
LEGACY_BACKFILL_BASE = 11
LEGACY_BACKFILL_DAYS = tuple(f"2026-10-{d:02d}" for d in range(1, 14))

#: 新增特性：**每工段 3 个**（合格 / 偏低 / 不合格 各一）——A′ 裁决，见模块 docstring。
#: 规格限为**演示规格限**（工艺侧待给定），不是产线真实规格。
#: （特性, 工段, usl, lsl, 判定档位）
NEW = (
    ("元件贴装偏移量(μm)", "SMT", 40.0, 0.0, "合格"),
    ("焊盘润湿面积(%)", "SMT", 95.0, 55.0, "偏低"),
    ("焊锡球直径(μm)", "SMT", 120.0, 60.0, "不合格"),
    ("极耳焊接拉力(N)", "PCM", 60.0, 20.0, "合格"),
    ("模组内阻(mΩ)", "PCM", 30.0, 5.0, "偏低"),
    ("保护板贴装偏移量(μm)", "PCM", 35.0, 0.0, "不合格"),
    ("汇流排焊接拉力(N)", "PACK", 55.0, 18.0, "合格"),
    ("模组气密泄漏率(%/min)", "PACK", 2.0, 0.0, "偏低"),
    ("线束插接位偏差(°)", "PACK", 3.0, 0.0, "不合格"),
    ("整机装配扭矩(N·m)", "整机", 5.0, 1.0, "合格"),
    ("老化后容量保持率(%)", "整机", 98.0, 88.0, "偏低"),
    ("终检电压偏差(V)", "整机", 0.5, -0.5, "不合格"),
)

DIGITS = 4
INGESTED_AT = "2026-10-16T08:00:00"


def _groups(rows: list[dict], scale: float, center: float) -> list[list[float]]:
    """按 subgroup_no 归组（子组容量恒为 5 → d2 常数 2.326）。"""
    buckets: dict[int, list[float]] = {}
    for r in rows:
        buckets.setdefault(r["subgroup_no"], []).append(
            round(center + scale * r["z"], DIGITS))
    return [buckets[k] for k in sorted(buckets)]


def _cpk(rows: list[dict], scale: float, center: float,
         usl: float, lsl: float) -> float | None:
    res = compute_capability(usl=usl, lsl=lsl,
                             subgroups=_groups(rows, scale, center),
                             characteristic="")
    return res.get("cpk")


def _calibrate(rows: list[dict], center: float, usl: float, lsl: float,
               target: float) -> float:
    """反标定 σ：cpk ≈ (usl-lsl)/(6·σ_within)；用真实内核迭代收敛到目标 cpk。

    标定**粒度由调用方决定**（这是 A′ 方案的关键，见模块 docstring）：
    - 既有 3 特性：按「整个 10-15 补数批次」（5 子组）标定 → 取值与历史基线一致；
    - 12 个新特性：按「(天 × 产品分类) 批次」（5 子组）标定 → 每个批次自身命中档位，
      组合口径（2 批 / 3 批）也不会漂出档位。

    迭代是**不动点**：Cpk(scale) = A/scale - B（A = h/(3σ̂_z)、B = |μ_z|/(3σ̂_z)），
    偏差按 ``B/T`` 收敛（实测 B/T ≈ 0.1）→ 12 次迭代足够命中 0.005 容差。
    """
    scale = (usl - lsl) / (6.0 * target)
    for _ in range(12):
        cpk = _cpk(rows, scale, center, usl, lsl)
        if not cpk:
            break
        if abs(cpk - target) < 0.005:
            break
        scale *= cpk / target
    return scale


def _legacy_rows(rng: random.Random) -> list[dict]:
    """既有 3 特性的维度补齐：10-15 补数（子组 6-10）+ 10-01~10-13 回填（子组 11 起）。

    回填与全月覆盖对齐——既有 3 特性此前只有 10-14（seed_v3）与 10-15（补数）两天，
    「最近 7 天 / 当月」口径的逐日表同样成片缺日（与 12 新特性同款缺陷）。
    每「天 × 特性」批次的 σ 仍按目标档位反标定（判定跨日期恒定）。
    """
    out: list[dict] = []
    for name, stage, cat, usl, lsl, target, start_min in LEGACY:
        center = (usl + lsl) / 2.0
        # ① 10-15 补数（保留原设计：5 子组，subgroup 6-10）
        rows = [{"subgroup_no": sg, "seq": seq,
                 "z": rng.normalvariate(0, 1)}
                for sg in range(LEGACY_SUBGROUP_BASE, LEGACY_SUBGROUP_BASE + 5)
                for seq in range(1, 6)]
        scale = _calibrate(rows, center, usl, lsl, target)
        for r in rows:
            minutes = start_min + (r["subgroup_no"] - LEGACY_SUBGROUP_BASE) * 10
            out.append({
                "characteristic": name, "subgroup_no": r["subgroup_no"],
                "seq": r["seq"],
                "value": round(center + scale * r["z"], DIGITS),
                "usl": usl, "lsl": lsl,
                "measured_at": f"2026-10-15 {minutes // 60:02d}:{minutes % 60:02d}:00",
                "process_stage": stage, "product_category": cat,
            })
        # ② 10-01 ~ 10-13 回填（每天 2 子组，subgroup 从 11 起逐日递增）
        for d_i, day in enumerate(LEGACY_BACKFILL_DAYS):
            batch: list[dict] = []
            for sg in range(1, DAILY_SUBGROUPS + 1):
                for seq in range(1, 6):
                    batch.append({
                        "subgroup_no": LEGACY_BACKFILL_BASE + d_i * DAILY_SUBGROUPS + (sg - 1),
                        "seq": seq,
                        "z": rng.normalvariate(0, 1),
                    })
            scale = _calibrate(batch, center, usl, lsl, target)
            for r in batch:
                sg_idx = r["subgroup_no"] - LEGACY_BACKFILL_BASE - d_i * DAILY_SUBGROUPS
                minutes = start_min + (d_i % 6) * 20 + sg_idx * 10
                out.append({
                    "characteristic": name, "subgroup_no": r["subgroup_no"],
                    "seq": r["seq"],
                    "value": round(center + scale * r["z"], DIGITS),
                    "usl": usl, "lsl": lsl,
                    "measured_at": f"{day} {minutes // 60:02d}:{minutes % 60:02d}:00",
                    "process_stage": stage, "product_category": cat,
                })
    return out


def _new_rows(rng: random.Random) -> list[dict]:
    """新特性：每工段 3 个 × 4 产品分类 × **当月 15 天（每天 2 子组）**。

    σ 标定粒度 = **(天 × 产品分类) 批次**（不是整个特性）：判定档位是在**筛选后的
    子集**上重算的——若整个特性共用一个 σ，Cpk 会在窗口切换时漂移掉档（实测，
    3 天 5 子组版曾有 48 组里 14 组掉档）。逐批次反标定既贴合「不同日/不同产品 =
    不同生产批」的物理事实，也让每个批次自身稳定命中目标档位。

    每天子组数从 5 降到 **2**（组容量仍恒为 5，与 d2 常数表匹配）：全月 15 天覆盖
    与 ``dashboard._SPC_MEAS_CAP = 8000`` 的行数红线不可兼得时，优先**日期完整性**
    （用户反馈：逐日表成片「—」看不出趋势）。单批 10 样本的 σ 精度由 ``_calibrate``
    比例迭代保证，48 组自检兜底。

    注意批次内的 ``subgroup_no`` 仍按 (天 × 分类) 唯一编号（``d_i*8 + c_i*2 + sg``）
    → 同一特性跨日期不混样，组容量恒为 5。
    """
    out: list[dict] = []
    for name, stage, usl, lsl, tier in NEW:
        center = (usl + lsl) / 2.0
        target = TIER_CPK[tier]
        for d_i, _day in enumerate(ALL_DAYS):
            for c_i, _cat in enumerate(CATS):
                # 一个批次 = 1 天 × 1 产品分类 × 2 子组 × 5 值（10 行）
                batch: list[dict] = []
                for sg in range(1, DAILY_SUBGROUPS + 1):
                    for seq in range(1, 6):
                        batch.append({
                            "sg": sg, "seq": seq,
                            # 同一特性内 (天 × 产品分类) 维度上 subgroup_no 唯一
                            "subgroup_no": d_i * len(CATS) * DAILY_SUBGROUPS
                                           + c_i * DAILY_SUBGROUPS + sg,
                            "z": rng.normalvariate(0, 1),
                        })
                scale = _calibrate(batch, center, usl, lsl, target)
                for r in batch:
                    minutes = ((8 + c_i) * 60 + 30 + (r["sg"] - 1) * 10
                               + (d_i % 3) * 15)
                    out.append({
                        "characteristic": name, "subgroup_no": r["subgroup_no"],
                        "seq": r["seq"],
                        "value": round(center + scale * r["z"], DIGITS),
                        "usl": usl, "lsl": lsl,
                        "measured_at": f"{ALL_DAYS[d_i]} "
                                       f"{minutes // 60:02d}:{minutes % 60:02d}:00",
                        "process_stage": stage,
                        "product_category": CATS[c_i],
                    })
    return out


def _num(v) -> str:
    """数值字面量（浮点统一保留有效位，整数不带小数点）。"""
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return repr(v) if isinstance(v, float) else str(v)


def _table_ddl() -> str:
    """本文件拥有的建表 / 建索引 DDL（单独抽出 → 自检与交付种子共用同一份，不漂移）。"""
    return "\n".join([
        "CREATE TABLE IF NOT EXISTS spc_measurement (",
        "    id INTEGER PRIMARY KEY AUTOINCREMENT,",
        "    characteristic VARCHAR(80) NOT NULL,",
        "    subgroup_no INTEGER NOT NULL,",
        "    seq INTEGER NOT NULL,",
        "    value REAL NOT NULL,",
        "    usl REAL,",
        "    lsl REAL,",
        "    measured_at VARCHAR(30),",
        "    source VARCHAR(40),",
        "    ingested_by VARCHAR(40),",
        "    ingested_at VARCHAR(30) NOT NULL,",
        "    process_stage TEXT,",
        "    product_category TEXT",
        ");",
        "CREATE INDEX IF NOT EXISTS idx_spc_measurement_char",
        "    ON spc_measurement (characteristic);",
    ]) + "\n"


def _sql_text(rows: list[dict]) -> str:
    """测量行 → 种子 SQL 全文（纯函数）。

    ``seg_end`` = 本文件 id 段的末 id（``SEED_ID_START + len(rows) - 1``）——
    守卫的「让位」与「删除」都以它为界，因此**必须在函数内按实际行数算**
    （写死常量会在行数变化时静默失配）。
    """
    seg_end = SEED_ID_START + len(rows) - 1
    head = [
        "-- SPC 过程能力维度种子（SPC 看板增量增强 · 数据层）—— 由一次性生成脚本产出，请勿手改。",
        "-- 为什么：spc_measurement 此前只有 75 行（3 特性 × 10-13/10-14），既无 process_stage",
        "--   也没有 product_category → 板级「工段 / 产品分类」筛选必然筛空；「当日」口径",
        "--   （2026-10-15）更是 0 行。",
        f"-- 规模：既有 3 特性在 10-15 各补一天（{len(LEGACY) * 25} 行，子组 6-10）"
        f"+ 10-01~10-13 回填（{len(LEGACY_BACKFILL_DAYS) * len(LEGACY) * DAILY_SUBGROUPS * 5} 行，子组 11 起）"
        f"+ 新增 {len(NEW)} 个特性（{len(STAGES)} 工段各 {len(NEW) // len(STAGES)} 个：合格/偏低/不合格各一）"
        f"× {len(CATS)} 产品分类 × {len(ALL_DAYS)} 天（10-01~10-15 全月）× {DAILY_SUBGROUPS} 子组 × 5 值"
        f"（{len(NEW) * len(CATS) * len(ALL_DAYS) * DAILY_SUBGROUPS * 5} 行）= {len(rows)} 行。",
        "-- 口径：判定档位由 core/spc.compute_capability 反标定 σ 得到（Cpk ≥1.33 合格 /",
        "--   1.00~1.33 偏低 / <1.00 不合格），保证任一「工段 × 产品分类 × 口径」组合下都",
        "--   有数据、**三档判定齐备**且判定可算；规格限为**演示规格限**（工艺侧待给定）。",
        "-- σ 粒度：按 **(天 × 产品分类) 批次**反标定（不是整特性一个 σ）—— 判定档位在",
        "--   筛选后的子集上重算，「当日」口径单组合只剩 5 个子组（σ 相对误差约 45%），",
        "--   整特性共用 σ 会让 Cpk 在 [0.60, 2.92] 漂移、48 组里 14 组掉档。",
        "-- 规格限：**同一特性跨日期恒定**（usl/lsl 是特性固有属性，取值与 data/seed_v3.sql",
        "--   完全一致）—— 规格限漂移会让换统计窗口即换判定（真实缺陷，见生成器自检）。",
        "-- 子组：每子组恒 5 个值（d2=2.326）；既有批次占 subgroup 1-5、10-15 补数占 6-10；",
        "--   新特性同一特性内 (天 × 产品分类) 维度上 subgroup_no 唯一 → 跨日期不混样，",
        "--   「当月」口径下组容量恒为 5（与 d2 常数表匹配，不产生静默 Cpk 失真）。",
        "-- 幂等：**先清空本文件拥有的行、再整批重灌**（守卫 + INSERT OR IGNORE + 显式 id）",
        f"--   → id 段 {SEED_ID_START}+ 归本文件所有，老库重放结果恒定。",
        "--   守卫的「本文件拥有的行」= **归属标记**（source='demo-seed' AND ingested_by='demo'）",
        "--   **且** id >= 4001 —— 归属 + id 下界缺一不可（详见守卫段注释）。",
        "--   id 段内若残留**用户数据**则**迁出 id 段**（不删）→ 用户数据不丢、id 段腾空。",
        "--   为什么必须有守卫：INSERT OR IGNORE 只保证「同 id 不重复插入」，不保证「同 id",
        "--   不残留旧值」→ 老库残留旧版种子时新行被静默跳过（实测少行 + 僵尸特性）。",
        "-- 原子性：守卫 + 老行回填 + 整批重灌 包在显式事务里 → 重灌中途失败不会",
        "--   留下「已删未插」的半拉子表（executescript 逐语句隐式提交，不加事务会清表）。",
        "-- ============================================================",
        "",
    ]
    head.extend(_table_ddl().rstrip("\n").split("\n"))
    head.extend([
        "",
        "-- 显式事务：DELETE 守卫 / 老行回填 / 整批重灌 原子生效。",
        "-- BEGIN IMMEDIATE（而非 BEGIN）：避免 SQLite 延迟事务的锁升级死锁 —— 读者持共享锁时，",
        "-- 写者从 BEGIN 升级到写锁会直接 BUSY（不等 busy_timeout），IMMEDIATE 则先取写锁再读。",
        "BEGIN IMMEDIATE;",
        "",
        "-- 重灌守卫：清空**本文件拥有的行**，再整批重灌。",
        "-- 「本文件拥有的行」= **归属标记**（source='demo-seed' AND ingested_by='demo'）",
        "--   **且** id >= 4001 —— 两个条件缺一不可（都是实测踩出来的）：",
        "--   * 归属标记：本文件与它的任何旧版本都写这个标记 → 旧版残留（僵尸特性）一并清掉。",
        "--   * id 下界：data/seed_v3.sql 的既有 75 行（id 1..75）**也**是",
        "--     source='demo-seed' AND ingested_by='demo' → 只按归属删会把既有基线一起删掉",
        "--     （真实 bug，2026-10-20 由守护测试当场抓到：既有 3 特性从 50 行掉到 25 行）。",
        "--     归属 + id 下界的**交集**才恰好是「本文件的行」。",
        "-- 让位而非删除：id 段内若残留**非种子行**（用户数据），先把它迁到 id 段之外",
        "--   → 用户数据**不丢**，同时腾出本文件的 id 段（否则显式 id 撞号 → 静默跳过 → 少行）。",
        "--   只迁「落在本文件 id 段内」的行（段外的用户行与本文件无关 → 不动，避免无谓改 id）。",
        "--   迁出基线 = MAX(表内最大 id, 本文件 id 段末)：只取「表内最大 id」时，老库残留",
        "--   旧种子（最大 id 5275）会把用户行迁到 5276 → **仍落在本文件 id 段内** → 照样撞号。",
        "--   逐行 UPDATE（不能用 `SET id = (SELECT MAX(id)+1 ...)` 一次性更新：多行会拿到",
        "--   **同一个**新 id → UNIQUE 约束冲突，实测报错）→ 用 rowid 排序后的行号做偏移。",
        "CREATE TEMP TABLE _spc_relocate (old_id INTEGER PRIMARY KEY, new_id INTEGER);",
        "INSERT INTO _spc_relocate (old_id, new_id)",
        " SELECT id, MAX((SELECT COALESCE(MAX(id), 0) FROM spc_measurement), "
        + str(seg_end) + ")",
        "        + ROW_NUMBER() OVER (ORDER BY id)",
        "   FROM spc_measurement",
        "  WHERE id >= " + str(SEED_ID_START),
        "    AND id <= " + str(seg_end),
        "    AND NOT (COALESCE(source, '') = 'demo-seed'",
        "             AND COALESCE(ingested_by, '') = 'demo');",
        "UPDATE spc_measurement",
        "   SET id = (SELECT new_id FROM _spc_relocate WHERE old_id = spc_measurement.id)",
        " WHERE id IN (SELECT old_id FROM _spc_relocate);",
        "DROP TABLE _spc_relocate;",
        "DELETE FROM spc_measurement",
        " WHERE id >= " + str(SEED_ID_START),
        "   AND COALESCE(source, '') = 'demo-seed'",
        "   AND COALESCE(ingested_by, '') = 'demo';",
        "",
        "-- 老库回填：既有 75 行按种子注释的产线映射补两列（老库无此两列，只补不改其它字段）。",
        "-- 工段：SMT-1 → SMT、DIP-1 → PCM；产品分类：SMT-1 → 消费电子、DIP-1 → 智能电源。",
        "-- 带 process_stage IS NULL 守卫：不命中即不写，重复执行结果恒定（幂等）。",
        "UPDATE spc_measurement SET process_stage = 'SMT', product_category = '消费电子'",
        " WHERE process_stage IS NULL",
        "   AND characteristic IN ('焊点高度(mm)', '锡膏厚度(μm)');",
        "UPDATE spc_measurement SET process_stage = 'PCM', product_category = '智能电源'",
        " WHERE process_stage IS NULL",
        "   AND characteristic = '回流焊峰值温度(℃)';",
        "",
        "",
        "INSERT OR IGNORE INTO spc_measurement (id, characteristic, subgroup_no, seq, value,",
        "  usl, lsl, measured_at, source, ingested_by, ingested_at, process_stage, product_category)",
        " VALUES",
    ])
    lines: list[str] = []
    for i, r in enumerate(rows):
        tail = "," if i < len(rows) - 1 else ";"
        lines.append(
            f"({SEED_ID_START + i}, '{r['characteristic']}', {r['subgroup_no']}, "
            f"{r['seq']}, {_num(r['value'])}, {_num(r['usl'])}, {_num(r['lsl'])}, "
            f"'{r['measured_at']}', 'demo-seed', 'demo', '{INGESTED_AT}', "
            f"'{r['process_stage']}', '{r['product_category']}'){tail}")
    # AUTOINCREMENT 计数器（sqlite_sequence）对齐：让位把用户行迁到了 id 段之外，但
    # UPDATE 改 id **不会**推进计数器（实测 seq 停在旧值）→ 后续用户 ingest 拿到的自增
    # id 会与「已让位」的行撞号 → UNIQUE 约束失败 → 用户写不进数据（真实隐患）。
    # 手法：插一个**锚点行**（id = MAX(id)+1）把计数器顶上去，再把锚点删掉
    # （DELETE 不会让计数器回退，实测）→ 计数器停在 MAX(id)+1，后续自增不撞号。
    # 锚点用 source='demo-seed' + ingested_by='demo' → 下一轮守卫会顺手清掉，
    # 即使本次执行中途异常也不会留下脏行（整段在显式事务里，异常即回滚）。
    lines.extend([
        "INSERT OR IGNORE INTO spc_measurement (id, characteristic, subgroup_no, seq, value,",
        "  usl, lsl, measured_at, source, ingested_by, ingested_at, process_stage, product_category)",
        " VALUES ((SELECT COALESCE(MAX(id), 0) + 1 FROM spc_measurement),",
        "  '__spc_seq_anchor__', 1, 1, 0, 0, 0, '2026-10-15 00:00:00',",
        "  'demo-seed', 'demo', '" + INGESTED_AT + "', NULL, NULL);",
        "DELETE FROM spc_measurement",
        " WHERE id = (SELECT MAX(id) FROM spc_measurement)",
        "   AND characteristic = '__spc_seq_anchor__';",
    ])
    # 显式事务闭合：与文件头的 BEGIN IMMEDIATE 配对 → 整批重灌原子生效。
    lines.append("COMMIT;")
    return "\n".join(head + lines) + "\n"


def _verdict_of(rows: list[dict]) -> tuple[str, float | None]:
    """一组测量行 → （判定, cpk）。"""
    groups: dict[int, list[float]] = {}
    for r in rows:
        groups.setdefault(r["subgroup_no"], []).append(r["value"])
    res = compute_capability(usl=rows[0]["usl"], lsl=rows[0]["lsl"],
                             subgroups=[groups[k] for k in sorted(groups)],
                             characteristic=rows[0]["characteristic"])
    return res.get("verdict") or "", res.get("cpk")


def _check_spec_and_subgroup_consistency(rows: list[dict]) -> int:
    """自检（QA 建议，2026-10-20）：① 同特性规格限必须唯一；② 同 (特性, subgroup_no) 不得跨日期。

    为什么必须守卫（真实缺陷，曾在演示库造成**静默** Cpk 失真）：
    - **规格限漂移**：``dashboard._spc_caps_from_rows`` 取该特性**第一行**的 usl/lsl →
      同一特性在不同日期写不同规格限，会让**换统计窗口即换判定**（如锡膏厚度
      10-13 写 150/90、10-15 写 160/120 → 最近7天 1.146「偏低」→ 当日 0.877「不合格」）。
    - **subgroup_no 跨日期复用**：多天并进同一子组 → 组容量变 10，而
      ``core/spc.compute_capability`` 的 d2 常数表按 **n=5→2.326** 取值
      （n=10 应对应 3.078）→ σ_within 失真 → **Cpk 静默偏移**；且各组容量整齐都是 10
      → 内核「剔除非众数子组」告警**不触发** → 用户与前端都看不到任何提示。

    生成时立即断言（而不是等 QA 复跑才发现）→ 重跑生成器即自曝。
    """
    bad = 0
    specs: dict[str, set[tuple[float, float]]] = {}
    seen: dict[tuple[str, int], set[str]] = {}
    for r in rows:
        specs.setdefault(r["characteristic"], set()).add((r["usl"], r["lsl"]))
        seen.setdefault((r["characteristic"], r["subgroup_no"]), set()).add(
            r["measured_at"][:10])
    for name, vals in sorted(specs.items()):
        if len(vals) > 1:
            bad += 1
            print(f"  !! 规格限漂移：{name} 出现多个组合 {sorted(vals)}"
                  f" → 换口径会翻转判定（_spc_caps_from_rows 取首行规格限）")
    for (name, gno), days in sorted(seen.items()):
        if len(days) > 1:
            bad += 1
            print(f"  !! subgroup_no 跨日期复用：({name}, {gno}) 出现在 {sorted(days)}"
                  f" → 当月口径会把多天并成同一子组，组容量与 d2 常数表失配（Cpk 静默失真）")
    print(f"[gen] 规格限唯一 + subgroup 不跨日期自检："
          f"{'全部通过' if not bad else f'{bad} 处不通过'}")
    return bad


#: 判定档位全集（三档齐备的判据；与 ``core/spc`` 的判定线同源）
TIERS: tuple[str, ...] = ("合格", "偏低", "不合格")

#: 三档时间口径（起止与 ``dashboard.WINDOWS`` 同源：最近7天 / 当日 / 当月）
WINDOW_RANGES: dict[str, tuple[str, str]] = {
    "window": ("2026-10-08", "2026-10-14"),
    "day": ("2026-10-15", "2026-10-15"),
    "month": ("2026-10-01", "2026-10-15"),
}


def _self_check(rows: list[dict]) -> int:
    """自检（fail-fast）：**48 组**（4 工段 × 4 产品分类 × 3 口径）逐组校验
    ① 有数据 ② **三档判定齐备** ③ 无「不可判定」；另附既有最差特性的越限率体检。

    A′ 裁决后本自检是**硬门禁**：任一组不过 → 返回非零 → ``main()`` 非零退出
    且**不落盘**坏种子（契约 §10 A8「三档判定在筛选后仍各自可见」的种子侧兑现）。

    另：既有 3 特性的 Cpk（1.461 / 1.146 / 0.615）必须仍是全库最差的一档，
    否则既有下钻排名断言（最差特性 = 回流焊峰值温度）会被新特性抢走 —— 打印体检值。
    """
    bad = 0
    for stage in STAGES:
        for cat in CATS:
            for wname, (start, end) in WINDOW_RANGES.items():
                sub = [r for r in rows
                       if r["process_stage"] == stage
                       and r["product_category"] == cat
                       and start <= r["measured_at"][:10] <= end]
                verdicts: set[str] = set()
                for name in {r["characteristic"] for r in sub}:
                    one = [r for r in sub if r["characteristic"] == name]
                    verdicts.add(_verdict_of(one)[0])
                has_data = bool(sub)
                all_three = set(TIERS) <= verdicts
                decidable = "不可判定" not in verdicts
                ok = has_data and all_three and decidable
                if not ok:
                    bad += 1
                    print(f"  !! {stage}/{cat}/{wname}: 行数={len(sub)} 判定={sorted(verdicts)}"
                          f"（有数据={has_data} 三档齐备={all_three} 可判定={decidable}）")
    print(f"[gen] 组合自检（{len(STAGES)} 工段 × {len(CATS)} 分类 × {len(WINDOW_RANGES)} 口径"
          f" = {len(STAGES) * len(CATS) * len(WINDOW_RANGES)} 组 · "
          f"有数据 + 三档齐备 + 无不可判定）："
          f"{'全部通过' if not bad else f'{bad} 处不通过'}")

    # 既有 3 特性的 Cpk（1.461 / 1.146 / 0.615）必须仍是全库最差的一档，
    # 否则既有下钻排名断言（最差特性 = 回流焊峰值温度）会被新特性抢走。
    worst = 0.0
    for name in {r["characteristic"] for r in rows}:
        one = [r for r in rows if r["characteristic"] == name]
        num = sum(1 for r in one
                  if (r["usl"] is not None and r["value"] > r["usl"])
                  or (r["lsl"] is not None and r["value"] < r["lsl"]))
        worst = max(worst, 100.0 * num / len(one))
    print(f"[gen] 全量最差越限率：{worst:.1f}%")
    return bad


def _self_check_rebuild(rows: list[dict]) -> int:
    """自检（fail-fast）：**老库重放 → 干净重建**（设计 §2.1.4 A′ 交付纪律①）。

    为什么必须守卫（真实缺陷，工程师 2026-10-20 实测复现）：
    ``INSERT OR IGNORE`` + 显式 id 只保证「同 id 不重复插入」，**不保证「同 id 不残留
    旧值」**。老库若已应用过**旧版**本文件（id 段 4001-5275、特性名与规模都不同），
    重灌新种子时旧行占着同 id → 新行被 ``INSERT OR IGNORE`` **静默跳过** →
    结果 = 旧版「僵尸特性」残留 + 新特性少行（实测「元件贴装偏移量(μm)」只有 275/300 行）。

    本自检在临时库里造一个「老库」（旧版 id 段的僵尸行 + 受治理运行时行），
    再重放本文件产出的 SQL，断言：
    ① 僵尸特性被清干净、每个特性行数与目标一致；
    ② **受治理运行时行不受影响**（守卫不得越界删用户数据）；
    ③ 重放两次结果恒定（幂等）；
    ④ **既有基线行（id < 4001，同样写 demo-seed/demo）不被误删**（真实 bug：
       守卫只按归属删时会把 data/seed_v3.sql 的 75 行一起删掉 → 既有 3 特性
       从 50 行掉到 25 行，守护测试当场抓到）；
    ⑤ **AUTOINCREMENT 计数器已推进**（sqlite_sequence ≥ 让位后的最大 id）→
       后续用户 ingest 的自增 id 不会与被让位的行撞号（UNIQUE 约束失败 → 用户写不进数据）。
    """
    import sqlite3
    import tempfile

    bad = 0
    expected: dict[str, int] = {}
    for r in rows:
        expected[r["characteristic"]] = expected.get(r["characteristic"], 0) + 1
    seed_id_end = SEED_ID_START + len(rows) - 1

    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "rebuild_check.db"
        conn = sqlite3.connect(str(db_path))
        try:
            # 1) 先建表（模拟老库：表已存在，列已补齐）
            conn.executescript(_table_ddl())
            # 2) 造「老库」残留 —— 必须在重灌**之前**写入，才模拟真实老库状态：
            #    a) 旧版种子写下的僵尸行（id 落在本文件 id 段内 → 与新行撞号）
            conn.execute(
                "INSERT INTO spc_measurement (id, characteristic, subgroup_no, seq, value,"
                " usl, lsl, measured_at, source, ingested_by, ingested_at)"
                " VALUES (?, '僵尸特性(μm)', 1, 1, 30.0, 40.0, 0.0,"
                " '2026-10-10 08:00:00', 'demo-seed', 'demo', '2026-10-16T08:00:00')",
                (SEED_ID_START,))
            #    b) 旧版种子超出本段上限的残留（id > 本段末 id）
            conn.execute(
                "INSERT INTO spc_measurement (id, characteristic, subgroup_no, seq, value,"
                " usl, lsl, measured_at, source, ingested_by, ingested_at)"
                " VALUES (?, '旧版残留(μm)', 1, 1, 30.0, 40.0, 0.0,"
                " '2026-10-10 08:00:00', 'demo-seed', 'demo', '2026-10-16T08:00:00')",
                (seed_id_end + 1,))
            #    c) 运行时受治理写入的行（ingested_by=用户 id）→ 守卫不得动它
            conn.execute(
                "INSERT INTO spc_measurement (id, characteristic, subgroup_no, seq, value,"
                " usl, lsl, measured_at, source, ingested_by, ingested_at)"
                " VALUES (?, '运行时写入(μm)', 1, 1, 30.0, 40.0, 0.0,"
                " '2026-10-15 09:00:00', '设备', 'u_alice', '2026-10-16T09:00:00')",
                (seed_id_end + 2,))
            #    d) **最容易误删的场景**（2026-10-20 实测发现）：老库残留旧版种子
            #       （最大 id 5275）时，用户 ingest 的一行会被 AUTOINCREMENT 分配
            #       5276 → **落进本文件的 id 段内**（4001-7675）→ 若守卫按 id 段删，
            #       这行**用户数据**会被误删。守卫必须让它**让位**（迁出 id 段）而非删除。
            conn.execute(
                "INSERT INTO spc_measurement (id, characteristic, subgroup_no, seq, value,"
                " usl, lsl, measured_at, source, ingested_by, ingested_at)"
                " VALUES (?, 'id段内用户行(μm)', 1, 1, 30.0, 40.0, 0.0,"
                " '2026-10-15 10:00:00', '设备', 'u_bob', '2026-10-16T10:00:00')",
                (SEED_ID_START + 10,))
            #    e) **既有基线行**（data/seed_v3.sql 的 75 行形态：id < 4001，且**同样**写
            #       source='demo-seed' AND ingested_by='demo'）→ 守卫**绝不能**删它
            #       （真实 bug：只按归属删会把既有基线一起删掉，既有 3 特性 50 → 25 行）。
            conn.execute(
                "INSERT INTO spc_measurement (id, characteristic, subgroup_no, seq, value,"
                " usl, lsl, measured_at, source, ingested_by, ingested_at)"
                " VALUES (?, '既有基线(μm)', 1, 1, 30.0, 40.0, 0.0,"
                " '2026-10-13 08:30:00', 'demo-seed', 'demo', '2026-10-14T09:00:00')",
                (SEED_ID_START - 1,))
            conn.commit()

            # 3) 重放本文件（= orchestrator._apply_seed 的执行方式）
            conn.executescript(_sql_text(rows))
            conn.executescript(_sql_text(rows))  # 幂等：重放两次结果恒定

            got: dict[str, int] = {}
            for name, cnt in conn.execute(
                    "SELECT characteristic, COUNT(*) FROM spc_measurement GROUP BY 1"):
                got[name] = cnt
            for name, want in sorted(expected.items()):
                if got.get(name) != want:
                    bad += 1
                    print(f"  !! 重建后特性 {name} 行数 {got.get(name)} ≠ 期望 {want}"
                          "（老库残留未被清干净 → 重灌被静默跳过）")
            for zombie in ("僵尸特性(μm)", "旧版残留(μm)"):
                if zombie in got:
                    bad += 1
                    print(f"  !! 重建后仍残留僵尸特性 {zombie}（DELETE 守卫未生效）")
            if got.get("运行时写入(μm)") != 1:
                bad += 1
                print("  !! 重建把运行时受治理写入的行删掉了（守卫越界）")
            if got.get("既有基线(μm)") != 1:
                bad += 1
                print("  !! 重建把既有基线行（id < 4001 的 demo-seed/demo 行）删掉了"
                      " —— 守卫必须带 id 下界，归属标记单用会误删 data/seed_v3.sql 的 75 行")
            if got.get("id段内用户行(μm)") != 1:
                bad += 1
                print("  !! 重建把「id 段内的用户行」删掉了（守卫越界）"
                      "—— 应让它让位（迁出 id 段），而不是删除")
            else:
                # 让位校验：它必须被迁到本文件的 id 段之外，且内容不变
                moved = conn.execute(
                    "SELECT id, value, source, ingested_by FROM spc_measurement"
                    " WHERE characteristic = 'id段内用户行(μm)'").fetchone()
                if moved[0] <= SEED_ID_START + len(rows) - 1:
                    bad += 1
                    print(f"  !! id 段内的用户行未被让位（id={moved[0]} 仍在 id 段内）"
                          "→ 会与本文件的显式 id 撞号 → 重灌被静默跳过")
                if (moved[1], moved[2], moved[3]) != (30.0, "设备", "u_bob"):
                    bad += 1
                    print(f"  !! id 段内的用户行内容被改动：{moved}（让位必须只改 id）")
            # 锚点行必须已被清掉（否则它会混进看板统计）
            if got.get("__spc_seq_anchor__"):
                bad += 1
                print("  !! 锚点行残留在表里（推进 AUTOINCREMENT 计数器的临时行没删干净）")
            # AUTOINCREMENT 计数器（sqlite_sequence）必须 ≥ 让位后的最大 id，
            # 否则后续用户 ingest 的自增 id 会与被让位的行撞号（UNIQUE 约束失败）。
            max_id = conn.execute("SELECT COALESCE(MAX(id), 0) FROM spc_measurement").fetchone()[0]
            seq = conn.execute(
                "SELECT seq FROM sqlite_sequence WHERE name = 'spc_measurement'").fetchone()
            if not seq or seq[0] < max_id:
                bad += 1
                print(f"  !! sqlite_sequence 未推进（seq={seq[0] if seq else None} < "
                      f"MAX(id)={max_id}）→ 后续用户 ingest 的自增 id 会撞号"
                      "（UNIQUE 约束失败 → 用户写不进数据）")
        finally:
            conn.close()
    print(f"[gen] 老库重放 → 干净重建自检（僵尸清除 + 运行时数据不伤 + 既有基线不伤"
          f" + 计数器推进 + 幂等）：{'全部通过' if not bad else f'{bad} 处不通过'}")
    return bad


def build_rows(seed: int = RNG_SEED) -> list[dict]:
    """确定性拼装全部测量行（既有 3 特性的 10-15 补数 + 12 个新特性）。

    纯函数：同一 ``seed`` 永远产出同一批行 → 交付种子可字节级复现（守护测试依赖）。
    """
    rng = random.Random(seed)
    return _legacy_rows(rng) + _new_rows(rng)


def build_sql_text(rows: list[dict]) -> str:
    """测量行 → 种子 SQL 全文（纯函数，不落盘）。"""
    return _sql_text(rows)


def main(output_path: Path | None = None) -> None:
    """生成种子 SQL：先自检、后落盘（fail-fast —— **不产出坏种子**）。

    ``output_path`` 缺省写到 ``data/seed_spc_dimensions.sql``；传参供守护测试在
    临时目录里复现（**不得**用它覆盖仓库里的交付种子）。
    """
    target = Path(output_path) if output_path else OUT
    rows = build_rows()
    # 先自检：不通过就**不落盘**（坏种子绝不进仓库）
    bad = _self_check(rows)
    # 规格限 / subgroup 一致性守卫（QA 建议）：生成即自曝，不等 QA 复跑
    bad += _check_spec_and_subgroup_consistency(rows)
    # 干净重建守卫（设计 §2.1.4 A′ 交付纪律①）：老库重放必须得到与新库一致的结果
    bad += _self_check_rebuild(rows)
    if bad:
        print(f"[gen] 自检未通过（{bad} 处）→ **不落盘**，非零退出。")
        raise SystemExit(1)
    # 统一 LF 落盘（显式 bytes，避免 Windows 文本模式的 CRLF 转换）→ 跨平台字节一致
    target.write_bytes(build_sql_text(rows).encode("utf-8"))
    days = sorted({r["measured_at"][:10] for r in rows})
    print(f"[gen] {target.name}: {len(rows)} 行（id {SEED_ID_START}~{SEED_ID_START + len(rows) - 1}）")
    print(f"[gen] 日期覆盖：{days}")
    print(f"[gen] 特性数：{len({r['characteristic'] for r in rows})}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else None)
