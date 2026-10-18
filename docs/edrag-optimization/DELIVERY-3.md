# 事故修复：「企业制度问答」一直思考中不回复

> 2026-10-18 深夜，Hulk 报障：23:06 与 23:08 两条提问（分别命中 RAG 与 direct
> 两条不同通道）「思考中」挂 4-6 分钟不回复，怀疑 LLM 不响应或 deepseek-flash
> 不支持向量。本文件记录排查结论、修复与端到端证据。提交 `173ca2f`。

## 1. 排查结论（两个怀疑均排除）

| 怀疑 | 结论 | 证据 |
|---|---|---|
| LLM 不响应 | **否** | 直接对后端 8004 复现两条原始提问：RAG 通道 2.3s 返回、direct 0.6s 返回，`/api/v2/health` 报 `llm: ok` |
| deepseek-flash 不支持向量 | **否**（概念错位） | deepseek-flash 是 chat 模型，本就不做 embedding；向量化由本地 BGE-M3 承担（`/health` 的 `vector: ok`，Milvus 19531 可达） |

**真因：双层超时缺失相乘。**

```
前端 _post 无 AbortController（_get 一直有，_post 是漏的）
     ×
后端 MilvusClient 无 timeout（pymilvus 默认 None = 无限等）
     ↓
任一层悬挂 → fetch 永远等待
     ↓
「思考中…」静止转圈 + S.sending 锁死后续全部提问
     ↓
用户无法区分"慢"与"死"
```

排查中还定位到用户环境的一个独立问题（不影响本修复，但解释了当时的混乱）：
**8000 端口上有个 Rust 预览代理，其上游已死**（`upstream connect failed:
os error 10061`），从 8000 打开页面全站 502；真实服务在 **8004**。
另有一个 22:25 启动后卡死在 import 阶段的 uvicorn 残骸进程（PID 21540）。

## 2. 修复（三层 + 两个顺带抓出的真 bug）

### 2.1 三层超时预算

| 层 | 改动 | 预算逻辑 |
|---|---|---|
| 前端 `_post` | `AbortController` + `timeoutMs` 参数（默认 12s） | 对齐既有 `_get` 的模式 |
| 前端 `kbqaQuery` | 显式传 75s | 覆盖后端最慢路径：Milvus 15s + LLM 60s，**后端先超时降级、前端不提前杀请求** |
| 后端 `MilvusClient` | `timeout=15` | 半死时 search 抛错 → `rag_unavailable` 诚实降级，不再挂死线程 |

### 2.2 失败自救入口（kb-screen）

- pending 气泡**显示等待秒数**（每秒 ticker）——用户看得见"在动"
- 超时/离线/错误气泡带 **↻ 重试**按钮——一键原样重发，不用手打问题
- 锁定期（上一条未返回）再提问给 toast 提示，不再静默吞掉

### 2.3 顺带修的两个真 bug（只有真实浏览器才暴露）

1. **`send()` 加了 `retryQuery` 参数后，`addEventListener("click", send)` 把
   Event 对象传进第一个参数** → TypeError，点击发送直接崩。用 typeof 判断挡掉。
2. **`messageMetaHtml` 用了 `esc` 却没把它作为参数传入**（迭代一加 channel
   角标时引入）→ 任何带 channel 的回答都让 paint 崩掉（ReferenceError）。
   **即迭代一交付的角标功能在浏览器里一直是崩的**——因为当时的 e2e 只走了
   curl API，UI 层从未被真实浏览器验证。

> 教训：零构建原生 JS 前端也必须过 Playwright，curl 验证覆盖不到渲染层。

## 3. 测试与端到端证据

**守护测试**（`tests/v2/test_架构守卫.py` 新增 5 条）：

| 测试 | 守护内容 |
|---|---|
| `test_post_has_abort_timeout` | `_post` 必须含 AbortController + signal |
| `test_kbqa_query_has_explicit_timeout_budget` | kbqaQuery 必须传显式预算 |
| `test_kbqa_pending_has_visible_timer_and_retry` | pending 秒数 + AbortError 分支 + 重试按钮 |
| `test_nav_check_kb_screen_js_syntax` | kb-screen.js 语法（此前只有 app.js 有） |
| `test_milvus_client_has_timeout` | MilvusClient 必须设 timeout |

**全量回归**：1131 passed / 0 failed / 0 skipped（junit XML 复核，含 Hulk 并行工作）。

**Playwright 端到端**（route 拦截制造真实悬挂）：

| 场景 | 结果 |
|---|---|
| 正常路径「年假怎么休」 | 制度直答 48ms，出处 QP-HR-003，角标渲染正常 |
| 悬挂路径 | 「思考中… 2s」→ 秒数递增 13s→23s→…→73s 全程可见 |
| 75s 预算打满 | 「⚠️ 响应超时（等待 75s 后端无响应）…」气泡 + ↻重试按钮出现 |
| 点重试（解除拦截后） | 立即自救成功，制度直答 1ms |

## 4. 环境注意事项（给 Hulk）

1. **打开地址用 `http://127.0.0.1:8004/demo-ui/index.html`**——8000 的 Rust
   预览代理上游已死，从 8000 打开会全站 502；
2. PID 21540（22:25 启动卡死的 uvicorn 残骸）可清理；清理 8004 服务前先
   确认没有浏览器页面连着（Edge 与 8004 有 ESTABLISHED 长连接）；
3. 起服务只起一个实例——双实例抢 8004 是 Windows SO_REUSEADDR 的经典坑。
