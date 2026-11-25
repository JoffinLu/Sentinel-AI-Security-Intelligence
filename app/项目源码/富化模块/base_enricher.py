"""
富化抽象基类与统一数据结构

7 个富化维度共享同一协议：
- 维度实现只需产出 EnrichResult，编排器负责调度、隔离、落库、埋点
- 单维失败不阻塞其余维度（异常隔离）
- 每维返回 0-1 置信度，低置信度进入待核验队列

这是"精准与关联推理"评分项的技术底座：
统一的置信度机制让 7 维富化的质量可量化、可分层统计。
"""

from __future__ import annotations

import abc
import asyncio
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from ..后端服务.logger import get_logger

logger = get_logger("enrich.base")


# ========== 7 个富化维度 ==========


class Dimension(str):
    """富化维度枚举（用 str 便于直接落库）"""

    CVSS = "cvss"  # 1 CVSS 评分
    ASSET = "asset"  # 2 受影响资产
    POC = "poc"  # 3 POC 关联
    PAPER = "paper"  # 4 关联论文
    FIX = "fix"  # 5 修复建议
    TECHNIQUE = "technique"  # 6 攻击技映射
    RISK = "risk"  # 7 AI 风险定级


ALL_DIMENSIONS = [
    Dimension.CVSS,
    Dimension.ASSET,
    Dimension.POC,
    Dimension.PAPER,
    Dimension.FIX,
    Dimension.TECHNIQUE,
    Dimension.RISK,
]

DIMENSION_LABELS = {
    Dimension.CVSS: "CVSS 评分",
    Dimension.ASSET: "受影响资产",
    Dimension.POC: "POC 关联",
    Dimension.PAPER: "关联论文",
    Dimension.FIX: "修复建议",
    Dimension.TECHNIQUE: "攻击技映射",
    Dimension.RISK: "AI 风险定级",
}


# ========== 富化上下文与结果 ==========


@dataclass(slots=True)
class VulnContext:
    """
    富化上下文

    携带待富化漏洞的全部信息，供各维度使用。
    预先查询好关联数据，避免每个维度重复查库。
    """

    vulnerability_id: int
    cve_id: str
    title: str
    description: str
    severity: str | None = None
    cvss_score: float | None = None
    cvss_vector: str | None = None
    affected_assets: list[dict[str, Any]] = field(default_factory=list)
    references: list[str] = field(default_factory=list)
    ai_category: str | None = None
    ai_relevance_score: float = 0.0
    published_at: datetime | None = None
    source_url: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    # 预加载的关联数据（编排器填充）
    all_pocs: list[dict[str, Any]] = field(default_factory=list)
    all_papers: list[dict[str, Any]] = field(default_factory=list)
    all_techniques: list[dict[str, Any]] = field(default_factory=list)


@dataclass(slots=True)
class EnrichResult:
    """
    单维度富化结果

    confidence 是质量分层统计的依据：
    ≥0.8 视为高置信（可直接展示）
    0.5-0.8 视为中置信（展示并标注）
    <0.5 视为低置信（进入待人工核验队列）
    """

    dimension: str
    success: bool
    content: dict[str, Any] = field(default_factory=dict)
    confidence: float = 0.0
    backend: str | None = None  # 产生该结果的后端（rule/llm/degraded）
    error: str | None = None
    elapsed_ms: int = 0

    @property
    def quality_tier(self) -> str:
        """置信度分层"""
        if self.confidence >= 0.8:
            return "high"
        if self.confidence >= 0.5:
            return "medium"
        return "low"

    def to_dict(self) -> dict[str, Any]:
        return {
            "dimension": self.dimension,
            "label": DIMENSION_LABELS.get(self.dimension, self.dimension),
            "success": self.success,
            "content": self.content,
            "confidence": round(self.confidence, 3),
            "quality_tier": self.quality_tier,
            "backend": self.backend,
            "error": self.error,
            "elapsed_ms": self.elapsed_ms,
        }


@dataclass(slots=True)
class EnrichmentReport:
    """单个漏洞的 7 维富化汇总"""

    cve_id: str
    vulnerability_id: int
    results: dict[str, EnrichResult] = field(default_factory=dict)
    elapsed_ms: int = 0

    @property
    def success_count(self) -> int:
        """成功维度数"""
        return sum(1 for r in self.results.values() if r.success)

    @property
    def total_count(self) -> int:
        return len(ALL_DIMENSIONS)

    @property
    def completeness(self) -> float:
        """
        富化完整度

        赛题硬性指标≥80%。计算方式为成功维度数 / 7。
        """
        return self.success_count / self.total_count if self.total_count else 0.0

    @property
    def avg_confidence(self) -> float:
        """平均置信度"""
        ok = [r.confidence for r in self.results.values() if r.success]
        return sum(ok) / len(ok) if ok else 0.0

    @property
    def low_confidence_count(self) -> int:
        """低置信维度数（待人工核验）"""
        return sum(
            1
            for r in self.results.values()
            if r.success and r.quality_tier == "low"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "cve_id": self.cve_id,
            "vulnerability_id": self.vulnerability_id,
            "completeness": round(self.completeness, 3),
            "success_count": self.success_count,
            "total_count": self.total_count,
            "avg_confidence": round(self.avg_confidence, 3),
            "low_confidence_count": self.low_confidence_count,
            "elapsed_ms": self.elapsed_ms,
            "dimensions": [r.to_dict() for r in self.results.values()],
        }


# ========== 富化器基类 ==========


class BaseEnricher(abc.ABC):
    """
    富化器基类

    子类实现 enrich 即可，异常处理、计时、置信度兜底由基类负责。
    """

    dimension: str = "base"
    # 该维度是否需要 LLM（决定是否受网关可用性影响）
    requires_llm: bool = False

    def __init__(self) -> None:
        self.log = get_logger(f"enrich.{self.dimension}")

    @abc.abstractmethod
    async def enrich(self, ctx: VulnContext) -> tuple[dict[str, Any], float]:
        """
        执行富化

        Returns:
            (富化内容, 置信度 0-1)
        """

    async def run(self, ctx: VulnContext) -> EnrichResult:
        """
        富化执行入口（统一入口，含异常隔离）

        任何异常都不会向上抛出，失败转为 success=False 的结果，
        保证其余维度不受影响。
        """
        start = time.perf_counter()
        try:
            content, confidence = await self.enrich(ctx)
            elapsed = int((time.perf_counter() - start) * 1000)
            return EnrichResult(
                dimension=self.dimension,
                success=True,
                content=content or {},
                confidence=max(0.0, min(1.0, confidence)),
                backend="llm" if self.requires_llm else "rule",
                elapsed_ms=elapsed,
            )
        except Exception as exc:  # noqa: BLE001
            elapsed = int((time.perf_counter() - start) * 1000)
            self.log.warning(
                "enrich_failed",
                dimension=self.dimension,
                cve_id=ctx.cve_id,
                error=str(exc)[:200],
            )
            return EnrichResult(
                dimension=self.dimension,
                success=False,
                error=str(exc)[:500],
                elapsed_ms=elapsed,
            )


# ========== 通用工具 ==========


def normalize_keyword(text: str) -> str:
    """
    抽取用于匹配的关键词

    从标题中提取产品名与技术特征词，供 POC/论文/技术映射使用。
    """
    import re

    # 去掉 CVE 编号等噪声
    cleaned = re.sub(r"(CVE-\d{4}-\d+|GHSA-[a-z0-9-]+)", " ", text, flags=re.I)
    # 提取引号内的内容（产品名常在引号里）
    quoted = re.findall(r'["\']([^"\']{2,40})["\']', cleaned)
    words = re.findall(r"[A-Za-z][A-Za-z0-9_.-]{2,}", cleaned)
    return " ".join((quoted + words)[:20]).lower()


def keyword_overlap(a: str, b: str) -> float:
    """
    关键词重合度（Jaccard 相似度）

    用于 POC 与论文的关联匹配，比纯字符串包含更稳健。
    """
    sa = {w for w in a.split() if len(w) > 2}
    sb = {w for w in b.split() if len(w) > 2}
    if not sa or not sb:
        return 0.0
    inter = sa & sb
    return len(inter) / len(sa | sb)


def utcnow_iso() -> str:
    """当前时间的 ISO 字符串"""
    return datetime.now(UTC).isoformat()


async def gather_with_isolation(
    tasks: list[asyncio.Task | Any],
    return_exceptions: bool = True,
) -> list[Any]:
    """
    并发执行并隔离异常

    用 asyncio.gather 包装，任一任务异常不影响其余任务结果。
    """
    return await asyncio.gather(*tasks, return_exceptions=return_exceptions)
