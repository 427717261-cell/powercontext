# 赛题二 · 端侧 AI 推理性能优化与 seekdb 混合检索 —— 验收矩阵

> 依据题面「三、验收标准」（3.1 功能与质量 7 项、3.2 性能与质量测试 6 项）与「四、提交材料」（4.1–4.3）。
> 状态标记：✅ 已通过（有可指认证据）/ 🔶 部分（说明缺口）/ ⬜ 未做。
> 最近一次全量验收：2026-10-28 12:12（WebGPU 9/9、WASM 9/9 全绿）

## A. 功能与质量（3.1）

| 编号 | 题面要求 | 证据 | 状态 |
|---|---|---|---|
| A1 | 完整流程：导入文本 → 生成向量 → 写入 seekdb → 查询 → 展示排序结果 | `evidence/e2e-1793160760381.json`（导入 1 块 → 写入 137 ms → 三种模式检索命中）；`index.html` + `src/main.js` | ✅ |
| A2 | 本地推理：文档与查询 embedding 均在浏览器计算，无云端推理 API | e2e 记录 `device=webgpu dtype=fp16 来源=local:/models`、`device=wasm dtype=q8`；`server/index.js` 中不存在任何 embedding 推理调用，`/api/docs` 只接收已算好的向量 | ✅ |
| A3 | 模型量化：INT8 或 FP16，给出模型来源版本、量化方式、体积对比、质量变化 | `bench/results.md`（90.46 → 22.90 / 45.30 MiB）、`TECH.md` §2；模型为 `Xenova/bge-small-zh-v1.5` 官方 ONNX 三档权重 | ✅ |
| A4 | 后端与降级：优先 WebGPU，不支持时自动降级 WASM 并明确提示，核心流程仍可跑 | WebGPU 路径 9/9（`e2e-1793160760381.json`）；`?backend=wasm` 强制降级 9/9（`e2e-1793160778010.json`）；界面徽标显示「WASM（已强制降级）」 | ✅ |
| A5 | seekdb 数据层：向量/原文/元数据存于 seekdb；向量+全文混合检索在 seekdb 内完成 | `db/schema.sql`（真实 `SHOW CREATE TABLE`：`VECTOR(512)` + `FULLTEXT ... WITH PARSER ik` + HNSW/VSAG）；检索返回 `engine.executedIn="seekdb"`、`fusion="rrf"`、`metric="rrf_score"` | ✅ |
| A6 | 状态与刷新：增量导入可用，刷新后新增内容可检索，集合与会话状态正确 | 「待刷新 N 条」提示 + 手动/自动刷新；`refresh` 后纯向量立即可检索（e2e）；可见性差异与延迟量级见 `probe-visibility.mjs`、`probe-latency.mjs` 与 `TECH.md` §3 第 4 条 | ✅ |
| A7 | 可复现性：按说明可启动应用与数据库并完成上述操作 | `README.md` §1–§4b 逐条核对；`scripts/seekdb-up.sh` 提供本地 seekdb 启动；`scripts/fetch-model.mjs` 提供模型获取。**缺口：未在第二台机器上完整重跑** | 🔶 |

## B. 性能与质量测试（3.2）

| 编号 | 题面要求 | 证据 | 状态 |
|---|---|---|---|
| B1 | 固定测试条件：设备、浏览器版本、语料、查询集、模型版本/精度/后端/批大小/seekdb 配置 | `bench/results-final.json` 的 `env` 段完整记录；`bench/results.md` 头部摘要 | ✅ |
| B2 | 模型体积：同口径统计实际模型文件总体积，量化前后对比，给压缩比 | 取实际 onnx 文件字节：90.46 / 45.30 / 22.90 MiB，压缩比 3.95× 与 2.00× | ✅ |
| B3 | 检索质量：相同评测集上对比量化前后，注明方法（Recall@K / nDCG@K）及变化幅度 | 30 篇语料 / 12 条查询 / qrels 人工标注；混合 R@10 四档均为 1.00、nDCG@10 均为 0.9898；补充向量余弦漂移（0.98811 / 0.99998）。**已注明语料规模小导致 R@10 饱和** | ✅ |
| B4 | 分阶段计时：模型下载/加载、文档向量化、写入与索引刷新、查询向量化、seekdb 检索、端到端 | `results-final.json` 每档含 `loadMs`、`docEmbedMs`、`writeMs`、`refreshMs`、`queryEmbedMedianMs`、`dbSearchMedianMs`、`e2eMedianMs` | ✅ |
| B5 | 区分首次与后续请求，重复测试取中位数，保留原始数据 | 文档向量化首轮（含预热）剔除后取 3 轮中位，各轮原始值存于 `docEmbedRoundsMs`；其余指标 3 轮中位；原始 JSON 已入库 | ✅ |
| B6 | 验证降级路径：WebGPU 可用/不可用两种环境，确认后端选择、界面提示与 WASM 下检索流程 | 同 A4，两条路径独立跑通并落盘 | ✅ |

## C. 提交材料（4.x）

| 编号 | 题面要求 | 现状 | 状态 |
|---|---|---|---|
| C1 | 可运行的 Web 应用（本地启动说明、浏览器要求、依赖安装、seekdb 启动脚本与连接配置） | `README.md`、`.env.example`、`scripts/seekdb-up.sh` | ✅ |
| C2 | 完整源码 + 依赖配置 + 模型获取与量化步骤 + seekdb 表结构与索引配置 + 复现测试数据 | `src/`、`server/`、`package.json`、`scripts/fetch-model.mjs`、`db/schema.sql`、`data/`、`bench/` | ✅ |
| C3 | 一页技术说明：模型与量化 / 端侧运行 / seekdb 使用 / 状态与优化，含关键性能对比 | `TECH.md` | ✅ |

## D. 实测结论（可复查，不是推测）

1. **集合物理结构**：`createCollection` 生成 `c$v2$<collection_id>` 表，DDL 自带
   `FULLTEXT KEY idx_fts (document) WITH PARSER ik PARSER_PROPERTIES=(ik_mode="smart")` 与
   `VECTOR KEY idx_vec (embedding) WITH (DISTANCE=L2, TYPE=HNSW, LIB=VSAG, M=16, EF_CONSTRUCTION=200, EF_SEARCH=64, SYNC_MODE=ASYNC)`；
   集合元数据（dimension/distance/analyzer）存于 `sdk_collections`。
2. **索引可见性按路径不同**（实测对照，见 `scripts/probe-visibility.mjs`）：
   纯向量（HNSW）约 200 ms 后可见；纯全文（ik）实时可见；混合检索在 refresh 前靠全文一路带出结果。
3. **可见性延迟量级**（`scripts/probe-latency.mjs`，5 次样本）：145 / 258 / 175 / 145 / 152 ms，
   均值 184 ms。`CALL dbms_index_manager.refresh()` 是同步的，写入后立刻 refresh 即可立即检索。
4. **混合检索在库内**：seekdb-js 生成 `SELECT DBMS_HYBRID_SEARCH.GET_SQL('<table>', @search_parm)` 后执行，
   客户端不做两路结果合并。
5. **`getCollection` 是惰性的**：集合不存在时不抛错，会在后续查询才失败。集合存在性必须查 `sdk_collections`。
6. **测量方法会影响结论**：分进程跑 WASM / WebGPU 且向量化只计单次，曾得出「WebGPU 比 WASM 慢 3 倍」；
   四档同轮 + 多轮中位 + 剔除预热后，真实结果是 WebGPU 快 3.6 倍。作废数据在 `bench/archive-v1/`。
7. **`__kbReady` 曾早于事件绑定置位**：模型加载完就置位，但 `bind()` 在其后，导致「点击无反应且无报错」。
   已改为在 `bind()` 与 `reloadCollections()` 之后置位。

## D. 提交信息

| 项 | 值 |
|---|---|
| 分支 | `2026-competition` |
| 目录 | `stepfun/`（共 36 个文件，324.7 KiB） |
| 私有库 | `427717261-cell/powercontext-2026-repo`，首次入库提交 `c69fb0c3a4bf4527979aa6345446341e91e99b48` |
| 公开 fork | `427717261-cell/powercontext`，首次入库提交 `e406bedc1c8025c3e81706dcdf728330a01ade7f` |

上面记的是**内容入库那次提交**（36 个文件）。此后若对本表做过文字修订，分支顶端会多出一两个
只改文档的提交，内容不变；查顶端用 `git log --oneline -1 -- stepfun/`。

两库均基于各自分支当时的最新 head 打补丁（`base_tree`），未覆盖他人提交。
逐文件校验结果：本地 36 个文件与远端 blob SHA 全等，缺失 0、哈希不符 0、远端多余 0。

**未入库**：`node_modules/`、`models/`（模型由 `scripts/fetch-model.mjs` 下载）、`dist/`、`.cache/`、`.env`
（仅提交 `.env.example` 占位模板，已确认真实密码不在其中）。
