import { defineConfig } from 'vite';
import { fileURLToPath } from 'node:url';

const page = (p) => fileURLToPath(new URL(p, import.meta.url));

export default defineConfig({
  server: {
    port: 5173,
    // 与生产一致：开启 crossOriginIsolated，WASM 后端才能多线程
    headers: {
      'Cross-Origin-Opener-Policy': 'same-origin',
      'Cross-Origin-Embedder-Policy': 'credentialless',
    },
    proxy: {
      '/api': 'http://127.0.0.1:8787',
      '/models': 'http://127.0.0.1:8787',
    },
  },
  build: {
    target: 'es2022',
    chunkSizeWarningLimit: 4000,
    rollupOptions: {
      input: {
        main: page('./index.html'),
        bench: page('./bench.html'),
      },
    },
  },
  // onnxruntime-web 自带 wasm 资源，交给浏览器按需加载，不做预打包
  optimizeDeps: {
    exclude: ['@huggingface/transformers'],
  },
});
