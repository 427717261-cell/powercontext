#!/usr/bin/env python3
"""根据 Dify 官方 .env.example 生成一份可直接使用的 .env。

用法：python3 make_dify_env.py <Dify 的 docker 目录>

生成的 .env 里口令是随机的；作品自身的凭据（seekdb 账号、Embedding Key）
由 install_dify.sh 的提示追加，真实值不进入版本库。
"""

from __future__ import annotations

import base64
import secrets
import sys
from pathlib import Path

EXTRA = {
    # Postgres / Redis / Celery 三处口令保持一致
    "DB_PASSWORD": None,
    "POSTGRES_PASSWORD": None,
    "REDIS_PASSWORD": None,
    "CELERY_BROKER_URL": None,
    # 向量库：复用 db(Postgres) 上的 pgvector，省掉独立向量库容器
    "VECTOR_STORE": "pgvector",
    "PGVECTOR_HOST": "db",
    "PGVECTOR_PORT": "5432",
    "PGVECTOR_USER": "postgres",
    "PGVECTOR_PASSWORD": None,
    "PGVECTOR_DATABASE": "dify",
    "PGVECTOR_MIN_CONNECTION": "1",
    "PGVECTOR_MAX_CONNECTION": "3",
    "PGVECTOR_PG_BIGM": "false",
    # 低规格机器上收紧并发
    "SERVER_WORKER_AMOUNT": "1",
    "CELERY_WORKER_AMOUNT": "1",
    "CELERY_AUTO_SCALE": "false",
    # 对外访问地址：默认走 nginx 同源相对路径
    "CONSOLE_API_URL": "",
    "APP_API_URL": "",
    "SERVICE_API_URL": "",
    "EXPOSE_NGINX_PORT": "80",
    "EXPOSE_API_PORT": "5001",
    "EXPOSE_WEB_PORT": "3000",
    "NGINX_PORT": "80",
    "LOG_LEVEL": "INFO",
    "PIP_MIRROR_URL": "https://pypi.tuna.tsinghua.edu.cn/simple",
    # 沙箱鉴权：api/worker 用 CODE_EXECUTION_API_KEY，sandbox 用 SANDBOX_API_KEY，
    # 两个变量互相独立，必须设置成同一个值，否则 code 节点会收到 401。
    "SANDBOX_API_KEY": None,
    "CODE_EXECUTION_API_KEY": None,
}


def rnd(n: int) -> str:
    return base64.urlsafe_b64encode(secrets.token_bytes(n)).decode().rstrip("=")[:n]


def main() -> int:
    if len(sys.argv) < 2:
        print("用法: python3 make_dify_env.py <Dify 的 docker 目录>", file=sys.stderr)
        return 2
    base = Path(sys.argv[1]).resolve()
    src = base / ".env.example"
    if not src.is_file():
        print(f"找不到 {src}", file=sys.stderr)
        return 2

    db_pw = rnd(20)
    redis_pw = rnd(20)
    settings = dict(EXTRA)
    settings["DB_PASSWORD"] = db_pw
    settings["POSTGRES_PASSWORD"] = db_pw
    settings["REDIS_PASSWORD"] = redis_pw
    settings["CELERY_BROKER_URL"] = f"redis://:{redis_pw}@redis:6379/1"
    settings["PGVECTOR_PASSWORD"] = db_pw
    sandbox_key = rnd(32)
    settings["SANDBOX_API_KEY"] = sandbox_key
    settings["CODE_EXECUTION_API_KEY"] = sandbox_key

    text = src.read_text(encoding="utf-8")
    out_lines: list[str] = []
    seen: set[str] = set()
    for line in text.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.split("=", 1)[0]
            if key in settings:
                out_lines.append(f"{key}={settings[key]}")
                seen.add(key)
                continue
        out_lines.append(line)
    for key, value in settings.items():
        if key not in seen:
            out_lines.append(f"{key}={value}")

    dst = base / ".env"
    dst.write_bytes(("\n".join(out_lines) + "\n").encode("utf-8"))
    print(f"generated {dst}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
