"""并发梯度压测（干净窗口重跑版）

6 个核心只读端点 × 4 档并发（10/50/100/200），另附问答端点延迟记录。
零依赖（urllib + 线程池），输出 归档/技术报告/数据/性能压测.json。

用法：
    ./.venv/Scripts/python.exe "归档/技术报告/评测脚本/压测_并发梯度.py"
"""

from __future__ import annotations

import concurrent.futures as cf
import json
import time
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / "归档/技术报告/数据/性能压测.json"

BASE = "http://127.0.0.1:8000"

ENDPOINTS = [
    ("健康检查", "/api/health"),
    ("态势总览", "/api/situation/overview"),
    ("情报列表", "/api/vulns?page=1&page_size=20"),
    ("图谱总览", "/api/graph/overview"),
    ("情报流水", "/api/collect/feed"),
    ("知识库统计", "/api/stats/knowledge-base"),
]

CONC = [10, 50, 100, 200]

QUESTIONS = [
    "mlflow 有哪些高危漏洞",
    "CVE-2021-43831 怎么修复",
    "AI 模型投毒有哪些案例",
    "Gradio 组件的历史漏洞",
    "prompt injection 相关的 CVE",
    "近期 AI 供应链安全事件",
]


def get(path: str) -> tuple[float, bool]:
    t0 = time.perf_counter()
    try:
        req = urllib.request.Request(BASE + path, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=60) as r:
            r.read()
            return (time.perf_counter() - t0) * 1000, r.status == 200
    except Exception:
        return (time.perf_counter() - t0) * 1000, False


def post_qa(question: str) -> tuple[float, bool]:
    qs = urllib.parse.urlencode({"question": question, "session_id": "loadtest"})
    t0 = time.perf_counter()
    try:
        req = urllib.request.Request(
            f"{BASE}/api/qa/ask?{qs}", data=b"", method="POST",
            headers={"Accept": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=60) as r:
            body = r.read()
            ok = r.status == 200 and b'"answer"' in body
            return (time.perf_counter() - t0) * 1000, ok
    except Exception:
        return (time.perf_counter() - t0) * 1000, False


def pct(sorted_lat: list[float], p: float) -> float:
    if not sorted_lat:
        return 0.0
    k = min(int(len(sorted_lat) * p / 100), len(sorted_lat) - 1)
    return round(sorted_lat[k], 1)


def main() -> None:
    api_rows: list[dict] = []

    for c in CONC:
        for name, path in ENDPOINTS:
            n = max(50, c * 3)
            with cf.ThreadPoolExecutor(max_workers=c) as ex:
                t0 = time.perf_counter()
                results = list(ex.map(lambda _: get(path), range(n)))
                wall = time.perf_counter() - t0
            lats = sorted(l for l, _ in results)
            ok = sum(1 for _, good in results if good)
            row = {
                "endpoint": name,
                "path": path,
                "concurrency": c,
                "requests": n,
                "success": ok,
                "errors": n - ok,
                "qps": round(n / wall, 1),
                "avg_ms": round(sum(lats) / len(lats), 1),
                "p50_ms": pct(lats, 50),
                "p95_ms": pct(lats, 95),
                "p99_ms": pct(lats, 99),
            }
            api_rows.append(row)
            print(f"c={c:<3} {name:<6} {ok}/{n}  QPS {row['qps']:<6} "
                  f"P50 {row['p50_ms']:<7} P95 {row['p95_ms']:<7} P99 {row['p99_ms']}")

        time.sleep(2)

    qa_rows = []
    for q in QUESTIONS:
        lat, ok = post_qa(q)
        qa_rows.append({"question": q, "ok": ok, "latency_ms": round(lat, 1)})
        print(f"QA {'OK ' if ok else 'ERR'} {round(lat)}ms  {q}")

    OUT.write_text(
        json.dumps(
            {"generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
             "api": api_rows, "qa": qa_rows},
            ensure_ascii=False, indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\n已输出：{OUT}")


if __name__ == "__main__":
    main()
