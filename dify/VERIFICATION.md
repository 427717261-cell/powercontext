# 验证样例与运行证据

> 作品：**深夜电台 · 怪谈档案**（Dify × seekdb × PowerContext）
> 验证环境：OpenCloudOS 9.4 / x86_64 / Docker 28.0.1 / compose 2.32.1
> Dify 1.4.2 + seekdb v1.4.0.0 + PowerContext 1.4.1 + MiniMax-M3

---

## 0. 初始状态

| 项 | 值 |
| --- | --- |
| seekdb 怪谈档案库 | `radio_archive.radio_archive`，**20 条**档案，`VECTOR(1536)` 列 + NGRAM 全文索引 + HNSW 向量索引 |
| PowerContext 记忆库 | SQLite（`volumes/powercontext/powercontext.db`），**空**（尚无任何听众 Scope） |
| Dify 工作区 | 新建管理员 `host@midnight-radio.local`，已装 `langgenius/openai_api_compatible:0.0.73` 插件并注册 `MiniMax-M3` |
| 应用 | 尚未导入 DSL |

> ⚠️ 上表里的 `host@midnight-radio.local` 是**我们验证环境**的账号，**不随作品交付**。
> 评审在自己环境部署时，管理员账号有两种产生方式（见 README「5.6」）：
>
> - `.env` 里 `INIT_PASSWORD` 留空 → 首次访问 `http://<host>:<EXPOSE_NGINX_PORT>/install`，
>   在页面上自建管理员；
> - `.env` 里设了 `INIT_PASSWORD` → Dify 启动时自动创建 `root@localhost`，口令即该值。
>
> 两种方式得到的账号**都可以**用于下面的 `verify_samples.py --email/--password`，
> 跑样例前用你自己的账号替换即可。

> 想让样例从「真正的空白」开始，按下面两步重置（都可重复执行）：
>
> ```bash
> # ① 重置听众记忆：删掉 PowerContext 的 SQLite 库文件再重启容器
> cd <dify>/docker
> docker compose stop powercontext
> rm -f volumes/powercontext/powercontext.db \
>       volumes/powercontext/powercontext.db-wal \
>       volumes/powercontext/powercontext.db-shm
> docker compose up -d powercontext
> curl -s http://127.0.0.1:8100/v1/scopes    # 期望只剩 Default 一个 Scope
>
> # ② 重置怪谈档案库：删表后重跑初始化脚本（幂等：重建表与索引并重新灌 20 条）
> mysql -h<seekdb> -P2881 -u<账号> -p radio_archive -e "DROP TABLE IF EXISTS radio_archive;"
> docker compose run --rm radio-archive python /app/scripts/init_archive.py
> ```
>
> **更省事的办法**：听众代号就是 PowerContext 的 Scope 键，换一个没用过的代号即可得到
> 「首次到访」的初始状态——`小满` 跑过之后换成 `阿澈` 就行。
> 代号与 Scope 的对应关系见 [3.2](#32-powercontext-真的在承担记忆)。
>
> ⚠️ **不要用 `POST /v1/memory/flush` 来清记忆**：实测它只推进游标、不删除条目，
> `/v1/context/prepare` 仍然能召回旧记忆，清不掉「首次到访」状态。
> 要回到空白状态请用上面的 ① 或换一个代号。

---

## 1. 样例一：首次到访 → 档案检索生效

**初始条件**：全新听众代号 `小满`（此前没有这个 Scope）。
**输入**（听众代号填 `小满`）：`想听末班地铁的怪谈`

**操作步骤**

1. 控制台 → 工作室 → 打开「深夜电台 · 怪谈档案」→ 发布 → 开始对话；
2. 开场表单的「听众代号」填 `小满`；
3. 发送 `想听末班地铁的怪谈`。

**预期表现**

- 主播认出这是**第一次来**的听众（因为 PowerContext 里没有这个 Scope 的记忆）；
- 广播末尾附上从 seekdb 档案库翻出来的卷宗，且标注**命中通道**（`fts` 全文 / `vector` 向量）。

**实际输出**（HTTP 200，conversation `28ae733f-261c-4fb8-bbad-4d5a15e06891`）：

```
咔哒。小满，档案里还没有你的记录，欢迎你第一次来到深夜电台。今晚翻出的是《末班地铁上的第七节车厢》。嘶——末班车从第七节车厢开始，就不再报站了。广播里的女声温柔地念着每位乘客的名字，可念到你时，她喊的是你小时候自己改的小名。整节车厢的人都会回头，笑着问：“我们认识吗？”下车前摘掉耳机，你会听见车厢外有人用同样的名字喊你。别回头。小满，想听第七节车厢的细节版，还是《总在错过的第12班》？

---
📻 今晚翻出的卷宗：《末班地铁上的第七节车厢》〔滨江·地铁末班车/城市怪谈/深夜｜命中通道:fts〕 / 《总在错过的第 12 班》〔城东·末班车/错失/深夜｜命中通道:fts〕 / 《末班公交的第 7 站》〔城东·末班车/城市怪谈/深夜｜命中通道:vector〕
```

**核对点**

| 预期 | 实际 | 证据 |
| --- | --- | --- |
| 认出首次到访 | 「档案里还没有你的记录，欢迎你第一次来到深夜电台」 | `evidence/03_chat_results.json` → `sample-1.answer` |
| seekdb 全文通道命中 | `命中通道:fts` × 2 | 同上 |
| seekdb 向量通道命中 | `命中通道:vector` × 1 | 同上 |
| 11 个节点全部成功 | `succeeded` × 11 | `evidence/01_dify_node_executions.json` |

---

## 2. 样例二：换新会话 → 跨会话记忆生效

**初始条件**：沿用听众代号 `小满`，但**开一个全新的会话**（不接着上一段对话，
也不传 `conversation_id`）——这一步是本作品的核心看点：记忆存在 PowerContext 里，
不在 Dify 的会话上下文里。

**输入**：`我今天特别累，随便讲个短的`

**操作步骤**

1. 在 WebApp 里点「新建会话」（或直接刷新页面重新开一段对话）；
2. 开场表单的「听众代号」**仍然填 `小满`**；
3. 发送 `我今天特别累，随便讲个短的`。

**预期表现**

- 主播**认出这是同一位听众**，并主动提起上一轮的点播内容；
- 先接住「特别累」的情绪，再讲稿子；
- 结尾给出 A/B 两个可选项，且选项来自上一轮的卷宗。

**实际输出**（HTTP 200，conversation `89443506-c424-4e91-8f8e-6c39f17cce7f`，
与样例一是**不同的** conversation）：

```
嗯哼，嘶——小满，第二次来了，我还记得你，上次想听末班地铁。今晚翻出来的卷宗也是末班车：有个人每天都赶第12班，从来没赶上。后来他不追了，等了半年，车才停下，司机说：“上车吧，今天我等你。”累了就别硬撑，你也可以先不赶路。想听A：第12班为何等人，还是B：第七节车厢的小名？

---
📻 今晚翻出的卷宗：《夜市收摊前的十分钟》〔城西·夜市/暖胃/老友｜命中通道:fts〕 / 《深夜便利店的关东煮汤》〔全城·便利店/暖胃/老友｜命中通道:fts〕 / 《电梯里的沉默竞赛》〔CBD·电梯/办公室/深夜｜命中通道:vector〕 / 《总在错过的第 12 班》〔城东·末班车/错失/深夜｜命中通道:vector〕
```

**核对点**

| 预期 | 实际 | 证据 |
| --- | --- | --- |
| 认出同一位听众 | 「小满，第二次来了，我还记得你，上次想听末班地铁」 | `evidence/03_chat_results.json` → `sample-2.answer` |
| 记忆来自跨会话存储 | 两个 conversation_id 不同 | 同上，`sample-*.conversation_id` |
| 先接情绪再讲稿 | 「先别硬撑…累了就别硬撑」 | 同上 |
| A/B 选项沿用上轮素材 | 「A：第12班为何等人，还是B：第七节车厢的小名」 | 同上 |

**界面佐证**（`evidence/screenshots/`）：同一条结论在真实 WebApp 界面上也有截图，
见 `06_跨会话记忆_第二次到访_混合检索.png` 与 `07_WorkflowProcess_11节点全绿.png`——
前者是同一听众代号 `123` 的**第二次**到访（主播答「嘶——记得，123。你第二次来了。上次城南那家
凌晨三点还亮着灯的便利店，我还记着：穿校服的店员，以及那串永远热着的关东煮。」），
与 `03_首轮播报_卷宗卡片.png` 的**第一次**到访构成对照；后者是这一轮的 Workflow Process，
11 个节点逐个成功，其中「召回听众记忆（POWERCONTEXT）」与「检索怪谈档案（SEEKDB）」
各自占一个独立节点。截图与本文的 HTTP 级证据互为印证。

**记忆是怎么被召回的**（`evidence/04_powercontext_memory.json`）：

- `POST /v1/context/prepare` 对 `scope_id=scp_…` 返回 `status=ready`，
  `content` 里带着样例一写入的「听众在深夜电台说：想听末班地铁的怪谈…」；
- `POST /v1/memory/search` 命中该条记忆，`matched_by=["text"]`；
- `evidence/03_powercontext_scopes.json` 里能看到为 `小满` 建的 Scope。

---

## 3. 技术结合的证据

### 3.1 seekdb 真的在承担检索

**档案表与索引**（`evidence/05_seekdb_archive.json`、`evidence/06_seekdb_indexes.json`）：

```
radio_archive 表：20 条怪谈档案
ft_radio_archive              FULLTEXT  （NGRAM 解析器）
idx_radio_archive_embedding   BTREE     （向量索引的入口）
```

**执行计划**（`evidence/07_seekdb_explain.txt`）——同一条 SQL 里两段扫描同时出现：

```
|ID|OPERATOR                                    |NAME                           |
|3 |│   └─TEXT RETRIEVAL SCAN                   |radio_archive(ft_radio_archive)|
|5 |  └─VECTOR INDEX ADAPTIVE SCAN (PRE-FILTER)|radio_archive                  |
```

**电台后端访问日志**（`evidence/08_radio_access.log`）能看到每次
`POST /archive/search` 的实际请求。

### 3.2 PowerContext 真的在承担记忆

- Scope 列表：`evidence/03_powercontext_scopes.json`；
- 记忆召回与写回：`evidence/04_powercontext_memory.json`；
- Dify 侧对应节点：`http_scope`（建 Scope）、`http_recall`（召回）、`http_save`（写回），
  节点执行记录里有完整的请求体与响应体，见 `evidence/01_dify_node_executions.json`。

**听众 / 会话标识怎么与工作流关联**（本作品没有预置任何记忆，记忆全部由样例跑出来）：

```
开场变量 listener_id（听众代号，Chatflow 的会话开场变量）
   └─ code_req 节点：listener = (listener_id or "").strip() or "无名听众"
        ├─ Scope 标题   = "深夜电台听众 {listener}"     ← 听众档案的身份
        ├─ 幂等键       = "midnight-radio-{listener}"   ← 同一代号永远落回同一个 Scope
        └─ 请求 user 字段 = {listener}                  ← Dify 侧的会话用户标识
```

- **同一个代号 ⇒ 同一个 Scope ⇒ 跨会话复用记忆**（样例二靠的就是这条）；
- **换一个代号 ⇒ 另一个 Scope ⇒ 对这位听众而言就是「第一次来」**（样例一的初始状态）；
- `conversation_id` 只存在于 Dify 侧，**记忆不随会话走**，所以样例二必须开一个全新会话；
- 本作品**不预置任何记忆**：初始状态下 PowerContext 里只有一个 `Default` Scope，
  所有听众记忆都由样例运行时写进去（见 `evidence/04_powercontext_memory.json` 的两条记录）。


### 3.3 Dify 真的在承担编排

11 个节点按 `start → code_req → http_scope → code_scope → http_recall → code_memory →
http_archive → code_save → http_save → llm_host → answer` 顺序执行，
每个节点的输入输出、耗时、状态都记录在 `evidence/01_dify_node_executions.json`：

```
start          开始                      succeeded   0.17s
code_req       组装请求体                 succeeded   0.20s
http_scope     建立听众档案（PowerContext）succeeded   0.58s
code_scope     解析 scope_id             succeeded   0.12s
http_recall    召回听众记忆（PowerContext）succeeded   0.75s
code_memory    整理记忆文本               succeeded   0.11s
http_archive   检索怪谈档案（seekdb）     succeeded   0.77s
code_save      整理本轮素材               succeeded   0.09s
http_save      记住这一轮（PowerContext） succeeded   0.68s
llm_host       夜班主播 · 阿雾             succeeded  16.47s
answer         播出                      succeeded   0.04s
```

**界面佐证**（`evidence/screenshots/`）：

- `07_WorkflowProcess_11节点全绿.png`——同一次运行在 WebApp 里展开后的逐节点耗时与状态，
  与上面这张表一一对应；
- `13_控制台_会话变量_conversation_var.png`——控制台编排页的会话变量抽屉：
  `conversation_var` 由「变量赋值」节点 WRITE、被「LLM」节点 READ，
  说明「召回的听众记忆 + 本轮卷宗素材」是这样显式拼进主播提示词的；
- `09_控制台_应用列表.jpg` / `08_控制台_登录页.jpg` / `11_控制台_运行日志.jpg`——
  控制台是真实部署的 Dify 1.4.2，作品以 CHATFLOW 形式存在，且有可查的运行日志。

---

## 4. 一键复现

```bash
# 1) 导入 DSL、发布、建 Key、跑两组样例
python3 dify/scripts/verify_samples.py \
  --base-url http://127.0.0.1:5001 \
  --email host@midnight-radio.local \
  --password '<控制台密码>' \
  --out-dir dify/evidence

# 2) 采集节点级 / 记忆 / 检索证据
python3 dify/scripts/collect_evidence.py \
  --out-dir dify/evidence \
  --listener 小满
```

> `collect_evidence.py` 需要能直连 Dify 的 Postgres、PowerContext 与 seekdb，
> 并能执行 `docker exec`；若只想复现「作品能不能跑」，用 `verify_samples.py` 就够了。

---

## 5. 证据文件清单

| 文件 | 内容 |
| --- | --- |
| `evidence/00_app_id.txt` | 本次导入的应用 ID |
| `evidence/01_import_response.json` | DSL 导入响应（`status=completed`，`dsl_version=0.3.0`） |
| `evidence/02_publish_response.json` | 工作流发布响应 |
| `evidence/03_chat_results.json` | 两组样例的完整请求/响应（含播报原文） |
| `evidence/01_dify_node_executions.json` | 两组样例共 22 条节点执行记录（每次 11 个节点）：状态、耗时、输入、输出、错误 |
| `evidence/02_dify_workflow_runs.json` | 两次样例的工作流执行记录（原始 TSV） |
| `evidence/03_powercontext_scopes.json` | PowerContext Scope 列表 |
| `evidence/04_powercontext_memory.json` | 记忆检索 + 上下文准备结果 |
| `evidence/05_seekdb_archive.json` | seekdb 里的 20 条怪谈档案 |
| `evidence/06_seekdb_indexes.json` | 档案表的索引清单 |
| `evidence/07_seekdb_explain.txt` | 检索语句的 EXPLAIN（全文扫描 + 向量索引） |
| `evidence/08_radio_access.log` | 电台检索后端的访问日志 |
| `evidence/09_summary.md` | 以上内容的摘要 |
| `evidence/screenshots/` | 界面截图佐证（11 张：8 张 JPG + 3 张 PNG，另有说明 README）：开场表单、开场白与推荐问题、首轮播报卷宗卡片、Workflow Process 面板、移动端界面、跨会话记忆第二次到访、11 节点全绿编排过程、控制台登录页 / 应用列表 / 运行日志 / 会话变量抽屉 |
