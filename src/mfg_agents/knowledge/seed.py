"""初始化ChromaDB历史异常案例库。运行一次：python -m mfg_agents.knowledge.seed"""
from ..tools.rag_tools import add_case

CASES = [
    {
        "id": "case-001",
        "doc": "SMT线锡膏印刷偏移：刮刀压力异常导致锡膏沉积量不稳定，表现为元件偏移、连锡。",
        "meta": {"rating": "B", "recovery_hours": 2, "root_cause": "刮刀压力漂移", "defect_type": "锡膏偏移"},
    },
    {
        "id": "case-002",
        "doc": "回流焊温区异常：温区3温度偏高10℃，导致立碑、虚焊。",
        "meta": {"rating": "B", "recovery_hours": 1.5, "root_cause": "加热管老化", "defect_type": "虚焊"},
    },
    {
        "id": "case-003",
        "doc": "来料PCB焊盘氧化：供应商批次问题导致可焊性差，批量虚焊。",
        "meta": {"rating": "A", "recovery_hours": 8, "root_cause": "供应商来料不良", "defect_type": "虚焊"},
    },
    {
        "id": "case-004",
        "doc": "贴片机吸嘴磨损：导致元件偏移、抛料率上升。",
        "meta": {"rating": "C", "recovery_hours": 0.5, "root_cause": "吸嘴磨损", "defect_type": "元件偏移"},
    },
    {
        "id": "case-005",
        "doc": "钢网清洁周期过长：锡膏堵塞网孔导致印刷缺件。",
        "meta": {"rating": "C", "recovery_hours": 1, "root_cause": "钢网清洁不及时", "defect_type": "缺件"},
    },
]


def run_seed():
    for c in CASES:
        add_case(c["id"], c["doc"], c["meta"])
    print(f"[seed] 已写入 {len(CASES)} 条历史案例")


if __name__ == "__main__":
    run_seed()
