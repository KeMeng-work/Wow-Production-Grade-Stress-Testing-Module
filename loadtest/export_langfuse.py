"""
从 Langfuse 公共 API 导出主模型 generation 记录（用于校准替身 mock_profile.json）

在服务器宿主机运行（读取项目根目录 .env 中的 LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY）：
    python3 export_langfuse.py
固定导出：用户 eval_r3_v1t_all，北京时间 2026-10-04 13:00–17:40（R3 原评估集全量评测），模型 qwen3-30b-a3b
输出：langfuse_generations_eval_r3_v1t_all.json
"""

import argparse
import base64
import json
import os
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent

# 固定导出范围：R3 原评估集全量评测（Langfuse API 使用 UTC，即北京时间 - 8 小时）
USER_ID = "eval_r3_v1t_all"
FROM_UTC = "2026-10-04T05:00:00Z"   # 北京时间 2026-10-04 13:00
TO_UTC = "2026-10-04T09:40:00Z"     # 北京时间 2026-10-04 17:40
MODEL = "qwen3-30b-a3b"


def load_env(path: Path) -> dict:
    env = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.split("#", 1)[0].strip().strip('"').strip("'")
    return env


def get(host, auth, path, params):
    url = f"{host.rstrip('/')}{path}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"Authorization": f"Basic {auth}"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default=None, help="默认读 .env 的 LANGFUSE_HOST，再退回 http://localhost:3000")
    ap.add_argument("--env", default=str(ROOT.parent / ".env"))
    args = ap.parse_args()
    args.user, args.from_ts, args.to_ts, args.model = USER_ID, FROM_UTC, TO_UTC, MODEL
    print(f"导出范围：user={USER_ID}  {FROM_UTC} ~ {TO_UTC}（UTC）  model={MODEL}")

    env = {**load_env(Path(args.env)), **os.environ}
    pk, sk = env.get("LANGFUSE_PUBLIC_KEY"), env.get("LANGFUSE_SECRET_KEY")
    if not pk or not sk:
        raise SystemExit("❌ 未找到 LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY")
    host = args.host or env.get("LANGFUSE_HOST") or "http://localhost:3000"
    if "langfuse-server" in host:          # .env 里是容器内地址时，宿主机改用 localhost
        host = "http://localhost:3000"
    auth = base64.b64encode(f"{pk}:{sk}".encode()).decode()

    rows, page = [], 1
    while True:
        params = {"type": "GENERATION", "userId": args.user, "fromStartTime": args.from_ts,
                  "toStartTime": args.to_ts, "page": page, "limit": 100}
        data = get(host, auth, "/api/public/observations", params)
        batch = data.get("data", [])
        for o in batch:
            if args.model and args.model not in str(o.get("model") or ""):
                continue
            rows.append({
                "id": o.get("id"), "traceId": o.get("traceId"), "name": o.get("name"),
                "model": o.get("model"),
                "startTime": o.get("startTime"), "endTime": o.get("endTime"),
                "completionStartTime": o.get("completionStartTime"),
                "latency": o.get("latency"), "timeToFirstToken": o.get("timeToFirstToken"),
                "usage": o.get("usage"), "usageDetails": o.get("usageDetails"),
                "promptTokens": o.get("promptTokens"), "completionTokens": o.get("completionTokens"),
                "output_preview": str(o.get("output"))[:120],
            })
        total_pages = (data.get("meta") or {}).get("totalPages", page)
        print(f"  page {page}/{total_pages}  累计 {len(rows)} 条")
        if page >= total_pages or not batch:
            break
        page += 1
        time.sleep(0.2)

    out = ROOT / f"langfuse_generations_{args.user}.json"
    out.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    have_ttft = sum(1 for r in rows if r["completionStartTime"] or r["timeToFirstToken"] is not None)
    have_tok = sum(1 for r in rows if r["usage"] or r["usageDetails"] or r["completionTokens"])
    print(f"✅ 导出 {len(rows)} 条 → {out}")
    print(f"   含首字时间 {have_ttft} 条，含 token 用量 {have_tok} 条")


if __name__ == "__main__":
    main()
