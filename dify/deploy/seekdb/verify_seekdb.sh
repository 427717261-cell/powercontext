#!/usr/bin/env bash
# 验证 seekdb 确实在承担「应用数据存储 + 混合检索」——赛题 4.4 要求的「实际读写验证步骤」。
#
#   bash verify_seekdb.sh
#
# 连接参数按下面的优先级取值：
#   环境变量 SEEKDB_HOST / SEEKDB_PORT / SEEKDB_USER / SEEKDB_PASSWORD /
#            SEEKDB_DATABASE / SEEKDB_TABLE
#   否则从 Dify 的 .env 里读 RADIO_SEEKDB_*（可用 DIFY_ENV 指定 .env 路径）
#
# 脚本做四件事：
#   1. 打印版本、监听端口与实际启动命令行；
#   2. 读：档案表行数 + 索引清单（应当同时有 FULLTEXT/NGRAM 与 VECTOR/HNSW）；
#   3. 写 + 读 + 删：在同库里建一张临时探针表，插入一行中文内容与一个向量，
#      分别用 MATCH(... ) AGAINST 和 l2_distance(... ) APPROXIMATE 命中它，
#      再用 EXPLAIN 确认两条通道确实走了各自的索引；
#   4. 跑一条与应用里完全一致的 UNION ALL 混合检索语句，然后清理探针表。
#
# 脚本不会改动 radio_archive 表里的任何一行，探针表用完即删。
set -uo pipefail

HOST="${SEEKDB_HOST:-127.0.0.1}"
PORT="${SEEKDB_PORT:-2881}"
USER_NAME="${SEEKDB_USER:-radio}"
PASSWORD="${SEEKDB_PASSWORD:-}"
DATABASE="${SEEKDB_DATABASE:-radio_archive}"
TABLE="${SEEKDB_TABLE:-radio_archive}"

# 没给口令时，尝试从 Dify 的 .env 里取
if [ -z "${PASSWORD}" ]; then
  ENV_FILE="${DIFY_ENV:-/root/dify-deploy/.env}"
  if [ -f "${ENV_FILE}" ]; then
    PASSWORD="$(grep -E '^RADIO_SEEKDB_PASSWORD=' "${ENV_FILE}" | head -1 | cut -d= -f2-)"
    HOST="${HOST}"
  fi
fi

export MYSQL_PWD="${PASSWORD}"
# 用数组保存命令，否则 "${MYSQL[@]}" 会被当成一个整体命令名
MYSQL=(mysql -h "${HOST}" -P "${PORT}" -u "${USER_NAME}" -D "${DATABASE}")

say () { printf '\n===== %s =====\n' "$1"; }

say "1. 版本与服务"
"${MYSQL[@]}" -e "SELECT VERSION() AS seekdb_version, DATABASE() AS db;" 2>&1 | head -5
if command -v ss >/dev/null 2>&1; then
  ss -lntp 2>/dev/null | grep ":${PORT}" | head -3
fi
if command -v pgrep >/dev/null 2>&1; then
  PID="$(pgrep -f '/usr/bin/seekdb' | head -1)"
  if [ -n "${PID}" ]; then
    printf '启动命令行: '
    tr '\0' ' ' < "/proc/${PID}/cmdline"
    echo
  fi
fi

say "2. 读：档案表与索引"
"${MYSQL[@]}" -e "SELECT COUNT(*) AS archive_rows FROM \`${TABLE}\`;" 2>&1 | head -5
"${MYSQL[@]}" -e "
SELECT INDEX_NAME, INDEX_TYPE, COLUMN_NAME
FROM information_schema.statistics
WHERE TABLE_SCHEMA='${DATABASE}' AND TABLE_NAME='${TABLE}' AND INDEX_NAME IN ('PRIMARY','ft_${TABLE}','idx_${TABLE}_embedding')
ORDER BY INDEX_NAME, SEQ_IN_INDEX;
" 2>&1 | head -15
echo "-- 权威口径：SHOW INDEX（INDEX_TYPE 会直接显示 FULLTEXT / VECTOR）--"
"${MYSQL[@]}" -B -e "SHOW INDEX FROM \`${TABLE}\`;" 2>&1 \
  | awk -F'\t' 'NR==1 {printf "  %-30s %s\n", "Key_name", "Index_type"; next}
                   {printf "  %-30s %s\n", $3, $11}' | head -12

say "3. 写 + 读 + 删：探针表"
"${MYSQL[@]}" -e "
DROP TABLE IF EXISTS seekdb_rw_probe;
CREATE TABLE seekdb_rw_probe (
  probe_id VARCHAR(64) NOT NULL,
  searchable_text MEDIUMTEXT NOT NULL,
  embedding VECTOR(1536) NULL,
  PRIMARY KEY (probe_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;
CREATE FULLTEXT INDEX ft_probe ON seekdb_rw_probe (searchable_text) WITH PARSER NGRAM;
CREATE VECTOR INDEX idx_probe_embedding ON seekdb_rw_probe (embedding) WITH (distance=l2, type=hnsw);
" 2>&1 | head -10

echo "-- 写入一行（向量直接复用档案表里已有的一条向量，无需调用 Embedding 服务）--"
"${MYSQL[@]}" -e "
INSERT INTO seekdb_rw_probe (probe_id, searchable_text, embedding)
SELECT 'probe-001', CONCAT('深夜电台 末班地铁 便利店 怪谈 读写验证探针 ', title), embedding
FROM \`${TABLE}\` LIMIT 1;
SELECT COUNT(*) AS probe_rows FROM seekdb_rw_probe;
" 2>&1 | head -10

echo "-- 3a. 全文通道：MATCH ... AGAINST（应为 probe-001）--"
"${MYSQL[@]}" -e "
SELECT probe_id, MATCH(searchable_text) AGAINST ('末班地铁' IN NATURAL LANGUAGE MODE) AS score
FROM seekdb_rw_probe
WHERE MATCH(searchable_text) AGAINST ('末班地铁' IN NATURAL LANGUAGE MODE);
" 2>&1 | head -10

echo "-- 3b. 取一条向量字面量，供向量通道使用 --"
VEC="$("${MYSQL[@]}" -N -B -e "SELECT embedding FROM \`${TABLE}\` LIMIT 1;" 2>/dev/null | head -1)"
if [ -z "${VEC}" ]; then
  echo "取不到向量字面量（档案表可能还没有向量），跳过向量通道验证。"
else
  echo "向量长度：${#VEC} 字符"

  echo "-- 3c. 向量通道：l2_distance(... ) APPROXIMATE（应为 probe-001，距离≈0）--"
  "${MYSQL[@]}" -e "
  SELECT probe_id, l2_distance(embedding, '${VEC}') AS distance
  FROM seekdb_rw_probe
  WHERE embedding IS NOT NULL
  ORDER BY l2_distance(embedding, '${VEC}') APPROXIMATE
  LIMIT 3;
  " 2>&1 | head -10

  echo "-- 3d. EXPLAIN：确认两条通道分别走 TEXT RETRIEVAL SCAN 与 VECTOR INDEX ADAPTIVE SCAN --"
  "${MYSQL[@]}" -e "
  EXPLAIN SELECT probe_id FROM seekdb_rw_probe
  WHERE MATCH(searchable_text) AGAINST ('末班地铁' IN NATURAL LANGUAGE MODE);
  " 2>&1 | grep -E 'TEXT RETRIEVAL SCAN|VECTOR INDEX|OPERATOR|Query Plan' | head -6
  "${MYSQL[@]}" -e "
  EXPLAIN SELECT probe_id FROM seekdb_rw_probe
  WHERE embedding IS NOT NULL
  ORDER BY l2_distance(embedding, '${VEC}') APPROXIMATE
  LIMIT 3;
  " 2>&1 | grep -E 'TEXT RETRIEVAL SCAN|VECTOR INDEX|OPERATOR|Query Plan' | head -6

  echo "-- 3e. 混合检索：与应用 service/radio_archive.py 完全同构的一条 SQL --"
  "${MYSQL[@]}" -e "
  SELECT * FROM (
    SELECT probe_id, 'fts' AS matched_by FROM seekdb_rw_probe
    WHERE MATCH(searchable_text) AGAINST ('末班地铁' IN NATURAL LANGUAGE MODE)
    LIMIT 3
  ) AS fts_hits
  UNION ALL
  SELECT * FROM (
    SELECT probe_id, 'vector' AS matched_by FROM seekdb_rw_probe
    WHERE embedding IS NOT NULL
    ORDER BY l2_distance(embedding, '${VEC}') APPROXIMATE
    LIMIT 3
  ) AS vector_hits;
  " 2>&1 | head -10
fi

say "4. 清理探针表"
"${MYSQL[@]}" -e "DROP TABLE IF EXISTS seekdb_rw_probe; SHOW TABLES;" 2>&1 | head -10

unset MYSQL_PWD
echo
echo "验证结束：以上 3a/3c 有命中行、3d 里出现 TEXT RETRIEVAL SCAN 与 VECTOR INDEX ADAPTIVE SCAN，"
echo "即说明 seekdb 真实承担了写入、全文检索与向量检索。"
