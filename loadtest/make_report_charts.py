"""
README 压测报告配图：全部从 results/<tag>/ 的 summary.json 与 requests.jsonl 计算，输出到 charts/

用法（本地，loadtest/ 下）：
    python make_report_charts.py
"""

import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parent
RES, OUT = ROOT / "results", ROOT / "charts"
OUT.mkdir(exist_ok=True)

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "Noto Sans CJK SC", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

BG, TEXT_1, TEXT_2, GRID = "#ffffff", "#0f172a", "#475569", "#e2e8f0"
ACCENT, NEUTRAL, WARN, GOOD, BAD = "#6366F1", "#64748B", "#F59E0B", "#10B981", "#EF4444"
SLO = {"ttfe": 5.0, "ttft": 8.0, "e2e": 12.0}


def summary(tag):
    return json.loads((RES / tag / "summary.json").read_text(encoding="utf-8"))


def records(tag):
    with open(RES / tag / "requests.jsonl", encoding="utf-8") as f:
        return [json.loads(line) for line in f]


def goodput(s):
    """summary 里有 SLO 评估就直接用；早期轮次（基线、opt1）没有，则按 slo_report.py 同口径从逐请求记录回算"""
    g = (s.get("slo") or {}).get("goodput_rps")
    if g is not None:
        return g
    return sum(map(good, records(s["tag"]))) / s["wall_s"]


def p95(values):
    v = sorted(values)
    return v[min(len(v) - 1, int(0.95 * len(v)))] if v else None


def good(r):
    return r["status"] == "ok" and all(r[k] is not None and r[k] <= v for k, v in SLO.items())


def style(ax, title, xlabel, ylabel):
    ax.set_facecolor(BG)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(GRID)
    ax.grid(axis="y", color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    ax.tick_params(colors=TEXT_2, labelsize=10)
    ax.set_title(title, loc="left", color=TEXT_1, fontsize=13, fontweight="bold", pad=12)
    ax.set_xlabel(xlabel, color=TEXT_2, fontsize=10)
    ax.set_ylabel(ylabel, color=TEXT_2, fontsize=10)


def save(fig, name):
    fig.tight_layout()
    fig.savefig(OUT / name, dpi=150, facecolor=BG)
    plt.close(fig)
    print("✓", OUT / name)


def line(ax, x, y, color, label, dash=False, labels=True):
    ax.plot(x, y, color=color, linewidth=2.2, linestyle="--" if dash else "-", marker="o", markersize=6,
            markeredgecolor=BG, markeredgewidth=1.5, label=label)
    if labels:
        for xi, yi in zip(x, y):
            ax.annotate(f"{yi:g}", (xi, yi), textcoords="offset points", xytext=(0, 8), ha="center", fontsize=8.5, color=color)


def slo_line(ax, y, label):
    ax.axhline(y, color=NEUTRAL, linewidth=1.2, linestyle=(0, (5, 4)))
    ax.annotate(label, (1.0, y), xycoords=("axes fraction", "data"), xytext=(-4, 4), textcoords="offset points",
                ha="right", fontsize=9, color=NEUTRAL)


# 1. 基线 vs 优化后：吞吐与 goodput、TTFT P95
def chart_opt():
    xs = [1, 10, 20, 40]
    base = [summary(f"baseline_s{n}") for n in xs]
    opt = [summary(f"opt1_s{n}") for n in xs]
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(12, 4.4))
    style(a1, "吞吐与 goodput（req/s）", "同时在线会话数", "req/s")
    line(a1, xs, [s["throughput_rps"] for s in base], NEUTRAL, "基线 吞吐")
    line(a1, xs, [round(goodput(s) or 0, 3) for s in base], NEUTRAL, "基线 goodput", dash=True, labels=False)
    line(a1, xs, [s["throughput_rps"] for s in opt], ACCENT, "优化后 吞吐")
    line(a1, xs, [round(goodput(s) or 0, 3) for s in opt], ACCENT, "优化后 goodput", dash=True, labels=False)
    a1.legend(frameon=False, fontsize=9)
    style(a2, "首字延迟 TTFT P95（秒）", "同时在线会话数", "秒")
    line(a2, xs, [round(s["ttft"]["p95"], 2) for s in base], NEUTRAL, "基线")
    line(a2, xs, [round(s["ttft"]["p95"], 2) for s in opt], ACCENT, "优化后")
    slo_line(a2, SLO["ttft"], "SLO 8 s")
    a2.legend(frameon=False, fontsize=9)
    save(fig, "01_baseline_vs_optimized.png")


# 2. 并发阶梯：吞吐 vs goodput；三项 P95 与 SLO
def chart_ladder():
    xs = [20, 30, 40, 50, 60]
    ss = [summary(f"ladder_s{n}") for n in xs]
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(12, 4.4))
    style(a1, "吞吐 vs goodput（准入关闭）", "同时在线会话数", "req/s")
    line(a1, xs, [s["throughput_rps"] for s in ss], NEUTRAL, "吞吐")
    line(a1, xs, [round(goodput(s), 2) for s in ss], ACCENT, "goodput")
    a1.axvspan(29, 31, color=GOOD, alpha=0.10)
    a1.annotate("稳定并发 30", (30, 0.3), ha="center", fontsize=9, color=GOOD)
    a1.annotate("拐点 40", (40, 0.3), ha="center", fontsize=9, color=WARN)
    a1.set_ylim(bottom=0)
    a1.legend(frameon=False, fontsize=9, loc="upper left")
    style(a2, "延迟 P95 与 SLO（秒）", "同时在线会话数", "秒")
    for k, c, name in (("ttfe", ACCENT, "TTFE"), ("ttft", WARN, "TTFT"), ("e2e", GOOD, "E2E")):
        line(a2, xs, [round(s[k]["p95"], 2) for s in ss], c, f"{name} P95", labels=False)
        a2.axhline(SLO[k], color=c, linewidth=1, linestyle=(0, (5, 4)), alpha=0.6)
    a2.legend(frameon=False, fontsize=9, loc="upper left")
    save(fig, "02_ladder.png")


# 3. 突增：两组准入参数的 30 秒窗口 TTFE P95 + 拒绝数
def chart_spike():
    fig, ax = plt.subplots(figsize=(12, 4.6))
    style(ax, "突增 10 → 60 → 10 会话：放行请求 TTFE P95（30 秒窗口）", "测试时间（2:00–4:00 为 60 会话）", "秒")
    ax2 = ax.twinx()
    ax2.spines["top"].set_visible(False)
    ax2.spines["right"].set_color(GRID)
    ax2.tick_params(colors=TEXT_2, labelsize=9)
    ax2.set_ylabel("被准入拒绝的请求数", color=TEXT_2, fontsize=10)
    labels = [f"{w // 60}:{w % 60:02d}" for w in range(0, 360, 30)]
    for i, (tag, color, name) in enumerate((("spike_10_60_10", WARN, "准入 24 / 排队 1.0 s"), ("spike_a20_q05", ACCENT, "准入 20 / 排队 0.5 s"))):
        recs = records(tag)
        t0 = min(r["t_send"] for r in recs)
        win = [[r for r in recs if w <= r["t_send"] - t0 < w + 30] for w in range(0, 360, 30)]
        y = [round(p95([r["ttfe"] for r in g if r["status"] == "ok"]), 2) for g in win]
        rej = [sum(1 for r in g if r["status"] != "ok") for g in win]
        ax2.bar([j + (i - 0.5) * 0.36 for j in range(len(win))], rej, width=0.36, color=color, alpha=0.22, label=f"拒绝数 · {name}")
        ax.plot(range(len(win)), y, color=color, linewidth=2.2, marker="o", markersize=6, markeredgecolor=BG, label=f"TTFE P95 · {name}", zorder=3)
    ax.set_zorder(ax2.get_zorder() + 1)
    ax.patch.set_visible(False)
    slo_line(ax, SLO["ttfe"], "SLO 5 s")
    ax.set_xticks(range(len(labels)), labels)
    ax.set_ylim(0, 8)
    ax2.set_ylim(0, 400)
    h1, l1 = ax.get_legend_handles_labels()
    h2, l2 = ax2.get_legend_handles_labels()
    ax.legend(h1 + h2, l1 + l2, frameon=False, fontsize=9, loc="upper left", ncol=2)
    save(fig, "03_spike.png")


# 4. 浸泡：每 10 分钟三项 P95
def chart_soak():
    recs = records("soak_s30")
    t0 = min(r["t_send"] for r in recs)
    xs = [f"{w}–{w + 10}" for w in range(0, 60, 10)]
    fig, ax = plt.subplots(figsize=(12, 4.2))
    style(ax, "浸泡 30 会话 × 60 分钟：每 10 分钟延迟 P95（秒）", "运行时间（分钟）", "秒")
    for k, c, name in (("ttfe", ACCENT, "TTFE"), ("ttft", WARN, "TTFT"), ("e2e", GOOD, "E2E")):
        y = []
        for w in range(0, 3600, 600):
            ok = [r[k] for r in recs if w <= r["t_send"] - t0 < w + 600 and r["status"] == "ok"]
            y.append(round(p95(ok), 2))
        line(ax, range(len(xs)), y, c, f"{name} P95")
        ax.axhline(SLO[k], color=c, linewidth=1, linestyle=(0, (5, 4)), alpha=0.5)
    ax.set_xticks(range(len(xs)), xs)
    ax.set_ylim(0, 13)
    ax.legend(frameon=False, fontsize=9, loc="lower right", ncol=3)
    save(fig, "04_soak.png")


# 5. 替身 vs 真实百炼：TTFE / TTFT P95
def chart_real():
    xs = [1, 10, 20]
    mock = [summary(t) for t in ("opt1_s1", "opt1_s10", "ladder_s20")]
    real = [summary(f"real_s{n}") for n in xs]
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(12, 4.4))
    for ax, k, name in ((a1, "ttft", "首字 TTFT P95"), (a2, "ttfe", "首个事件 TTFE P95")):
        style(ax, f"{name}：替身 vs 真实百炼（秒）", "同时在线会话数", "秒")
        line(ax, xs, [round(s[k]["p95"], 2) for s in mock], ACCENT, "本地替身")
        line(ax, xs, [round(s[k]["p95"], 2) for s in real], WARN, "真实百炼")
        slo_line(ax, SLO[k], f"SLO {SLO[k]:g} s")
        ax.set_ylim(0, SLO[k] * 1.35)
        ax.legend(frameon=False, fontsize=9, loc="lower right")
    save(fig, "05_mock_vs_real.png")


# 6. 负载下质量：60 题 RAGAS，R3 无负载 vs 负载下（同题均值）
def chart_ragas():
    ev = ROOT.parent / "data" / "evaluation"
    load = json.loads((ev / "ragas_results_batch_60.json").read_text(encoding="utf-8"))["details"]
    r3 = {x["id"]: x for x in json.loads((ev / "ragas_results_batch_3.json").read_text(encoding="utf-8"))["details"]}
    metrics = [("faithfulness", "Faithfulness"), ("answer_relevancy", "Answer Relevancy"),
               ("context_precision", "Context Precision"), ("context_recall", "Context Recall"),
               ("answer_correctness", "Answer Correctness")]
    base = [round(sum(r3[x["id"]][k] for x in load) / len(load), 3) for k, _ in metrics]
    vals = []
    for k, _ in metrics:
        v = [x[k] for x in load if x.get(k) is not None]
        vals.append(round(sum(v) / len(v), 3))
    fig, ax = plt.subplots(figsize=(12, 4.4))
    style(ax, "负载下回答质量：60 题 RAGAS（R3 无负载 vs 真实 20 会话负载）", "", "分数")
    xs = range(len(metrics))
    w = 0.36
    for off, ys, color, name in ((-w / 2, base, NEUTRAL, "R3 无负载"), (w / 2, vals, ACCENT, "负载下")):
        bars = ax.bar([x + off for x in xs], ys, width=w - 0.04, color=color, label=name)
        for bar, y in zip(bars, ys):
            ax.annotate(f"{y:.3f}", (bar.get_x() + bar.get_width() / 2, y), textcoords="offset points", xytext=(0, 4),
                        ha="center", fontsize=9, color=color)
    ax.set_xticks(list(xs), [n for _, n in metrics])
    ax.set_ylim(0, 1.12)
    ax.legend(frameon=False, fontsize=9, loc="upper right", ncol=2)
    save(fig, "06_ragas_under_load.png")


if __name__ == "__main__":
    chart_opt()
    chart_ladder()
    chart_spike()
    chart_soak()
    chart_real()
    chart_ragas()
