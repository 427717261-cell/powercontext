# 提交材料对照表

> 作品：**深夜电台 · 怪谈档案**（Dify Chatflow × seekdb 混合检索 × PowerContext 跨会话记忆）
> 赛题：Dify × OceanBase · Just for Fun
> 对应提交分支：`2026-competition-ecosystem`（本作品全部内容在 `dify/` 目录；
> 默认分支 `2026-competition` 的 `dify/` 目录内容完全一致）
> 评测平台 / 评审的入口速查见 [`SUBMISSION_INFO.md`](./SUBMISSION_INFO.md)

本表把赛题「四、提交材料」的每一条要求，映射到本目录里的具体文件与小节，
方便评审逐条核对。

---

## 4.1 Dify DSL

| 要求 | 落在哪 |
| --- | --- |
| 提交完成应用所需的**全部** DSL 文件 | `apps/midnight-radio.yml`（**唯一一个** DSL 文件） |
| 说明各文件的**用途**与**导入顺序** | README「5.9 DSL 说明与『重新绑定地址』怎么做」 |
| 注明适用的 **Dify 版本** | README「4. 运行环境」与 5.9：**Dify 1.4.2**（`langgenius/dify-api:1.4.2` / `dify-web:1.4.2` / `dify-sandbox:0.2.12` / `dify-plugin-daemon:0.1.2-local`） |
| 注明**部署方式** | README「5. 部署步骤」：官方 `docker-compose.yaml` + 本目录 `deploy/docker-compose.override.yaml`，**未修改 Dify 源码** |
| 重新绑定**知识库** | **不适用**：本作品未使用 Dify 知识库功能，档案数据在 seekdb 里由配套后端检索，DSL 中不含知识库连接信息 |
| 重新绑定**工具 / 服务地址**的具体步骤 | README 5.9：控制台 → 编排 → 环境变量 → 改 `POWERCONTEXT_BASE_URL` / `POWERCONTEXT_TOKEN` / `RADIO_API_BASE` → **发布**；开了鉴权时还要把三个 HTTP 节点改成 Bearer |

DSL 内含 11 个节点的 Chatflow：
`start → code_req → http_scope → code_scope → http_recall → code_memory → http_archive → code_save → http_save → llm_host → answer`

---

## 4.2 运行说明（`README.md`）

| 要求 | 小节 |
| --- | --- |
| 作品创意、玩法、核心功能、体验方式 | 「1. 作品创意与玩法」 |
| Dify 与指定技术的**分工** | 「2. 技术结合与分工」（含「一句话分工」）+「3. 目录结构」+ 本文件「4.4 按依赖补充的材料」 |
| 运行环境 / 软件 / 插件版本 | 「4. 运行环境」表 |
| 所需模型服务及配置项 | 「5.7 安装模型插件并注册模型」（`MiniMax-M3`，chat，context 204800） |
| **Embedding / Rerank** 服务说明 | 「4. 运行环境」（`embo-01`，**1536 维**）+「5.4 补齐凭据」；本作品**不使用** Rerank，排序在 SQL 里用 `l2_distance(...) APPROXIMATE` 完成 |
| 从安装依赖 → 导入 DSL → 初始化数据 → 启动应用的**完整步骤** | 「5.1 → 5.9」共 9 小节，命令可直接复制 |
| **应用入口** | 「6. 应用入口」（控制台 / 应用公开 API / 两个配套后端） |
| 外部服务的**获取**与配置方式 | 「5.4 补齐凭据」（含 MiniMax 开放平台取 Key 的说明）+ seekdb 建账号 SQL |
| Dify 访问服务所需的**网络条件** | 「4. 运行环境」网络行 + 5.9 末尾（`ssrf_proxy`、服务名解析、`host.docker.internal`） |
| 必要的**资源条件** | 「4. 运行环境」（内存 ≥ 4GB、磁盘 ≥ 10GB）+「8. 已知限制」第 5 条（单机内存紧张与 `mem_limit`） |
| **已知功能限制** | 「8. 已知限制」（NGRAM 依赖、SQLite 后端、向量维度依赖 Embedding 模型、内存、PowerContext 需自建服务） |
| 配置示例用**占位值**，不提交真实凭据 | 「5.4」全部用 `<你的 seekdb 口令>` / `<你的 MiniMax API Key>` 占位；仓库内**不含任何真实 Key / 口令**（推送前有凭据扫描） |
| 复用公开工作流 / 代码 / 数据时注明**来源与改动** | 「9. 第三方来源与改动说明」：Dify 1.4.2、PowerContext 1.4.1、seekdb 均**未修改**；本作品原创文件已逐个列出 |

---

## 4.3 验证样例与运行证据（`VERIFICATION.md` + `evidence/`）

| 要求 | 落点 |
| --- | --- |
| 初始数据 / **记忆状态** | VERIFICATION「0. 初始状态」：档案库 20 条、PowerContext 无任何听众 Scope、应用未导入 |
| 输入内容与**操作顺序** | VERIFICATION「1. 样例一」「2. 样例二」的「操作步骤」（含 WebApp 开场表单与 API 两种入口） |
| 预期交互 / 输出形式 / 验证方式 | 同样两节的「预期表现」+「核对点」表（预期 ↔ 实际 ↔ 证据文件 三列对应） |
| **实际运行结果** | 同样两节的「实际输出」（完整播报原文 + 卷宗卡片）与 `evidence/03_chat_results.json` |
| 截图 / 录屏 / **日志** | 截图见 `evidence/screenshots/`（11 张：WebApp 侧 7 张——开场表单、开场白、首轮播报、Workflow Process、移动端、**跨会话记忆第二次到访**、11 节点全绿；控制台侧 4 张——登录页、应用列表、运行日志、**会话变量抽屉**）；日志见 `evidence/08_radio_access.log`（电台后端每次检索的实际请求） |
| **seekdb / PowerContext 的作用及对应记录** | VERIFICATION「3.1 seekdb 真的在承担检索」「3.2 PowerContext 真的在承担记忆」，逐条指向 `evidence/05`–`07`、`03`–`04` |
| 连续操作：执行顺序与**如何恢复初始状态** | VERIFICATION「0. 初始状态」末尾给出**可执行**的重置步骤（删 PowerContext 的 SQLite 库文件 + 删表重跑 `init_archive.py`），并说明「换一个新听众代号」这条更省事的路子；另注明 `POST /v1/memory/flush` 只推进游标、清不掉记忆，别用它重置 |

### evidence/ 文件清单

| 文件 | 内容 |
| --- | --- |
| `00_app_id.txt` | 导入后的应用 ID |
| `01_import_response.json` | DSL 导入响应（`status=completed`，`dsl_version=0.3.0`） |
| `02_publish_response.json` | 工作流发布响应 |
| `03_chat_results.json` | 两组样例的完整请求/响应（含播报原文、conversation_id、token 用量） |
| `01_dify_node_executions.json` | **22 条**节点执行记录（两次样例 × 11 个节点）：状态、耗时、输入、输出、错误 |
| `02_dify_workflow_runs.json` | 两次样例的工作流执行记录（原始 TSV） |
| `03_powercontext_scopes.json` | PowerContext Scope 列表（含「深夜电台听众 小满」） |
| `04_powercontext_memory.json` | 记忆检索命中（`matched_by=["text"]`）+ 上下文准备（`status=ready`） |
| `05_seekdb_archive.json` | seekdb 里的 20 条怪谈档案 |
| `06_seekdb_indexes.json` | 档案表的索引清单（NGRAM 全文索引 + 向量索引） |
| `07_seekdb_explain.txt` | 检索语句的 EXPLAIN（`TEXT RETRIEVAL SCAN` 与 `VECTOR INDEX ADAPTIVE SCAN` 同现） |
| `08_radio_access.log` | 电台检索后端的访问日志 |
| `09_summary.md` | 以上内容的摘要 |
| `screenshots/` | 界面截图佐证（11 张 + 说明 README）：WebApp 侧 7 张（开场表单、开场白与推荐问题、首轮播报卷宗卡片、Workflow Process 面板、移动端界面、**跨会话记忆第二次到访**、**11 节点全绿编排过程**）+ 控制台侧 4 张（登录页、应用列表、运行日志、**会话变量 conversation_var 抽屉**）；每张图对应哪个验证点见该目录下的 `README.md` |

---

## 4.4 按依赖补充的材料

| 赛题列举的依赖 | 本作品是否涉及 | 补充材料 |
| --- | --- | --- |
| **使用 seekdb 部署 Dify** | **不适用**（显式说明） | Dify 官方 1.4.2 未提供 seekdb 适配分支，其内部库只用 Postgres/MySQL 系；本作品改用 `pgvector/pgvector:pg16` 作 Dify 内部库（省掉独立向量库容器），**seekdb 承担的是「应用数据存储 + 混合检索」**——20 条怪谈档案的表、`VECTOR(1536)` 列、NGRAM 全文索引与 HNSW 向量索引都在 seekdb 上，电台后端用同一条 SQL 同时走两条通道。**seekdb 版本 `OceanBase seekdb 1.4.0.0`**（rpm 包 `seekdb-1.4.0-100000172026082615.el7.x86_64`，SQL 层版本串 `5.7.25-OceanBase seekdb-v1.4.0.0`）；**数据库用途**＝怪谈档案库的应用数据存储 + 混合检索；**部署配置**见 [`deploy/seekdb/README.md`](./deploy/seekdb/README.md)（含版本、目录布局、`seekdb.cnf`、systemd 启动、建号 SQL）与 `deploy/seekdb/seekdb.cnf`、`deploy/seekdb/install_seekdb.sh`；初始化见 `scripts/init_archive.py`；**实际读写验证步骤**见 `deploy/seekdb/verify_seekdb.sh`——一条命令跑完：打印版本与监听 → 读档案表行数与索引清单 → 建临时表实测 NGRAM 全文命中 + HNSW 向量命中 → EXPLAIN 确认 `TEXT RETRIEVAL SCAN` 与 `VECTOR INDEX ADAPTIVE SCAN` 同现 → 删除临时表（不改档案表里的任何一行）；已归档的实测记录见 `evidence/05`–`07` 与 `08_radio_access.log`。**未修改 Dify 任何源码**，因此无适配源码需附 |
| **知识库或外部数据** | 是 | 数据文件 `data/archive.json`（20 条怪谈，**本作品原创**，非抓取/改编）；获取与初始化方式：`scripts/init_archive.py`（建表 → 建 NGRAM 全文索引 → 建 HNSW 向量索引 → 调 `embo-01` 生成向量 → 灌库，幂等）；必要的导入、索引与检索配置见该脚本与 `service/radio_archive.py` |
| **预置记忆或互动状态** | 是（**不预置任何记忆**） | 初始化脚本同 `init_archive.py`；用户/角色/会话标识与工作流的关联方式见 VERIFICATION「3.2」：`listener_id` → Scope 标题「深夜电台听众 {代号}」→ 幂等键 `midnight-radio-{代号}`；记忆全部由样例运行时写回 |
| **现成插件** | 是 | `langgenius/openai_api_compatible`，版本 `0.0.73`，**安装来源：Dify 插件商店（marketplace）**，配置步骤见 README「5.7」。除该插件外没有别的插件、没有自定义插件包 |
| **自定义插件** | 否 | 本作品**没有**自定义插件 |
| **自定义后端或前端** | 是（两个后端，**无前端**，前端用 Dify 自带 WebApp） | ① `service/radio_archive.py`（seekdb 检索后端，标准库 + `pymysql`）；② PowerContext 服务端（官方 PyPI 包 `powercontext[cli,server]==1.4.1`，以容器方式部署）。源码分别在 `service/` 与官方包；依赖清单 `requirements.txt`；配置示例 `.env` 变量见 README「5.4」（用占位值）；启动说明见 README「5.6」；镜像与构建方式：`deploy/Dockerfile.radio`、`deploy/Dockerfile.powercontext`，镜像名 `midnight-radio/radio-archive:1.0`、`midnight-radio/powercontext:1.0`，由 `docker compose build` 构建 |

---

## 凭据与占位值

- 仓库内**不含任何真实 API Key、口令或 Token**；推送前用凭据扫描逐文件比对过。
- README 与本文件中出现的账号/口令/地址一律是占位形式（`<你的 seekdb 口令>`、`<控制台密码>`、`<host>`）。
- 应用 API Key 由评审在自己的 Dify 上重新创建（README「5.8」+「7. 验证样例」）。
