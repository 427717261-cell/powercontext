// 端侧 embedding：Transformers.js（ONNX Runtime Web），WebGPU 优先，自动降级 WASM
// 模型：Xenova/bge-small-zh-v1.5（BAAI bge-small-zh-v1.5 的 ONNX 转换版，512 维，中文检索专用）
//   精度策略：
//     - WebGPU → fp16（半精度，GPU 上更快、体积约减半）
//     - WASM   → q8  （INT8 量化，CPU 后端实用的低精度方案）
//   可用 URL 参数强制切换，便于验证降级路径：
//     ?backend=wasm | webgpu     ?dtype=fp32|fp16|q8
import { pipeline, env } from '@huggingface/transformers';

export const MODEL_ID = 'Xenova/bge-small-zh-v1.5';
export const DIM = 512;
export const MODEL_URL = `https://huggingface.co/${MODEL_ID}`;
// BGE 中文模型检索时给 query 加指令前缀（官方用法）
export const QUERY_PREFIX = '为这个句子生成表示以用于检索相关文章：';

// 实测文件体积（字节，2026-10-28 从 hf-mirror 下载后 ls 取整），供技术说明的量化前后对比使用
export const MODEL_FILE_BYTES = {
  'onnx/model.onnx': 94851877, // FP32
  'onnx/model_fp16.onnx': 47497427, // FP16
  'onnx/model_quantized.onnx': 24010842, // INT8
};

const params = new URLSearchParams(location.search);
const FORCE_BACKEND = params.get('backend'); // wasm | webgpu | null
const FORCE_DTYPE = params.get('dtype'); // fp32 | fp16 | q8 | null

// 国内网络回退镜像：主源失败时自动切换
const MIRRORS = ['https://huggingface.co/', 'https://hf-mirror.com/'];

let _pipe = null;
let _info = null;

/** 本地是否已放置模型（public/models 或 server 托管的 models/），有则优先本地加载，全离线可用 */
async function hasLocalModel() {
  try {
    const r = await fetch(`/models/${MODEL_ID}/config.json`, { method: 'HEAD', cache: 'no-store' });
    return r.ok;
  } catch {
    return false;
  }
}

export async function detectWebGPU() {
  if (!('gpu' in navigator)) return false;
  try {
    const adapter = await navigator.gpu.requestAdapter();
    return !!adapter;
  } catch {
    return false;
  }
}

export function desiredBackend(webgpuOk) {
  if (FORCE_BACKEND === 'wasm') return 'wasm';
  if (FORCE_BACKEND === 'webgpu') return webgpuOk ? 'webgpu' : 'wasm';
  return webgpuOk ? 'webgpu' : 'wasm';
}

export function dtypeFor(device) {
  if (FORCE_DTYPE) return FORCE_DTYPE;
  return device === 'webgpu' ? 'fp16' : 'q8';
}

/**
 * 加载端侧模型。
 * @param {{onProgress?: Function, forceDevice?: 'webgpu'|'wasm', forceDtype?: string}} opts
 */
export async function loadEmbedder({ onProgress, forceDevice, forceDtype } = {}) {
  if (_pipe) return _info;
  const webgpuOk = await detectWebGPU();
  const device = forceDevice ?? desiredBackend(webgpuOk);
  const dtype = forceDtype ?? dtypeFor(device);
  const forced = !!FORCE_BACKEND || !!FORCE_DTYPE || !!forceDevice || !!forceDtype;

  // 记录实际下载的模型文件体积（与"量化前后体积"同一口径：实际文件字节数）
  const files = {};
  let lastErr = null;

  // 优先本地模型（离线可用、评测环境不依赖外网），没有再去远端（含镜像）
  const local = await hasLocalModel();
  const sources = local ? ['local'] : MIRRORS;

  for (const src of sources) {
    if (src === 'local') {
      env.allowLocalModels = true;
      env.allowRemoteModels = false;
      env.localModelPath = '/models/';
    } else {
      env.allowLocalModels = false;
      env.allowRemoteModels = true;
      env.remoteHost = src;
    }
    for (const k of Object.keys(files)) delete files[k];
    try {
      const t0 = performance.now();
      _pipe = await pipeline('feature-extraction', MODEL_ID, {
        device,
        dtype,
        progress_callback: (p) => {
          if (p?.file && p?.total) files[p.file] = p.total;
          onProgress?.({ phase: p?.status, file: p?.file, pct: p?.progress });
        },
      });
      const loadMs = performance.now() - t0;
      const modelBytes = Object.values(files).reduce((a, b) => a + b, 0);
      _info = {
        device,
        dtype,
        webgpuOk,
        forced,
        forceBackend: FORCE_BACKEND,
        forceDtype: FORCE_DTYPE,
        modelId: MODEL_ID,
        modelUrl: MODEL_URL,
        dim: DIM,
        loadMs: Math.round(loadMs),
        modelBytes,
        files: { ...files },
        source: src === 'local' ? 'local:/models' : src,
      };
      return _info;
    } catch (e) {
      lastErr = e;
      _pipe = null;
      onProgress?.({ phase: 'mirror-fallback', file: src });
    }
  }
  throw lastErr ?? new Error('模型加载失败');
}

/** 文本 → 归一化向量（512 维数组）。传数组则批处理。BGE 系列用 CLS pooling。 */
export async function embed(texts) {
  if (!_pipe) throw new Error('模型未加载');
  const arr = Array.isArray(texts) ? texts : [texts];
  const out = await _pipe(arr, { pooling: 'cls', normalize: true });
  const n = out.dims[0];
  const dim = out.dims[out.dims.length - 1];
  const vectors = [];
  for (let i = 0; i < n; i++) {
    vectors.push(Array.from(new Float32Array(out.data.buffer, out.data.byteOffset + i * dim * 4, dim)));
  }
  return Array.isArray(texts) ? vectors : vectors[0];
}

/** 查询向量：加 BGE 指令前缀后再编码（官方推荐用法） */
export async function embedQuery(text) {
  return embed(`${QUERY_PREFIX}${text}`);
}

/** 释放当前 pipeline（bench 换精度时需要重新加载） */
export async function disposeEmbedder() {
  try {
    await _pipe?.dispose?.();
  } catch {
    /* ignore */
  }
  _pipe = null;
  _info = null;
}

export function currentInfo() {
  return _info;
}
