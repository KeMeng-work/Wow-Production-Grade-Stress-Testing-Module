#!/bin/bash
# 第 3 步 B2：浸泡重跑（B 轮浸泡在 1560s 处因磁盘写满中断；突增已完成，不重跑）
# 前提：系统盘已扩容；准入 24 / 排队 1.0；替身运行中
# 用法（loadtest/ 下）：nohup bash step3_b2.sh > results/step3_b2.log 2>&1 &
# 约 62 分钟：30 会话 × 60 分钟；监控 CSV 末列 disk_free_gb 记录根分区剩余空间
cd "$(dirname "$0")"
PY=~/loadtest-venv/bin/python

echo "开始前磁盘剩余：$(df -h --output=avail / | tail -1)"
bash monitor.sh results/step3_b2_metrics.csv 5 > results/step3_b2_monitor.out 2>&1 &
MON=$!
trap 'kill $MON 2>/dev/null' EXIT

$PY run_load.py --sessions 30 --duration 3600 --tag soak_s30_r2
echo "结束时磁盘剩余：$(df -h --output=avail / | tail -1)"
echo "STEP3_B2_DONE $(date)"
