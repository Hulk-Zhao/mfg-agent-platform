"""Streamlit 入口：streamlit run mfg_agents/ui/app.py"""
import streamlit as st
import sys
from pathlib import Path

# 允许直接 python -m 运行时找到src
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from mfg_agents.graph.builder import app

st.set_page_config(page_title="制造业多Agent协同平台", layout="wide")
st.title("制造业多Agent协同平台")
st.caption("业务Agent编排制造/品质/供应链/设备/FA/仓储，自动生成对客报告")

# 示例问题
EXAMPLES = {
    "停线应急": "A型号今天在SMT-1线停线了，客户急单，什么时候能复线？",
    "来料异常": "今天IQC发现锡膏批次有问题，怎么处理？",
    "客户投诉": "华为投诉A型号BATCH-A-1008批次元件偏移，需要8D",
    "出货风险": "A型号今天能发多少货？客户催SO-20261015-01",
    "设备预警": "SMT-1线设备参数有异常吗？",
}

with st.sidebar:
    st.header("场景示例")
    for label, q in EXAMPLES.items():
        if st.button(label, key=label):
            st.session_state["query"] = q

query = st.text_area("请输入业务问题", value=st.session_state.get("query", ""), height=100)

if st.button("开始分析", type="primary") and query.strip():
    with st.spinner("多Agent协同分析中..."):
        result = app.invoke({"user_query": query})

    st.subheader("1. 业务Agent任务拆解")
    st.write(f"识别场景：**{result.get('scenario')}** | 型号：{result.get('product_model')} | 线体：{result.get('line_code')}")
    for step in result.get("task_plan", []):
        st.write(f"- {step}")

    st.subheader("2. 各专业Agent返回")
    c1, c2 = st.columns(2)
    with c1:
        st.info(f"**制造**：{result.get('production_summary','-')}")
        st.info(f"**品质**：{result.get('quality_summary','-')}")
        st.info(f"**供应链**：{result.get('supply_summary','-')}")
    with c2:
        st.info(f"**设备**：{result.get('equipment_summary','-')}")
        st.info(f"**仓储**：{result.get('warehouse_summary','-')}")
        st.success(f"**FA分析**：{result.get('fa_summary','-')}")

    if result.get("verification_items"):
        st.write("复线前验证项：")
        for v in result["verification_items"]:
            st.write(f"- [ ] {v}")

    st.subheader("3. 对外报告（脱敏）")
    st.text_area("给客户的回复", value=result.get("customer_report", ""), height=150, label_visibility="collapsed")

    with st.expander("内部简报（不对客）"):
        st.json(result.get("internal_brief", {}))
