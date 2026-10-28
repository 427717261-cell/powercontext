// 量化：增量写入后，纯向量检索需要多久才能看到新文档（HNSW 异步索引的可见性延迟）
import 'dotenv/config';
import { SeekdbClient } from 'seekdb';

const c = new SeekdbClient({
  host: process.env.SDB_HOST,
  port: Number(process.env.SDB_PORT || 2881),
  user: process.env.SDB_USER,
  password: process.env.SDB_PASSWORD,
  database: process.env.SDB_DATABASE,
});

const NAME = 'probe_lat';
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

try {
  for (const r of await c.execute('SELECT collection_id FROM sdk_collections WHERE collection_name=?', [NAME])) {
    await c.execute('DROP TABLE IF EXISTS `c$v2$' + r.collection_id + '`');
    await c.execute('DELETE FROM sdk_collections WHERE collection_id=?', [r.collection_id]);
  }
} catch (e) {
  console.log('清理跳过:', e.message);
}

const col = await c.createCollection({
  name: NAME,
  configuration: { dimension: 512, distance: 'l2' },
  embeddingFunction: null,
});
const vec = (seed) => Array.from({ length: 512 }, (_, i) => Math.sin(seed + i * 0.01));

// 先铺 20 条存量并强制刷新，模拟真实已有索引的场景
const seedIds = Array.from({ length: 20 }, (_, i) => `seed-${i}`);
await col.add({
  ids: seedIds,
  documents: seedIds.map((id) => `${id} 存量文档，关于向量检索与数据库。`),
  metadatas: seedIds.map((id) => ({ title: id })),
  embeddings: seedIds.map((_, i) => vec(i + 1)),
});
await c.execute('CALL dbms_index_manager.refresh()');
console.log(`已铺 ${seedIds.length} 条存量并 refresh\n`);

const waitVisible = async (id) => {
  const t0 = Date.now();
  for (let i = 0; i < 100; i++) {
    const r = await col.query({ queryEmbeddings: vec(100 + i), nResults: 50, include: ['metadatas'] });
    if ((r.ids?.[0] ?? []).includes(id)) return Date.now() - t0;
    await sleep(100);
  }
  return null;
};

const rounds = [];
for (let k = 0; k < 5; k++) {
  const id = `inc-${k}`;
  // 用与自身最接近的向量查询，确保一旦入索引必然排在最前
  const emb = vec(100 + k * 7);
  await col.add({
    ids: [id],
    documents: [`${id} 增量文档，用于测量可见性延迟。`],
    metadatas: [{ title: id }],
    embeddings: [emb],
  });

  const t0 = Date.now();
  let visibleAt = null;
  for (let i = 0; i < 120; i++) {
    const r = await col.query({ queryEmbeddings: emb, nResults: 50, include: ['metadatas'] });
    if ((r.ids?.[0] ?? []).includes(id)) {
      visibleAt = Date.now() - t0;
      break;
    }
    await sleep(100);
  }
  rounds.push(visibleAt);
  console.log(`第 ${k + 1} 次增量写入：纯向量可见耗时 ${visibleAt === null ? '>12s 未可见' : visibleAt + ' ms'}`);
}

const ok = rounds.filter((x) => x !== null);
console.log('\n汇总：');
console.log('  样本', JSON.stringify(rounds));
if (ok.length) {
  console.log(`  可见延迟 最小 ${Math.min(...ok)} ms / 最大 ${Math.max(...ok)} ms / 均值 ${Math.round(ok.reduce((a, b) => a + b, 0) / ok.length)} ms`);
}

// 再验证：refresh 是否能让"立刻可见"
const id = 'forced-1';
const emb = vec(999);
await col.add({ ids: [id], documents: ['forced-1 强制刷新测试'], metadatas: [{ title: id }], embeddings: [emb] });
await c.execute('CALL dbms_index_manager.refresh()');
const r = await col.query({ queryEmbeddings: emb, nResults: 10, include: ['metadatas'] });
const hit = (r.ids?.[0] ?? []).includes(id);
console.log(`\n写入后立刻 refresh 再查：${hit ? '可检索' : '仍不可检索'}（证明 refresh 是同步生效的）`);

try {
  for (const row of await c.execute('SELECT collection_id FROM sdk_collections WHERE collection_name=?', [NAME])) {
    await c.execute('DROP TABLE IF EXISTS `c$v2$' + row.collection_id + '`');
    await c.execute('DELETE FROM sdk_collections WHERE collection_id=?', [row.collection_id]);
  }
  console.log('已清理 probe_lat');
} catch (e) {
  console.log('清理失败:', e.message);
}

process.exit(0);
