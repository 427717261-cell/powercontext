import './style.css';
import { api } from './api.js';
import { detectWebGPU, loadEmbedder, embed, embedQuery, disposeEmbedder, DIM, MODEL_ID, MODEL_URL } from './embedder.js';
import CORPUS from '../data/eval-corpus.json';
import QUERIES from '../data/eval-queries.json';

const $ = (s) => document.querySelector(s);
const fmtMs = (n) => (n >= 1000 ? `${(n / 1000).toFixed(2)}s` : `${Math.round(n)}ms`);
const mib = (b) => +(b / 1048576).toFixed(2);
const mean = (a) => (a.length ? a.reduce((x, y) => x + y, 0) / a.length : 0);
const median = (a) => {
  if (!a.length) return 0;
  const s = [...a].sort((x, y) => x - y);
  const m = s.length >> 1;
  return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2;
};

// onnx 文件字节（与 embedder.js 常量同源；缓存命中时 progress 不再触发，用它兜底）
const ONNX_BYTES = { fp32: 94851877, fp16: 47497427, q8: 24010842, int8: 24010842 };

const CONFIGS = [
  { key: 'fp32-wasm', label: 'FP32 · WASM（量化前基线）', dtype: 'fp32', device: 'wasm' },
  { key: 'q8-wasm', label: 'INT8 · WASM', dtype: 'q8', device: 'wasm' },
  { key: 'fp32-webgpu', label: 'FP32 · WebGPU', dtype: 'fp32', device: 'webgpu' },
  { key: 'fp16-webgpu', label: 'FP16 · WebGPU', dtype: 'fp16', device: 'webgpu' },
];

const DOCS = CORPUS.docs;
const QS = QUERIES.queries;
let results = null;
let baselineVectors = null; // 首个 FP32 配置的文档向量，作为量化漂移的固定基准
let baselineKey = null; // 记录基准来自哪个配置，写进结果便于追溯

/** 两个等长向量的余弦相似度（模型输出已归一化，点积即余弦） */
function cosine(a, b) {
  let s = 0;
  for (let i = 0; i < a.length; i++) s += a[i] * b[i];
  return s;
}

function log(s) {
  const el = $('#log');
  el.textContent += `${new Date().toLocaleTimeString()}  ${s}\n`;
  el.scrollTop = el.scrollHeight;
}

function setProgress(p, label) {
  $('#progress').classList.remove('hidden');
  $('#bar').style.width = `${Math.min(100, p * 100)}%`;
  $('#label').textContent = label || '';
}

// ---------- 指标 ----------
function recallAt(ids, rel, k) {
  const top = new Set(ids.slice(0, k));
  return rel.filter((r) => top.has(r)).length / rel.length;
}

function ndcgAt(ids, rel, k) {
  const relSet = new Set(rel);
  let dcg = 0;
  ids.slice(0, k).forEach((id, i) => {
    if (relSet.has(id)) dcg += 1 / Math.log2(i + 2);
  });
  let idcg = 0;
  for (let i = 0; i < Math.min(rel.length, k); i++) idcg += 1 / Math.log2(i + 2);
  return idcg ? dcg / idcg : 0;
}

// ---------- 单个配置 ----------
// ctx = { out, rounds, batch, step, totalSteps }
async function runConfig(cfg, ctx) {
  const { rounds, batch, step, totalSteps } = ctx;
  const colName = `bench_${cfg.dtype}_${cfg.device}`;
  const pct = (step - 1) / totalSteps;
  log(`—— [${cfg.label}] 开始 ——`);

  // 1. 加载模型（含精度切换）
  await disposeEmbedder();
  setProgress(pct, `${cfg.label}：加载模型…`);
  const tLoad = performance.now();
  const info = await loadEmbedder({
    forceDevice: cfg.device,
    forceDtype: cfg.dtype,
    onProgress: (p) => {
      if (p.phase === 'progress' && p.file) {
        setProgress(pct, `${cfg.label}：下载 ${p.file} ${Math.round(p.pct || 0)}%`);
      }
    },
  });
  const loadMs = performance.now() - tLoad;
  const onnxBytes = ONNX_BYTES[cfg.dtype] ?? info.modelBytes;
  log(`模型就绪：device=${info.device} dtype=${info.dtype} onnx=${mib(onnxBytes)}MiB 加载=${fmtMs(loadMs)}`);

  // 2. 集合清空重建，保证每配置数据状态一致
  await api.deleteCollection(colName).catch(() => {});
  await api.createCollection(colName, DIM);

  // 3. 文档向量化（全部在浏览器本地）
  // 首轮含算子/shader 预热，单独记录不计入中位数；之后按 rounds 轮计时取中位数。
  // 单次计时噪声太大（实测同一配置在两次独立运行间可差 2 倍），不足以支撑对外结论。
  const embedRounds = [];
  let vectors = null;
  for (let r = 0; r < rounds; r++) {
    const tEmbed = performance.now();
    const vs = [];
    for (let i = 0; i < DOCS.length; i += batch) {
      const slice = DOCS.slice(i, i + batch);
      vs.push(...(await embed(slice.map((d) => `${d.title}。${d.text}`))));
      setProgress(pct, `${cfg.label}：向量化 第${r + 1}/${rounds} 轮 ${Math.min(i + batch, DOCS.length)}/${DOCS.length}`);
    }
    const ms = performance.now() - tEmbed;
    embedRounds.push(+ms.toFixed(0));
    vectors = vs; // 入库用最后一轮结果
  }
  const timedRounds = embedRounds.length > 1 ? embedRounds.slice(1) : embedRounds;
  const embedMs = median(timedRounds);
  const throughput = DOCS.length / (embedMs / 1000);
  log(
    `文档向量化 ${DOCS.length} 篇：中位 ${fmtMs(embedMs)}（${throughput.toFixed(1)} 篇/s）` +
      `｜各轮 ${embedRounds.join(' / ')} ms${embedRounds.length > 1 ? '（首轮含预热已剔除）' : ''}`
  );

  // 量化漂移：与本轮 FP32 基线向量逐条算余弦相似度
  let meanCos = null;
  let minCos = null;
  let maxAbsDiff = null;
  if (cfg.dtype === 'fp32' && !baselineVectors) {
    // 只认首个 FP32：四档同轮跑时若每遇 fp32 就覆盖，后面的 q8 会跟错基准
    baselineVectors = vectors;
    baselineKey = cfg.key;
  } else if (baselineVectors) {
    const cos = vectors.map((v, i) => cosine(v, baselineVectors[i]));
    meanCos = +mean(cos).toFixed(5);
    minCos = +Math.min(...cos).toFixed(5);
    let mx = 0;
    vectors.forEach((v, i) => v.forEach((x, j) => (mx = Math.max(mx, Math.abs(x - baselineVectors[i][j])))));
    maxAbsDiff = +mx.toFixed(5);
    log(`量化漂移：与 FP32 向量余弦 均值 ${meanCos} 最小 ${minCos}，单元素最大绝对偏差 ${maxAbsDiff}`);
  }

  // 4. 写入 + 索引刷新
  const items = DOCS.map((d, i) => ({
    id: d.id,
    content: `${d.title}。${d.text}`,
    embedding: vectors[i],
    metadata: { title: d.title },
  }));
  const wr = await api.addDocs(colName, items);
  const rf = await api.refresh(colName);
  log(`写入 seekdb：${fmtMs(wr.ms)}｜refresh：${fmtMs(rf.ms)}`);

  // 5. 检索：多轮取中位数，第一轮结果用于算检索质量
  const perQuery = [];
  const rEmbed = [];
  const rDb = [];
  const rE2e = [];
  for (let r = 0; r < rounds; r++) {
    for (const item of QS) {
      const s = performance.now();
      const emb = await embedQuery(item.q);
      const embedQMs = performance.now() - s;
      const sr = await api.search({
        collection: colName,
        embedding: emb,
        keywords: item.q,
        topK: 10,
        mode: 'hybrid',
      });
      const e2e = performance.now() - s;
      rEmbed.push(embedQMs);
      rDb.push(sr.timings.dbMs);
      rE2e.push(e2e);
      if (r === 0) {
        const ids = sr.results.map((x) => x.id);
        perQuery.push({
          q: item.q,
          type: item.type,
          relevant: item.relevant,
          top10: ids,
          recall5: recallAt(ids, item.relevant, 5),
          recall10: recallAt(ids, item.relevant, 10),
          ndcg10: ndcgAt(ids, item.relevant, 10),
          embedMs: +embedQMs.toFixed(1),
          dbMs: +sr.timings.dbMs.toFixed(1),
          e2eMs: +e2e.toFixed(1),
        });
      }
      setProgress(pct, `${cfg.label}：检索 第${r + 1}/${rounds} 轮 ${item.q}`);
    }
  }

  const recall5 = mean(perQuery.map((p) => p.recall5));
  const recall10 = mean(perQuery.map((p) => p.recall10));
  const ndcg10 = mean(perQuery.map((p) => p.ndcg10));

  // 6. 纯向量路径质量：隔离全文命中，单独看量化对语义检索本身的影响
  setProgress(pct, `${cfg.label}：纯向量质量评估…`);
  const vecHits = [];
  for (const item of QS) {
    const emb = await embedQuery(item.q);
    const sr = await api.search({ collection: colName, embedding: emb, topK: 10, mode: 'vector' });
    const ids = sr.results.map((x) => x.id);
    vecHits.push({ recall10: recallAt(ids, item.relevant, 10), ndcg10: ndcgAt(ids, item.relevant, 10) });
  }
  const vectorRecall10 = mean(vecHits.map((v) => v.recall10));
  const vectorNdcg10 = mean(vecHits.map((v) => v.ndcg10));

  const rec = {
    key: cfg.key,
    label: cfg.label,
    device: info.device,
    dtype: info.dtype,
    onnxMiB: mib(onnxBytes),
    loadMs: +loadMs.toFixed(0),
    docEmbedMs: +embedMs.toFixed(0),
    docEmbedRoundsMs: embedRounds,
    docThroughput: +throughput.toFixed(2),
    writeMs: +wr.ms.toFixed(0),
    refreshMs: +rf.ms.toFixed(0),
    queryEmbedMedianMs: +median(rEmbed).toFixed(1),
    dbSearchMedianMs: +median(rDb).toFixed(1),
    e2eMedianMs: +median(rE2e).toFixed(1),
    recall5: +recall5.toFixed(4),
    recall10: +recall10.toFixed(4),
    ndcg10: +ndcg10.toFixed(4),
    vectorRecall10: +vectorRecall10.toFixed(4),
    vectorNdcg10: +vectorNdcg10.toFixed(4),
    meanCosToBaseline: meanCos,
    minCosToBaseline: minCos,
    maxAbsDiff,
    baselineKey,
    coldStart: step === 1,
    rounds,
    perQuery,
  };
  log(
    `结果：体积 ${rec.onnxMiB}MiB｜向量化 ${rec.docThroughput} 篇/s｜检索中位 ${rec.dbSearchMedianMs}ms｜` +
      `端到端中位 ${rec.e2eMedianMs}ms｜混合 R@5 ${rec.recall5} R@10 ${rec.recall10} nDCG@10 ${rec.ndcg10}｜` +
      `纯向量 R@10 ${rec.vectorRecall10} nDCG@10 ${rec.vectorNdcg10}`
  );
  setProgress(step / totalSteps, `${cfg.label} 完成`);
  return rec;
}

// ---------- 主流程 ----------
async function run() {
  const webgpuOk = await detectWebGPU();
  const selected = CONFIGS.filter((c) => document.querySelector(`#cfg-${c.key}`)?.checked).filter(
    (c) => c.device !== 'webgpu' || webgpuOk
  );
  if (!selected.length) {
    // 无人值守时不能只弹 alert 就停住，会让 harness 一直等到超时
    const msg = webgpuOk ? '至少选择一个当前环境可用的配置' : '本环境不支持 WebGPU，也没有勾选 WASM 配置';
    log(msg);
    window.__benchResult = { env: { webgpuAvailable: webgpuOk, error: msg }, configs: [], errors: [{ key: '-', label: '选择', message: msg }] };
    window.__benchDone = true;
    alert(msg);
    return;
  }

  const rounds = Math.max(1, Math.min(5, Number($('#rounds').value) || 3));
  const batch = Math.max(1, Math.min(32, Number($('#batch').value) || 8));

  $('#btn-run').disabled = true;
  $('#btn-json').disabled = true;
  $('#btn-md').disabled = true;
  $('#log').textContent = '';
  baselineVectors = null; // 每次运行重新确立基准，避免沿用上一轮残留
  baselineKey = null;
  log(`环境：${navigator.userAgent}`);
  log(`WebGPU 可用 ${webgpuOk}｜语料 ${DOCS.length} 篇｜查询 ${QS.length} 条｜轮次 ${rounds}｜批大小 ${batch}`);

  const out = {
    env: {
      userAgent: navigator.userAgent,
      webgpuAvailable: webgpuOk,
      crossOriginIsolated: self.crossOriginIsolated === true,
      hardwareConcurrency: navigator.hardwareConcurrency,
      deviceMemoryGB: navigator.deviceMemory ?? null,
      model: MODEL_ID,
      modelUrl: MODEL_URL,
      dim: DIM,
      corpusDocs: DOCS.length,
      queries: QS.length,
      rounds,
      batchSize: batch,
      seekdb: null,
      startedAt: new Date().toISOString(),
    },
    configs: [],
    errors: [],
  };

  try {
    const h = await api.health();
    out.env.seekdb = { host: h.host, port: h.port, database: h.database, healthLatencyMs: h.latencyMs };
  } catch (e) {
    log(`seekdb 健康检查失败：${e.message}`);
    out.env.seekdbError = e.message;
  }

  const totalSteps = selected.length;
  for (let i = 0; i < selected.length; i++) {
    const cfg = selected[i];
    try {
      const rec = await runConfig(cfg, { rounds, batch, step: i + 1, totalSteps });
      out.configs.push(rec);
    } catch (e) {
      log(`[${cfg.label}] 失败：${e.message}`);
      console.error(e);
      // 失败必须进结果文件：否则 harness 只看到 configs=[] 的"成功"空结果，
      // 无法区分"跑完了没数据"和"根本跑不起来"。
      out.errors.push({ key: cfg.key, label: cfg.label, message: e?.message || String(e), stack: e?.stack || null });
    }
  }

  results = out;
  window.__benchResult = out;
  window.__benchDone = true;
  render(out);
  $('#btn-run').disabled = false;
  $('#btn-json').disabled = out.configs.length === 0;
  $('#btn-md').disabled = out.configs.length === 0;
  setProgress(1, '全部完成');
  log('全部配置完成。可下载 JSON 或复制 Markdown 表格。');
}

// ---------- 渲染 ----------
function render(out) {
  if (!out.configs.length) {
    $('#summary').innerHTML = '<p class="empty">没有成功完成的配置。</p>';
    return;
  }
  const base = out.configs.find((c) => c.dtype === 'fp32' && c.device === 'wasm') || out.configs.find((c) => c.dtype === 'fp32') || out.configs[0];
  const delta = (c, v) => (c === base ? '—' : `${v >= 0 ? '+' : ''}${v.toFixed(3)}`);

  const rows = out.configs
    .map((c) => {
      const ratio = (base.onnxMiB / c.onnxMiB).toFixed(2);
      return `<tr>
        <td>${c.label}</td>
        <td class="num">${c.onnxMiB.toFixed(2)} MiB</td>
        <td class="num">${ratio}×</td>
        <td class="num">${fmtMs(c.loadMs)}</td>
        <td class="num">${c.docThroughput.toFixed(2)} 篇/s</td>
        <td class="num">${c.queryEmbedMedianMs} ms</td>
        <td class="num">${c.dbSearchMedianMs} ms</td>
        <td class="num">${c.e2eMedianMs} ms</td>
        <td class="num">${c.recall5.toFixed(3)}</td>
        <td class="num">${c.recall10.toFixed(3)} <span class="dim">${delta(c, c.recall10 - base.recall10)}</span></td>
        <td class="num">${c.ndcg10.toFixed(3)} <span class="dim">${delta(c, c.ndcg10 - base.ndcg10)}</span></td>
        <td class="num">${c.vectorRecall10.toFixed(3)}</td>
        <td class="num">${c.vectorNdcg10.toFixed(3)}</td>
        <td class="num">${c.meanCosToBaseline == null ? '—' : c.meanCosToBaseline.toFixed(4)}</td>
      </tr>`;
    })
    .join('');

  $('#summary').innerHTML = `<table class="bench-table">
    <thead><tr>
      <th>配置</th><th>onnx 体积</th><th>压缩比</th><th>模型加载</th><th>文档向量化</th>
      <th>查询向量化(中位)</th><th>seekdb 检索(中位)</th><th>端到端(中位)</th>
      <th>混合 R@5</th><th>混合 R@10</th><th>混合 nDCG@10</th><th>纯向量 R@10</th><th>纯向量 nDCG@10</th><th>与 FP32 余弦</th>
    </tr></thead>
    <tbody>${rows}</tbody>
  </table>
  <p class="empty">压缩比 = FP32 onnx 体积 ÷ 该配置 onnx 体积；Δ 相对基线；轮次 ${out.env.rounds} 取中位数；crossOriginIsolated=${out.env.crossOriginIsolated}（决定 WASM 是否多线程）；「与 FP32 余弦」为同后端 FP32 基线与量化版本文档向量的平均余弦相似度。</p>`;

  const first = out.configs[0];
  $('#detail').innerHTML = `<table class="bench-table">
    <thead><tr><th>查询</th><th>类型</th><th>期望</th><th>${first.label} Top5</th><th>R@5</th><th>R@10</th><th>nDCG@10</th></tr></thead>
    <tbody>${first.perQuery
      .map(
        (p) => `<tr><td>${p.q}</td><td class="dim">${p.type}</td><td class="dim">${p.relevant.join(',')}</td>
        <td class="dim">${p.top10.slice(0, 5).join(',')}</td>
        <td class="num">${p.recall5.toFixed(2)}</td><td class="num">${p.recall10.toFixed(2)}</td><td class="num">${p.ndcg10.toFixed(2)}</td></tr>`
      )
      .join('')}</tbody></table>`;
}

// ---------- 导出 ----------
function toMarkdown(out) {
  const base = out.configs.find((c) => c.dtype === 'fp32' && c.device === 'wasm') || out.configs.find((c) => c.dtype === 'fp32') || out.configs[0];
  const head =
    '| 配置 | onnx 体积 | 压缩比 | 模型加载 | 文档向量化 | 查询向量化(中位) | seekdb 检索(中位) | 端到端(中位) | 混合 R@5 | 混合 R@10 | 混合 nDCG@10 | 纯向量 R@10 | 纯向量 nDCG@10 | 与 FP32 余弦 |\n|---|---|---|---|---|---|---|---|---|---|---|---|---|---|';
  const lines = out.configs.map((c) => {
    const ratio = (base.onnxMiB / c.onnxMiB).toFixed(2);
    const cos = c.meanCosToBaseline == null ? '—' : c.meanCosToBaseline.toFixed(4);
    return `| ${c.label} | ${c.onnxMiB.toFixed(2)} MiB | ${ratio}× | ${fmtMs(c.loadMs)}${c.coldStart ? '（冷启动）' : '（热）'} | ${c.docThroughput.toFixed(2)} 篇/s | ${c.queryEmbedMedianMs} ms | ${c.dbSearchMedianMs} ms | ${c.e2eMedianMs} ms | ${c.recall5.toFixed(3)} | ${c.recall10.toFixed(3)} | ${c.ndcg10.toFixed(3)} | ${c.vectorRecall10.toFixed(3)} | ${c.vectorNdcg10.toFixed(3)} | ${cos} |`;
  });
  return [
    head,
    ...lines,
    '',
    `环境：${out.env.userAgent}`,
    `语料 ${out.env.corpusDocs} 篇 / 查询 ${out.env.queries} 条 / 重复 ${out.env.rounds} 轮取中位数 / 批大小 ${out.env.batchSize} / 模型 ${out.env.model} / 维度 ${out.env.dim}`,
    `seekdb：${out.env.seekdb ? `${out.env.seekdb.host}:${out.env.seekdb.port}/${out.env.seekdb.database}` : '未连接'}`,
    `测试时间：${out.env.startedAt}`,
  ].join('\n');
}

// ---------- 事件 ----------
$('#btn-run').addEventListener('click', run);

$('#btn-json').addEventListener('click', () => {
  if (!results) return;
  const blob = new Blob([JSON.stringify(results, null, 2)], { type: 'application/json' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = `bench-${results.env.startedAt.replace(/[:.]/g, '-')}.json`;
  a.click();
  URL.revokeObjectURL(a.href);
});

$('#btn-md').addEventListener('click', async () => {
  if (!results) return;
  const md = toMarkdown(results);
  try {
    await navigator.clipboard.writeText(md);
    log('Markdown 表格已复制到剪贴板');
  } catch {
    alert(md);
  }
});

// ---------- 初始化 ----------
async function init() {
  const webgpuOk = await detectWebGPU();
  const params = new URLSearchParams(location.search);
  const wanted = (params.get('configs') || '')
    .split(',')
    .map((s) => s.trim())
    .filter(Boolean);

  $('#configs').innerHTML = CONFIGS.map((c) => {
    const unavailable = c.device === 'webgpu' && !webgpuOk;
    const checked = wanted.length ? wanted.includes(c.key) : !unavailable;
    return `<label class="chk"><input id="cfg-${c.key}" type="checkbox" ${checked && !unavailable ? 'checked' : ''} ${unavailable ? 'disabled' : ''} /> ${c.label}${unavailable ? '（本环境不可用）' : ''}</label>`;
  }).join('');

  if (params.get('rounds')) $('#rounds').value = params.get('rounds');
  if (params.get('batch')) $('#batch').value = params.get('batch');

  let note = `WebGPU ${webgpuOk ? '可用' : '不可用'}｜语料 ${DOCS.length} 篇｜查询 ${QS.length} 条｜模型 ${MODEL_ID}（${DIM} 维）`;
  try {
    const h = await api.health();
    note += `｜seekdb ${h.host}:${h.port}/${h.database} ${h.latencyMs}ms`;
  } catch {
    note += '｜seekdb 未连接';
  }
  $('#env-note').textContent = note;

  // 无人值守：?autostart=1 自动开跑，结果挂在 window.__benchResult 供脚本读取
  if (params.get('autostart') === '1') {
    log('autostart=1，自动开始');
    await run();
  }
}

init();
