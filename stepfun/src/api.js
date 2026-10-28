// REST 封装：所有向量都在浏览器端算好再发出去（服务端不做推理）
async function jfetch(url, options) {
  const res = await fetch(url, options);
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.error || `HTTP ${res.status}`);
  return body;
}

const json = (method, body) => ({
  method,
  headers: { 'content-type': 'application/json' },
  body: JSON.stringify(body),
});

export const api = {
  health: () => jfetch('/api/health'),
  stats: (collection) =>
    jfetch(`/api/stats${collection ? `?collection=${encodeURIComponent(collection)}` : ''}`),
  collections: () => jfetch('/api/collections'),
  createCollection: (name, dimension = 512) => jfetch('/api/collections', json('POST', { name, dimension })),
  deleteCollection: (name) => jfetch(`/api/collections/${encodeURIComponent(name)}`, { method: 'DELETE' }),
  docs: (collection, limit = 200) =>
    jfetch(`/api/docs?collection=${encodeURIComponent(collection)}&limit=${limit}`),
  addDocs: (collection, items) => jfetch('/api/docs', json('POST', { collection, items })),
  deleteDocs: (collection, ids) => jfetch('/api/docs', json('DELETE', { collection, ids })),
  refresh: (collection) => jfetch('/api/refresh', json('POST', { collection })),
  search: (body) => jfetch('/api/search', json('POST', body)),
};
