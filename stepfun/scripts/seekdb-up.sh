#!/usr/bin/env bash
# =============================================================
#  seekdb 本地启动（server 模式，监听 2881）
#  用法： sudo bash scripts/seekdb-up.sh
#  说明： 若你已经有可用的 seekdb 实例（本机或云上），不需要跑这个脚本，
#         直接改 .env 里的 SDB_HOST / SDB_PORT / SDB_PASSWORD 即可。
# =============================================================
set -eu

ok()   { printf '  [OK]  %s\n' "$*"; }
info() { printf '        %s\n' "$*"; }
die()  { printf '\n[X] %s\n' "$*"; exit 1; }

info "这一步会调用 OceanBase 官方 seekdb 一键安装脚本，需要 Linux + systemd + root。"
info "安装完成后 seekdb 默认监听 2881，root 账号密码见安装输出。"

if [ "$(id -u)" != "0" ]; then
  die "请用 root 运行：sudo bash scripts/seekdb-up.sh"
fi

if ! command -v systemctl >/dev/null 2>&1 || [ ! -d /run/systemd/system ]; then
  cat <<'EOF'
[X] 这台机器没有 systemd，官方一键安装脚本跑不了。两条替代路径：
    1) 已装 Docker：按官方文档用容器方式启动 seekdb，映射 2881 端口；
    2) 直接用一台已有的 seekdb（云主机 / 队友机器），把地址写进 .env。
EOF
  exit 1
fi

# 已安装则直接给状态
if command -v seekdb >/dev/null 2>&1; then
  ok "已检测到 seekdb：$(command -v seekdb)"
else
  info "下载官方安装脚本…"
  curl -fsSL --connect-timeout 20 --max-time 600 \
    https://obportal.s3.ap-southeast-1.amazonaws.com/download-center/opensource/seekdb/seekdb_install.sh \
    -o /tmp/seekdb_install.sh
  sh /tmp/seekdb_install.sh
  ok "安装脚本执行完成"
fi

info "检查监听端口 2881…"
if ss -ltn 2>/dev/null | grep -q ':2881'; then
  ok "seekdb 已在监听 2881"
else
  info "尝试 start（服务名可能是 seekdb 或 obseekdb）"
  systemctl start seekdb 2>/dev/null || systemctl start obseekdb 2>/dev/null || true
  sleep 3
  ss -ltn 2>/dev/null | grep -q ':2881' && ok "seekdb 已启动" || die "2881 仍未监听，请查看 systemctl status seekdb 与安装脚本输出"
fi

cat <<'EOF'

下一步：
  1) 拿到 root 密码（安装输出里有；也可在 /etc/seekdb/ 下的配置里找）
  2) cp .env.example .env 并填入 SDB_HOST=127.0.0.1 / SDB_PASSWORD=<密码>
  3) npm start，浏览器打开 http://127.0.0.1:8787/
EOF
