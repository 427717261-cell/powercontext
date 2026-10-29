# 深夜电台 · 怪谈档案 —— 运行证据摘要

- Dify 节点执行记录条数：22（见 `01_dify_node_executions.json`）
- PowerContext Scope 数：5
- seekdb 怪谈档案条数：20
- 索引清单：
  - `idx_radio_archive_embedding_index_snapshot_data_table` (BTREE) 列：__key_22_1793160251406791,story_id
  - `ft_radio_archive` (FULLTEXT) 列：__word_segment_24_1793160250321404,__doc_id_1793160249734159
  - `vid_rowkey_table` (BTREE) 列：__vid_1793160251056535
  - `PRIMARY` (BTREE) 列：story_id
  - `rowkey_vid_table` (BTREE) 列：story_id
  - `idx_radio_archive_embedding` (BTREE) 列：__vid_1793160251056535,__type_22_1793160251332217
  - `idx_radio_archive_embedding_index_id_table` (BTREE) 列：__scn_22_1793160251389679,__vid_1793160251056535,__type_22_1793160251332217

## 节点执行状态
- `answer` (answer) — 播出 — succeeded — 0.042856s
- `llm_host` (llm) — 夜班主播 · 阿雾 — succeeded — 16.467373s
- `http_save` (http-request) — 记住这一轮（PowerContext） — succeeded — 0.684163s
- `code_save` (code) — 整理本轮素材 — succeeded — 0.092852s
- `http_archive` (http-request) — 检索怪谈档案（seekdb） — succeeded — 0.76515s
- `code_memory` (code) — 整理记忆文本 — succeeded — 0.114552s
- `http_recall` (http-request) — 召回听众记忆（PowerContext） — succeeded — 0.745252s
- `code_scope` (code) — 解析 scope_id — succeeded — 0.118337s
- `http_scope` (http-request) — 建立听众档案（PowerContext） — succeeded — 0.584612s
- `code_req` (code) — 组装请求体 — succeeded — 0.202123s
- `start` (start) — 开始 — succeeded — 0.173616s
- `answer` (answer) — 播出 — succeeded — 0.112584s
- `llm_host` (llm) — 夜班主播 · 阿雾 — succeeded — 12.896171s
- `http_save` (http-request) — 记住这一轮（PowerContext） — succeeded — 0.635665s
