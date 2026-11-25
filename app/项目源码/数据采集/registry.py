"""
采集器注册表

统一管理 9 类数据源的采集器实例，是 MonitorAgent 调度与健康检查的依据。
新增数据源只需在此登记一次，其余模块无需改动。
"""

from __future__ import annotations

from typing import Any

from .arxiv_collector import ArxivCollector
from .base_collector import BaseCollector
from .cisa_kev_collector import CISAKEVCollector
from .community_collector import SecurityRSSCollector
from .github_advisory_collector import GitHubAdvisoryCollector
from .mitre_atlas_collector import MITREAtlasCollector
from .nvd_collector import NVDCollector
from .osv_collector import OSVCollector
from .poc_collector import POCCollector
from .policy_collector import PolicyCollector

# 9 类数据源（对应赛题要求的 CVE/NVD、安全社区、厂商公告、博客等）
COLLECTOR_CLASSES: list[type[BaseCollector]] = [
    # --- 漏洞库 ---
    NVDCollector,              # NVD CVE 2.0
    GitHubAdvisoryCollector,   # GitHub Security Advisory
    OSVCollector,              # OSV.dev 开源漏洞
    CISAKEVCollector,          # CISA KEV 在野利用
    # --- 学术 ---
    ArxivCollector,            # arXiv 论文
    # --- 社区 / POC ---
    POCCollector,              # POC 验证代码（GitHub 搜索）
    SecurityRSSCollector,      # 安全社区与厂商博客
    # --- 标准 ---
    MITREAtlasCollector,       # MITRE ATLAS 攻击技
    PolicyCollector,           # AI 安全政策法规（赛题赛题三类知识底座之一）
]

_registry: dict[str, BaseCollector] = {}


def get_collectors() -> dict[str, BaseCollector]:
    """
    获取全部采集器实例（单例）

    Returns:
        {source_code: collector}
    """
    if not _registry:
        for cls in COLLECTOR_CLASSES:
            inst = cls()
            _registry[inst.code] = inst
    return _registry


def get_collector(code: str) -> BaseCollector | None:
    """按 code 获取单个采集器"""
    return get_collectors().get(code)


# ---------- 采集周期覆盖值 ----------
#
# 为什么放在这里而不是 scheduler 里：
#   scheduler 模块级导入了 ingest_pipeline，而 ingest_pipeline 也要读
#   「当前生效周期」。若让 ingest_pipeline 反向 import scheduler，
#   会形成环；若让它调 get_scheduler() 拿单例，又会白白新建一个
#   IngestPipeline（其 __init__ 会拉分类器）。
#   registry 只依赖采集器、不被任何人依赖成环，是唯一安全的中转站。

_interval_overrides: dict[str, int] = {}


def set_interval_override(code: str, seconds: int | None) -> None:
    """
    设置或清除某源的周期覆盖值

    seconds=None 表示恢复采集器默认值（删除覆盖）。
    """
    if seconds is None:
        _interval_overrides.pop(code, None)
    else:
        _interval_overrides[code] = int(seconds)


def get_interval_overrides() -> dict[str, int]:
    """当前全部覆盖值（副本，避免调用方改动内部状态）"""
    return dict(_interval_overrides)


def effective_interval(code: str) -> int:
    """
    该源**实际生效**的采集周期（秒）

    解析顺序：运维自定义覆盖值 → 采集器 default_interval。
    没有覆盖时返回默认值，因此默认行为与加此功能前完全一致。
    """
    override = _interval_overrides.get(code)
    if override is not None:
        return int(override)
    collector = get_collector(code)
    if collector is None:
        return 43_200
    return int(collector.default_interval)


async def close_all() -> None:
    """关闭全部采集器的 HTTP 连接"""
    for c in get_collectors().values():
        await c.close()


# 数据源元信息（供前端看板与文档使用）
#
# 全部字段从采集器实例**派生**，不另写一份数字。
# 历史上这里手写了 9 个 interval_h，与各采集器的 default_interval
# 各说各话 —— 改周期时只改一处，另一处就悄悄失准，且无人会发现。
# 现在 default_interval 是唯一的真相来源，这里只是它的一个视图。
#
# 注：当前全项目无代码引用本字典（保留是为了文档与排查时一眼可查）。
SOURCE_META: dict[str, dict[str, Any]] = {
    code: {
        "name": c.name,
        "category": c.category,
        "interval_h": round(c.default_interval / 3600, 2),
        "min_interval_h": round(getattr(c, "min_interval", 3600) / 3600, 2),
        "max_interval_h": round(getattr(c, "max_interval", 86400) / 3600, 2),
        "interval_basis": getattr(c, "interval_basis", ""),
    }
    for code, c in get_collectors().items()
}
