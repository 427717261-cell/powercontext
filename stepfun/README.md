# mini-kb · 端侧向量化 + seekdb 混合检索

浏览器本地完成 embedding（Transformers.js + ONNX Runtime Web），向量、原文与元数据存入 seekdb，
检索走 seekdb 内部的向量 + 全文混合检索（`DBMS_HYBRID_SEARCH` + RRF 融合）。

对应赛题：**seekdb × 阶跃星辰 —— 端侧 AI 推理性能优化与 seekdb 混合检索（20 分）**

```
浏览器                                    服务端（Node/Express）           seekdb
┌───────────────────────────┐            ┌──────────────────────┐       ┌───────────────────────────┐
│ 文档 → 分块                │            │ /api/docs  只收向量   │       │ 集合表 c$v2$<id>          │
│ bge-small-zh-v1.5 (ONNX)  │ ──向量──▶  │ /api/search 转发     │ ───▶ │  VECTOR(512) + HNSW(VSAG) │
│ WebGPU 优先 / WASM 降级    │            │ /api/refresh 刷新索引 │       │  FULLTEXT (WITH PARSER ik)│
│ 查询 → 本地编码 → 展示结果 │ ◀─排序──   │ /models 本地模型托管  │ ◀─── │  DBMS_HYBRID_SEARCH (RRF) │
└───────────────────────────┘            └──────────────────────┘       └───────────────────────────┘
```

不调用任何云端推理 API：`/api/docs` 只接收浏览器算好的向量，服务端不含任何 embedding 代码。

## 1. 环境要求

| 项 | 版本 / 说明 |
|---|---|
| Node.js | ≥ 20（实测 22.22.2） |
| 浏览器 | Chrome / Edge 最新版（WebGPU 需 113+；不可用时自动降级 WASM） |
| seekdb | server 模式，默认端口 2881（云实例或本机实例均可） |
| 内存 | ≥ 8 GB（模型 + 浏览器推理） |

依赖：`express@5`、`seekdb`（seekdb-js）、`@huggingface/transformers`、`vite@7`。

## 2. 三步启动

```bash
# 0) 准备 seekdb（已有实例可跳过，直接改 .env）
bash scripts/seekdb-up.sh          # Linux + systemd，官方一键安装脚本
# 或者用任意已运行的 seekdb：确保 2881 端口可达，root 账号可登录

# 1) 安装依赖
npm install --legacy-peer-deps     # seekdb-js 的 peer 依赖需要放行

# 2) 配置连接（复制模板后填真实密码）
cp .env.example .env               # 改 SDB_HOST / SDB_PASSWORD

# 3) 拉模型 + 启动
node scripts/fetch-model.mjs       # 默认只下 INT8（23MB）；加 --all 下 FP16/FP32（跑 bench 用）
npm start                          # 生产模式：http://127.0.0.1:8787/
# 或者开发模式（热更新）：npm run dev:server 与 npm run dev:web，访问 http://127.0.0.1:5173/
```

`.env` 字段：

```ini
SDB_HOST=127.0.0.1      # seekdb 地址
SDB_PORT=2881
SDB_USER=root
SDB_PASSWORD=******
SDB_DATABASE=stepfun_kb # 不存在会自动创建
PORT=8787               # 应用 HTTP 端口
```

首次启动会自动建库、建 `docs` 集合（512 维、L2、ik 分词），无需手工执行 SQL。

## 3. 使用流程

1. **导入**：粘贴文本 / 拖入 `.txt`·`.md` / 勾选示例文档 → 「向量化并写入」。长文本按约 200 字、重叠 40 字自动分块。
   页面实时显示分阶段耗时（向量化 / 写入 / 刷新 / 端到端）与吞吐。
2. **检索**：输入查询 → 选择「混合 / 向量 / 全文」，展示排序结果、得分与「查询向量化 · seekdb 检索 · 端到端」分段耗时。
3. **刷新索引**：seekdb 的 HNSW 与全文索引是异步构建的，**写入后必须刷新才能被检索到**。
   顶部有「待刷新 N 条」提示与「刷新索引」按钮；导入区可勾掉「导入后自动刷新索引」来演示刷新前后差异。
4. **集合管理**：可新建/删除集合、切换集合；当前集合与会话历史存在 localStorage，刷新页面后状态一致。
5. **后端切换**：右上角下拉可强制 `WebGPU` / `WASM`；也可直接用 URL 参数：
   `/index.html?backend=wasm&dtype=q8` —— 用于验证降级路径。

## 4. 复现实测数据

```bash
# 1) 下齐三种精度（FP32 / FP16 / INT8）
node scripts/fetch-model.mjs --all

# 2) 启动服务（bench 页与 API 同源）
npm start

# 3) 一次进程内跑完四档，横向可比（需要 GPU，会打开浏览器窗口）
node scripts/run-bench-headless.mjs \
  --configs fp32-wasm,q8-wasm,fp16-webgpu,fp32-webgpu \
  --rounds 3 --batch 8 --out bench/results-final.json --webgpu
```

也可以人工打开 `http://127.0.0.1:8787/bench.html`，勾选配置点「开始实测」，页面直接出表，
支持「下载 JSON」与「复制 Markdown 表格」。

**四档必须一次性跑完**：WASM 与 WebGPU 分两次运行会因机器状态与网络时延不同而不可比，
早期分开跑曾得出"WebGPU 比 WASM 慢 3 倍"的错误结论（详见 `bench/archive-v1/README.md`）。

指标口径：Recall@K / nDCG@10 以 `data/eval-queries.json` 的 qrels 为准（30 篇语料 / 12 条查询）；
计时为 3 轮中位数，文档向量化的首轮含预热已剔除；检索走应用同一条 seekdb 混合检索路径。

## 4b. 索引可见性与降级路径验证

```bash
# 三条检索路径在未 refresh 时的可见性差异（直连 seekdb，不经浏览器）
node scripts/probe-visibility.mjs

# 增量写入后纯向量可见的延迟量级（实测 145–258 ms，均值 184 ms）
node scripts/probe-latency.mjs

# 端到端验收（逐项 PASS/FAIL，结果写入 evidence/）
node scripts/e2e-flow.mjs --backend webgpu --port 9384
node scripts/e2e-flow.mjs --backend wasm   --port 9385
```

## 5. 目录结构

```
server/index.js            Express API：/api/{health,stats,collections,docs,search,refresh} + /models 托管
src/embedder.js            端侧推理封装：WebGPU 优先 → WASM 降级、精度切换、本地/镜像模型加载
src/main.js                应用主界面逻辑（导入、检索、集合、刷新提示、后端徽标）
src/bench.js               量化与性能实测页
src/api.js                 REST 封装
data/eval-corpus.json      评测语料 30 篇
data/eval-queries.json     评测查询 12 条 + qrels
db/schema.sql              seekdb 表结构与索引配置（SDK 实际生成，附等价手写 DDL）
scripts/fetch-model.mjs    模型下载与校验（HF 主源 + hf-mirror 回退）
scripts/run-bench-headless.mjs  无人值守 bench（CDP 驱动本机 Chromium）
scripts/e2e-flow.mjs       端到端验收（导入→刷新前后可见性→混合检索，逐项 PASS/FAIL）
scripts/probe-visibility.mjs    三条检索路径的索引可见性差异（直连 seekdb）
scripts/probe-latency.mjs       增量写入的向量索引可见延迟测量
scripts/seekdb-up.sh       seekdb 本地启动脚本
bench/results-final.json   四档同轮实测原始数据（权威）
bench/results.md           实测结果汇总表与解读
bench/archive-v1/          已作废的早期数据（旧测量方法，不参与结论）
evidence/                  端到端验收的运行记录（含失败过程）
ACCEPTANCE.md              验收矩阵（逐项证据）
TECH.md                    一页技术说明
```

## 6. 已知限制

- 评测语料只有 30 篇、12 条查询，检索任务偏简单（混合检索 R@10 = 1.000），
  因此量化前后检索质量差异为 0，需结合向量余弦漂移一起看（见 TECH.md）。
- 端侧模型 `bge-small-zh-v1.5` 为中文优化的小模型，跨语言检索能力弱。
- 演示环境使用云上 seekdb，单条检索含公网 RTT（约 100 ms 量级）；
  本机部署 seekdb 时该段耗时会明显更低，因此文档中把「seekdb 检索」与「端到端」分开记录。
