#!/bin/bash
# 第 3 步 A：并发阶梯 + 恒定速率（替身，准入控制关闭：.env 中 ADMISSION_MAX_INFLIGHT=0）
# 用法（loadtest/ 下）：nohup bash step3_a.sh > results/step3_a.log 2>&1 &
# 约 55 分钟：阶梯 20/30/40/50/60 会话各 5 分钟 + 开环 2 档各 5 分钟，档间休息 60 秒
cd "$(dirname "$0")"
PY=~/loadtest-venv/bin/python

bash monitor.sh results/step3_a_metrics.csv 5 > results/step3_a_monitor.out 2>&1 &
MON=$!
trap 'kill $MON 2>/dev/null' EXIT

for s in 20 30 40 50 60; do
  $PY run_load.py --sessions $s --duration 300 --tag ladder_s$s
  sleep 60
done

# 开环：会话到达速率 0.6/s ≈ 1.8 req/s（拐点以下），0.9/s ≈ 2.7 req/s（接近拐点）
for r in 0.6 0.9; do
  $PY run_load.py --load open --arrival-rate $r --duration 300 --tag open_r$r
  sleep 60
done
echo "STEP3_A_DONE $(date)"
