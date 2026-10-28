# 技术说明

一个纯前端的本地知识库检索应用：文档导入后由**浏览器本地**完成向量化，向量与原文写入
**seekdb**，检索时向量与关键词一次查询在**库内**融合。除 seekdb 连接外不依赖任何外部服务。

---

## 1. 端侧推理

| 项 | 实现 |
|---|---|
| 运行时 | Transformers.js（ONNX Runtime Web），随前端打包，无 native 依赖 |
| 模型 | `Xenova/bge-small-zh-v1.5`，512 维，中文检索专用 |
| 权重来源 | 优先读本地 `models/`（后端静态托管，离线可用）；缺失时回退 `huggingface.co` → `hf-mirror.com` |
| 后端选择 | 探测 `navigator.gpu.requestAdapter()`，可用则 WebGPU，否则 WASM |
| 默认精度 | WebGPU → FP16；WASM → INT8(q8) |
| 强制切换 | `?backend=wasm\|webgpu`、`?dtype=fp32\|fp16\|q8`，用于验证降级路径 |
| 界面提示 | 顶栏常驻显示当前后端、精度、模型来源与体积；降级时显式标注原因 |
| 查询侧处理 | 按 BGE 官方用法，query 加指令前缀「为这个句子生成表示以用于检索相关文章：」，文档不加 |

WASM 后端需要 `SharedArrayBuffer`，依赖 `Cross-Origin-Opener-Policy: same-origin` 与
`Cross-Origin-Embedder-Policy: require-corp`。后端已统一注入这两个响应头，实测
`crossOriginIsolated=true`，多线程生效；未注入时自动退化为单线程，功能不受影响但吞吐下降。

## 2. 量化前后对比

同一进程内一次跑完四档，同一浏览器实例、同一网络、同一后端；每项取 3 轮中位数，
向量化的首轮（含算子/shader 预热）已剔除。完整表格与逐查询明细见 `bench/results.md`。

| 配置 | onnx 体积 | 压缩比 | 文档向量化 | seekdb 检索 | 端到端 | Recall@10 | nDCG@10 | 与 FP32 向量余弦 |
|---|---|---|---|---|---|---|---|---|
| FP32 · WASM（基线） | 90.46 MiB | 1.00× | 19.7 篇/s | 161.6 ms | 252.2 ms | 1.00 | 0.9898 | 基准 |
| INT8 · WASM | 22.90 MiB | 3.95× | 39.7 篇/s | 163.7 ms | 230.5 ms | 1.00 | 0.9898 | 0.98811 |
| FP32 · WebGPU | 90.46 MiB | 1.00× | 71.9 篇/s | 132.7 ms | 206.0 ms | 1.00 | 0.9898 | 1.00000 |
| FP16 · WebGPU | 45.30 MiB | 2.00× | 68.3 篇/s | 93.3 ms | 218.4 ms | 1.00 | 0.9898 | 0.99998 |

结论：

1. INT8 把权重压到 1/3.95，混合检索 Recall@10 与 nDCG@10 **与 FP32 完全一致**，
   向量余弦 0.98811，单元素最大绝对偏差 0.0345——扰动存在但不改变排序。
2. FP16 体积减半，余弦 0.99998，几乎无损。
3. WebGPU 在文档批量向量化上比 WASM 快约 3.6 倍（71.9 vs 19.7 篇/s）；
   但单条查询（batch=1）FP16 反而慢于 FP32（51.6 vs 18.6 ms），
   原因是 GPU 占用率过低、权重转换开销摊不开。**实践中建议：批量导入用 FP16+WebGPU，
   查询侧不需要为省体积牺牲延迟。**
4. 检索延迟 93–164 ms 的波动主要来自公网往返（seekdb 在云服务器），不是数据库内核，
   不宜按后端解读。

> 测量方法上有一个已排除的错误：早期用"分进程各跑一半 + 向量化只计单次"测过一轮，
> 得出过"WebGPU 比 WASM 慢 3 倍"的结论，那是跨进程不可比与预热计入造成的假象。
> 作废数据留在 `bench/archive-v1/`，不参与任何对外结论。

## 3. seekdb 使用

集合由 seekdb-js 创建，`db/schema.sql` 是从运行实例 `SHOW CREATE TABLE` 抓下来的真实 DDL：

```sql
-- 逻辑集合 docs → 物理表 c$v2$<collection_id>
CREATE TABLE `c$v2$<id>` (
  `_id`       varbinary(512) NOT NULL,
  `document`  string,
  `embedding` VECTOR(512),
  `metadata`  json,
  PRIMARY KEY (`_id`),
  FULLTEXT KEY `idx_fts` (`document`) WITH PARSER ik PARSER_PROPERTIES=(ik_mode="smart"),
  VECTOR KEY `idx_vec` (`embedding`)
    WITH (DISTANCE=L2, TYPE=HNSW, LIB=VSAG, M=16, EF_CONSTRUCTION=200, EF_SEARCH=64, SYNC_MODE=ASYNC)
) ORGANIZATION HEAP;
```

用到 seekdb 的四项能力：

1. **向量索引**：HNSW / VSAG，L2 距离，`EF_SEARCH=64`。
2. **中文全文索引**：`WITH PARSER ik`，`ik_mode="smart"`，中文分词开箱可用，不需要额外部署分词服务。
3. **库内混合检索**：命中走 `hybridSearch({ query: { whereDocument: { $contains: kw } }, knn: {...}, rank: { rrf: {} } })`。
   SDK 内部执行 `SELECT DBMS_HYBRID_SEARCH.GET_SQL(...) FROM dual` 再执行返回的 SQL，
   **两路召回与 RRF 融合都由数据库完成，客户端只发一条查询、只收一份结果**，没有本地拼接。
   接口返回体里带 `engine.executedIn = "seekdb"` 与 `metric = "rrf_score"` 便于核对。
4. **增量写入与索引可见性**：HNSW 以 `SYNC_MODE=ASYNC` 异步构建，实测增量写入到纯向量可检索
   存在 **145–258 ms（均值 184 ms，5 次样本）** 的延迟窗口；`ik` 全文索引则是实时可见的。
   `CALL dbms_index_manager.refresh()` 是**同步**的，写入后立刻 refresh 可保证立即可检索
   （实测写入→refresh→查询，一次命中）。
   因此界面在导入后自动 refresh，并保留手动刷新按钮与"待刷新 N 条"提示——
   这既是为了消除那约 200 ms 的窗口，也是为了让"刚导入就能查"成为确定行为而不是靠等。

   三条检索路径的可见性差异（实测）：

   | 路径 | 未 refresh 时新文档 | refresh 后 |
   |---|---|---|
   | 纯向量（HNSW） | 约 200 ms 后可见；立即查询可能查不到 | 立即可见 |
   | 纯全文（ik） | 立即可见 | 立即可见 |
   | 混合（RRF） | 能返回，但只靠全文一路带进来 | 两路都收录 |

   这条差异是实测踩出来的：同一段脚本，间隔几百毫秒查就"看得到"、立即查就"看不到"，
   早期据此写过"未刷新一定查不到"的结论，是不准确的。

物理表与逻辑集合的映射记录在 SDK 维护的 `sdk_collections` 表
（`collection_id` / `collection_name` / `settings`）。集合是否存在、维度多少都从这里查，
不依赖 `getCollection`（该调用惰性，集合不存在时不会报错，直接静默失败）。

## 4. 性能与优化

| 瓶颈 | 现象 | 处理 |
|---|---|---|
| 模型加载 | FP32 冷启动 WASM 首次编译耗时数秒 | 后端静态托管本地权重；FP16/INT8 分别降到 1.0 s / 0.5 s |
| 向量化 | WASM 单线程吞吐受限 | WebGPU 优先，实测 3.6 倍；批量导入按 batch 8 分片避免长任务阻塞界面 |
| 索引可见性 | 写入后立即查询为空 | 导入后自动 `refresh`，并维护待刷新计数 |
| 检索往返 | 公网到云 seekdb 93–164 ms | 查询向量与关键词一次请求发回，混合检索只发一条 SQL，不产生两次往返 |
| 长任务卡顿 | 大批量向量化阻塞主线程 | 分片 + 逐片上报进度，界面保持可交互 |

模型体积与加载耗时都由实际文件字节与 `performance.now()` 测得，不是估算。

## 5. 已知限制

1. 评测语料 30 篇、查询 12 条，规模小，Recall@10 已饱和在 1.00，量化带来的排序差异**在这个规模下看不出来**。
   若要观察质量损失，需要更大语料与更难的负例。
2. 检索延迟包含公网往返，不代表 seekdb 内核性能。本地部署 seekdb 会显著更低。
3. 未做 re-rank、未做分块（chunking）：文档按整篇向量化，长文档会有信息稀释问题。
4. 后端只做转发，不做鉴权，仅适合本机或内网演示。
