#!/usr/bin/env python3
"""采集「深夜电台 · 怪谈档案」的运行证据。

产出（写到 --out-dir）：
  01_dify_node_executions.json  Dify 每个节点的执行记录（含 HTTP 节点的请求/响应）
  02_dify_workflow_runs.json    工作流执行记录
  03_powercontext_scopes.json   PowerContext Scope 列表
  04_powercontext_memory.json   PowerContext 记忆检索结果（证明跨会话写回与召回）
  05_seekdb_archive.json        seekdb 里的 20 条怪谈档案
  06_seekdb_indexes.json        档案表的全文/向量索引清单
  07_seekdb_explain.txt         检索语句的 EXPLAIN（全文扫描 + 向量索引）
  08_radio_access.log           电台后端访问日志
  09_summary.md                人读摘要

用法：python3 collect_evidence.py --out-dir <dir> [--listener 小满] [--chat-results <03_chat_results.json>]

默认会读取 <out-dir>/03_chat_results.json，把 Dify 侧的节点执行记录与工作流记录
限定到那两组验证样例的运行上，这样证据文件里的每条记录都能对应到文档里的样例。
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import subprocess
import urllib.error
import urllib.request


def http_json(method: str, url: str, payload=None, timeout=120):
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    headers = {"Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8")
            return response.status, (json.loads(body) if body else None)
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            return exc.code, json.loads(raw)
        except ValueError:
            return exc.code, raw[:2000]
    except Exception as exc:  # noqa: BLE001
        return 0, f"{type(exc).__name__}: {exc}"


def psql(sql: str, db: str = "dify") -> str:
    cmd = [
        "docker", "exec", "dify-deploy-db-1", "psql", "-U", "postgres", "-d", db,
        "-At", "-F", "\t", "-c", sql,
    ]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=120).stdout


def pymysql_query(sql: str, database: str):
    import pymysql

    conn = pymysql.connect(
        host=os.environ["RADIO_SEEKDB_HOST"],
        port=int(os.environ.get("RADIO_SEEKDB_PORT", "2881")),
        user=os.environ["RADIO_SEEKDB_USER"],
        password=os.environ["RADIO_SEEKDB_PASSWORD"],
        database=database,
        charset="utf8mb4",
        connect_timeout=20,
    )
    try:
        with conn.cursor() as cursor:
            cursor.execute(sql)
            rows = cursor.fetchall()
            columns = [desc[0] for desc in cursor.description] if cursor.description else []
        return columns, rows
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--listener", default="小满")
    parser.add_argument("--dify", default="http://127.0.0.1:5001")
    parser.add_argument("--powercontext", default="http://127.0.0.1:8100")
    parser.add_argument("--radio", default="http://127.0.0.1:8801")
    parser.add_argument(
        "--chat-results",
        default="",
        help="样例结果文件（默认取 <out-dir>/03_chat_results.json）。"
        "用它把节点执行记录限定到这两组样例的运行上，避免混入其它运行的记录",
    )
    args = parser.parse_args()

    out = pathlib.Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)

    # ---- 找出两组样例对应的会话，从而把证据限定在样例运行上 ----
    chat_results_path = pathlib.Path(args.chat_results) if args.chat_results else out / "03_chat_results.json"
    conversation_ids: list[str] = []
    if chat_results_path.exists():
        try:
            samples = json.loads(chat_results_path.read_text(encoding="utf-8"))
            conversation_ids = [
                item.get("conversation_id")
                for item in samples
                if isinstance(item, dict) and item.get("conversation_id")
            ]
        except ValueError:
            conversation_ids = []
    if conversation_ids:
        print("样例会话：", ", ".join(conversation_ids))
    else:
        print("未找到样例会话，节点记录将取最近 40 条")

    quoted = ",".join(f"'{cid}'" for cid in conversation_ids)
    run_filter = (
        f"id in (select id from workflow_runs where "
        f"inputs::jsonb->>'sys.conversation_id' in ({quoted}))"
        if conversation_ids
        else "1=1"
    )
    node_filter = (
        f"workflow_run_id in (select id from workflow_runs where "
        f"inputs::jsonb->>'sys.conversation_id' in ({quoted}))"
        if conversation_ids
        else "1=1"
    )

    # ---- Dify 节点级执行记录 ----
    sql = (
        "select id, app_id, workflow_run_id, node_id, node_type, title, status, elapsed_time, "
        f"inputs, outputs, error from workflow_node_executions where {node_filter} "
        "order by created_at desc limit 40"
    )
    rows = psql(sql).splitlines()
    records = []
    for row in rows:
        parts = row.split("\t")
        if len(parts) < 11:
            continue
        records.append(
            {
                "id": parts[0],
                "app_id": parts[1],
                "workflow_run_id": parts[2],
                "node_id": parts[3],
                "node_type": parts[4],
                "title": parts[5],
                "status": parts[6],
                "elapsed_time": parts[7],
                "inputs": parts[8],
                "outputs": parts[9],
                "error": parts[10],
            }
        )
    (out / "01_dify_node_executions.json").write_text(
        json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print("node executions:", len(records))

    sql = (
        "select id, app_id, status, workflow_id, created_by, created_at, elapsed_time, inputs, outputs "
        f"from workflow_runs where {run_filter} order by created_at desc limit 10"
    )
    (out / "02_dify_workflow_runs.json").write_text(psql(sql), encoding="utf-8")

    # ---- PowerContext ----
    status, scopes = http_json("GET", f"{args.powercontext}/v1/scopes")
    (out / "03_powercontext_scopes.json").write_text(
        json.dumps({"status": status, "body": scopes}, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print("scopes:", status)

    scope_id = ""
    if isinstance(scopes, dict):
        for item in scopes.get("items") or scopes.get("scopes") or []:
            if args.listener in json.dumps(item, ensure_ascii=False):
                scope_id = item.get("scope_id") or ""
                break
    if scope_id:
        status, memory = http_json(
            "POST",
            f"{args.powercontext}/v1/memory/search",
            {"scope_id": scope_id, "query": "末班地铁 怪谈 点播", "limit": 10, "mode": "auto"},
        )
        status2, prepared = http_json(
            "POST",
            f"{args.powercontext}/v1/context/prepare",
            {"scope_id": scope_id, "query": "这位听众在深夜电台说过的话、点播过的怪谈和当时的心情", "max_bytes": 2000},
        )
        (out / "04_powercontext_memory.json").write_text(
            json.dumps(
                {
                    "scope_id": scope_id,
                    "listener": args.listener,
                    "search": {"status": status, "body": memory},
                    "prepare": {"status": status2, "body": prepared},
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        print("memory scope:", scope_id, status, status2)

    # ---- seekdb ----
    database = os.environ.get("RADIO_SEEKDB_DATABASE", "radio_archive")
    columns, rows = pymysql_query(
        "select story_id, title, tags, region, left(content, 80) as content_head from radio_archive order by story_id",
        database,
    )
    (out / "05_seekdb_archive.json").write_text(
        json.dumps({"columns": columns, "rows": rows}, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print("archive rows:", len(rows))

    columns, index_rows = pymysql_query(
        "select index_name, index_type, group_concat(column_name order by seq_in_index) as cols "
        "from information_schema.statistics where table_schema = database() and table_name = 'radio_archive' "
        "group by index_name, index_type",
        database,
    )
    (out / "06_seekdb_indexes.json").write_text(
        json.dumps({"columns": columns, "rows": index_rows}, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print("indexes:", index_rows)

    # EXPLAIN 需要一个向量常量（seekdb 不支持在向量索引上用子查询取列）
    _, vector_rows = pymysql_query(
        "select embedding from radio_archive where embedding is not null limit 1",
        database,
    )
    vector_literal = str(vector_rows[0][0]) if vector_rows else "[0.0]"
    explain_sql = (
        "EXPLAIN SELECT * FROM ("
        "SELECT story_id, title, tags, region, content, NULL AS distance, 'fts' AS matched_by FROM radio_archive "
        "WHERE MATCH(searchable_text) AGAINST ('末班地铁' IN NATURAL LANGUAGE MODE) "
        "ORDER BY MATCH(searchable_text) AGAINST ('末班地铁' IN NATURAL LANGUAGE MODE) DESC, story_id LIMIT 2"
        ") AS fts_hits UNION ALL SELECT * FROM ("
        "SELECT story_id, title, tags, region, content, l2_distance(embedding, "
        f"'{vector_literal}') AS distance, 'vector' AS matched_by FROM radio_archive "
        "WHERE embedding IS NOT NULL "
        f"ORDER BY l2_distance(embedding, '{vector_literal}') APPROXIMATE LIMIT 2"
        ") AS vector_hits"
    )
    columns, explain_rows = pymysql_query(explain_sql, database)
    (out / "07_seekdb_explain.txt").write_text(
        "\t".join(columns) + "\n" + "\n".join("\t".join(str(c) for c in r) for r in explain_rows) + "\n",
        encoding="utf-8",
    )
    print("explain rows:", len(explain_rows))

    # ---- 电台后端日志 ----
    logs = subprocess.run(
        ["docker", "logs", "--tail", "200", "dify-deploy-radio-archive-1"],
        capture_output=True, text=True, timeout=60,
    )
    (out / "08_radio_access.log").write_text(logs.stdout + logs.stderr, encoding="utf-8")

    # ---- 摘要 ----
    summary = [
        "# 深夜电台 · 怪谈档案 —— 运行证据摘要",
        "",
        f"- Dify 节点执行记录条数：{len(records)}（见 `01_dify_node_executions.json`）",
        f"- PowerContext Scope 数：{len((scopes or {}).get('items') or []) if isinstance(scopes, dict) else 'n/a'}",
        f"- seekdb 怪谈档案条数：{len(rows)}",
        "- 索引清单：",
    ]
    for row in index_rows:
        summary.append(f"  - `{row[0]}` ({row[1]}) 列：{row[2]}")
    summary.append("")
    summary.append("## 节点执行状态")
    for record in records[:14]:
        summary.append(
            f"- `{record['node_id']}` ({record['node_type']}) — {record['title']} — {record['status']} "
            f"— {record['elapsed_time']}s"
        )
    (out / "09_summary.md").write_text("\n".join(summary) + "\n", encoding="utf-8")
    print("evidence written to", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
