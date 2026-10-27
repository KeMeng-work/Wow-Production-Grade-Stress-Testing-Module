# 压测操作手册（loadtest runbook）

压测阶段的全部脚本与结果。全部在**服务器宿主机**运行，不进容器；使用独立虚拟环境，**不要装进 vLLM 的环境**。

| 文件 | 作用 |
|---|---|
| `mock_llm.py` | 主模型替身：兼容 OpenAI 接口，按 `mock_profile.json` 的真实耗时分布等待并返回（工具调用 / 流式答案），不调用百炼 |
| `mock_profile.json` | 替身参数（用 Langfuse 导出的 R3 数据校准） |
| `run_load.py` | 压测客户端：多轮会话闭环负载，逐请求记录 TTFE / TTFT / E2E / ITL，输出 `results/<tag>/` |
| `monitor.sh` | 资源采样：GPU、vLLM 排队、app / TEI 容器 CPU 与内存，每 5 秒一行 CSV |

## 一次性准备

```bash
python3 -m venv ~/loadtest-venv
~/loadtest-venv/bin/pip install fastapi uvicorn httpx
```

## 第一阶段：改造前基线

### 1. 切换压测配置（服务器 `.env`，改完重建 app 镜像）

依次为：主模型指向替身、关闭高频缓存、关闭在线 RAGAS、关闭在线搜索、放开全局限流、放开单条查询限流。
**注释不要写在同一行**，否则值会带上注释导致解析失败。

```
LLM_BASE_URL=http://host.docker.internal:8010/v1
CACHE_THRESHOLD=1000000
RAGAS_ENABLED=false
WEB_SEARCH_ENABLED=false
RATE_LIMIT_GLOBAL=1000
RATE_LIMIT_QUERY=1000
AUTH_ENABLED=false
```

代码是打包进镜像的（Dockerfile `COPY . .`），同步了 `main.py` / `config_path.py` 后必须 `--build`：

```bash
docker compose up -d --build app
docker compose exec app env | grep -E "LLM_BASE_URL|CACHE_THRESHOLD|RATE_LIMIT"   # 确认生效
```

### 2. 启动替身（终端 1）

```bash
cd loadtest && ~/loadtest-venv/bin/python mock_llm.py --port 8010
curl -s localhost:8010/v1/models        # 应返回模型列表
```

### 3. 启动资源采样（终端 2）

```bash
cd loadtest && bash monitor.sh results/baseline_metrics.csv 5
```

### 4. 跑两档（终端 3）

```bash
cd loadtest
~/loadtest-venv/bin/python run_load.py --sessions 10 --duration 300 --tag baseline_s10
sleep 60
~/loadtest-venv/bin/python run_load.py --sessions 20 --duration 300 --tag baseline_s20
```

跑完后在终端 2 按 Ctrl+C 停止采样，并导出这段时间的 app 日志：

```bash
docker compose logs app --since 20m > results/baseline_app.log
```

### 5. 拉回本地

`loadtest/results/` 整个目录（`baseline_s10/`、`baseline_s20/`、`baseline_metrics.csv`、`baseline_app.log`）。

## 压测结束后恢复

1. 删除 `.env` 中上面 6 行（或改回原值），`docker compose up -d --force-recreate app`
2. 停止替身（终端 1 Ctrl+C）
3. 清理压测产生的测试数据（user_id / session_id 均以 `lt_` 开头）：
   - Redis 短期记忆：`docker compose exec redis redis-cli --scan --pattern "*lt_*"`，确认后删除
   - PostgreSQL 长期记忆与对话记录：按 `user_id LIKE 'lt_%'` 删除

## 指标口径

- **TTFE** 首个 SSE 事件；**TTFT** 首个答案字；**E2E** 收到 done；**ITL** 答案相邻 chunk 平均间隔
- 每档报 P50 / P95；样本 ≥ 500 时报 P99（汇总中 `p99_reliable` 标记）
- 错误按类型分开：`http_429`（限流）、`error_503`（熔断）、`timeout`、`incomplete`、`empty_answer`、`client_error`
- 检索层拒答是业务行为，计入成功，单独统计 `refused_ratio`
