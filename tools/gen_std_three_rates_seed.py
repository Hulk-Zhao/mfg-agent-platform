"""标准三率（工段 × 班组）种子生成器：产出 ``data/seed_std_three_rates.sql``。

为什么要有这个脚本：
- 标准三率此前只有 10 条审核记录（A/B/C 型号 × 2 周），既无工段也无班组，
  看板只能按型号列一张表，做不出「1 级工段 / 2 级班组」两级看板。
- 数据按真实工艺写死（4 工段 × 5 道工序 × 15 个班组），脚本只做**确定性拼装**：
  同一输入永远产出同一份 SQL（无随机、无占位文案），便于评审与复现。

口径（与 ``v2/services/dashboard.py::_three_rates`` 同源）：
- 完整率 = 有标准文件（doc_present=1）的审核行 ÷ 审核行总数；
- 完善率 = 标准完善（doc_complete=1）的审核行 ÷ 有标准文件的审核行；
- 遵守率 = Σaudit_conform ÷ Σaudit_total；
- 符合率 = 完整率 × 完善率 × 遵守率。
每行 = 某班组某天对某道工序标准的审核记录，故「行数」即「审核项数」。

幂等：id ≥ 2001 的区间**归本文件所有**——装载时先 ``DELETE`` 该区间再整批插入，
老库能从 3 班组/8 工序的旧网格自动对齐到当前网格；id 101–110 的历史行只做回填不动。

用法：python tools/gen_std_three_rates_seed.py
"""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "data" / "seed_std_three_rates.sql"
OUT_CAUSE = Path(__file__).resolve().parents[1] / "data" / "seed_std_team_cause.sql"
SEED_ID_START = 2001
#: 原因表的 id 区间（与审核明细分开，互不覆盖）
CAUSE_ID_START = 4001

#: 每个工段的班组数（1 级看板点进去 = 15 行班组明细）
N_TEAMS = 15

#: 数据起始日 ~ 演示「今天」（dashboard.DEMO_TODAY = 2026-10-15）。
#: 覆盖「当月」（10-01~10-15）与「最近 7 天」（10-08~10-14）两个板级口径。
DATE_FROM = date(2026, 10, 1)
DATE_TO = date(2026, 10, 15)

#: 4 工段 · 线体 · 工序（与 quality_defect 的工段/线体同源）
#: 每工段 5 道工序标准：15 个班组下，单日单工段样本 = 15×5 = 75 行，
#: 完整率台阶已足够细（1.3%）；工序再多只是把 SQL 撑大，不增加信息量。
STAGES: list[tuple[str, str, list[str], tuple[str, str]]] = [
    ("SMT", "SMT-3",
     ["锡膏印刷", "贴片", "回流焊", "AOI", "首件确认"],
     ("BT-48V20", "BT-60V30")),
    ("PCM", "PCM-1",
     ["分容配组", "极耳焊接", "保护板贴装", "电性能测试", "内阻检测"],
     ("PCM-13S", "PCM-16S")),
    ("PACK", "PACK-1",
     ["电芯装配", "汇流排焊接", "线束连接", "气密测试", "模组入箱"],
     ("PACK-4820", "PACK-6030")),
    ("整机", "ASSY-1",
     ["整机装配", "老化", "功能测试", "包装", "终检"],
     ("ESS-5K", "ESS-10K")),
]

#: 工段 → 班组能力曲线（最好班组, 最差班组）=(完整率%, 完善率%, 遵守率%)
#: 15 个班组在两端之间线性插值，形成"同一工段内部的班组梯队"：
#: PCM段整体最差（标准缺失 + 现场不遵守），整机段最优，SMT/PACK 居中。
TEAM_CURVE: dict[str, tuple[tuple[float, float, float], tuple[float, float, float]]] = {
    "SMT": ((100.0, 98.0, 97.0), (98.0, 91.0, 90.0)),
    "PCM": ((100.0, 96.0, 95.0), (78.0, 70.0, 80.0)),
    "PACK": ((100.0, 97.0, 94.0), (96.0, 89.0, 90.0)),
    "整机": ((100.0, 99.0, 98.0), (96.0, 94.0, 94.0)),
}

#: 工序 → 审核项数（不同工段/工序的抽样强度不同，避免数据"太整齐"）
AUDIT_TOTAL = [20, 22, 18, 24, 16]

#: 三率维度 → (中文名, 不符合计量单位)。原因必须按维度归因：
#: 完整率差 = 标准缺失；完善率差 = 标准与现场不一致；遵守率差 = 现场没按标准做。
#: 只按这三类归因，**不符合数才与主表逐行对得上**（可加总验证，不是编故事）。
RATE_DIMS: list[tuple[str, str, str]] = [
    ("completeness", "标准完整率", "行"),
    ("perfection", "标准完善率", "行"),
    ("compliance", "标准遵守率", "项"),
]

#: 维度 → 具体原因话术（每维度两条，按班组的确定比例分摊，形成"主因 / 次因"）
CAUSE_LIB: dict[str, list[str]] = {
    "completeness": [
        "作业指导书未建档 / 未受控发放",
        "标准换版后旧版未回收、新版未下发到线边",
    ],
    "perfection": [
        "标准参数与设备实际能力不符，现场凭经验执行",
        "检验频次 / 抽样量与产线节拍冲突，标准无法照做",
    ],
    "compliance": [
        "首件确认漏项、点检记录事后补填",
        "参数私自调整后未回归标准（换型未复位）",
    ],
}

#: 主因占比（按班组名次轮换，避免 60 个班组原因构成完全一样）
CAUSE_SPLIT = [0.62, 0.50, 0.72, 0.44]

#: 工段 → 该段真实会出的不良类型（只对「遵守率」维度挂不良，另两维挂不了产品不良）
STAGE_DEFECT: dict[str, list[str]] = {
    "SMT": ["连锡", "元件偏移", "虚焊"],
    "PCM": ["极耳虚焊", "内阻超差", "容量不达标"],
    "PACK": ["汇流排虚焊", "气密超差", "线束错接"],
    "整机": ["外观划伤", "功能测试不过", "包装破损"],
}

_CN_DIGIT = "一二三四五六七八九十"


def cn_num(n: int) -> str:
    """1–99 → 中文数字（班组命名用：一班 … 十五班）。"""
    if n <= 10:
        return _CN_DIGIT[n - 1]
    if n < 20:
        return "十" + _CN_DIGIT[n - 11]
    tens, ones = divmod(n, 10)
    return _CN_DIGIT[tens - 1] + "十" + (_CN_DIGIT[ones - 1] if ones else "")


def teams_of(stage: str) -> list[tuple[str, float, float, float]]:
    """工段 → 15 个班组（名, 完整率, 完善率, 遵守率），按能力从高到低。"""
    (c0, p0, o0), (c1, p1, o1) = TEAM_CURVE[stage]
    out = []
    for i in range(N_TEAMS):
        t = i / (N_TEAMS - 1)
        out.append((f"{stage}{cn_num(i + 1)}班",
                    round(c0 + (c1 - c0) * t, 1),
                    round(p0 + (p1 - p0) * t, 1),
                    round(o0 + (o1 - o0) * t, 1)))
    return out


def _spread(n: int, k: int, offset: int = 0) -> set[int]:
    """在 ``range(n)`` 上等距挑 ``k`` 个下标（Bresenham，恰好 k 个）。

    ``offset`` 按天轮转挑选结果：Bresenham 在小 k 时**恒定挑末位下标**
    （n=5,k=1 永远选 index 4），不轮转的话同一道工序会被 15 天全选成
    「标准缺失」，工序级拆解就会出现 0% / 100% 的假极端值——
    班组级总缺口不变，但「哪道工序拖后腿」的结论会被带偏。
    """
    if k <= 0:
        return set()
    if k >= n:
        return set(range(n))
    picked = {i for i in range(n) if int((i + 1) * k / n) > int(i * k / n)}
    return {(i + offset) % n for i in picked} if offset else picked


def _rates(rows: list[dict]) -> tuple[float, float, float, float]:
    total = len(rows)
    present = sum(1 for r in rows if r["doc_present"])
    complete = sum(1 for r in rows if r["doc_complete"])
    comp = round(100.0 * present / total, 1) if total else 0.0
    perf = round(100.0 * complete / present, 1) if present else 0.0
    tot = sum(r["audit_total"] for r in rows)
    obey = round(100.0 * sum(r["audit_conform"] for r in rows) / tot, 1) if tot else 0.0
    return comp, perf, obey, round(comp * perf * obey / 10000.0, 1)


def build_rows() -> list[dict]:
    days = []
    d = DATE_FROM
    while d <= DATE_TO:
        days.append(d.isoformat())
        d += timedelta(days=1)

    rows: list[dict] = []
    for stage, line, steps, models in STAGES:
        for t_idx, (team, comp, perf, obey) in enumerate(teams_of(stage)):
            model = models[t_idx % 2]
            nsteps = len(steps)
            day_rows_list: list[list[dict]] = []
            for day in days:
                day_rows = [{
                    "audit_date": day, "product_model": model,
                    "process_stage": stage, "team": team,
                    "process_step": step,
                    "standard_no": f"STD-{'ASSY' if stage == '整机' else stage}-{s_idx + 1:02d}",
                    "audit_total": AUDIT_TOTAL[s_idx % len(AUDIT_TOTAL)],
                    "doc_present": 1, "doc_complete": 1,
                } for s_idx, step in enumerate(steps)]
                day_rows_list.append(day_rows)
            group = [r for dr in day_rows_list for r in dr]
            n, nd = len(group), len(day_rows_list)

            # 完整率：缺失标准文件的行 —— 按**天**配平（累计目标取整后作差），
            # 而不是整段等距铺开：整段铺开会把余数全堆在窗口最后一天，导致
            # 「当日」口径系统性偏差（单日样本 8 行，多 1 行就差 12.5pt）。
            # 没有标准文件的行不可能"标准完善"，doc_complete 同步置 0，
            # 否则完善率分母只含 present 行、分子却混入无文件行，口径失真。
            miss_total = n - round(n * comp / 100.0)
            assigned = 0
            for d_idx, day_rows in enumerate(day_rows_list):
                cum = round((d_idx + 1) * miss_total / nd)
                for i in _spread(nsteps, cum - assigned, offset=d_idx):
                    day_rows[i]["doc_present"] = day_rows[i]["doc_complete"] = 0
                assigned = cum
            # 完善率：分母只含有标准文件的行，同样按天配平
            present_total = sum(1 for r in group if r["doc_present"])
            inc_total = present_total - round(present_total * perf / 100.0)
            cum_present = assigned = 0
            for d_idx, day_rows in enumerate(day_rows_list):
                cum_present += sum(1 for r in day_rows if r["doc_present"])
                cum = (round(cum_present * inc_total / present_total)
                       if present_total else 0)
                idx = [i for i, r in enumerate(day_rows) if r["doc_present"]]
                for i in _spread(len(idx), max(cum - assigned, 0), offset=d_idx):
                    day_rows[idx[i]]["doc_complete"] = 0
                assigned = cum
            # 遵守率：按 Σaudit_total 精确配平（先各自四舍五入，再把余量逐行找平）
            target = round(sum(r["audit_total"] for r in group) * obey / 100.0)
            conform = [round(r["audit_total"] * obey / 100.0) for r in group]
            diff = target - sum(conform)
            while diff:
                step_dir = 1 if diff > 0 else -1
                progressed = False
                for i in range(n):
                    cap = group[i]["audit_total"]
                    if step_dir > 0 and conform[i] < cap:
                        conform[i] += 1
                        diff -= 1
                        progressed = True
                    elif step_dir < 0 and conform[i] > 0:
                        conform[i] -= 1
                        diff += 1
                        progressed = True
                    if not diff:
                        break
                if not progressed:   # 理论上不可达；防御死循环
                    break
            for i, r in enumerate(group):
                r["audit_conform"] = conform[i]
            rows.extend(group)
    return rows


def sql_text(rows: list[dict]) -> str:
    comp, perf, obey, conf = _rates(rows)
    n_days = (DATE_TO - DATE_FROM).days + 1
    n_steps = sum(len(s[2]) for s in STAGES)

    head = [
        "-- 标准三率审核明细（工段 × 班组）—— 由 tools/gen_std_three_rates_seed.py 生成，请勿手改。",
        "-- 为什么：标准三率改为「1 级看板 = 4 工段 / 2 级看板 = 各班组」，原 10 条按型号的",
        "-- 审核记录既无工段也无班组，撑不起两级看板，故按真实工艺补齐班组级审核明细。",
        f"-- 网格：{DATE_FROM} ~ {DATE_TO}（{n_days} 天）× 4 工段（SMT/PCM/PACK/整机）"
        f" × {N_TEAMS} 个班组/工段 × {n_steps // len(STAGES)} 道工序 = {len(rows)} 条。",
        "-- 口径与 dashboard._three_rates 同源：每行 = 某班组某天对某道工序标准的审核记录，",
        "-- 完整率 = 有标准文件行 ÷ 总行；完善率 = 标准完善行 ÷ 有标准文件行；",
        "-- 遵守率 = Σaudit_conform ÷ Σaudit_total；符合率 = 三者乘积。",
        f"-- 目标：整体 {comp}% × {perf}% × {obey}% = {conf}%（预警）；PCM段为短板（严重）。",
        "-- 幂等：id ≥ 2001 归本文件所有 —— 先 DELETE 该区间再整批插入，",
        "-- 网格调整（班组数 / 工序数 / 日期范围）后老演示库能自动对齐，不会残留旧班组；",
        "-- id 101–110 的历史行只做工段/班组回填（UPDATE），不删不改其它字段。",
        "-- ============================================================",
        "",
        "",
        "-- 老库回填：原 10 条按型号的审核记录补工段/班组（旧口径无此两列，故按工序静态映射）。",
        "-- 不映射的后果：这 10 条无工段的行既进不了工段分解、又会把整体值拉偏，",
        "-- 造成「4 工段加权 ≠ 整体」。映射依据见 dashboard._STEP_STAGE；",
        "-- 班组名必须是生成网格里的真实班组，否则会凭空多出一个孤立班组。",
        "-- 故意不加 `process_stage IS NULL` 守卫：班组命名随网格变过（甲班 → 一班），",
        "-- 带守卫就不会重跑，老库会残留「SMT甲班」这种 16 号孤立班组；",
        "-- CASE 是 process_step 的纯函数，重复执行结果恒定，天然幂等。",
        "UPDATE std_standard_audit SET process_stage = CASE process_step",
        "    WHEN '贴片' THEN 'SMT' WHEN '锡膏印刷' THEN 'SMT' WHEN '回流焊' THEN 'SMT'",
        "    WHEN '插件' THEN 'PCM' WHEN '波峰焊' THEN 'PCM'",
        "    WHEN '包装' THEN 'PACK'",
        "    ELSE '整机' END,",
        "  team = CASE process_step",
        "    WHEN '贴片' THEN 'SMT一班' WHEN '锡膏印刷' THEN 'SMT一班' WHEN '回流焊' THEN 'SMT一班'",
        "    WHEN '插件' THEN 'PCM一班' WHEN '波峰焊' THEN 'PCM一班'",
        "    WHEN '包装' THEN 'PACK一班'",
        "    ELSE '整机一班' END",
        "WHERE id BETWEEN 101 AND 110;",
        "",
        "",
        f"-- 清空本文件拥有的 id 区间（{SEED_ID_START}+）后整批重灌：网格调整也能幂等对齐。",
        f"DELETE FROM std_standard_audit WHERE id >= {SEED_ID_START};",
        "",
        "",
        "INSERT OR IGNORE INTO std_standard_audit (id, audit_date, product_model,",
        "  process_stage, team, process_step, standard_no, doc_present, doc_complete,",
        "  audit_total, audit_conform) VALUES",
    ]
    lines = []
    for i, r in enumerate(rows):
        tail = ";" if i == len(rows) - 1 else ","
        lines.append(
            f"({SEED_ID_START + i}, '{r['audit_date']}', '{r['product_model']}', "
            f"'{r['process_stage']}', '{r['team']}', '{r['process_step']}', "
            f"'{r['standard_no']}', {r['doc_present']}, {r['doc_complete']}, "
            f"{r['audit_total']}, {r['audit_conform']}){tail}")
    return "\n".join(head + lines) + "\n"


def build_causes(rows: list[dict]) -> list[dict]:
    """审核明细 → 班组级不良原因（按三率维度归因，数量与主表可对账）。

    粒度 = 某班组 × **某天** × 某维度 × 具体原因。按天落库（而不是只落班组汇总）是
    必须的：弹窗要跟当前口径走（当日/最近7天/当月），若原因只按 15 天汇总，
    切到 7 天窗口后「原因件数」会大于主表缺口，占比直接算出 150%。

    ``occurrences`` 口径与三个因子一一对应：
    - completeness：该班组当天**无标准文件**的审核行数；
    - perfection：有文件但**标准不完善**的审核行数；
    - compliance：审核**不符合项数** Σ(audit_total − audit_conform)。
    同一 (班组, 天, 维度) 的原因 occurrences 相加 == 当天该维度不符合数。
    """
    out: list[dict] = []
    for stage, _line, _steps, _models in STAGES:
        for t_idx, (team, _c, _p, _o) in enumerate(teams_of(stage)):
            days = sorted({r["audit_date"] for r in rows
                           if r["process_stage"] == stage and r["team"] == team})
            for day in days:
                sub = [r for r in rows if r["process_stage"] == stage
                       and r["team"] == team and r["audit_date"] == day]
                miss_rows = [r for r in sub if not r["doc_present"]]
                inc_rows = [r for r in sub if r["doc_present"] and not r["doc_complete"]]
                noncon = [{"process_step": r["process_step"],
                           "n": r["audit_total"] - r["audit_conform"]} for r in sub]
                buckets = {"completeness": miss_rows, "perfection": inc_rows,
                           "compliance": noncon}
                for dim, _cn, unit in RATE_DIMS:
                    items = buckets[dim]
                    if dim == "compliance":
                        occ = sum(x["n"] for x in items)
                        step_stat: dict[str, int] = {}
                        for x in items:
                            step_stat[x["process_step"]] = (
                                step_stat.get(x["process_step"], 0) + x["n"])
                    else:
                        occ = len(items)
                        step_stat = {}
                        for r in items:
                            step_stat[r["process_step"]] = (
                                step_stat.get(r["process_step"], 0) + 1)
                    if occ <= 0:
                        continue
                    # 涉及工序：不符合最多的前 2 道，写成「贴片,AOI」
                    top_steps = [s for s, n in sorted(step_stat.items(),
                                                      key=lambda kv: -kv[1]) if n > 0][:2]
                    share = CAUSE_SPLIT[t_idx % len(CAUSE_SPLIT)]
                    first = max(1, round(occ * share))
                    if first >= occ:        # 少数不符合项时不硬拆出 0 项的次因
                        parts = [occ]
                    else:
                        parts = [first, occ - first]
                    for i, n in enumerate(parts):
                        out.append({
                            "audit_date": day,
                            "process_stage": stage, "team": team,
                            "rate_dim": dim, "cause": CAUSE_LIB[dim][i],
                            "defect_type": (STAGE_DEFECT[stage][(t_idx + i) % 3]
                                            if dim == "compliance" else ""),
                            "process_steps": ",".join(top_steps),
                            "occurrences": n, "unit": unit,
                            "is_primary": 1 if i == 0 else 0,
                        })
    return out


def cause_sql_text(causes: list[dict]) -> str:
    head = [
        "-- 标准三率「班组级不良原因」—— 由 tools/gen_std_three_rates_seed.py 生成，请勿手改。",
        "-- 为什么：2 级看板点班组要看「这三率为什么差」，光有比率没有归因，等于只报数不诊断。",
        "-- 归因口径（与 _three_rates 的三个因子一一对应，数量可加总核对）：",
        "--   completeness = 无标准文件的审核行数；perfection = 有文件但标准不完善的行数；",
        "--   compliance = 不符合项数 Σ(audit_total − audit_conform)。",
        "-- 同一 (班组, 审核日, 维度) 的 occurrences 相加 == 当天该维度不符合数；",
        "-- 只对「遵守率」维度挂 defect_type（那才是产品不良），另两维是标准本身的问题。",
        f"-- 规模：{len(causes)} 条（按天落库：4 工段 × {N_TEAMS} 班组 × 15 天 × ≤3 维度 × ≤2 原因）。",
        f"-- 幂等：id ≥ {CAUSE_ID_START} 归本文件所有 —— 先 DELETE 再整批插入。",
        "-- ============================================================",
        "",
        "",
        f"DELETE FROM std_team_cause WHERE id >= {CAUSE_ID_START};",
        "",
        "",
        "INSERT OR IGNORE INTO std_team_cause (id, audit_date, process_stage, team,",
        "  rate_dim, cause, defect_type, process_steps, occurrences, unit, is_primary) VALUES",
    ]
    lines = []
    for i, c in enumerate(causes):
        tail = ";" if i == len(causes) - 1 else ","
        lines.append(
            f"({CAUSE_ID_START + i}, '{c['audit_date']}', '{c['process_stage']}', "
            f"'{c['team']}', '{c['rate_dim']}', '{c['cause']}', '{c['defect_type']}', "
            f"'{c['process_steps']}', {c['occurrences']}, '{c['unit']}', "
            f"{c['is_primary']}){tail}")
    return "\n".join(head + lines) + "\n"


def main() -> None:
    rows = build_rows()
    OUT.write_text(sql_text(rows), encoding="utf-8")
    causes = build_causes(rows)
    OUT_CAUSE.write_text(cause_sql_text(causes), encoding="utf-8")
    print(f"[gen] {OUT.name}: {len(rows)} 行 · 每工段 {N_TEAMS} 个班组")
    print(f"[gen] {OUT_CAUSE.name}: {len(causes)} 条不良原因")
    for stage in ("SMT", "PCM", "PACK", "整机"):
        sub = [r for r in rows if r["process_stage"] == stage]
        teams = teams_of(stage)
        best, worst = teams[0][0], teams[-1][0]
        print(f"  {stage:4s} {_rates(sub)}  最好 {best} {_rates([r for r in sub if r['team'] == best])}"
              f"  最差 {worst} {_rates([r for r in sub if r['team'] == worst])}")
    print(f"  整体   {_rates(rows)}")

    # 对账自检：每个班组三个维度的原因条数必须等于该维度不符合数（否则弹窗数字对不上）
    bad = 0
    for stage, _l, _s, _m in STAGES:
        for team, _c, _p, _o in teams_of(stage):
            sub = [r for r in rows if r["process_stage"] == stage and r["team"] == team]
            want = {
                "completeness": sum(1 for r in sub if not r["doc_present"]),
                "perfection": sum(1 for r in sub if r["doc_present"] and not r["doc_complete"]),
                "compliance": sum(r["audit_total"] - r["audit_conform"] for r in sub),
            }
            got: dict[str, int] = {}
            for c in causes:
                if c["process_stage"] == stage and c["team"] == team:
                    got[c["rate_dim"]] = got.get(c["rate_dim"], 0) + c["occurrences"]
            for dim, n in want.items():
                if n and got.get(dim, 0) != n:
                    bad += 1
                    print(f"  !! 对账不符 {stage}/{team}/{dim}: 原因合计 {got.get(dim, 0)} ≠ 实际 {n}")
    print(f"  原因对账：{'全部一致' if not bad else f'{bad} 处不一致'}")


if __name__ == "__main__":
    main()
