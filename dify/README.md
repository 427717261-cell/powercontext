# 深夜电台 · 怪谈档案

> Dify 编排 × seekdb 混合检索 × PowerContext 跨会话记忆
> —— 赛题「Dify × OceanBase · Just for Fun」参赛作品

一个只在深夜开播的电台。你给自己起个**听众代号**，就可以点播都市怪谈、接主播的梗、
说今天很累。主播会去档案馆翻卷宗（seekdb），也会记得你上次说过什么（PowerContext）。

> **从哪看起**：
> - 评测平台 / 评审 → 看 [`SUBMISSION_INFO.md`](./SUBMISSION_INFO.md)（提测信息速查：仓库分支、作品目录、最短复现路径、两条样例结论）
> - 想按赛题「四、提交材料」逐条核对 → 看 [`SUBMISSION.md`](./SUBMISSION.md)（对照表）
> - 想自己跑一遍 → 看「[5. 部署步骤](#5-部署步骤)」和 [`VERIFICATION.md`](./VERIFICATION.md)
> - 运行证据原文 → 看 [`evidence/`](./evidence/)

---

## 1. 作品创意与玩法

**一句话**：这是一个「有记性」的深夜电台主播。

- **听众代号**：会话开场时填一个代号（可留空）。代号决定你的 Scope——同一个代号，
  永远是同一个听众，跨会话、跨时间都认得。
- **点播**：说「想听末班地铁的怪谈」，电台后端会用**全文 + 向量**两条通道去
  seekdb 的怪谈档案库里翻卷宗，挑出最贴题的 2 条，附在播报后面。
- **接梗与情绪**：说「我今天特别累」「今天遇到个灵异客户」，主播会先接住情绪再讲稿子。
- **越聊越离奇**：每一轮你说过的话和用到的卷宗都会被写回记忆，下一次你来，
  主播会主动提起上次的卷宗并给你 A/B 两个新选项。

**体验方式**：Dify 自带的 WebApp（对话界面）即可，无需另做前端。

---

## 2. 技术结合与分工

| 组件 | 版本 | 在本作品里承担什么 |
| --- | --- | --- |
| **Dify** | 1.4.2（`langgenius/dify-api:1.4.2` / `dify-web:1.4.2` / `dify-sandbox:0.2.12` / `dify-plugin-daemon:0.1.2-local`） | 核心流程编排：输入处理 → HTTP 调用 → 代码节点 → LLM 主播 → 输出；模型调用、对话界面、工作流执行记录 |
| **seekdb**（OceanBase 轻量版） | 5.7.25-OceanBase seekdb-v1.4.0.0 | **怪谈档案库**：20 条怪谈档案，承载 `VECTOR` 列 + NGRAM 全文索引 + HNSW 向量索引；电台后端用**同一条 SQL**同时走全文与向量两条通道并做标签过滤 |
| **PowerContext** | 1.4.1（PyPI `powercontext[cli,server]`） | **听众记忆**：Scope 幂等建立、跨会话召回（`/v1/context/prepare`）、本轮记忆写回（`/v1/memory/remember`） |
| MiniMax-M3 | 通过 Dify 的 `langgenius/openai_api_compatible` 插件接入 | 主播话术生成（LLM） |
| embo-01 | MiniMax Embedding | 档案入库与检索时的向量（维度 1536） |

**一句话分工**：Dify 负责「流程与嘴」，seekdb 负责「档案与检索」，PowerContext 负责「记性」。

> 补充说明：PowerContext 服务端默认使用官方支持的 SQLite 后端落盘听众记忆。
> 它的 `oceanbase` 后端要求完整 OceanBase 租户视图（`oceanbase.GV$OB_PARAMETERS`、
> `oceanbase.DBA_OB_TENANTS`），seekdb 轻量版不提供这些视图，实测无法直接对接；
> 内嵌 seekdb 后端（`pylibseekdb`）在本机内存下无法完成启动。因此本作品里
> **seekdb 承担的是「应用数据存储 + 检索」这一角色**（且是主力计算负载），
> PowerContext 承担「记忆与上下文」这一角色，二者分工清晰、互不替代。

---

## 3. 目录结构

```
dify/
├── README.md                         # 本文件：运行说明
├── VERIFICATION.md                   # 验证样例：步骤、预期表现、实际运行记录
├── SUBMISSION.md                     # 提交材料对照表（赛题四·提交材料逐条对应）
├── SUBMISSION_INFO.md                # 提测信息速查（评测平台/评审入口）
├── requirements.txt                  # 配套后端的 Python 依赖
├── apps/
│   └── midnight-radio.yml            # Dify DSL（Chatflow，11 个节点）
├── data/
│   └── archive.json                  # 20 条怪谈档案的初始数据
├── deploy/
│   ├── docker-compose.override.yaml  # Dify 部署覆盖：pgvector + 内存上限 + 两个配套后端服务
│   ├── Dockerfile.radio              # 电台检索后端镜像
│   ├── Dockerfile.powercontext       # PowerContext 服务端镜像
│   └── seekdb/                       # seekdb 部署配置（赛题 4.4 要求）
│       ├── README.md                 #   版本 / 用途 / 部署配置 / 初始化 / 读写验证
│       ├── install_seekdb.sh         #   安装 + 写配置 + 启动
│       ├── seekdb.cnf                #   实际使用的配置文件
│       ├── init_account.sql          #   建库 + 建最小权限账号
│       └── verify_seekdb.sh          #   实际读写与混合检索验证
├── evidence/                         # 运行证据（节点执行记录、记忆读写、检索记录）
│   └── screenshots/                  # 界面截图佐证（11 张 + 说明 README）
├── scripts/
│   ├── install_dify.sh               # 一键把作品接入已有 Dify 部署
│   ├── make_dify_env.py              # 生成 Dify .env（随机口令）
│   ├── init_archive.py               # 初始化 seekdb 怪谈档案库
│   ├── verify_samples.py             # 跑两组验证样例并落证据
│   └── collect_evidence.py           # 采集运行证据（节点/记忆/检索）
└── service/
    └── radio_archive.py              # 电台检索后端（HTTP 服务）
```

---

## 4. 运行环境

| 项目 | 版本 / 要求 |
| --- | --- |
| 操作系统 | Linux x86_64（开发与验证环境：OpenCloudOS 9.4 / x86_64） |
| Docker | 24+（验证环境：Docker 28.0.1 / compose 2.32.1） |
| Python | 3.12（仅本机直接跑后端时需要；容器内已自带） |
| 内存 | 建议 ≥ 4GB（compose 已给每个服务设 `mem_limit`） |
| 磁盘 | ≥ 10GB 空闲（镜像合计约 5GB） |
| 网络 | 需要能访问 `marketplace.dify.ai`（装插件）、`api.minimaxi.com`（LLM + Embedding）、`pypi.org`（pip） |
| seekdb | OceanBase seekdb 轻量版 **1.4.0.0**（rpm 包 `seekdb-1.4.0-100000172026082615.el7.x86_64`；SQL 层版本串 `5.7.25-OceanBase seekdb-v1.4.0.0`），监听 **2881**（MySQL 协议）；部署配置见 [`deploy/seekdb/README.md`](./deploy/seekdb/README.md) |

> 国内机器建议在 `.env` 里加 `PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple`，
> 镜像可用 `docker.1ms.run/`、`docker.m.daocloud.io/` 等代理加速。

---

## 5. 部署步骤

### 5.1 部署 seekdb（怪谈档案库）

本作品的档案库跑在 seekdb（OceanBase 轻量版）里，**不是 Docker 容器**——seekdb 官方只提供
rpm / deb 包与 systemd 单元。完整的版本、配置、目录布局、账号、初始化与读写验证见
[`deploy/seekdb/README.md`](./deploy/seekdb/README.md)，这里只给最短路径。

**版本**：`OceanBase seekdb 1.4.0.0`
（rpm 包 `seekdb-1.4.0-100000172026082615.el7.x86_64`；
SQL 层 `SELECT VERSION()` 返回 `5.7.25-OceanBase seekdb-v1.4.0.0`，这是对 MySQL 客户端暴露的兼容版本串）。

```bash
# 安装 + 写配置 + 启动，一条脚本搞定（幂等）
sudo bash deploy/seekdb/install_seekdb.sh
```

脚本做三件事：官方 yum / apt 仓库装 seekdb → 把 `deploy/seekdb/seekdb.cnf` 写到
`/etc/seekdb/seekdb.cnf` → `systemctl enable --now seekdb` 并等 2881 端口就绪。
（官方 rpm 的单元默认是 `disabled`，必须显式 `enable` 才会开机自启。）

装好后的实际形态（验证环境实测）：

```
/usr/bin/seekdb --nodaemon --base-dir=/var/lib/seekdb \
  --data-dir=/var/lib/seekdb/store --redo-dir=/var/lib/seekdb/store/redo \
  --port=2881 --parameter cpu_count=4
```

监听 `0.0.0.0:2881`（MySQL 协议），数据在 `/var/lib/seekdb/store/`。

**建库 + 建最小权限账号**（需要 seekdb 的 root 账号，口令用你安装时设置的那个；
先把脚本里的 `<radio_口令>` 换成自己的值）：

```bash
mysql -h 127.0.0.1 -P 2881 -u root -p < deploy/seekdb/init_account.sql
```

它会建 `radio_archive` 库和只对该库有权限的 `radio` 账号。实测授权结果：

```
GRANT USAGE ON *.* TO 'radio'
GRANT ALL PRIVILEGES ON `radio_archive`.* TO 'radio'
```

> 本作品验证环境里 seekdb 跑在**宿主机**上，Dify 容器通过 `host.docker.internal:2881` 访问，
> override 已为电台后端配好 `extra_hosts: ["host.docker.internal:host-gateway"]`。
> 如果你把 seekdb 也放进 compose，把 `.env` 里的 `RADIO_SEEKDB_HOST` 改成服务名即可。
>
> **想立刻确认 seekdb 真的在读写**，随时可以跑一次验证脚本（不改档案表里的任何一行）：
>
> ```bash
> bash deploy/seekdb/verify_seekdb.sh
> ```
>
> 它会打印版本与监听、档案表行数与索引清单，并建一张临时表实测
> **NGRAM 全文命中** + **HNSW 向量命中**，再用 EXPLAIN 确认两条通道分别走了
> `TEXT RETRIEVAL SCAN` 与 `VECTOR INDEX ADAPTIVE SCAN`，最后删掉临时表。

### 5.2 准备 Dify 1.4.2

```bash
git clone --depth 1 --branch 1.4.2 https://github.com/langgenius/dify.git
cd dify/docker
```

（也可直接用官方发布包，目录里须有 `docker-compose.yaml` 与 `.env.example`。）

### 5.3 一键接入本作品

```bash
# 假设本作品目录为 <repo>/dify
bash <repo>/dify/scripts/install_dify.sh <repo>/dify/docker
```

脚本会把 `docker-compose.override.yaml`、两个 Dockerfile、`service/`、`scripts/`、`data/`
复制进 Dify 的 `docker/` 目录，并基于官方 `.env.example` 生成 `.env`（口令随机生成）。

### 5.4 补齐凭据

在 `<dify>/docker/.env` 末尾追加（**真实值不要提交进仓库**）：

```bash
# seekdb（怪谈档案库）
RADIO_SEEKDB_HOST=host.docker.internal   # seekdb 跑在宿主机时用这个；跑在 compose 里就填服务名
RADIO_SEEKDB_PORT=2881
RADIO_SEEKDB_USER=<你的 seekdb 账号>
RADIO_SEEKDB_PASSWORD=<你的 seekdb 口令>
RADIO_SEEKDB_DATABASE=radio_archive
RADIO_ARCHIVE_TABLE=radio_archive

# Embedding 服务（MiniMax）
RADIO_EMBEDDING_API_BASE=https://api.minimaxi.com/v1
RADIO_EMBEDDING_API_KEY=<你的 MiniMax API Key>
RADIO_EMBEDDING_MODEL=embo-01

# 可选：国内 pip 加速
PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
```

> **MiniMax API Key 从哪来**：在 MiniMax 开放平台（`platform.minimaxi.com`）登录后，
> 于「接口密钥」页新建一个 Key。本作品用到同一账号的两个能力，共用这一个 Key：
>
> | 能力 | 模型 | 谁调用 |
> | --- | --- | --- |
> | 对话（主播话术） | `MiniMax-M3` | Dify 的 `openai_api_compatible` 插件（控制台配置） |
> | 向量（档案检索） | `embo-01`（1536 维） | `init_archive.py` 与电台检索后端 |
>
> Key 只写进 `<dify>/docker/.env`，**不要提交进仓库**。用量计费按 Token Plan，
> 额度用尽时 LLM 节点会返回 429（`已达到 Token Plan 用量上限`），此时对话会失败但检索链路仍正常。

seekdb 侧的账号与库这样建（最小权限，已在 5.1 做过可跳过；
完整脚本见 [`deploy/seekdb/init_account.sql`](./deploy/seekdb/init_account.sql)）：

```sql
CREATE DATABASE IF NOT EXISTS radio_archive DEFAULT CHARACTER SET utf8mb4;
CREATE USER 'radio'@'%' IDENTIFIED BY '<强口令>';
GRANT ALL PRIVILEGES ON `radio_archive`.* TO 'radio'@'%';
FLUSH PRIVILEGES;
```

> **seekdb 必须支持 NGRAM 全文解析器**。轻量版 seekdb v1.4.0.0 实测支持
> `CREATE FULLTEXT INDEX ... WITH PARSER NGRAM`；中文档案若用默认的 SPACE 解析器，
> 整句会被当成一个词，检索永远命中不了。

### 5.5 初始化怪谈档案库

```bash
cd <dify>/docker
docker compose run --rm radio-archive python /app/scripts/init_archive.py
```

脚本会：建表（`VECTOR(1536)` 列）→ 建 NGRAM 全文索引 → 建 HNSW 向量索引 →
调 Embedding 接口生成 20 条档案的向量并灌库。脚本幂等，重复执行会重建表。

输出示例：

```
档案条目：20 条
数据库 radio_archive 就绪
向量维度：1536
表 radio_archive 与全文(NGRAM)/向量索引就绪
已写入 20 条怪谈档案
```

### 5.6 拉起整套服务

```bash
cd <dify>/docker
docker compose up -d
docker compose ps        # api / web / db / redis / sandbox / plugin_daemon / ssrf_proxy / nginx / radio-archive / powercontext
```

**创建管理员账号（第一次部署必做）**：

`.env` 里 `INIT_PASSWORD` 留空时，Dify 首次启动会进入**初始化页**，
浏览器打开 `http://<host>:<EXPOSE_NGINX_PORT>/install`，在页面上自行创建管理员账号。
如果你希望跳过这一步、直接用固定账号登录，在 `.env` 里设置 `INIT_PASSWORD=<你的口令>`，
Dify 启动时会自动创建管理员 `root@localhost`，口令即该值。

> 本作品的验证环境用的是 `INIT_PASSWORD` + 邮箱 `host@midnight-radio.local`，
> **那只是我们环境里的账号，不随作品交付**。请用你自己的账号，
> 后面的 `verify_samples.py --email/--password` 填你自己的即可。
>
> 端口口径：`EXPOSE_NGINX_PORT` 决定控制台与 WebApp 的对外端口，
> `make_dify_env.py` 默认生成 **80**；我们的验证机把它改成了 **8080**，
> 所以本文与截图里出现的 `:8080` 只是那台机器的取值，改 `.env` 里的
> `EXPOSE_NGINX_PORT` 即可，**不要把 8080 当成固定要求**。

### 5.7 安装模型插件并注册模型

Dify 1.4.2 的模型供应商由插件提供，首次使用需要装一次：

1. 控制台 → 插件 → 商店 → 搜索 **OpenAI-API-compatible**
   （`langgenius/openai_api_compatible`，验证版本 `0.0.73`）→ 安装。
2. 设置 → 模型供应商 → OpenAI-API-compatible → 填：
   - API Base URL：`https://api.minimaxi.com/v1`
   - API Key：你的 MiniMax Key
   - 模型名称：`MiniMax-M3`，对话类型 `chat`，上下文长度 `204800`
3. 把 `MiniMax-M3` 设为默认模型。

### 5.8 导入 DSL

控制台 → 工作室 → 导入 DSL → 选择 `dify/apps/midnight-radio.yml` → 导入 → 发布。

DSL 内置三个环境变量，默认值已指向 compose 里的服务名，通常无需改动：

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `POWERCONTEXT_BASE_URL` | `http://powercontext:8100` | PowerContext 服务端地址 |
| `POWERCONTEXT_TOKEN` | 空 | 访问令牌，留空表示不鉴权 |
| `RADIO_API_BASE` | `http://radio-archive:8801` | 电台检索后端地址 |

> 如果你把后端跑在 compose 之外，改成对应地址即可（例如 `http://127.0.0.1:8801`，
> 或 `http://<你的服务器IP>:8801`）。注意 Dify 的出站请求会经过 `ssrf_proxy`，
> 容器名（服务名）在同一 compose 网络内可正常解析。

### 5.9 DSL 说明与「重新绑定地址」怎么做

**本作品只有一个 DSL 文件**，导入顺序就是它本身，没有先后依赖：

| 文件 | 形态 | 用途 | 导入顺序 |
| --- | --- | --- | --- |
| `apps/midnight-radio.yml` | Dify Chatflow（`mode: advanced-chat`），11 个节点 | 整个应用：开场变量「听众代号」→ 组装请求 → 建/复用 Scope → 召回记忆 → 检索档案 → 整理素材 → 写回记忆 → 主播生成 → 回答 | 第 1 个（唯一一个） |

- **适用版本**：Dify **1.4.2**（`langgenius/dify-api:1.4.2` / `dify-web:1.4.2` /
  `dify-sandbox:0.2.12` / `dify-plugin-daemon:0.1.2-local`）。
- **部署方式**：官方 `docker-compose.yaml` + 本目录的 `docker-compose.override.yaml`；
  **未修改 Dify 任何源码**，只新增覆盖文件与两个配套后端镜像。
- **DSL 里不含知识库**：本作品没有使用 Dify 的知识库功能，档案数据在 seekdb 里、由配套后端检索，
  因此不存在需要重新绑定的「知识库连接信息」。DSL 里要绑定的只有下面三个环境变量。
- 导入后一定要**发布**一次工作流（导入得到的是草稿），WebApp 与 API 才会跑到已发布的版本。

**重新绑定服务地址的具体步骤**（后端不在 compose 里、换了域名或端口时）：

1. 控制台 → 工作室 → 打开「深夜电台 · 怪谈档案」→ 顶部切到 **编排** 标签；
2. 左侧/画布上的 **环境变量** 面板（`{}` 图标）里改这三项：

   | 变量 | 默认值 | 改成 |
   | --- | --- | --- |
   | `POWERCONTEXT_BASE_URL` | `http://powercontext:8100` | PowerContext 的实际地址，如 `http://127.0.0.1:8100` |
   | `POWERCONTEXT_TOKEN` | 空 | PowerContext 开了鉴权时填访问令牌，否则留空 |
   | `RADIO_API_BASE` | `http://radio-archive:8801` | 电台检索后端的实际地址，如 `http://127.0.0.1:8801` |

3. 改完点右上角 **发布**，让新的环境变量生效；
4. 若 PowerContext 开了鉴权：DSL 里 `http_scope` / `http_recall` / `http_save` 三个 HTTP 节点
   目前都是 `authorization.type = no-auth`，需要在编排里把它们改成 **Bearer** 并填同一个 Token。

> Dify 的出站请求会经过 `ssrf_proxy`，容器名（服务名）在同一 compose 网络内可正常解析；
> 后端跑在宿主机上时用 `http://host.docker.internal:8801`
> （Linux 需要 `extra_hosts: ["host.docker.internal:host-gateway"]`，override 里已为电台后端配好）。

---

## 6. 应用入口

| 入口 | 地址 |
| --- | --- |
| Web 控制台 | `http://<host>:<EXPOSE_NGINX_PORT>/`（默认 80；验证机是 8080） |
| **WebApp 对话界面** | `http://<host>:<EXPOSE_NGINX_PORT>/chat/<应用的 access_code>` |
| Dify API（控制台） | `http://<host>:5001/console/api/...` |
| 应用公开 API | `http://<host>:5001/v1/chat-messages`（需应用 API Key） |
| 电台检索后端 | `http://127.0.0.1:8801/healthz`、`POST /archive/search` |
| PowerContext | `http://127.0.0.1:8100/health/ready`、`/v1/capabilities` |

> `access_code` 在控制台的应用「发布 → WebApp」页面拿到；也可以用
> `scripts/verify_samples.py` 跑样例时自动建好并打印。

> 两个配套后端默认**只绑定 127.0.0.1**（见 `deploy/docker-compose.override.yaml`）：
> PowerContext 的记忆接口默认无鉴权，不宜直接暴露到公网。需要从别的机器访问时，
> 把 override 里的 `EXPOSE_POWERCONTEXT_BIND` / `EXPOSE_RADIO_BIND` 改成 `0.0.0.0` 即可。
> Dify 容器内部一律用服务名互访（`http://powercontext:8100`、`http://radio-archive:8801`）。

体验路径：控制台 → 工作室 → 打开「深夜电台 · 怪谈档案」→ 点击「开始对话」→
在开场表单里填**听众代号** → 开始聊天。

---

## 7. 验证样例

见 [`VERIFICATION.md`](./VERIFICATION.md)。也可以直接跑：

```bash
# 1) 跑两组样例（导入 DSL → 发布 → 建 Key → 跑样例）
python3 dify/scripts/verify_samples.py \
  --base-url http://127.0.0.1:5001 \
  --email host@midnight-radio.local \
  --password '<控制台密码>' \
  --out-dir dify/evidence

# 2) 采集运行证据（节点执行记录、PowerContext 记忆读写、seekdb 检索记录）
python3 dify/scripts/collect_evidence.py \
  --out-dir dify/evidence \
  --listener 小满 \
  --powercontext http://127.0.0.1:8100
```

> `verify_samples.py` 默认会先清掉同名旧应用再重新导入，因此会生成新的应用与站点；
> 如果 DSL 已经在控制台导入过，用 `--reuse-app <app_id>` 复用它，
> 只重跑样例、不动应用（这样已有的 WebApp 访问地址不会变）。
>
> `collect_evidence.py` 会读取 `dify/evidence/03_chat_results.json`，
> 把 Dify 侧的节点执行记录与工作流记录**限定到那两组样例的运行上**，
> 这样证据文件里的每一条记录都能对应到 `VERIFICATION.md` 里的样例。

---

## 8. 已知限制

1. **Dify 出站请求经 squid 代理**：compose 默认把 api/worker 的 HTTP(S) 流量交给
   `ssrf_proxy`。它不拦截内网目标，但**服务名必须在同一 compose 网络内**；
   若把后端挪到宿主机，请填宿主机可达的地址。
2. **中文全文检索依赖 NGRAM 解析器**：若你的 seekdb 版本不支持
   `WITH PARSER NGRAM`，全文通道会建索引失败；此时可只保留向量通道（把
   `service/radio_archive.py` 里的 `_SEARCH_SQL` 换成 `_FTS_ONLY_SQL` 的相反版本即可）。
3. **PowerContext 的记忆后端是 SQLite**：文件在 `volumes/powercontext/`。
   换成 OceanBase 需按 PowerContext 文档改 `POWERCONTEXT_SERVER_DATABASE_KIND`。
4. **向量维度依赖 Embedding 模型**：`embo-01` 实测 1536 维。换了 Embedding 模型必须
   重新跑 `init_archive.py`（脚本按接口返回的实际维度建列）。
5. **单机内存紧张**：本作品验证环境只有 3.6GB 内存，因此 compose 给每个服务都加了
   `mem_limit`；如果你不需要低配模式，删掉覆盖文件里的 `mem_limit` 即可。

---

## 9. 第三方来源与改动说明

- Dify 1.4.2：Apache-2.0，来自 <https://github.com/langgenius/dify>。本作品**未修改**
  Dify 源码，只新增了 `docker-compose.override.yaml`（部署覆盖）与两个后端镜像。
- PowerContext 1.4.1：Apache-2.0，来自 <https://github.com/oceanbase/powercontext>
  （PyPI 包 `powercontext[cli,server]==1.4.1`）。**未修改**其源码，只按官方 HTTP 契约调用。
- seekdb：OceanBase 轻量版，来自 <https://github.com/oceanbase/seekdb>。**未修改**。
- `service/radio_archive.py`、`scripts/*.py`、`deploy/*`、`apps/midnight-radio.yml`、
  `data/archive.json` 均为本作品原创。
- 怪谈档案内容（`data/archive.json`）为原创虚构故事，非真实灵异事件记录。
- 依赖的第三方 Python 包见 `requirements.txt`，均为官方 PyPI 包，未做二次分发。
