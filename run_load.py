"""
压测客户端 —— 多轮会话负载（闭环）

每个"会话"模拟一个用户：固定 session_id / user_id，连续提问 1–N 轮（同一主题优先），
两轮之间随机思考时间；会话结束后立即开启新会话，始终保持 --sessions 个会话同时在线。
逐个请求记录：首个事件（TTFE）、首个答案字（TTFT）、端到端（E2E）、平均出字间隔（ITL）、状态与错误类型。

运行（服务器宿主机）：
    python run_load.py --sessions 10 --duration 300 --tag baseline_s10
    python run_load.py --sessions 10 --duration 240 --mode single --tag single_s10   # 单轮对照
输出：results/<tag>/requests.jsonl（逐请求明细）+ summary.json（汇总）
"""

import argparse
import asyncio
import json
import random
import statistics
import time
import uuid
from collections import defaultdict
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent
PROJECT = ROOT.parent
DEFAULT_POOLS = [
    PROJECT / "data/evaluation/ragas_input_batch_2_deduped.json",   # R3 原评估集 185 条
    PROJECT / "data/evaluation/ragas_input_batch_50_deduped.json",  # R3 合成数据 60 条
]


# ── 题库 ─────────────────────────────────────────────────────────────────────
def load_pool(paths):
    by_topic = defaultdict(list)
    allq = []
    for p in paths:
        p = Path(p)
        if not p.exists():
            print(f"⚠️  题库文件不存在，跳过：{p}")
            continue
        for x in json.loads(p.read_text(encoding="utf-8")):
            q = {"qid": f"{p.stem}:{x['id']}", "question": x["question"], "topic": x.get("topic", "")}
            by_topic[q["topic"]].append(q)
            allq.append(q)
    if not allq:
        raise SystemExit("❌ 题库为空")
    return by_topic, allq


def pick_session_questions(by_topic, allq, n_turns, rng):
    """同一会话优先抽同一主题的题；不够时用随机题补齐"""
    topic = rng.choice(list(by_topic))
    qs = rng.sample(by_topic[topic], min(n_turns, len(by_topic[topic])))
    while len(qs) < n_turns:
        qs.append(rng.choice(allq))
    return qs


# ── 单个请求 ──────────────────────────────────────────────────────────────────
async def one_request(client, url, question, session_id, user_id, timeout):
    rec = {"t_send": time.time(), "status": "ok", "http": None, "error": None,
           "ttfe": None, "ttft": None, "e2e": None, "itl": None,
           "answer_chars": 0, "answer_chunks": 0, "tools": [], "retrieval_meta": None}
    t0 = time.perf_counter()
    last_answer_t = None
    gaps = []
    done = False
    try:
        async with client.stream("POST", url, timeout=timeout,
                                 json={"user_input": question, "session_id": session_id, "user_id": user_id},
                                 headers={"Accept": "text/event-stream"}) as resp:
            rec["http"] = resp.status_code
            if resp.status_code != 200:
                rec["status"] = f"http_{resp.status_code}"
                await resp.aread()
                rec["e2e"] = time.perf_counter() - t0
                return rec
            async for line in resp.aiter_lines():
                if not line.startswith("data:"):
                    continue
                now = time.perf_counter()
                if rec["ttfe"] is None:
                    rec["ttfe"] = now - t0
                try:
                    ev = json.loads(line[5:].strip())
                except json.JSONDecodeError:
                    continue
                et = ev.get("type")
                if et == "answer":
                    if rec["ttft"] is None:
                        rec["ttft"] = now - t0
                    if last_answer_t is not None:
                        gaps.append(now - last_answer_t)
                    last_answer_t = now
                    rec["answer_chunks"] += 1
                    rec["answer_chars"] += len(ev.get("content", ""))
                elif et == "tool_call" and ev.get("status") == "start":
                    rec["tools"].append(ev.get("name"))
                elif et == "retrieval_meta":
                    rec["retrieval_meta"] = {k: v for k, v in ev.items() if k != "type"}
                elif et == "error":
                    # reason 区分准入拒绝（503 admission）、请求截止（504 deadline）与熔断（503 无 reason）
                    rec["status"] = f"error_{ev.get('code')}" + (f"_{ev['reason']}" if ev.get("reason") else "")
                    rec["error"] = str(ev.get("detail", ""))[:200]
                elif et == "done":
                    done = True
        rec["e2e"] = time.perf_counter() - t0
        if rec["status"] == "ok":
            if not done:
                rec["status"] = "incomplete"
            elif rec["answer_chars"] == 0:
                rec["status"] = "empty_answer"
    except httpx.TimeoutException:
        rec["status"], rec["e2e"] = "timeout", time.perf_counter() - t0
    except Exception as e:  # 连接失败等
        rec["status"], rec["error"], rec["e2e"] = "client_error", f"{type(e).__name__}: {e}"[:200], time.perf_counter() - t0
    if gaps:
        rec["itl"] = statistics.mean(gaps)
    return rec


# ── 一个会话（1–N 轮）──────────────────────────────────────────────────────────
async def run_session(wid, n, args, client, url, by_topic, allq, t_end, out, rng, active=lambda: True, phase_of=None):
    """跑完一个会话；时长结束或（突增模式下）本 worker 不再处于激活档位时，不再发起下一轮"""
    sid = f"lt_{args.tag}_{wid}_{n}_{uuid.uuid4().hex[:6]}"
    uid = f"lt_{args.tag}_{wid}_{n}"
    turns = 1 if args.mode == "single" else rng.randint(args.min_turns, args.max_turns)
    qs = pick_session_questions(by_topic, allq, turns, rng)
    for turn, q in enumerate(qs, start=1):
        if time.time() >= t_end or not active():
            return
        rec = await one_request(client, url, q["question"], sid, uid, args.timeout)
        rec.update({"tag": args.tag, "worker": wid, "session": sid, "turn": turn, "turns_planned": turns,
                    "qid": q["qid"], "topic": q["topic"]})
        if phase_of is not None:
            rec["phase"] = phase_of(rec["t_send"])
        out.append(rec)
        if args.mode == "multi" and turn < turns:
            await asyncio.sleep(min(rng.expovariate(1.0 / args.think_mean), args.think_mean * 4))


# ── 会话 worker（闭环）────────────────────────────────────────────────────────
async def session_worker(wid, args, client, url, by_topic, allq, t_end, out, rng, active=lambda: True, phase_of=None):
    """闭环：一个会话结束后立即开始下一个；突增模式下 active() 为 False 时空转等待"""
    n = 0
    while time.time() < t_end:
        if not active():
            await asyncio.sleep(0.5)
            continue
        n += 1
        await run_session(wid, n, args, client, url, by_topic, allq, t_end, out, rng, active, phase_of)


# ── 开环：会话按泊松过程到达（恒定速率）────────────────────────────────────────
async def open_loop(args, client, url, by_topic, allq, t_start, t_end, out):
    """
    会话到达速率固定为 --arrival-rate（个/秒，指数分布间隔），与系统处理快慢无关；
    系统变慢时在途请求会堆积，用于检验"给定流量下能否满足 SLO"。每个会话内部仍是多轮 + 思考时间。
    返回已创建的会话任务列表。
    """
    rng = random.Random(args.seed or 0)
    tasks = []
    n = 0
    next_t = t_start
    while True:
        next_t += rng.expovariate(args.arrival_rate)
        if next_t >= t_end:
            break
        await asyncio.sleep(max(0.0, next_t - time.time()))
        n += 1
        srng = random.Random((args.seed or 0) * 100000 + n)
        tasks.append(asyncio.create_task(run_session(n, 1, args, client, url, by_topic, allq, t_end, out, srng)))
    return tasks


# ── 汇总 ──────────────────────────────────────────────────────────────────────
def pct(vals, p):
    vals = sorted(v for v in vals if v is not None)
    if not vals:
        return None
    k = (len(vals) - 1) * p / 100
    f, c = int(k), min(int(k) + 1, len(vals) - 1)
    return round(vals[f] + (vals[c] - vals[f]) * (k - f), 3)


# ── SLO（2026-10-07 定稿）────────────────────────────────────────────────────
# 依据：无负载（1 会话）P95 为 TTFE 4.1 / TTFT 5.7 / E2E 7.5 秒；越靠近用户感知的指标倍数越小
# （TTFE ×1.2、TTFT ×1.4、E2E ×1.6），TTFE / TTFT 不超过 10 秒注意力上限（Nielsen）
SLO_DEFAULT = {"ttfe": 5.0, "ttft": 8.0, "e2e": 12.0, "error_rate": 0.01}
ADMISSION_STATUS = "error_503_admission"


def slo_eval(recs, wall, slo=None):
    """
    SLO 评估：
      - 达标请求：status=ok 且 TTFE / TTFT / E2E 均不超过目标（检索层拒答属业务行为，按 ok 计）
      - attainment = 达标请求 / 全部请求；goodput = 达标请求 / 墙钟秒
      - error_rate 不含准入拒绝（503 admission 单独统计，属主动保护而非故障）
      - met = 三项 P95 与错误率全部达标
    """
    slo = slo or SLO_DEFAULT
    n = len(recs)
    ok = [r for r in recs if r["status"] == "ok"]
    admission = sum(1 for r in recs if r["status"] == ADMISSION_STATUS)
    served = n - admission
    errors = served - len(ok)
    keys = ("ttfe", "ttft", "e2e")
    good = sum(1 for r in ok if all(r.get(k) is not None and r[k] <= slo[k] for k in keys))
    p95 = {k: pct([r.get(k) for r in ok], 95) for k in keys}
    err_rate = round(errors / served, 4) if served else None
    checks = {k: p95[k] is not None and p95[k] <= slo[k] for k in keys}
    checks["error_rate"] = err_rate is not None and err_rate <= slo["error_rate"]
    return {
        "targets": slo, "p95": p95, "error_rate": err_rate,
        "admission_rejected": admission, "admission_rejected_ratio": round(admission / n, 4) if n else None,
        "good": good, "attainment": round(good / n, 4) if n else None,
        "goodput_rps": round(good / wall, 3) if wall else None,
        "checks": checks, "met": all(checks.values()),
    }


def summarize(recs, args, wall):
    ok = [r for r in recs if r["status"] == "ok"]
    status = defaultdict(int)
    for r in recs:
        status[r["status"]] += 1
    n = len(recs)
    s = {
        "tag": args.tag, "mode": args.mode, "sessions": args.sessions, "duration_s": args.duration,
        "wall_s": round(wall, 1), "requests": n, "ok": len(ok),
        "throughput_rps": round(len(ok) / wall, 3) if wall else None,
        "error_rate": round((n - len(ok)) / n, 4) if n else None,
        "status_counts": dict(status),
        "p99_reliable": len(ok) >= 500,
    }
    for key in ("ttfe", "ttft", "e2e", "itl"):
        vals = [r[key] for r in ok]
        s[key] = {"p50": pct(vals, 50), "p95": pct(vals, 95), "p99": pct(vals, 99),
                  "max": round(max(v for v in vals if v is not None), 3) if any(v is not None for v in vals) else None,
                  "n": sum(1 for v in vals if v is not None)}
    refused = sum(1 for r in ok if (r.get("retrieval_meta") or {}).get("refused"))
    s["refused_ratio"] = round(refused / len(ok), 4) if ok else None
    s["turn_dist"] = dict(sorted(defaultdict(int, {t: sum(1 for r in recs if r["turn"] == t) for t in {r["turn"] for r in recs}}).items()))
    s["slo"] = slo_eval(recs, wall, {"ttfe": args.slo_ttfe, "ttft": args.slo_ttft, "e2e": args.slo_e2e,
                                     "error_rate": args.slo_error_rate})
    s["load"] = args.load
    if args.load == "open":
        s["arrival_rate_sessions_per_s"] = args.arrival_rate
        s["offered_rps"] = round(n / args.duration, 3) if args.duration else None   # 实际发起的请求速率
    if args.load == "spike":
        s["phases"] = phase_summaries(recs, args)
    return s


def parse_phases(text):
    """'10:120,60:120,10:120' → [(10, 120), (60, 120), (10, 120)]（会话数:秒）"""
    out = []
    for part in text.split(","):
        k, v = part.split(":")
        out.append((int(k), int(v)))
    return out


def phase_summaries(recs, args):
    """突增模式：按请求发出时所处的阶段分别统计（墙钟 = 该阶段时长）"""
    res = []
    for i, (level, secs) in enumerate(parse_phases(args.phases)):
        sub = [r for r in recs if r.get("phase") == i]
        ok = [r for r in sub if r["status"] == "ok"]
        st = defaultdict(int)
        for r in sub:
            st[r["status"]] += 1
        res.append({
            "phase": i, "sessions": level, "seconds": secs, "requests": len(sub), "ok": len(ok),
            "throughput_rps": round(len(ok) / secs, 3),
            "status_counts": dict(st),
            "ttft": {"p50": pct([r["ttft"] for r in ok], 50), "p95": pct([r["ttft"] for r in ok], 95)},
            "e2e": {"p50": pct([r["e2e"] for r in ok], 50), "p95": pct([r["e2e"] for r in ok], 95)},
            "slo": slo_eval(sub, secs, {"ttfe": args.slo_ttfe, "ttft": args.slo_ttft, "e2e": args.slo_e2e,
                                        "error_rate": args.slo_error_rate}),
        })
    return res


def print_summary(s):
    print("\n" + "=" * 64)
    print(f"  {s['tag']}  mode={s['mode']}  sessions={s['sessions']}  wall={s['wall_s']}s")
    print("=" * 64)
    print(f"  请求 {s['requests']}  成功 {s['ok']}  吞吐 {s['throughput_rps']} req/s  错误率 {s['error_rate']}")
    print(f"  状态分布 {s['status_counts']}")
    for key, name in (("ttfe", "首个事件 TTFE"), ("ttft", "首个答案字 TTFT"), ("e2e", "端到端 E2E"), ("itl", "出字间隔 ITL")):
        v = s[key]
        p99 = v["p99"] if s["p99_reliable"] else f"{v['p99']}(样本不足)"
        print(f"  {name:<14} P50 {v['p50']}  P95 {v['p95']}  P99 {p99}  max {v['max']}  n={v['n']}")
    print(f"  检索层拒答占比 {s['refused_ratio']}   轮次分布 {s['turn_dist']}")
    v = s["slo"]
    t = v["targets"]
    mark = lambda k: "✅" if v["checks"][k] else "❌"
    print(f"  SLO {'✅ 达标' if v['met'] else '❌ 未达标'}  "
          f"TTFE P95 {v['p95']['ttfe']}≤{t['ttfe']}{mark('ttfe')}  TTFT P95 {v['p95']['ttft']}≤{t['ttft']}{mark('ttft')}  "
          f"E2E P95 {v['p95']['e2e']}≤{t['e2e']}{mark('e2e')}  错误率 {v['error_rate']}≤{t['error_rate']}{mark('error_rate')}")
    print(f"  达标率 {v['attainment']}  goodput {v['goodput_rps']} req/s  准入拒绝 {v['admission_rejected']}（{v['admission_rejected_ratio']}）")
    if s.get("load") == "open":
        print(f"  开环：会话到达 {s['arrival_rate_sessions_per_s']} 个/s，实际发起请求 {s['offered_rps']} req/s")
    for p in s.get("phases", []):
        v = p["slo"]
        print(f"  阶段{p['phase']} {p['sessions']}会话×{p['seconds']}s：请求 {p['requests']} 成功 {p['ok']} "
              f"吞吐 {p['throughput_rps']}  TTFT P95 {p['ttft']['p95']}  E2E P95 {p['e2e']['p95']}  "
              f"准入拒绝 {v['admission_rejected']}  达标率 {v['attainment']}  {'✅' if v['met'] else '❌'}  {p['status_counts']}")
    print("=" * 64)


# ── 主流程 ────────────────────────────────────────────────────────────────────
async def main_async(args):
    by_topic, allq = load_pool(args.pool or DEFAULT_POOLS)
    url = args.api.rstrip("/") + "/chat/stream"
    out_dir = ROOT / "results" / args.tag
    out_dir.mkdir(parents=True, exist_ok=True)
    if args.load == "spike":
        phases = parse_phases(args.phases)
        args.duration = sum(sec for _, sec in phases)
        args.sessions = max(lvl for lvl, _ in phases)
        desc = f"突增 {args.phases}"
    elif args.load == "open":
        desc = f"开环 会话到达 {args.arrival_rate}/s"
    else:
        desc = f"{args.sessions} 个会话"
    print(f"🚀 {args.tag}: {desc} × {args.duration}s，load={args.load} mode={args.mode}，"
          f"题库 {len(allq)} 条 / {len(by_topic)} 个主题 → {url}")

    recs: list = []
    max_conn = 1000 if args.load == "open" else args.sessions * 2 + 10
    limits = httpx.Limits(max_connections=max_conn, max_keepalive_connections=min(max_conn, 200))
    t_start = time.time()
    t_end = t_start + args.duration
    async with httpx.AsyncClient(limits=limits, timeout=args.timeout) as client:
        if args.load == "open":
            tasks = [asyncio.create_task(open_loop(args, client, url, by_topic, allq, t_start, t_end, recs))]
        else:
            active_fn = lambda i: (lambda: True)
            phase_of = None
            if args.load == "spike":
                bounds = []
                acc = t_start
                for lvl, sec in phases:
                    bounds.append((acc, acc + sec, lvl))
                    acc += sec

                def phase_of(t):
                    for idx, (a, b, _) in enumerate(bounds):
                        if a <= t < b:
                            return idx
                    return len(bounds) - 1

                def level_now():
                    return bounds[phase_of(time.time())][2]

                active_fn = lambda i: (lambda: i < level_now())
            rngs = [random.Random((args.seed or 0) * 1000 + i) for i in range(args.sessions)]
            tasks = [asyncio.create_task(session_worker(i, args, client, url, by_topic, allq, t_end, recs, rngs[i],
                                                        active_fn(i), phase_of))
                     for i in range(args.sessions)]

        async def progress():
            while not all(t.done() for t in tasks):
                await asyncio.sleep(30)
                ok = sum(1 for r in recs if r["status"] == "ok")
                print(f"  … {int(time.time() - t_start)}s  完成 {len(recs)}  成功 {ok}")
        prog = asyncio.create_task(progress())
        # 时长结束后不再发起新请求；已发出的请求最多再等 grace 秒
        await asyncio.wait(tasks, timeout=args.duration + args.grace)
        if args.load == "open" and tasks[0].done():
            # 开环：发生器结束后，再等待它创建的会话任务
            sess = tasks[0].result()
            tasks = tasks + sess
            if sess:
                await asyncio.wait(sess, timeout=max(1.0, t_end + args.grace - time.time()))
        for t in tasks:
            if not t.done():
                t.cancel()
        prog.cancel()
    wall = time.time() - t_start

    with open(out_dir / "requests.jsonl", "w", encoding="utf-8") as f:
        for r in recs:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    s = summarize(recs, args, min(wall, args.duration + args.grace))
    s["config"] = {k: v for k, v in vars(args).items() if k != "pool"}
    (out_dir / "summary.json").write_text(json.dumps(s, ensure_ascii=False, indent=2), encoding="utf-8")
    print_summary(s)
    print(f"📄 {out_dir}")


def main():
    ap = argparse.ArgumentParser(description="多轮会话压测客户端")
    ap.add_argument("--api", default="http://localhost:8000")
    ap.add_argument("--load", choices=["closed", "open", "spike"], default="closed",
                    help="closed=闭环固定会话数；open=开环按 --arrival-rate 泊松到达；spike=按 --phases 分阶段改变会话数")
    ap.add_argument("--sessions", type=int, default=0, help="闭环：同时在线的会话数")
    ap.add_argument("--arrival-rate", type=float, default=0.0, help="开环：会话到达速率（个/秒）")
    ap.add_argument("--phases", default="", help="突增：会话数:秒,…，如 10:120,60:120,10:120")
    ap.add_argument("--duration", type=int, default=300, help="发起请求的时长（秒）")
    ap.add_argument("--grace", type=int, default=120, help="时长结束后等待在途请求完成的秒数")
    ap.add_argument("--mode", choices=["multi", "single"], default="multi")
    ap.add_argument("--min-turns", type=int, default=1)
    ap.add_argument("--max-turns", type=int, default=5)
    ap.add_argument("--think-mean", type=float, default=8.0, help="两轮之间平均思考时间（秒，指数分布）")
    ap.add_argument("--timeout", type=float, default=120.0, help="单请求超时（秒）")
    ap.add_argument("--tag", required=True, help="结果目录名，如 baseline_s10")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--pool", nargs="*", help="题库文件（默认 R3 原题 + 合成数据）")
    ap.add_argument("--slo-ttfe", type=float, default=SLO_DEFAULT["ttfe"], help="SLO：TTFE P95 上限（秒）")
    ap.add_argument("--slo-ttft", type=float, default=SLO_DEFAULT["ttft"], help="SLO：TTFT P95 上限（秒）")
    ap.add_argument("--slo-e2e", type=float, default=SLO_DEFAULT["e2e"], help="SLO：E2E P95 上限（秒）")
    ap.add_argument("--slo-error-rate", type=float, default=SLO_DEFAULT["error_rate"], help="SLO：错误率上限（不含准入拒绝）")
    args = ap.parse_args()
    if args.load == "closed" and args.sessions <= 0:
        ap.error("闭环模式需要 --sessions")
    if args.load == "open" and args.arrival_rate <= 0:
        ap.error("开环模式需要 --arrival-rate")
    if args.load == "spike" and not args.phases:
        ap.error("突增模式需要 --phases")
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
