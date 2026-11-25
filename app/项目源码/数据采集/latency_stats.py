"""采集延迟统计（赛题性能指标：监测时效性）

口径（2026-10-14 定稿，经 9 数据源特征全量核查 + v2/v3/v4 三轮试算验证）：

1. **发布窗口**：只统计 published_at 在近 ``window_days``（默认 7）天内发布的
   条目，且对全部数据表统一生效（漏洞/论文/政策）——排除冷启动历史回填污染。
   试算结论：窗口 7→30 天只救活 policy 一个源且那 9 个样本是冷启动残留，
   ghsa/kev/osv 依然为 0；瓶颈是机器休眠让回合失效而非窗口长度，
   故**不做自适应窗口**，全表统一 7 天。
2. **回合有效性**：有效回合 = 与同源上次「成功」采集的间隔
   ≤ ``max(2 × 当期周期, 2h)``。容忍晚一轮；晚两轮以上 = 停摆/补采，
   整回合剔除。首回合无效（冷启动）。manual/api 触发同规则。
   注：周期取调用方注入的**当前生效值**（registry.effective_interval，
   运维可调）——系统不保存历史周期档案，改过周期的旧回合按新周期
   评判，属近似处理（方向偏保守：周期调小则旧回合更易被判无效）。
3. **ghsa 双钥匙去重**：同源 URL 出现 > ``URL_MAX_MULTIPLICITY`` 次 =
   目录型 URL（如 CISA KEV 全表共享一个目录页，1740 条 1 个 URL），
   跳过去重；其余 URL 只保留首次入库——GitHub 事后分配 CVE 后同公告会
   换钥匙二次入库（实测 4 对：GHSA 键 11h → 次日 CVE 键 33h），
   后者延迟实为 CVE 分配延迟而非采集延迟。
4. **记录归桶**：按 ``ingest_at`` 归入回合窗口
   ``[started_at, finished_at 或下一回合 started_at)``。
5. **总体口径**：总体 = 6 个漏洞源（nvd/ghsa/cisa_kev/osv/poc_repo/rss）
   合并；arxiv（论文表）/policy（政策表）单列，不并入总体——
   它们的 published_at 语义与漏洞源不同（论文是提交时刻、政策是正式
   发布日期），合并会稀释漏洞监测时效的真实水平。

本模块为**纯函数**：不碰数据库、不 import 采集器，周期由调用方注入
（``registry.effective_interval``），便于单测与复用。
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime

# 样本数低于此值的源，看板标注「样本不足」（不强行展示分位数）
MIN_SAMPLES = 10

# 同源 URL 出现次数超过此值视为目录型 URL，不作为去重键
URL_MAX_MULTIPLICITY = 5

# 有效回合间隔下限：容忍晚一轮，至少给 2 小时
MIN_ROUND_LIMIT_SECONDS = 2 * 3600

# 默认发布窗口（天）
DEFAULT_WINDOW_DAYS = 7


@dataclass(frozen=True)
class LogEvent:
    """一次采集尝试（对应 collection_logs 一行）"""

    source: str
    started_at: datetime
    finished_at: datetime | None
    status: str


@dataclass(frozen=True)
class LatencyRecord:
    """一条已入库情报的延迟测量"""

    source: str  # 逻辑源代码（调用方已将 rss/policy 的 feed 归一）
    latency_seconds: int | None
    published_at: datetime | None
    ingest_at: datetime
    source_url: str | None = None


@dataclass(frozen=True)
class Round:
    """一个采集回合窗口"""

    start: datetime
    end: datetime | None
    valid: bool


def _as_utc(value: datetime | None) -> datetime | None:
    """naive 时间一律按 UTC 解释（与入库管线 _parse_iso 口径一致）"""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def percentile(values: list[int], q: float) -> int | None:
    """计算分位数（与 scheduler._percentile 同口径：向上取整位置）"""
    if not values:
        return None
    ordered = sorted(values)
    idx = min(int(len(ordered) * q), len(ordered) - 1)
    return ordered[idx]


def round_limit(interval_seconds: int) -> int:
    """有效回合的最大间隔：max(2 × 当期周期, 2h)"""
    if interval_seconds <= 0:
        return MIN_ROUND_LIMIT_SECONDS
    return max(2 * interval_seconds, MIN_ROUND_LIMIT_SECONDS)


def build_rounds(events: list[LogEvent], interval_seconds: int) -> list[Round]:
    """由采集日志构建回合窗口序列

    规则：
    - 按 started_at 排序后逐条构建；窗口 end = finished_at，
      缺失（进程被杀等）则退化为下一回合的 started_at，末回合开放。
    - 有效性锚定「距上次**成功**采集」：失败轮不重置时钟——
      v1 规则曾用「距上一轮」导致失败轮把时钟刷新，停摆被洗白。
    - 首回合无效（冷启动，无法判断间隔）。
    """
    limit = round_limit(interval_seconds)
    ordered = sorted(events, key=lambda e: _as_utc(e.started_at) or e.started_at)
    rounds: list[Round] = []
    last_success: datetime | None = None
    for i, ev in enumerate(ordered):
        start = _as_utc(ev.started_at)
        assert start is not None  # started_at 非空
        gap = None if last_success is None else (start - last_success).total_seconds()
        valid = gap is not None and gap <= limit
        end = _as_utc(ev.finished_at)
        if end is None and i + 1 < len(ordered):
            nxt = _as_utc(ordered[i + 1].started_at)
            end = nxt
        rounds.append(Round(start=start, end=end, valid=valid))
        if ev.status == "success":
            last_success = start
    return rounds


def attribute_round(rounds: list[Round], at: datetime) -> Round | None:
    """把入库时刻归入回合窗口 [start, end)，末回合 end 为 None 时开放"""
    moment = _as_utc(at)
    for r in rounds:
        if moment >= r.start and (r.end is None or moment < r.end):
            return r
    return None


def dedup_by_source_url(records: list[LatencyRecord]) -> list[LatencyRecord]:
    """ghsa 双钥匙去重：同源 URL 仅保留首次入库

    目录型 URL（同源出现 > URL_MAX_MULTIPLICITY 次）不参与去重——
    CISA KEV 全部 1740 条共享一个目录页 URL，按 URL 去重会误删 1739 条。
    """
    multiplicity = Counter(
        (r.source, r.source_url) for r in records if r.source_url
    )
    first: dict[tuple[str, str], LatencyRecord] = {}
    for r in records:
        url = r.source_url
        if not url:
            continue
        if multiplicity[(r.source, url)] > URL_MAX_MULTIPLICITY:
            continue  # 目录型 URL，跳过
        key = (r.source, url)
        prev = first.get(key)
        if prev is None or r.ingest_at < prev.ingest_at:
            first[key] = r
    kept_ids = {id(r) for r in first.values()}
    return [
        r for r in records
        if not r.source_url
        or multiplicity[(r.source, r.source_url)] > URL_MAX_MULTIPLICITY
        or id(r) in kept_ids
    ]


def collect_valid_samples(
    records: list[LatencyRecord],
    rounds: list[Round],
    *,
    now: datetime,
    window_days: int = DEFAULT_WINDOW_DAYS,
) -> tuple[list[int], dict[str, int]]:
    """过滤出有效延迟样本，并返回各剔除原因的计数

    Returns:
        (有效延迟列表, {"excluded": 无效回合样本数, "out_of_window": 窗口外,
         "unattributed": 无对应回合})
    """
    now_utc = _as_utc(now)
    assert now_utc is not None
    cutoff = now_utc.timestamp() - window_days * 86400

    valid: list[int] = []
    excluded = 0   # 落在无效回合（停摆/补采/首回合）
    out_window = 0  # 发布时间超出窗口（冷启动回填）
    unattributed = 0  # 入库时刻无对应回合（日志被截断等）

    for r in records:
        lat = r.latency_seconds
        if lat is None or lat < 0:
            continue
        pub = _as_utc(r.published_at)
        if pub is None or pub.timestamp() < cutoff:
            out_window += 1
            continue
        w = attribute_round(rounds, r.ingest_at)
        if w is None:
            unattributed += 1
        elif w.valid:
            valid.append(int(lat))
        else:
            excluded += 1

    counts = {
        "excluded": excluded,
        "out_of_window": out_window,
        "unattributed": unattributed,
    }
    return valid, counts


def summarize_source(
    records: list[LatencyRecord],
    rounds: list[Round],
    *,
    now: datetime,
    interval_seconds: int,
    window_days: int = DEFAULT_WINDOW_DAYS,
) -> dict:
    """单源延迟统计（便捷包装：collect_valid_samples + summary_from_samples）

    Returns:
        含 samples/p50/p95/有效回合数/各剔除原因计数的字典；
        无有效样本时 p50/p95 为 None（看板显示「—」并标注样本不足）。
    """
    valid, counts = collect_valid_samples(
        records, rounds, now=now, window_days=window_days
    )
    valid_rounds = sum(1 for w in rounds if w.valid)
    return summary_from_samples(
        valid,
        valid_rounds=valid_rounds,
        total_rounds=len(rounds),
        interval_seconds=interval_seconds,
        window_days=window_days,
        **counts,
    )


def summary_from_samples(
    valid: list[int],
    *,
    valid_rounds: int,
    total_rounds: int,
    interval_seconds: int,
    window_days: int,
    excluded: int = 0,
    out_of_window: int = 0,
    unattributed: int = 0,
) -> dict:
    """由有效样本列表生成统计字典（单源与总体共用）"""
    n = len(valid)
    return {
        "samples": n,
        "valid_rounds": valid_rounds,
        "total_rounds": total_rounds,
        "interval_seconds": interval_seconds,
        "window_days": window_days,
        "p50_seconds": percentile(valid, 0.5),
        "p90_seconds": percentile(valid, 0.9),
        "p95_seconds": percentile(valid, 0.95),
        "max_seconds": max(valid) if valid else None,
        "avg_seconds": int(sum(valid) / n) if n else None,
        "within_6h_ratio": (
            sum(1 for x in valid if x <= 6 * 3600) / n if n else None
        ),
        "within_24h_ratio": (
            sum(1 for x in valid if x <= 24 * 3600) / n if n else None
        ),
        "excluded_round_samples": excluded,
        "out_of_window": out_of_window,
        "unattributed": unattributed,
        "insufficient": n < MIN_SAMPLES,
    }


def summarize_overall(
    samples: list[int],
    *,
    valid_rounds: int = 0,
    total_rounds: int = 0,
    window_days: int = DEFAULT_WINDOW_DAYS,
    contributors: list[str] | None = None,
) -> dict:
    """总体统计：合并各漏洞源的有效样本（样本级合并，非分位数再平均）

    nvd 占漏洞情报约 98.6%，总体数字实质反映主流源水平，
    这一点随 _note 一并向看板/评审说明。
    """
    return summary_from_samples(
        samples,
        valid_rounds=valid_rounds,
        total_rounds=total_rounds,
        interval_seconds=0,
        window_days=window_days,
    ) | {
        "sources": sorted(contributors or []),
        "_note": "总体为漏洞源样本级合并（nvd 占约 98.6%，实质反映主流源水平）；"
        "arxiv/policy 语义不同，单列不并入",
    }
