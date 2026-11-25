# pytest 单元测试：采集周期可调（监控看板「周期」列）
#
# 背景：赛题考核「监测时效性」，运维需要能在界面上直接下调采集周期
# （如 NVD 2h→1h）而不是改代码重启。本文件覆盖三层：
#   ① 采集器声明的周期区间自洽（min ≤ default ≤ max）且附区间依据
#   ② registry 覆盖值中转站（规避导入环的唯一落点）
#   ③ scheduler 的档位裁剪 / 越界拦截 / 恢复默认 / 落库与重排调用
#
# DB 写入（_persist_interval / _delete_interval）在 fixture 里挡掉 ——
# 单测不应依赖 Docker 里的 PostgreSQL，重启恢复由 E2E 冒烟验证。

import sys
from pathlib import Path

import pytest

# 以项目根为包根导入中文包
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from 项目源码.数据采集.registry import (  # noqa: E402
    SOURCE_META,
    effective_interval,
    get_collectors,
    get_interval_overrides,
    set_interval_override,
)
from 项目源码.数据采集.scheduler import (  # noqa: E402
    INTERVAL_LADDER,
    CollectionScheduler,
    _format_interval,
)


@pytest.fixture(autouse=True)
def _clean_overrides():
    """registry 是模块级状态，用例间必须隔离，否则越跑越玄"""
    for code in list(get_interval_overrides()):
        set_interval_override(code, None)
    yield
    for code in list(get_interval_overrides()):
        set_interval_override(code, None)


@pytest.fixture
def sched(monkeypatch):
    """
    构造一个不碰数据库、不真启动 APScheduler 的调度器

    _persist_interval / _delete_interval 换成记录型假实现，
    _reschedule 换成记录型假实现并恒返回 True（模拟已启动）。
    """
    s = CollectionScheduler()
    calls: dict[str, list] = {"persist": [], "delete": [], "reschedule": []}

    async def fake_persist(code: str, seconds: int) -> None:
        calls["persist"].append((code, seconds))

    async def fake_delete(code: str) -> None:
        calls["delete"].append(code)

    def fake_reschedule(code: str, seconds: int) -> bool:
        calls["reschedule"].append((code, seconds))
        return True

    monkeypatch.setattr(s, "_persist_interval", fake_persist)
    monkeypatch.setattr(s, "_delete_interval", fake_delete)
    monkeypatch.setattr(s, "_reschedule", fake_reschedule)
    return s, calls


# ========== ① 采集器周期区间自洽 ==========


def test_interval_bounds_self_consistent():
    """每个源的 min ≤ default ≤ max，且都写了区间依据"""
    collectors = get_collectors()
    assert collectors, "注册表为空"
    for code, c in collectors.items():
        lo = int(getattr(c, "min_interval", 0))
        hi = int(getattr(c, "max_interval", 0))
        default = int(c.default_interval)
        assert lo >= 60, f"{code} 下限低于接口校验（60 秒），设了也存不进去"
        assert lo <= default <= hi, (
            f"{code} 区间不自洽：min={lo} default={default} max={hi}"
        )
        assert getattr(c, "interval_basis", ""), (
            f"{code} 缺 interval_basis：用户会问「为什么这个源不能调到 10 分钟」"
        )


def test_source_meta_derived_from_collectors():
    """SOURCE_META 是 default_interval 的派生视图，不允许各说各话"""
    collectors = get_collectors()
    assert set(SOURCE_META) == set(collectors)
    for code, meta in SOURCE_META.items():
        c = collectors[code]
        assert meta["interval_h"] == round(c.default_interval / 3600, 2)
        assert meta["min_interval_h"] == round(
            getattr(c, "min_interval", 3600) / 3600, 2
        )
        assert meta["max_interval_h"] == round(
            getattr(c, "max_interval", 86400) / 3600, 2
        )


# ========== ② registry 覆盖值中转站 ==========


def test_override_roundtrip():
    code = "nvd"
    default = int(get_collectors()[code].default_interval)
    assert effective_interval(code) == default
    assert code not in get_interval_overrides()

    set_interval_override(code, 3600)
    assert effective_interval(code) == 3600
    assert get_interval_overrides() == {code: 3600}

    set_interval_override(code, None)
    assert effective_interval(code) == default
    assert get_interval_overrides() == {}


def test_get_interval_overrides_returns_copy():
    """返回副本：调用方改动不应污染内部状态"""
    set_interval_override("nvd", 3600)
    snapshot = get_interval_overrides()
    snapshot["nvd"] = 999
    assert get_interval_overrides() == {"nvd": 3600}


def test_effective_interval_unknown_source_fallback():
    assert effective_interval("__no_such_source__") == 43_200


# ========== ③ scheduler 档位 / 越界 / 恢复默认 ==========


def test_interval_options_respect_bounds():
    """档位全部落在区间内，且默认值一定在列（不会被困在自定义值上）"""
    s = CollectionScheduler()
    for code, c in get_collectors().items():
        lo = int(getattr(c, "min_interval", 3600))
        hi = int(getattr(c, "max_interval", 86400))
        options = s.interval_options(code)
        assert options, f"{code} 一个档位都没有，下拉将是空的"
        seconds_list = [int(o["seconds"]) for o in options]
        assert all(lo <= v <= hi for v in seconds_list), (
            f"{code} 档位越界：{seconds_list} 不在 [{lo}, {hi}]"
        )
        assert int(c.default_interval) in seconds_list, (
            f"{code} 默认值不在档位里，用户选不回去"
        )
        # 档位按秒升序（前端下拉顺序）
        assert seconds_list == sorted(seconds_list)
        # current 标记指向当前生效值
        current = s.effective_interval(code)
        assert any(o["current"] for o in options if int(o["seconds"]) == current)


def test_interval_options_unknown_source():
    assert CollectionScheduler().interval_options("__no_such_source__") == []


async def test_set_interval_rejects_out_of_range(sched):
    """越界在落库前就被拦下，且不留下任何副作用"""
    s, calls = sched
    r = await s.set_interval("nvd", 60)  # nvd min=1800
    assert r["ok"] is False
    assert "reason" in r and r["reason"], "越界时必须附带区间依据"
    assert r["min_seconds"] == 1800 and r["max_seconds"] == 43200
    # 无副作用：没写库、没重排、没进覆盖表
    assert calls["persist"] == [] and calls["reschedule"] == []
    assert "nvd" not in get_interval_overrides()


async def test_set_interval_unknown_source(sched):
    s, calls = sched
    r = await s.set_interval("__no_such_source__", 3600)
    assert r["ok"] is False
    assert "未知数据源" in r["error"]
    assert calls["persist"] == []


async def test_set_interval_success_path(sched):
    """合法值：先落库 → 再进覆盖表 → 最后重排 job（顺序即回滚安全性）"""
    s, calls = sched
    r = await s.set_interval("nvd", 3600)
    assert r["ok"] is True
    assert r["source_code"] == "nvd"
    assert r["interval_seconds"] == 3600
    assert r["applied_immediately"] is True
    assert calls["persist"] == [("nvd", 3600)]
    assert calls["reschedule"] == [("nvd", 3600)]
    assert effective_interval("nvd") == 3600


async def test_set_interval_off_ladder_value_allowed(sched):
    """接口层允许区间内任意秒数，不必是阶梯档位"""
    s, calls = sched
    r = await s.set_interval("nvd", 5400)  # 1.5 小时，不在阶梯上
    assert r["ok"] is True
    assert effective_interval("nvd") == 5400


async def test_reset_interval_restores_default(sched):
    s, calls = sched
    default = int(get_collectors()["nvd"].default_interval)
    await s.set_interval("nvd", 3600)
    calls["persist"].clear()
    calls["reschedule"].clear()

    r = await s.reset_interval("nvd")
    assert r["ok"] is True
    assert r["interval_seconds"] == default
    assert calls["delete"] == ["nvd"]
    assert calls["reschedule"] == [("nvd", default)]
    assert "nvd" not in get_interval_overrides()
    assert effective_interval("nvd") == default


async def test_reset_interval_unknown_source(sched):
    s, _ = sched
    r = await s.reset_interval("__no_such_source__")
    assert r["ok"] is False


async def test_set_then_options_mark_custom_current(sched):
    """设了自定义值后，档位的 current 标记要跟着切过去"""
    s, _ = sched
    await s.set_interval("nvd", 3600)
    options = s.interval_options("nvd")
    current = [o for o in options if o["current"]]
    assert len(current) == 1
    assert int(current[0]["seconds"]) == 3600


# ========== ④ 展示格式化 ==========


def test_format_interval():
    assert _format_interval(600) == "10 分钟"
    assert _format_interval(3600) == "1 小时"
    assert _format_interval(7200) == "2 小时"
    assert _format_interval(86400) == "1 天"
    assert _format_interval(604800) == "7 天"
    assert _format_interval(5400) == "90 分钟"  # 非整小时兜底
    assert _format_interval(45) == "45 秒"


def test_interval_ladder_sorted_and_unique():
    values = [v for v, _ in INTERVAL_LADDER]
    assert values == sorted(values)
    assert len(values) == len(set(values))
