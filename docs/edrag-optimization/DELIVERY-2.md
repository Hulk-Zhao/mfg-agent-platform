# DELIVERY-2 — kbqa 意图路由（DESIGN-2）交付摘要

> 迭代：ADR-001 增量「L1 miss 后分流：RAG 检索 vs 约束 LLM 直答」
> 分支：`feat/kbqa-faq-tier`（延续迭代一）｜状态：**已完成，全量回归 1126 passed / 0 failed**

## 1. 要解决的问题

迭代一后域外问题（天气/闲聊/写作）仍走完整 Milvus 检索 + LLM 链路。
实测分段耗时（2026-10-18，本机）：

| 场景 | L1 匹配 | search_kb | L2 全程 |
|---|---|---|---|
| 域内-模糊 | 0.4ms | 2059ms | 2722ms |
| 域外-天气 | 0.3ms | 1091ms | 2617ms |
| 域外-写诗 | 0.3ms | 1458ms | 2530ms |
| 冷启动首问 | — | **10659ms** | 2594ms |

域外问题白花 1-1.5s 检索；首问还要现加载 391MB BGE-M3。

## 2. 方案与关键决策

- **「LLM 分类意图」出局**：单次 LLM ~1.4s > 要省的检索 1-1.5s，负优化。
  路由判定必须零延迟 → 词表（实测单次 3.5µs）
- **「都不命中 → 约束直答」**（Hulk 确认，参考 EduRAG 思路）：一跳生成，
  LLM 在约束 prompt 下自行决定答法。实测 thinking-disabled 裸 LLM ~1.0s，
  比全链路省 ~1.6s；不存在"先分类再走 RAG = 双跳更慢"的陷阱
- **域外模式词表砍掉**：都走 direct，路由价值相同；只保留域内词表做
  「保出处通道」的质量守卫

## 3. 变更清单

| 文件 | 变更 |
|---|---|
| `v2/services/kbqa_intent.py`（新，~130 行） | 域内词表 122 词 + 21 拉丁缩写（词边界正则）+ `route()` 纯函数；退化输入（空/纯标点含全角）保守走 RAG |
| `v2/services/kbqa.py` | `answer()` L1 miss 后插入路由分支；新增 `CHANNEL_DIRECT` 与 `_answer_direct()`（prompt 四约束、max_tokens 200、LLM 挂诚实降级且不缓存）；docstring 更新 |
| `common/config.py` | `KBQA_DIRECT_ENABLED`（默认 true，逃生阀）/ `KBQA_DIRECT_MAX_TOKENS`（200） |
| `v2/demo/api.py` | lifespan 启动钩子：daemon 线程预热 `search_kb`（消 10.7s 冷启动；失败只告警） |
| `v2/demo/ui/kb-screen.js` | `CH_LABEL` 加 `direct: "助手直答"` + 角标 class 分支 |
| `v2/demo/ui/app.css` | `.kbqa-badge-direct` 灰色角标（无出处回答不许伪装制度原文） |
| `tests/v2/test_kbqa_intent.py`（新，17 条） | M 路由纯函数（含词表快照/零外部调用/1000 次 <100ms）/ D 直答契约（形状/不调 search_kb/逃生阀/降级/缓存）/ A 端点层 |
| `tests/v2/test_kbqa_faq_tier.py` | **修存量假绿**：R 组用例改域内词查询 + `no_redis_cache` 隔离（见 §5） |

## 4. 端到端验证证据（真实服务 + SQLite，端口 8902）

| 步骤 | 结果 |
|---|---|
| 域外「推荐几部好看的电影」 | `channel=direct`，首次 1652ms；答案正确执行四约束（身份声明 + 可帮方向 + 不越界） |
| 同问缓存回放 | 19ms（direct 也在 L0 覆盖范围） |
| 预热后域内模糊「出差机票怎么订」 | `channel=rag`，1.84s 完成检索+生成（冷启动对照 10.7s）；答案带出处 |
| 域内「VPN 连不上怎么办」 | `channel=faq`，score 1.0，0ms |
| `/kbqa/stats?days=1` | distribution 含 `direct` 计数（无代码改动自动多一类） |

冷启动免疫佐证：步骤 1 在服务刚起、预热仍在后台加载模型时发出，
direct 不依赖 `search_kb`，照样 ~1.6s 返回。

## 5. 过程中拦下的两个真问题

1. **存量测试假绿（缓存遗留）**：`test_r_l1_store_failure_does_not_break_answer`
   不隔离缓存，读到 6380 Redis 上旧架构同键 `miss` 条目 → 41 passed 是假绿。
   清缓存后立刻现形（该查询"任意问题"在新架构下正确走 direct）。
   修法：查询改域内词（守护语义本就是"L1 故障不影响 L2 可达"）+
   `no_redis_cache` 隔离。**教训**：用例结果不允许取决于外部缓存状态，
   新测试文件全部用例缓存隔离（写进 docstring）
2. **词表漏词**：系统比对 137 个语料关键词发现漏 3 个（账号安全/订单/资产，
   升级），已补；deliberately-dropped 的 9 个泛词（计划/版本/取消/责任/
   响应/需求/通道/结算/释放）在代码注释与 DESIGN-2 §2.1 记录理由

## 6. 遗留项（已记录，不在本期）

- **词表运营**：上线后用 `/api/v2/kbqa/stats` 回看 direct 分布，把误判的
  域内问法补词（与 FAQ 晋升同款运营思路）；`test_m_lexicon_snapshot`
  守手滑删除（哨兵词 + 规模下限 115）
- **direct 误判无自动告警**：当前靠人看 stats；阈值化告警待迭代三
- ** miss 模板化**：维持不做（低频且"换个问法"话术有价值，直答已覆盖
  大部分原 miss 场景）
