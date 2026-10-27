#!/bin/bash
# 第 3 步 C：真实百炼校验 + 负载下质量（主模型接真实 qwen3-30b-a3b）
# 前提：.env 已删除 LLM_BASE_URL 行（恢复百炼），并已 --force-recreate；准入控制保持定稿值
# 用法（loadtest/ 下）：nohup bash step3_c.sh > results/step3_c.log 2>&1 &
# 约 12 分钟：
#   - 负载：20 会话多轮闭环 10 分钟（与替身同档对比 → 验证替身可信；同时统计真实 token / 成本）
#   - 质量：负载进行中，评估脚本串行重答 R3 原评估集中按题型分层抽取的 60 题 → 离线 RAGAS，与 R3 同题分数对比
# 预估百炼用量：约 0.7M TPM（配额 1M 的 70%），10 分钟约 7M token
cd "$(dirname "$0")"
PY=~/loadtest-venv/bin/python
IDS="2,6,10,12,16,18,28,32,38,40,44,46,50,54,56,58,60,66,74,90,92,106,110,114,126,128,130,132,138,146,150,160,166,172,178,182,186,188,210,212,214,220,224,228,242,252,256,272,294,296,312,314,316,320,332,334,348,356,358,360"

bash monitor.sh results/step3_c_metrics.csv 5 > results/step3_c_monitor.out 2>&1 &
MON=$!
trap 'kill $MON 2>/dev/null' EXIT

$PY run_load.py --sessions 20 --duration 600 --tag real_s20 > results/real_s20.log 2>&1 &
LOAD=$!
sleep 30   # 负载进入稳态后再开始质量评估

# 评估脚本在 app 容器内运行（scripts/ 与 data/ 已挂载），输出 data/evaluation/ragas_input_batch_3_eval_loadq.json
(cd .. && docker compose exec -T app python scripts/run_eval_baseline.py \
    --from-batch 2 --ids "$IDS" --user-id eval_loadq --rerun --delay 0)

wait $LOAD
cat results/real_s20.log
echo "STEP3_C_DONE $(date)"
