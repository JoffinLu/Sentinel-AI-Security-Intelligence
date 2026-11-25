"""
态势总览聚合接口
================================================================
一个接口返回某时间范围内的全部态势数据（指标卡 / 趋势 / 分布 /
最新高危 / 流水线 / 情报源健康）。

为什么要单独一个接口：
    态势页要在**一个时间范围**下展示七八种口径的数据。
    若前端分别调 6~7 个现有接口，每个接口都得自己支持时间参数，
    且会出现"不同接口的时间边界不一致"的诡异问题
    （比如分类分布算的是 7 天、趋势算的是 30 天）。
    服务端一次聚合，前端只负责画，边界天然一致。

时间口径：
    `ingest_at`（入库时间）而不是 `published_at`——
    我们要看的是"系统采到了多少"，不是"漏洞是什么时候发布的"。
    这是"监测时效性"指标的口径，和采集延迟指标不冲突。

分桶规则：
    hours <= 48  → 按小时分桶
    否则         → 按天分桶
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from fastapi import APIRouter, Query
from sqlalchemy import case, func, select

from ..知识库.database import get_session
from ..知识库.models import (
    CollectionLog,
    Paper,
    Policy,
    Source,
    Vulnerability,
)

router = APIRouter(tags=["态势"], prefix="/api/situation")

# 高危档位（用于"高危占比"与趋势的高危序列）
_HIGH_SEVERITIES = ("CRITICAL", "HIGH")


def _range_start(hours: int | None) -> dt.datetime | None:
    """None 表示不限时间（全周期）"""
    if hours is None:
        return None
    return dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=hours)


def _bucket_expr(bucket: str):
    """按小时或按天分桶。PostgreSQL 的 date_trunc 直接在库里分，
    比把数据拉回 Python 再分组快得多。"""
    unit = "hour" if bucket == "hour" else "day"
    return func.date_trunc(unit, Vulnerability.ingest_at)


def _fill_bucket_gaps(
    rows: list[tuple[dt.datetime, int, int, int]],
    start: dt.datetime | None,
    bucket: str,
) -> list[tuple[str, int, int, int]]:
    """把 GROUP BY 的结果补成连续时间轴。

    **为什么必须补**：`GROUP BY date_trunc(...)` 只返回**有数据**的桶。
    采集器不是每小时都跑（RSS 1 小时、NVD 6 小时、ATLAS 7 天），
    所以「24 小时」档实际常只有 4~6 个桶。ECharts 的 category 轴
    等宽摆放这些桶→中间 18 小时的真空档被压成一格，
    **曲线在视觉上彻底失真**（看着像"这 6 小时涨了 300 条"）。

    补齐后：
      · 每个时间位置都有一格，没数据就是 0（曲��平直，不误导）
      · 末桶固定为「当前小时 / 当天」——否则此刻若恰好没采集，
        曲线右端会停在最后一次采集时刻，看着像"数据不更新了"
    **时区（易踩坑）**：`ingest_at` 是 `timestamptz`，而 PG 会话时区是
    `Etc/UTC`（asyncpg 默认），所以 `date_trunc('hour', ingest_at)`
    返回的是 **UTC 桶**。若直接 `strftime("%m-%d %H:00")`，
    本地 12:00 的数据会被标成 "04:00"——**整条轴差8 小时**，
    用户看到的时钟和图对不上。这里统一 `.astimezone()` 转本地再格式化。
    """
    step = dt.timedelta(hours=1) if bucket == "hour" else dt.timedelta(days=1)
    # 末桶按**本地**当前小时/天对齐——用户看的是本地时钟
    now = dt.datetime.now().astimezone()

    # 末桶：当前小时的对齐值（date_trunc 后的桶起点）
    end_bucket = now.replace(minute=0, second=0, microsecond=0)
    if bucket == "day":
        end_bucket = end_bucket.replace(hour=0)
        # 今天还没过完，若库里已有今天的数据，末桶就是今天
        if start is not None and start > end_bucket:
            return []
    elif start is not None and start > end_bucket:
        return []

    # 起点：给定范围则取其桶对齐值，否则取最早有数据的那个桶
    # start 来自 `_range_start()`（UTC），转本地后才能和上面的桶同处一个时区
    if start is not None:
        cur_bucket = start.astimezone().replace(minute=0, second=0, microsecond=0)
        if bucket == "day":
            cur_bucket = cur_bucket.replace(hour=0)
    elif rows:
        # 同样要归一化到本地零点——与下面 table 的键口径一致
        cur_bucket = rows[0][0].astimezone()
        if bucket == "day":
            cur_bucket = cur_bucket.replace(hour=0)
        else:
            cur_bucket = cur_bucket.replace(minute=0, second=0, microsecond=0)
    else:
        return []

    # 全周期且跨度过大时不补——几千个空格毫无意义，还会让接口变慢
    span = (end_bucket - cur_bucket) / step
    fmt = "%m-%d %H:00" if bucket == "hour" else "%m-%d"
    if span > 400:
        # 跨度太大不补，但标签仍要转到本地，否则用户看到的时钟对不上
        out2: list[tuple[str, int, int, int]] = []
        merged: dict[str, list[int]] = {}
        for b, new_c, high_c, enr_c in rows:
            local = b.astimezone()
            key = (local.replace(hour=0, minute=0, second=0, microsecond=0)
                   if bucket == "day" else local).strftime(fmt)
            acc = merged.setdefault(key, [0, 0, 0])
            acc[0] += int(new_c or 0)
            acc[1] += int(high_c or 0)
            acc[2] += int(enr_c or 0)
        for key, (n, h, e) in merged.items():
            out2.append((key, n, h, e))
        return out2

    # 键统一用**本地时区**，与上面的 cur_bucket / end_bucket 同一坐标系，
    # 否则查表全部 miss，真实数据会被当成 0
    #
    # 按天分桶有个额外坑：`date_trunc('day', ...)` 是在 **UTC** 上切的，
    # UTC「10-09 全天」转本地是 10-09 08:00 ~ 10-10 07:59，横跨两个本地日。
    # 直接 astimezone 后当键，会既对不上循环、又把一天的数据错记到相邻日。
    # 所以天粒度的键一律**归一化到本地零点**。
    def _key(value: dt.datetime) -> dt.datetime:
        local = value.astimezone()
        return local.replace(hour=0, minute=0, second=0, microsecond=0) if bucket == "day" else local

    table: dict[dt.datetime, list[tuple[int, int, int]]] = {}
    for b, new_c, high_c, enr_c in rows:
        k = _key(b)
        # 同一本地日可能出现多个 UTC 桶（跨日边界的那个），累加而不是覆盖
        prev = table.get(k, (0, 0, 0))
        table[k] = (
            prev[0] + int(new_c or 0),
            prev[1] + int(high_c or 0),
            prev[2] + int(enr_c or 0),
        )
    out: list[tuple[str, int, int, int]] = []
    cur = cur_bucket
    while cur <= end_bucket:
        n, h, e = table.get(cur, (0, 0, 0))
        out.append((cur.strftime(fmt), n, h, e))
        cur += step
    return out


def _hour_progress() -> float:
    """当前小时已经过去的比例（0~1，保留 4 位小数）。

    前端据此判断「这一小时的数据是否已经刷新过」——
    24 小时档的桶是整点对齐的，progress 从 0 走到 1 再归零，
    归零的那一刻就是数据推进的时刻。

    与时区无关（只用分钟秒），所以用哪个时区的 now 都一样。
    """
    now = dt.datetime.now(dt.timezone.utc)
    return round(
        (now.minute * 60 + now.second) / 3600.0,
        4,
    )


@router.get("/overview", summary="态势总览聚合")
async def situation_overview(
    hours: int | None = Query(
        None,
        ge=1,
        le=8760,
        description="回看小时数；省略表示全周期",
    ),
) -> dict[str, Any]:
    """态势总览：一次返回时间范围内的全部分析数据"""
    start = _range_start(hours)
    bucket = "hour" if hours is not None and hours <= 48 else "day"

    def scoped(*conditions: Any):
        """在查询上叠加时间范围（start 为 None 时不加条件=全周期）"""
        stmt = select(func.count()).select_from(Vulnerability)
        if start is not None:
            stmt = stmt.where(Vulnerability.ingest_at >= start)
        for cond in conditions:
            stmt = stmt.where(cond)
        return stmt

    async def count_of(*conditions: Any) -> int:
        return int(await session.scalar(scoped(*conditions)) or 0)

    async def avg_of(column: Any) -> float | None:
        stmt = select(func.avg(column)).where(column.isnot(None))
        if start is not None:
            stmt = stmt.where(Vulnerability.ingest_at >= start)
        return await session.scalar(stmt)

    # 一次会话查完所有口径，避免多次往返
    category_rows: list[tuple[str | None, int]] = []
    severity_rows: list[tuple[str | None, int]] = []
    async with get_session() as session:
        sev_expr = func.upper(Vulnerability.severity)
        high_cond = sev_expr.in_(_HIGH_SEVERITIES)

        # ---------- 指标卡 ----------
        total_vuln = await count_of()
        high_count = await count_of(high_cond)
        avg_cvss = await avg_of(Vulnerability.cvss_score)
        enriched_count = await count_of(Vulnerability.enrichment_status == "completed")
        indexed_count = await count_of(Vulnerability.embedding.isnot(None))
        classified_count = await count_of(Vulnerability.ai_category.isnot(None))

        # ---------- 分类分布（severity 同样做大小写归一） ----------
        cat_q = select(Vulnerability.ai_category, func.count()).group_by(
            Vulnerability.ai_category
        )
        sev_q = select(sev_expr, func.count()).group_by(sev_expr)
        bucket_col = _bucket_expr(bucket)
        trend_q = select(
            bucket_col.label("b"),
            func.count().label("new_count"),
            func.sum(case((high_cond, 1), else_=0)).label("high_count"),
            func.sum(
                case((Vulnerability.enrichment_status == "completed", 1), else_=0)
            ).label("enriched_count"),
        )
        if start is not None:
            cat_q = cat_q.where(Vulnerability.ingest_at >= start).where(
                Vulnerability.ai_category.isnot(None)
            )
            sev_q = sev_q.where(Vulnerability.ingest_at >= start).where(
                Vulnerability.severity.isnot(None)
            )
            trend_q = trend_q.where(Vulnerability.ingest_at >= start)

        for name, n in (await session.execute(cat_q)).all():
            category_rows.append((name, n))
        for name, n in (await session.execute(sev_q)).all():
            severity_rows.append((name, n))

        # ---------- 趋势序列 ----------
        # 补齐空桶：GROUP BY 只返回有数据的桶，直接用会让等宽category 轴
        # 把相隔十几小时的桶画成相邻，曲线视觉失真（详见 _fill_bucket_gaps）
        trend_rows = (
            await session.execute(trend_q.group_by(bucket_col).order_by(bucket_col))
        ).all()
        filled = _fill_bucket_gaps(trend_rows, start, bucket)
        trend_buckets = [f[0] for f in filled]
        trend_new = [f[1] for f in filled]
        trend_high = [f[2] for f in filled]
        trend_enriched = [f[3] for f in filled]

        # ---------- 其他知识底座的总量 ----------
        #
        # 刻意**不含 techniques（ATLAS 攻击技术库）**：
        # 该表没有 ingest_at 字段，传入 hours 时无法按时间过滤，
        # 只能返回全量 227 条 —— 实测「24 小时」档里它贡献了 227
        # （真实新增为 0），会让「新增情报」虚高，且与漏洞/论文/政策
        # 的口径不一致（其余三者都按范围过滤）。
        # ATLAS 是整库快照式数据，本身没有"单条技术何时入库"的概念，
        # 与其给一个口径不一致的数字，不如不计入本卡。
        other_counts: dict[str, int] = {}
        for label, model in (
            ("论文", Paper),
            ("政策", Policy),
        ):
            q = select(func.count()).select_from(model)
            if start is not None and hasattr(model, "ingest_at"):
                q = q.where(model.ingest_at >= start)
            other_counts[label] = await session.scalar(q) or 0

        # ---------- 最新高危 Top 10 ----------
        top_q = (
            select(
                Vulnerability.cve_id,
                Vulnerability.title,
                Vulnerability.cvss_score,
                Vulnerability.severity,
                Vulnerability.ai_category,
                Vulnerability.origin_source,
                Vulnerability.published_at,
                Vulnerability.ingest_at,
            )
            .where(func.upper(Vulnerability.severity).in_(_HIGH_SEVERITIES))
            .order_by(Vulnerability.ingest_at.desc())
            .limit(10)
        )
        top_rows = (await session.execute(top_q)).all()

        # ---------- 采集延迟（发布 → 入库）----------
        # 口径与 monitor_overview 保持一致：**只统计时间范围内发布的**情报。
        # 全周期下把多年的历史条目一起平均，会得到「平均 12 天」这种
        # 没有运维意义的数字（老漏洞的 published_at 距今数年）。
        # 采集延迟本身是「增量采集时效」，看的应是近期数据。
        latency_scope = (
            select(Vulnerability.collect_latency_seconds).where(
                Vulnerability.collect_latency_seconds.isnot(None)
            )
        )
        if start is not None:
            latency_scope = latency_scope.where(
                Vulnerability.published_at >= start
            )
        else:
            latency_scope = latency_scope.where(
                Vulnerability.published_at
                >= dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=7)
            )
        lat_rows = (await session.execute(latency_scope)).all()
        valid = sorted(int(v[0]) for v in lat_rows if v[0] is not None and int(v[0]) >= 0)
        # 掐掉极端离群值（个别源的时间戳异常会拖垮均值）
        p95_index = int(len(valid) * 0.95) if valid else 0
        trimmed = [v for v in valid if v <= (valid[p95_index] if valid else 0)]
        latency = {
            # 中位数代表「典型延迟」，均值受长尾影响大，界面以中位数为主
            "median": trimmed[len(trimmed) // 2] if trimmed else 0,
            "avg": round(sum(trimmed) / len(trimmed), 1) if trimmed else 0.0,
            "max": valid[-1] if valid else 0,
            "p95": valid[p95_index] if valid else 0,
            "samples": len(valid),
        }

        # ---------- 情报源健康 ----------
        # CollectionLog 用 status 表达结果（success/failed/partial），
        # 没有布尔 success 列——按 status 判定更贴合实际语义。
        log_q = select(
            CollectionLog.source_code,
            func.count().label("runs"),
            func.sum(
                case((CollectionLog.status == "success", 1), else_=0)
            ).label("ok_runs"),
            func.avg(CollectionLog.latency_ms).label("avg_latency"),
        )
        if start is not None:
            log_q = log_q.where(CollectionLog.started_at >= start)
        log_rows = (await session.execute(log_q.group_by(CollectionLog.source_code))).all()

        src_q = select(Vulnerability.origin_source, func.count())
        if start is not None:
            src_q = src_q.where(Vulnerability.ingest_at >= start)
        src_counts = {
            name: n
            for name, n in (await session.execute(src_q.group_by(
                Vulnerability.origin_source
            ))).all()
            if name
        }

        source_rows = (await session.execute(select(Source))).scalars().all()
        meta_by_code = {s.code: s for s in source_rows}

        health: list[dict[str, Any]] = []
        for code, count in sorted(src_counts.items(), key=lambda kv: -kv[1]):
            runs = ok_runs = 0
            avg_latency = None
            for log_code, r, ok, lat in log_rows:
                if log_code == code:
                    runs, ok_runs, avg_latency = r, ok or 0, lat
            meta = meta_by_code.get(code)
            health.append(
                {
                    "code": code,
                    "name": getattr(meta, "name", None) or code,
                    "category": getattr(meta, "category", None),
                    "count": count,
                    "runs": runs,
                    "success_rate": round(ok_runs / runs * 100, 1) if runs else None,
                    "avg_latency": round(float(avg_latency), 1) if avg_latency else None,
                }
            )

    # ---------- 知识图谱统计（独立客户端，失败不拖累其余数据）----------
    try:
        from ..问答模块.graph import get_graph_retriever

        graph = get_graph_retriever().health_check()
    except Exception:  # noqa: BLE001
        graph = {}

    total_new = total_vuln + sum(other_counts.values())
    return {
        "range": {
            "hours": hours,
            "bucket": bucket,
            "label": _label_of(hours),
            "start": start.isoformat() if start else None,
        },
        "metrics": {
            "new_intel": total_new,
            "new_vuln": total_vuln,
            "other": other_counts,
            "enriched": enriched_count,
            "high_count": int(high_count),
            # 占比分母用「有严重度判定的条目」，全为 None 时退化用总数
            "high_ratio": round(high_count / total_vuln * 100, 1) if total_vuln else 0.0,
            "avg_cvss": round(float(avg_cvss), 1) if avg_cvss else None,
            "classified": classified_count,
            "indexed": indexed_count,
            "graph": graph,
            "source_count": len(health),
        },
        "trend": {
            "buckets": trend_buckets,
            "new": trend_new,
            "high": trend_high,
            "enriched": trend_enriched,
            # 当前小时已过去的比例（0~1）。只对小时粒度有意义——
            # 前端用它做亚像素左移，让曲线看起来一直在走，
            # 而不必等整点才「跳」一下。数据语义仍是整点对齐。
            "progress": _hour_progress() if bucket == "hour" else None,
        },
        "by_category": _sort_categories(category_rows),
        "by_severity": _sort_severities(severity_rows),
        "top_critical": [
            {
                "cve_id": c,
                "title": (t or "")[:160],
                "cvss": score,
                "severity": (sev or "").upper(),
                "ai_category": cat,
                "source": src,
                "published_at": pub.isoformat() if pub else None,
                "ingest_at": ing.isoformat() if ing else None,
            }
            for c, t, score, sev, cat, src, pub, ing in top_rows
        ],
        "latency": latency,
        "pipeline": [
            {"stage": "采集入库", "count": total_vuln},
            {"stage": "已分类", "count": classified_count},
            {"stage": "已富化", "count": enriched_count},
            {"stage": "已建索引", "count": indexed_count},
        ],
        "sources": health,
    }


def _label_of(hours: int | None) -> str:
    if hours is None:
        return "全部"
    if hours % 24 == 0 and hours >= 24:
        return f"{hours // 24} 天"
    return f"{hours} 小时"


_CATEGORY_ORDER = (
    "AI基础设施/推理框架风险",
    "模型与数据层风险",
    "AI应用与智能体风险",
    "供应链风险",
)
_SEVERITY_ORDER = ("CRITICAL", "HIGH", "MEDIUM", "MODERATE", "LOW", "NONE")


def _sort_categories(rows: list[tuple[str | None, int]]) -> list[dict[str, Any]]:
    """按预设分类顺序排列，未知分类排在最后按数量降序"""
    mapping = {name: n for name, n in rows if name}
    out = [{"name": k, "value": mapping.pop(k, 0)} for k in _CATEGORY_ORDER if mapping.get(k)]
    out += [
        {"name": k, "value": v} for k, v in sorted(mapping.items(), key=lambda kv: -kv[1])
    ]
    return out


def _sort_severities(rows: list[tuple[str | None, int]]) -> list[dict[str, Any]]:
    mapping = {str(name).upper(): n for name, n in rows if name}
    out = [
        {"name": k, "value": mapping.pop(k, 0)}
        for k in _SEVERITY_ORDER
        if mapping.get(k)
    ]
    out += [
        {"name": k, "value": v} for k, v in sorted(mapping.items(), key=lambda kv: -kv[1])
    ]
    return out