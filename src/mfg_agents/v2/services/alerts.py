"""预警中心聚合服务：五类预警，前四类由**当前只读数据**机械推导。

纯聚合，不落库、不建工单：把本地只读数据源的当前状态折成「预警中心」条目，
供 ``demo/api.py`` 的 ``GET /api/v2/alerts`` 返回。前端据此做真实优先展示，
后端离线 / 降级时才回落到演示快照（与看板屏同一套「真实优先 + 演示降级」纪律）。

第五类「现场推送」的真相源是**治理库**（``site_board_push``，客诉 8D 分派后的
现场处置队列），不走 ``DataSourceRegistry``——它是分派的真实结果，业务库故障时
也照实展示（见 ``build_alerts`` 降级口径）；状态取自现场真实回写，不编造。

纪律（与 ``services/dashboard.py`` / ``services/digest.py`` 同源）：
- 真实优先：全部走 ``DataSourceRegistry``（只读 + 行数上限），口径即代码；
- 同源可对账：SPC 越限复用 SPC 看板的 ``build_spc_dashboard``、订单临期复用订单
  看板的 ``build_order_dashboard``、缺料复用统一注册查询 ``material_shortage``
  ——与三个看板/速查逐字段一致，绝不另算一套口径；
- 降级不崩：任一数据源失败只记 ``degraded_reason``，端点返回骨架，**绝不 500**；
- 绝不编造：库里没有预警处置生命周期表，就按**可复算的机械规则**给等级与状态
  （见 ``ALERT_RULES``），不做「已派人处理 / 预计 2h 复线」这类无据描述。
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Optional

logger = logging.getLogger(__name__)

__all__ = ["build_alerts", "ALERT_TYPES", "ALERT_RULES"]

#: 预警类别（顺序即前端筛选片顺序，**字面量必须与前端 typeCls 键完全一致**）：
#: 前四类由**当前只读业务数据**机械推导；「现场推送」的真相源是**治理库**
#: 的现场看板推送表（``site_board_push``，客诉 8D 分派后的现场处置队列）——
#: 业务库故障不影响它，这是设计而非漏判（见 build_alerts 降级口径）。
ALERT_TYPES: tuple[str, ...] = ("SPC越限", "缺料", "订单临期", "设备告警", "现场推送")

#: 联动部门（按预警类型的固定协同对象，体现多域协同设计；属展示配置）
_TAGS: dict[str, list[str]] = {
    "SPC越限": ["品质域 · PQE", "制造域 · 生产现场"],
    "缺料": ["供应链域 · 采购", "供应链域 · 物控", "制造域 · 生产现场"],
    "订单临期": ["业务域 · 业务", "供应链域 · 物控"],
    "设备告警": ["设备域 · 设备维护", "制造域 · 生产现场"],
    "现场推送": ["制造域 · 生产现场", "品质域 · QA（8D 牵头）"],
}

#: 每类预警的来源责任人（库里没有预警责任人字段，按类型给责任域，不指名到人）
_OWNER: dict[str, str] = {
    "SPC越限": "品质域 · PQE",
    "缺料": "供应链域 · 物控",
    "订单临期": "业务域 · 业务",
    "设备告警": "设备域 · 设备维护",
    "现场推送": "制造域 · 生产现场",
}

#: 判定口径（随载荷回传给前端展示——让「预警怎么来的」在界面上可见）
ALERT_RULES: list[str] = [
    "SPC越限：SPC 看板最近 7 天窗口内 Cpk 低于控制下限 1.33 的特性；"
    "Cpk<1.00 → P1，1.00≤Cpk<1.33 → P2；状态=待处置。",
    "缺料：材料库存可用量低于安全库存的料号（按缺口降序取前 8）；"
    "可用量为 0 或缺口 ≥ 安全库存 75% → P1，其余 → P2；状态=待处置。",
    "订单临期：订单看板最近 7 天窗口内 risk_type≠正常 的订单（按延期天数降序取前 8）；"
    "客户等级 S 或已延期 → P1，其余 → P2；已进入「发货」环节=处置中，其余=待处置。",
    "设备告警：equipment_log 中 is_abnormal=1 的现场告警；告警码以 E 开头 → P1，"
    "其余 → P2；当日记录=待处置，更早记录=已闭环。",
    "现场推送：客诉 8D 场景经 dispatch_site 节点归因责任工段后落到治理库 "
    "site_board_push 的推送队列；状态取自现场真实回写（pushed=待处置、acked=处置中）；"
    "客诉处置令 → P1，8D 工单通知 → P2；已闭环不进预警清单（闭环历史在 /api/v2/site-board 查）。",
]

#: 每类预警取前 N 条（缺料 / 订单量级远大于一屏可读；SPC 特性数有限不截断）
_TOPN = 8


# ---------------------------------------------------------------- 基础工具

def _rows(registry, sql: str, state: dict[str, Any]) -> list[dict]:
    """执行只读聚合 SQL；异常兜底返回空列表并记录降级原因（与 dashboard 同款）。"""
    try:
        return registry.execute_readonly_sql(sql)
    except Exception as exc:  # noqa: BLE001 —— 聚合失败不抛出，记原因后降级
        _note(state, f"{type(exc).__name__}: {exc}")
        return []


def _note(state: dict[str, Any], msg: str) -> None:
    if msg and msg not in state["reasons"]:
        state["reasons"].append(msg)


def _num(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _alert(*, aid: str, type_: str, level: str, status: str, title: str,
           detail: str, basis: str, time: str, owner: str = "",
           ask: str = "", entity: Optional[dict] = None) -> dict[str, Any]:
    """统一预警条目（字段与前端 ``render_alerts`` 的展示契约一致）。"""
    return {
        "id": aid,
        "type": type_,
        "level": level,              # P1 / P2（驱动 alert-level 配色）
        "status": status,            # 待处置 / 处置中 / 已闭环
        "title": title,
        "detail": detail,
        "basis": basis,              # 判定依据（展开面板展示，非编造的处置步骤）
        "tags": list(_TAGS.get(type_, [])),
        "time": time,
        "owner": owner or _OWNER.get(type_, ""),
        "ask": ask,                  # 携带真实实体的问题（供「去问数分析」）
        "entity": entity or {},      # 供后续上下文关联 / 审计留痕
    }


# ---------------------------------------------------------------- 四类预警

def _spc_alerts(registry, state: dict[str, Any], label: str) -> list[dict]:
    """SPC 越限：复用 SPC 看板能力判定（同一 ``build_spc_dashboard``）→ 可对账。"""
    from .dashboard import build_spc_dashboard

    dash = build_spc_dashboard(registry, "window")
    if dash.get("degraded"):
        _note(state, "SPC 看板聚合降级：" + str(dash.get("degraded_reason") or "数据源降级"))
    label = (dash.get("window") or {}).get("label") or label
    kpi_by = {k.get("characteristic"): k for k in (dash.get("kpis") or [])}
    bad = [c for c in (dash.get("capabilities") or [])
           if c.get("verdict") in ("不合格", "偏低")]
    # 不合格在前（Cpk 升序），保证 P1 优先
    bad.sort(key=lambda c: (c.get("verdict") != "不合格", _num(c.get("cpk")) or 9.0))

    out: list[dict] = []
    for i, cap in enumerate(bad, 1):
        name = cap.get("characteristic") or "—"
        cpk = cap.get("cpk")
        kpi = kpi_by.get(name) or {}
        spec = cap.get("spec") or {}
        level = "P1" if (cpk is not None and _num(cpk) < 1.0) else "P2"
        out.append(_alert(
            aid=f"AL-SPC-{i:02d}", type_="SPC越限", level=level, status="待处置",
            title=f"{name} Cpk={cpk}，低于控制下限 1.33",
            detail=f"{label}窗口内该特性共 {cap.get('n_subgroups') or 0} 个子组 / "
                   f"{cap.get('n_total') or 0} 个测量值；超差率 {kpi.get('over_rate')}%"
                   f"（{kpi.get('over_num')}/{kpi.get('over_den')}）；"
                   f"规格限 USL={spec.get('usl')} / LSL={spec.get('lsl')}；"
                   f"判定：{cap.get('verdict')}。",
            basis="SPC 判定线：Cpk≥1.33 合格 / 1.00≤Cpk<1.33 偏低 / Cpk<1.00 不合格"
                  "（双西格玛口径，与 SPC 过程能力看板同一套内核）。",
            time=label,
            ask=kpi.get("ask") or f"{name} 的过程能力 Cpk 如何？是否需要加严检验或工程介入？",
            entity={"characteristic": name},
        ))
    return out


def _shortage_alerts(registry, state: dict[str, Any]) -> list[dict]:
    """缺料：复用统一注册查询 ``material_shortage``（与数据速查同一口径）。"""
    try:
        rows = registry.run_query("material_shortage")
    except Exception as exc:  # noqa: BLE001 —— 单类降级不拖垮整页
        _note(state, f"{type(exc).__name__}: {exc}")
        return []
    top = sorted(rows, key=lambda r: -_num(r.get("shortage_qty")))[:_TOPN]

    out: list[dict] = []
    for i, r in enumerate(top, 1):
        part = r.get("part_no") or "—"
        avail = _num(r.get("available_qty"))
        safety = _num(r.get("safety_stock"))
        short = _num(r.get("shortage_qty"))
        ratio = (short / safety) if safety > 0 else 1.0
        level = "P1" if (avail <= 0 or ratio >= 0.75) else "P2"
        out.append(_alert(
            aid=f"AL-MAT-{part}", type_="缺料", level=level, status="待处置",
            title=f"{part} {r.get('part_name') or ''} 可用量 {int(avail)} 低于安全库存 "
                  f"{int(safety)}，缺口 {int(short)}",
            detail=f"类别 {r.get('category') or '—'}；在库 {int(_num(r.get('on_hand_qty')))} / "
                   f"预留 {int(_num(r.get('reserved_qty')))} / 可用 {int(avail)}；"
                   f"安全库存 {int(safety)}；缺口 {int(short)}。"
                   f"（可用量 = 在库 − 预留，与「数据速查 · 缺料清单」同一口径）",
            basis="材料库存可用量低于安全库存即为缺料；缺口占安全库存 75% 以上或可用量为 0 判为 P1。",
            time="当前快照",
            ask=f"{part} 的库存、在途与齐套情况如何？",
            entity={"part_no": part},
        ))
    return out


def _order_alerts(registry, roles: list[str], state: dict[str, Any]) -> list[dict]:
    """订单临期：复用订单看板风险订单清单；客户身份按 orders 数据范围同一套规则脱敏。"""
    from .dashboard import build_order_dashboard

    dash = build_order_dashboard(registry, "window")
    if dash.get("degraded"):
        _note(state, "订单看板聚合降级：" + str(dash.get("degraded_reason") or "数据源降级"))
    label = (dash.get("window") or {}).get("label") or ""
    rows = [dict(r) for r in (dash.get("risk_orders") or [])]
    # 预警中心全员可见，但客户名仍只给承担对客责任的岗位：
    # 复用 orders 的脱敏规则（risk_orders[].customer / customer_level），不另立一套。
    try:
        from .data_scope import get_data_scope
        wrapped, _masked = get_data_scope().apply("orders", {"risk_orders": rows}, roles)
        rows = wrapped.get("risk_orders") or []
    except Exception as exc:  # noqa: BLE001 —— 脱敏异常按最严处理：隐藏客户名
        _note(state, f"客户字段脱敏降级：{type(exc).__name__}")
        for r in rows:
            r["customer"], r["customer_level"] = "客户 —", "—"

    out: list[dict] = []
    for i, r in enumerate(rows, 1):
        order_no = r.get("order_no") or "—"
        delay = int(_num(r.get("delay_days")))
        stage = r.get("stage") or "—"
        level = "P1" if (r.get("customer_level") == "S" or delay > 0) else "P2"
        tail = f"，已延期 {delay} 天" if delay > 0 else ""
        out.append(_alert(
            aid=f"AL-{order_no}", type_="订单临期", level=level,
            status="处置中" if stage == "发货" else "待处置",
            title=f"{order_no}（{r.get('customer') or '—'} · {r.get('product_model') or '—'}）"
                  f"{r.get('risk_type') or '风险'}{tail}",
            detail=f"数量 {int(_num(r.get('qty')))}；客户等级 {r.get('customer_level') or '—'}；"
                   f"当前环节 {stage}；进度 {int(_num(r.get('progress')))}%；"
                   f"缺口说明：{r.get('shortage_note') or '—'}；统计窗口 {label}。",
            basis="订单看板最近 7 天窗口内 risk_type≠正常 的订单；"
                  "客户等级 S 或已延期判为 P1；已进入「发货」环节视作处置中，其余待处置。",
            time=r.get("due_date") or "",
            owner=r.get("owner") or _OWNER["订单临期"],
            ask=f"{order_no} 的交付风险与 ATP 可承诺量如何？",
            entity={"order_no": order_no, "model": r.get("product_model") or ""},
        ))
    return out


def _equipment_alerts(registry, state: dict[str, Any]) -> list[dict]:
    """设备告警：equipment_log 中 is_abnormal=1 的现场告警（当日 / 更早区分状态）。"""
    from .dashboard import DEMO_TODAY

    rows = _rows(registry,
                 "SELECT equipment_id, line_code, log_time, alarm_code, param_name, "
                 "param_value FROM equipment_log WHERE is_abnormal = 1 "
                 "ORDER BY log_time DESC", state)
    out: list[dict] = []
    for i, r in enumerate(rows, 1):
        log_time = str(r.get("log_time") or "")
        code = str(r.get("alarm_code") or "")
        level = "P1" if code.upper().startswith("E") else "P2"
        out.append(_alert(
            aid=f"AL-EQ-{i:02d}", type_="设备告警", level=level,
            status="待处置" if log_time[:10] == DEMO_TODAY else "已闭环",
            title=f"{r.get('equipment_id') or '—'}（{r.get('line_code') or '—'}）"
                  f"{r.get('param_name') or '参数'} 异常：{r.get('param_value') or '—'}",
            detail=f"告警码 {code or '—'}；记录时间 {log_time or '—'}。"
                   f"（equipment_log.is_abnormal=1 的现场告警记录）",
            basis="现场设备日志中的异常记录；告警码以 E 开头判为 P1；"
                  "当日记录=待处置，更早记录=已闭环。",
            time=log_time[:16],
            ask=f"{r.get('equipment_id') or ''} 设备参数异常吗？",
            entity={"equipment_id": r.get("equipment_id") or "",
                    "line": r.get("line_code") or ""},
        ))
    return out


# ---------------------------------------------------------------- 现场推送（治理库）


def _site_board_alerts(roles: list[str], state: dict[str, Any]) -> list[dict]:
    """现场推送：客诉 8D 分派到责任工段的现场看板队列（治理库 ``site_board_push``）。

    与其余四类的区别（**刻意**）：真相源是治理库的推送表，不是业务只读库——
    客诉处置令 / 8D 工单通知都是分派的真实结果，业务库故障时也照实展示。

    状态与等级都是可复算的机械规则（不是编出来的生命周期）：``pushed``=待处置、
    ``acked``=处置中，``已闭环``不进预警清单（预警中心是"要做的事"，闭环历史走
    ``/api/v2/site-board`` 查）。客户名与现场看板接口同一套脱敏规则（``rows[].customer``）。
    """
    from . import site_board

    rows = [r for r in site_board.list_pushes()
            if r.get("status") in ("pushed", "acked")]
    try:
        from .data_scope import get_data_scope
        wrapped, _masked = get_data_scope().apply("site_board", {"rows": rows}, roles)
        rows = wrapped.get("rows") or []
    except Exception as exc:  # noqa: BLE001 —— 脱敏异常按最严处理：隐藏客户名
        _note(state, f"现场推送客户字段脱敏降级：{type(exc).__name__}")
        for r in rows:
            r["customer"] = "客户 —"

    out: list[dict] = []
    for r in rows:
        push_id = str(r.get("push_id") or "")
        stage = str(r.get("stage") or "")
        complaint_no = str(r.get("complaint_no") or "")
        batch = str(r.get("batch_no") or "")
        customer = str(r.get("customer") or "")
        level = "P1" if str(r.get("source") or "") == "complaint_8d" else "P2"
        kind_cn = "客诉处置令" if level == "P1" else "8D 整改工单通知"
        status = "待处置" if r.get("status") == "pushed" else "处置中"
        acked = (f"；已由 {r.get('ack_by')} 接收于 {r.get('ack_at')}"
                 if r.get("status") == "acked" and r.get("ack_by") else "")
        out.append(_alert(
            aid=push_id, type_="现场推送", level=level, status=status,
            title=(f"{complaint_no or batch or push_id}"
                   f"（{customer or '—'}）· {stage} {kind_cn}"),
            detail=(f"归因工段 {stage}；批次 {batch or '—'}；{kind_cn}；"
                    f"推送于 {r.get('created_at') or '—'}{acked}"),
            basis=("客诉 8D 场景由 dispatch_site 节点归因责任工段"
                   "（显式工段 / 产线前缀 / 批次反查），经通知服务落到治理库 site_board_push；"
                   "pushed=待处置、acked=处置中；客诉处置令判 P1、工单通知判 P2。"),
            time=str(r.get("created_at") or ""),
            owner=_OWNER["现场推送"],
            ask=(f"{complaint_no or batch} 的客诉处置进展与批次围堵状态如何？"
                 if (complaint_no or batch) else "现场推送的处置进展如何？"),
            entity={"push_id": push_id, "stage": stage,
                    "complaint_no": complaint_no, "batch_no": batch},
        ))
    return out


# ---------------------------------------------------------------- 聚合入口

def build_alerts(registry, roles: list[str]) -> dict[str, Any]:
    """预警中心载荷：五类预警 + 计数 + 判定口径（真实优先，降级不崩）。

    ``roles``：当前岗位的权限角色 key 列表（服务端令牌裁决得到），
    仅用于订单客户 / 现场推送客户字段脱敏——预警中心本身全员可见
    （异常可能来自任何岗位）。

    降级口径（与「四类都由只读业务库推导」不同，故单列）：业务库故障时
    **只有前四类为空**，「现场推送」读的是治理库 ``site_board_push``，仍如实展示
    ——它是客诉分派的真实结果，不因业务库不可用而隐瞒。
    """
    from .dashboard import resolve_window

    state: dict[str, Any] = {"reasons": []}
    win = resolve_window("window")
    label = win.get("label") or "最近7天"

    alerts: list[dict] = []
    for collector in (
        lambda: _spc_alerts(registry, state, label),
        lambda: _shortage_alerts(registry, state),
        lambda: _order_alerts(registry, roles, state),
        lambda: _equipment_alerts(registry, state),
        lambda: _site_board_alerts(roles, state),
    ):
        try:
            alerts.extend(collector())
        except Exception as exc:  # noqa: BLE001 —— 单类失败不拖垮整页
            _note(state, f"{type(exc).__name__}: {exc}")
            logger.warning("[alerts] 预警聚合失败: %s", exc)

    # 排序：P1 优先，其次按类型固定顺序（前端筛选片顺序）
    order = {t: i for i, t in enumerate(ALERT_TYPES)}
    alerts.sort(key=lambda a: (0 if a.get("level") == "P1" else 1,
                               order.get(a.get("type"), 99)))

    counts = {t: 0 for t in ALERT_TYPES}
    for a in alerts:
        if a.get("type") in counts:
            counts[a["type"]] += 1

    return {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "source": "sqlite:dev",
        "degraded": bool(state["reasons"]),
        "degraded_reason": "；".join(state["reasons"][:3]),
        "window": win,
        "types": list(ALERT_TYPES),
        "counts": counts,
        "rules": list(ALERT_RULES),
        "alerts": alerts,
    }
