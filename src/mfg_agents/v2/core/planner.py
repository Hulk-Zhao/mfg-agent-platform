"""协同规划器（V4 多 Agent 协同核心）：跨域问题的并行取证与汇总。

设计立场（与"有边界的自主决策"同源，不引入自主 Agent 群）：
- **Planner 是编排者，不是同事**：跨域问题先由 Planner 拆解为 ≤4 个单域子任务
  （每个子任务指定目标域智能体与建议工具），子任务之间不互相调用、
  不递归触发 Planner——协同结构始终是"规划器 → 执行者"的浅层星型，
  防止 Agent 间自由组网的递归失控；
- **域智能体只做取证**：每个子任务复用既有 ``AgenticDomainAgent``（ReAct 工具
  循环、步数上限、HITL 只发起不放行），并行执行（线程池，互不阻塞）；
- **汇总器诚实聚合**：各域结论分节呈现、数据缺口合并去重、冲突不仲裁
  （不同域给出不同口径时如实并列，交给提问者判断）、provenance 全量保留；
- **降级不崩**：LLM 不可用时 Planner 退化为规则拆解（按域关键词命中逐域生成
  子任务）；单域失败不影响其他域结论（缺口如实标注）。

与场景 DAG 的边界：Planner = **即时只读跨域取证**（无工单、无 HITL 放行）；
判定需要闭环处置时，子智能体仍会经 ``run_scenario`` 建议走对应协同场景。
"""
from __future__ import annotations

import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Optional

logger = logging.getLogger(__name__)

__all__ = ["CollaborativePlanner", "CROSS_DOMAIN_SIGNALS"]

MAX_SUBTASKS = 4          # 子任务上限（防拆解爆炸）
_PER_DOMAIN_BUDGET_S = 60  # 单域子任务墙钟预算（域智能体内部另有步数/超时限制）

# 跨域信号词（域组 → 关键词；命中 ≥2 个不同域即判定跨域协同）。
# 词表与 router.DOMAIN_KEYWORDS / orchestrator.ROLE_KEYWORDS 同源精简——
# 这里只收"取证型"信号（判定/分析/查询），协同场景强信号（停线/客诉等）
# 已在路由层走 scenario，不会进到 Planner。
CROSS_DOMAIN_SIGNALS: dict[str, tuple[str, ...]] = {
    "quality": ("不良", "缺陷", "CPK", "Cpk", "cpk", "SPC", "良率", "合格率",
                "直通率", "质量", "返工", "报废率", "客诉", "8D"),
    "manufacturing": ("OEE", "稼动率", "产出", "设备", "产线", "线体", "排产",
                      "排程", "工时", "停机", "报工", "工段"),
    "supply_chain": ("订单", "交付", "OTD", "齐套", "缺料", "库存", "ATP",
                     "发货", "交期", "在途", "采购", "供应商"),
    "rd_engineering": ("BOM", "ECN", "NPI", "图纸", "工艺", "试产", "EVT",
                       "DVT", "PVT"),
}


class CollaborativePlanner:
    """跨域问数的规划器：判定 → 拆解 → 并行取证 → 汇总。"""

    def __init__(self, orchestrator: Any) -> None:
        self.orchestrator = orchestrator

    # ---------- 判定 ----------

    @staticmethod
    def hit_domains(query: str) -> list[str]:
        """返回问题命中的域组列表（按 DOMAIN_AGENT_CONFIGS 键序稳定输出）。"""
        hits = []
        for domain in ("quality", "manufacturing", "supply_chain", "rd_engineering"):
            words = CROSS_DOMAIN_SIGNALS.get(domain, ())
            if any(w in query for w in words):
                hits.append(domain)
        return hits

    def should_collaborate(self, query: str) -> bool:
        """跨域判定：≥2 个域组信号命中即协同（单域交给原域智能体路径）。"""
        return len(self.hit_domains(query)) >= 2

    # ---------- 拆解 ----------

    def plan(self, query: str) -> list[dict[str, Any]]:
        """拆解子任务：LLM 可用时按语义拆（每子任务指明域与关注点），
        不可用时按命中域规则拆（原问题逐域分发，域提示词即边界）。"""
        subtasks: list[dict[str, Any]] = [
            {"domain": d, "question": query, "focus": ""}
            for d in self.hit_domains(query)[:MAX_SUBTASKS]
        ]
        try:
            from ...common.llm_factory import get_llm, llm_available
            if llm_available() and len(subtasks) >= 2:
                llm = get_llm(temperature=0.0, tier="L1", max_tokens=500,
                              timeout=30, thinking="disabled")
                domains = json.dumps(
                    [{"domain": s["domain"],
                      "signals": list(CROSS_DOMAIN_SIGNALS.get(s["domain"], ()))[:6]}
                     for s in subtasks], ensure_ascii=False)
                prompt = (
                    "把下面的跨部门问题拆解为最多 4 个单域取证子任务（只拆取证/查询，"
                    "不做处置决策）。只输出一个 JSON 数组，每项形如 "
                    '{"domain": "quality|manufacturing|supply_chain|rd_engineering", '
                    '"question": "面向该域的自然语言子问题", "focus": "该域需要查什么"}。'
                    "每个域最多一个子任务，不要重复。"
                    f"\n可分配的域（含各自信号词）：{domains}\n原问题：{query}")
                resp = llm.invoke(prompt)
                raw = resp.content if hasattr(resp, "content") else str(resp)
                import re as _re
                m = _re.search(r"\[.*\]", raw or "", _re.S)
                if m:
                    parsed = json.loads(m.group(0))
                    # 域去重（LLM 可能同域拆两次——并行跑同域是浪费且结论重复）
                    seen: set[str] = set()
                    valid = []
                    for t in parsed:
                        if (isinstance(t, dict)
                                and t.get("domain") in {s["domain"] for s in subtasks}
                                and t["domain"] not in seen
                                and str(t.get("question") or "").strip()):
                            seen.add(t["domain"])
                            valid.append(t)
                    if len(valid) >= 2:
                        subtasks = valid[:MAX_SUBTASKS]
        except Exception as exc:  # noqa: BLE001 —— 拆解失败退回规则拆解（降级不崩）
            logger.warning("[planner] LLM 拆解失败，退回规则拆解: %s", exc)
        return subtasks

    # ---------- 执行 ----------

    def run(self, query: str, user_ctx: Optional[dict] = None,
            sink: Any = None) -> Optional[dict[str, Any]]:
        """执行协同取证：拆解 → 并行域智能体 → 汇总。

        返回 ``{answer, report, steps, hitl_tasks, data_gaps, domains, subtasks}``；
        判定不该协同或全部失败时返回 None（调用方回落原链路）。
        """
        subtasks = self.plan(query)
        if len(subtasks) < 2:
            return None
        t0 = time.perf_counter()
        results: list[dict[str, Any]] = []

        def _emit(event: dict) -> None:
            if sink is not None:
                try:
                    sink.emit(event)
                except Exception:  # noqa: BLE001 —— 事件失败不影响取证
                    pass

        def _run_one(task: dict[str, Any]) -> dict[str, Any]:
            from ..agents.agentic import AgenticDomainAgent
            domain = task["domain"]
            node = f"collab_{domain}"
            _emit({"type": "agent_start", "node": node,
                   "label": f"{domain} 域并行取证", "detail": task.get("focus", "")})
            t_start = time.perf_counter()
            try:
                agent = AgenticDomainAgent(domain=domain, orchestrator=self.orchestrator)
                out = agent.run(task["question"], user_ctx=user_ctx)
                _emit({"type": "agent_end", "node": node, "status": "ok",
                       "elapsed_ms": round((time.perf_counter() - t_start) * 1000, 1),
                       "summary": f"{len(out.get('steps') or [])} 步工具调用"})
                return {"domain": domain, "ok": True, "out": out,
                        "focus": task.get("focus", "")}
            except Exception as exc:  # noqa: BLE001 —— 单域失败不拖垮整体
                logger.warning("[planner] 域 %s 取证失败: %s", domain, exc)
                _emit({"type": "agent_end", "node": node, "status": "degraded",
                       "elapsed_ms": round((time.perf_counter() - t_start) * 1000, 1),
                       "summary": f"取证失败：{type(exc).__name__}"})
                return {"domain": domain, "ok": False, "error": str(exc)[:120],
                        "focus": task.get("focus", "")}

        with ThreadPoolExecutor(max_workers=min(4, len(subtasks))) as pool:
            futures = {pool.submit(_run_one, t): t for t in subtasks}
            for fut in as_completed(futures):
                results.append(fut.result())

        # 汇总器：分节聚合，冲突不仲裁（不同口径如实并列），缺口合并去重
        ok_results = [r for r in results if r.get("ok") and r.get("out")]
        if not ok_results:
            return None
        sections: list[str] = []
        steps: list[dict[str, Any]] = []
        data_gaps: list[str] = []
        hitl_tasks: list[dict[str, Any]] = []
        domain_names: dict[str, str] = {}
        from ..agents.agentic import DOMAIN_AGENT_CONFIGS
        for r in sorted(ok_results, key=lambda x: x["domain"]):
            out = r["out"]
            name = DOMAIN_AGENT_CONFIGS.get(r["domain"], {}).get("name", r["domain"])
            domain_names[r["domain"]] = name
            answer = str(out.get("answer") or "").strip()
            if answer:
                sections.append(f"【{name}】{'（' + r['focus'] + '）' if r['focus'] else ''}\n{answer}")
            for st in (out.get("steps") or []):
                st = dict(st)
                st["domain"] = r["domain"]
                steps.append(st)
            for gap in (out.get("data_gaps") or []):
                if gap and gap not in data_gaps:
                    data_gaps.append(gap)
            if out.get("hitl_task"):
                hitl_tasks.append(out["hitl_task"])
        failed = [r for r in results if not r.get("ok")]
        if failed:
            for r in failed:
                data_gaps.append(f"{domain_names.get(r['domain'], r['domain'])}取证失败：{r.get('error', '')}")
        for r in results:
            if r.get("ok") and not (r.get("out") or {}).get("answer"):
                data_gaps.append(f"{DOMAIN_AGENT_CONFIGS.get(r['domain'], {}).get('name', r['domain'])}未能形成结论")

        body = "\n\n".join(sections)
        elapsed = time.perf_counter() - t0
        report = (f"{body}\n\n（由协同规划器完成跨域取证：{len(ok_results)} 个域智能体并行、"
                  f"共 {len(steps)} 步工具调用、耗时 {elapsed:.1f}s"
                  f"{'；存在数据缺口，已如实标注' if data_gaps else ''}）")
        _emit({"type": "fan_in", "node": "fanin",
               "summary": f"{len(ok_results)} 域并行取证完成，结论已汇总"})
        return {
            "answer": body,
            "report": report,
            "steps": steps,
            "hitl_tasks": hitl_tasks,
            "data_gaps": data_gaps,
            "domains": [r["domain"] for r in ok_results],
            "subtasks": subtasks,
            "elapsed_ms": round(elapsed * 1000, 1),
        }
