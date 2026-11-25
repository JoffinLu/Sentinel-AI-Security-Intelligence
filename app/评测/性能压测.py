"""性能压测：核心只读接口 × 并发梯度（10/50/100/200）。

输出 JSON 到 归档/技术报告/数据/性能压测.json，供技术报告引用。
用法：./.venv/Scripts/python.exe 评测/性能压测.py
"""

from __future__ import annotations

import asyncio
import json
import os
import statistics
import time
from pathlib import Path

import httpx

BASE = "http://127.0.0.1:8000"
OUT = Path("归档/技术报告/数据/性能压测.json")

os.environ["NO_PROXY"] = "127.0.0.1,localhost"
os.environ["no_proxy"] = "127.0.0.1,localhost"

# (展示名, 方法, 路径, 请求体)
ENDPOINTS = [
    ("健康检查", "GET", "/api/health", None),
    ("态势总览", "GET", "/api/situation/overview", None),
    ("情报列表", "GET", "/api/vulns?page=1&page_size=20", None),
    ("图谱总览", "GET", "/api/graph/overview", None),
    ("情报流水", "GET", "/api/collect/feed", None),
    ("知识库统计", "GET", "/api/stats/knowledge-base", None),
]

CONCURRENCY = [10, 50, 100, 200]
WARMUP = 3


def pct(sorted_lat: list[float], p: float) -> float:
    if not sorted_lat:
        return 0.0
    k = max(0, min(len(sorted_lat) - 1, round(p * len(sorted_lat)) - 1))
    return sorted_lat[k]


async def run_level(
    client: httpx.AsyncClient, name: str, method: str, path: str, body, level: int
) -> dict:
    total = min(600, max(50, level * 3))

    for _ in range(WARMUP):
        try:
            await client.request(method, BASE + path, json=body)
        except Exception:
            pass

    lat: list[float] = []
    errors = 0
    sem = asyncio.Semaphore(level)

    async def one() -> None:
        nonlocal errors
        async with sem:
            t0 = time.perf_counter()
            try:
                r = await client.request(method, BASE + path, json=body)
                if r.status_code >= 400:
                    errors += 1
            except Exception:
                errors += 1
            lat.append((time.perf_counter() - t0) * 1000)

    t0 = time.perf_counter()
    await asyncio.gather(*[one() for _ in range(total)])
    elapsed = time.perf_counter() - t0

    lat.sort()
    ok = len(lat) - errors
    return {
        "endpoint": name,
        "path": path,
        "concurrency": level,
        "requests": len(lat),
        "success": ok,
        "errors": errors,
        "qps": round(ok / elapsed, 1) if elapsed else 0,
        "avg_ms": round(statistics.fmean(lat), 1) if lat else 0,
        "p50_ms": round(pct(lat, 0.50), 1),
        "p95_ms": round(pct(lat, 0.95), 1),
        "p99_ms": round(pct(lat, 0.99), 1),
    }


async def qa_probe(client: httpx.AsyncClient) -> list[dict]:
    """问答链路：串行 6 问（LLM 网关限流，不做并发压测）。"""
    questions = [
        "mlflow 有哪些高危漏洞",
        "CVE-2021-43831 怎么修复",
        "AI 模型投毒有哪些案例",
        "Gradio 组件的历史漏洞",
        "prompt injection 相关的 CVE",
        "近期 AI 供应链安全事件",
    ]
    out = []
    for q in questions:
        t0 = time.perf_counter()
        try:
            r = await client.post(BASE + "/api/qa/ask", params={"question": q}, timeout=60)
            dt = (time.perf_counter() - t0) * 1000
            ok = r.status_code < 400
            out.append({"question": q, "ok": ok, "latency_ms": round(dt, 0)})
        except Exception as e:
            out.append({"question": q, "ok": False, "latency_ms": round((time.perf_counter() - t0) * 1000, 0), "error": str(e)[:80]})
    return out


async def main() -> None:
    results: list[dict] = []
    async with httpx.AsyncClient(
        timeout=30.0,
        limits=httpx.Limits(max_connections=250, max_keepalive_connections=250),
        trust_env=False,
    ) as client:
        r = await client.get(BASE + "/api/health")
        if r.status_code != 200:
            raise SystemExit(f"后端不可用: {r.status_code}")
        print("后端健康，开始压测\n")
        for name, method, path, body in ENDPOINTS:
            for level in CONCURRENCY:
                row = await run_level(client, name, method, path, body, level)
                results.append(row)
                print(
                    f"{name:<6} c={level:<3} qps={row['qps']:<7}"
                    f" p50={row['p50_ms']:<8} p95={row['p95_ms']:<8}"
                    f" p99={row['p99_ms']:<8} err={row['errors']}"
                )
        print("\n问答链路（串行）：")
        qa = await qa_probe(client)
        for q in qa:
            print(f"  {'OK ' if q['ok'] else 'ERR'} {q['latency_ms']:>7.0f} ms  {q['question']}")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(
        json.dumps({"generated_at": time.strftime("%Y-%m-%d %H:%M:%S"), "api": results, "qa": qa},
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\n结果已写入 {OUT}")


if __name__ == "__main__":
    asyncio.run(main())
