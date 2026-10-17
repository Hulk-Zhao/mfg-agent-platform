# 外网访问指南（给外部用户演示）

> 面向零基础：照着做即可，不需要懂网络。全程只双击 `.bat` 脚本。
>
> 相关脚本：`scripts/start_public.bat`（Cloudflare）、`scripts/start_public_cpolar.bat`（cpolar）、`scripts/stop_public.bat`（停止）。

## 1. 先搞懂三种"访问方式"

你的电脑就像一栋办公楼，演示平台在 **8004 房间**：

| 方式 | 比喻 | 谁能进 |
|---|---|---|
| 本机访问 | 你自己在房间里 | 只有你这台电脑（`http://127.0.0.1:8004/demo-ui/`） |
| 内网访问 | 同一栋办公楼的人走楼梯 | 同一办公室 / 同一 WiFi 的人（`http://你的内网IP:8004/demo-ui/`） |
| 公网访问 | 在楼外开一个"传送门" | 任何拿到链接的人（`https://xxxx.trycloudflare.com/demo-ui/`） |

"传送门"就是**隧道（tunnel）**：一个跑在你电脑上的小程序，把本地 8004 端口映射成一个公网网址。不需要公网 IP、不需要改路由器、不需要买服务器。

## 2. 内网访问（最简单，优先用）

**适用**：对方和你处在同一网络（同一办公室、连同一个 WiFi/交换机）。

1. 双击 `scripts\start_local.bat`（已自动绑定 `0.0.0.0`，即"对整栋办公楼开放"）。
2. 启动后窗口会打印 `Network: http://<你的内网IP>:8004/demo-ui/`。
3. 把这个 `http://<内网IP>:8004/demo-ui/` 发给同事，对方浏览器直接打开。

查内网 IP 的方法：`Win + R` → 输入 `cmd` → 执行 `ipconfig` → 看"IPv4 地址"，形如 `192.168.x.x`。

## 3. 公网访问 · Cloudflare（免安装，推荐先用这个）

**适用**：外部用户在任何网络（家里、客户现场、4G/5G）。

1. 双击 `scripts\start_public.bat`。
2. 脚本会自动做三件事：
   - 检查 8004 服务，没起就自动拉起（新开一个最小化窗口，日志在 `logs\api.log`）；
   - 启动 `bin\cloudflared.exe` 隧道（日志在 `logs\cloudflared.log`）；
   - 等待约 10~40 秒，提取公网网址并打印，同时存到 `logs\public_url.txt`。
3. 把窗口里打印的链接发给外部用户，例如：
   ```
   https://random-words-1234-5678.trycloudflare.com/demo-ui/
   ```
4. 对方打开后选岗位（如"总经理"）即可进入演示门户。实时事件流（SSE）、HITL 会签在隧道下**无需额外配置**，与本地完全一致。

**链接特点**：免费快速隧道的域名是随机生成的，**每次重启脚本都会变**。对方打不开时，重跑 `start_public.bat` 拿新链接即可。

## 4. 公网访问 · cpolar（国内备选）

**适用**：对方网络访问 `*.trycloudflare.com` 不畅时（部分国内宽带/政企网络会屏蔽 Cloudflare 域名）。

1. 到 https://www.cpolar.com 下载安装 cpolar。
2. 装好后打开 cmd，执行一次（只需一次， token 在 cpolar 官网"仪表盘 → 验证"页获取）：
   ```
   cpolar authtoken <你的token>
   ```
3. 双击 `scripts\start_public_cpolar.bat`。
4. 窗口里会打印 `Forwarding  https://xxxxxxxx.cpolar.top -> http://localhost:8004`，把 `https://xxxxxxxx.cpolar.top/demo-ui/` 发给对方。

cpolar 免费版同样是随机域名、重启即变；需要固定域名可购买其套餐。

## 5. 如何停止

双击 `scripts\stop_public.bat`。它会：

1. 结束 `cloudflared.exe` 隧道进程；
2. 结束本项目的 FastAPI 进程（按命令行精确匹配 `uvicorn mfg_agents.v2.demo.api`，**不会误杀你机器上其他 Python 项目**）；
3. 检查 8004 端口是否释放。

## 6. 常见问题（FAQ）

| 现象 | 原因 | 解决 |
|---|---|---|
| 双击 start_public.bat 提示 `cloudflared not found` | `bin\cloudflared.exe` 丢失且系统 PATH 里也没有 | 从 https://github.com/cloudflare/cloudflared/releases/latest 下载 `cloudflared-windows-amd64.exe`，存为项目 `bin\cloudflared.exe` 后重跑 |
| 脚本报 `Failed to get public URL` | 本机到 Cloudflare 网络不通 | 看 `logs\cloudflared.log`；换 `scripts\start_public_cpolar.bat` |
| 对方打开链接转圈 / 打不开 | ① 对方网络屏蔽 trycloudflare；② 服务已停 | 换 cpolar 链接；确认你的电脑没关机、脚本窗口没关 |
| 对方说"链接变了打不开" | 快速隧道域名每次重启都变 | 重跑 `start_public.bat`，把新链接发过去 |
| 端口 8004 被占用 | Docker 容器（`mfg_app`）或之前的进程占着 | `docker compose down` 停掉容器，或先跑 `stop_public.bat`；确认无占用后重跑 |
| 想换端口 | 脚本默认 8004 | 改三个脚本里的 `set PORT=8004`（start_public / start_public_cpolar / stop_public 保持一致），服务窗口同步用新端口启动 |
| 演示数据乱了 / 想重置 | SQLite 演示库被写脏 | 删 `data\mfg_agent.db` 后重跑 `start_local.bat`（首次启动自动重建并写种子数据） |

## 7. 安全须知（重要）

- 演示门户是 **10 岗位免密登录**：公网链接 = 谁拿到谁就能进，且能操作 HITL 审批。
- **只用于演示，用完立刻跑 `stop_public.bat`。**
- 不要在公网链接里放真实客户数据；当前演示库本身就是虚构数据，但链接暴露期间任何人都可能改写工单状态。
- 对客正式提供前，必须先加认证（访问令牌门禁 / 登录态），见 README"生产化清单"。
