#!/bin/bash
# 第 3 步 B：突增 + 浸泡（替身，准入控制开启：.env 中删除 ADMISSION_MAX_INFLIGHT=0，恢复默认 30）
# 用法（loadtest/ 下）：nohup bash step3_b.sh > results/step3_b.log 2>&1 &
# 约 75 分钟：突增 10→60→10 会话（各 2 分钟）+ 浸泡 30 会话 60 分钟
cd "$(dirname "$0")"
PY=~/loadtest-venv/bin/python

bash monitor.sh results/step3_b_metrics.csv 5 > results/step3_b_monitor.out 2>&1 &
MON=$!
trap 'kill $MON 2>/dev/null' EXIT

$PY run_load.py --load spike --phases 10:120,60:120,10:120 --tag spike_10_60_10
sleep 60
$PY run_load.py --sessions 30 --duration 3600 --tag soak_s30
echo "STEP3_B_DONE $(date)"
