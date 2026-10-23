#!/usr/bin/env bash
# ============================================================
# 前端一键同步：本地 ui/ → 云服务器 /var/www/mfg-ui
#
# 在 Windows 的 Git Bash 里执行：
#   bash deploy/hulkai-cn/hybrid/sync-frontend.sh
#
# 做三件事：
#   1. 把整个 ui 目录（含 scenes/）推上去，并清空旧文件
#   2. 推上版本号注入脚本并执行（复刻后端托管时的缓存根治行为）
#   3. 让 Nginx 校验配置并重载
#
# 为什么用 tar + ssh 管道而不是 rsync：Git Bash 默认不带 rsync，
# 而 scp -r 无法清理服务器上的残留旧文件。tar 管道两者都能做到，零额外依赖。
# ============================================================
set -euo pipefail

SERVER="${MFG_SERVER:-root@hulkai.cn}"
REMOTE_DIR="${MFG_REMOTE_DIR:-/var/www/mfg-ui}"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
LOCAL_UI="$(cd "$SCRIPT_DIR/../../../src/mfg_agents/v2/demo/ui" && pwd)"

[[ -f "$LOCAL_UI/index.html" ]] || { echo "[失败] 找不到 index.html：$LOCAL_UI"; exit 1; }

echo "==> 本地前端目录：$LOCAL_UI"
echo "==> 目标服务器：$SERVER:$REMOTE_DIR"

echo "==> 1/3 同步静态文件"
# shellcheck disable=SC2029
ssh "$SERVER" "mkdir -p '$REMOTE_DIR' && rm -rf '${REMOTE_DIR:?}/'*"
tar czf - -C "$LOCAL_UI" . | ssh "$SERVER" "tar xzf - -C '$REMOTE_DIR'"

echo "==> 2/3 同步并运行版本号注入"
scp "$SCRIPT_DIR/inject-version.py" "$SERVER:$REMOTE_DIR/inject-version.py"
# shellcheck disable=SC2029
ssh "$SERVER" "cd '$REMOTE_DIR' && python3 inject-version.py"

echo "==> 3/3 校验并重载 Nginx"
# shellcheck disable=SC2029
ssh "$SERVER" "nginx -t && nginx -s reload"

echo
echo "完成。自检三步："
echo "  curl -sI https://hulkai.cn/           # 期望 200，且 Cache-Control 含 no-store"
echo "  curl -s  https://hulkai.cn/api/v2/health   # 期望返回 status: ok"
echo "  浏览器打开 https://hulkai.cn/ 看左下角是否显示在线"
