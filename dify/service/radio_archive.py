"""深夜电台的档案检索服务（配套后端）。

职责：把 seekdb 里的「怪谈档案库」变成 Dify 工作流可以用的一条 HTTP 接口。

* 全文分支走原生全文索引 ``MATCH ... AGAINST``（索引使用 ``WITH PARSER NGRAM``，
  这样中文词组也能命中；SPACE 解析器在中文下会整句当成一个词，无法检索）；
* 向量分支走原生向量索引 ``l2_distance(...) APPROXIMATE``；
* 结构化过滤（标签）在数据库内完成。

两个分支合并在**同一条** SQL 里（派生表 UNION ALL），每条通道各取自己的 top-k，
并用 ``matched_by`` 标记来源；服务把结果拼成一段可直接喂给 Dify 里 LLM 节点的
摘要文本，同时返回结构化 hits 供界面展示。

依赖：Python 3.12 标准库 + pymysql（MySQL 协议）。不依赖任何 Web 框架。
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pymysql

# --------------------------------------------------------------------------- 配置

SEEKDB_HOST = os.environ.get("RADIO_SEEKDB_HOST", "127.0.0.1")
SEEKDB_PORT = int(os.environ.get("RADIO_SEEKDB_PORT", "2881"))
SEEKDB_USER = os.environ.get("RADIO_SEEKDB_USER", "root")
SEEKDB_PASSWORD = os.environ.get("RADIO_SEEKDB_PASSWORD", "")
SEEKDB_DATABASE = os.environ.get("RADIO_SEEKDB_DATABASE", "radio_archive")
TABLE = os.environ.get("RADIO_ARCHIVE_TABLE", "radio_archive")

EMBEDDING_BASE = os.environ.get(
    "RADIO_EMBEDDING_API_BASE", "https://api.minimaxi.com/v1"
).rstrip("/")
EMBEDDING_KEY = os.environ.get("RADIO_EMBEDDING_API_KEY", "")
EMBEDDING_MODEL = os.environ.get("RADIO_EMBEDDING_MODEL", "embo-01")

HTTP_HOST = os.environ.get("RADIO_HTTP_HOST", "0.0.0.0")
HTTP_PORT = int(os.environ.get("RADIO_HTTP_PORT", "8801"))

#: 送进全文索引的查询需要去掉全文检索的运算符字符，否则会被当成语法。
_FTS_OPERATORS = re.compile(r"[+\-~*<>\"()@:^,;!?'\\]")


def sanitize_fts_query(value: str) -> str:
    """清理全文检索查询：只保留可被 NGRAM 切分的普通词字符。"""
    cleaned = _FTS_OPERATORS.sub(" ", value or "")
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned[:200]


#: 检索 SQL：全文 + 向量 + 标签过滤，同一条语句。
_SEARCH_SQL = """
SELECT * FROM (
    SELECT
        story_id,
        title,
        tags,
        region,
        content,
        NULL AS distance,
        'fts' AS matched_by
    FROM {table}
    WHERE MATCH(searchable_text) AGAINST (%(query)s IN NATURAL LANGUAGE MODE)
      AND (%(tag)s = '' OR FIND_IN_SET(%(tag)s, tags) > 0)
    ORDER BY MATCH(searchable_text) AGAINST (%(query)s IN NATURAL LANGUAGE MODE) DESC,
             story_id
    LIMIT %(limit)s
) AS fts_hits
UNION ALL
SELECT * FROM (
    SELECT
        story_id,
        title,
        tags,
        region,
        content,
        l2_distance(embedding, %(vector)s) AS distance,
        'vector' AS matched_by
    FROM {table}
    WHERE embedding IS NOT NULL
      AND (%(tag)s = '' OR FIND_IN_SET(%(tag)s, tags) > 0)
    ORDER BY l2_distance(embedding, %(vector)s) APPROXIMATE
    LIMIT %(limit)s
) AS vector_hits
""".format(table=TABLE)

#: 只走全文分支（向量不可用时）。
_FTS_ONLY_SQL = _SEARCH_SQL.split("UNION ALL")[0]


# --------------------------------------------------------------------------- 数据库


def _connect() -> pymysql.connections.Connection:
    return pymysql.connect(
        host=SEEKDB_HOST,
        port=SEEKDB_PORT,
        user=SEEKDB_USER,
        password=SEEKDB_PASSWORD,
        database=SEEKDB_DATABASE,
        charset="utf8mb4",
        autocommit=True,
        connect_timeout=20,
    )


def archive_count() -> int:
    with _connect() as connection, connection.cursor() as cursor:
        cursor.execute(f"SELECT COUNT(*) FROM {TABLE}")
        row = cursor.fetchone()
        return int(row[0]) if row else 0


# --------------------------------------------------------------------------- 向量


def embed(texts: list[str], kind: str) -> list[list[float]] | None:
    """调用 MiniMax 的 embedding 接口；不可用时返回 None（退化为纯全文检索）。"""
    if not EMBEDDING_KEY or not texts:
        return None
    payload = {"model": EMBEDDING_MODEL, "type": kind, "texts": texts}
    request = urllib.request.Request(
        f"{EMBEDDING_BASE}/embeddings",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {EMBEDDING_KEY}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            body = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ValueError):
        return None
    vectors = body.get("vectors")
    if not isinstance(vectors, list) or not vectors:
        return None
    return [[float(value) for value in vector] for vector in vectors]


def _format_vector(values: list[float]) -> str:
    """MySQL 协议里 VECTOR 字面量的写法：'[0.1,0.2,...]'。"""
    return "[" + ",".join(f"{value:.6f}" for value in values) + "]"


# --------------------------------------------------------------------------- 检索


def search(query: str, top_k: int = 3, tag: str = "") -> dict[str, Any]:
    """检索怪谈档案：全文 + 向量 + 标签过滤，同一条 SQL。"""
    started = time.perf_counter()
    fts_query = sanitize_fts_query(query)
    vectors = embed([query], "query") if fts_query else None
    engine = "seekdb:fulltext+vector" if vectors else "seekdb:fulltext"

    parameters: dict[str, Any] = {
        "query": fts_query,
        "tag": tag,
        "limit": int(top_k),
        "vector": _format_vector(vectors[0]) if vectors else "[0]",
    }
    sql = _SEARCH_SQL if vectors else _FTS_ONLY_SQL

    hits: list[dict[str, Any]] = []
    seen: set[str] = set()
    if fts_query:
        with _connect() as connection, connection.cursor() as cursor:
            cursor.execute(sql, parameters)
            rows = cursor.fetchall()
        for story_id, title, tags, region, content, distance, matched_by in rows:
            if story_id in seen:
                continue
            seen.add(story_id)
            hits.append(
                {
                    "story_id": story_id,
                    "title": title,
                    "tags": [item for item in (tags or "").split(",") if item],
                    "region": region,
                    "snippet": (content or "")[:160],
                    "content": content,
                    "distance": None if distance is None else float(distance),
                    "matched_by": matched_by,
                }
            )

    summary_lines = []
    for hit in hits:
        channels = "+".join(hit["matched_by"].split(","))
        summary_lines.append(
            f"《{hit['title']}》〔{hit['region']}·{'/'.join(hit['tags'])}｜命中通道:{channels}〕\n{hit['snippet']}"
        )
    summary = (
        "\n\n".join(summary_lines)
        if summary_lines
        else "（档案库里没有匹配到相关怪谈，主播可以自由发挥，但要说明这是即兴发挥。）"
    )

    return {
        "summary": summary,
        "hits": hits,
        "engine": engine,
        "count": len(hits),
        "query": query,
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
    }


# --------------------------------------------------------------------------- HTTP


class _Handler(BaseHTTPRequestHandler):
    server_version = "RadioArchive/1.0"

    def _reply(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _reply_text(self, status: int, text: str) -> None:
        body = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler 约定
        path = self.path.split("?", 1)[0].rstrip("/")
        if path in ("/healthz", "/health"):
            try:
                self._reply(200, {"status": "ok", "archive_count": archive_count()})
            except Exception as error:  # noqa: BLE001
                self._reply(503, {"status": "error", "detail": str(error)})
            return
        if path == "/archive/search":
            # 方便人工排查：GET /archive/search?query=...&top_k=2&tag=...
            from urllib.parse import parse_qs, urlsplit

            params = parse_qs(urlsplit(self.path).query)
            query = (params.get("query") or [""])[0].strip()
            if not query:
                self._reply(400, {"status": "bad_request", "detail": "缺少 query"})
                return
            try:
                result = search(
                    query,
                    top_k=int((params.get("top_k") or ["3"])[0]),
                    tag=(params.get("tag") or [""])[0],
                )
            except Exception as error:  # noqa: BLE001
                self._reply(500, {"status": "error", "detail": str(error)})
                return
            self._reply(200, result)
            return
        self._reply(404, {"status": "not_found"})

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler 约定
        path = self.path.split("?", 1)[0].rstrip("/")
        if path != "/archive/search":
            self._reply(404, {"status": "not_found"})
            return
        length = int(self.headers.get("Content-Length") or 0)
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
        except ValueError:
            self._reply(400, {"status": "bad_request", "detail": "body 不是合法 JSON"})
            return
        query = str(payload.get("query") or "").strip()
        if not query:
            self._reply(400, {"status": "bad_request", "detail": "缺少 query"})
            return
        try:
            result = search(
                query,
                top_k=int(payload.get("top_k") or 3),
                tag=str(payload.get("tag") or ""),
            )
        except Exception as error:  # noqa: BLE001
            self._reply(500, {"status": "error", "detail": str(error)})
            return
        result["listener"] = payload.get("listener") or ""
        if "format=text" in self.path:
            # 纯文本模式：Dify 的 HTTP 节点直接把它塞进 LLM 提示词
            self._reply_text(200, result["summary"])
            return
        self._reply(200, result)

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"[radio-archive] {self.address_string()} {fmt % args}", flush=True)


def main() -> None:
    server = ThreadingHTTPServer((HTTP_HOST, HTTP_PORT), _Handler)
    print(f"[radio-archive] 监听 http://{HTTP_HOST}:{HTTP_PORT}", flush=True)
    print(
        f"[radio-archive] seekdb={SEEKDB_HOST}:{SEEKDB_PORT}/{SEEKDB_DATABASE} "
        f"table={TABLE} embedding={'on' if EMBEDDING_KEY else 'off'}",
        flush=True,
    )
    server.serve_forever()


if __name__ == "__main__":
    main()
