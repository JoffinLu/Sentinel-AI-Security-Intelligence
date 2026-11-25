# pytest 单元测试
#
# 覆盖采集延迟统计模块（赛题性能指标：监测时效性）的全部口径规则。
# 这些规则都对应真实踩过的坑，回归时按场景名可追溯到事发经过。

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from 项目源码.数据采集.latency_stats import (  # noqa: E402
    MIN_ROUND_LIMIT_SECONDS,
    LatencyRecord,
    LogEvent,
    attribute_round,
    build_rounds,
    collect_valid_samples,
    dedup_by_source_url,
    percentile,
    round_limit,
    summarize_overall,
    summarize_source,
    summary_from_samples,
)

UTC = UTC
T0 = datetime(2026, 10, 10, 0, 0, 0, tzinfo=UTC)


def _ev(minutes: float, status: str = "success", finish: float | None = None) -> LogEvent:
    """以 T0 为基准造一个采集事件（minutes 为相对分钟数）"""
    return LogEvent(
        source="nvd",
        started_at=T0 + timedelta(minutes=minutes),
        finished_at=None if finish is None else T0 + timedelta(minutes=finish),
        status=status,
    )


def _rec(
    lat: int,
    pub_minutes: float,
    ingest_minutes: float,
    source: str = "nvd",
    url: str | None = None,
) -> LatencyRecord:
    return LatencyRecord(
        source=source,
        latency_seconds=lat,
        published_at=T0 + timedelta(minutes=pub_minutes),
        ingest_at=T0 + timedelta(minutes=ingest_minutes),
        source_url=url,
    )


# ========== 回合构建 ==========


def test_first_round_invalid():
    """首回合无效：冷启动无法判断与上次成功的间隔"""
    rounds = build_rounds([_ev(0), _ev(120)], 7200)
    assert len(rounds) == 2
    assert rounds[0].valid is False
    # 第二回合距首回合成功 120 分钟 = 2×周期，刚好在限内 → 有效
    assert rounds[1].valid is True


def test_gap_anchored_to_last_success_not_previous_round():
    """失败轮不重置时钟（v1 规则曾按「距上一轮」判，停摆被洗白）

    场景：T0 成功 → T0+3h 失败 → T0+7h 成功（周期 2h，限 4h）。
    正确口径：第二次成功距上次成功 7h > 4h → 无效（停摆补采）。
    错误口径：距上一轮 4h ≤ 4h → 有效。
    """
    rounds = build_rounds(
        [_ev(0), _ev(180, status="failed"), _ev(420)], 7200
    )
    assert rounds[0].valid is False  # 首回合
    assert rounds[1].valid is True   # 失败轮本身：距 T0 3h ≤ 4h
    assert rounds[2].valid is False  # 距上次成功（T0）7h > 4h → 停摆补采


def test_round_limit_boundary():
    """间隔临界值：== 限值算有效，超 1 秒算无效"""
    limit = round_limit(7200)  # 4h
    assert limit == 4 * 3600
    rounds = build_rounds([_ev(0), _ev(limit / 60)], 7200)
    assert rounds[1].valid is True
    rounds = build_rounds([_ev(0), _ev(limit / 60 + 1 / 60)], 7200)
    assert rounds[1].valid is False


def test_round_limit_min_2h():
    """周期再短，有效间隔下限也是 2h（容忍晚一轮）"""
    assert round_limit(600) == MIN_ROUND_LIMIT_SECONDS == 7200
    assert round_limit(0) == 7200       # 脏数据兜底
    assert round_limit(-5) == 7200


def test_manual_and_schedule_same_rule():
    """manual/api 触发与 schedule 同规则：长间隔后的手动轮同样判无效

    LogEvent 不带 trigger 字段——统计层不区分触发来源，
    这是口径的刻意设计（手动补采不应被洗白成有效回合）。
    """
    # T0 成功后隔 10 小时才有一次（无论谁触发），周期 2h → 无效
    rounds = build_rounds([_ev(0), _ev(600)], 7200)
    assert rounds[1].valid is False


def test_round_window_end_falls_back_to_next_start():
    """finished_at 缺失（进程被杀）时，窗口 end 退化为下一回合 start"""
    rounds = build_rounds(
        [_ev(0, finish=60), _ev(120, finish=None), _ev(240)], 7200
    )
    assert rounds[0].end == T0 + timedelta(minutes=60)
    assert rounds[1].end == T0 + timedelta(minutes=240)  # 缺失 → 下一回合 start
    assert rounds[2].end is None  # 末回合开放


def test_events_sorted_regardless_of_input_order():
    """日志乱序传入也能正确构建（DB 不保证返回顺序时）"""
    rounds = build_rounds([_ev(120), _ev(0)], 7200)
    assert rounds[0].start == T0
    assert rounds[1].start == T0 + timedelta(minutes=120)
    assert rounds[1].valid is True


# ========== 记录归桶 ==========


def test_attribute_round_open_ended_last():
    """末回合窗口开放：其后入库的记录归入末回合"""
    rounds = build_rounds([_ev(0), _ev(120)], 7200)
    w = attribute_round(rounds, T0 + timedelta(hours=10))
    assert w is rounds[1]


def test_attribute_round_before_first_is_none():
    """首回合开始前入库 → 无归属（日志被截断的场景）"""
    rounds = build_rounds([_ev(120)], 7200)
    assert attribute_round(rounds, T0) is None


# ========== 双钥匙去重 ==========


def test_dedup_ghsa_twins_keeps_first_ingest():
    """ghsa 双钥匙：同源 URL 仅保留首次入库

    实测 4 对：GHSA 键 10-09 入库（lat 11h，真实采集延迟），
    GitHub 事后分配 CVE 后 10-10 按 CVE 键二次入库（lat 33h，
    实为 CVE 分配延迟）——后者必须剔除。
    """
    first = _rec(11 * 3600, pub_minutes=-600, ingest_minutes=0, source="ghsa",
                 url="https://github.com/advisories/GHSA-xxxx")
    second = _rec(33 * 3600, pub_minutes=-600, ingest_minutes=1440, source="ghsa",
                  url="https://github.com/advisories/GHSA-xxxx")
    kept = dedup_by_source_url([second, first])  # 乱序传入
    assert kept == [first]


def test_dedup_directory_url_skipped():
    """目录型 URL 跳过去重：CISA KEV 全表共享一个目录页

    实测 1740 条 kev 共享 1 个 URL——按 URL 去重会误删 1739 条。
    """
    url = "https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json"
    records = [
        _rec(100, pub_minutes=-i, ingest_minutes=i, source="cisa_kev", url=url)
        for i in range(10)
    ]
    kept = dedup_by_source_url(records)
    assert len(kept) == 10  # 一条不删


def test_dedup_records_without_url_all_kept():
    """无 URL 的记录（arxiv/policy 表）不参与去重"""
    records = [_rec(100, -i, i, source="arxiv") for i in range(5)]
    assert len(dedup_by_source_url(records)) == 5


# ========== 有效样本收集 ==========


def test_collect_valid_samples_full_flow():
    """窗口 × 回合有效性 × 归桶的完整串联"""
    # 回合：T0（首回合无效）→ T0+2h（有效，窗口开放）
    rounds = build_rounds([_ev(0), _ev(120)], 7200)
    now = T0 + timedelta(hours=3)
    records = [
        _rec(600, pub_minutes=-30, ingest_minutes=30),    # 无效回合 → excluded
        _rec(1200, pub_minutes=-60, ingest_minutes=150),  # 有效回合 → valid
        _rec(1800, pub_minutes=-60, ingest_minutes=200),  # 有效回合 → valid
        _rec(60, pub_minutes=-8 * 1440, ingest_minutes=150),  # 窗口外（8 天前发布）
        _rec(60, pub_minutes=-30, ingest_minutes=-60),    # 归桶前 → unattributed
        _rec(-5, pub_minutes=-30, ingest_minutes=150),    # 负延迟 → 直接跳过
    ]
    samples, counts = collect_valid_samples(records, rounds, now=now, window_days=7)
    assert sorted(samples) == [1200, 1800]
    assert counts == {"excluded": 1, "out_of_window": 1, "unattributed": 1}


def test_collect_valid_samples_published_none_counted_as_out_of_window():
    """published_at 缺失 → 计入窗口外（无法证明是近期发布）"""
    rounds = build_rounds([_ev(0), _ev(120)], 7200)
    rec = LatencyRecord(
        source="nvd", latency_seconds=100,
        published_at=None, ingest_at=T0 + timedelta(minutes=150),
    )
    samples, counts = collect_valid_samples([rec], rounds, now=T0 + timedelta(hours=3))
    assert samples == []
    assert counts["out_of_window"] == 1


def test_collect_valid_samples_naive_datetime_treated_as_utc():
    """naive 时间按 UTC 解释（与入库管线 _parse_iso 同口径）"""
    rounds = build_rounds([_ev(0), _ev(120)], 7200)
    rec = LatencyRecord(
        source="nvd", latency_seconds=100,
        published_at=datetime(2026, 10, 9, 20, 0, 0),   # naive
        ingest_at=datetime(2026, 10, 10, 2, 30, 0),     # naive，落在第二（有效）回合
    )
    samples, counts = collect_valid_samples(
        [rec], rounds, now=datetime(2026, 10, 10, 3, 0, 0)
    )
    assert samples == [100]
    assert counts["out_of_window"] == 0


# ========== 汇总 ==========


def test_summarize_source_insufficient_threshold():
    """样本 < 10 → insufficient，分位数仍照算（看板决定是否展示）"""
    rounds = build_rounds([_ev(0), _ev(120)], 7200)
    now = T0 + timedelta(hours=3)
    nine = [_rec(100, -30, 150) for _ in range(9)]
    ten = nine + [_rec(200, -30, 150)]
    s9 = summarize_source(nine, rounds, now=now, interval_seconds=7200)
    s10 = summarize_source(ten, rounds, now=now, interval_seconds=7200)
    assert s9["insufficient"] is True and s9["samples"] == 9
    assert s10["insufficient"] is False and s10["samples"] == 10
    assert s10["p50_seconds"] == 100
    assert s10["valid_rounds"] == 1 and s10["total_rounds"] == 2


def test_summarize_source_empty():
    """空数据边界：全 None / 0，不抛错"""
    s = summarize_source([], [], now=T0, interval_seconds=7200)
    assert s["samples"] == 0
    assert s["p50_seconds"] is None and s["p95_seconds"] is None
    assert s["within_6h_ratio"] is None
    assert s["insufficient"] is True
    assert s["valid_rounds"] == 0 and s["total_rounds"] == 0


def test_summarize_overall_pools_samples():
    """总体 = 样本级合并（不是分位数再平均）"""
    overall = summarize_overall(
        [100, 200, 30000], valid_rounds=2, total_rounds=5,
        contributors=["nvd", "rss"],
    )
    assert overall["samples"] == 3
    assert overall["p50_seconds"] == 200      # 中位数，非 (100+200+30000)/3
    assert overall["p95_seconds"] == 30000
    assert overall["within_6h_ratio"] == pytest.approx(2 / 3)
    assert overall["sources"] == ["nvd", "rss"]
    assert "nvd" in overall["_note"]


def test_percentile_basic():
    """分位数口径：nearest-rank 向上取整（继承 scheduler._percentile 原公式）"""
    assert percentile([], 0.5) is None
    assert percentile([1, 2, 3, 4], 0.5) == 3
    assert percentile([1, 2, 3, 4], 0.95) == 4
    assert percentile([5], 0.95) == 5


def test_summary_from_samples_shared_shape():
    """单源与总体共用同一汇总形状（前端只需一套渲染逻辑）"""
    s = summary_from_samples(
        [3600], valid_rounds=1, total_rounds=2, interval_seconds=7200,
        window_days=7, excluded=2, out_of_window=3, unattributed=1,
    )
    assert s["samples"] == 1
    assert s["within_6h_ratio"] == 1.0
    assert s["within_24h_ratio"] == 1.0
    assert s["excluded_round_samples"] == 2
    assert s["out_of_window"] == 3
    assert s["unattributed"] == 1
