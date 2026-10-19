# 演示数据规格说明书 spec-v1（T01 交付物）

> 对内工作文件 · 2026-10-19 · 状态：已开发（T01 完成）· 对应《AIGC 顺序开发计划 v2.0》T01
> 行业设定：电子装配（机加工通过 `tools/demo_gen/profile_machining.json` 切换）
> 所有企业名、人名、料号、电话、金额均为虚构，仅用于演示。

## 1. 演示故事设定

- **主线订单 SO-DEMO-001**：客户「华骏电子（虚构）」订购控制板 DEMO-PCB-5001 5,000 片，走完整五阶段：接单评审 → 立项 → 制样（EVT/DVT/PVT）→ 批量 → 出货。
- **支线 A（ECN-DEMO-001）**：批量进行到 60% 时，因电源芯片 DEMO-IC-2201 停产风险，客户提出换料，触发 ECN，影响 2 张在制工单 + 1 批库存 + 1 批在途。
- **支线 B（客诉 8D-DEMO-001）**：首批出货后 20 天，客户反馈 12 片烧录异常，演示 15 分钟批次定位（批次→工单→工序→设备→当班→同批库存）。
- **背景数据**：另 2 张进行中订单、5 家供应商、8 台设备、30 天 SPC 序列（含 2 个判异点）、制度问答 FAQ 10 条、脱敏合成案例 20 条。

## 2. 对象模型与字段口径

### 2.1 客户（customer）
| 字段 | 类型 | 口径 |
|------|------|------|
| customer_id | string | 主键，`DEMO-CUS-xxx` |
| name | string | 虚构企业名 |
| quality_profile | string | 历史合作质量摘要（客诉次数/退货率） |
| credit_grade | string | A/B/C |

### 2.2 物料（material）
| 字段 | 类型 | 口径 |
|------|------|------|
| material_id | string | 主键 `DEMO-MAT-xxx` |
| name | string | 虚构料名 |
| kind | enum | finished / semi / raw |
| std_cost | number | 标准成本（元/单位），用于报价分量 1 |
| supplier_id | string | 默认供应商 |

### 2.3 BOM（bom，EBOM/MBOM 两版）
| 字段 | 类型 | 口径 |
|------|------|------|
| bom_id | string | 主键 |
| material_id | string | 归属父项 |
| version | string | EBOM v1.0 / MBOM v1.0 / v1.1（ECN 后） |
| kind | enum | E / M |
| lines | array | {material_id, qty, ref_des} |

### 2.4 销售订单（sales_order / order_line）
| 字段 | 类型 | 口径 |
|------|------|------|
| order_id | string | `DEMO-SO-xxx`，主线 SO-DEMO-001 |
| customer_id | string | |
| lines | array | {material_id, qty, unit_price, promise_date} |
| status | enum | draft / reviewing / approved / in_production / shipped / closed |
| promise_date | date | 对客承诺交期 |
| actual_ship_date | date | 实际发货日，OTD=actual-promise ≤0 |

### 2.5 报价单（quote）
| 字段 | 类型 | 口径 |
|------|------|------|
| quote_id | string | `DEMO-QT-001` |
| order_id | string | |
| material_cost | number | 材料成本=BOM 用量×最新采购价 |
| labor_cost | number | 工时成本=标准工时×费率 |
| tool_amort | number | 模具摊销=模具费/订单量 |
| price_low / price_high | number | 报价区间，三个分量之和为下限，上限加 8% 毛利缓冲 |

### 2.6 生产工单（work_order / operation）
| 字段 | 类型 | 口径 |
|------|------|------|
| wo_id | string | `DEMO-WO-xxx` |
| order_id | string | |
| material_id | string | |
| qty_planned / qty_good | number | 投入/直通 |
| status | enum | released / in_progress / done / closed |
| ops | array | {op_no, name, std_hours, equipment_id}，主线三道工序 SMT/DIP/测试 |

### 2.7 检验批（inspection_batch）
| 字段 | 类型 | 口径 |
|------|------|------|
| batch_id | string | `DEMO-IB-xxx` |
| wo_id | string | |
| stage | enum | IQC / IPQC / OQC |
| sample_qty / defect_qty | number | |
| result | enum | pass / fail / concession |
| inspector / ts | string/datetime | |

### 2.8 设备（equipment）
| 字段 | 类型 | 口径 |
|------|------|------|
| equipment_id | string | `DEMO-EQ-xxx`，共 8 台 |
| name | string | 虚构机型（贴片机/回流焊/波峰焊/ICT/...） |
| status | enum | running / idle / down |

### 2.9 批次追溯（lot）
| 字段 | 类型 | 口径 |
|------|------|------|
| lot_id | string | `DEMO-LOT-xxx` |
| material_id | string | |
| wo_id / op_no | string | 产出生序 |
| qty | number | |
| location | string | 当前库位（在库/在途/已发） |

### 2.10 事件流（event）
| 字段 | 类型 | 口径 |
|------|------|------|
| event_id | string | 主键 |
| ts | datetime | 时间轴单调递增 |
| type | enum | order_status / report / inspection / alert / hitl / ecn / complaint |
| object_ref | string | 关联对象 id |
| payload | object | 关键数字快照 |
| scene | enum | S1..S6，该事件支撑哪个场景屏 |

## 3. 主线时间轴（SO-DEMO-001，节选关键节点）

| # | 阶段 | 事件 | 支撑场景 |
|---|------|------|----------|
| 1 | 接单 | 询价到达，ATP 初评（库存+产能+料期） | S1 |
| 2 | 接单 | 报价单三分量计算，报价区间产出 | S1 |
| 3 | 接单 | 订单评审会签三人通过，订单 approved | S1 |
| 4 | 立项 | 立项材料汇总，长周期料暴露，案例检索推送 | S2 |
| 5 | 制样 | EVT 报告 + 阶段门会签 | S3 |
| 6 | 制样 | DVT 报告（BOM 冻结、CPK≥1.33）+ 阶段门 | S3 |
| 7 | 制样 | PVT 报告（直通率≥98%）+ 量产放行 | S3 |
| 8 | 批量 | 工单下达，SMT/DIP/测试 三道工序报工 | S4 |
| 9 | 批量 | SPC 判异 1：SMT 炉温偏离，推送处置会签 | S4 |
| 10 | 批量 | **支线 A：ECN 换料，影响面四象限产出，切换会签** | S4 |
| 11 | 批量 | ECN 切换执行，工单 60% 后切新 MBOM v1.1 | S4 |
| 12 | 批量 | SPC 判异 2：ICT 误判率异常，处置闭环 | S4 |
| 13 | 出货 | OQC 放行单签发，发货确认，开票 | S5 |
| 14 | 出货 | **支线 B：客诉 8D，批次 15 分钟定位** | S5 |
| 15 | 采购 | PO 下发/交期确认/缺料预警/催料任务 | S6 |

完整 40+ 事件由 T02 生成器按本表展开（含背景事件的间隙填充）。

## 4. 五角色数据视图

| 角色 | 看的字段 | 场景权限 |
|------|----------|----------|
| gm-demo | 全量汇总：订单管道、风险订单、指标看板 | S1–S6 只读 |
| biz-demo | 订单、报价、ATP、客户档案 | S1/S5 |
| rd-demo | BOM、ECN、立项、阶段门、案例 | S2/S3/S4 |
| qa-demo | 检验批、SPC、判异、8D、追溯 | S3/S4/S5 |
| mfg-demo | 工单、工序、设备、报工、缺料 | S4/S6 |

## 5. 界面数字台账（T02 生成、T03 校验、场景屏消费）

| 界面数字 | 字段/算法 |
|----------|-----------|
| ATP 可用量 | 库存 - 在制占用 - 安全库存 |
| 报价下限 | material_cost + labor_cost + tool_amort |
| OTD 达成 | actual_ship_date ≤ promise_date |
| 直通率 | qty_good / qty_planned（工单级） |
| CPK | 按 SPC 序列重算（(USL-LSL)/6σ） |
| ECN 影响面 | 在制工单数 + 库存批次 + 在途批 + 未发订单行 |
| 客诉定位用时 | 批次首次出现事件 ts → 定位完成事件 ts，演示目标 ≤15 分钟 |
| 催料任务数 | 缺料预警触发的待办数 |

## 6. 约束

1. 全部对象编号带 `DEMO-` 前缀；虚构企业名词表见 `tools/demo_gen/wordlist.json`，T12 隔离脚本以此比对。
2. 时间轴起点固定为演示日（生成参数 `--base-date`），保证任何时间跑出来都是「刚刚发生」。
3. 金额单位元，保留 2 位小数；数量整数；比率保留 4 位小数。
