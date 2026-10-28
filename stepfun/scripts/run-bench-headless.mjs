// 用本机 Chromium 内核浏览器（Edge/Chrome）无人值守跑 bench，把结果存成 JSON
// 用法：
//   node scripts/run-bench-headless.mjs --configs fp32-wasm,q8-wasm --rounds 3
//   node scripts/run-bench-headless.mjs --webgpu          # 需真实 GPU，走 headful
//   node scripts/run-bench-headless.mjs --browser "C:/Program Files/Google/Chrome/Application/chrome.exe"
import { spawn } from 'node:child_process';
import fs from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';

const args = process.argv.slice(2);
const arg = (name, def) => {
  const i = args.indexOf(`--${name}`);
  return i >= 0 && args[i + 1] && !args[i + 1].startsWith('--') ? args[i + 1] : def;
};
const flag = (name) => args.includes(`--${name}`);

const BASE = arg('base', 'http://127.0.0.1:8787');
const CONFIGS = arg('configs', 'fp32-wasm,q8-wasm');
const ROUNDS = arg('rounds', '3');
const BATCH = arg('batch', '8');
const PORT = Number(arg('port', '9333'));
const WEBGPU = flag('webgpu');
const OUT = arg('out', path.join('bench', `bench-${new Date().toISOString().replace(/[:.]/g, '-')}.json`));

const CANDIDATES = [
  arg('browser', ''),
  'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
  'C:/Program Files/Microsoft/Edge/Application/msedge.exe',
  'C:/Program Files/Google/Chrome/Application/chrome.exe',
  'C:/Program Files (x86)/Google/Chrome/Application/chrome.exe',
].filter(Boolean);

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function findBrowser() {
  for (const p of CANDIDATES) {
    try {
      await fs.access(p);
      return p;
    } catch {
      /* next */
    }
  }
  throw new Error('未找到 Edge/Chrome，请用 --browser 指定可执行文件路径');
}

function makeSender(ws) {
  let id = 0;
  const pending = new Map();
  ws.addEventListener('message', (ev) => {
    const msg = JSON.parse(ev.data);
    if (msg.id && pending.has(msg.id)) {
      const { resolve, reject } = pending.get(msg.id);
      pending.delete(msg.id);
      if (msg.error) reject(new Error(JSON.stringify(msg.error)));
      else resolve(msg.result);
    }
  });
  return (method, params = {}) =>
    new Promise((resolve, reject) => {
      const mid = ++id;
      pending.set(mid, { resolve, reject });
      ws.send(JSON.stringify({ id: mid, method, params }));
    });
}

const browser = await findBrowser();
// 固定 profile：复用模型 HTTP 缓存，避免每轮都重新下载与编译 ONNX
const profile = path.resolve('.cache/bench-profile');
await fs.mkdir(profile, { recursive: true });
const flags = [
  `--remote-debugging-port=${PORT}`,
  `--user-data-dir=${profile}`,
  '--no-first-run',
  '--no-default-browser-check',
  '--disable-extensions',
  // 后台/非前台窗口会被浏览器降频，headful 跑 WebGPU 时必须关掉这些节流
  '--disable-background-timer-throttling',
  '--disable-backgrounding-occluded-windows',
  '--disable-renderer-backgrounding',
];
if (WEBGPU) flags.push('--enable-unsafe-webgpu', '--ignore-gpu-blocklist');
else flags.push('--headless=new', '--disable-gpu');

console.log(`浏览器  ${browser}`);
console.log(`模式    ${WEBGPU ? 'headful + WebGPU 标志' : 'headless（WASM）'}`);
console.log(`配置    ${CONFIGS}｜轮次 ${ROUNDS}｜批大小 ${BATCH}`);

const proc = spawn(browser, [...flags, 'about:blank'], { stdio: 'ignore' });
const kill = () => {
  try {
    proc.kill();
  } catch {
    /* ignore */
  }
};
process.on('exit', kill);
process.on('SIGINT', () => {
  kill();
  process.exit(130);
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
if (!targets?.length) {
  kill();
  throw new Error('devtools 端口未就绪，浏览器可能启动失败');
}

const page = targets.find((t) => t.type === 'page') ?? targets[0];
const ws = new WebSocket(page.webSocketDebuggerUrl);
await new Promise((resolve, reject) => {
  ws.addEventListener('open', resolve);
  ws.addEventListener('error', reject);
});
const send = makeSender(ws);
await send('Page.enable');
await send('Runtime.enable');

const url = `${BASE}/bench.html?autostart=1&configs=${encodeURIComponent(CONFIGS)}&rounds=${ROUNDS}&batch=${BATCH}`;
console.log(`打开    ${url}`);
await send('Page.navigate', { url });

const deadline = Date.now() + 40 * 60 * 1000;
let result = null;
let lastLine = '';
while (Date.now() < deadline) {
  await sleep(3000);
  const r = await send('Runtime.evaluate', {
    expression:
      "(() => ({ done: !!window.__benchDone, res: window.__benchDone ? JSON.stringify(window.__benchResult) : null, log: document.querySelector('#log')?.textContent || '' }))()",
    returnByValue: true,
  });
  const v = r?.result?.value;
  if (v?.log) {
    const line = v.log.trim().split('\n').filter(Boolean).slice(-1)[0] || '';
    if (line && line !== lastLine) {
      lastLine = line;
      console.log(`  ${line}`);
    }
  }
  if (v?.done) {
    result = JSON.parse(v.res);
    break;
  }
}

if (!result) {
  kill();
  throw new Error('等待超时，未拿到 bench 结果');
}

// 空结果不写盘：之前会写出 configs=[] 的"成功"文件，把失败伪装成跑完了
if (!result.configs?.length) {
  kill();
  const errs = (result.errors || []).map((e) => `  · ${e.label}: ${e.message}`).join('\n');
  console.error(`\n所有配置均失败，未写入结果文件。\nseekdb: ${result.env?.seekdb ? 'ok' : '未连接 ' + (result.env?.seekdbError || '')}\n${errs}`);
  process.exit(1);
}

await fs.mkdir(path.dirname(OUT), { recursive: true });
await fs.writeFile(OUT, JSON.stringify(result, null, 2));
console.log(`\n结果写入 ${OUT}`);
for (const c of result.configs) {
  console.log(
    `${c.label.padEnd(26)} 体积 ${String(c.onnxMiB).padStart(6)}MiB  向量化 ${String(c.docThroughput).padStart(6)}/s  ` +
      `检索 ${String(c.dbSearchMedianMs).padStart(7)}ms  端到端 ${String(c.e2eMedianMs).padStart(7)}ms  ` +
      `混合 R@10 ${c.recall10}  nDCG@10 ${c.ndcg10}  纯向量 R@10 ${c.vectorRecall10}  余弦 ${c.meanCosToBaseline ?? '—'}`
  );
}
if (result.errors?.length) {
  console.log(`\n失败 ${result.errors.length} 个配置：`);
  for (const e of result.errors) console.log(`  · ${e.label}: ${e.message}`);
}
kill();
process.exit(0);
