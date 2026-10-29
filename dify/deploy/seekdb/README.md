# seekdb 部署配置（作品「怪谈档案库」的承载数据库）

> 对应赛题「四、4.4 按依赖补充的材料 → 使用 seekdb 部署 Dify」一行。
> 本文件把 **seekdb 版本、数据库用途、部署配置、初始化方式、实际读写验证步骤** 一次性交代清楚。

## 1. 版本

| 口径 | 值 | 怎么看到的 |
| --- | --- | --- |
| 二进制版本 | `OceanBase seekdb 1.4.0.0` | `seekdb --version` |
| 源码 revision | `100000172026082615-0003f8d4b32a2cfe3b75ae8f4dbe26fcee994541` | `seekdb --version` 的 `REVISION` |
| 发行包 | `seekdb-1.4.0-100000172026082615.el7.x86_64`（rpm） | `rpm -qi seekdb` |
| SQL 层版本串 | `5.7.25-OceanBase seekdb-v1.4.0.0` | `SELECT VERSION();` |
| 构建时间 | 2026-08-26 | `seekdb --version` 的 `BUILD_TIME` |
| 许可证 | Apache 2.0（OceanBase Inc.） | `rpm -qi seekdb` |

MySQL 协议兼容层报的是 `5.7.25-OceanBase seekdb-v1.4.0.0`，这是 seekdb 对 MySQL 客户端
暴露的兼容版本号，**seekdb 自身版本是 1.4.0.0**，两个都要写清楚以免评审对不上。

## 2. 数据库用途（本作品里 seekdb 到底干什么）

| 用途 | 说明 |
| --- | --- |
| **应用数据存储** | 怪谈档案库 `radio_archive.radio_archive` 表，20 条怪谈档案（标题、标签、地域、正文、向量） |
| **混合检索** | 同一张表上同时挂 **NGRAM 全文索引**（`ft_radio_archive`）与 **HNSW 向量索引**（`idx_radio_archive_embedding`），检索时用**同一条 SQL** 走全文 + 向量两条通道再做标签过滤 |
| **不是** | 不是 Dify 自身的内部数据库。Dify 1.4.2 官方只支持 Postgres / MySQL 系，**未提供 seekdb 适配分支**，所以 Dify 的内部库用 `pgvector/pgvector:pg16`；seekdb 承担的是赛题允许的「应用数据存储 + 检索」这一角色（题面原文：*只要 seekdb 实际承担所声明的数据存储职责……即满足 seekdb 的使用要求*） |

> PowerContext 负责「听众记忆」，seekdb 负责「档案与检索」，Dify 负责「流程与嘴」，
> 三者分工见 `README.md` 第 2 节。

## 3. 部署配置

### 3.1 安装方式

官方 yum / apt 仓库安装（**不是 Docker 镜像**，seekdb 官方未提供容器镜像）：

```bash
curl -fsSL -o /tmp/seekdb_install.sh \
  https://obportal.s3.ap-southeast-1.amazonaws.com/download-center/opensource/seekdb/seekdb_install.sh
sudo bash /tmp/seekdb_install.sh
```

该脚本会配置 OceanBase 仓库并安装 `seekdb` 包，同时落下一份 systemd 单元
`/usr/lib/systemd/system/seekdb.service`。

> 本目录的 `install_seekdb.sh` 把上面这套流程连同配置写入、启动、等就绪打包成一个脚本，
> 评审可以直接跑 `bash install_seekdb.sh`。

### 3.2 配置文件

seekdb 读 `/etc/seekdb/seekdb.cnf`。本作品验证环境里实际使用的配置
（本目录 `seekdb.cnf` 与之逐字一致）：

```ini
# 这些参数永久有效
base-dir=/var/lib/seekdb
data-dir=/var/lib/seekdb/store
redo-dir=/var/lib/seekdb/store/redo

# 这些参数仅在初始化时有效
port=2881
cpu_count=4
```

官方 systemd 启动脚本 `/usr/libexec/seekdb/scripts/seekdb_systemd_start` 会逐行读这个文件：
`base-dir` / `data-dir` / `redo-dir` / `port` 拼进命令行，其余键值以
`--parameter key=value` 传给实例。

> ⚠️ `port` 与 `cpu_count` **只在初始化阶段生效**。已有数据目录时重启只保留 `--base-dir`，
> 想换端口必须清空数据目录重新初始化。本作品固定用默认端口 2881。

### 3.3 目录布局

| 路径 | 内容 |
| --- | --- |
| `/usr/bin/seekdb` | 主程序（约 169 MB 的 ELF 可执行文件） |
| `/usr/bin/obshell` | 运维工具 |
| `/etc/seekdb/` | 配置（`seekdb.cnf` + 若干系统变量 JSON） |
| `/usr/libexec/seekdb/` | 启停脚本、时区 / SRS 数据导入脚本 |
| `/usr/share/seekdb/admin/` | 内部系统视图的 SQL 定义 |
| `/var/lib/seekdb/` | 数据根目录（`etc/` `log/` `run/` `store/`） |
| `/var/lib/seekdb/store/` | `redo/`（redo log）、`slog/`、`sstable/`（数据文件） |

### 3.4 启动

```bash
# 官方 rpm 提供的 seekdb.service 默认是 disabled，必须显式 enable
sudo systemctl enable --now seekdb
sudo systemctl status seekdb --no-pager
```

实际跑起来的命令行（由上面的脚本拼装）：

```
/usr/bin/seekdb --nodaemon --base-dir=/var/lib/seekdb \
  --data-dir=/var/lib/seekdb/store --redo-dir=/var/lib/seekdb/store/redo \
  --port=2881 --parameter cpu_count=4
```

监听：`0.0.0.0:2881`（MySQL 协议）。

> 本作品部署里 seekdb 跑在**宿主机**上，Dify 容器通过 `host.docker.internal:2881` 访问
> （override 里为电台后端配了 `extra_hosts: ["host.docker.internal:host-gateway"]`）。
> 如果你把 seekdb 也放进 compose，直接把 `RADIO_SEEKDB_HOST` 改成服务名即可。

### 3.5 账号与库

本作品用**最小权限账号**，只给 `radio_archive` 库上的权限：

```sql
CREATE DATABASE IF NOT EXISTS `radio_archive`
  DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_bin;
CREATE USER 'radio'@'%' IDENTIFIED BY '<radio_口令>';
GRANT ALL PRIVILEGES ON `radio_archive`.* TO 'radio'@'%';
FLUSH PRIVILEGES;
```

实测授权结果：

```
Grants for radio@%
GRANT USAGE ON *.* TO 'radio'
GRANT ALL PRIVILEGES ON `radio_archive`.* TO 'radio'
```

完整脚本见本目录 `init_account.sql`（用 seekdb 的 root 账号执行）。

## 4. 初始化方式

```bash
# 1) 建表 + NGRAM 全文索引 + HNSW 向量索引 + 灌 20 条档案与向量
#    （在 Dify 的 docker 目录里执行；凭据来自 .env）
docker compose run --rm radio-archive python /app/scripts/init_archive.py
```

脚本 `scripts/init_archive.py` 是幂等的（先 DROP 后 CREATE），做的事情：

1. `CREATE DATABASE IF NOT EXISTS radio_archive`（无建库权限时会跳过并提示）；
2. 调 `embo-01` 生成 20 条档案的向量（实测维度 **1536**）；
3. 建表，`embedding` 列类型 `VECTOR(1536)`；
4. `CREATE FULLTEXT INDEX ... WITH PARSER NGRAM` —— **中文必须用 NGRAM**，
   默认的 SPACE 解析器会把整句当成一个词，检索永远命中不了；
5. `CREATE VECTOR INDEX ... WITH (distance=l2, type=hnsw)`；
6. 灌入 `data/archive.json` 的 20 条档案并回读计数。

建完之后的表结构（实测 `SHOW CREATE TABLE`）：

```sql
CREATE TABLE `radio_archive` (
  `story_id` varchar(64) COLLATE utf8mb4_bin NOT NULL,
  `title` varchar(200) COLLATE utf8mb4_bin NOT NULL,
  `tags` varchar(255) COLLATE utf8mb4_bin NOT NULL DEFAULT '',
  `region` varchar(64) COLLATE utf8mb4_bin NOT NULL DEFAULT '',
  `content` mediumtext COLLATE utf8mb4_bin NOT NULL,
  `searchable_text` mediumtext COLLATE utf8mb4_bin NOT NULL,
  `embedding` VECTOR(1536) DEFAULT NULL,
  PRIMARY KEY (`story_id`),
  FULLTEXT KEY `ft_radio_archive` (`searchable_text`)
    WITH PARSER ngram PARSER_PROPERTIES=(ngram_token_size=2) BLOCK_SIZE 16384,
  VECTOR KEY `idx_radio_archive_embedding` (`embedding`)
    WITH (DISTANCE=L2, TYPE=HNSW, LIB=VSAG, M=16, EF_CONSTRUCTION=200, EF_SEARCH=64)
    BLOCK_SIZE 16384
) ... CHARSET = utf8mb4 COLLATE = utf8mb4_bin ...;
```

## 5. 实际读写验证步骤

一条命令跑完，随时可重跑：

```bash
bash deploy/seekdb/verify_seekdb.sh
```

它做四件事：

| 步骤 | 做什么 | 期望看到 |
| --- | --- | --- |
| 1 | 打印 `SELECT VERSION()`、监听端口、实际启动命令行 | `5.7.25-OceanBase seekdb-v1.4.0.0`、`0.0.0.0:2881` |
| 2 | **读**：档案表行数 + 索引清单 | 行数 `20`；`SHOW INDEX` 里同时出现 `ft_radio_archive`（`Index_type=FULLTEXT`）与 `idx_radio_archive_embedding`（`Index_type=VECTOR`） |
| 3 | **写 + 读 + 删**：建临时表 `seekdb_rw_probe`（FULLTEXT NGRAM + VECTOR HNSW），插入一行中文内容与一个向量 | 3a 全文通道命中 `probe-001`；3c 向量通道命中 `probe-001`（距离≈0）；3d 的 EXPLAIN 里同时出现 `TEXT RETRIEVAL SCAN` 与 `VECTOR INDEX ADAPTIVE SCAN`；3e 的 UNION ALL 混合检索返回 `fts` 与 `vector` 两种 `matched_by` |
| 4 | 删除临时表 | 库里只剩 `radio_archive` |

脚本**不会改动 `radio_archive` 表里的任何一行**，临时表用完即删。

> 索引清单以 `SHOW INDEX` 为准：`information_schema.statistics` 会把向量索引显示成
> 一组以 `__vid_*` / `__type_*` 命名的内部 BTREE 索引，**看不到 `VECTOR` 字样**，
> 容易误判成「没有向量索引」。`SHOW INDEX` 的 `Index_type` 列才准。

> 向量字面量的取法有个坑：`CAST(embedding AS CHAR)` 会报
> `ERROR 5083 Invalid data type for the operation`，`SET @v = (SELECT embedding ...)` 会报
> `ERROR 1235 Variable value type not supported`。
> **正确写法是直接 `SELECT embedding FROM ...`**（客户端拿到的就是 `[0.012456,...]` 这种字面量），
> 或者用 `INSERT ... SELECT` 直接拷贝向量。

已归档的运行记录见 `evidence/05_seekdb_archive.json`、`06_seekdb_indexes.json`、
`07_seekdb_explain.txt`（`TEXT RETRIEVAL SCAN` 与 `VECTOR INDEX ADAPTIVE SCAN` 同现）。

## 6. 已知限制

- seekdb 轻量版**不提供**完整 OceanBase 的租户视图（`oceanbase.GV$OB_PARAMETERS`、
  `oceanbase.DBA_OB_TENANTS`），因此 PowerContext 的 `oceanbase` 后端无法直接对接它；
  本作品 PowerContext 改用官方支持的 SQLite 后端（见 `README.md` 第 2 节说明）。
- `port` / `cpu_count` 只在初始化时生效，改端口需重新初始化。
- 官方安装脚本走 OceanBase 的 yum / apt 仓库，需要能访问 `mirrors.oceanbase.com`。
