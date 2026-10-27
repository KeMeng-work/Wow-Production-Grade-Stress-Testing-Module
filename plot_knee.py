"""
拐点图：吞吐 vs 会话数、端到端延迟 P50/P95 vs 会话数
读取 results/<tag>/summary.json，输出 PNG 到 results/charts/

用法（本地）：
    python plot_knee.py --tags baseline_s1 baseline_s10 baseline_s20 baseline_s40 --name baseline
    python plot_knee.py --tags baseline_s1 baseline_s10 baseline_s20 baseline_s40         --after opt1_s1 opt1_s10 opt1_s20 opt1_s40 --name compare_opt1      # 优化前后对比
"""

import argparse
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parent
plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "Noto Sans CJK SC", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

SURFACE = "#fcfcfb"
TEXT_1, TEXT_2, GRID = "#0b0b0b", "#52514e", "#e6e5e0"
S1, S2 = "#2a78d6", "#eb6834"   # 分类色槽 1（蓝）、2（橙）
REF = "#9b9a94"                 # 参考线（中性灰）


def load(tags):
    rows = []
    for t in tags:
        s = json.loads((ROOT / "results" / t / "summary.json").read_text(encoding="utf-8"))
        rows.append(s)
    return sorted(rows, key=lambda s: s["sessions"])


def style(ax, title, ylabel):
    ax.set_facecolor(SURFACE)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(GRID)
    ax.grid(axis="y", color=GRID, linewidth=0.8)
    ax.tick_params(colors=TEXT_2, labelsize=10)
    ax.set_title(title, loc="left", color=TEXT_1, fontsize=13, pad=12)
    ax.set_xlabel("同时在线会话数", color=TEXT_2, fontsize=10)
    ax.set_ylabel(ylabel, color=TEXT_2, fontsize=10)


def plot_throughput(rows, out, label):
    x = [r["sessions"] for r in rows]
    y = [r["throughput_rps"] for r in rows]
    per = rows[0]["throughput_rps"] / rows[0]["sessions"]   # 最低档的单会话吞吐 → 理想线性
    fig, ax = plt.subplots(figsize=(7.5, 4.6), dpi=160)
    fig.patch.set_facecolor(SURFACE)
    style(ax, f"吞吐 vs 并发会话（{label}）", "吞吐（req/s）")
    ax.plot([0, x[-1]], [0, per * x[-1]], color=REF, linewidth=1.5, linestyle=(0, (4, 3)))
    ax.text(x[-1], per * x[-1], " 理想线性", color=TEXT_2, fontsize=9, va="center")
    ax.plot(x, y, color=S1, linewidth=2, marker="o", markersize=7,
            markeredgecolor=SURFACE, markeredgewidth=2, zorder=3)
    for i, (xi, yi) in enumerate(zip(x, y)):
        off, ha = ((10, 2), "left") if i == 0 else ((0, -16), "center")   # 首点放右侧，避开 x 轴刻度
        ax.annotate(f"{yi:.2f}", (xi, yi), textcoords="offset points", xytext=off,
                    ha=ha, color=TEXT_1, fontsize=9)
    ax.text(x[-1], y[-1], "  实测", color=TEXT_2, fontsize=9, va="bottom")
    ax.set_xlim(0, x[-1] * 1.12)
    ax.set_ylim(0, per * x[-1] * 1.08)
    ax.set_xticks(x)
    fig.tight_layout()
    fig.savefig(out, facecolor=SURFACE)
    plt.close(fig)


def plot_latency(rows, out, label, metric="e2e"):
    x = [r["sessions"] for r in rows]
    p50 = [r[metric]["p50"] for r in rows]
    p95 = [r[metric]["p95"] for r in rows]
    fig, ax = plt.subplots(figsize=(7.5, 4.6), dpi=160)
    fig.patch.set_facecolor(SURFACE)
    style(ax, f"端到端延迟 vs 并发会话（{label}）", "端到端延迟（秒）")
    for ys, c, name, dy in ((p95, S2, "P95", 8), (p50, S1, "P50", -16)):
        ax.plot(x, ys, color=c, linewidth=2, marker="o", markersize=7,
                markeredgecolor=SURFACE, markeredgewidth=2, label=name, zorder=3)
        for xi, yi in zip(x, ys):
            ax.annotate(f"{yi:.1f}", (xi, yi), textcoords="offset points", xytext=(0, dy),
                        ha="center", color=TEXT_1, fontsize=9)
        ax.text(x[-1], ys[-1], f"  {name}", color=TEXT_2, fontsize=9, va="center")
    leg = ax.legend(loc="upper left", frameon=False, fontsize=9)
    for t in leg.get_texts():
        t.set_color(TEXT_2)
    ax.set_xlim(0, x[-1] * 1.12)
    ax.set_ylim(0, max(p95) * 1.15)
    ax.set_xticks(x)
    fig.tight_layout()
    fig.savefig(out, facecolor=SURFACE)
    plt.close(fig)


def plot_compare(before, after, out_dir, name, labels=("优化前", "优化后")):
    """优化前后对比：吞吐（含理想线）与 E2E P50/P95，两组档位需一致"""
    xb = [r["sessions"] for r in before]; xa = [r["sessions"] for r in after]
    per = before[0]["throughput_rps"] / before[0]["sessions"]
    xmax = max(xb[-1], xa[-1])

    fig, ax = plt.subplots(figsize=(7.5, 4.6), dpi=160)
    fig.patch.set_facecolor(SURFACE)
    style(ax, "吞吐 vs 并发会话（优化前后）", "吞吐（req/s）")
    ax.plot([0, xmax], [0, per * xmax], color=REF, linewidth=1.5, linestyle=(0, (4, 3)))
    ax.text(xmax, per * xmax, " 理想线性", color=TEXT_2, fontsize=9, va="center")
    for rows, c, lab, dy in ((before, S1, labels[0], -16), (after, S2, labels[1], 9)):
        x = [r["sessions"] for r in rows]; y = [r["throughput_rps"] for r in rows]
        ax.plot(x, y, color=c, linewidth=2, marker="o", markersize=7,
                markeredgecolor=SURFACE, markeredgewidth=2, label=lab, zorder=3)
        for i, (xi, yi) in enumerate(zip(x, y)):
            if i == 0:
                continue                      # 1 会话两组几乎重合，不标数值
            ax.annotate(f"{yi:.2f}", (xi, yi), textcoords="offset points", xytext=(0, dy),
                        ha="center", color=TEXT_1, fontsize=9)
        ax.text(x[-1], y[-1], f"  {lab}", color=TEXT_2, fontsize=9, va="center")
    leg = ax.legend(loc="upper left", frameon=False, fontsize=9)
    for t in leg.get_texts():
        t.set_color(TEXT_2)
    ax.set_xlim(0, xmax * 1.16); ax.set_ylim(0, per * xmax * 1.08); ax.set_xticks(sorted(set(xb + xa)))
    fig.tight_layout(); fig.savefig(out_dir / f"{name}_throughput.png", facecolor=SURFACE); plt.close(fig)

    fig, axes = plt.subplots(1, 2, figsize=(11, 4.6), dpi=160, sharey=True)
    fig.patch.set_facecolor(SURFACE)
    for ax, pk, title in ((axes[0], "p50", "端到端延迟 P50"), (axes[1], "p95", "端到端延迟 P95")):
        style(ax, f"{title}（优化前后）", "秒" if pk == "p50" else "")
        for rows, c, lab, dy in ((before, S1, labels[0], 8), (after, S2, labels[1], -16)):
            x = [r["sessions"] for r in rows]; y = [r["e2e"][pk] for r in rows]
            ax.plot(x, y, color=c, linewidth=2, marker="o", markersize=7,
                    markeredgecolor=SURFACE, markeredgewidth=2, label=lab, zorder=3)
            for i, (xi, yi) in enumerate(zip(x, y)):
                if i == 0:
                    continue
                ax.annotate(f"{yi:.1f}", (xi, yi), textcoords="offset points", xytext=(0, dy),
                            ha="center", color=TEXT_1, fontsize=9)
            ax.text(x[-1], y[-1], f"  {lab}", color=TEXT_2, fontsize=9, va="center")
        ax.set_xlim(0, xmax * 1.2); ax.set_xticks(sorted(set(xb + xa)))
    axes[0].set_ylim(0, max(r["e2e"]["p95"] for r in before + after) * 1.15)
    leg = axes[0].legend(loc="upper left", frameon=False, fontsize=9)
    for t in leg.get_texts():
        t.set_color(TEXT_2)
    fig.tight_layout(); fig.savefig(out_dir / f"{name}_latency.png", facecolor=SURFACE); plt.close(fig)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tags", nargs="+", required=True, help="单组档位；与 --after 同用时为优化前")
    ap.add_argument("--after", nargs="+", help="优化后的档位（给出则画前后对比图）")
    ap.add_argument("--name", required=True, help="输出文件前缀，如 baseline")
    ap.add_argument("--label", default="优化前基线")
    args = ap.parse_args()
    rows = load(args.tags)
    out_dir = ROOT / "results" / "charts"
    out_dir.mkdir(parents=True, exist_ok=True)
    if args.after:
        plot_compare(rows, load(args.after), out_dir, args.name)
        print(f"✅ {out_dir}")
        return
    plot_throughput(rows, out_dir / f"{args.name}_throughput.png", args.label)
    plot_latency(rows, out_dir / f"{args.name}_latency.png", args.label)
    print(f"✅ {out_dir}")


if __name__ == "__main__":
    main()
