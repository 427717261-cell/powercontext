// 探测：未 refresh 时，纯向量 / 纯全文 / 混合 三条路径对新文档的可见性差异
import 'dotenv/config';
import { SeekdbClient } from 'seekdb';

const c = new SeekdbClient({
  host: process.env.SDB_HOST,
  port: Number(process.env.SDB_PORT || 2881),
  user: process.env.SDB_USER,
  password: process.env.SDB_PASSWORD,
  database: process.env.SDB_DATABASE,
});

const NAME = 'probe_vis';
const MARK = `VIS${Date.now().toString(36).toUpperCase()}`;

// 清理旧集合
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
console.log('标记词', MARK);

const vec = (seed) => Array.from({ length: 512 }, (_, i) => Math.sin(seed + i * 0.01));

// 先写入一条历史文档，让索引不是全新状态（更接近真实增量场景）
await col.add({
  ids: ['old-1'],
  documents: ['这条是增量写入之前就存在的旧文档，内容关于向量数据库。'],
  metadatas: [{ title: 'old' }],
  embeddings: [vec(1)],
});
await c.execute('CALL dbms_index_manager.refresh()');
console.log('已写入旧文档并 refresh，作为存量\n');

// 增量写入一条带唯一标记的新文档，不 refresh
await col.add({
  ids: ['new-1'],
  documents: [`${MARK} 这条是增量写入的新文档，内容关于混合检索与索引刷新。`],
  metadatas: [{ title: MARK }],
  embeddings: [vec(2)],
});
console.log('已增量写入新文档（未 refresh），立即查询：\n');

async function probe(tag) {
  const out = {};

  // 纯向量
  try {
    const r = await col.query({ queryEmbeddings: vec(2), nResults: 10, include: ['documents', 'metadatas'] });
    out.vector = (r.ids?.[0] ?? []).length;
    out.vectorHitNew = (r.ids?.[0] ?? []).includes('new-1');
  } catch (e) {
    out.vector = 'err:' + e.message.slice(0, 60);
  }

  // 纯全文
  try {
    const r = await col.hybridSearch({
      query: { whereDocument: { $contains: MARK } },
      nResults: 10,
      include: ['documents', 'metadatas'],
    });
    out.fulltext = (r.ids?.[0] ?? []).length;
    out.fulltextHitNew = (r.ids?.[0] ?? []).includes('new-1');
  } catch (e) {
    out.fulltext = 'err:' + e.message.slice(0, 60);
  }

  // 混合
  try {
    const r = await col.hybridSearch({
      query: { whereDocument: { $contains: MARK } },
      knn: { queryEmbeddings: vec(2), nResults: 10 },
      rank: { rrf: {} },
      nResults: 10,
      include: ['documents', 'metadatas'],
    });
    out.hybrid = (r.ids?.[0] ?? []).length;
    out.hybridHitNew = (r.ids?.[0] ?? []).includes('new-1');
  } catch (e) {
    out.hybrid = 'err:' + e.message.slice(0, 60);
  }

  console.log(`[${tag}]`, JSON.stringify(out));
  return out;
}

const before = await probe('未 refresh');

await c.execute('CALL dbms_index_manager.refresh()');
const after = await probe('refresh 之后');

// 清理
try {
  for (const r of await c.execute('SELECT collection_id FROM sdk_collections WHERE collection_name=?', [NAME])) {
    await c.execute('DROP TABLE IF EXISTS `c$v2$' + r.collection_id + '`');
    await c.execute('DELETE FROM sdk_collections WHERE collection_id=?', [r.collection_id]);
  }
  console.log('\n已清理 probe_vis');
} catch (e) {
  console.log('清理失败:', e.message);
}

console.log('\n结论：');
console.log('  纯向量  未刷新命中新文档 =', before.vectorHitNew, '→ refresh 后 =', after.vectorHitNew);
console.log('  纯全文  未刷新命中新文档 =', before.fulltextHitNew, '→ refresh 后 =', after.fulltextHitNew);
console.log('  混合    未刷新命中新文档 =', before.hybridHitNew, '→ refresh 后 =', after.hybridHitNew);

process.exit(0);
