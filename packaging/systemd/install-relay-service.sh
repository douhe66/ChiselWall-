#!/usr/bin/env bash
# 安装/启用 ChiselWall relay 的 systemd 服务。
# 用法：sudo ./install-relay-service.sh [安装目录] [relay.json] [服务用户] [端口]
set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "请用 root 运行：sudo $0 ..." >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="$(cd "${1:-$SCRIPT_DIR}" && pwd)"
CONFIG="${2:-$INSTALL_DIR/relay.json}"
SERVICE_USER="${3:-chiselwall}"
PORT="${4:-18760}"
UNIT=/etc/systemd/system/chiselwall-relay.service

if [[ ! -x "$INSTALL_DIR/chiselwall-relay/chiselwall-relay" ]]; then
  echo "找不到可执行文件：$INSTALL_DIR/chiselwall-relay/chiselwall-relay" >&2
  exit 1
fi
if [[ ! -f "$CONFIG" ]]; then
  echo "找不到 relay 配置：$CONFIG（先运行 relay --init 或 admin invite）" >&2
  exit 1
fi

if ! getent passwd "$SERVICE_USER" >/dev/null; then
  useradd --system --no-create-home --shell /usr/sbin/nologin "$SERVICE_USER"
  echo "已创建系统用户：$SERVICE_USER"
fi

install -d -m 0750 -o "$SERVICE_USER" -g "$SERVICE_USER" /var/log/chiselwall
# relay 会把组注册表写在配置文件同目录；确保服务用户可写该目录。
install -d -m 0750 -o "$SERVICE_USER" -g "$SERVICE_USER" "$(dirname "$CONFIG")"

sed \
  -e "s|__INSTALL_DIR__|$INSTALL_DIR|g" \
  -e "s|__CONFIG__|$CONFIG|g" \
  -e "s|__SERVICE_USER__|$SERVICE_USER|g" \
  -e "s|__PORT__|$PORT|g" \
  "$SCRIPT_DIR/chiselwall-relay.service.template" > "$UNIT"

systemctl daemon-reload
systemctl enable --now chiselwall-relay
echo
echo "已安装并启动 chiselwall-relay.service"
systemctl --no-pager --full status chiselwall-relay || true
echo
echo "日志：journalctl -u chiselwall-relay -f"
echo "文件：/var/log/chiselwall/relay.log"
