import './style.css';
import { api } from './api.js';
import { loadEmbedder, embed, embedQuery, detectWebGPU, desiredBackend, dtypeFor, MODEL_ID, MODEL_URL, DIM } from './embedder.js';

const $ = (sel) => document.querySelector(sel);
const fmtMs = (n) => (n >= 1000 ? `${(n / 1000).toFixed(2)}s` : `${Math.round(n)}ms`);
const fmtBytes = (b) => (b ? `${(b / 1024 / 1024).toFixed(1)} MB` : '未知');

// 失败要能被指出来：界面提示 + 供自动化脚本读取，避免"点了没反应"式静默失败
function fail(where, err) {
  const msg = `${where}：${err?.message || err}`;
  window.__kbError = msg;
  console.error('[mini-kb]', msg, err);
  const bar = document.querySelector('#import-label');
  if (bar) bar.textContent = msg;
}
window.addEventListener('error', (e) => fail('页面异常', e.error || e.message));
window.addEventListener('unhandledrejection', (e) => fail('未处理的异步错误', e.reason));

const params = new URLSearchParams(location.search);
const FORCE_BACKEND = params.get('backend');

// ---------- 状态 ----------
const state = {
  collection: localStorage.getItem('kb.collection') || 'docs',
  ready: false,
  modelInfo: null,
  autoRefresh: localStorage.getItem('kb.autoRefresh') !== '0',
};

function setBadge(id, text, cls = '', title) {
  const el = $(id);
  el.textContent = text;
  el.className = `badge ${cls}`;
  if (title) el.title = title;
}

// ---------- 初始化 ----------
async function init() {
  const webgpuOk = await detectWebGPU();
  const want = desiredBackend(webgpuOk);
  const dtype = dtypeFor(want);
  setBadge(
    '#badge-backend',
    want === 'webgpu' ? 'WebGPU' : 'WASM',
    want === 'webgpu' ? 'ok' : 'warn',
    `探测：${webgpuOk ? 'WebGPU 可用' : 'WebGPU 不可用'}；本次使用 ${want.toUpperCase()}`
  );
  if (FORCE_BACKEND === 'wasm') setBadge('#badge-backend', 'WASM（已强制降级）', 'warn', 'URL 参数 backend=wasm');
  if (FORCE_BACKEND === 'webgpu' && want === 'wasm') {
    setBadge('#badge-backend', 'WASM（强制 WebGPU 不可用，已降级）', 'err', '请求 WebGPU 但环境不支持');
  }
  $('#backend-select').value = FORCE_BACKEND ?? '';

  api
    .health()
    .then((h) => setBadge('#badge-db', `seekdb ${h.ok ? '已连接' : '异常'} · ${h.latencyMs}ms`, h.ok ? 'ok' : 'err', `${h.host}:${h.port}/${h.database}`))
    .catch(() => setBadge('#badge-db', 'seekdb 连接失败', 'err'));

  // 模型加载（首访需下载，之后走浏览器 HTTP 缓存）
  setBadge('#badge-model', '模型加载中…');
  $('#import-label') && ($('#import-label').textContent = '模型加载中…');
  $('#import-progress').classList.remove('hidden');
  try {
    state.modelInfo = await loadEmbedder({
      onProgress: (p) => {
        if (p.phase === 'progress' && p.file) {
          $('#import-label').textContent = `下载模型 ${p.file} ${Math.round(p.pct || 0)}%`;
        } else if (p.phase === 'mirror-fallback') {
          $('#import-label').textContent = `主源不可用，切换镜像 ${p.file}`;
        }
      },
    });
    state.ready = true;
    // 注意：这里不能设 __kbReady。模型就绪 ≠ 界面可用，bind() 还没执行，
    // 提前置位会让"就绪"被误判为可交互（点按钮毫无反应）。放到 bind() 之后。
    window.__kbModel = state.modelInfo;
    const { device, dtype: dt, modelBytes, loadMs } = state.modelInfo;
    setBadge(
      '#badge-model',
      `${MODEL_ID.split('/')[1]} · ${String(dt).toUpperCase()} · ${fmtBytes(modelBytes)}`,
      'ok',
      `${MODEL_URL}\n加载耗时 ${fmtMs(loadMs)}；实际文件：${Object.keys(state.modelInfo.files).join(', ')}`
    );
  } catch (e) {
    setBadge('#badge-model', '模型加载失败', 'err');
    $('#import-label').textContent = `模型加载失败：${e.message}`;
    alert(
      `端侧模型加载失败：${e.message}

请检查网络（已尝试 huggingface.co 与 hf-mirror.com），或按 README 用本地模型目录离线加载。`
    );
  }
  $('#import-progress').classList.add('hidden');

  await reloadCollections();
  bind();
  renderHistory();
  // 事件已绑定、集合列表已就绪，此刻界面才真正可交互
  window.__kbReady = true;
}

// ---------- 集合管理 ----------
async function reloadCollections() {
  try {
    const { collections } = await api.collections();
    const sel = $('#collection');
    const names = collections.map((c) => c.name);
    if (!names.includes(state.collection)) state.collection = names[0] ?? 'docs';
    sel.innerHTML =
      names.map((n) => `<option value="${n}" ${n === state.collection ? 'selected' : ''}>${n}</option>`).join('') ||
      '<option>docs</option>';
    const cur = collections.find((c) => c.name === state.collection);
    $('#doc-count').textContent = cur && cur.count != null ? `（${cur.count} 条）` : '';
    renderPending(cur?.pendingRefresh ?? 0);
    await reloadDocList();
  } catch (e) {
    console.error(e);
    $('#doc-list').innerHTML = `<p class="empty">读取集合失败：${escapeHtml(e.message)}</p>`;
  }
}

function renderPending(n) {
  const hint = $('#pending-hint');
  if (n > 0) {
    hint.classList.remove('hidden');
    hint.textContent = `待刷新 ${n} 条（刷新后新内容才可检索）`;
    hint.className = 'hint warn';
  } else {
    hint.classList.add('hidden');
  }
}

async function reloadDocList() {
  const list = $('#doc-list');
  try {
    const { docs, total, pendingRefresh } = await api.docs(state.collection);
    renderPending(pendingRefresh);
    if (!docs.length) {
      list.innerHTML = '<p class="empty">空集合。先在上方导入文档。</p>';
      $('#doc-count').textContent = '（0 条）';
      return;
    }
    $('#doc-count').textContent = `（${total} 条）`;
    list.innerHTML = docs
      .map(
        (d) => `<div class="doc" data-id="${d.id}">
          <div class="doc-main"><b>${escapeHtml(d.title || '（无标题）')}</b><span class="dim"> · ${d.length} 字</span>
          <p>${escapeHtml(d.preview)}…</p></div>
          <button class="ghost danger" data-del="${d.id}">删</button>
        </div>`
      )
      .join('');
  } catch (e) {
    list.innerHTML = `<p class="empty">${escapeHtml(e.message)}</p>`;
  }
}

// ---------- 导入 ----------
function chunkText(text, size = 200, overlap = 40) {
  const clean = text.replace(/\r\n/g, '\n').trim();
  if (clean.length <= size) return [clean];
  const chunks = [];
  for (let i = 0; i < clean.length; i += size - overlap) {
    chunks.push(clean.slice(i, i + size));
    if (i + size >= clean.length) break;
  }
  return chunks.filter((c) => c.trim());
}

const SAMPLES = [
  ['向量数据库入门', '向量数据库用于存储高维嵌入向量，支持近似最近邻搜索。它把文本、图片等内容编码为向量后建立索引，检索时按距离返回最相似的条目，是语义检索和推荐系统的基础组件。常见的索引结构包括 HNSW 和 IVF。'],
  ['红烧肉做法', '红烧肉选用肥瘦相间的五花肉，切成麻将块后冷水下锅焯水。锅内放少许油，加冰糖小火炒出糖色，下肉块翻炒上色，加生抽、老抽、料酒和热水，小火慢炖四十分钟，最后大火收汁。'],
  ['OceanBase 简介', 'OceanBase 是蚂蚁集团自研的原生分布式数据库，采用 Paxos 协议保证强一致与高可用，单集群可支撑千万级 QPS。其向量检索能力与 MySQL 兼容接口让 RAG 应用可以直接在数据库内完成混合检索。'],
  ['HNSW 算法要点', 'HNSW 是分层可导航小世界图，通过多层跳表式结构把搜索复杂度降到对数级。构建时逐层插入节点并维护每个节点的近邻连接，查询时从顶层贪心下降到底层，兼顾召回率与速度。'],
  ['什么是混合检索', '混合检索把关键词全文检索与向量语义检索结合：全文检索保证专有名词、编号等精确匹配不丢失，向量检索补足同义改写和跨语言场景。两者在数据库内用一条查询融合，避免客户端拼接两路结果的复杂度。'],
  ['端侧 AI 的优势', '在浏览器本地运行小型模型，数据不出设备，天然满足隐私要求；首访下载模型后即可离线推理，延迟稳定不受网络波动影响。Transformers.js 借助 WebGPU 调用显卡加速，推理吞吐较纯 WASM 显著提升。'],
];

let importing = false;
async function doImport() {
  if (!state.ready) return alert('模型尚未加载完成，请稍候');
  if (importing) return;
  const title = $('#doc-title').value.trim();
  const text = $('#doc-text').value.trim();
  let docs;
  if (text) {
    docs = [[title || '未命名文档', text]];
  } else if ($('#use-sample').checked) {
    docs = SAMPLES;
  } else {
    return alert('请输入文本、选择文件，或勾选示例文档');
  }
  importing = true;
  $('#btn-import').disabled = true;
  try {
    await importBatch(docs);
    $('#doc-text').value = '';
    $('#doc-title').value = '';
  } catch (e) {
    fail('导入失败', e);
  } finally {
    importing = false;
    $('#btn-import').disabled = false;
  }
}

async function importBatch(docs) {
  const metrics = [];
  const show = $('#import-progress');
  show.classList.remove('hidden');
  const t0 = performance.now();

  // 1. 分块
  const items = [];
  for (const [t, body] of docs) {
    const chunks = chunkText(body);
    chunks.forEach((c, i) => {
      items.push({
        id: crypto.randomUUID(),
        title: chunks.length > 1 ? `${t} · 块${i + 1}` : t,
        content: c,
        embedding: null,
        metadata: { title: t, chunk: chunks.length > 1 ? i + 1 : null },
      });
    });
  }
  setProgress(2, `共 ${items.length} 块，开始本地向量化…`);

  // 2. 批量向量化（全部在浏览器本地完成）
  const BATCH = Number($('#batch-size')?.value) || 8;
  const tEmbed = performance.now();
  for (let i = 0; i < items.length; i += BATCH) {
    const batch = items.slice(i, i + BATCH).map((it) => it.content);
    const vecs = await embed(batch);
    vecs.forEach((v, j) => (items[i + j].embedding = v));
    setProgress(5 + ((i + BATCH) / items.length) * 75, `向量化 ${Math.min(i + BATCH, items.length)}/${items.length}`);
  }
  const embedMs = performance.now() - tEmbed;
  metrics.push(['向量化耗时（本地）', fmtMs(embedMs)]);
  metrics.push(['向量化吞吐', `${(items.length / (embedMs / 1000)).toFixed(1)} 块/s`]);
  metrics.push(['批大小', BATCH]);

  // 3. 写入 seekdb
  setProgress(82, '写入 seekdb…');
  const r = await api.addDocs(state.collection, items);
  metrics.push(['写入 seekdb', fmtMs(r.ms)]);

  // 4. 索引刷新（可选，用于演示刷新前后可见性）
  if (state.autoRefresh) {
    setProgress(90, '刷新索引…');
    const rf = await api.refresh(state.collection);
    metrics.push(['索引刷新 refresh()', fmtMs(rf.ms)]);
  } else {
    metrics.push(['索引刷新', `已跳过（待刷新 ${r.pendingRefresh} 条）`]);
  }
  metrics.push(['端到端', fmtMs(performance.now() - t0)]);

  setProgress(100, `完成：${items.length} 块已入库`);
  renderMetrics(metrics);
  window.__kbLastImport = { items: items.length, metrics, collection: state.collection };
  await reloadCollections();
  if (state.autoRefresh) setTimeout(() => show.classList.add('hidden'), 1500);
}

function setProgress(pct, label) {
  $('#import-bar').style.width = `${Math.min(100, pct)}%`;
  $('#import-label').textContent = label || '';
}

function renderMetrics(rows) {
  const tb = $('#import-metrics');
  tb.classList.remove('hidden');
  tb.innerHTML = rows.map(([k, v]) => `<tr><td>${escapeHtml(k)}</td><td>${escapeHtml(v)}</td></tr>`).join('');
}

// ---------- 检索 ----------
const history = JSON.parse(localStorage.getItem('kb.history') || '[]');
function renderHistory() {
  $('#query-history').innerHTML = history
    .map((h) => `<button class="chip" data-q="${escapeHtml(h)}">${escapeHtml(h)}</button>`)
    .join('');
}
function pushHistory(q) {
  const i = history.indexOf(q);
  if (i >= 0) history.splice(i, 1);
  history.unshift(q);
  if (history.length > 8) history.pop();
  localStorage.setItem('kb.history', JSON.stringify(history));
  renderHistory();
}

let searching = false;
async function doSearch() {
  if (!state.ready) return alert('模型尚未加载完成，请稍候');
  const q = $('#query').value.trim();
  if (!q) return;
  if (searching) return;
  searching = true;
  $('#btn-search').disabled = true;
  const t0 = performance.now();
  try {
    const mode = $('#mode').value;
    const topK = Number($('#topk').value) || 8;
    let embedding = null;
    let embedMs = 0;
    if (mode !== 'fulltext') {
      const s = performance.now();
      embedding = await embedQuery(q);
      embedMs = performance.now() - s;
    }
    const r = await api.search({ collection: state.collection, embedding, keywords: q, topK, mode });
    const e2e = performance.now() - t0;
    pushHistory(q);
    renderResults(r, { embedMs, e2e });
  } catch (e) {
    $('#results').innerHTML = `<p class="empty">检索失败：${escapeHtml(e.message)}</p>`;
    $('#search-timing').textContent = '';
  } finally {
    searching = false;
    $('#btn-search').disabled = false;
  }
}

function renderResults(r, t) {
  const scoreLabel = (res) => {
    if (res.distance == null) return '';
    return r.metric === 'l2_distance' ? `距离 ${Number(res.distance).toFixed(3)}` : `得分 ${Number(res.distance).toFixed(4)}`;
  };
  const engine = r.engine.fusion ? `seekdb 内 RRF 融合` : `seekdb ${r.mode}`;
  window.__kbLastSearch = r;
  $('#search-timing').textContent =
    `查询向量化 ${fmtMs(t.embedMs)} · ${engine} ${fmtMs(r.timings.dbMs)} · 端到端 ${fmtMs(t.e2e)} · 返回 ${r.results.length} 条` +
    `（表 ${r.engine.table}）`;

  const parts = [];
  if (!r.results.length) {
    parts.push('<p class="empty">无结果：集合为空，或新写入的数据尚未刷新索引（点上方「刷新索引」）。</p>');
  }
  parts.push(
    r.results
      .map(
        (res, i) => `<div class="result">
          <div class="result-head"><span class="rank">#${i + 1}</span><b>${escapeHtml(res.title || '（无标题）')}</b>
            <span class="dim">${escapeHtml(scoreLabel(res))}</span></div>
          <p>${escapeHtml(res.content)}</p>
        </div>`
      )
      .join('')
  );
  $('#results').innerHTML = parts.join('');
}

// ---------- 工具 ----------
function escapeHtml(s) {
  return String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

// ---------- 事件绑定 ----------
function bind() {
  $('#btn-import').addEventListener('click', doImport);

  $('#btn-pick-file').addEventListener('click', () => $('#file-input').click());
  $('#file-input').addEventListener('change', async (e) => {
    const files = [...e.target.files];
    if (!files.length) return;
    const docs = [];
    for (const f of files) docs.push([f.name.replace(/\.[^.]+$/, ''), await f.text()]);
    $('#doc-text').value = docs.map(([t, c]) => `【${t}】\n${c}`).join('\n\n');
    e.target.value = '';
  });

  const ta = $('#doc-text');
  ['dragover', 'drop'].forEach((ev) =>
    ta.addEventListener(ev, async (e) => {
      e.preventDefault();
      if (ev !== 'drop') return;
      const files = [...(e.dataTransfer?.files ?? [])];
      if (!files.length) return;
      const docs = [];
      for (const f of files) docs.push([f.name.replace(/\.[^.]+$/, ''), await f.text()]);
      ta.value = docs.map(([t, c]) => `【${t}】\n${c}`).join('\n\n');
    })
  );

  $('#btn-search').addEventListener('click', doSearch);
  $('#query').addEventListener('keydown', (e) => e.key === 'Enter' && doSearch());
  $('#query-history').addEventListener('click', (e) => {
    const q = e.target?.dataset?.q;
    if (q) {
      $('#query').value = q;
      doSearch();
    }
  });

  $('#collection').addEventListener('change', async (e) => {
    state.collection = e.target.value;
    localStorage.setItem('kb.collection', state.collection);
    $('#results').innerHTML = '<p class="empty">已切换集合，请重新检索。</p>';
    $('#search-timing').textContent = '';
    await reloadCollections();
  });

  $('#btn-new-collection').addEventListener('click', async () => {
    const name = prompt('新集合名（字母/数字/下划线）');
    if (!name) return;
    try {
      await api.createCollection(name, DIM);
      state.collection = name;
      localStorage.setItem('kb.collection', name);
      await reloadCollections();
    } catch (err) {
      alert(err.message);
    }
  });

  $('#btn-del-collection').addEventListener('click', async () => {
    if (!confirm(`删除集合「${state.collection}」及其全部数据？`)) return;
    try {
      await api.deleteCollection(state.collection);
      state.collection = 'docs';
      await reloadCollections();
    } catch (err) {
      alert(err.message);
    }
  });

  $('#btn-clear').addEventListener('click', async () => {
    if (!confirm(`清空集合「${state.collection}」的全部文档？`)) return;
    await api.deleteDocs(state.collection, []);
    await reloadCollections();
  });

  $('#doc-list').addEventListener('click', async (e) => {
    const id = e.target?.dataset?.del;
    if (!id) return;
    await api.deleteDocs(state.collection, [id]);
    await reloadCollections();
  });

  $('#btn-refresh').addEventListener('click', async () => {
    try {
      const r = await api.refresh(state.collection);
      renderPending(0);
      $('#search-timing').textContent = `索引已刷新：${fmtMs(r.ms)}，本次刷新写入 ${r.flushedWrites} 条`;
      await reloadDocList();
    } catch (e) {
      alert(`刷新失败：${e.message}`);
    }
  });

  $('#backend-select').addEventListener('change', (e) => {
    const v = e.target.value;
    const url = new URL(location.href);
    if (v) url.searchParams.set('backend', v);
    else url.searchParams.delete('backend');
    location.href = url.toString();
  });

  const ar = $('#auto-refresh');
  if (ar) {
    ar.checked = state.autoRefresh;
    ar.addEventListener('change', (e) => {
      state.autoRefresh = e.target.checked;
      localStorage.setItem('kb.autoRefresh', state.autoRefresh ? '1' : '0');
    });
  }
}

init();
