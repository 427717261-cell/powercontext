#!/usr/bin/env bash
# 安装并启动 seekdb（OceanBase 轻量版）——本作品「怪谈档案库」的承载数据库。
#
#   bash install_seekdb.sh
#
# 脚本做五件事：
#   1. 装 seekdb（官方 yum / apt 仓库；已安装则跳过）；
#   2. 写入 /etc/seekdb/seekdb.cnf（备份已有配置）；
#   3. systemctl enable --now seekdb（官方 rpm 默认是 disabled，必须显式启用）；
#   4. 等待 2881 端口就绪，打印版本与实际启动命令行；
#   5. 打印下一步：用 root 建库建账号（见 init_account.sql）与初始化档案库。
#
# 幂等：重复执行安全。配置已存在且内容一致时不会覆盖。
set -euo pipefail

SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CNF_SRC="${SOURCE_DIR}/seekdb.cnf"
CNF_DST="/etc/seekdb/seekdb.cnf"
PORT="${SEEKDB_PORT:-2881}"
INSTALL_SCRIPT_URL="https://obportal.s3.ap-southeast-1.amazonaws.com/download-center/opensource/seekdb/seekdb_install.sh"

say () { printf '[install_seekdb] %s\n' "$1"; }

# ---------------------------------------------------------------- 1. 安装
if command -v seekdb >/dev/null 2>&1 || [ -x /usr/bin/seekdb ]; then
  say "seekdb 已安装：$(seekdb --version 2>&1 | sed -n '2p')"
else
  say "开始安装 seekdb（官方仓库）…"
  if command -v apt >/dev/null 2>&1 && [ -f /etc/debian_version ]; then
    curl -fsSL -o /tmp/seekdb_install.sh "${INSTALL_SCRIPT_URL}"
    bash /tmp/seekdb_install.sh
  elif command -v yum >/dev/null 2>&1; then
    curl -fsSL -o /tmp/seekdb_install.sh "${INSTALL_SCRIPT_URL}"
    bash /tmp/seekdb_install.sh
  else
    echo "需要 yum 或 apt 才能安装 seekdb" >&2
    exit 1
  fi
  rm -f /tmp/seekdb_install.sh
  say "安装完成：$(seekdb --version 2>&1 | sed -n '2p')"
fi

# ---------------------------------------------------------------- 2. 配置
if [ ! -f "${CNF_SRC}" ]; then
  echo "找不到 ${CNF_SRC}" >&2
  exit 1
fi
if [ -f "${CNF_DST}" ] && ! cmp -s "${CNF_SRC}" "${CNF_DST}"; then
  cp -a "${CNF_DST}" "${CNF_DST}.bak.$(date +%Y%m%d%H%M%S)"
  say "原配置已备份"
fi
if [ -f "${CNF_DST}" ] && cmp -s "${CNF_SRC}" "${CNF_DST}"; then
  say "配置已是目标内容，跳过写入"
else
  install -m 644 "${CNF_SRC}" "${CNF_DST}"
  say "已写入 ${CNF_DST}"
fi

# ---------------------------------------------------------------- 3. 启动
# 官方 rpm 提供的 seekdb.service 默认 disabled，必须显式 enable 才会开机自启。
if command -v systemctl >/dev/null 2>&1; then
  systemctl enable seekdb >/dev/null 2>&1 || true
  systemctl restart seekdb
  say "systemctl restart seekdb 完成"
else
  nohup /usr/bin/seekdb --nodaemon \
    --base-dir=/var/lib/seekdb \
    --data-dir=/var/lib/seekdb/store \
    --redo-dir=/var/lib/seekdb/store/redo \
    --port="${PORT}" --parameter cpu_count=4 >/var/log/seekdb.out 2>&1 &
  say "已后台启动 seekdb（日志 /var/log/seekdb.out）"
fi

# ---------------------------------------------------------------- 4. 就绪等待
say "等待 ${PORT} 端口就绪…"
for _ in $(seq 1 60); do
  if (exec 3<>"/dev/tcp/127.0.0.1/${PORT}") 2>/dev/null; then
    exec 3<&- 3>&-
    say "seekdb 已在 ${PORT} 上就绪"
    break
  fi
  sleep 2
done

say "版本：$(seekdb --version 2>&1 | sed -n '2p')"
say "进程：$(pgrep -f '/usr/bin/seekdb' | head -1 | xargs -r -I{} tr '\0' ' ' < /proc/{}/cmdline)"

cat <<EOF

==> 接下来请执行：

  # 1) 建库并建最小权限账号（需要 seekdb 的 root 账号，口令用你在安装时设置的那个）
  #    把 <seekdb_root_口令> 与 <radio_口令> 换成自己的值，再执行：
  mysql -h 127.0.0.1 -P ${PORT} -u root -p < ${SOURCE_DIR}/init_account.sql

  # 2) 初始化怪谈档案库（建表 + NGRAM 全文索引 + HNSW 向量索引 + 灌 20 条数据）
  #    需要 Dify 的 .env 里配好 RADIO_SEEKDB_* 与 RADIO_EMBEDDING_API_KEY，
  #    然后在 Dify 的 docker 目录里执行：
  #      docker compose run --rm radio-archive python /app/scripts/init_archive.py

  # 3) 验证 seekdb 真的在承担读写与混合检索（可选，随时可重跑）：
  bash ${SOURCE_DIR}/verify_seekdb.sh

EOF
