"""修复 SPC 种子数据的 P0-1 缺陷（规格限漂移 + subgroup_no 跨日期复用）。

为什么需要（真实缺陷，工程师 2026-10-20 报出、QA 独立复现并写成守卫断言）：
1. **规格限漂移**：``data/seed_spc_dimensions.sql`` 里既有 3 特性在 2026-10-15 的补数行
   把 ``usl`` / ``lsl`` 改成了与 ``data/seed_v3.sql`` 不同的值（锡膏厚度 160/120 vs
   150/90；回流焊峰值温度 250/235 vs 255/235）。而 ``dashboard._spc_caps_from_rows``
   取的是该特性**第一行**的规格限 → **换统计窗口就换判定**（锡膏厚度：最近7天
   Cpk=1.146「偏低」→ 当日 0.877「不合格」）。规格限是**特性属性**，不随日期变。
2. **subgroup_no 跨日期复用**：10-15 补数复用了 10-13/10-14 已占用的 1..5
   → 「当月」口径把多天并进同一子组，组容量变 10，而 ``core/spc.compute_capability``
   的 d2 常数表按 **n=5→2.326** 取值（n=10 应对应 3.078）→ σ_within 失真 → Cpk 静默偏移。
   且各组容量整齐都是 10 → 内核「剔除非众数子组」告警**不触发** → 静默失真。

本脚本做什么（**只改这 75 行**）：
- 规格限回归 ``data/seed_v3.sql`` 的真相源（不动取值 value / 时间 / 维度列）；
- 10-15 补数的 ``subgroup_no`` 改 6..10（与既有批次 1..5 错开，两批不混样）。

**不破坏**设计 §2.1.5 的「既有 3 特性逐字段不变」：最近 7 天窗口（10-08~10-14）不含
10-15 → 该窗口的取数与判定完全不受本次修改影响。

幂等：脚本可重复执行（第二次起无任何变更）；``INSERT OR IGNORE`` + 显式 id 保证重放安全。

用法：python tools/fix_spc_seed_spec_limits.py [--check]
"""
from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SEED_V3 = ROOT / "data" / "seed_v3.sql"
SEED_SPC = ROOT / "data" / "seed_spc_dimensions.sql"

#: 既有 3 特性：10-15 补数的 subgroup_no 起点（既有批次占 1..5 → 补数从 6 起，两批不混样）
LEGACY_SUBGROUP_BASE = 6

_ROW_RE = re.compile(
    r"\(\s*(?:\d+\s*,\s*)?'([^']+)'\s*,\s*(\d+)\s*,\s*(\d+)\s*,"
    r"\s*[-0-9.]+\s*,\s*([0-9.]+)\s*,\s*([0-9.]+)\s*,\s*'([^']+)'", re.M)

_INSERT_RE = re.compile(
    r"INSERT\s+(?:OR\s+IGNORE\s+)?INTO\s+spc_measurement\b.*?;", re.I | re.S)


def _legacy_rows(text: str) -> list[tuple[str, int, int, str, str, str]]:
    """解析一个种子文件里 spc_measurement 的 (特性, subgroup_no, seq, usl, lsl, 时间)。"""
    out: list[tuple[str, int, int, str, str, str]] = []
    for m in _INSERT_RE.finditer(text):
        for name, gno, seq, usl, lsl, ts in _ROW_RE.findall(m.group(0)):
            out.append((name, int(gno), int(seq), usl, lsl, ts))
    return out


def truth_spec_limits() -> dict[str, tuple[str, str]]:
    """从 ``data/seed_v3.sql``（既有 3 特性原始行）取规格限真相源。"""
    truth: dict[str, set[tuple[str, str]]] = {}
    for name, _gno, _seq, usl, lsl, _ts in _legacy_rows(SEED_V3.read_text(encoding="utf-8")):
        truth.setdefault(name, set()).add((usl, lsl))
    out: dict[str, tuple[str, str]] = {}
    for name, vals in truth.items():
        if len(vals) != 1:
            raise SystemExit(f"[fix] 真相源自身不唯一：{name} → {sorted(vals)}")
        out[name] = next(iter(vals))
    if not out:
        raise SystemExit("[fix] 未从 seed_v3.sql 解析到任何既有测量行")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="修复 SPC 种子 P0-1 缺陷")
    ap.add_argument("--check", action="store_true", help="只检查不写盘")
    args = ap.parse_args()

    truth = truth_spec_limits()
    print(f"[fix] 规格限真相源（seed_v3.sql）：{truth}")

    text = SEED_SPC.read_text(encoding="utf-8")
    before = text

    # 既有 3 特性在 seed_spc_dimensions.sql 里的行：规格限回归真相源 + subgroup_no 改 6..10
    target_rows = {name for name in truth}
    # 先收集这些行在文件里的 (特性 → 该特性出现顺序内的子组序号)，用于换算新 subgroup_no
    seen_gno: dict[str, set[int]] = {}
    edits: list[tuple[int, int, str]] = []  # (start, end, 新元组文本)

    # 逐条替换 INSERT 段内的元组
    def _fix_insert(match: re.Match[str]) -> str:
        seg = match.group(0)
        out_lines: list[str] = []
        for line in seg.split("\n"):
            stripped = line.strip()
            if not stripped.startswith("("):
                out_lines.append(line)
                continue
            m = _ROW_RE.search(line)
            if not m:
                out_lines.append(line)
                continue
            name = m.group(1)
            if name not in target_rows:
                out_lines.append(line)
                continue
            gno = int(m.group(2))
            usl, lsl = m.group(4), m.group(5)
            ts = m.group(6)
            new_usl, new_lsl = truth[name]
            # 幂等：已处于目标态（规格限 == 真相源 且 subgroup_no 已在 6..10 段）→ 原样保留。
            # 注意：**不能**按 (特性, subgroup_no) 去重跳过 —— 同一子组的 5 个 seq 行
            # 共享同一个 subgroup_no，按子组去重会漏改其中 4 行。
            if usl == new_usl and lsl == new_lsl and gno >= LEGACY_SUBGROUP_BASE:
                out_lines.append(line)
                continue
            # 规格限漂移 → 回归真相源（真相源取自 seed_v3.sql）
            # 同时把 subgroup_no 平移到 6..10 段：既有批次已占用 1..5，
            # 补数改 6..10 → 两批不混样，「当月」口径下组容量恒为 5（与 d2=n=5 匹配）
            new_gno = LEGACY_SUBGROUP_BASE + (gno - 1)
            new_line, n_sub = re.subn(
                rf"('{re.escape(name)}',\s*){gno}(\s*,\s*)",
                rf"\g<1>{new_gno}\g<2>", line, count=1)
            if n_sub != 1:
                raise SystemExit(f"[fix] subgroup_no 替换失败：{line.strip()[:80]}")
            new_line, n_spec = re.subn(
                rf"(\s*,\s*){re.escape(usl)}(\s*,\s*){re.escape(lsl)}(\s*,\s*'{re.escape(ts)}')",
                rf"\g<1>{new_usl}\g<2>{new_lsl}\g<3>", new_line, count=1)
            if n_spec != 1:
                raise SystemExit(f"[fix] 规格限替换失败：{line.strip()[:80]}")
            out_lines.append(new_line)
        return "\n".join(out_lines)

    text = _INSERT_RE.sub(_fix_insert, text)

    if text == before:
        print("[fix] 无需修改（已是目标状态，幂等）")
        return 0

    if args.check:
        print("[fix] --check 模式：检测到差异，未写盘")
        return 1

    backup = SEED_SPC.with_suffix(".sql.bak")
    shutil.copy2(SEED_SPC, backup)
    SEED_SPC.write_text(text, encoding="utf-8")
    print(f"[fix] 已写 {SEED_SPC.name}（备份：{backup.name}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
