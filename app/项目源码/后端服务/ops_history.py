"""
运维观测：运行历史与自愈事件记录
================================================================
为什么需要它：
    编排器（智能体编排/orchestrator.py）里已经有完整的自愈机制——
    熔断器、心跳、反思重试、主管重路由——但这些动作只落在
    structlog 的文本日志里。文本日志没法回答「富化 Agent 最近
    成功了多少次、平均耗时多少、什么时候熔断过」。

    运维页要的正是这些数字，所以这里把关键节点的结构化数据
    抽出来，进程内用环形缓冲保存。

为什么用内存缓冲而不是建表：
    · 演示与答辩场景只需要「最近发生了什么」，重启后清空可接受；
    · 不动数据库结构，零 DDL 风险；
    · 内存占用有上界（默认各 500 条），不会涨爆。

将来若要长期审计，只需把 append_history / append_event 换成
写表，接口契约不变。
"""

from __future__ import annotations

import threading
import time
from collections import deque
from typing import Any

# ---- 环形缓冲容量（条）----
MAX_HISTORY = 500
MAX_EVENTS = 300

# 历史记录：{"node": 节点名, "agent": Agent 名, "ok": 是否成功,
#            "ms": 耗时毫秒, "error": 错误摘要, "retry": 重试次数,
#            "ts": 时间戳}
_history: deque[dict[str, Any]] = deque(maxlen=MAX_HISTORY)

# 自愈事件：{"kind": 事件类型, "title": 一句话, "detail": 详情,
#            "level": info/warn/error, "ts": 时间戳}
_events: deque[dict[str, Any]] = deque(maxlen=MAX_EVENTS)

_lock = threading.Lock()

# 事件类型 → 展示层级（前端按这个上色）
_EVENT_LEVEL = {
    "circuit_open": "error",
    "agent_failed": "warn",
    "agent_recovered": "info",
    "supervisor_retry": "warn",
    "reflect_retry": "info",
    "degraded": "warn",
    "dependency_down": "error",
    "dependency_up": "info",
}


def append_history(
    node: str,
    ok: bool,
    ms: float,
    *,
    agent: str | None = None,
    error: str | None = None,
    retry: int = 0,
) -> None:
    """记录一次节点执行结果"""
    with _lock:
        _history.append(
            {
                "node": node,
                "agent": agent or node,
                "ok": ok,
                "ms": round(ms, 1),
                "error": (error or "")[:180] or None,
                "retry": retry,
                "ts": time.time(),
            }
        )


def append_event(
    kind: str,
    title: str,
    detail: str = "",
    *,
    level: str | None = None,
) -> None:
    """记录一条自愈/告警事件"""
    with _lock:
        _events.append(
            {
                "kind": kind,
                "title": title[:120],
                "detail": detail[:400],
                "level": level or _EVENT_LEVEL.get(kind, "info"),
                "ts": time.time(),
            }
        )


def snapshot_history(limit: int = 200) -> list[dict[str, Any]]:
    with _lock:
        return list(_history)[-limit:]


def snapshot_events(limit: int = 100) -> list[dict[str, Any]]:
    with _lock:
        # 最新的在前——运维页看的是「刚发生了什么」
        return list(reversed(_events))[:limit]


def stats_by_agent() -> dict[str, dict[str, Any]]:
    """按 Agent 聚合：执行次数 / 成功次数 / 成功率 / 平均耗时 / 最近耗时"""
    with _lock:
        rows = list(_history)

    agg: dict[str, dict[str, Any]] = {}
    for r in rows:
        a = agg.setdefault(
            r["agent"],
            {"runs": 0, "ok": 0, "fail": 0, "total_ms": 0.0, "last_ms": 0.0,
             "retries": 0, "last_ts": 0.0, "last_error": None},
        )
        a["runs"] += 1
        if r["ok"]:
            a["ok"] += 1
        else:
            a["fail"] += 1
            a["last_error"] = r.get("error")
        a["total_ms"] += r["ms"]
        a["last_ms"] = r["ms"]
        a["retries"] += r.get("retry", 0)
        if r["ts"] >= a["last_ts"]:
            a["last_ts"] = r["ts"]

    for a in agg.values():
        a["success_rate"] = round(a["ok"] / a["runs"] * 100, 1) if a["runs"] else None
        a["avg_ms"] = round(a["total_ms"] / a["runs"], 1) if a["runs"] else None
        a["avg_ms"] = round(a["avg_ms"], 1)
    return agg


def timeline(buckets: int = 24, span_hours: int = 24) -> dict[str, Any]:
    """按小时聚合出趋势：执行数 / 失败数 / 平均耗时"""
    now = time.time()
    # 桶必须**以当前小时为最后一桶**，而不是从「24 小时前的整点」起排。
    # 原实现从 start 整点对齐后排 24 个桶，末桶落在 23:00，
    # **当前这一小时的桶根本不在窗口内**——表现为
    # 「刚跑完采集，趋势图最新桶却是空的，看起来不实时」。
    current_hour = (now // 3600) * 3600
    aligned = current_hour - (buckets - 1) * 3600

    with _lock:
        rows = [r for r in _history if r["ts"] >= aligned]

    labels: list[str] = []
    runs: list[int] = []
    fails: list[int] = []
    avg_ms: list[float | None] = []

    for i in range(buckets):
        lo = aligned + i * 3600
        hi = lo + 3600
        seg = [r for r in rows if lo <= r["ts"] < hi]
        labels.append(time.strftime("%m-%d %H:00", time.localtime(lo)))
        runs.append(len(seg))
        fails.append(sum(1 for r in seg if not r["ok"]))
        avg_ms.append(
            round(sum(r["ms"] for r in seg) / len(seg), 1) if seg else None
        )

    return {
        "labels": labels,
        "runs": runs,
        "fails": fails,
        "avg_ms": avg_ms,
        "total_runs": sum(runs),
        "total_fails": sum(fails),
    }