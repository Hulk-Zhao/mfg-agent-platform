# 架构说明

## 场景与路由

| 场景key | 业务问题示例 | 编排路径 |
|---|---|---|
| line_stop | "A型号在SMT-1停线，何时复线" | business → manufacturing → (fan-out: quality + supply_chain + equipment) → fa → report |
| incoming_quality | "今天锡膏批次有问题" | business → supply_chain → quality → fa → report |
| customer_complaint | "华为投诉BATCH-A-1008" | business → quality → fa → report |
| delivery_risk | "A型号能发多少货" | business → manufacturing → warehouse → report |
| equipment_warning | "SMT-1设备参数异常吗" | business → equipment → fa → report |

## Agent 职责矩阵

| Agent | 对接系统 | 输入 | 输出 | 关键工具 |
|---|---|---|---|---|
| Business | - | 用户自然语言 | scenario/model/line/batch/urgency + task_plan | LLM JSON抽取 |
| Manufacturing | MES | model, line | oee_data, confirmed_stop, stop_info | Text-to-SQL |
| Quality | QMS | model, line | dominant_defect, quality_summary | Text-to-SQL |
| SupplyChain | SCM | - | abnormal_material, supply_summary | Text-to-SQL |
| Equipment | EAM | line | abnormal_alarms, equipment_summary | Text-to-SQL |
| Warehouse | WMS/TMS | model | available_qty, warehouse_summary | Text-to-SQL |
| FA | ChromaDB+LLM | 以上聚合 | rating/root_cause/eta/verification | RAG + LLM结构化输出 |
| Report | - | internal_brief | customer_report(脱敏) + internal_brief | Prompt脱敏 |

## 关键设计

1. **fan-out/fan-in**：停线场景下品质/供应链/设备三Agent并行查询，LangGraph自动等三者完成再进FA
2. **安全SQL**：Text-to-SQL后正则过滤DDL/DML，只允许SELECT
3. **脱敏报告**：Report Agent用Prompt禁词表（设备故障/供应商不良/停线/根因）替换为对外话术
4. **本地LLM**：复用宿主机Ollama qwen3.5:9b，无需外网API
