"""
按 SLO 重新评估已有压测结果（不重跑），输出对比表

用法（本地）：
    python slo_report.py baseline_s1 baseline_s10 baseline_s20 baseline_s40 opt1_s1 opt1_s10 opt1_s20 opt1_s40
SLO 默认值见 run_load.SLO_DEFAULT，可用 --slo-ttfe / --slo-ttft / --slo-e2e / --slo-error-rate 覆盖
"""

import argparse
import json
from pathlib import Path

from run_load import SLO_DEFAULT, slo_eval

ROOT = Path(__file__).resolve().parent


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("tags", nargs="+")
    ap.add_argument("--slo-ttfe", type=float, default=SLO_DEFAULT["ttfe"])
    ap.add_argument("--slo-ttft", type=float, default=SLO_DEFAULT["ttft"])
    ap.add_argument("--slo-e2e", type=float, default=SLO_DEFAULT["e2e"])
    ap.add_argument("--slo-error-rate", type=float, default=SLO_DEFAULT["error_rate"])
    args = ap.parse_args()
    slo = {"ttfe": args.slo_ttfe, "ttft": args.slo_ttft, "e2e": args.slo_e2e, "error_rate": args.slo_error_rate}
    print(f"SLO：TTFE P95 ≤ {slo['ttfe']}s  TTFT P95 ≤ {slo['ttft']}s  E2E P95 ≤ {slo['e2e']}s  错误率 ≤ {slo['error_rate']}")
    print(f"{'tag':<16}{'会话':>4}{'吞吐':>8}{'goodput':>9}{'达标率':>8}{'TTFE95':>8}{'TTFT95':>8}{'E2E95':>8}{'错误率':>8}{'准入拒绝':>8}  SLO")
    for tag in args.tags:
        d = ROOT / "results" / tag
        s = json.loads((d / "summary.json").read_text(encoding="utf-8"))
        recs = [json.loads(l) for l in (d / "requests.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
        v = slo_eval(recs, s["wall_s"], slo)          # wall_s 与压测时计算吞吐所用墙钟一致
        print(f"{tag:<16}{s['sessions']:>4}{s['throughput_rps']:>8}{v['goodput_rps']:>9}{v['attainment']:>8}"
              f"{v['p95']['ttfe']:>8}{v['p95']['ttft']:>8}{v['p95']['e2e']:>8}{v['error_rate']:>8}{v['admission_rejected']:>8}"
              f"  {'✅' if v['met'] else '❌'}")


if __name__ == "__main__":
    main()
