# 外网访问指南（给外部用户演示）

> 面向零基础：照着做即可，不需要懂网络。全程双击 `.bat` 脚本。
>
> 脚本清单：
> | 脚本 | 作用 |
> |---|---|
> | `scripts\start_local.bat` | 本地启动（绑定 `0.0.0.0`，局域网可访问） |
> | `scripts\start_public_ssh.bat` | 公网 · SSH over 443 隧道 |
> | `scripts\start_public.bat` | 公网 · Cloudflare 快速隧道（assistant 项目同款） |
> | `scripts\start_public_cpolar.bat` | 公网 · cpolar（国内线路，推荐） |
> | `scripts\stop_public.bat` | 停止隧道与服务 |

## 0. 先看结论：本机（2026-10-17）实测状态

| 访问方式 | 状态 | 证据 |
|---|---|---|
| **内网** `http://192.168.34.135:8004/demo-ui/` | ✅ 可用 | 绑定 `0.0.0.0`，实测 200 / 4151B |
| **Cloudflare 快速隧道** | ✅ **可用**（需关闭 VPN 的 TUN 虚拟网卡） | 关闭后 DNS 回到真实 IP `198.41.192.x`，预检 `TCP Connectivity: PASS`；公网 `/demo-ui/` 实测 200 / 4151B |
| **cpolar** | ✅ 方案就绪，只差 token | `bin\cpolar.exe` 已内置；未配 token 时提示 `not found authtoken` |
| **SSH(443)** | ⚠️ 匿名配额用尽 | 前两次匿名成功、公网 health 200；之后服务端要求认证 |

### ⚠️ 关键前提：跑 Cloudflare 隧道前先关掉 TUN 虚拟网卡

本机的代理客户端是 `E:\ikuuu_vpn\app\iKuuuVPNCore.exe`。它的 **TUN 模式 + fake-ip** 会把 DNS 结果劫持成 `198.18.x.x` 虚拟地址，cloudflared 拿到虚拟地址后连不上 Cloudflare 边缘端口 7844，表现为：

```
TLS handshake with edge error: EOF
precheck: TCP Connectivity region1.v2.argotunnel.com  FAIL
边缘地址解析成 198.18.0.59（fake-ip，应为 198.41.x.x）
```

**关闭虚拟网卡后**（VPN 客户端里关掉 TUN 模式，保留"系统代理"即可），DNS 立刻回到真实 IP，隧道 3 秒内注册成功。

验证方法（关掉后执行，结果应为 `198.41.x.x` 而非 `198.18.x.x`）：

```
nslookup region1.v2.argotunnel.com
```

如果哪天又打不开，先查这一项——90% 是 TUN 虚拟网卡又被打开了。

## 1. 三种"访问方式"是什么

你的电脑像一栋办公楼，演示平台在 **8004 房间**：

| 方式 | 比喻 | 谁能进 |
|---|---|---|
| 本机访问 | 你自己在房间里 | 只有你这台电脑（`http://127.0.0.1:8004/demo-ui/`） |
| 内网访问 | 同一栋楼的人走楼梯 | 同一办公室 / 同一 WiFi（`http://你的内网IP:8004/demo-ui/`） |
| 公网访问 | 在楼外开一个"传送门" | 任何拿到链接的人（`https://xxx.cpolar.top/demo-ui/`） |

"传送门"就是**隧道（tunnel）**：跑在你电脑上的一个程序，把本地 8004 端口映射成公网网址。不需要公网 IP、不需要改路由器、不需要买服务器。

## 2. 内网访问（最简单，优先用）

**适用**：对方和你同一网络（同一办公室、连同一个 WiFi/交换机）。

1. 双击 `scripts\start_local.bat`（已自动绑定 `0.0.0.0`，即"对整栋楼开放"）。
2. 窗口会打印 `Network: http://<你的内网IP>:8004/demo-ui/`。
3. 把这个地址发给同事，对方浏览器直接打开。

查内网 IP：`Win + R` → 输入 `cmd` → 执行 `ipconfig` → 看"IPv4 地址"，形如 `192.168.x.x`。

## 3. 公网访问 · cpolar（国内推荐，assistant 项目同款思路）

**适用**：外部用户在家、客户现场、4G/5G。**一次性配置 3 步，之后每次双击脚本即可。**

1. 注册免费账号：https://www.cpolar.com （手机号即可）
2. 登录后在"仪表盘 → 验证"复制你的 **authtoken**
3. 在 cmd 里执行一次（只需一次）：
   ```
   cd /d E:\tools\Factroy_Agents\mfg-agent-platform
   bin\cpolar.exe authtoken 你的token
   ```
4. 双击 `scripts\start_public_cpolar.bat`，窗口会打印：
   ```
   Forwarding  https://xxxxxxxx.cpolar.top -> http://localhost:8004
   ```
5. 把 `https://xxxxxxxx.cpolar.top/demo-ui/` 发给外部用户。

cpolar 免费版是随机域名（每次重启都变）；需要固定域名可购买其套餐。

## 4. 公网访问 · Cloudflare 快速隧道（本机已验证可用）

**适用**：外部用户在家、客户现场、4G/5G。**前提：先关闭 VPN 的 TUN 虚拟网卡**（见第 0 节）。这是 assistant 项目用的方案，本项目已原样复刻并加固。

1. 双击 `scripts\start_public.bat`。
2. 脚本自动：检查并拉起 8004 服务 → 起 cloudflared → 等待"URL 生成 **且** 隧道注册成功"（`Registered tunnel connection`，最多 60s）→ 实测公网 `/api/v2/health` 返回 200 → 打印 READY。
3. 看到 `PUBLIC ACCESS READY` 后，把 `https://xxx.trycloudflare.com/demo-ui/` 发出去。

**如果报 "Tunnel URL was created but the edge connection FAILED"**：说明 URL 有了但隧道没注册，链接打不开（Cloudflare 会返回 1033）。按顺序修：

| 顺序 | 操作 | 说明 |
|---|---|---|
| a | **关闭 VPN 的 TUN 虚拟网卡**（保留"系统代理"即可） | 最直接，本机实测有效：DNS 从 `198.18.x.x` 变回 `198.41.x.x` 后隧道 3 秒注册 |
| b | 或在 VPN 的 fake-ip 过滤列表里加 `+.argotunnel.com` | 同上，但只放行这一个域名 |
| c | 换 VPN 节点后重试 | 有些节点到 Cloudflare 边缘不通 |
| d | 改用 `start_public_cpolar.bat` | 绕开这个问题 |

## 5. 公网访问 · SSH over 443（备用）

`scripts\start_public_ssh.bat` 用系统自带 ssh 走 443 端口反向隧道（本机曾实测拿到公网 URL 并访问成功）。适合 7844 被封但 443 通的网络。注意：

- 匿名隧道由服务端限额，用过几次后会要求认证；
- 需要稳定使用就注册免费 pinggy.io 账号拿到 token，然后在该脚本顶部加一行 `set PINGGY_TOKEN=你的token`；
- 匿名隧道 60 分钟过期。

## 6. 如何停止

双击 `scripts\stop_public.bat`，它会：

1. 结束 `cloudflared.exe` 隧道进程；
2. 结束本项目的 SSH 隧道进程（按命令行匹配 `a.pinggy.io`）；
3. 结束本项目的 FastAPI 进程（按命令行精确匹配 `uvicorn mfg_agents.v2.demo.api`，**不会误杀你机器上其他 Python / ssh 会话**）；
4. 检查 8004 端口是否释放。

## 7. 常见问题（FAQ）

| 现象 | 原因 | 解决 |
|---|---|---|
| 脚本报 `cloudflared not found` | `bin\cloudflared.exe` 丢失且 PATH 里也没有 | 从 https://github.com/cloudflare/cloudflared/releases/latest 下载 `cloudflared-windows-amd64.exe`，存为项目 `bin\cloudflared.exe` |
| Cloudflare 脚本报"URL 有了但隧道注册失败" | TUN 模式 VPN 的 fake-ip 劫持 DNS，7844 不通 | **先关 TUN 虚拟网卡**，见第 0 / 4 节 |
| cpolar 提示 `not found authtoken` | 没配 token | 见第 3 节第 3 步，配一次即可 |
| SSH 脚本等不到 URL | 匿名配额用尽 / 443 到 a.pinggy.io 不通 | 配 `PINGGY_TOKEN`，或换 VPN 节点，或改用 cpolar |
| 对方打不开链接 | ① 对方网络屏蔽该域名；② 隧道已过期；③ 服务已停 | 换 cpolar 链接；重跑脚本拿新链接；确认电脑没关机 |
| 端口 8004 被占用 | Docker 容器 `mfg_app` 或旧进程占着 | `docker compose down`，或先跑 `stop_public.bat` |
| 想换端口 | 脚本默认 8004 | 三个脚本里的 `set PORT=8004` 保持一致修改，服务也用新端口启动 |
| 演示数据乱了 / 想重置 | SQLite 演示库被写脏 | 删 `data\mfg_agent.db` 后重跑 `start_local.bat`（首次启动自动重建并写种子数据） |

## 8. 公网访问性能（已优化，实测数据）

公网访问慢有**两个独立原因**，都已修复：

### 原因一：后端探测超时过短（功能性 bug，最严重）

前端 `api.probe()` 早期用 1800ms 超时探测后端。本地毫秒级没问题，但**公网隧道单次往返就要 1.2-2s**（实测 `/api/v2/health` 1.21s），探测必然 `abort()` → 误判"后端离线" → **全站退回演示数据**（看板/审批中心显示的是假数据，不是真实工单）。

已修：超时提到 9000ms，并加 in-flight 守卫防止 `boot()` 与 `enterApp()` 并发探测互相覆盖状态。

**自检方法**：右上角状态必须显示 **"后端在线 · 真实链路"**（绿点）。若显示"后端离线 · 演示模式"（红点），说明探测又失败了——刷新页面重试。

### 原因二：静态资源未压缩 + 阻塞加载

`demo-ui` 的 JS/CSS 合计约 284 KB 纯文本，`index.html` 里 4 个 `<script>` 无 `defer`，全部阻塞渲染。经隧道实测串行约 12s 才发第一个 API 请求。

已修：后端加 `GZipMiddleware`（118 KB → 19 KB，压到 16%）+ 4 个 script 加 `defer` 并行下载。

### 优化前后对比（真实浏览器实测，经 Cloudflare 隧道）

| 指标 | 优化前 | 优化后 |
|---|---|---|
| 首屏渲染 | 12s+ 白屏 | **0.92s** |
| 静态资源传输量 | 284 KB | **82 KB**（GZip） |
| 后端状态 | ❌ 后端离线 · 演示数据 | ✅ **后端在线 · 真实链路** |
| 审批中心待办列表 | 一直转圈 | **3.2s 加载完成，452 项真实工单** |

回归测试：**391 passed / 2 skipped**，零回归。

### 仍然存在的物理上限

并发拉取全部静态资源经隧道实测约 7.8s——**Cloudflare 隧道的单连接带宽是硬瓶颈**，不是应用层问题。同城/同网络的演示直接用内网地址（毫秒级）最快；跨公网时建议演示方**先打开一次页面**，让浏览器缓存住静态资源，后续切页就快了。

## 9. 安全须知（重要）

- 演示门户是 **10 岗位免密登录**：公网链接 = 谁拿到谁就能进，且能操作 HITL 审批。
- **只用于演示，用完立刻跑 `stop_public.bat`。**
- 不要在公网链接里放真实客户数据；当前演示库本身是虚构数据，但链接暴露期间任何人都可能改写工单状态。
- 对客正式提供前，必须先加认证（访问令牌门禁 / 登录态），见 README"生产化清单"。
