#!/bin/bash
# 资源采样：每 INTERVAL 秒记录 GPU、vLLM 排队与 KV、容器 CPU/内存、宿主机 CPU、app 内部指标（/metrics），写入 CSV
# 用法：bash monitor.sh results/opt2_metrics.csv [间隔秒数，默认 5]
# 压测结束后 kill 停止
# 2026-10-08：末尾新增 disk_free_gb（根分区剩余 GB），浸泡时监控磁盘
# 2026-10-07 P2：新增 v17b_kv、host_cpu（宿主机整体，%，8 核满载 = 100）与 app /metrics 字段（旧版 /metrics 不存在时留空）
OUT=${1:-results/metrics.csv}
INTERVAL=${2:-5}
APP_METRICS_URL=${APP_METRICS_URL:-http://localhost:8000/metrics}
# /metrics 仅限 ops / admin（2026-10-17）：认证开启时设置 WOW_OPS_TOKEN；压测 AUTH_ENABLED=false 时可不设
AUTH_HEADER=()
[ -n "$WOW_OPS_TOKEN" ] && AUTH_HEADER=(-H "Authorization: Bearer $WOW_OPS_TOKEN")
mkdir -p "$(dirname "$OUT")"

vllm_metric() {  # $1=端口 $2=指标名前缀；取第一条匹配的值，没有则为空
  curl -s --max-time 2 "http://localhost:$1/metrics" | grep -E "^$2(\{|\s)" | head -1 | awk '{print $NF}'
}

cpu_ticks() {  # 输出 "总 tick 空闲 tick"（/proc/stat 第一行，空闲 = idle + iowait）
  awk '/^cpu /{t=0; for(i=2;i<=NF;i++) t+=$i; print t, $5+$6}' /proc/stat
}

app_metrics() {  # 从 /metrics JSON 提取字段，逗号分隔；失败时输出等量空字段
  curl -s --max-time 2 "${AUTH_HEADER[@]}" "$APP_METRICS_URL" | python3 -c '
import json, sys
try:
    d = json.load(sys.stdin)
    a, b, r, lag, p = d["admission"], d["bulkheads"], d["retry_budget"], d["loop_lag"], d.get("thread_pool") or {}
    v = [a["in_use"], a["peak_in_use"], a["waiting"], a["rejected_total"],
         lag["max_ms"], p.get("queue", ""), p.get("threads", ""),
         b["rewrite_4b"]["peak_in_use"], b["rewrite_4b"]["rejected_total"],
         b["evaluator_4b"]["peak_in_use"], b["evaluator_4b"]["rejected_total"],
         b["rerank_tei"]["peak_in_use"], b["rerank_tei"]["rejected_total"],
         r["denied_total"], d.get("background_tasks", "")]
    print(",".join(str(x) for x in v))
except Exception:
    print("," * 14)
'
}

echo "ts,gpu_util,gpu_mem_mb,v4b_running,v4b_waiting,v4b_kv,v17b_running,v17b_waiting,v17b_kv,app_cpu,app_mem,tei_cpu,tei_mem,host_cpu,adm_inflight,adm_peak,adm_waiting,adm_rejected_total,loop_lag_max_ms,pool_queue,pool_threads,rw_peak,rw_rejected_total,eval_peak,eval_rejected_total,rerank_peak,rerank_rejected_total,retry_denied_total,bg_tasks,disk_free_gb" > "$OUT"
read PREV_T PREV_I < <(cpu_ticks)
while true; do
  TS=$(date +%s)
  GPU=$(nvidia-smi --query-gpu=utilization.gpu,memory.used --format=csv,noheader,nounits | head -1 | tr -d ' ')
  R4=$(vllm_metric 8001 "vllm:num_requests_running")
  W4=$(vllm_metric 8001 "vllm:num_requests_waiting")
  K4=$(vllm_metric 8001 "vllm:(gpu_cache_usage_perc|kv_cache_usage_perc)")
  R17=$(vllm_metric 8002 "vllm:num_requests_running")
  W17=$(vllm_metric 8002 "vllm:num_requests_waiting")
  K17=$(vllm_metric 8002 "vllm:(gpu_cache_usage_perc|kv_cache_usage_perc)")
  APP=$(docker stats --no-stream --format "{{.CPUPerc}},{{.MemUsage}}" react-agent-app 2>/dev/null | sed 's/ \/.*//' | tr -d '%')
  TEI=$(docker stats --no-stream --format "{{.CPUPerc}},{{.MemUsage}}" tei-reranker 2>/dev/null | sed 's/ \/.*//' | tr -d '%')
  read CUR_T CUR_I < <(cpu_ticks)
  HOST=$(awk -v dt=$((CUR_T - PREV_T)) -v di=$((CUR_I - PREV_I)) 'BEGIN{ if (dt > 0) printf "%.1f", (1 - di/dt) * 100 }')
  PREV_T=$CUR_T; PREV_I=$CUR_I
  AM=$(app_metrics)
  DISK=$(df -BG --output=avail / | tail -1 | tr -dc '0-9')
  echo "$TS,$GPU,$R4,$W4,$K4,$R17,$W17,$K17,$APP,$TEI,$HOST,$AM,$DISK" >> "$OUT"
  sleep "$INTERVAL"
done
