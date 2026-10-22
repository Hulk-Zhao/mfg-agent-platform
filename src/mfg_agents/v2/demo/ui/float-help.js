/**
 * 智造协同平台 · 悬浮帮助助手（float-help.js）
 *
 * 一个可拖动的悬浮入口：自动识别用户当前所在的屏幕（场景），给出该场景的
 * 预置答疑；点击选项后**快速流式输出**预先写好的答案，答完后选项仍在，
 * 点「＋」重新展开。**切换场景即开新会话**：历史问答清空，只留当前场景的
 * 问候语与选项。
 *
 * 设计纪律（与平台既有约定对齐）：
 * - 零依赖零构建：原生 JS + IIFE，样式走 app.css 的 `.fh-` 命名空间；
 * - 场景识别的唯一事实源是 `window.App.state.screen`（含 SceneRegistry 合并的
 *   场景屏），解析不到时回落 location.hash——绝不自己造第二套路由；
 * - 答疑内容必须与代码现状一致：改屏内交互 / 链路 / 权限口径时同步改本文件，
 *   否则助手会失真（与 help-screen.js 同一纪律）；
 * - 文案用「」不用 ASCII 直引号；所有动态文本过 esc()；
 * - 内容禁真实品牌 / 手机号 / 外链：本文件位于 T12 隔离自检扫描范围内
 *   （src/mfg_agents/v2/demo/**），示例问题一律用虚构口径（A型号 / SMT-1）。
 *
 * 位置持久化：localStorage `mfg_float_help_pos_v1`（仅存 FAB 左上角坐标）。
 * 首次打开（无存档）默认到**最右边**（右下角，距边 20px）。
 *
 * 健壮性约定（修过的坑，改前先读）：
 * - 选项区超过 ~6 条出现独立滚动条（滚轮下滑），overscroll-behavior
 *   防止滚到头连带滚动背后页面；消息区同理；
 * - 流式输出期间用户上滑回看时不强制跟底，新消息入场才强制滚到底部；
 * - 流式被中断（点别的选项 / 切场景 / 关面板）时摘除残留光标；
 * - 拖动哨兵用布尔量，不用 (0,0) 坐标当「没按过」的判据；
 * - 拖到一半松手不算点击（防意外开合面板），位置钳制在视口内。
 */
(function () {
  "use strict";

  /* ============================================================
   * 一、常量
   * ============================================================ */

  var POS_KEY = "mfg_float_help_pos_v1";
  var FAB_SIZE = 48;          // 悬浮按钮直径（px）
  var EDGE = 20;              // 默认距屏幕边缘（px）
  var DRAG_THRESHOLD = 4;     // 位移超过该值才算拖动（否则算点击）
  var SCENE_TICK_MS = 400;    // 场景识别轮询间隔
  var STREAM_CHUNK = 2;       // 流式输出每次追加字符数
  var STREAM_TICK = 16;       // 流式输出间隔（ms）——约 120 字/秒，"快速"但不闪

  /* ============================================================
   * 二、预置答疑知识库（按屏幕 id 索引）
   *
   * 字段：name 场景名 / intro 场景一句话说明 / faqs 选项答疑列表。
   * 答案支持极简格式：**加粗**、`代码`、"- " 列表项、空行分段。
   * 内容口径与 screens.js / kb-screen.js / case-screen.js / office-screen.js
   * 及各场景屏的现状逐一核对过——改那些文件时务必同步这里。
   * ============================================================ */

  var KB = {
    dashboard: {
      name: "工作台",
      intro: "一屏总览：关键指标、异常预警与各屏快捷入口。",
      faqs: [
        {
          q: "第一次进来，从哪看起？",
          a: "按这个顺序三分钟上手：\n\n- **指标区**：先扫一眼核心 KPI，有异常的卡片点一下（会带你下钻到对应看板）；\n- **预警区**：看有没有标红的异常摘要，点击进预警中心；\n- **入口区**：常用功能都在这，不用去翻菜单。\n\n想系统了解平台，去「帮助中心」；有具体问题，去「协同问数」直接问。",
        },
        {
          q: "顶栏的状态灯是什么意思？",
          a: "那是后端健康探针（`/api/v2/health`）的结果：\n\n- **绿**：业务库 / 治理库 / 案例库 / 缓存 / 向量库全部正常；\n- **黄**：部分依赖降级（比如缓存或模型不可用，功能仍可用）；\n- **红**：后端离线——页面自动进入演示模式，顶栏会写明「后端离线 · 演示模式」，演示数据绝不冒充实时结果。",
        },
        {
          q: "工作台是做什么的？",
          a: "工作台是进入平台后的**第一屏**：把当天最该看的数字、预警和入口集中在一处。\n\n- 指标区：核心 KPI 一览，多数卡片可以点击；\n- 预警区：异常摘要，点击可下钻到预警中心；\n- 入口区：常用功能快捷跳转。\n\n它的定位是「看现状」，不是操作台——要追问原因，去协同问数。",
        },
        {
          q: "点击看板上的数字会发生什么？",
          a: "多数 KPI 卡可点击，行为分两种：\n\n- **带着问题去问数**：自动跳到协同问数并发送预填问题（你还能继续追问）；\n- **下钻到看板**：跳到对应看板的分解视图（按维度 → 单实体 → 工序/工位逐级展开）。\n\n后端离线时下钻会回落演示数据，并明确标注「演示」，不会冒充实时。",
        },
        {
          q: "为什么我和同事看到的界面不一样？",
          a: "岗位差异化分**三层，互相独立**：\n\n- 菜单层：按职责收敛，只保留与本岗位相关的屏；\n- 看板数据层：按后端数据范围目录裁决，未登记的看板默认拒绝（fail-closed）；\n- 待办数据层：按是否持有 HITL 槽位权限显示。\n\n菜单隐藏只是展示层收敛，真正的数据权限由后端把关。",
        },
      ],
    },

    orders: {
      name: "订单履约看板",
      intro: "订单全链路管道：OTD / OTIF / 延期订单率 / 齐套率与风险订单。",
      faqs: [
        {
          q: "客户名称显示「客户A」是坏了吗？",
          a: "不是故障，是**字段脱敏**：仓库 / 采购 / 物控等岗位看到的是「客户A / 客户B」代号，并且客户维度下钻不可用；商务等对口岗位可见原文。\n\n这是后端按岗位数据范围裁决的结果——看到代号说明你的岗位本来就不该看到客户身份。",
        },
        {
          q: "四个核心指标是什么口径？",
          a: "订单履约看板盯四件事：\n\n- **OTD 准交率**：按承诺日期准时交付的比例；\n- **OTIF**：准时且完整（数量、批次不缺）交付的比例；\n- **延期订单率**：已延期订单占比，按交期紧迫度排序；\n- **齐套率**：物料齐套程度，缺一套就发不出货。\n\n口径由平台指标库统一给出，在协同问数里追问会拿到同口径数字。",
        },
        {
          q: "风险订单怎么用？",
          a: "风险订单清单按交期、齐套、金额综合排序，点击行可逐级下钻：按维度分解 → 单个订单 → 工序/工位明细。\n\n客户名称等敏感字段按岗位脱敏——看不到不是故障，是权限。\n\n发现对客交期有风险时，最终承诺要走人工确认，Agent 只发起、不放行。",
        },
        {
          q: "能对客承诺交期吗？",
          a: "**不能自动承诺**。对客交期承诺是平台 10 个 HITL 槽位之一：Agent 可以汇总影响面、发起工单，但放行必须由对应岗位人工确认。\n\n在审批确认中心可以跟踪这类工单的会签进度与 SLA 倒计时。",
        },
      ],
    },

    mfgboard: {
      name: "制造综合看板",
      intro: "制造 + 品质双看板：稼动率 / 报废率 / 报工完成率 / 直通率 / 标准三率。",
      faqs: [
        {
          q: "「三率」是什么意思？",
          a: "标准三率 = **完整率 × 完善率 × 遵守率 = 符合率**：\n\n- **完整率**：该做记录的审核项有没有做全；\n- **完善率**：做了的记录内容是否达标；\n- **遵守率**：作业是否按标准执行。\n\n三者相乘意味着任何一环掉链子都会拉低总符合率——这也是为什么下钻时能定位短板环节。",
        },
        {
          q: "双看板分别看什么？",
          a: "制造侧：**稼动率**（设备实际开动占比）、**报工完成率**（工单报工进度）；品质侧：**报废率**、测试**直通率**与标准三率。\n\n所有指标按岗位数据范围裁决，看不到的产线/型号是权限范围外，不是数据丢失。",
        },
        {
          q: "标准三率现在怎么分级看？",
          a: "标准三率是两级看板：\n\n- **1 级 · 工段**：SMT / PCM / PACK / 整机 四张卡，各显示本段完整率 × 完善率 × 遵守率 = 符合率，颜色随是否达标变化，卡脚标出该段班组数（每段 15 个）与审核量；\n- **2 级 · 班组**：**单击工段卡下钻**到 2 级看板。上半部是该工段汇总（三率公式 + 达标班组数 / 最低班组 / 与目标差 / 短板环节），紧接着是该工段窗口内 TOP3 不良类型，下半部是 15 个班组明细（含与工段差 pt），最差班组排最前并标 TOP1–3。\n- **3 级 · 班组明细**：**单击任意班组行弹窗**，看该班组的三率拆解（按工序 / 逐日趋势）与不良原因归因（标准缺失 / 标准不完善 / 未按标准作业三个维度，数量与审核明细可对账）。\n\n面包屑「返回标准三率」可回 1 级；弹窗与下钻页都跟随当前口径（当日/当月/最近7天）。整体、工段、班组同一套口径（按审核项加权），分项加起来就回整体。",
        },
        {
          q: "指标异常了怎么跟进？",
          a: "两条路：\n\n- 在看板内逐级下钻，定位到具体产线 / 型号 / 工序；\n- 点「去问数」带着上下文跳到协同问数，用自然语言追问根因。\n\n涉及停机、复线等决策时，平台会走人工确认工单，不会自动执行。",
        },
      ],
    },

    spc: {
      name: "SPC 过程能力看板",
      intro: "逐特性 Cpk / Pp / Ppk 判定、子组超差率下钻与不良柏拉图。",
      faqs: [
        {
          q: "第一次进来，从哪看起？",
          a: "推荐三步：\n\n- **先看判定**：每张「逐特性 Cpk」卡片自带合格 / 偏低 / 不合格结论，红色（不合格）优先处理；\n- **再看超差率**：点「2 级看板 · 按特性分解」进入全特性排行，超差率最高的排最前；\n- **最后看柏拉图**：不良集中在哪几类缺陷，锁定关键少数。\n\n顶部还有三组筛选（统计口径 / 产品分类 / 工段），全部卡片、表格、柏拉图同时生效。",
        },
        {
          q: "Cpk 和 Ppk 有什么区别？",
          a: "都是过程能力指数，区别在波动口径：\n\n- **Cpk**：只计子组**内部**波动，反映短期过程能力（设备/工艺本身行不行）；\n- **Ppk**：计**整体**波动（含组间漂移），反映长期实际表现，通常略低于 Cpk。\n\n两者相差明显说明过程有系统性漂移——正是需要排查的信号。判定线一致：≥1.33 充足。",
        },
        {
          q: "Cpk 多少算合格？",
          a: "常用判定线：\n\n- **Cpk ≥ 1.33**：过程能力充足；\n- **1.0 – 1.33**：有能力但需改进；\n- **< 1.0**：能力不足，需整改。\n\n看板对每个特性给出 Cpk / Pp / Ppk 与判定结论——判定由后端按数据计算，不是拍脑袋。",
        },
        {
          q: "「子组超差率」是什么？跟 Cpk 什么关系？",
          a: "看板下钻用的核心指标：**超出规格限的测量值条数 ÷ 测量条数**（按子组口径）。\n\n- 它和 Cpk 是**同一过程的两面**：Cpk 高则超差率低；\n- Cpk 是无量纲指数（≥1.33 合格），超差率是百分比（目标 ≤5%），两者对照着看：\n  Cpk 告诉你「能力够不够」，超差率告诉你「实际超了多少」。\n\n下钻页的大数字、逐日表、明细行的「分子 ÷ 分母」都是它的构成：分子 = 越限条数，分母 = 测量条数。",
        },
        {
          q: "筛选（口径 / 产品分类 / 工段）怎么用？",
          a: "顶部工具栏三组筛选：\n\n- **统计口径**：当日 / 当月 / 最近 7 天，决定取哪个时间窗的数据；\n- **产品分类**：消费电子 / 智能电源 / 储能电源 / 车载；\n- **工段**：SMT / PCM / PACK / 整机。\n\n筛选后 KPI 卡、能力明细、柏拉图、TOP5 **同时生效**，口径标签会显示当前范围（如「工段 SMT · 产品分类 消费电子」），问数也带同一口径——看到的和问到的永远一致。",
        },
        {
          q: "2 级看板和弹窗怎么看？",
          a: "点「过程能力明细」标题旁的**「2 级看板 · 按特性分解」**进入：\n\n- **顶部大数字**：全特性汇总的超差率（分子 ÷ 分母 已标注业务含义）；\n- **明细行**：每个特性一行，按超差率排序；\n- **单击任意一行**：弹出该特性的能力明细——Cpk / 判定 / 逐日越限 / 原始记录，不用离开当前页；\n- 弹窗里还有**「进入 3 级看板」**，进入单特性的完整下钻页（逐日 + 原始记录），面包屑可随时返回。\n\n从 L1 的能力明细表点某一行，也能直接进 3 级看该特性原始记录。",
        },
        {
          q: "逐日表里好多「—」是什么意思？",
          a: "**「—」表示当天没有测量记录，不是零超差**——没有生产或没有检验，就没有样本。\n\n例如某特性只在 10-14 与 10-15 有数据，那么 10-08 到 10-13 全部显示「—」。这比填 0 更诚实：填 0 会假装「当天测了且全部合格」。",
        },
        {
          q: "规格限是真实的工艺标准吗？",
          a: "**不是**。看板里的规格上限 / 下限是**演示规格限**（工艺侧待给定），界面上已明确标注。\n\n- 判定档位（合格 / 偏低 / 不合格）由规格限与波动共同算出，规格限一换，判定可能随之改变；\n- 规格限经工艺确认后回填，判定需重新复核。\n\n在正式使用前，请勿把当前数值当作真实放行标准。",
        },
        {
          q: "柏拉图怎么用？",
          a: "不良柏拉图按「二八原则」排序不良类型（关键少数 = 累计首达 80%）。点击某一类型，可继续按产线 / 型号分解下钻，快速定位贡献最大的那个环节。\n\n柏拉图与能力明细**共用同一套筛选口径**，下钻明细同样受岗位权限约束。",
        },
        {
          q: "SPC 异常谁来处置？",
          a: "SPC 处置是 HITL 槽位之一：平台给出判定与预警，处置动作（调整工艺参数、停机排查等）由对应岗位人工确认后闭环，全程留痕可审计。",
        },
      ],
    },

    alerts: {
      name: "预警中心",
      intro: "质量 / 制造 / 供应链 / 设备多域预警与联动处置。",
      faqs: [
        {
          q: "预警太多，先处理哪条？",
          a: "新手建议按影响排序：\n\n- **先看影响交付的**：停线、缺料导致工单受阻的，拖一天损失实在；\n- **再看质量类**：不良率、判异预警，多数可以当天内闭环；\n- **已生成工单的**：去审批确认中心跟 SLA 倒计时，超时会分级催办。\n\n每条预警都带「去问数」入口，不确定影响面就带着上下文问一句。",
        },
        {
          q: "预警从哪里来？",
          a: "多域规则计算：质量域（不良率、判异）、制造域（停机、产出偏差）、供应链域（缺料、延期）、设备域（告警日志）。\n\n列表按岗位可见——你只看得到职责范围内的异常，这是后端权限裁决的结果。",
        },
        {
          q: "一条预警怎么处理？",
          a: "每个预警卡片带动作入口：\n\n- **去问数**：带着该预警上下文跳到协同问数，直接追问根因；\n- **跟工单**：已生成处置工单的，在审批确认中心跟踪会签与 SLA。\n\n预警是「看见异常」，处置是「流程治理」，两步分开但可衔接。",
        },
      ],
    },

    chat: {
      name: "协同问数",
      intro: "对话式跨部门协同分析：两级路由 → 场景 DAG → 工单 → 人工确认 → 报告。",
      faqs: [
        {
          q: "不知道怎么提问？给我几个例子",
          a: "三类典型问法，直接照抄改词就行：\n\n- **查数**：「A型号成品库存还有多少？」「SMT-1 昨天的稼动率是多少？」\n- **问原因**：「A型号在SMT-1停线，何时复线？」「这批锡膏来料检验结论是什么？」\n- **跨部门关联**：「不良率升高的同时 OTD 也降了，有什么关系？」——这类会自动拆成多个子任务并行取证。\n\n问错也没成本：重新问一句就行，对话随时可清空。",
        },
        {
          q: "怎么提问？",
          a: "在输入框直接打字，**Enter 发送、Shift+Enter 换行**；也可以点输入框上方的快捷问题一键提问。\n\n支持自然语言，例如「A型号在SMT-1停线，何时复线？」。不必记语法——平台先做路由判断，再决定走哪条链路。",
        },
        {
          q: "提问后平台做了什么？",
          a: "分两段：\n\n- **两级路由**：先判代价。看板点进来的问题走**结构化口径直查**（与看板同一个聚合函数，档位/筛选与你所看一致，毫秒级）；**开放式提问**（「本月度最需关注什么」「整体情况怎么样」「最近有什么风险」）走**跨域态势汇总**——按你所在岗位可见的看板口径，列出未达标项、SPC 能力不足与待你确认的工单，口径随提问（本月→当月）；简单数据问题（查库存、查指标）走直答；复杂问题（停线、客诉）才走重型链路。\n- **协同编排**：单域问题由域智能体 ReAct 取证；**跨部门问题**（如「不良率升高的同时 OTD 也降了」）由协同规划器拆成 ≤4 个子任务，多域智能体**并行取证**后分节汇总——过程有 SSE 实时事件流，每个域在做什么都看得见。\n\n需要闭环处置的仍走场景 DAG（工单 → 人工确认 → 报告）。",
        },
        {
          q: "问过的问题能找回吗？",
          a: "可以。输入框上方「最近问过」保存本次会话的问题，点击即可重问；「清空对话」一键清空当前记录。\n\n跨刷新不丢失——会话记录按岗位保存在本地。",
        },
        {
          q: "后端离线会怎样？",
          a: "进入**演示模式**：提问使用预置结果回放，界面明确标注「演示」。\n\n这样做是为了保证页面在任何环境下都完整可用，同时绝不把编造的数据冒充实时结果——顶栏后端状态灯也会显示「后端离线 · 演示模式」。",
        },
        {
          q: "结论可信吗？",
          a: "三条保证：\n\n- **可溯源**：结果卡带数据溯源（SQL 原文、命中行、脱敏标记、耗时），可展开核对；\n- **人在环**：关键决策（复线、对客口径、8D 关闭）一律人工确认，Agent 只发起、不放行；\n- **不编造**：查不到就明说，并附数据缺口或转人工工单。",
        },
      ],
    },

    kbqa: {
      name: "企业制度问答",
      intro: "制度 / 流程规范知识库问答，结论带出处。",
      faqs: [
        {
          q: "这里适合问什么？和协同问数有什么区别？",
          a: "问**制度、流程、规范**类问题，例如：\n\n- 「差旅报销流程是什么？」\n- 「来料特采需要哪些角色会签？」\n\n答案带制度名与条款出处，可点开核对原文。\n\n区别一句话：**问「规定是什么」来这里，问「现在数据怎么样」去协同问数**——两边链路不同，别拿制度库问库存。",
        },
        {
          q: "为什么有的问题秒回？",
          a: "四层降级，能确定性答的**不调模型**：\n\n- L0 Redis 缓存（同问题直接命中）；\n- L1 FAQ 双闸直答：相似度 ≥ 0.70 **且**领先第二名 ≥ 0.15 才直答——两个相近条目时绝不随机选；\n- L2 意图路由：先判是不是域内问题；\n- L3 才走 RAG（向量召回 + 模型生成）。\n\n所以高频制度问题是零推理成本、结果可复现的。",
        },
        {
          q: "回答一定准吗？",
          a: "结论**带出处**（制度名 + 条款），可点开核对原文。\n\n域外问题（比如拿制度库问设备参数）会走约束直答而不是硬塞答案——宁可不答，也不编。",
        },
        {
          q: "答得好怎么沉淀？",
          a: "点回答下方的「👍 收进直答库」：该回答进入管理员审核队列，过审即收进 FAQ 直答库——下次同问 0ms 直答。\n\n好答案被复用，是这套机制的设计目的。",
        },
      ],
    },

    cases: {
      name: "制造案例检索",
      intro: "历史质量问题案例检索：权限前置、引用可溯源。",
      faqs: [
        {
          q: "怎么搜命中率更高？",
          a: "三个技巧：\n\n- **用规范术语**：「锡膏印刷偏移」比「印歪了」命中率高——平台有受控词表会归一同义说法，但越接近规范用语召回越准；\n- **带限定词**：加上型号、工序（如「A型号 PCM 段」），缩小范围；\n- **先大类后细化**：先搜缺陷模式看有哪些历史案例，再按根因/对策筛。\n\n搜不到不一定是没有——也可能在权限范围外（见下一条）。",
        },
        {
          q: "为什么搜不到某些案例？",
          a: "**权限前置**：身份在检索前就编译成过滤条件下推给向量库——无权案例统一返回 404，连「存在性」都不泄漏。\n\n候选不足时会如实告知 hidden_count，但绝不放宽过滤去补数。看不到 ≠ 没录入。",
        },
        {
          q: "引用怎么溯源？",
          a: "每条引用过三元组校验：案例ID + 块ID + 内容版本。对不上即拒载。\n\n检索全程落审计日志（只记哈希不记正文，支持对账）。可看 ≠ 可带走——导出默认拒绝，外发恒定拒绝。",
        },
        {
          q: "想看其他部门的案例？",
          a: "提交跨部门访问申请，审批通过后按授权范围可见。案例是竞争资产，授权必须发生在检索之前，这是产品裁定而非技术将就。",
        },
      ],
    },

    data: {
      name: "数据速查",
      intro: "材料 / 成品 / 批次库存只读速查，受岗位数据权限约束。",
      faqs: [
        {
          q: "数据速查和协同问数有什么区别？",
          a: "一句话：**速查是「点开就看」，问数是「开口就问」**。\n\n- **数据速查**：固定四类只读快照（材料 / 成品 / 批次 / 呆滞），页面秒开，适合每天例行看数；\n- **协同问数**：自然语言任意问，走路由与编排，适合追问原因、跨部门关联分析。\n\n例行巡检用速查，遇到「为什么」去问数。",
        },
        {
          q: "能查哪些数据？",
          a: "四类只读快照：\n\n- 材料库存与缺料缺口；\n- 成品库存与可发量；\n- 来料批次与检验结论；\n- 库龄呆滞与批次追溯入口。\n\n一律只读——不承诺出入库、调拨、盘点。",
        },
        {
          q: "查不到想要的数据集？",
          a: "受岗位数据权限约束。可提交数据查询权限申请：说明数据集与用途，审批通过后按范围开放。\n\n演示环境下有「演示自动审批」入口（带演示角标），方便走完流程——真实环境没有自动审批。",
        },
        {
          q: "速查会改我的数据吗？",
          a: "不会。速查链路是纯只读：后端按令牌角色做表级 ACL 与字段脱敏，前端没有任何写入口。要发起操作（如预订、订餐）去 OA 协同，且高影响事项仍要人工确认。",
        },
      ],
    },

    oa: {
      name: "OA 协同",
      intro: "会议室预订 / 订餐办理 + 我的预订订单，高影响先人工确认。",
      faqs: [
        {
          q: "会议室时间被占了怎么办？",
          a: "预订时会**自动避冲突**：已占时段不可选，选了已被占的时段会直接提示冲突，不会静默覆盖别人的预订。\n\n会议室实在订不到，可以换筛选条件（容量 / 设备）找替代会议室，或换时段；需要报告厅 / 培训室等需授权资源时走人工确认工单。",
        },
        {
          q: "能办哪些事？",
          a: "办公事务四类：\n\n- 会议室预订（按容量/设备筛选，自动避冲突）；\n- 订餐（食堂 / 商务餐厅，按菜单计价）；\n- 查我的预订与订单；\n- 报销 / 请假 / 补卡自助申请。\n\n写操作限本人名下办理，不会静默覆盖他人预订。",
        },
        {
          q: "为什么有的操作要点确认？",
          a: "**高影响事项不自动放行**：报告厅/培训室等需授权资源、商务客餐、≥500 元订单、报销/请假/补卡申请——这些一律走人工确认工单。\n\n日常低影响操作（普通会议室、工作餐）可直接办理并留痕。",
        },
        {
          q: "订错了怎么办？",
          a: "「我的」页面里可取消**本人名下**的预订 / 订单，取消即时生效并留痕。他人名下的记录在此页面不可见，也从根上避免了误操作别人的安排。",
        },
      ],
    },

    hitl: {
      name: "审批确认中心",
      intro: "人工在环：待办队列、会签、SLA——Agent 只发起，不放行。",
      faqs: [
        {
          q: "HITL、会签、SLA 这些词是什么意思？",
          a: "审批中心的三个核心名词：\n\n- **HITL**（Human-in-the-loop，人工在环）：关键决策必须由人放行，Agent 只发起、不审批；\n- **会签**（cosign）：多角色对同一决策背书，**缺一不可**，面板实时显示还差谁；\n- **SLA**：处置时限承诺——工单有时限，超时会按级别催办升级，卡片上能看到倒计时。\n\n这三个词共同回答一个问题：**谁为这个决策负责**。",
        },
        {
          q: "怎么知道我有没有审批权限？",
          a: "不用猜，两个地方看：\n\n- **菜单**：「审批确认中心」只对持有 HITL 槽位权限的岗位显示——你看到这个入口，说明至少有域内观察权限；\n- **列表**：进去后分「我的活」（轮到你动作）与「域内待观察」（你域内但没轮到你）。\n\n没有治理职责的岗位本就不该看到这个屏——不是权限出错了。",
        },
        {
          q: "待办列表分几种？",
          a: "两种可见性：\n\n- **我的活**（mine）：轮到本岗位动作，可发起审批 / 会签；\n- **域内待观察**（domain）：属于本治理域但还没轮到你，只读展示。\n\n这样设计是因为「活没到我这」和「没活」是两回事——后者会让审批中心看起来瘫痪。",
        },
        {
          q: "会签是什么规则？",
          a: "多角色背书，**缺一不可**：复线、来料处置、8D 关闭等决策要求规定角色全部签字。进度实时显示还差谁、各自状态如何。\n\n想推动流程，照着缺失角色去点人即可。",
        },
        {
          q: "Agent 能自己审批吗？",
          a: "**不能**。权限注册表在构造期就禁止 Agent 持有任何审批权限——智能体只发起工单、附上影响面与建议，放行永远是人。\n\n这不是策略开关，是代码级约束。",
        },
        {
          q: "为什么我的列表是空的？",
          a: "两种可能：\n\n- 确实没有待办（去其他屏看看）；\n- 本岗位不持有任何 HITL 槽位权限——此时会显示说明而不是空白页，因为「不参与治理审批」本身就该说清。\n\n菜单可见性同样只展示持有槽位的岗位，不让你白跑一趟。",
        },
      ],
    },

    reports: {
      name: "报告中心",
      intro: "内部简报与对客脱敏报告同屏对比，版本留痕。",
      faqs: [
        {
          q: "报告是谁写的？",
          a: "由**协同问数链路自动生成并归档**：事件闭环后，多域取证的结果 + 人工确认记录自动汇成报告，数字全部来自 SQL 溯源，不是人工誊抄。\n\n内部简报与对客脱敏报告同事件各一份——对客那份的字段级脱敏也是链路自动做的，人只负责校对。",
        },
        {
          q: "内部简报和对客报告差别在哪？",
          a: "同一事件的两份产物：\n\n- **内部简报**：完整事实与数据，含工单与人工确认记录；\n- **对客报告**：字段级脱敏 + 禁词表兜底，敏感词高亮标注（悬停可见禁词原文，方便校对）。\n\n脱敏映射表就列在对客报告下方，改口径先改它。",
        },
        {
          q: "历史报告在哪看？",
          a: "协同问数产生的报告自动归档**最近 30 份**，点击展开全文。\n\n每次修订都留痕（版本、时间、操作人、修订说明）——报告是给人和审计看的，不是一次性产物。",
        },
        {
          q: "能导出吗？",
          a: "点右上角「打印 / 导出」，走浏览器打印对话框另存 PDF。内部简报与对客报告分别可打印——对外发的那份，永远用脱敏版。",
        },
      ],
    },

    help: {
      name: "帮助中心",
      intro: "架构图、智能体清单、工具清单与治理安全说明。",
      faqs: [
        {
          q: "想系统了解平台，从哪开始？",
          a: "推荐路径：\n\n- **先看本页架构分层图**：十分钟理解「提问 → 路由 → 取证 → 工单 → 会签 → 报告」这条主线；\n- **再看智能体清单**：18 个智能体各管什么、边界在哪；\n- **最后看治理与安全**：三套权限分域怎么分工。\n\n看不懂的名词，回到对应功能页实操一遍再回来——帮助中心是地图，不是教程。",
        },
        {
          q: "帮助中心讲什么？",
          a: "平台「是什么」的权威一屏：\n\n- 协同架构分层图（与代码现状对齐）；\n- 18 个智能体的意图、边界与 HITL 准则；\n- 可用工具清单（与 MCP / A2A 同源）；\n- 治理与安全设计（三套权限分域、全程可审计）。\n\n智能体与工具清单实时同步自后端，不是手抄的快照。",
        },
        {
          q: "内容会和实际不符吗？",
          a: "帮助中心是**架构事实的文档副本**：改了检索链路 / 路由 / 权限口径，必须同步改帮助中心——这是写死在开发纪律里的。\n\n拿不准某个细节时，帮助中心 + 本助手 + 后端接口三处对照，基本能定位。",
        },
      ],
    },

    s00: {
      name: "生态链场景总览",
      intro: "S1–S6 场景串成一条演示主线：接单 → 立项 → 阶段门 → 批量ECN → 出货追溯 → 采购执行。",
      faqs: [
        {
          q: "六个场景按什么顺序看？",
          a: "就按 **S1 → S6** 的编号顺序：这正是主线订单从接单到交付的生命周期——接单评审 → 立项 → 阶段门 → 批量与ECN → 出货追溯 → 采购执行。\n\n时间紧只看两个的话，看 **S1**（一笔订单怎么被评估着接进来）和 **S5**（出了客诉怎么全链路追溯）——一头一尾最有代表性。",
        },
        {
          q: "生态链场景是什么？",
          a: "六个场景屏（S1–S6）串成一条**演示主线**：接单评审 → 立项 → 阶段门会签 → 批量与ECN → 出货追溯 → 采购执行。\n\n每个场景解决一类真实协同问题，数据来自演示装载层，页面带「演示数据」徽标——看数可以，别当生产数据。",
        },
        {
          q: "为什么场景屏是只读的？",
          a: "场景屏面向**演示浏览**：把「问题怎么被发现、怎么被协同处理」讲清楚。\n\n真正的写操作与审批走审批确认中心的真实工单链路——演示归演示，治理归治理，两边不混。",
        },
        {
          q: "演示访客身份是什么？",
          a: "登录页「演示访客」分组有五个只读身份：总经理 / 业务 / 研发 / 质量 / 制造，各自看到对应场景与数据视图。\n\n访客只读：不能审批、不能写，适合走马观花看全链。",
        },
      ],
    },

    s01: {
      name: "S1 接单评审",
      intro: "订单接入、报价三分量评审与 ATP 可承诺量。",
      faqs: [
        {
          q: "什么是 ATP？",
          a: "**A**vailable **to** **P**romise，可承诺量 = 库存 + 在途 − 已占用。\n\n回答的是销售最关心的问题：**这单我现在敢不敢接**。接单评审时承诺量 > 订单量才敢接；它来自三来源现算（在库 / 在制 / 安全库存），不是拍脑袋的数字。",
        },
        {
          q: "报价三分量是什么？",
          a: "报价区间拆成三个分量展示：**成本**（底价）、**利润**（目标）、**风险**（不确定性缓冲）。\n\n意义在于评审时能逐项讨论——「让一点」到底让的是哪个分量，一目了然，而不是盯着一个总数吵架。",
        },
        {
          q: "ATP 可承诺量怎么用？",
          a: "ATP 结合库存与在途给出**可承诺数量**，接单评审时直接引用：承诺量 > 订单量才敢接。\n\n它是供应链域的口径，对客最终承诺仍走人工确认。",
        },
      ],
    },

    s02: {
      name: "S2 立项启动",
      intro: "NPI 立项：里程碑、样机与试产安排、跨部门任务分派。",
      faqs: [
        {
          q: "EVT / DVT / PVT / MP 是什么？",
          a: "新品导入的四个阶段，一路走下来才能量产：\n\n- **EVT**：工程验证——把设计做出来，验证功能可行；\n- **DVT**：设计验证——验证设计是否满足全部规格；\n- **PVT**：生产验证——用生产产线小批量跑，验证量产工艺；\n- **MP**：量产。\n\n每两个阶段之间有「阶段门」（见 S3），过了门才能转段。",
        },
        {
          q: "立项看什么？",
          a: "三件事：\n\n- **里程碑**：EVT / DVT / PVT / MP 各节点时间与状态；\n- **样机与试产**：数量、安排、负责部门；\n- **任务分派**：跨部门任务谁领、进度如何。\n\n立项通过后进入制样阶段门（S3），按阶段转段。",
        },
        {
          q: "转段谁说了算？",
          a: "阶段转段判定是 HITL 槽位：Agent 汇总各节点证据，放行由对应岗位人工确认——试产放行尤其如此，不靠「看起来差不多了」。",
        },
      ],
    },

    s03: {
      name: "S3 阶段门会签",
      intro: "制样阶段门多角色会签，缺一不可。",
      faqs: [
        {
          q: "什么是「阶段门」？",
          a: "Stage-Gate：每个阶段结束处设一道**门**，过门要两样东西：\n\n- **证据**：该阶段的记分卡达标（EVT / DVT / PVT 各有四维评分，现场现算）；\n- **会签**：规定角色全部签字，缺一不可。\n\n设计意图是不让「差不多就行」的项目流入下一阶段——每个阶段的钱和产能都锁在门后面。",
        },
        {
          q: "阶段门会签怎么过？",
          a: "规定角色对同一阶段门逐一背书，**缺一不可**：面板实时显示每个角色的签字状态与整体进度。\n\n会签不是形式——它是把「谁为这个转段负责」钉在纸面上。",
        },
        {
          q: "卡住了怎么办？",
          a: "看缺谁：面板列出缺失角色，到审批确认中心可找到对应工单并跟进（催办、查看 SLA）。\n\n会签未完成时场景入口会明确提示进度，不会让你干等。",
        },
      ],
    },

    s04: {
      name: "S4 批量监控与 ECN",
      intro: "工单批量进度、工序报工、设备状态与 ECN 影响面。",
      faqs: [
        {
          q: "什么是 ECN？",
          a: "**E**ngineering **C**hange **N**otice，工程变更通知：研发改了设计 / 物料 / 工艺，用 ECN 正式通知制造侧。\n\n平台的关键动作是**先算影响面再执行**：涉及哪些 BOM、在制工单、库存批次、在途物料，按对象分组摆出来——换一颗料牵动多少在制，看图就知道，不用等部门互相打电话确认。",
        },
        {
          q: "ECN 影响面怎么看？",
          a: "变更单（ECN）进来先算影响面：涉及哪些 BOM、在制工单、库存批次，按对象分组展示。\n\n研发域发起、制造域评估、质量域判定——跨域协同在这张图上完成，然后才谈执行。",
        },
        {
          q: "批量监控看什么？",
          a: "一屏总览批量生产：工单进度、工序报工完成率、设备状态与告警。\n\n异常工序可点开看逐日报工与过站记录——定位「卡在哪」不需要翻系统。",
        },
      ],
    },

    s05: {
      name: "S5 出货与质量追溯",
      intro: "批次双向追溯、8D 会签与出货风险。",
      faqs: [
        {
          q: "什么是 8D？",
          a: "客诉问题的结构化处置方法，8 个步骤（8 Disciplines）：组建团队 → 描述问题 → 临时围堵 → 找根因 → 纠正措施 → 效果验证 → 预防再发 → 关闭表彰。\n\n平台上的 8D 报告沿这条时间线组织；**关闭不是点个按钮**——纠正措施验证通过后仍需规定角色会签（HITL 槽位），这是「关不掉」的原因，也是设计意图。",
        },
        {
          q: "批次追溯怎么查？",
          a: "输入批次号，沿**来料 → 制程 → 出货**正向 / 反向追溯：经过哪些工单、工序、检验批，一链路展开。\n\n客诉场景下这是第一现场——先锁范围，再谈根因。",
        },
        {
          q: "8D 报告关不掉？",
          a: "8D 关闭是 HITL 槽位：纠正措施验证通过后，仍需规定角色会签才可关闭。\n\n「关不掉」通常是还有角色未签——去审批确认中心看进度，比反复点关闭有用。",
        },
      ],
    },

    s06: {
      name: "S6 采购执行",
      intro: "缺料缺口、催料进度与供应商绩效。",
      faqs: [
        {
          q: "什么是 SCAR？",
          a: "**S**upplier **C**orrective **A**ction **R**equest，供应商纠正措施要求：来料不合格时，正式要求供应商限期整改的文书。\n\n供应商绩效页会跟催每张 SCAR 的状态（发出 / 回复 / 验证 / 关闭）——SCAR 堆积多的供应商，来料合格率自然也好看不了。",
        },
        {
          q: "缺料怎么跟？",
          a: "缺料缺口按供应商汇总：差多少、预计哪天到、催到哪一步。\n\n缺口影响排产时，会联动工单进度——采购催料与制造排产在同一口径下对话。",
        },
        {
          q: "供应商绩效看什么？",
          a: "三个核心：**交期达成率**、**来料合格率**、**SCAR 跟催状态**。\n\n来料不合格处置（退换货 / 特采）是会签决策，采购智能体只汇总证据、发起工单。",
        },
      ],
    },
  };

  /** 全场景通用答疑（每个场景的选项列表末尾都会拼接）。 */
  var COMMON_FAQS = [
    {
      q: "这里看到的数据是真实的吗？",
      a: "分得清清楚楚，看标注：\n\n- 带**「演示」徽标 / 角标**的：演示数据（固定种子合成、可复现），不是生产实况；\n- 不带徽标的：后端在线时按真实链路现算（演示环境用的是种子数据）；\n- 后端离线时自动回落演示数据，并明确标注，绝不冒充实时。\n\n拿不准时认一个原则：**凡演示，必有标注**。",
    },
    {
      q: "界面能换深色 / 调大字号吗？",
      a: "可以，都在**顶栏右侧**：\n\n- 明暗切换按钮：一键换深浅主题；\n- 「Aa」按钮：字号分档（适合投屏 / 远看）。\n\n偏好记在本机浏览器，下次进来还是你调好的样子。",
    },
    {
      q: "如何切换岗位身份？",
      a: "点页面右上角头像 → 「切换岗位身份」，选一个岗位即可。\n\n切换后菜单、看板数据范围、待办列表、访问令牌**一起切换**——你立刻以新身份看数、以新身份被鉴权。演示环境免密码，随时可换。",
    },
    {
      q: "数据权限到底由谁把关？",
      a: "**后端**把关，前端只做提示。三套权限相互独立：\n\n- 业务数据：按数据范围目录裁决，未登记默认拒绝；\n- 治理待办：按 HITL 槽位持有权限裁决，跨域一律 404；\n- 案例访问：按授权状态过滤，无权案例不泄漏存在性。\n\n前端菜单隐藏只是展示收敛，不是安全边界——别把「看不到」当漏洞，也别把「菜单有」当权限。",
    },
    {
      q: "这个悬浮助手怎么用？",
      a: "三句话：\n\n- **拖**：按住助手按钮或面板顶栏，拖到任意位置；位置自动记忆，首次打开默认在最右边；\n- **认**：自动识别你当前所在的场景，给出该场景的常见问题；\n- **问**：点选项，预置答案快速流出；答完后选项还在，点「＋」重新展开。\n\n答过的选项可重复点，随时回看。",
    },
  ];

  /** 场景名兜底表（NAV_ITEMS / SceneRegistry 都拿不到时用，如后端未就绪的极早期）。 */
  var FALLBACK_NAMES = {
    dashboard: "工作台", orders: "订单履约看板", mfgboard: "制造综合看板", spc: "SPC 过程能力看板",
    alerts: "预警中心", chat: "协同问数", kbqa: "企业制度问答", cases: "制造案例检索",
    data: "数据速查", oa: "OA 协同", hitl: "审批确认中心", reports: "报告中心", help: "帮助中心",
    s00: "生态链场景总览", s01: "S1 接单评审", s02: "S2 立项启动", s03: "S3 阶段门会签",
    s04: "S4 批量监控与 ECN", s05: "S5 出货与质量追溯", s06: "S6 采购执行",
  };

  var ICON_HELP = '<svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" '
    + 'stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
    + '<path d="M9.5 9a2.5 2.5 0 1 1 3.6 2.24c-.7.4-1.1 1.02-1.1 1.76v.5"/>'
    + '<path d="M12 17h.01"/>'
    + '<circle cx="12" cy="12" r="9.2"/></svg>';
  var ICON_SPARK = '<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" '
    + 'stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
    + '<path d="M12 3l1.9 5.1L19 10l-5.1 1.9L12 17l-1.9-5.1L5 10l5.1-1.9L12 3z"/>'
    + '<path d="M19 15l.9 2.1L22 18l-2.1.9L19 21l-.9-2.1L16 18l2.1-.9L19 15z"/></svg>';

  /* ============================================================
   * 三、状态
   * ============================================================ */

  var st = {
    mounted: false,
    open: false,
    scene: "",            // 当前识别到的屏幕 id
    pos: null,            // FAB 左上角 {left, top}
    dragging: false,
    moved: false,
    optionsCollapsed: false,
    streaming: false,
    streamTimer: null,
    activeBubble: null,   // 正在流式输出的气泡（打断时摘光标用）
    els: {},              // DOM 引用
  };

  /* ============================================================
   * 四、工具
   * ============================================================ */

  function $(id) { return document.getElementById(id); }

  function esc(s) {
    if (s === null || s === undefined) return "";
    return String(s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }

  /** 极简排版：**加粗** / `代码` / "- " 列表 / 空行分段。
   *  流式输出时前缀可能截断在标记中间——未闭合的 ** 先按字面渲染，
   *  避免半对标记产出畸形标签。 */
  function inline(s) {
    return esc(s)
      .replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>")
      .replace(/\*\*([^*]*)$/, "$1")
      .replace(/`([^`]+)`/g, "<code>$1</code>");
  }

  function fmt(src) {
    var lines = String(src).split("\n");
    var out = "";
    var inList = false;
    var closeList = function () { if (inList) { out += "</div>"; inList = false; } };
    lines.forEach(function (ln) {
      if (/^\s*-\s+/.test(ln)) {
        if (!inList) { out += '<div class="fh-list">'; inList = true; }
        out += '<div class="fh-li">' + inline(ln.replace(/^\s*-\s+/, "")) + "</div>";
      } else if (ln.trim() === "") {
        closeList();
        out += '<div class="fh-gap"></div>';
      } else {
        closeList();
        out += '<div class="fh-p">' + inline(ln) + "</div>";
      }
    });
    closeList();
    return out;
  }

  /* ============================================================
   * 五、位置（持久化 + 默认最右 + 视口钳制）
   * ============================================================ */

  function loadPos() {
    try {
      const raw = localStorage.getItem(POS_KEY);
      if (!raw) return null;
      const p = JSON.parse(raw);
      if (!p || typeof p.left !== "number" || typeof p.top !== "number") return null;
      if (!isFinite(p.left) || !isFinite(p.top)) return null;
      return { left: p.left, top: p.top };
    } catch (err) {
      return null;   // 隐私模式 / 存储禁用 / JSON 损坏 → 用默认位
    }
  }

  function savePos(p) {
    try { localStorage.setItem(POS_KEY, JSON.stringify(p)); } catch (err) { /* ignore */ }
  }

  /** 首次打开（无存档）：默认到**最右边**——右下角逐屏校准（距边 EDGE）。 */
  function defaultPos() {
    return {
      left: Math.max(EDGE, window.innerWidth - FAB_SIZE - EDGE),
      top: Math.max(EDGE, window.innerHeight - FAB_SIZE - EDGE),
    };
  }

  /** 任何来源的位置都过钳制：改窗口大小 / 换分辨率后按钮不许飞出屏幕。 */
  function clampPos(p) {
    const maxLeft = Math.max(EDGE, window.innerWidth - FAB_SIZE - EDGE);
    const maxTop = Math.max(EDGE, window.innerHeight - FAB_SIZE - EDGE);
    return {
      left: Math.min(Math.max(EDGE, p.left), maxLeft),
      top: Math.min(Math.max(EDGE, p.top), maxTop),
    };
  }

  function applyPos() {
    const fab = st.els.fab;
    if (!fab || !st.pos) return;
    fab.style.left = st.pos.left + "px";
    fab.style.top = st.pos.top + "px";
    positionPanel();
  }

  /* ============================================================
   * 六、DOM 构建
   * ============================================================ */

  function buildDom() {
    const fab = document.createElement("div");
    fab.id = "fh-fab";
    fab.className = "fh-fab";
    fab.setAttribute("role", "button");
    fab.setAttribute("tabindex", "0");
    fab.setAttribute("aria-label", "帮助助手（可拖动）");
    fab.title = "帮助助手 · 按住可拖动";
    fab.innerHTML = ICON_HELP + '<span class="fh-fab-dot" id="fh-fab-dot" hidden></span>';

    const panel = document.createElement("div");
    panel.id = "fh-panel";
    panel.className = "fh-panel";
    panel.hidden = true;
    panel.innerHTML = `
      <div class="fh-head" id="fh-head" title="按住可拖动">
        <span class="fh-head-avatar">${ICON_SPARK}</span>
        <span class="fh-head-meta">
          <span class="fh-head-name">智造助手<span class="fh-head-tag">场景识别</span></span>
          <span class="fh-head-scene" id="fh-scene">当前场景 · 识别中…</span>
        </span>
        <button class="fh-head-close" id="fh-close" type="button" aria-label="收起助手" title="收起">×</button>
      </div>
      <div class="fh-body" id="fh-body" aria-live="polite"></div>
      <div class="fh-optbar" id="fh-optbar">
        <button class="fh-opt-toggle" id="fh-opt-toggle" type="button" aria-expanded="true">
          <span class="fh-opt-sign" id="fh-opt-sign">－</span>
          <span id="fh-opt-toggle-text">常见问题</span>
        </button>
        <div class="fh-opts" id="fh-opts"></div>
      </div>`;

    document.body.appendChild(fab);
    document.body.appendChild(panel);
    st.els = {
      fab, panel,
      dot: $("fh-fab-dot"),
      head: $("fh-head"),
      close: $("fh-close"),
      scene: $("fh-scene"),
      body: $("fh-body"),
      optbar: $("fh-optbar"),
      optToggle: $("fh-opt-toggle"),
      optSign: $("fh-opt-sign"),
      optToggleText: $("fh-opt-toggle-text"),
      opts: $("fh-opts"),
    };
  }

  /* ============================================================
   * 七、场景识别
   * ============================================================ */

  /** 当前屏幕 id：唯一事实源是 window.App.state.screen（含 SceneRegistry 屏）。 */
  function currentScreenId() {
    try {
      if (window.App && window.App.state && window.App.state.screen) {
        return String(window.App.state.screen);
      }
    } catch (err) { /* 落到 hash */ }
    const hash = (location.hash || "").replace(/^#\/?/, "");
    return hash || "dashboard";
  }

  /** 是否已进入应用（登录页 / 未登录时不显示助手）。 */
  function inApp() {
    const loginRoot = $("login-root");
    if (loginRoot && loginRoot.hidden === false) return false;
    try {
      return !!(window.App && window.App.state && window.App.state.role);
    } catch (err) {
      return false;
    }
  }

  /** 场景元信息：先查导航表（含场景屏），再查知识库，最后兜底名。 */
  function sceneMeta(id) {
    let name = "";
    try {
      if (window.App && Array.isArray(window.App.NAV_ITEMS)) {
        const item = window.App.NAV_ITEMS.find((n) => n.id === id);
        if (item) name = item.label || item.title || "";
      }
      if (!name && window.SceneRegistry && typeof window.SceneRegistry.get === "function") {
        const def = window.SceneRegistry.get(id);
        if (def) name = def.label || def.title || "";
      }
    } catch (err) { /* 用兜底 */ }
    if (!name) name = (KB[id] && KB[id].name) || FALLBACK_NAMES[id] || "当前页面";
    const intro = (KB[id] && KB[id].intro) || "";
    return { id, name, intro };
  }

  /** 某场景的答疑选项：场景专属 + 全场景通用。 */
  function faqsFor(id) {
    const own = (KB[id] && KB[id].faqs) ? KB[id].faqs.slice() : [];
    if (KB[id]) return own.concat(COMMON_FAQS);
    // 未知识库化的屏：只给通用答疑（绝不编造场景专属内容）
    return COMMON_FAQS.slice();
  }

  function syncScene() {
    if (!st.mounted) return;
    const fab = st.els.fab;
    if (!inApp()) {
      fab.style.display = "none";
      closePanel();
      return;
    }
    if (fab.style.display === "none") fab.style.display = "";

    const id = currentScreenId();
    if (id === st.scene) return;
    st.scene = id;
    const meta = sceneMeta(id);
    st.els.scene.textContent = "当前场景 · " + meta.name;

    // 切换场景 = 开新会话：历史问答（含未播完的流式输出）全部清空，
    // 只保留当前场景的问候语与选项——旧场景的上下文不该串到新场景。
    stopStream();
    clearMessages();
    renderOptions();
    setOptionsCollapsed(false);
    if (st.open) {
      pushBotMessage(sceneWelcome(meta));
      scrollBody();
    } else {
      showDot(true);   // 面板关着：按钮上亮小点，打开时补问候语
    }
  }

  function sceneWelcome(meta) {
    const intro = meta.intro ? "\n\n" + meta.intro : "";
    return "你好，我是智造助手。已识别当前场景：**" + meta.name + "**。" + intro
      + "\n\n下面列出了本场景的常见问题，点一下直接讲清楚；按住我或面板顶栏可以拖到顺手的位置。";
  }

  /* ============================================================
   * 八、消息渲染
   * ============================================================ */

  /**
   * 消息区滚动。默认只在「本来就贴着底部」时才自动跟底：
   * 流式输出期间用户上滑回看时，不每一帧把人拽回底部（force=true
   * 用于新消息入场的强制滚动，那一步用户预期就是看到最新内容）。
   */
  function scrollBody(force) {
    const b = st.els.body;
    if (!b) return;
    if (!force) {
      const nearBottom = b.scrollHeight - b.scrollTop - b.clientHeight < 48;
      if (!nearBottom) return;
    }
    b.scrollTop = b.scrollHeight;
  }

  /** 清空会话区（切场景 / 重新开场时用）。 */
  function clearMessages() {
    if (st.els.body) st.els.body.innerHTML = "";
  }

  function pushUserMessage(text) {
    const div = document.createElement("div");
    div.className = "fh-msg fh-msg-user";
    div.innerHTML = '<div class="fh-bubble">' + esc(text) + "</div>";
    st.els.body.appendChild(div);
    scrollBody(true);
    return div;
  }

  function pushBotMessage(text) {
    const div = document.createElement("div");
    div.className = "fh-msg fh-msg-bot";
    div.innerHTML = '<span class="fh-avatar">' + ICON_SPARK + "</span>"
      + '<div class="fh-bubble">' + fmt(text) + "</div>";
    st.els.body.appendChild(div);
    scrollBody(true);
    return div;
  }

  /* ============================================================
   * 九、流式输出（预置答案 → 快速逐字流出）
   * ============================================================ */

  function stopStream() {
    if (st.streamTimer) {
      clearInterval(st.streamTimer);
      st.streamTimer = null;
    }
    st.streaming = false;
    // 打断收尾：被中断的气泡里可能还挂着流式光标（▍），不移除会永远闪烁。
    // 正常播完时 stopStream 先于最终 innerHTML 覆盖执行，这里摘掉也无副作用。
    if (st.activeBubble) {
      const caret = st.activeBubble.querySelector(".fh-caret");
      if (caret) caret.remove();
      st.activeBubble = null;
    }
  }

  /**
   * 把预置答案流式写进 bubble。
   * 每次重渲染整个前缀——答案都是几百字的短文本，代价可忽略；
   * 换来的是格式（加粗 / 列表 / 分段）随文本增长自然成形。
   */
  function streamAnswer(text, bubble, done) {
    stopStream();
    st.streaming = true;
    st.activeBubble = bubble;   // 打断时用于摘除残留光标
    let i = 0;
    const paint = () => {
      bubble.innerHTML = fmt(text.slice(0, i)) + '<span class="fh-caret"></span>';
      scrollBody();            // 非强制：用户上滑回看时不拽回底部
    };
    paint();
    st.streamTimer = setInterval(() => {
      i = Math.min(text.length, i + STREAM_CHUNK);
      paint();
      if (i >= text.length) {
        stopStream();
        bubble.innerHTML = fmt(text);
        scrollBody();
        if (typeof done === "function") done();
      }
    }, STREAM_TICK);
  }

  /** 点击一个选项：用户气泡 + 助手流式作答；答完后选项保留并自动收起。 */
  function ask(faq) {
    if (!st.open) openPanel();
    stopStream();
    pushUserMessage(faq.q);
    const bubble = document.createElement("div");
    bubble.className = "fh-bubble";
    const wrap = document.createElement("div");
    wrap.className = "fh-msg fh-msg-bot";
    wrap.innerHTML = '<span class="fh-avatar">' + ICON_SPARK + "</span>";
    wrap.appendChild(bubble);
    st.els.body.appendChild(wrap);
    scrollBody();
    streamAnswer(faq.a, bubble, () => setOptionsCollapsed(true));
  }

  /* ============================================================
   * 十、选项区（+/－ 折叠展开）
   * ============================================================ */

  function renderOptions() {
    const faqs = faqsFor(st.scene);
    st.els.opts.innerHTML = faqs.map((f, i) =>
      '<button class="fh-opt" type="button" data-idx="' + i + '">'
      + '<span class="fh-opt-q">' + esc(f.q) + "</span>"
      + '<span class="fh-opt-go">›</span></button>'
    ).join("");
    st.els.opts.querySelectorAll(".fh-opt").forEach((btn) => {
      btn.addEventListener("click", () => {
        const faq = faqs[Number(btn.dataset.idx)];   // 闭包持有渲染时的列表，避免点击时二次取
        if (faq) ask(faq);
      });
    });
    st.els.optToggleText.textContent = "常见问题（" + faqs.length + "）";
  }

  function setOptionsCollapsed(collapsed) {
    st.optionsCollapsed = !!collapsed;
    st.els.optbar.classList.toggle("collapsed", st.optionsCollapsed);
    st.els.optToggle.setAttribute("aria-expanded", String(!st.optionsCollapsed));
    st.els.optSign.textContent = st.optionsCollapsed ? "＋" : "－";
    if (!collapsed) scrollBody();
  }

  /* ============================================================
   * 十一、面板开合与定位
   * ============================================================ */

  function showDot(on) {
    if (st.els.dot) st.els.dot.hidden = !on;
  }

  function positionPanel() {
    const panel = st.els.panel;
    const fab = st.els.fab;
    if (!panel || panel.hidden || !fab || !st.pos) return;
    const pw = panel.offsetWidth || 364;
    const ph = panel.offsetHeight || 480;
    const vw = window.innerWidth;
    const vh = window.innerHeight;
    // 水平：按钮右侧放得下就向右展开，否则向左；两侧都紧贴边缘时钳制。
    let left;
    if (st.pos.left + FAB_SIZE + 12 + pw <= vw - 8) {
      left = st.pos.left + FAB_SIZE + 12;
    } else {
      left = Math.max(8, st.pos.left - 12 - pw);
    }
    // 垂直：与按钮居中对齐，再钳制在视口内。
    let top = st.pos.top + FAB_SIZE / 2 - ph / 2;
    top = Math.min(Math.max(8, top), Math.max(8, vh - ph - 8));
    panel.style.left = left + "px";
    panel.style.top = top + "px";
  }

  function openPanel() {
    if (!st.mounted || st.open) return;
    st.open = true;
    st.els.panel.hidden = false;
    showDot(false);
    if (!st.scene) syncScene();
    // 会话区为空（首次打开 / 刚切换场景被清空）→ 补当前场景的问候语；
    // 同场景内关闭再打开保留原会话，不重置。
    if (st.els.body && !st.els.body.firstElementChild) {
      pushBotMessage(sceneWelcome(sceneMeta(st.scene)));
    }
    setOptionsCollapsed(false);   // 每次打开：选项展开，先让用户看到能问什么
    positionPanel();
  }

  function closePanel() {
    if (!st.mounted || !st.open) return;
    st.open = false;
    st.els.panel.hidden = true;
    stopStream();
  }

  function togglePanel() {
    if (st.open) closePanel(); else openPanel();
  }

  /* ============================================================
   * 十二、拖动（FAB 与面板顶栏都是抓取区）
   * ============================================================ */

  /**
   * 指针拖动统一入口。点击（位移 < 阈值）与拖动分离：
   * 拖完松手不触发热键区的 click——否则"拖到一半松手"会意外开合面板。
   */
  function wireDrag(handle, isFab) {
    let armed = false;          // pointerdown 成功登记后才响应 move/up
    let startX = 0, startY = 0, originLeft = 0, originTop = 0;

    handle.addEventListener("pointerdown", (e) => {
      if (e.button !== 0 && e.pointerType === "mouse") return;
      // 交互子元素（顶栏的 × 关闭按钮等）不启动拖动：一旦 setPointerCapture，
      // 随后的 pointerup / click 都会被重定向到顶栏本身，按钮的 click 监听
      // 永远收不到——表现为「点 × 关不掉」。
      if (e.target && typeof e.target.closest === "function" && e.target.closest("button")) return;
      st.dragging = false;
      st.moved = false;
      armed = true;             // 哨兵用布尔量：用 (0,0) 坐标当哨兵会在
      startX = e.clientX;       // 屏幕原点按下时误判「没按过」
      startY = e.clientY;
      originLeft = st.pos.left;
      originTop = st.pos.top;
      try { handle.setPointerCapture(e.pointerId); } catch (err) { /* 老浏览器忽略 */ }
    });

    handle.addEventListener("pointermove", (e) => {
      if (!armed) return;
      const dx = e.clientX - startX;
      const dy = e.clientY - startY;
      if (!st.moved && Math.abs(dx) + Math.abs(dy) < DRAG_THRESHOLD) return;
      if (!st.dragging) {
        st.dragging = true;
        document.body.classList.add("fh-dragging");
        st.els.fab.classList.add("fh-grab");
      }
      st.moved = true;
      st.pos = clampPos({ left: originLeft + dx, top: originTop + dy });
      applyPos();
      e.preventDefault();
    });

    const finish = (e) => {
      if (!armed) return;
      armed = false;
      const wasDragging = st.dragging;
      st.dragging = false;
      document.body.classList.remove("fh-dragging");
      st.els.fab.classList.remove("fh-grab");
      try { handle.releasePointerCapture(e.pointerId); } catch (err) { /* ignore */ }
      if (wasDragging) {
        savePos(st.pos);          // 落盘：下次打开（含刷新）还在原处
        st.moved = false;
        return;                    // 拖动结束：不触发热键
      }
      if (isFab && !st.moved) togglePanel();
      st.moved = false;
    };

    handle.addEventListener("pointerup", finish);
    handle.addEventListener("pointercancel", (e) => {
      armed = false;
      st.dragging = false;
      document.body.classList.remove("fh-dragging");
      st.els.fab.classList.remove("fh-grab");
      try { handle.releasePointerCapture(e.pointerId); } catch (err) { /* ignore */ }
    });

    // 键盘可达：Enter / 空格 = 开合（FAB 上）
    if (isFab) {
      handle.addEventListener("keydown", (e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          togglePanel();
        }
      });
    }
  }

  /* ============================================================
   * 十三、装配
   * ============================================================ */

  function wire() {
    st.els.close.addEventListener("click", closePanel);
    st.els.optToggle.addEventListener("click", () => setOptionsCollapsed(!st.optionsCollapsed));

    // 点面板外关闭（FAB 除外——它有自己的 toggle；选项点击不关闭）。
    // 侧边栏导航除外：助手要跨场景常驻——切场景时它自己清场换问候语，
    // 一导航就关会让「切换场景后的新内容」永远看不到。
    document.addEventListener("click", (e) => {
      if (!st.open) return;
      const t = e.target;
      if (st.els.panel.contains(t) || st.els.fab.contains(t)) return;
      if (t && typeof t.closest === "function" && t.closest(".nav-item")) return;
      closePanel();
    });

    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && st.open) closePanel();
    });

    window.addEventListener("resize", () => {
      st.pos = clampPos(st.pos);
      applyPos();
    });

    wireDrag(st.els.fab, true);
    wireDrag(st.els.head, false);
  }

  function boot() {
    if (st.mounted) return;
    if (!document.body) return;
    st.mounted = true;
    buildDom();
    st.pos = clampPos(loadPos() || defaultPos());
    applyPos();
    wire();
    // 契约：每次刷新 / 启动，窗口默认关闭（只留悬浮球）。CSS 侧另有
    // .fh-panel[hidden]{display:none} 兜底——单靠 hidden 属性压不住
    // display:flex， historically 这就是「刷新后窗口挂在左上角」的根因。
    closePanel();
    syncScene();
    setInterval(syncScene, SCENE_TICK_MS);
  }

  /* ============================================================
   * 十四、导出（调试 / 测试钩子）
   * ============================================================ */

  window.FloatHelp = {
    mount: boot,
    open: openPanel,
    close: closePanel,
    toggle: togglePanel,
    /** 当前状态快照（测试与排查看这个，不用翻 DOM）。 */
    snapshot() {
      return {
        mounted: st.mounted,
        open: st.open,
        scene: st.scene,
        sceneName: sceneMeta(st.scene).name,
        pos: { left: st.pos.left, top: st.pos.top },
        optionsCollapsed: st.optionsCollapsed,
        streaming: st.streaming,
        faqCount: faqsFor(st.scene).length,
      };
    },
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
