// 端到端验收脚本：无人值守跑「导入 → 刷新前后可见性 → 混合检索」整条链路
// 用法：node scripts/e2e-flow.mjs [--browser <path>] [--port 9340] [--backend wasm|webgpu]
// 输出：逐项 PASS/FAIL + JSON 摘要（写到 evidence/e2e-<ts>.json）
import { spawn } from 'node:child_process';
import fs from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';

const args = process.argv.slice(2);
const arg = (n, d) => {
  const i = args.indexOf(`--${n}`);
  return i >= 0 && args[i + 1] && !args[i + 1].startsWith('--') ? args[i + 1] : d;
};
const BASE = arg('base', 'http://127.0.0.1:8787');
const PORT = Number(arg('port', '9340'));
const BACKEND = arg('backend', 'wasm');
const HEADLESS = args.includes('--headless');
const MARK = `E2E-${Date.now().toString(36)}`;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const CANDIDATES = [
  arg('browser', ''),
  'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
  'C:/Program Files/Microsoft/Edge/Application/msedge.exe',
  'C:/Program Files/Google/Chrome/Application/chrome.exe',
].filter(Boolean);

let browser = null;
for (const p of CANDIDATES) {
  try {
    await fs.access(p);
    browser = p;
    break;
  } catch {
    /* next */
  }
}
if (!browser) throw new Error('未找到 Edge/Chrome，请用 --browser 指定');

// 固定 profile：模型 ONNX 体积大，每次换新目录都会导致缓存失效、重新下载并重新编译
// （实测临时目录方案下 WASM q8 冷启动要 90 s 以上，固定目录复用缓存后降到数秒）
const profile = path.resolve('.cache/e2e-profile');
await fs.mkdir(profile, { recursive: true });
const proc = spawn(
  browser,
  [
    `--remote-debugging-port=${PORT}`,
    `--user-data-dir=${profile}`,
    // headless + --disable-gpu 时 WASM 后端会退化，24 MB 模型编译可超过 5 分钟；
    // 默认走 headful 更快，也便于人工观察界面。要纯无头可加 --headless。
    ...(HEADLESS ? ['--headless=new', '--disable-gpu'] : ['--window-size=1280,900']),
    // WebGPU 在部分 Edge/Chrome 版本需显式解锁，与 bench 脚本保持一致
    ...(BACKEND === 'webgpu' && !HEADLESS ? ['--enable-unsafe-webgpu', '--ignore-gpu-blocklist'] : []),
    '--no-first-run',
    'about:blank',
  ],
  { stdio: 'ignore' }
);
process.on('exit', () => {
  try {
    proc.kill();
  } catch {
    /* ignore */
  }
});

let targets = null;
for (let i = 0; i < 60; i++) {
  try {
    const r = await fetch(`http://127.0.0.1:${PORT}/json/list`);
    targets = await r.json();
    if (targets?.length) break;
  } catch {
    /* retry */
  }
  await sleep(500);
}
if (!targets?.length) throw new Error('devtools 未就绪');

const ws = new WebSocket(targets.find((t) => t.type === 'page').webSocketDebuggerUrl);
await new Promise((res, rej) => {
  ws.addEventListener('open', res);
  ws.addEventListener('error', rej);
});
let id = 0;
const pending = new Map();
ws.addEventListener('message', (ev) => {
  const m = JSON.parse(ev.data);
  if (m.id && pending.has(m.id)) {
    const { resolve, reject } = pending.get(m.id);
    pending.delete(m.id);
    m.error ? reject(new Error(JSON.stringify(m.error))) : resolve(m.result);
  }
});
const send = (method, params = {}) =>
  new Promise((resolve, reject) => {
    const mid = ++id;
    pending.set(mid, { resolve, reject });
    ws.send(JSON.stringify({ id: mid, method, params }));
  });

const evaluate = async (expr) => {
  const r = await send('Runtime.evaluate', { expression: expr, returnByValue: true, awaitPromise: true });
  if (r.exceptionDetails) throw new Error(r.exceptionDetails.exception?.description || '页面执行异常');
  return r.result?.value;
};

const waitFor = async (expr, timeoutMs, label) => {
  const t0 = Date.now();
  while (Date.now() - t0 < timeoutMs) {
    if (await evaluate(expr)) return true;
    await sleep(1000);
  }
  // 超时要能说清卡在哪，否则只能靠猜
  const diag = await evaluate(`JSON.stringify({
    label: document.querySelector('#import-label')?.textContent ?? null,
    model: document.querySelector('#badge-model')?.textContent ?? null,
    backend: document.querySelector('#badge-backend')?.textContent ?? null,
    db: document.querySelector('#badge-db')?.textContent ?? null,
    err: window.__kbError ?? null,
    ready: window.__kbReady ?? null,
    modelInfo: window.__kbModel ?? null,
  })`).catch(() => '（页面已不可访问）');
  throw new Error(`等待超时：${label}\n页面状态：${diag}`);
};

const results = [];
const check = (name, ok, detail) => {
  results.push({ name, ok, detail });
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${detail ? `  —— ${detail}` : ''}`);
};

await send('Page.enable');
await send('Runtime.enable');

// ---------- 1. 打开应用，等模型就绪 ----------
await send('Page.navigate', { url: `${BASE}/index.html?backend=${BACKEND}` });
console.log(`打开 ${BASE}/index.html?backend=${BACKEND}`);
await waitFor('window.__kbReady === true', 300000, '模型加载');
const model = await evaluate('JSON.stringify(window.__kbModel)');
const m = JSON.parse(model);
check('A2-端侧推理就绪', true, `device=${m.device} dtype=${m.dtype} 来源=${m.source} 加载=${m.loadMs}ms 体积=${(m.modelBytes / 1048576).toFixed(2)}MiB`);
check('A4-后端选择', m.device === BACKEND, `请求 ${BACKEND}，实际 ${m.device}`);

const h = await evaluate('fetch("/api/health").then(r=>r.json()).then(JSON.stringify)');
check('A5-seekdb 连通', JSON.parse(h).ok === true, h.slice(0, 120));

// ---------- 2. 关闭自动刷新，导入一条带唯一标记的文档 ----------
// 显式置为关闭，不要用 click 切换：固定 profile 下 localStorage 会残留上次运行的选择，
// toggle 一次的结果取决于历史状态，不幂等（曾因此让"待刷新"断言随机失败）。
const autoRefreshOff = await evaluate(`(() => {
  const el = document.querySelector('#auto-refresh');
  if (el.checked) el.click();
  return el.checked === false;
})()`);
if (!autoRefreshOff) throw new Error('无法关闭自动刷新开关');
const docText = `${MARK} 这条文档用来验证索引刷新前后可见性。向量检索与全文检索都应当能命中它。`;
await evaluate(`(() => {
  const t = document.querySelector('#doc-text');
  t.value = ${JSON.stringify(docText)};
  document.querySelector('#doc-title').value = ${JSON.stringify(MARK)};
  document.querySelector('#use-sample').checked = false;
  document.querySelector('#btn-import').click();
  return true;
})()`);
await waitFor('window.__kbLastImport != null', 180000, '导入完成');
const imp = JSON.parse(await evaluate('JSON.stringify(window.__kbLastImport)'));
check('A1-导入与向量化写入', imp.items >= 1, `写入 ${imp.items} 块｜${imp.metrics.map(([k, v]) => `${k}=${v}`).join(' · ')}`);
check('A6-写入后处于待刷新', /待刷新/.test(JSON.stringify(imp.metrics)), '自动刷新已关闭，指标显示待刷新');

// ---------- 3. 刷新前检索 ----------
// 注意：不能拿混合检索去断言"刷新前不可见"。实测（scripts/_probe-visibility.mjs）：
//   纯向量(HNSW) 异步建索引 → 未 refresh 查不到新文档
//   纯全文(ik)    实时          → 未 refresh 也能查到新文档
//   混合(RRF)     含全文一路     → 未 refresh 也能"看到"新文档，但向量那路尚未收录
// 所以按路径分别断言，才是有意义的验证。
const searchOnce = async (q, mode = 'hybrid') => {
  await evaluate(`(() => {
    const el = document.querySelector('#query');
    el.value = ${JSON.stringify(q)};
    document.querySelector('#mode').value = ${JSON.stringify(mode)};
    window.__kbLastSearch = null;
    document.querySelector('#btn-search').click();
    return true;
  })()`);
  await sleep(300);
  await waitFor('window.__kbLastSearch != null', 60000, '检索返回');
  const r = JSON.parse(await evaluate('JSON.stringify(window.__kbLastSearch)'));
  return r;
};
const hitNew = (r) => r.results.some((x) => x.title === MARK || x.content.includes(MARK));

const beforeVec = await searchOnce(MARK, 'vector');
// 观察项，不作断言：HNSW 是异步构建，存在秒级可见性延迟窗口。
// 实测同一条链路，有时刷新前查不到、有时能查到（后台合并已完成），取决于时机，
// 拿它当硬断言必然飘。真正要保证的是"refresh 之后一定可检索"，见下方断言。
console.log(
  `OBS   A6-刷新前纯向量可见性  —— 返回 ${beforeVec.results.length} 条，含新文档=${hitNew(beforeVec)}（异步索引，仅记录）`
);

const beforeFts = await searchOnce(MARK, 'fulltext');
check(
  'A6-刷新前全文已可检索',
  hitNew(beforeFts) === true,
  `全文检索返回 ${beforeFts.results.length} 条，含新文档=${hitNew(beforeFts)}（ik 全文索引实时，符合预期）`
);

const beforeHyb = await searchOnce(MARK, 'hybrid');
check(
  'A6-刷新前混合可命中但靠全文一路',
  hitNew(beforeHyb) === true,
  `混合检索返回 ${beforeHyb.results.length} 条，含新文档=${hitNew(beforeHyb)}`
);

// ---------- 4. 刷新索引后再检索：向量路径应能查到 ----------
await evaluate('window.__kbLastSearch = null; document.querySelector("#btn-refresh").click();');
await sleep(2500);
const after = await searchOnce(MARK, 'vector');
const afterHit = hitNew(after);
check('A6-刷新后纯向量可检索', afterHit === true, `刷新后向量检索返回 ${after.results.length} 条，含新文档=${afterHit}`);

const afterHyb = await searchOnce(MARK, 'hybrid');
check(
  'A5-混合检索在库内',
  afterHyb.engine?.executedIn === 'seekdb' && afterHyb.engine?.fusion === 'rrf',
  `engine=${JSON.stringify(afterHyb.engine)} 耗时 db=${afterHyb.timings.dbMs}ms 命中=${afterHyb.results.length} 条`
);

// ---------- 5. 清理（按标题精确定位，删除后刷新） ----------
const cleaned = await evaluate(`(async () => {
  const r = await fetch('/api/docs?collection=docs&limit=1000').then((x) => x.json());
  const ids = (r.docs || []).filter((d) => d.title === ${JSON.stringify(MARK)}).map((d) => d.id);
  if (ids.length) {
    await fetch('/api/docs', { method: 'DELETE', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ collection: 'docs', ids }) });
    await fetch('/api/refresh', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ collection: 'docs' }) });
  }
  return ids.length;
})()`);
console.log(`清理测试文档 ${cleaned} 条`);

const out = {
  ranAt: new Date().toISOString(),
  backend: BACKEND,
  userAgent: await evaluate('navigator.userAgent'),
  checks: results,
  model: m,
  import: imp,
  hitBeforeRefreshVector: hitNew(beforeVec),
  hitBeforeRefreshFulltext: hitNew(beforeFts),
  hitBeforeRefreshHybrid: hitNew(beforeHyb),
  hitAfterRefreshVector: afterHit,
  engine: afterHyb.engine,
};
await fs.mkdir('evidence', { recursive: true });
const file = path.join('evidence', `e2e-${Date.now()}.json`);
await fs.writeFile(file, JSON.stringify(out, null, 2));
const failed = results.filter((r) => !r.ok).length;
console.log(`\n${failed === 0 ? '全部通过' : `${failed} 项失败`}｜摘要 ${file}`);
proc.kill();
process.exit(failed === 0 ? 0 : 1);
