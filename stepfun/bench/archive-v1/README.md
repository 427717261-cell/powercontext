# 作废数据（仅保留供追溯，不要引用）

这三份是用**旧测量方法**跑出来的，结论不可信：

- `results-wasm.json` / `results-webgpu.json`：WASM 与 WebGPU 分两次独立进程运行，
  机器状态与网络时延不同，横向不可比；且文档向量化只计单次，未剔除预热轮。
- `results-batch32.json`：同上，且批大小从 8 改到 32 后单次测量噪声被放大
  （q8-wasm 出现 46.7 → 20.18 篇/s 的反常下降）。

由此得到过"WebGPU 向量化比 WASM 慢 3 倍"的错误结论。修正测量方法后
（多轮取中位、剔除预热、四档同进程同环境），真实结果是 WebGPU 快约 3.6 倍。

**权威数据见 `../results-final.json` 与 `../results.md`。**
