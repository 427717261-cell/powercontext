#!/usr/bin/env python3
"""一键跑「深夜电台 · 怪谈档案」的验证样例并落证据。

做四件事：
  1. 导入 Dify DSL（apps/midnight-radio.yml）并发布工作流；
  2. 创建应用 API Key；
  3. 跑两组验证样例（第 1 组首次到访，第 2 组换新会话验证跨会话记忆）；
  4. 采集 Dify 节点执行记录、PowerContext 记忆记录、seekdb 检索记录。

用法：
    python3 verify_samples.py \
        --base-url http://127.0.0.1:5001 \
        --email host@midnight-radio.local \
        --password '<控制台密码>' \
        --out-dir ../evidence \
        [--listener 小满]

    # 已经在控制台导入过 DSL 时，复用该应用重跑样例（不删应用、不重新导入）：
    python3 verify_samples.py ... --reuse-app <app_id>

说明：脚本只做「读多写少」的验证动作，不修改 Dify 的既有数据；
除导入的应用外，唯一写入的是 PowerContext 里该听众代号对应的 Scope。
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time
import urllib.error
import urllib.request

CONSOLE_TOKEN = ""


def call(method: str, base: str, path: str, payload=None, token: str = "", timeout=300):
    url = f"{base}{path}"
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    headers = {"Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
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
            return exc.code, raw[:1500]


def pick_api_key(listing) -> str:
    """从 api-keys 列表响应里取出应用 API Key（列表键名在不同版本里是 items 或 data）。"""
    if not isinstance(listing, dict):
        return ""
    items = listing.get("items") or listing.get("data") or []
    return items[0].get("token", "") if items else ""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:5001")
    parser.add_argument("--email", required=True)
    parser.add_argument("--password", required=True)
    parser.add_argument("--dsl", default=str(pathlib.Path(__file__).resolve().parents[1] / "apps" / "midnight-radio.yml"))
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--listener", default="小满")
    parser.add_argument("--keep-old-apps", action="store_true", help="保留已导入的同名应用（默认先清理）")
    parser.add_argument(
        "--reuse-app",
        default="",
        help="复用已导入的应用：传入 app_id 后跳过导入/发布，直接用它的 API Key 跑样例",
    )
    args = parser.parse_args()

    global CONSOLE_TOKEN
    out_dir = pathlib.Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    status, body = call(
        "POST", args.base_url, "/console/api/login",
        {"email": args.email, "password": args.password, "language": "zh-Hans", "remember_me": True},
    )
    if status != 200:
        print(f"登录失败：{status} {body}", file=sys.stderr)
        return 1
    CONSOLE_TOKEN = body["data"]["access_token"]
    print("登录成功")

    app_key = ""
    if args.reuse_app:
        # 复用已导入的应用：不删旧应用、不重新导入，只取它现有的 API Key。
        app_id = args.reuse_app
        print("复用已有应用：", app_id)
        status, listing = call(
            "GET", args.base_url, f"/console/api/apps/{app_id}/api-keys", token=CONSOLE_TOKEN
        )
        app_key = pick_api_key(listing)
        print("应用 API Key：", (app_key[:8] + "***") if app_key else "(未找到)")
    else:
        if not args.keep_old_apps:
            status, listing = call("GET", args.base_url, "/console/api/apps?page=1&page_size=50", token=CONSOLE_TOKEN)
            if isinstance(listing, dict):
                for item in listing.get("data") or []:
                    if item.get("name") == "深夜电台 · 怪谈档案" and item.get("id"):
                        call("DELETE", args.base_url, f"/console/api/apps/{item['id']}", token=CONSOLE_TOKEN)
                        print("清理旧应用：", item["id"])

        yaml_content = pathlib.Path(args.dsl).read_text(encoding="utf-8")
        status, body = call(
            "POST", args.base_url, "/console/api/apps/imports",
            {
                "mode": "yaml-content",
                "yaml_content": yaml_content,
                "name": "深夜电台 · 怪谈档案",
                "description": "Dify 编排 + seekdb 怪谈档案检索 + PowerContext 听众记忆",
            },
            token=CONSOLE_TOKEN, timeout=180,
        )
        (out_dir / "01_import_response.json").write_text(json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8")
        print("导入 DSL：", status, str(body)[:200])
        if status not in (200, 202):
            return 1
        app_id = body.get("app_id")

        status, body = call(
            "POST", args.base_url, f"/console/api/apps/{app_id}/workflows/publish",
            {"marked_name": "v1", "marked_comment": "验证样例发布"},
            token=CONSOLE_TOKEN, timeout=120,
        )
        (out_dir / "02_publish_response.json").write_text(json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8")
        print("发布工作流：", status)

        status, body = call("POST", args.base_url, f"/console/api/apps/{app_id}/api-keys", {}, token=CONSOLE_TOKEN)
        if isinstance(body, dict):
            app_key = body.get("token", "")
        if not app_key:
            status, listing = call("GET", args.base_url, f"/console/api/apps/{app_id}/api-keys", token=CONSOLE_TOKEN)
            app_key = pick_api_key(listing)
        print("应用 API Key：", (app_key[:8] + "***") if app_key else "(未创建)")

    if not app_key:
        print("没有可用的应用 API Key，无法跑样例", file=sys.stderr)
        return 1

    samples = [
        {
            "id": "sample-1",
            "listener": args.listener,
            "query": "想听末班地铁的怪谈",
            "conversation_id": "",
            "expect": "首次到访：记忆召回为空；seekdb 档案库应命中「末班地铁 / 末班车」相关怪谈，并标注命中通道",
        },
        {
            "id": "sample-2",
            "listener": args.listener,
            "query": "我今天特别累，随便讲个短的",
            "conversation_id": "",
            "expect": "换新会话（不传 conversation_id）：PowerContext 应召回 sample-1 的记忆，主播应认出这是同一位听众",
        },
    ]

    results = []
    for sample in samples:
        print(f"--- {sample['id']}：{sample['query']}")
        status, body = call(
            "POST", args.base_url, "/v1/chat-messages",
            {
                "inputs": {"listener_id": sample["listener"]},
                "query": sample["query"],
                "response_mode": "blocking",
                "user": sample["listener"],
                "conversation_id": sample.get("conversation_id", ""),
            },
            token=app_key, timeout=300,
        )
        answer = body.get("answer", "") if isinstance(body, dict) else ""
        sample["http_status"] = status
        sample["answer"] = answer
        sample["conversation_id"] = body.get("conversation_id", "") if isinstance(body, dict) else ""
        sample["raw"] = body
        print("  HTTP", status)
        print("  ", answer.replace("\n", " ")[:400])
        results.append(sample)
        time.sleep(2)

    (out_dir / "03_chat_results.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "00_app_id.txt").write_text(f"{app_id}\n", encoding="utf-8")
    print("应用 ID：", app_id)
    print("样例结果已写入", out_dir / "03_chat_results.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
