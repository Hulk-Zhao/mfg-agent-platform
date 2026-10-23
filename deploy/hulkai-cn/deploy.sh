#!/usr/bin/env bash
# ============================================================
# hulkai.cn 一键部署脚本（在轻量云服务器上以 root 执行）
#
#   chmod +x deploy.sh && ./deploy.sh
#
# 幂等设计：重复执行不会重建证书、不会覆盖已存在的 .env。
# ============================================================
set -euo pipefail

DOMAIN="hulkai.cn"
APP_DIR="/opt/mfg-agent-platform/hulkai-cn"
CERT_DIR="/etc/letsencrypt"

log() { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
die() { printf '\n\033[1;31m[失败] %s\033[0m\n' "$*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "请用 root 执行（sudo -i 后重跑）"

log "第 1 步 / 环境自检"
command -v docker >/dev/null 2>&1 || die "未安装 docker，请先执行官方安装脚本"
docker compose version >/dev/null 2>&1 || die "未安装 docker compose 插件"

log "第 2 步 / 同步代码到 ${APP_DIR}"
# rsync 缺失时给明确指引，不要让用户看到一条费解的 command not found
command -v rsync >/dev/null 2>&1 \
  || die "未安装 rsync，请先执行：apt-get install -y rsync （CentOS 用 yum install -y rsync）"

mkdir -p "${APP_DIR}"
SYNC_DIR="$(cd "$(dirname "$0")" && pwd)"
# 必须同步 .dockerignore：构建上下文是 ${APP_DIR}，缺了它的话
# data/*.db、docs、tests、__pycache__ 会被全部塞进镜像。
rsync -a --delete \
  --exclude '.git' --exclude '.venv' --exclude '__pycache__' --exclude '.pytest_cache' \
  --exclude 'logs' \
  "${SYNC_DIR}/../../src" "${SYNC_DIR}/../../data" \
  "${SYNC_DIR}/../../pyproject.toml" "${SYNC_DIR}/../../uv.lock" \
  "${SYNC_DIR}/../../README.md" "${SYNC_DIR}/../../entrypoint.sh" \
  "${SYNC_DIR}/../../.dockerignore" "${SYNC_DIR}/../../deploy" \
  "${APP_DIR}/" || die "代码同步失败"
mkdir -p "${APP_DIR}/data"

log "第 3 步 / 云上实例配置"
if [[ -f "${APP_DIR}/deploy/hulkai-cn/.env" ]]; then
  echo "已存在 .env，跳过（如需重填请手动删除）"
else
  cp "${APP_DIR}/deploy/hulkai-cn/env.cloud.example" \
     "${APP_DIR}/deploy/hulkai-cn/.env"
  echo "已生成 .env 模板：${APP_DIR}/deploy/hulkai-cn/.env"
  echo "请先填入 DeepSeek 密钥后重跑本脚本"
  exit 0
fi
grep -q '^OPENAI_API_KEY=sk-' "${APP_DIR}/deploy/hulkai-cn/.env" \
  || die "请先填好 ${APP_DIR}/deploy/hulkai-cn/.env 里的 OPENAI_API_KEY"

log "第 4 步 / TLS 证书"
mkdir -p "${CERT_DIR}"
if [[ -f "${CERT_DIR}/live/${DOMAIN}/fullchain.pem" ]]; then
  echo "证书已存在，跳过签发"
else
  echo "选择校验方式："
  echo "  1) DNS-01  （推荐：备案期间 80 端口常被拦截，这种方式不受影响）"
  echo "  2) HTTP-01 （要求域名已解析到本机且 80 端口可达）"
  read -r -p "输入 1 或 2： " CHOICE
  curl -s https://get.acme.sh | sh -s email="admin@${DOMAIN}"
  export ACME_HOME="/root/.acme.sh"
  case "$CHOICE" in
    1)
      read -r -p "DNSPod ID： " DPID
      read -r -p "DNSPod Token： " DPKEY
      export DP_Id="$DPID" DP_Key="$DPKEY"
      "${ACME_HOME}/acme.sh" --issue --dns dns_dp -d "$DOMAIN" -d "www.${DOMAIN}" --server letsencrypt
      ;;
    2)
      "${ACME_HOME}/acme.sh" --issue --standalone -d "$DOMAIN" -d "www.${DOMAIN}" --server letsencrypt
      ;;
    *)
      die "无效选择"
      ;;
  esac
  "${ACME_HOME}/acme.sh" --install-cert -d "$DOMAIN" \
    --key-file       "${CERT_DIR}/live/${DOMAIN}/privkey.pem" \
    --fullchain-file "${CERT_DIR}/live/${DOMAIN}/fullchain.pem" \
    --reloadcmd      "cd ${APP_DIR}/deploy/hulkai-cn && docker compose -f docker-compose.cloud.yml exec nginx nginx -s reload"
fi

log "第 5 步 / 构建并启动"
cd "${APP_DIR}/deploy/hulkai-cn"
docker compose -f docker-compose.cloud.yml up -d --build

log "第 6 步 / 自检"
for i in $(seq 1 30); do
  if curl -fsS "http://127.0.0.1:8004/api/v2/health" >/dev/null 2>&1; then
    echo "容器健康：/api/v2/health 已通过"
    break
  fi
  [[ $i -eq 30 ]] && { docker compose -f docker-compose.cloud.yml logs --tail=60 app; die "健康检查 30 次未通过，日志见上"; }
  sleep 5
done

echo "--- 根路径跳转 ---"
curl -sI "https://${DOMAIN}/" | head -3
echo "--- 服务状态 ---"
docker compose -f docker-compose.cloud.yml ps

log "完成"
echo "访问地址：https://${DOMAIN}/  （会自动跳到 /demo-ui/）"
echo "若打不开，按此顺序排查：DNS 是否生效 → 443 安全组是否放行 → 备案是否通过"
