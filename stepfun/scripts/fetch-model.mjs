// 下载端侧模型到本地 models/ 目录（一次性，之后全离线可用）
// 用法：
//   node scripts/fetch-model.mjs                 # 只下 INT8（默认，够跑应用）
//   node scripts/fetch-model.mjs --all           # INT8 + FP16 + FP32（跑完整 bench 用）
//   node scripts/fetch-model.mjs --mirror https://hf-mirror.com
import fs from 'node:fs/promises';
import { createWriteStream } from 'node:fs';
import path from 'node:path';
import { Readable } from 'node:stream';
import { pipeline as streamPipeline } from 'node:stream/promises';
import crypto from 'node:crypto';

const MODEL_ID = 'Xenova/bge-small-zh-v1.5';
const args = process.argv.slice(2);
const ALL = args.includes('--all');
const MIRROR = (args.includes('--mirror') ? args[args.indexOf('--mirror') + 1] : 'https://hf-mirror.com').replace(/\/$/, '');

const ROOT = path.resolve(process.cwd(), 'models', MODEL_ID);

const COMMON = [
  'config.json',
  'tokenizer.json',
  'tokenizer_config.json',
  'special_tokens_map.json',
  'vocab.txt',
];
const ONNX = ALL
  ? ['onnx/model_quantized.onnx', 'onnx/model_fp16.onnx', 'onnx/model.onnx']
  : ['onnx/model_quantized.onnx'];

// 官方仓库文件字节数，用于校验完整性（同一口径的体积数据）
const EXPECT = {
  'onnx/model.onnx': 94851877,
  'onnx/model_fp16.onnx': 47497427,
  'onnx/model_quantized.onnx': 24010842,
  'config.json': 716,
  'tokenizer.json': 439125,
  'tokenizer_config.json': 367,
  'special_tokens_map.json': 125,
  'vocab.txt': 109540,
};

async function head(url) {
  const r = await fetch(url, { method: 'HEAD', redirect: 'follow' });
  return r.ok ? Number(r.headers.get('content-length') || 0) : 0;
}

async function download(rel) {
  const dest = path.join(ROOT, rel);
  await fs.mkdir(path.dirname(dest), { recursive: true });
  const url = `${MIRROR}/${MODEL_ID}/resolve/main/${rel}`;
  const t0 = Date.now();
  const res = await fetch(url, { redirect: 'follow' });
  if (!res.ok) throw new Error(`${rel} 下载失败 HTTP ${res.status}`);
  await streamPipeline(Readable.fromWeb(res.body), createWriteStream(dest));
  const st = await fs.stat(dest);
  const sha = crypto.createHash('sha256').update(await fs.readFile(dest)).digest('hex').slice(0, 16);
  const ms = Date.now() - t0;
  const ok = !EXPECT[rel] || EXPECT[rel] === st.size;
  console.log(
    `${ok ? 'OK  ' : 'WARN'} ${rel.padEnd(30)} ${(st.size / 1048576).toFixed(2)} MiB  ${(ms / 1000).toFixed(1)}s  sha256:${sha}`
  );
  if (!ok) console.log(`     期望 ${EXPECT[rel]} 字节，实际 ${st.size}`);
  return { rel, size: st.size, sha, ms };
}

const files = [...COMMON, ...ONNX];
console.log(`模型 ${MODEL_ID}`);
console.log(`源   ${MIRROR}`);
console.log(`目录 ${ROOT}`);
console.log(`文件 ${files.length} 个${ALL ? '（含 FP32/FP16）' : '（仅 INT8）'}\n`);

const report = [];
for (const rel of files) {
  try {
    report.push(await download(rel));
  } catch (e) {
    console.error(`FAIL ${rel}: ${e.message}`);
    process.exitCode = 1;
  }
}

await fs.writeFile(
  path.join(ROOT, 'fetch-report.json'),
  JSON.stringify({ model: MODEL_ID, mirror: MIRROR, fetchedAt: new Date().toISOString(), files: report }, null, 2)
);
console.log('\n完成，报告写入 fetch-report.json');
