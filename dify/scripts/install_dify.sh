#!/usr/bin/env bash
# 把「深夜电台 · 怪谈档案」接入一套已有的 Dify 1.4.2 部署。
#
#   bash install_dify.sh /path/to/dify/docker
#
# 脚本做四件事：
#   1. 把本作品的 compose 覆盖文件、Dockerfile 与后端源码复制进 Dify 的 docker 目录；
#   2. 基于官方 .env.example 生成 .env（随机口令，向量库走 pgvector）；
#   3. 把本作品需要的凭据追加进 .env（seekdb 账号、Embedding 服务 Key 等）；
#   4. 打印后续需要手动执行的两条命令（初始化档案库、拉起整套服务）。
#
# 本脚本不改动 Dify 官方 docker-compose.yaml，只新增 docker-compose.override.yaml。
set -euo pipefail

SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET_DIR="${1:-}"
if [[ -z "${TARGET_DIR}" ]]; then
  echo "用法: bash install_dify.sh <Dify 的 docker 目录>" >&2
  exit 2
fi
if [[ ! -f "${TARGET_DIR}/docker-compose.yaml" ]]; then
  echo "目标目录里没有 docker-compose.yaml，请指向 Dify 的 docker 目录：${TARGET_DIR}" >&2
  exit 2
fi

echo "==> 复制部署文件到 ${TARGET_DIR}"
cp "${SOURCE_DIR}/deploy/docker-compose.override.yaml" "${TARGET_DIR}/docker-compose.override.yaml"
cp "${SOURCE_DIR}/deploy/Dockerfile.radio" "${TARGET_DIR}/Dockerfile.radio"
cp "${SOURCE_DIR}/deploy/Dockerfile.powercontext" "${TARGET_DIR}/Dockerfile.powercontext"
mkdir -p "${TARGET_DIR}/service" "${TARGET_DIR}/scripts" "${TARGET_DIR}/data"
cp "${SOURCE_DIR}/service/radio_archive.py" "${TARGET_DIR}/service/"
cp "${SOURCE_DIR}/scripts/init_archive.py" "${TARGET_DIR}/scripts/"
cp "${SOURCE_DIR}/data/archive.json" "${TARGET_DIR}/data/"

echo "==> 生成 .env"
if [[ ! -f "${TARGET_DIR}/.env" ]]; then
  python3 "${SOURCE_DIR}/scripts/make_dify_env.py" "${TARGET_DIR}"
else
  echo "    .env 已存在，跳过生成（如需重置请先删除它）"
fi

cat <<'EOF'

==> 接下来请执行：

  # 1) 补齐凭据（不要把真实 Key 提交进仓库）
  #    在 ${TARGET_DIR}/.env 末尾追加下面这些变量：
  #      RADIO_SEEKDB_HOST=host.docker.internal     # seekdb 跑在宿主机时用这个
  #      RADIO_SEEKDB_PORT=2881
  #      RADIO_SEEKDB_USER=<你的 seekdb 账号>
  #      RADIO_SEEKDB_PASSWORD=<你的 seekdb 口令>
  #      RADIO_SEEKDB_DATABASE=radio_archive
  #      RADIO_EMBEDDING_API_KEY=<Embedding 服务 Key>
  #      PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple   # 可选，国内加速

  # 2) 初始化怪谈档案库（建表 + NGRAM 全文索引 + 向量索引 + 灌 20 条数据）
  docker compose run --rm radio-archive python /app/scripts/init_archive.py

  # 3) 拉起整套服务
  docker compose up -d

  # 4) 导入 DSL
  #    Dify 控制台 → 工作室 → 导入 DSL → 选择 apps/midnight-radio.yml

  # 5) 配置模型
  #    设置 → 模型供应商 → OpenAI-API-compatible → 填 base_url 与 API Key，
  #    新增模型 MiniMax-M3（chat），再设为默认模型。
EOF
