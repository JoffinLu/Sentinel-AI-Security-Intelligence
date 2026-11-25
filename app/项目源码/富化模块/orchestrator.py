"""
富化编排器

P2 的执行核心。把 7 个维度并行调度、结果落库、指标埋点串成完整链路。

设计要点（对应"精准与关联推理"与"系统自动化"评分项）：

1. 并行执行：asyncio.gather 同时跑 7 个维度，
   单维失败不影响其余维度（异常隔离在 BaseEnricher.run 内实现）
2. 上下文预加载：一次查询取回 POC/论文/ATLAS 库，
   避免每个维度重复查库
3. 置信度分层：低置信维度标记为待人工核验，
   为"富化准确率"指标提供分层统计口径
4. 幂等落库：upsert 到 enrichments 表，重复富化不产生脏数据
5. 批量处理：支持指定 CVE 列表或按 AI 相关性筛选，
   配合限流避免冲击 LLM 网关
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from ..后端服务 import metrics
from ..后端服务.logger import get_logger
from ..知识库.database import get_session
from ..知识库.models import Enrichment, Paper, Technique, Vulnerability
from .asset_enricher import AssetEnricher
from .base_enricher import (
    ALL_DIMENSIONS,
    DIMENSION_LABELS,
    BaseEnricher,
    EnrichmentReport,
    EnrichResult,
    VulnContext,
)
from .cvss_enricher import CVSSEnricher
from .fix_enricher import FixEnricher
from .paper_enricher import PaperEnricher
from .poc_enricher import POCEnricher
from .risk_enricher import RiskEnricher
from .technique_enricher import TechniqueEnricher

logger = get_logger("enrich.orchestrator")


# ========== 富化器注册表 ==========

_ENRICHERS: dict[str, BaseEnricher] = {}


def get_enrichers() -> dict[str, BaseEnricher]:
    """获取全部富化器实例（单例）"""
    if not _ENRICHERS:
        for cls in (
            CVSSEnricher,
            AssetEnricher,
            POCEnricher,
            PaperEnricher,
            FixEnricher,
            TechniqueEnricher,
            RiskEnricher,
        ):
            inst = cls()
            _ENRICHERS[inst.dimension] = inst
    return _ENRICHERS


@dataclass(slots=True)
class BatchReport:
    """批量富化报告"""

    total: int = 0
    succeeded: int = 0
    failed: int = 0
    complete_enriched: int = 0  # 完整度 ≥80% 的漏洞数
    total_dimensions: int = 0
    total_success_dimensions: int = 0
    avg_completeness: float = 0.0
    avg_confidence: float = 0.0
    low_confidence_dims: int = 0
    elapsed_ms: int = 0
    reports: list[EnrichmentReport] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "succeeded": self.succeeded,
            "failed": self.failed,
            "complete_enriched": self.complete_enriched,
            "completeness_rate": round(
                self.complete_enriched / self.total * 100, 2
            )
            if self.total
            else 0.0,
            "avg_completeness": round(self.avg_completeness, 4),
            "avg_confidence": round(self.avg_confidence, 4),
            "dimension_coverage": round(
                self.total_success_dimensions / (self.total * 7) * 100, 2
            )
            if self.total
            else 0.0,
            "low_confidence_dims": self.low_confidence_dims,
            "elapsed_ms": self.elapsed_ms,
        }


class EnrichmentOrchestrator:
    """
    富化编排器

    用法：
        orch = EnrichmentOrchestrator()
        report = await orch.enrich_one(cve_id)
        batch = await orch.enrich_batch(limit=50, ai_only=True)
    """

    def __init__(self) -> None:
        self.enrichers = get_enrichers()
        self.log = logger

    # ---------- 上下文构建 ----------

    async def _load_reference_data(self) -> dict[str, list[dict[str, Any]]]:
        """
        一次性加载关联库

        POC/论文/ATLAS 数据量小且变化慢，
        批量富化时重复查询会浪费大量时间，故统一预加载。
        """
        async with get_session() as session:
            # POC 条目的标记存在 JSONB 的 extra 字段中，
            # 需用 ->> 取文本值后比较，不能用 IS TRUE（类型不匹配）
            pocs = (
                await session.execute(
                    select(
                        Vulnerability.cve_id,
                        Vulnerability.title,
                        Vulnerability.description,
                        Vulnerability.extra,
                        Vulnerability.source_url,
                    ).where(Vulnerability.extra["is_poc"].astext == "true")
                )
            ).all()
            papers = (
                await session.execute(
                    select(
                        Paper.arxiv_id,
                        Paper.title,
                        Paper.abstract,
                        Paper.ai_topics,
                        Paper.pdf_url,
                        Paper.published_at,
                    )
                )
            ).all()
            techniques = (
                await session.execute(
                    select(
                        Technique.atlas_id,
                        Technique.name,
                        Technique.tactic,
                        Technique.description,
                    ).where(Technique.is_case_study.is_(False))
                )
            ).all()

        return {
            "pocs": [
                {
                    "repo_name": (r.extra or {}).get("repo_name", r.cve_id),
                    "title": r.title,
                    "description": r.description,
                    "stars": (r.extra or {}).get("stars"),
                    "language": (r.extra or {}).get("language"),
                    "availability": (r.extra or {}).get("availability"),
                    "source_url": r.source_url,
                }
                for r in pocs
            ],
            "papers": [
                {
                    "arxiv_id": r.arxiv_id,
                    "title": r.title,
                    "abstract": r.abstract,
                    "ai_topics": r.ai_topics or [],
                    "pdf_url": r.pdf_url,
                    "published_at": r.published_at,
                }
                for r in papers
            ],
            "techniques": [
                {
                    "atlas_id": r.atlas_id,
                    "name": r.name,
                    "tactic": r.tactic,
                    "description": r.description,
                }
                for r in techniques
            ],
        }

    async def _build_context(
        self, vuln: Vulnerability, ref_data: dict[str, list[dict[str, Any]]]
    ) -> VulnContext:
        """构建富化上下文"""
        return VulnContext(
            vulnerability_id=vuln.id,
            cve_id=vuln.cve_id,
            title=vuln.title or "",
            description=vuln.description or "",
            severity=vuln.severity,
            cvss_score=vuln.cvss_score,
            cvss_vector=vuln.cvss_vector,
            affected_assets=vuln.affected_assets or [],
            references=vuln.references or [],
            ai_category=vuln.ai_category,
            ai_relevance_score=vuln.ai_relevance_score or 0.0,
            published_at=vuln.published_at,
            source_url=vuln.source_url,
            extra=vuln.extra or {},
            all_pocs=ref_data.get("pocs", []),
            all_papers=ref_data.get("papers", []),
            all_techniques=ref_data.get("techniques", []),
        )

    # ---------- 单个富化 ----------

    async def enrich_one(
        self,
        cve_id: str,
        dimensions: list[str] | None = None,
        ref_data: dict[str, list[dict[str, Any]]] | None = None,
    ) -> EnrichmentReport | None:
        """
        富化单个漏洞

        Args:
            cve_id: 漏洞编号
            dimensions: 指定维度，缺省执行全部 7 维
            ref_data: 预加载的关联库（批量时复用，避免重复查询）
        """
        start = time.perf_counter()

        async with get_session() as session:
            vuln = await session.scalar(
                select(Vulnerability).where(Vulnerability.cve_id == cve_id)
            )
            if vuln is None:
                self.log.warning("enrich_target_not_found", cve_id=cve_id)
                return None
            if ref_data is None:
                ref_data = await self._load_reference_data()
            ctx = await self._build_context(vuln, ref_data)

        target_dims = dimensions or ALL_DIMENSIONS
        results = await self._run_dimensions(ctx, target_dims)

        # 落库
        async with get_session() as session:
            await self._persist(session, ctx.vulnerability_id, results)
            await self._sync_vuln_fields(session, ctx.vulnerability_id, results)

        report = EnrichmentReport(
            cve_id=ctx.cve_id,
            vulnerability_id=ctx.vulnerability_id,
            results=results,
            elapsed_ms=int((time.perf_counter() - start) * 1000),
        )
        self.log.info(
            "enrich_one_done",
            cve_id=ctx.cve_id,
            completeness=f"{report.completeness:.0%}",
            avg_confidence=f"{report.avg_confidence:.2f}",
        )
        return report

    async def _run_dimensions(
        self, ctx: VulnContext, dimensions: list[str]
    ) -> dict[str, EnrichResult]:
        """
        并行执行多个维度

        规则维度几乎零耗时，LLM 维度受网关限流影响。
        这里并发执行以压缩整体耗时，同时用 LLM 客户端自身的
        并发信号量做二级保护。
        """
        tasks = []
        used: list[str] = []
        for dim in dimensions:
            enricher = self.enrichers.get(dim)
            if enricher is None:
                continue
            tasks.append(enricher.run(ctx))
            used.append(dim)

        if not tasks:
            return {}

        # return_exceptions=True 保证单维崩溃不影响整体
        raw_results = await asyncio.gather(*tasks, return_exceptions=True)

        results: dict[str, EnrichResult] = {}
        for dim, r in zip(used, raw_results, strict=False):
            if isinstance(r, BaseException):
                # 理论上 BaseEnricher.run 已捕获异常，这里是最后一道防线
                results[dim] = EnrichResult(
                    dimension=dim, success=False, error=str(r)[:300]
                )
            else:
                results[dim] = r
        return results

    # ---------- 批量富化 ----------

    async def enrich_batch(
        self,
        limit: int = 50,
        ai_only: bool = True,
        cve_ids: list[str] | None = None,
        dimensions: list[str] | None = None,
        concurrency: int = 3,
    ) -> BatchReport:
        """
        批量富化

        串行调度 + 维度级并行，逐个落库以保持事务清晰。
        concurrency 控制同时处理多少个漏洞，
        需根据 LLM 网关的实际承受力调整。
        """
        start = time.perf_counter()
        batch = BatchReport()

        async with get_session() as session:
            stmt = select(Vulnerability.cve_id)
            if cve_ids:
                stmt = stmt.where(Vulnerability.cve_id.in_(cve_ids))
            elif ai_only:
                stmt = stmt.where(Vulnerability.ai_relevance_score >= 0.5)
            targets = list((await session.execute(stmt.limit(limit * 2))).scalars())

        # 预加载关联库，整个批次复用
        ref_data = await self._load_reference_data()

        # 优先富化未富化的，再补充已富化但可能过期的
        async with get_session() as session:
            enriched = set(
                (
                    await session.execute(
                        select(Vulnerability.cve_id)
                        .join(
                            Enrichment,
                            Enrichment.vulnerability_id == Vulnerability.id,
                        )
                        .where(Vulnerability.cve_id.in_(targets))
                        .distinct()
                    )
                ).scalars()
            )
        ordered = [c for c in targets if c not in enriched] + [c for c in targets if c in enriched]
        selected = ordered[:limit]

        batch.total = len(selected)
        if not selected:
            return batch

        semaphore = asyncio.Semaphore(concurrency)

        async def _one(cve_id: str) -> EnrichmentReport | None:
            async with semaphore:
                try:
                    return await self.enrich_one(cve_id, dimensions, ref_data)
                except Exception as exc:  # noqa: BLE001
                    self.log.warning("enrich_item_failed", cve_id=cve_id, error=str(exc)[:200])
                    return None

        reports = await asyncio.gather(*[_one(c) for c in selected])

        for r in reports:
            if r is None:
                batch.failed += 1
                continue
            batch.succeeded += 1
            batch.reports.append(r)
            batch.total_dimensions += r.total_count
            batch.total_success_dimensions += r.success_count
            batch.low_confidence_dims += r.low_confidence_count

        # 赛题硬性指标富化完整度 ≥80%
        if batch.reports:
            batch.avg_completeness = sum(r.completeness for r in batch.reports) / len(
                batch.reports
            )
            batch.avg_confidence = sum(r.avg_confidence for r in batch.reports) / len(
                batch.reports
            )
            batch.complete_enriched = sum(1 for r in batch.reports if r.completeness >= 0.8)

        batch.elapsed_ms = int((time.perf_counter() - start) * 1000)
        self.log.info("enrich_batch_done", **batch.to_dict())
        return batch

    # ---------- 结果落库 ----------

    async def _persist(
        self,
        session,
        vulnerability_id: int,
        results: dict[str, EnrichResult],
    ) -> None:
        """
        富化结果落库（幂等 upsert）

        每条记录携带置信度与耗时，为质量分层统计提供数据。
        """
        for dim, result in results.items():
            stmt = (
                pg_insert(Enrichment)
                .values(
                    vulnerability_id=vulnerability_id,
                    dimension=dim,
                    status="success" if result.success else "failed",
                    confidence=result.confidence,
                    content=result.content,
                    backend=result.backend,
                    error=result.error,
                    elapsed_ms=result.elapsed_ms,
                )
                .on_conflict_do_update(
                    index_elements=[Enrichment.vulnerability_id, Enrichment.dimension],
                    set_={
                        "status": "success" if result.success else "failed",
                        "confidence": result.confidence,
                        "content": result.content,
                        "backend": result.backend,
                        "error": result.error,
                        "elapsed_ms": result.elapsed_ms,
                        "updated_at": datetime.now(UTC),
                    },
                )
            )
            await session.execute(stmt)

            # 监控埋点
            metrics.record_enrich(
                dimension=dim,
                success=result.success,
                confidence=result.confidence,
                elapsed_ms=result.elapsed_ms,
            )

    async def _sync_vuln_fields(
        self, session, vulnerability_id: int, results: dict[str, EnrichResult]
    ) -> None:
        """
        把关键富化结果回写到漏洞主表

        冗余存储的目的是让查询接口无需 JOIN 即可筛选，
        例如按风险等级、按 CVSS 分数检索。
        """
        vuln = await session.get(Vulnerability, vulnerability_id)
        if vuln is None:
            return

        cvss = results.get("cvss")
        if cvss and cvss.success and cvss.content.get("score") is not None:
            vuln.cvss_score = cvss.content["score"]
            sev = cvss.content.get("severity")
            if sev:
                vuln.severity = sev

        risk = results.get("risk")
        if risk and risk.success:
            extra = dict(vuln.extra or {})
            extra["risk_priority"] = risk.content.get("priority")
            extra["risk_score"] = risk.content.get("risk_score")
            extra["risk_level"] = risk.content.get("risk_level")
            extra["business_impact"] = risk.content.get("business_impact")
            vuln.extra = extra
            if risk.content.get("ai_relevance") is not None:
                vuln.ai_relevance_score = risk.content["ai_relevance"]

        asset = results.get("asset")
        if asset and asset.success and asset.content.get("assets"):
            vuln.affected_assets = asset.content["assets"]

        poc = results.get("poc")
        if poc and poc.success and poc.content.get("pocs"):
            vuln.poc_refs = poc.content["pocs"]

        paper = results.get("paper")
        if paper and paper.success and paper.content.get("papers"):
            vuln.paper_refs = paper.content["papers"]

        tech = results.get("technique")
        if tech and tech.success and tech.content.get("techniques"):
            vuln.technique_refs = tech.content["techniques"]

        fix = results.get("fix")
        if fix and fix.success:
            vuln.fix_info = {
                "patch_version": fix.content.get("patch_version"),
                "mitigation": fix.content.get("mitigation"),
                "fix_links": fix.content.get("fix_links"),
                "guidance": fix.content.get("guidance"),
            }

        vuln.enrichment_status = "completed"
        await session.flush()

    # ---------- 查询 ----------

    async def get_enrichment(self, cve_id: str) -> dict[str, Any] | None:
        """查询单个漏洞的富化结果"""
        async with get_session() as session:
            vuln = await session.scalar(
                select(Vulnerability).where(Vulnerability.cve_id == cve_id)
            )
            if vuln is None:
                return None

            rows = (
                await session.execute(
                    select(Enrichment).where(
                        Enrichment.vulnerability_id == vuln.id
                    )
                )
            ).scalars().all()

            dimensions = []
            for r in rows:
                dimensions.append(
                    {
                        "dimension": r.dimension,
                        "label": DIMENSION_LABELS.get(r.dimension, r.dimension),
                        "status": r.status,
                        "confidence": round(r.confidence or 0, 3),
                        "quality_tier": (
                            "high"
                            if (r.confidence or 0) >= 0.8
                            else "medium"
                            if (r.confidence or 0) >= 0.5
                            else "low"
                        ),
                        "content": r.content,
                        "error": r.error,
                        "elapsed_ms": r.elapsed_ms,
                    }
                )

            ok = [d for d in dimensions if d["status"] == "success"]
            return {
                "cve_id": cve_id,
                "title": vuln.title,
                "completeness": round(len(ok) / len(ALL_DIMENSIONS), 3),
                "success_count": len(ok),
                "total_count": len(ALL_DIMENSIONS),
                "avg_confidence": round(
                    sum(d["confidence"] for d in ok) / len(ok), 3
                )
                if ok
                else 0.0,
                "enrichment_status": vuln.enrichment_status,
                "dimensions": dimensions,
            }

    async def get_statistics(self) -> dict[str, Any]:
        """
        富化统计（质量分层）

        为赛题要求"富化准确率 ≥90%"与"完整度 ≥80%"提供分层举证：
        高置信部分可直接采信，低置信部分需人工核验。
        """
        async with get_session() as session:
            # 注意：这里需要 COUNT 而非取单值，
            # 用 scalar 会只得到第一行的 id 而非总数
            enriched_total = (
                await session.scalar(
                    select(func.count())
                    .select_from(Vulnerability)
                    .where(Vulnerability.enrichment_status == "completed")
                )
            ) or 0
            rows = (
                await session.execute(
                    select(
                        Enrichment.dimension,
                        Enrichment.status,
                        Enrichment.confidence,
                    )
                )
            ).all()

        by_dim: dict[str, dict[str, Any]] = {
            d: {"success": 0, "failed": 0, "conf_sum": 0.0, "high": 0, "medium": 0, "low": 0}
            for d in ALL_DIMENSIONS
        }
        for dim, status, conf in rows:
            if dim not in by_dim:
                continue
            conf = conf or 0.0
            if status == "success":
                by_dim[dim]["success"] += 1
                by_dim[dim]["conf_sum"] += conf
                if conf >= 0.8:
                    by_dim[dim]["high"] += 1
                elif conf >= 0.5:
                    by_dim[dim]["medium"] += 1
                else:
                    by_dim[dim]["low"] += 1
            else:
                by_dim[dim]["failed"] += 1

        dimensions = []
        total_success = 0
        total_all = 0
        for dim, stat in by_dim.items():
            attempted = stat["success"] + stat["failed"]
            avg_conf = stat["conf_sum"] / stat["success"] if stat["success"] else 0.0
            total_success += stat["success"]
            total_all += attempted
            dimensions.append(
                {
                    "dimension": dim,
                    "label": DIMENSION_LABELS[dim],
                    "success": stat["success"],
                    "failed": stat["failed"],
                    "coverage": round(stat["success"] / attempted * 100, 1)
                    if attempted
                    else 0.0,
                    "avg_confidence": round(avg_conf, 3),
                    "high_confidence": stat["high"],
                    "medium_confidence": stat["medium"],
                    "low_confidence": stat["low"],
                }
            )

        return {
            "enriched_vulnerabilities": enriched_total,
            "dimension_count": len(ALL_DIMENSIONS),
            "dimensions": dimensions,
            "overall_coverage": round(total_success / total_all * 100, 1)
            if total_all
            else 0.0,
            "high_confidence_ratio": round(
                sum(d["high_confidence"] for d in dimensions) / total_success * 100, 1
            )
            if total_success
            else 0.0,
        }


# ========== 全局单例 ==========

_orchestrator: EnrichmentOrchestrator | None = None


def get_orchestrator() -> EnrichmentOrchestrator:
    """获取编排器单例"""
    global _orchestrator
    if _orchestrator is None:
        _orchestrator = EnrichmentOrchestrator()
    return _orchestrator
