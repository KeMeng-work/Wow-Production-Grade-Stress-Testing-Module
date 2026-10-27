"""
主模型替身（Mock LLM）—— 压测专用

兼容 OpenAI /v1/chat/completions 接口（流式 + 非流式、工具调用、reasoning_content），
按 mock_profile.json 中的真实耗时分布"等待并返回"，替代云端 qwen3-30b-a3b：
  - 未出现工具结果时 → 先流式输出一段思考，再返回 knowledge_retriever 工具调用（query 取自系统提示中的 en_query）
  - 已出现工具结果时 → 按概率再检索一次（模拟多跳），否则流式输出思考 + 固定格式答案
它不做任何推理，只占用与真实主模型相近的时间，使本地链路（改写、检索、GPU、事件循环）承受真实压力。

运行（服务器宿主机，独立虚拟环境）：
    python mock_llm.py --port 8010 --profile mock_profile.json
app 侧 .env：LLM_BASE_URL=http://host.docker.internal:8010/v1
"""

import argparse
import asyncio
import json
import math
import random
import re
import time
import uuid
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

app = FastAPI()
PROFILE: dict = {}
STATS = {"requests": 0, "tool_calls": 0, "answers": 0, "in_flight": 0}

# 系统提示中渲染后的 en_query：`knowledge_retriever` 的 `query` 参数默认使用 `xxx`
_EN_QUERY_RE = re.compile(r"默认使用 `([^`]+)`")
_ANSWER_TEXT = "（压测替身回答）根据知识库资料，该问题的相关事实如下。" * 3


def _lognormal(spec: dict) -> float:
    """按 {p50, p95} 构造对数正态分布并抽样；截断到 [min, max]"""
    p50, p95 = spec["p50"], spec["p95"]
    mu = math.log(max(p50, 1e-6))
    sigma = max((math.log(max(p95, p50 * 1.0001)) - mu) / 1.645, 1e-3)
    v = random.lognormvariate(mu, sigma)
    return min(max(v, spec.get("min", 0.0)), spec.get("max", float("inf")))


def _estimate_input_tokens(messages: list) -> int:
    chars = sum(len(str(m.get("content") or "")) for m in messages)
    return int(chars / PROFILE.get("chars_per_token", 2.0))


def _decide(messages: list, tools: list) -> str:
    """返回 'tool' 或 'answer'"""
    if not tools:
        return "answer"
    n_tool_results = sum(1 for m in messages if m.get("role") == "tool")
    if n_tool_results == 0:
        return "tool"
    p_more = PROFILE.get("p_extra_retrieval", 0.05)
    if n_tool_results < PROFILE.get("max_retrievals", 3) and random.random() < p_more:
        return "tool"
    return "answer"


def _plan(kind: str, messages: list) -> dict:
    """抽样本次调用的时间与 token 规划"""
    spec = PROFILE["calls"][kind]
    in_tok = _estimate_input_tokens(messages)
    extra = max(in_tok - PROFILE.get("ttft_ref_input_tokens", 3000), 0) / 1000.0
    ttft = _lognormal(spec["ttft_s"]) + extra * PROFILE.get("ttft_per_1k_extra_input_s", 0.0)
    think_tok = int(_lognormal(spec["thinking_tokens"]))
    content_tok = int(_lognormal(spec["content_tokens"])) if kind == "answer" else 0
    tok_per_s = _lognormal(spec["tokens_per_s"])
    return {"ttft": ttft, "think": think_tok, "content": content_tok,
            "itl": 1.0 / max(tok_per_s, 1e-3), "in_tok": in_tok}


def _query_from(messages: list) -> str:
    for m in messages:
        if m.get("role") == "system":
            hit = _EN_QUERY_RE.search(str(m.get("content") or ""))
            if hit:
                return hit.group(1)
    for m in reversed(messages):
        if m.get("role") == "user":
            return str(m.get("content") or "")[:200]
    return "test query"


def _chunk(cid: str, model: str, delta: dict, finish=None, usage=None) -> str:
    body = {"id": cid, "object": "chat.completion.chunk", "created": int(time.time()), "model": model,
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
    if usage is not None:
        body["usage"] = usage
        body["choices"] = []
    return "data: " + json.dumps(body, ensure_ascii=False) + "\n\n"


async def _stream(kind: str, plan: dict, model: str, messages: list, include_usage: bool):
    cid = "chatcmpl-mock-" + uuid.uuid4().hex[:12]
    STATS["in_flight"] += 1
    try:
        yield _chunk(cid, model, {"role": "assistant", "content": ""})
        # 首 token 之前的等待（排队 + prefill）
        await asyncio.sleep(plan["ttft"])
        # 思考 token：按 ITL 逐个吐出（每 4 个 token 合并一个 chunk，降低压测客户端开销）
        step = 4
        for _ in range(0, plan["think"], step):
            yield _chunk(cid, model, {"reasoning_content": "思考" * step})
            await asyncio.sleep(plan["itl"] * step)
        if kind == "tool":
            args = json.dumps({"query": _query_from(messages)}, ensure_ascii=False)
            call_id = "call_" + uuid.uuid4().hex[:16]
            yield _chunk(cid, model, {"tool_calls": [{"index": 0, "id": call_id, "type": "function",
                                                      "function": {"name": "knowledge_retriever", "arguments": ""}}]})
            yield _chunk(cid, model, {"tool_calls": [{"index": 0, "function": {"arguments": args}}]})
            finish, out_tok = "tool_calls", plan["think"] + 20
            STATS["tool_calls"] += 1
        else:
            text = (_ANSWER_TEXT * (plan["content"] // len(_ANSWER_TEXT) + 1))[:max(plan["content"], 1)]
            for i in range(0, len(text), step):
                yield _chunk(cid, model, {"content": text[i:i + step]})
                await asyncio.sleep(plan["itl"] * step)
            finish, out_tok = "stop", plan["think"] + plan["content"]
            STATS["answers"] += 1
        yield _chunk(cid, model, {}, finish=finish)
        if include_usage:
            yield _chunk(cid, model, {}, usage={"prompt_tokens": plan["in_tok"], "completion_tokens": out_tok,
                                               "total_tokens": plan["in_tok"] + out_tok})
        yield "data: [DONE]\n\n"
    finally:
        STATS["in_flight"] -= 1


@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    body = await request.json()
    STATS["requests"] += 1
    messages = body.get("messages") or []
    kind = _decide(messages, body.get("tools") or [])
    plan = _plan(kind, messages)
    model = body.get("model", "mock")

    if body.get("stream"):
        include_usage = bool((body.get("stream_options") or {}).get("include_usage"))
        return StreamingResponse(_stream(kind, plan, model, messages, include_usage),
                                 media_type="text/event-stream")

    # 非流式：整体等待后一次返回
    STATS["in_flight"] += 1
    try:
        await asyncio.sleep(plan["ttft"] + plan["itl"] * (plan["think"] + plan["content"]))
    finally:
        STATS["in_flight"] -= 1
    message = {"role": "assistant", "content": "", "reasoning_content": "思考"}
    if kind == "tool":
        message["tool_calls"] = [{"id": "call_" + uuid.uuid4().hex[:16], "type": "function",
                                  "function": {"name": "knowledge_retriever",
                                               "arguments": json.dumps({"query": _query_from(messages)}, ensure_ascii=False)}}]
        finish = "tool_calls"
    else:
        message["content"] = _ANSWER_TEXT[:max(plan["content"], 1)]
        finish = "stop"
    out_tok = plan["think"] + plan["content"]
    return JSONResponse({"id": "chatcmpl-mock-" + uuid.uuid4().hex[:12], "object": "chat.completion",
                         "created": int(time.time()), "model": model,
                         "choices": [{"index": 0, "message": message, "finish_reason": finish}],
                         "usage": {"prompt_tokens": plan["in_tok"], "completion_tokens": out_tok,
                                   "total_tokens": plan["in_tok"] + out_tok}})


@app.get("/v1/models")
async def models():
    return {"object": "list", "data": [{"id": "qwen3-30b-a3b", "object": "model"}]}


@app.get("/stats")
async def stats():
    return STATS


def main():
    global PROFILE
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8010)
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--profile", default=str(Path(__file__).with_name("mock_profile.json")))
    ap.add_argument("--seed", type=int, default=None)
    args = ap.parse_args()
    if args.seed is not None:
        random.seed(args.seed)
    PROFILE = json.loads(Path(args.profile).read_text(encoding="utf-8"))
    print(f"mock_llm: profile={args.profile} source={PROFILE.get('source')}")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
