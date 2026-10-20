# STORY.md · 《让工厂知识随问随答》RAG 产品讲解（20 页）

## ① 用户意图对齐

这份 PPT 用于 **RAG 培训课程的现场讲解**：观众是培训学员与讲师，多数人懂一点 AI 概念但没亲手搭过完整 RAG 链路；讲者是产品独立开发者（Hulk），明天现场讲解。核心目标：讲完后观众要**相信**这个产品是从工厂真实痛点里长出来的、每一条架构决策都有评测数据背书；**记住**三层链路（L0 缓存 → L1 双闸速查 → 意图路由 → L2 RAG）与两个关键阈值（0.70 / 0.15）；**带走**一套可复刻的搭建方法。总长 20 页，Hero 页配额 4 页（20%）。视觉调性：**科技蓝 / 明亮清晰 / 制造业专业 / 数据驱动 / 零基础友好**。内容边界：必讲产品初衷、RAG 模块架构与搭建思路；多 Agent 一页带过（讲者原话「Agent 还没学，先加了很多 Agent」）；生态链场景未开发完，**全篇不写**；不写未上线的承诺。

## ② 页面布局骨架

全篇 20 页、3 章。目录声明 3 章 → 全篇有且仅有 3 个 `type: section` 扉页，编号 01–03 连续，标题与页码区间和目录逐字一致：

| 目录条目 | 章节扉页 | 覆盖页码 |
| :--- | :--- | :--- |
| 01 产品初衷：为什么做这个产品 | P03 | P04–P06 |
| 02 RAG 模块：三层架构与搭建思路 | P07 | P08–P16 |
| 03 多 Agent 与总结：协同与质量基线 | P17 | P18–P20 |

- **Hero 页**：P01 封面、P08 三层动机、P15 效果数据、P20 结束 = 4/20 = 20%，两两不相邻。
- **rhythm 曲线**：peak = 01/06/08/10/13/15/20；valley = 02/04/05/09/11/12/14/16/18/19；transition = 03/07/17。无连续 ≥3 页 valley。
- **非对称版式预算**：16 个内容页中 12 页非对称（75%，≥40% 达标）；`左大图+右侧文字`(P05/P18) + `非对称双栏`(P04/P10/P13/P19) 合计 6 页 = 30% ≤ 40%。
- **对称版式预算**：仅 2 个内容页用对称（P08、P15 均为「巨型数字+洞察」）；`N卡片横排` 全篇 0 次。封面/扉页/结束页按母版规则使用自定义版式，不占内容页对称预算。

## ③ 页面大纲

### P01 · 封面
- title: 让工厂知识随问随答
- type: cover ｜ role: hero ｜ rhythm: peak ｜ layout: 全幅图+骑线文字
- visual: L1: cover.png（全幅工厂+数据流，深色蒙版，文字左侧骑入）｜ visual_role: anchor
- density: 字数约 70 / 图片 1 张 / 留白约 35%
- anti_pattern: 禁止标题块右侧塞 200×70 装饰小图；禁止把产品名做成比主题大的主角
- description: 副标「RAG 模块的技术实现与搭建思路 · 独立开发实践分享」，落款培训讲解 / Hulk / 2026-10-21。封面即立论：这是一款从工厂真实痛点里长出来的 RAG 产品，今天只讲三件事——为什么做、RAG 怎么搭、多 Agent 什么关系。

### P02 · 目录
- title: 今天讲什么
- type: catalog ｜ role: supporting ｜ rhythm: valley ｜ layout: 左标题+右内容
- visual: L3: 页脚徽标（全篇一致）｜ visual_role: evidence
- density: 字数约 140 / 图片 0 张 / 留白约 25%
- anti_pattern: 禁止四卡片横排当目录；禁止只列章节名不给一句话说明
- description: 三章各配一句导读：初衷讲「问题从哪来」，RAG 讲「每层怎么实现、怎么搭起来」，多 Agent 讲「和 RAG 什么关系、质量底线在哪」。

### P03 · 章节扉页 01
- title: 01 产品初衷：为什么做这个产品（P04–P06）
- type: section ｜ role: transition ｜ rhythm: transition ｜ layout: 全屏视觉+大标题
- visual: L1: sec1.png（工厂车间质检场景全幅）｜ visual_role: atmosphere
- density: 字数约 45 / 图片 1 张 / 留白约 45%（扉页不受常规页限制）
- anti_pattern: 禁止铺满正文段落；禁止四卡片预览
- description: 一句转场：「先讲清楚问题，再讲方案——没有痛点的架构是空中楼阁。」

### P04 · 三个痛点
- title: 制造企业的知识，卡在三个地方
- type: content ｜ role: supporting ｜ rhythm: valley ｜ layout: 非对称双栏（65:35）
- visual: L1: 左侧三条痛点条（FAIcon + 序号）+ 右栏巨型数字「3」｜ visual_role: evidence
- density: 字数约 205 / 图片 0 张（图标+大数字）/ 留白约 28%
- anti_pattern: 禁止三等宽卡片横排；禁止把痛点写成无判断的名词短语
- description: 三条痛点全部来自一线口径：① 跨部门同步以天计——进度散落在各部门系统与微信群里；② 取数三件套——问人、导 Excel、写 SQL，一个数等半天；③ 取证靠打电话——停线/缺料/客诉闭环周期长，且结论留在聊天记录里，同类问题第二次发生时系统没有任何记忆。**判断：不是缺工具，是缺一条把「提问 → 取证 → 决策 → 留痕」串起来的链路。**

### P05 · 产品是什么
- title: 一个平台，把异常响应收敛成一条可审计链路
- type: content ｜ role: supporting ｜ rhythm: valley ｜ layout: 左大图+右侧文字（图占 58%）
- visual: L1: kbqa_screen.png（制度问答真实界面截图，答案带出处）｜ visual_role: evidence
- density: 字数约 195 / 图片 1 张 / 留白约 25%
- anti_pattern: 禁止 50:50 等分双栏；禁止把产品截图缩成小尺寸贴片
- description: 右侧一页看懂产品：链路六步「提问 → 路由 → 多域并行取证 → 工单 → 人工会签 → 对客报告」，每步有事件流、SQL 溯源与权限校验；数字条 18 智能体 / 7 协同场景 / 31 岗位 / 13 主导航屏。**判断：先把「能办成事、能留痕」做扎实，才谈更智能——这是产品的第一性原理。**

### P06 · 设计原则
- title: 初衷落到工程：四条验收级的设计原则
- type: content ｜ role: supporting ｜ rhythm: peak ｜ layout: 左标题+右内容
- visual: L1: 四条巨型序号 01–04 + 左侧色标栏 ｜ visual_role: evidence
- density: 字数约 215 / 图片 0 张 / 留白约 25%
- anti_pattern: 禁止四条无差别平铺（必须用序号+色标拉开层级）；禁止空喊原则不给工程落点
- description: ① 数据质量优先于智能能力；② 私有化部署，数据不出厂；③ 关键决策人在环路（HITL 会签）；④ CPK/SPC 统计量一律代码计算，LLM 只做解读。每条配工程落点（只读连接、会签幂等、data_gap 强制人工）。**判断：可验收的初衷才是真初衷——四条全部来自架构文档原文，不是口号。**

### P07 · 章节扉页 02
- title: 02 RAG 模块：三层架构与搭建思路（P08–P16）
- type: section ｜ role: transition ｜ rhythm: transition ｜ layout: 全屏视觉+大标题
- visual: L1: sec2.png（抽象发光知识网络全幅）｜ visual_role: atmosphere
- density: 字数约 50 / 图片 1 张 / 留白约 45%
- anti_pattern: 禁止铺满正文段落；禁止提前展开技术细节
- description: 一句转场：「今天的主场——一个知识问答模块，怎么从『能答』做到『答得准、答得快、答得有出处』。」

### P08 · 为什么是三层
- title: 单一 RAG 的两个坑：答得慢，或答得偏
- type: content ｜ role: hero ｜ rhythm: peak ｜ layout: 巨型数字+洞察（对称 1/2）
- visual: L1: 左右两个 72px+ 巨型数字「0ms 答偏」vs「2.6s 答对」｜ visual_role: anchor
- density: 字数约 175 / 图片 0 张 / 留白约 40%（hero 允许）
- anti_pattern: 禁止把两个数字塞进等宽卡片；禁止 L3 角标顶替 L1；禁止只摆数字不给判断
- description: 核心命题「0ms 答偏比 2.6s 答对更糟」。两个坑：① 速查卡（<60 字）与完整文档严重不对称——问「年假怎么休」时 FAQ 命中反而答不全；② 用 LL 分类意图要 ~1.4s，属负优化，词表路由是唯一 viable 方案。**判断：三层分工不是架构炫技，是拿两个坑换来的——便宜的路由先跑（0.3ms），贵的检索兜底（~2.6s）。**

### P09 · 三层架构总览
- title: 三层链路：L0 缓存 → L1 速查 → 意图路由 → L2 RAG
- type: content ｜ role: supporting ｜ rhythm: valley ｜ layout: 上大图+下方卡片
- visual: L1: 全宽 SVG 架构图（占 B 区约 55%，每层带延迟标注）｜ visual_role: evidence
- density: 字数约 235 / 图片 0 张（SVG 主视觉）/ 留白约 22%
- anti_pattern: 禁止用 N 卡片横排充当架构图；禁止架构图无层级/无延迟标注
- description: 全链路图：用户问题 → L0 Redis 缓存 → L1 FAQ 双闸 → 意图路由（域内→L2 / 域外→direct，how_to 让位 RAG）→ L2 Milvus 混合召回+重排 → 带出处生成。下方四张责任卡各带一句职责 + 延迟证据 + 源码文件。**判断：每层只干自己擅长的，贵的后置——这是全部设计的出发点。**

### P10 · L1 双闸
- title: L1 · FAQ 双闸匹配：答得快，更要答得准
- type: content ｜ role: supporting ｜ rhythm: peak ｜ layout: 非对称双栏（60:40）
- visual: L1: SVG 双闸计分条（流程卡 1.0 vs 单事实卡 0.8，margin 0.2 > 0.15）｜ visual_role: evidence
- density: 字数约 210 / 图片 0 张 / 留白约 24%
- anti_pattern: 禁止把评分公式堆成代码块不解释；禁止左右等分
- description: 评分 = 关键词×2（封顶 6）+ 标题×3 + 内容×1，除以 6 归一；双闸 = score ≥ 0.70 且 top1−top2 ≥ 0.15。三条设计理由：① margin 防「看起来像」的歧义命中；② 单 2 字符关键词不能独自过闸；③ 流程维度衰减 ×0.8——问流程时流程卡压过单事实卡，margin 拉到 0.2。**判断：阈值不是玄学，是拿 badcase 一条条标定出来的——流程类 10/10、事实类 8/8。**

### P11 · 意图路由
- title: 意图路由：sub-毫秒决定「问知识还是问模型」
- type: content ｜ role: supporting ｜ rhythm: valley ｜ layout: 左标题+右内容
- visual: L1: 路由决策 SVG（L1 miss → 域内词? → RAG / 域外 → direct）｜ visual_role: evidence
- density: 字数约 200 / 图片 0 张 / 留白约 26%
- anti_pattern: 禁止把 124 个词表铺成表格；禁止只说「用了 NLP」不说为什么不用 LLM
- description: 路由是纯函数、sub-ms、零外部调用：124 个域内词（CJK 子串）+ 24 个拉丁缩写（词边界正则防 MES 命中 TIMES）；域内→RAG 保出处，域外→约束 LLM 直答省 ~1.6s；how_to（怎么/如何/怎样）让位 RAG，RAG miss 再回落 L1 并明示未找到完整流程。**判断：路由层的价值是「别让贵路径处理便宜问题」。**

### P12 · L2 RAG
- title: L2 · RAG：混合召回 + 重排 + 带出处生成
- type: content ｜ role: supporting ｜ rhythm: valley ｜ layout: 上大图+下方卡片
- visual: L1: 检索漏斗 SVG（双路召回 → 混合排序 → 重排 → top_n）｜ visual_role: evidence
- density: 字数约 220 / 图片 0 张 / 留白约 22%
- anti_pattern: 禁止只写「用了向量数据库」不写召回/重排细节；禁止漏斗无数字
- description: BGE-M3 dense（1024 维）+ sparse 双路混合召回，WeightedRanker 0.7/0.3，候选 max(12, top_k×3) → BGE-Reranker-v2-M3 CrossEncoder 重排取 top_n；chunk 按段 400 字符、超长按句切，metadata 带部门/来源；生成强制带 [文件名 第N段] 出处（temperature 0.3、max_tokens 400）；LLM 不可用回放最相关片段并标 degraded。**判断：双路召回解决「说得不一样」，重排解决「相关的不一定最相关」——没有 rerank 的 RAG 是把半成品递给用户。**

### P13 · L0 缓存
- title: L0 · 缓存与版本治理：改一行知识，缓存自动失效
- type: content ｜ role: supporting ｜ rhythm: peak ｜ layout: 非对称双栏（65:35）
- visual: L1: 缓存键结构 SVG + 19ms 回放大数字 ｜ visual_role: evidence
- density: 字数约 190 / 图片 0 张 / 留白约 25%
- anti_pattern: 禁止把缓存写成「加了个缓存」一句带过；禁止左右等分
- description: 键结构 `mfg:kbqa:v2:{faq_version}:{sha1(query|top_k)}`，TTL 1800s，只包 L2/direct 贵路径；faq_version = `policy:{count}:{max_id}|faq:{count}:{max_id}`——FAQ 任何写操作换命名空间，旧键自然不可达，无需主动失效；rag_unavailable / degraded 状态主动删除不缓存。**判断：缓存难点从来不是「存」是「何时失效」——19ms 回放不是优化技巧，是版本治理的副产品。**

### P14 · 降级链
- title: 兜底链：每一级降级都「如实标注」
- type: content ｜ role: supporting ｜ rhythm: valley ｜ layout: 左标题+右内容
- visual: L1: 五级降级链 SVG（自上而下权重递减）｜ visual_role: evidence
- density: 字数约 200 / 图片 0 张 / 留白约 26%
- anti_pattern: 禁止把降级画成与正常流程同权重；禁止五级并列无层级
- description: 五级兜底：L1 库挂→跳过前置层照常答；Milvus 挂→rag_unavailable 且不缓存；LLM 挂→回放最相关片段 degraded=True；RAG miss + how_to→回落 L1 并明示未找到完整流程；遥测写失败只告警不抛。**判断：demo 可以降级，但不能撒谎——LLM 全留空的离线模式也能跑通全链路，这是可演示的底线。**

### P15 · 效果数据
- title: 效果：快与准，可以兼得
- type: content ｜ role: hero ｜ rhythm: peak ｜ layout: 巨型数字+洞察（对称 2/2）
- visual: L1: 三个 72px+ 巨型数字（7ms / 2.7s / 1.8s）｜ visual_role: anchor
- density: 字数约 165 / 图片 0 张 / 留白约 40%
- anti_pattern: 禁止把三个数字装进等宽卡片；禁止数字无单位无使用场景
- description: 全部 2026-10-18 本机实测：FAQ 命中 7ms 且 0 次 LLM 调用（「年假怎么休」confidence 1.0）；域内模糊 RAG 全程 2722ms；域外 direct 1812ms 且未跑 Milvus（省 ~1.6s）；验收：流程类 10/10、事实类 8/8，「怎么报销」RAG 113ms。**判断：7ms 和 2.7s 不是矛盾，是分工——便宜的路由先跑，贵的检索兜底。**

### P16 · 搭建思路
- title: 搭建思路：五条可复刻的工程方法
- type: content ｜ role: supporting ｜ rhythm: valley ｜ layout: 左标题+右内容
- visual: L1: 五条巨型序号 01–05 + 方法标签 ｜ visual_role: evidence
- density: 字数约 230 / 图片 0 张 / 留白约 24%
- anti_pattern: 禁止五条写成鸡汤（每条必须带「我怎么做的」）；禁止等宽五卡
- description: ① 契约先行——一个 answer() 统一入口 + 统一返回契约；② 纯函数可离线测——匹配/路由零外部依赖，sub-ms；③ 阈值用评测标定——双闸/衰减/词表全部来自 badcase；④ 测试守护行为——28 条双闸 + 17 条路由，Redis 隔离防假绿、炸弹函数防「假装调了检索」；⑤ 文档即架构——ADR-001 把决策写进仓库。**判断：RAG 的上限在模型，下限在工程；把工程做脏一点，演示才干净。**

### P17 · 章节扉页 03
- title: 03 多 Agent 与总结：协同与质量基线（P18–P20）
- type: section ｜ role: transition ｜ rhythm: transition ｜ layout: 全屏视觉+大标题
- visual: L1: sec3.png（抽象多智能体星链网络全幅）｜ visual_role: atmosphere
- density: 字数约 55 / 图片 1 张 / 留白约 45%
- anti_pattern: 禁止铺满正文段落；禁止展开七个场景细节
- description: 一句转场：「Agent 我还在学——但先讲清楚它和 RAG 的关系：知识底座稳了，编排才立得住。」

### P18 · 多 Agent 一页看懂
- title: 多 Agent 模块：18 个智能体怎么协同
- type: content ｜ role: supporting ｜ rhythm: valley ｜ layout: 左大图+右侧文字（图占 55%）
- visual: L1: 编排链 SVG（YAML 场景 → LangGraph 子图 → 域 Agent 并行取证 → 工单 → 会签）｜ visual_role: evidence
- density: 字数约 195 / 图片 0 张 / 留白约 26%
- anti_pattern: 禁止展开讲 7 个场景细节（一页带过）；禁止把 18 个智能体列成表格
- description: 18 = 17 域智能体 + 1 办公智能体；场景不写代码——YAML 声明节点与依赖，加载期编译成 LangGraph 子图；HITL：9 槽位 × 3 动词 = 27 条权限，Agent 身份只允许 *.propose，持 approve 启动即失败；MCP / A2A 双协议走同一套治理。**判断：多 Agent 是「编排」不是「堆数量」——RAG 给每个 Agent 提供知识与出处，Agent 把知识用成决策。**

### P19 · 协同与质量基线
- title: RAG × Agent：知识底座 + 质量基线
- type: content ｜ role: supporting ｜ rhythm: valley ｜ layout: 非对称双栏（60:40）
- visual: L1: 协同关系 SVG（Agent 取证 → 查 FAQ/RAG → 引用三元组 → 无引用不输出）｜ visual_role: evidence
- density: 字数约 205 / 图片 0 张 / 留白约 25%
- anti_pattern: 禁止左右等分；禁止只讲测试数量不讲守护了什么行为
- description: 协同：域 Agent 取证时查制度 FAQ / RAG，结论必须挂 (case_id, chunk_id, content_version) 引用三元组，无引用不输出；案例检索两段式授权（过滤条件下推向量库 + 关系库二次授权），候选不足绝不放宽。质量基线：1896 条离线测试、CI 3.11/3.12/3.13、测试隔离 Redis 缓存防假绿、架构守卫 fail-closed。**判断：测试数不是 KPI，是被守住的行为清单——守护的是「不许撒谎」。**

### P20 · 结束页
- title: 先让答案有出处，再让系统有记忆
- type: ending ｜ role: hero ｜ rhythm: peak ｜ layout: 居中金句（自定义）
- visual: L1: ending.png（清晨工厂天际线全幅 + 深色蒙版）｜ visual_role: atmosphere
- density: 字数约 85 / 图片 1 张 / 留白约 45%（hero 允许）
- anti_pattern: 禁止「谢谢观看」四个字裸奔；禁止再引入新信息
- description: 金句 + 三个带走（初衷：从痛点出发 / 架构：三层分工 / 方法：工程可复刻）+ 落款（谢谢聆听 · 欢迎交流 · Hulk · 2026-10-21）+ 底部一行开源信息（github.com/Hulk-Zhao/mfg-agent-platform · MIT）。

## Checklist 自检
- Hero 页 4/20 = 20% ✓（01/08/15/20，互不相邻）
- 无连续 ≥3 页 supporting+valley ✓
- `N卡片横排` 0 次 ✓
- 非对称版式 12/16 内容页 = 75% ≥ 40% ✓
- 相邻页版式无重复 ✓（已逐对核对：01→02→03→04→05→06→07→08→09→10→11→12→13→14→15→16→17→18→19→20）
- `左大图+右侧文字`+`非对称双栏` 合计 6 页 = 30% ≤ 40% ✓
- 每页均有 role / rhythm / visual_role / anti_pattern ✓
- 含数据页均带「数据+判断」两段式 ✓（04/08/10/13/15/19）
- section 扉页 3 个 = 目录 3 章，编号 01–03 连续、标题与页码区间逐字一致 ✓
