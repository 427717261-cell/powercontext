"""初始化「怪谈档案库」：在 seekdb 上建表、建索引、灌入数据与向量。

用法::

    export RADIO_SEEKDB_HOST=127.0.0.1
    export RADIO_SEEKDB_PORT=2881
    export RADIO_SEEKDB_USER=radio
    export RADIO_SEEKDB_PASSWORD=***
    export RADIO_SEEKDB_DATABASE=radio_archive
    export RADIO_EMBEDDING_API_KEY=***
    python init_archive.py

脚本是幂等的：重复执行会重建表并重新灌数据（先 DROP 后 CREATE）。

全文索引使用 ``WITH PARSER NGRAM``：档案正文是中文，SPACE 解析器会把整句当成
一个词导致检索永远命中不了，NGRAM 才能按中文 n-gram 命中。
"""

from __future__ import annotations

import json
import os
import pathlib
import sys
import urllib.error
import urllib.request

import pymysql

ROOT = pathlib.Path(__file__).resolve().parents[1]
ARCHIVE = ROOT / "data" / "archive.json"

HOST = os.environ.get("RADIO_SEEKDB_HOST", "127.0.0.1")
PORT = int(os.environ.get("RADIO_SEEKDB_PORT", "2881"))
USER = os.environ.get("RADIO_SEEKDB_USER", "root")
PASSWORD = os.environ.get("RADIO_SEEKDB_PASSWORD", "")
DATABASE = os.environ.get("RADIO_SEEKDB_DATABASE", "radio_archive")
TABLE = os.environ.get("RADIO_ARCHIVE_TABLE", "radio_archive")

EMBEDDING_BASE = os.environ.get(
    "RADIO_EMBEDDING_API_BASE", "https://api.minimaxi.com/v1"
).rstrip("/")
EMBEDDING_KEY = os.environ.get("RADIO_EMBEDDING_API_KEY", "")
EMBEDDING_MODEL = os.environ.get("RADIO_EMBEDDING_MODEL", "embo-01")


def connect(database: str | None = DATABASE):
    return pymysql.connect(
        host=HOST,
        port=PORT,
        user=USER,
        password=PASSWORD,
        database=database,
        charset="utf8mb4",
        autocommit=True,
        connect_timeout=20,
    )


def embed(texts: list[str], kind: str) -> list[list[float]]:
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
    with urllib.request.urlopen(request, timeout=120) as response:
        body = json.loads(response.read().decode("utf-8"))
    vectors = body.get("vectors") or []
    if not vectors:
        raise RuntimeError(f"embedding 接口没有返回向量: {body}")
    return [[float(value) for value in vector] for vector in vectors]


def format_vector(values: list[float]) -> str:
    return "[" + ",".join(f"{value:.6f}" for value in values) + "]"


def main() -> int:
    if not EMBEDDING_KEY:
        print("缺少 RADIO_EMBEDDING_API_KEY，无法生成向量", file=sys.stderr)
        return 1

    data = json.loads(ARCHIVE.read_text(encoding="utf-8"))
    stories = data["stories"]
    print(f"档案条目：{len(stories)} 条")

    # 1. 确保数据库存在（账号没有建库权限时，要求 DBA 预先建好）
    try:
        with connect(None) as connection, connection.cursor() as cursor:
            cursor.execute(
                f"CREATE DATABASE IF NOT EXISTS `{DATABASE}` "
                "CHARACTER SET utf8mb4 COLLATE utf8mb4_bin"
            )
        print(f"数据库 {DATABASE} 就绪")
    except Exception as error:  # noqa: BLE001
        print(f"跳过建库（账号可能无建库权限，请确认 {DATABASE} 已存在）：{error}")

    # 2. 生成向量
    texts = [f"{story['title']}。{story['content']}" for story in stories]
    vectors = embed(texts, "db")
    dimension = len(vectors[0])
    print(f"向量维度：{dimension}")

    # 3. 建表 + 索引
    ddl = [
        f"DROP TABLE IF EXISTS `{TABLE}`",
        f"""
        CREATE TABLE `{TABLE}` (
            `story_id` VARCHAR(64) NOT NULL,
            `title` VARCHAR(200) NOT NULL,
            `tags` VARCHAR(255) NOT NULL DEFAULT '',
            `region` VARCHAR(64) NOT NULL DEFAULT '',
            `content` MEDIUMTEXT NOT NULL,
            `searchable_text` MEDIUMTEXT NOT NULL,
            `embedding` VECTOR({dimension}) NULL,
            PRIMARY KEY (`story_id`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin
        """,
        # 中文全文检索必须用 NGRAM 解析器
        f"CREATE FULLTEXT INDEX `ft_{TABLE}` ON `{TABLE}` (`searchable_text`) WITH PARSER NGRAM",
        f"CREATE VECTOR INDEX `idx_{TABLE}_embedding` ON `{TABLE}` (`embedding`) WITH (distance=l2, type=hnsw)",
    ]
    with connect() as connection, connection.cursor() as cursor:
        for statement in ddl:
            if statement.strip():
                cursor.execute(statement)
    print(f"表 {TABLE} 与全文(NGRAM)/向量索引就绪")

    # 4. 灌数据
    rows = []
    for story, vector in zip(stories, vectors):
        searchable = " ".join(
            [story["title"], story["region"], " ".join(story["tags"]), story["content"]]
        )
        rows.append(
            (
                story["story_id"],
                story["title"],
                ",".join(story["tags"]),
                story["region"],
                story["content"],
                searchable,
                format_vector(vector),
            )
        )
    with connect() as connection, connection.cursor() as cursor:
        cursor.executemany(
            f"INSERT INTO `{TABLE}` "
            "(story_id, title, tags, region, content, searchable_text, embedding) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s)",
            rows,
        )
        cursor.execute(f"SELECT COUNT(*) FROM `{TABLE}`")
        total = cursor.fetchone()[0]
    print(f"已写入 {total} 条怪谈档案")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
