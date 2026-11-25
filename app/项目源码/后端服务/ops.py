"""
系统运维与自愈 · 观测接口
================================================================
回答四个问题：
    1. 链路上的每一环现在健康吗（编排节点 + 采集器）
    2. 机器资源水位在哪（psutil 真实采样）
    3. 最近 24 小时的任务队列与延迟趋势如何
    4. 刚刚发生了哪些自愈动作与告警

所有数字都来自真实运行数据：
    · 编排节点 → ops_history 环形缓冲 + BaseAgent.health()
    · 采集器   → collection_logs 聚合
    · 资源     → psutil
    · 依赖     → /api/health 的探测结果
没有一处是为了好看编的：没跑过的节点显示「未执行」而不是 0%。
"""

from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, Query
from sqlalchemy import case, func, select

from ..知识库.database import get_session
from ..知识库.models import CollectionLog
from . import ops_history

router = APIRouter(tags=["运维"], prefix="/api/ops")

# 进程启动时刻：用于算后端运行时长（模块导入即进程已起来）
_PROCESS_START = time.time()

# LangGraph 状态机的节点顺序（与 orchestrator.build() 保持一致）
#
# 第三个元素是「执行记录里可能出现的 agent 名」列表——
# ops_history.append_history 用的是 Agent 类的 name 属性，
# 而这里展示的是流水线节点的 key，两者不一定同字：
#   key="enrich"（流水线节点）vs Agent.name="enrichment"（类定义）
# 早期只按 key 查，导致富化 Agent 执行再多也永远显示 runs=0
# （看着像摆设）。这里显式列出别名，查询时逐个尝试。
PIPELINE_NODES = [
    ("monitor", "监测 Agent", "识别新增情报并判定优先级", ("monitor",)),
    ("triage", "优先级判定", "按风险与时效分流", ("triage",)),
    ("enrich", "富化 Agent", "七维富化补齐证据链", ("enrich", "enrichment")),
    ("skip_enrich", "跳过富化", "低价值条目直接放行", ("skip_enrich",)),
    ("qa", "问答 Agent", "检索增强生成回答", ("qa",)),
    ("supervisor", "主管 Agent", "失败可恢复性判定与重试", ("supervisor",)),
    ("reflect", "反思重试", "不达标结果自动重跑", ("reflect",)),
]

# 每个 Agent 的**真实触发条件**。
# 为什么需要这张表：编排器里 monitor 只在全流程（pipeline）工作流才执行
# （见 orchestrator._node_monitor——qa/enrich 工作流跳过采集，因为
# 采集耗时以分钟计，而问答/富化本身只要几秒）。
# 所以「数据一直在采集」与「监测 Agent 有执行记录」**并不矛盾**：
# 定时采集走调度器自己的链路，不经过编排器的monitor 节点。
# 不把这件事讲清楚，看板就成了「摆设」——用户只会认为功能没接上。
TRIGGER_HINT = {
    "monitor": "仅在全流程工作流（/api/orchestration/run?workflow_type=pipeline）触发；定时采集走调度器独立链路，不经过本节点",
    "triage": "随 monitor 之后执行；monitor 跳过时本节点仅做占位判断",
    "enrich": "采集后自动富化（调度器 _auto_enrich）或手动跑 enrich 工作流时触发",
    "skip_enrich": "优先级判定为低价值时走条件边，否则不进入",
    "qa": "智能问答页提问、或跑 qa 工作流时触发",
    "supervisor": "富化/问答分支汇聚后统一评估可恢复性",
    "reflect": "主管判定结果不达标时重跑；达标则不触发",
}

# 执行记录是**进程内环形缓冲**（ops_history，500 条/300 条），
# 后端重启即清零。所以「未执行」有两种含义，必须让用户能分辨：
#   1. 本次启动后确实还没触发过
#   2. 触发过，但记录随重启丢失了
RECORD_SCOPE = "进程内记录，后端重启后清零（最近 500 次执行 / 300 条事件）"


@router.get("/components", summary="链路组件健康")
async def ops_components() -> dict[str, Any]:
    """编排节点 + 采集器的执行次数 / 成功率 / 耗时 / 熔断状态"""
    from ..数据采集.registry import get_collectors
    from ..数据采集.scheduler import get_scheduler
    from ..问答模块.graph import get_graph_retriever

    agg = ops_history.stats_by_agent()

    # ---- 编排节点 ----
    import datetime as dt

    from ..智能体编排.orchestrator import get_workflow

    agents_health: dict[str, dict[str, Any]] = {}
    try:
        for a in get_workflow().agent_health():
            agents_health[a["name"]] = a
    except Exception:  # noqa: BLE001
        agents_health = {}

    nodes: list[dict[str, Any]] = []
    for key, label, desc, agent_names in PIPELINE_NODES:
        # 按别名依次取，第一个有记录的即采用（避免双计数）
        st = next((agg[a] for a in agent_names if a in agg), None)
        # 熔断/心跳也走同一套别名——Agent.health() 返回的 name 是
        # 类定义的 "enrichment"，而 agents_health 是按 name 建索引的
        health = next(
            (agents_health[a] for a in agent_names if a in agents_health), {}
        )
        runs = st["runs"] if st else 0
        # 「多久前触发」的数据源：
        #   last_ts   —— ops_history 里该节点最后一条记录的时间戳（所有节点都有）
        #   heartbeat —— BaseAgent 实例的心跳（仅 3 个 BaseAgent 有，UTC 带时区）
        # 两者统一换算成「本地时区的 epoch 秒」再比大小——
        # **不能直接比ISO 字符串**：心跳是 UTC（...T17:22:36+00:00），
        # 而 history 转出来的是本地时间（...T01:22:36），两者字符串比较
        # 与真实先后无关，UTC 时钟到 20:00 之后还会反向选错。
        last_ts: float | None = st["last_ts"] if st else None
        heartbeat = health.get("last_heartbeat")
        if heartbeat:
            try:
                hb_ts = dt.datetime.fromisoformat(str(heartbeat)).timestamp()
            except ValueError:
                hb_ts = None
            if hb_ts is not None and (last_ts is None or hb_ts > last_ts):
                last_ts = hb_ts
        # 前端只要相对时间，本地 naive ISO 足够（JS new Date() 会按本地时区解析）
        last_run_at = (
            time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(last_ts))
            if last_ts
            else None
        )
        nodes.append(
            {
                "key": key,
                "name": label,
                "desc": desc,
                "kind": "agent",
                "runs": runs,
                "success_rate": st["success_rate"] if st else None,
                "avg_ms": st["avg_ms"] if st else None,
                "last_ms": st["last_ms"] if st else None,
                "retries": st["retries"] if st else 0,
                "circuit_open": health.get("circuit_open", False),
                "healthy": health.get("healthy"),
                "last_heartbeat": health.get("last_heartbeat"),
                "last_run_at": last_run_at,
                "last_error": st["last_error"] if st else None,
                # runs=0 时前端要能说清「为什么是0」——
                # 是设计上就不该触发，还是真的在等首次触发。
                # 不给这个区分，用户只能理解成「摆设」。
                "trigger": TRIGGER_HINT.get(key),
                "record_scope": RECORD_SCOPE,
            }
        )

    # ---- 调度器 ----
    sched = get_scheduler()
    nodes.append(
        {
            "key": "scheduler",
            "name": "调度器",
            "desc": "9 个数据源的定时与手动采集",
            "kind": "scheduler",
            "running": sched.running,
            "paused": sched.paused,
        }
    )

    # ---- 采集器（逐源真实成功率与延迟）----
    async with get_session() as session:
        rows = (
            await session.execute(
                select(
                    CollectionLog.source_code,
                    func.count().label("runs"),
                    func.sum(
                        case((CollectionLog.status == "success", 1), else_=0)
                    ).label("ok"),
                    func.avg(CollectionLog.latency_ms).label("avg_ms"),
                    func.sum(CollectionLog.new_count).label("new_items"),
                )
                .where(CollectionLog.started_at.isnot(None))
                .group_by(CollectionLog.source_code)
            )
        ).all()

    collectors_meta = get_collectors()
    log_by_code = {r[0]: r for r in rows}

    collector_cards: list[dict[str, Any]] = []
    for code, meta in collectors_meta.items():
        rec = log_by_code.get(code)
        runs = int(rec[1]) if rec else 0
        ok = int(rec[2] or 0) if rec else 0
        collector_cards.append(
            {
                "key": code,
                "name": getattr(meta, "display_name", None)
                or getattr(meta, "name", code),
                "desc": getattr(meta, "category", "") or "数据源",
                "kind": "collector",
                "runs": runs,
                "success_rate": round(ok / runs * 100, 1) if runs else None,
                "avg_ms": round(float(rec[3]), 0) if rec and rec[3] is not None else None,
                "new_items": int(rec[4] or 0) if rec else 0,
            }
        )
    collector_cards.sort(key=lambda x: -x["runs"])

    # ---- 依赖健康 ----
    try:
        graph = get_graph_retriever().health_check()
    except Exception:  # noqa: BLE001
        graph = {"available": False}

    return {
        "nodes": nodes,
        "collectors": collector_cards,
        "graph": graph,
        "scheduler": {
            "running": sched.running,
            "paused": sched.paused,
            "count": len(collectors_meta),
        },
        "totals": {
            # 用 .get：调度器卡没有 runs 字段（它展示 running/paused 而非执行次数）
            "agent_runs": sum(n.get("runs") or 0 for n in nodes),
            "collector_runs": sum(c["runs"] for c in collector_cards),
            "executed_nodes": sum(1 for n in nodes if (n.get("runs") or 0) > 0),
            "total_nodes": len(nodes),
        },
    }


@router.get("/resources", summary="资源水位")
async def ops_resources() -> dict[str, Any]:
    """CPU / 内存 / 磁盘 / 数据库连接——psutil 真实采样"""
    import psutil

    cpu = psutil.cpu_percent(interval=None)
    # 内存：取「已用/总量」，与任务管理器口径一致
    vm = psutil.virtual_memory()
    disk = psutil.disk_usage("/") if psutil.os.name != "nt" else psutil.disk_usage(
        psutil.drive_letter_root() if hasattr(psutil, "drive_letter_root") else "C:\\"
    )

    # 数据库连接数：真实查询 pg_stat_activity
    db_conn = None
    db_max = None
    db_active = None
    try:
        from sqlalchemy import text as sql_text

        async with get_session() as session:
            db_conn = int(
                (
                    await session.execute(
                        sql_text("SELECT count(*) FROM pg_stat_activity")
                    )
                ).scalar()
                or 0
            )
            db_active = int(
                (
                    await session.execute(
                        sql_text(
                            "SELECT count(*) FROM pg_stat_activity "
                            "WHERE state = 'active'"
                        )
                    )
                ).scalar()
                or 0
            )
            db_max = int(
                (
                    await session.execute(
                        sql_text("SHOW max_connections")
                    )
                ).scalar()
                or 100
            )
    except Exception:  # noqa: BLE001
        db_conn = None

    # 进程运行时长：用模块导入时刻做基准。
    # 原来用 psutil.boot_time()（系统开机时间）有两个问题：
    #   1. 语义不对——运维面板要的是「后端跑了多久」，不是「机器开了多久」
    #   2. Windows 上常因权限不足抛异常 → uptime_seconds 恒为 None
    uptime = int(time.time() - _PROCESS_START)

    return {
        "cpu": {"percent": round(cpu, 1), "cores": psutil.cpu_count()},
        "memory": {
            "percent": round(vm.percent, 1),
            "used_gb": round(vm.used / 1024**3, 1),
            "total_gb": round(vm.total / 1024**3, 1),
        },
        "disk": {
            "percent": round(disk.percent, 1),
            "used_gb": round(disk.used / 1024**3, 1),
            "total_gb": round(disk.total / 1024**3, 1),
        },
        "database": {
            "connections": db_conn,
            "active": db_active,
            "max_connections": db_max,
        },
        "process": {"uptime_seconds": uptime},
    }


@router.get("/timeline", summary="队列与延迟趋势")
async def ops_timeline(
    buckets: int = Query(24, ge=6, le=48, description="时间桶数量（每小时一桶）"),
) -> dict[str, Any]:
    """编排执行的按小时趋势 + 采集执行趋势"""
    tl = ops_history.timeline(buckets=buckets, span_hours=buckets)

    # 采集侧：近N 小时的采集执行与失败
    import datetime as dt

    from sqlalchemy import cast, Date as SqlDate  # noqa: F401  (类型对齐用)

    # 窗口起点也用本地时区，与下面的分桶键保持一致。
    # 原来这里用 utc 而分桶用 utc（两处一致但都错）；
    # 改成全本地时区后，labels（来自 ops_history.localtime）与
    # per_hour 的键才真正对齐。
    since = dt.datetime.now().astimezone() - dt.timedelta(hours=buckets)
    async with get_session() as session:
        rows = (
            await session.execute(
                select(
                    CollectionLog.started_at,
                    CollectionLog.status,
                    CollectionLog.latency_ms,
                ).where(CollectionLog.started_at >= since)
            )
        ).all()

    per_hour: dict[str, dict[str, Any]] = {}
    for ts, status, lat in rows:
        if ts is None:
            continue
        # 必须用**本地时区**分桶，不能用 UTC：
        # labels 来自 ops_history.timeline()（本地时区），
        # 若这里按 UTC 分桶，两套时区混用会整体错位 8 小时——
        # 表现为「刚跑的采集不出现在趋势图最新桶里」，右侧大片空白。
        key = ts.astimezone().strftime("%m-%d %H:00")
        slot = per_hour.setdefault(key, {"runs": 0, "fails": 0, "lat": 0.0})
        slot["runs"] += 1
        if status != "success":
            slot["fails"] += 1
        if lat is not None:
            slot["lat"] += float(lat)

    labels: list[str] = []
    c_runs: list[int] = []
    c_fails: list[int] = []
    c_avg: list[float | None] = []
    for i in range(buckets):
        lb = tl["labels"][i] if i < len(tl["labels"]) else ""
        labels.append(lb)
        slot = per_hour.get(lb)
        c_runs.append(slot["runs"] if slot else 0)
        c_fails.append(slot["fails"] if slot else 0)
        c_avg.append(
            round(slot["lat"] / slot["runs"], 0) if slot and slot["runs"] else None
        )

    return {
        "labels": labels,
        "agent_runs": tl["runs"],
        "agent_fails": tl["fails"],
        "collector_runs": c_runs,
        "collector_fails": c_fails,
        "avg_latency_ms": c_avg,
        "totals": {
            "agent_runs": tl["total_runs"],
            "agent_fails": tl["total_fails"],
            "collector_runs": sum(c_runs),
            "collector_fails": sum(c_fails),
        },
    }


@router.get("/events", summary="自愈事件与告警")
async def ops_events(
    limit: int = Query(60, ge=1, le=300),
) -> dict[str, Any]:
    """最近的熔断 / 失败 / 重试事件，以及由采集失败派生的告警"""
    events = ops_history.snapshot_events(limit)

    # 采集失败也进告警中心：取近 24 小时失败次数最多的源
    import datetime as dt

    since = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=24)
    async with get_session() as session:
        fail_rows = (
            await session.execute(
                select(
                    CollectionLog.source_code,
                    func.count().label("fails"),
                    func.max(CollectionLog.finished_at).label("last_at"),
                )
                .where(CollectionLog.status != "success")
                .where(CollectionLog.started_at >= since)
                .group_by(CollectionLog.source_code)
                .order_by(func.count().desc())
                .limit(8)
            )
        ).all()

    from ..数据采集.registry import get_collectors

    meta = get_collectors()
    alerts = []
    for code, n, ts in fail_rows:
        alerts.append(
            {
                "source": code,
                "name": getattr(meta.get(code), "display_name", None)
                or getattr(meta.get(code), "name", code),
                "fails": int(n),
                "level": "error" if int(n) >= 3 else "warn",
                "last_at": ts.isoformat() if ts else None,
                "last_ts": ts.timestamp() if ts else 0.0,
            }
        )

    # 采集侧事件也混进时间线，让「自愈事件」覆盖采集失败
    for a in alerts:
        events.append(
            {
                "kind": "collector_failure",
                "title": f"{a['name']}：24 小时内失败 {a['fails']} 次",
                "detail": "来源：collection_logs 聚合",
                "level": a["level"],
                "ts": a["last_ts"],
            }
        )
    events.sort(key=lambda e: e.get("ts") or 0, reverse=True)

    counts = {"error": 0, "warn": 0, "info": 0}
    for e in events:
        lv = e.get("level")
        if lv in counts:
            counts[lv] += 1

    return {"events": events[:limit], "alerts": alerts, "counts": counts}