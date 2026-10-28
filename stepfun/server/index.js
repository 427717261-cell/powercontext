import 'dotenv/config';
import express from 'express';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { SeekdbClient, SeekdbAdminClient } from 'seekdb';

const __dirname = path.dirname(fileURLToPath(import.meta.url));

const config = {
  host: process.env.SDB_HOST || '127.0.0.1',
  port: Number(process.env.SDB_PORT || 2881),
  user: process.env.SDB_USER || 'root',
  password: process.env.SDB_PASSWORD || '',
  database: process.env.SDB_DATABASE || 'stepfun_kb',
  httpPort: Number(process.env.PORT || 8787),
};

const t0 = () => Number(process.hrtime.bigint()) / 1e6;

// ---------- seekdb 连接 ----------
let client = null;
async function getClient() {
  if (client) return client;
  client = new SeekdbClient({
    host: config.host,
    port: config.port,
    user: config.user,
    password: config.password,
    database: config.database,
  });
  return client;
}

async function ensureDatabase() {
  const admin = new SeekdbAdminClient({
    host: config.host,
    port: config.port,
    user: config.user,
    password: config.password,
  });
  const dbs = await admin.listDatabases();
  if (!dbs.includes(config.database)) await admin.createDatabase(config.database);
}

// 连接被服务端/中间网络断开后，mysql2 连接池进入 closed 状态且不会自愈。
// 这里统一捕获连接类错误 → 丢弃旧 client → 重建后重试一次。
const CONN_ERR = /closed state|Connection lost|PROTOCOL_CONNECTION_LOST|ECONNRESET|ETIMEDOUT|socket hang up|Connection lost: The server closed/i;

async function dropClient() {
  const old = client;
  client = null;
  try {
    await old?.close?.();
  } catch {
    /* ignore */
  }
}

async function withConn(fn) {
  try {
    return await fn();
  } catch (e) {
    if (!CONN_ERR.test(`${e?.message}`)) throw e;
    console.warn('[mini-kb] seekdb 连接异常，重建后重试：', e.message);
    await dropClient();
    return await fn();
  }
}

class HttpError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

function safeName(name) {
  if (!/^[a-zA-Z_][a-zA-Z0-9_]{0,62}$/.test(String(name || ''))) {
    throw new HttpError(400, `非法集合名「${name}」：仅允许字母、数字、下划线，且以字母或下划线开头`);
  }
  return String(name);
}

const parseSettings = (s) => {
  if (!s) return null;
  if (typeof s === 'object') return s;
  try {
    return JSON.parse(s);
  } catch {
    return null;
  }
};

/** 集合元数据登记在 sdk_collections，物理表名为 c$v2$<collection_id> */
async function listCollections() {
  const c = await getClient();
  const rows = await c.execute(
    'SELECT collection_id, collection_name, settings, created_at FROM sdk_collections ORDER BY collection_name'
  );
  return rows.map((r) => {
    const settings = parseSettings(r.settings);
    const vec = settings?.schema?.vectorIndex?.hnsw;
    return {
      name: r.collection_name,
      id: r.collection_id,
      table: `c$v2$${r.collection_id}`,
      dimension: vec?.dimension ?? null,
      distance: vec?.distance ?? null,
      analyzer: settings?.schema?.fulltextIndex?.analyzer ?? null,
      createdAt: r.created_at,
    };
  });
}

async function findCollection(name) {
  const all = await listCollections();
  return all.find((c) => c.name === name) ?? null;
}

async function openCollection(name) {
  const meta = await findCollection(name);
  if (!meta) throw new HttpError(404, `集合「${name}」不存在，请先新建集合`);
  const c = await getClient();
  // embeddingFunction: null → 向量一律由浏览器端算好传入，服务端不做任何推理
  const col = await c.getCollection({ name, embeddingFunction: null });
  return { col, meta };
}

async function createCollection(name, dimension = 512) {
  const existing = await findCollection(name);
  if (existing) return existing;
  const c = await getClient();
  // seekdb-js 建表时自动附带 VECTOR KEY(HNSW/VSAG) 与 FULLTEXT KEY(WITH PARSER ik)
  await c.createCollection({
    name,
    configuration: { dimension, distance: 'l2' },
    embeddingFunction: null,
  });
  const meta = await findCollection(name);
  if (!meta) throw new HttpError(500, `集合「${name}」创建后未能读到元数据`);
  return meta;
}

/** 待刷新计数：seekdb 的 HNSW/全文索引为异步构建，写后必须 refresh 才可检索 */
const pendingWrites = new Map();
const bumpPending = (name, n) => pendingWrites.set(name, (pendingWrites.get(name) ?? 0) + n);
const clearPending = () => pendingWrites.clear();

// ---------- REST API ----------
const app = express();
app.use(express.json({ limit: '64mb' }));

// crossOriginIsolated：让 ONNX Runtime 的 WASM 后端启用多线程（SharedArrayBuffer 需要这两个响应头）
app.use((_req, res, next) => {
  res.setHeader('Cross-Origin-Opener-Policy', 'same-origin');
  res.setHeader('Cross-Origin-Embedder-Policy', 'credentialless');
  next();
});

// 本地模型目录（由 scripts/fetch-model.mjs 下载），让端侧推理不依赖外网
app.use('/models', express.static(path.join(__dirname, '..', 'models'), { maxAge: '1h' }));

const wrap = (fn) => (req, res) => {
  Promise.resolve(fn(req, res)).catch((e) => {
    const status = e instanceof HttpError ? e.status : 500;
    res.status(status).json({ error: e.message });
  });
};

// 所有路由都过一层连接自愈
const handle = (fn) => wrap((req, res) => withConn(() => fn(req, res)));

app.get('/api/health', handle(async (_req, res) => {
  const started = t0();
  const c = await getClient();
  const rows = await c.execute('SELECT 1 AS ok, DATABASE() AS db');
  res.json({
    ok: rows?.[0]?.ok === 1,
    host: config.host,
    port: config.port,
    database: config.database,
    latencyMs: +(t0() - started).toFixed(1),
  });
}));

// 数据层自证：集合 → 物理表 → DDL（含 VECTOR / FULLTEXT 索引），供验收留档
app.get('/api/stats', handle(async (req, res) => {
  const c = await getClient();
  const name = req.query.collection ? safeName(req.query.collection) : null;
  const collections = await listCollections();
  const target = name ? collections.find((x) => x.name === name) : null;
  if (name && !target) throw new HttpError(404, `集合「${name}」不存在`);

  const detailed = [];
  for (const meta of target ? [target] : collections) {
    const { col } = await openCollection(meta.name);
    const ddlRow = await c.execute(`SHOW CREATE TABLE \`${meta.table}\``);
    const ddl =
      Object.values(ddlRow?.[0] ?? {}).find((v) => typeof v === 'string' && /CREATE TABLE/i.test(v)) ?? '';
    detailed.push({
      ...meta,
      count: await col.count(),
      pendingRefresh: pendingWrites.get(meta.name) ?? 0,
      indexes: {
        vector: /VECTOR KEY/i.test(ddl),
        fulltext: /FULLTEXT KEY/i.test(ddl),
        hnsw: /HNSW/i.test(ddl),
        ikParser: /WITH PARSER ik/i.test(ddl),
      },
      ddl,
    });
  }
  res.json({
    database: config.database,
    engine: 'seekdb',
    hybridApi: 'DBMS_HYBRID_SEARCH (RRF)',
    collections: detailed,
  });
}));

app.get('/api/collections', handle(async (_req, res) => {
  const collections = await listCollections();
  const out = [];
  for (const meta of collections) {
    let count = null;
    try {
      const { col } = await openCollection(meta.name);
      count = await col.count();
    } catch {
      count = null;
    }
    out.push({ ...meta, count, pendingRefresh: pendingWrites.get(meta.name) ?? 0 });
  }
  res.json({ collections: out });
}));

app.post('/api/collections', handle(async (req, res) => {
  const name = safeName(req.body?.name);
  const dimension = Number(req.body?.dimension || 512);
  const meta = await createCollection(name, dimension);
  res.json({ ok: true, collection: meta });
}));

app.delete('/api/collections/:name', handle(async (req, res) => {
  const name = safeName(req.params.name);
  const meta = await findCollection(name);
  if (!meta) throw new HttpError(404, `集合「${name}」不存在`);
  const c = await getClient();
  await c.execute(`DROP TABLE IF EXISTS \`${meta.table}\``);
  await c.execute('DELETE FROM sdk_collections WHERE collection_id = ?', [meta.id]);
  pendingWrites.delete(name);
  res.json({ ok: true, dropped: meta.table });
}));

app.get('/api/docs', handle(async (req, res) => {
  const name = safeName(req.query.collection || 'docs');
  const limit = Math.min(Math.max(Number(req.query.limit) || 200, 1), 1000);
  const { col } = await openCollection(name);
  const result = await col.get({ include: ['metadatas', 'documents'], limit });
  const docs = (result.ids ?? []).map((id, i) => ({
    id,
    title: result.metadatas?.[i]?.title ?? '',
    chunk: result.metadatas?.[i]?.chunk ?? null,
    preview: (result.documents?.[i] || '').slice(0, 120),
    length: (result.documents?.[i] || '').length,
  }));
  res.json({
    total: await col.count(),
    shown: docs.length,
    docs,
    pendingRefresh: pendingWrites.get(name) ?? 0,
  });
}));

// 批量写入：向量已由浏览器端生成并显式传入，服务端不做任何推理
app.post('/api/docs', handle(async (req, res) => {
  const started = t0();
  const name = safeName(req.body?.collection || 'docs');
  const items = req.body?.items;
  if (!Array.isArray(items) || items.length === 0) throw new HttpError(400, 'items 不能为空');

  const { col, meta } = await openCollection(name);
  for (const it of items) {
    if (!it.id || typeof it.content !== 'string' || !Array.isArray(it.embedding)) {
      throw new HttpError(400, '每条 item 需要 id / content / embedding');
    }
    if (it.embedding.length !== meta.dimension) {
      throw new HttpError(400, `向量维度应为 ${meta.dimension}，收到 ${it.embedding.length}`);
    }
  }
  await col.add({
    ids: items.map((it) => it.id),
    documents: items.map((it) => it.content),
    metadatas: items.map((it) => it.metadata ?? { title: it.title ?? '' }),
    embeddings: items.map((it) => it.embedding),
  });
  bumpPending(name, items.length);
  res.json({
    ok: true,
    written: items.length,
    ms: +(t0() - started).toFixed(1),
    pendingRefresh: pendingWrites.get(name) ?? 0,
  });
}));

app.delete('/api/docs', handle(async (req, res) => {
  const name = safeName(req.body?.collection || 'docs');
  const ids = req.body?.ids;
  const { col } = await openCollection(name);
  if (Array.isArray(ids) && ids.length) {
    await col.delete({ ids });
    bumpPending(name, ids.length);
  } else {
    await col.delete({});
    bumpPending(name, 1);
  }
  res.json({ ok: true, pendingRefresh: pendingWrites.get(name) ?? 0 });
}));

// 索引刷新：HNSW 与全文索引异步构建，写完必须刷新才可检索
app.post('/api/refresh', handle(async (req, res) => {
  const started = t0();
  const name = req.body?.collection ? safeName(req.body.collection) : null;
  const before = name ? pendingWrites.get(name) ?? 0 : [...pendingWrites.values()].reduce((a, b) => a + b, 0);
  const c = await getClient();
  await c.execute('CALL dbms_index_manager.refresh()');
  clearPending();
  res.json({ ok: true, ms: +(t0() - started).toFixed(1), flushedWrites: before });
}));

// 检索：向量 + 全文融合全部在 seekdb 单条查询内完成（DBMS_HYBRID_SEARCH + RRF）
app.post('/api/search', handle(async (req, res) => {
  const started = t0();
  const name = safeName(req.body?.collection || 'docs');
  const { embedding, keywords, topK = 8, mode = 'hybrid' } = req.body || {};
  const n = Math.min(Math.max(Number(topK) || 8, 1), 50);
  const kw = keywords && String(keywords).trim() ? String(keywords).trim() : null;
  const { col, meta } = await openCollection(name);

  if (mode !== 'fulltext') {
    if (!Array.isArray(embedding)) throw new HttpError(400, '缺少查询向量（mode=vector/hybrid 需要）');
    if (embedding.length !== meta.dimension) {
      throw new HttpError(400, `查询向量维度应为 ${meta.dimension}，收到 ${embedding.length}`);
    }
  }

  const dbStart = t0();
  let result;
  let metric;
  let fusion;
  if (mode === 'vector') {
    result = await col.query({
      queryEmbeddings: embedding,
      nResults: n,
      include: ['documents', 'metadatas', 'distances'],
    });
    metric = 'l2_distance';
    fusion = null;
  } else if (mode === 'fulltext') {
    if (!kw) throw new HttpError(400, '缺少关键词（mode=fulltext 需要）');
    result = await col.hybridSearch({
      query: { whereDocument: { $contains: kw } },
      nResults: n,
      include: ['documents', 'metadatas', 'distances'],
    });
    metric = 'fulltext_score';
    fusion = null;
  } else {
    if (!kw) throw new HttpError(400, '混合检索需要关键词（同时给向量与关键词）');
    result = await col.hybridSearch({
      query: { whereDocument: { $contains: kw } },
      knn: { queryEmbeddings: embedding, nResults: n },
      rank: { rrf: {} },
      nResults: n,
      include: ['documents', 'metadatas', 'distances'],
    });
    metric = 'rrf_score';
    fusion = 'rrf';
  }
  const dbMs = +(t0() - dbStart).toFixed(1);

  const ids = result.ids?.[0] ?? [];
  const docs = result.documents?.[0] ?? [];
  const metas = result.metadatas?.[0] ?? [];
  const dists = result.distances?.[0] ?? [];
  res.json({
    mode,
    metric,
    engine: { store: 'seekdb', collection: name, table: meta.table, fusion, executedIn: 'seekdb' },
    timings: { dbMs, totalMs: +(t0() - started).toFixed(1) },
    results: ids.map((id, i) => ({
      id,
      title: metas?.[i]?.title ?? '',
      chunk: metas?.[i]?.chunk ?? null,
      content: docs?.[i] ?? '',
      distance: dists?.[i] ?? null,
    })),
  });
}));

// ---------- 静态托管（生产模式） ----------
const distDir = path.join(__dirname, '..', 'dist');
app.use(express.static(distDir));
app.get(/^\/(?!api\/).*/, (_req, res) => res.sendFile(path.join(distDir, 'index.html')));

// ---------- 启动 ----------
async function main() {
  await ensureDatabase();
  try {
    await createCollection('docs', 512);
  } catch (e) {
    console.warn('[mini-kb] 初始化默认集合失败：', e.message);
  }
  app.listen(config.httpPort, () => {
    console.log(`[mini-kb] API     http://127.0.0.1:${config.httpPort}/api`);
    console.log(`[mini-kb] seekdb  ${config.host}:${config.port} / ${config.database}`);
    console.log(`[mini-kb] 前端    生产模式 http://127.0.0.1:${config.httpPort}/ ｜ 开发模式用 vite（5173）`);
  });

  // 保活：提前发现空闲断连并重建（断线自愈逻辑见 withConn）
  const ping = setInterval(() => {
    withConn(async () => {
      const c = await getClient();
      await c.execute('SELECT 1');
    }).catch(() => {});
  }, 30_000);
  ping.unref?.();
}

// 全局兜底：单条请求或后台保活链上的 reject 不应带走整个服务进程。
// 之前没有这层保护，Node 对未处理的 rejection 默认退出，导致服务在长跑时突然静默死亡
// （bench 跑到 WebGPU 那轮时后端已不在，所有配置连带失败）。
process.on('unhandledRejection', (e) => {
  console.error('[mini-kb] 未处理的 rejection（已拦截，服务继续）:', e?.message || e);
});
process.on('uncaughtException', (e) => {
  console.error('[mini-kb] 未捕获异常（已拦截，服务继续）:', e?.message || e);
});

main().catch((e) => {
  console.error('[mini-kb] 启动失败:', e.message);
  process.exit(1);
});
